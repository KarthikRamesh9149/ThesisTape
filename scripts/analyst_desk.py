"""Serve ThesisTape's local analyst screen over the same checkpoint files as the CLI."""

import argparse
import json
import re
import secrets
import signal
import sys
from decimal import Decimal, InvalidOperation
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Event, Lock, Thread
from urllib.parse import urlsplit

from . import open_desk_report as public
from . import quantitative_engine
from . import research_desk_agent as engine
from .archive_receipts import RecordedHttp
from .desk_report import inspect_record
from .disney_hulu_case import CaseError, read_saved_file
from .research_team import ROUTES, queue_research, resume, saved_case
from .run_checkpoint import PauseRequested, load, own_run
from .sol_baseline import PROJECT_ROOT, fingerprint, json_bytes
from .team_research import COLLECTIONS, analyst_case
from .temporal_web import ArchiveGateway

ASSETS = PROJECT_ROOT / "desk"
EXAMPLES = {
    "example-twitter": "material_dispute/trial/dispute",
    "example-disney": "first-team/disney-team",
    "example-toyota": "visual_reader/pair/visual",
    "example-tradeweb": "first-team/tradeweb-team",
    "example-tradeweb-desk": "analyst_desk/proof",
}
LIVE_ID = re.compile(r"desk-[a-f0-9]{32}")
TERMINAL = public.FINISHED


class DeskError(CaseError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def local_id(path):
    return (
        path.name
        if LIVE_ID.fullmatch(path.name)
        else "local-" + fingerprint(path.name.encode())
    )


def limit(value):
    try:
        if (
            not isinstance(value, str)
            or len(value) > 20
            or not re.fullmatch(r"[0-9]+(?:\.[0-9]+)?", value)
        ):
            raise ValueError
        amount = Decimal(value)
        if not 0 < amount <= 100:
            raise ValueError
        return amount
    except (InvalidOperation, ValueError):
        raise DeskError(400, "Enter a positive USD spending limit up to 100.") from None


class Desk:
    def __init__(self, root, *, provider=None):
        self.root = root.resolve()
        self.root.mkdir(mode=0o700, exist_ok=True, parents=True)
        self.provider = provider
        self.token = secrets.token_urlsafe(32)
        self.lock = Lock()
        self.jobs = {}
        self.errors = {}
        self.closing = False

    def folders(self):
        for p in self.root.iterdir():
            if (
                not p.name.startswith(".")
                and not p.is_symlink()
                and p.is_dir()
                and (p / "run.json").is_file()
            ):
                yield local_id(p), p

    def directory(self, key):
        if key in EXAMPLES:
            return PROJECT_ROOT / "evaluations" / EXAMPLES[key]
        path = (
            self.root / key if LIVE_ID.fullmatch(key) else dict(self.folders()).get(key)
        )
        if (
            path is None
            or path.is_symlink()
            or not path.is_dir()
            or not (path / "run.json").is_file()
        ):
            raise DeskError(404, "Research run not found.")
        return path

    @staticmethod
    def record(directory):
        r = load(directory / "run.json")
        if not public.is_public(r) and (
            r.get("schema_version") != 3 or r.get("route") not in ROUTES
        ):
            raise DeskError(404, "This file is not a research-team run.")
        return r

    def library(self):
        rows = []
        for key, folder in [
            *self.folders(),
            *((k, self.directory(k)) for k in EXAMPLES),
        ]:
            try:
                r = self.record(folder)
                if public.is_public(r):
                    title, question, cutoff = public.TITLE, r["question"], r["cutoff"]
                else:
                    case = saved_case(r)
                    title, question, cutoff = case.title, case.question, case.cutoff
                rows.append(
                    {
                        "id": key,
                        "title": title,
                        "question": question,
                        "cutoff": cutoff,
                        "state": r["state"],
                        "created": r["created_at"],
                        "example": key in EXAMPLES,
                    }
                )
            except (OSError, ValueError, KeyError, TypeError):
                continue
        local = sorted(
            (r for r in rows if not r["example"]),
            key=lambda r: r["created"],
            reverse=True,
        )
        return local + [r for r in rows if r["example"]]

    def inspect(self, key):
        folder = self.directory(key)
        record = self.record(folder)
        info, report = (public.inspect if public.is_public(record) else inspect_record)(
            folder, record
        )
        with self.lock:
            owned = key in self.jobs
            info.update(
                id=key,
                active=owned,
                can_pause=owned and record["state"] in ("running", "queued"),
                pausing=owned and self.jobs[key][1].is_set(),
                can_resume=not owned
                and key not in EXAMPLES
                and record["state"] not in TERMINAL,
                operator_error=self.errors.get(key),
            )
        return info, report

    def start_locked(self, key, directory):
        if self.closing:
            raise DeskError(503, "The desk is shutting down.")
        if key in self.jobs:
            return
        stop = Event()
        self.errors.pop(key, None)

        def work():
            try:
                r = self.record(directory)
                if public.is_public(r):
                    profile = r["research_profile"]
                    quantitative = profile.startswith("evidence_team")
                    implementation = quantitative_engine if quantitative else engine
                    gateway = (
                        ArchiveGateway(
                            r["cutoff"],
                            directory / ".archive",
                            http=RecordedHttp(directory / ".operator-http"),
                        )
                        if quantitative
                        else None
                    )
                    implementation.run(
                        r["question"],
                        r["cutoff"],
                        directory,
                        directory / ".archive",
                        r["budget_usd"],
                        provider=self.provider,
                        pause=stop,
                        profile=profile,
                        gateway=gateway,
                        assignment=r["assignment"],
                    )
                else:
                    resume(directory, self.provider, pause=stop)
            except PauseRequested:
                pass
            # This thread is the UI error boundary. Unexpected provider errors may
            # contain request data and must not be forwarded to the browser.
            except Exception as error:  # noqa: BLE001
                with self.lock:
                    self.errors[key] = (
                        str(error)
                        if isinstance(error, CaseError)
                        else "The worker stopped. Inspect the saved run before resuming."
                    )
            finally:
                with self.lock:
                    self.jobs.pop(key, None)

        thread = Thread(target=work, name="research-" + key)
        self.jobs[key] = (thread, stop)
        thread.start()

    def submit(self, fields):
        if set(fields) - {"profile"} != {
            "request_id",
            "question",
            "cutoff",
            "collection",
            "budget",
        }:
            raise DeskError(
                400,
                "A question, cutoff, source collection, budget and request ID are required.",
            )
        request_id = fields["request_id"]
        if not isinstance(request_id, str) or not re.fullmatch(
            r"[0-9a-f]{32}", request_id
        ):
            raise DeskError(400, "Invalid request ID.")
        web = fields["collection"] == public.COLLECTION
        profile = fields.get("profile", "mixed")
        if (
            profile not in ("mixed", "evidence_team_v3")
            or not web
            and "profile" in fields
        ):
            raise DeskError(
                400, "Select an available research method for this source collection."
            )
        if web:
            question, cutoff = public.intake(fields["question"], fields["cutoff"])
        else:
            case = analyst_case(
                fields["question"], fields["cutoff"], fields["collection"]
            )
        budget = limit(fields["budget"])
        key = "desk-" + request_id
        directory = self.root / key
        with self.lock:
            if self.closing:
                raise DeskError(503, "The desk is shutting down.")
            if not directory.exists() and not directory.is_symlink():
                if web:
                    implementation = (
                        quantitative_engine if profile == "evidence_team_v3" else engine
                    )
                    implementation.run(
                        question,
                        cutoff,
                        directory,
                        directory / ".archive",
                        budget,
                        queued=True,
                        profile=profile,
                    )
                else:
                    queue_research(case, directory, budget)
                self.start_locked(key, directory)
            else:
                r = self.record(self.directory(key))
                same = public.is_public(r) == web and (
                    (r["question"], r["cutoff"], r["research_profile"])
                    == (question, cutoff, profile)
                    if web
                    else saved_case(r) == case
                )
                if not same or Decimal(r["budget_usd"]) != budget:
                    raise DeskError(
                        409, "This request ID already belongs to a different intake."
                    )
        return {"id": key}

    def control(self, key, action):
        directory = self.directory(key)
        if key in EXAMPLES:
            raise DeskError(409, "This saved example is read-only.")
        with self.lock:
            if action == "pause":
                if key not in self.jobs:
                    raise DeskError(
                        409, "This desk does not own an active worker for the run."
                    )
                self.jobs[key][1].set()
            elif (
                key not in self.jobs and self.record(directory)["state"] not in TERMINAL
            ):
                # Give the caller an immediate ownership error if the CLI is still working.
                owner = (
                    directory.parent / ("." + directory.name + ".lock")
                    if public.is_public(self.record(directory))
                    else directory
                )
                with own_run(owner):
                    pass
                self.start_locked(key, directory)
        return {"id": key}

    def close(self):
        with self.lock:
            self.closing = True
            jobs = list(self.jobs.values())
            for _, stop in jobs:
                stop.set()
        for thread, _ in jobs:
            thread.join()


class Handler(BaseHTTPRequestHandler):
    server_version = "ThesisTape"

    def log_message(self, fmt, *args):
        pass

    def send_content(self, status, data, mime, *, download=None):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; img-src 'self'; font-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'none'",
        )
        if download:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{download}"'
            )
        self.end_headers()
        self.wfile.write(data)

    def reply(self, data, status=200):
        self.send_content(status, json_bytes(data), "application/json; charset=utf-8")

    def handle_request(self, write):
        desk = self.server.desk
        authority = self.headers.get("Host", "")
        port = self.server.server_port
        if authority not in (f"127.0.0.1:{port}", f"localhost:{port}"):
            raise DeskError(403, "Use the local desk address.")
        origin = self.headers.get("Origin")
        if origin is not None and origin != "http://" + authority:
            raise DeskError(403, "Use the local desk origin.")
        path = urlsplit(self.path).path
        if write:
            if not secrets.compare_digest(
                self.headers.get("X-Desk-Token", ""), desk.token
            ):
                raise DeskError(403, "Reload the desk to refresh its session.")
            size = int(self.headers.get("Content-Length", "-1"))
            if (
                self.headers.get_content_type() != "application/json"
                or not 0 <= size <= 16384
            ):
                raise DeskError(400, "A small JSON request body is required.")
            fields = json.loads(self.rfile.read(size))
            if not isinstance(fields, dict):
                raise DeskError(400, "Invalid request body.")
            if path == "/api/runs":
                return self.reply(desk.submit(fields), 202)
            action = re.fullmatch(r"/api/runs/([a-z0-9-]+)/(pause|resume)", path)
            if action and not fields:
                return self.reply(desk.control(*action.groups()), 202)
        else:
            assets = {
                "/": ("index.html", "text/html"),
                "/desk.css": ("desk.css", "text/css"),
                "/desk.js": ("desk.js", "text/javascript"),
            }
            if path in assets:
                name, mime = assets[path]
                return self.send_content(
                    200, read_saved_file(ASSETS / name), mime + "; charset=utf-8"
                )
            if path == "/favicon.ico":
                return self.send_content(204, b"", "image/x-icon")
            if path == "/api/session":
                return self.reply(
                    {
                        "token": desk.token,
                        "collections": {public.COLLECTION: public.LABEL, **COLLECTIONS},
                        "runs": desk.library(),
                    }
                )
            if path == "/api/runs":
                return self.reply({"runs": desk.library()})
            match = re.fullmatch(
                r"/api/runs/([a-z0-9-]+)(?:/(memo\.md|passages/([1-9][0-9]{0,3})))?",
                path,
            )
            if match:
                key, resource, number = match.groups()
                info, report = desk.inspect(key)
                if resource is None:
                    return self.reply(info)
                if report is None:
                    raise DeskError(409, "This run has no released memo.")
                if resource == "memo.md":
                    return self.send_content(
                        200,
                        report.memo,
                        "text/markdown; charset=utf-8",
                        download=f"thesistape-{key}.md",
                    )
                if int(number) > len(report.passages):
                    raise DeskError(
                        404, "This passage is not cited in the released report."
                    )
                hit = report.passages[int(number) - 1]
                return self.reply(
                    {
                        "number": int(number),
                        "title": hit.title,
                        "locator": hit.locator,
                        "text": hit.text,
                        "published": hit.published,
                        "edition": hit.edition,
                        "url": hit.citation_url,
                        "sha256": hit.sha256,
                    }
                )
        raise DeskError(404, "Page not found.")

    def dispatch(self, write):
        try:
            self.handle_request(write)
        except DeskError as error:
            self.reply({"error": str(error)}, error.status)
        except (CaseError, ValueError, KeyError, TypeError, OSError) as error:
            if isinstance(error, (BrokenPipeError, ConnectionResetError)):
                return
            status = HTTPStatus.BAD_REQUEST if write else HTTPStatus.CONFLICT
            self.reply(
                {
                    "error": str(error)
                    if isinstance(error, ValueError)
                    else "The saved run could not be read or verified."
                },
                status,
            )

    def do_GET(self):
        self.dispatch(False)

    def do_POST(self):
        self.dispatch(True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--runs-dir", type=Path, default=PROJECT_ROOT / "runs/desk")
    args = parser.parse_args()
    desk = Desk(args.runs_dir)
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        server.desk = desk

        def stop(signum, frame):
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, stop)
        print(
            f"ThesisTape research desk: http://127.0.0.1:{server.server_port}/",
            flush=True,
        )
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            desk.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
