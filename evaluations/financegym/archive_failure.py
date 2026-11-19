"""Verify a transport parser failure against the actual dated searches and their original HTTP bytes."""

import argparse
import gzip
import json
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import Lock

from pypdf.errors import PdfReadError

from evaluations.paired.corpus import require
from evaluations.paired.event_audit import verify_private
from scripts.archive_receipts import RecordedHttp
from scripts.disney_hulu_case import CaseError
from scripts.focused_archive import FocusedArchive
from scripts.open_desk_report import SavedArchive
from scripts.packet_archive import SavedSearches
from scripts.research_team import check_configuration
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_meter import recorded_output, verify_ledger
from scripts.temporal_web import MAX_BODY, article

from . import (
    budget,
    budget_publication,
    editorial,
    pacing,
    recovery,
    runner,
    titled_publication,
)

registered_attempt = recovery.attempt


def original_pdf(directory, sha):
    require(
        isinstance(sha, str) and re.fullmatch(r"[0-9a-f]{64}", sha),
        "Invalid archived PDF content address",
    )
    with gzip.open(directory / (sha + ".gz"), "rb") as stream:
        if stream.read(4) != b"%PDF":
            return None
        raw = b"%PDF" + stream.read(MAX_BODY - 3)
    require(
        len(raw) <= MAX_BODY and fingerprint(raw) == sha,
        "Archived PDF content address changed",
    )
    return raw


def parser_error(raw):
    try:
        article(raw)
    except PdfReadError as error:
        return str(error)
    except CaseError:
        # For example, the archive rejects a well-formed PDF over its page limit.
        pass
    return None


def damaged_receipts(folder, keys, message):
    damaged, checked = [], {}
    for key in sorted(keys):
        envelope = load(folder / (key + ".json"))
        receipt = envelope["receipt"]
        if receipt["status"] != 200:
            continue
        sha = receipt["sha256"]
        if sha not in checked:
            raw = original_pdf(folder, sha)
            if raw is not None:
                require(
                    len(raw) == receipt["byte_length"],
                    "The PDF length differs from its receipt",
                )
            checked[sha] = raw is not None and parser_error(raw) == message
        if checked[sha]:
            damaged.append(envelope)
    require(
        damaged, "The original HTTP responses contain no PDF reproducing the failure"
    )
    return damaged


def attempt(loc, row, version, assigned, route, ids):
    run = loc / "research"
    rec = load(run / "run.json")
    if rec.get("failure") != "PdfReadError" or rec["state"] != "failed":
        return registered_attempt(loc, row, version, assigned, route, ids)
    engine, profile, cap = route
    check_configuration(
        rec,
        engine.configuration(
            row["question"],
            row["cutoff"],
            Decimal(cap),
            SavedArchive(row["cutoff"], version),
            profile,
            assigned,
        ),
    )
    paid, held = verify_ledger(rec, partial=True)
    require(
        not held and paid <= Decimal(cap),
        "Unsettled or excessive spend on a PDF failure",
    )
    require(
        not (run / "memo.md").exists() and not (run / "result.json").exists(),
        "A parser failure unexpectedly has a report",
    )
    verify_private(run, rec, ids)
    entries = {**rec["calls"], **rec.get("skipped_calls", {})}
    seen, mutex = set(), Lock()

    def ask(stage, prompt, limit, *, keep_usd=None):
        with mutex:
            require(
                stage in entries and stage not in seen,
                "A request in the failed run changed",
            )
            seen.add(stage)
            return recorded_output(entries[stage], stage, prompt, limit, keep_usd)

    http = RecordedHttp(loc / "operator-http", offline=True)
    with tempfile.TemporaryDirectory() as temporary:
        scratch = Path(temporary)
        gateway = FocusedArchive(
            row["cutoff"], scratch / "cache", question=row["question"], http=http
        )
        require(
            gateway.VERSION == version,
            "Unexpected archive version for the failed search",
        )
        try:
            engine.research(
                row["question"],
                row["cutoff"],
                SavedSearches(gateway, scratch / "searches"),
                ask,
                profile=profile,
            )
        except PdfReadError as error:
            message = str(error)
        else:
            raise ValueError(
                "The original HTTP receipts did not reproduce the PDF parser failure"
            )
        actual = {p.name: p.read_bytes() for p in (scratch / "searches").glob("*.json")}
        saved = {p.name: p.read_bytes() for p in (run / "searches").glob("*.json")}
        require(
            saved == actual,
            "The failed run's completed searches differ from the original bytes",
        )
    require(seen == set(entries), "PDF replay missed an original model assignment")
    require(
        not http.missing
        and http.seen == {p.stem for p in http.directory.glob("*.json")},
        "The failed run's HTTP inventory differs from the actual planned searches",
    )
    receipts = damaged_receipts(http.directory, http.seen, message)
    urls = {
        source["url"]
        for raw in saved.values()
        for source in json.loads(raw)["body"]["sources"]
    }
    editions = {u.split("#:~:text=")[0] for u in urls}
    seconds = (
        datetime.fromisoformat(rec["finished_at"])
        - datetime.fromisoformat(rec["created_at"])
    ).total_seconds()
    require(seconds >= 0, "Invalid parser-failure duration")
    info = {
        "profile": profile,
        "state": rec["state"],
        "research_state": rec["state"],
        "original_run_sha256": fingerprint((run / "run.json").read_bytes()),
        "source_report_sha256": None,
        "report_sha256": None,
        "attribution_review": None,
        "estimated_cost_usd": str(paid),
        "outstanding_usd": "0",
        "elapsed_s": round(seconds, 3),
        "failure": rec["failure"],
        "searches": len(saved),
        "transport_requests": len(http.seen),
        "eligible_sources": len(editions),
        "eligible_passage_urls": len(urls),
        "archive_failure": {"error": message, "pdf_receipts": receipts},
    }
    return None, info, editions


def failed_pdf_files(run, certificate=None):
    record = load(run / "run.json")
    if record.get("failure") != "PdfReadError" or record["state"] != "failed":
        return {}
    files = {}
    http = run.parent / "operator-http"
    digests = (
        {env["receipt"]["sha256"] for env in certificate["pdf_receipts"]}
        if certificate
        else (p.stem for p in http.glob("*.gz"))
    )
    for sha in digests:
        raw = original_pdf(http, sha)
        if raw is not None and parser_error(raw):
            files[f"failed-pdf/{sha}.bin"] = raw
    return files


def verify_certificate(run, proof):
    record = load(run / "run.json")
    detail = proof.get("archive_failure")
    blobs = {p.stem: p for p in (run / "failed-pdf").glob("*.bin")}
    if record.get("failure") != "PdfReadError" or record["state"] != "failed":
        require(
            detail is None and not blobs,
            "Unrelated research contains a PDF failure certificate",
        )
        return
    require(
        proof.get("failure") == "PdfReadError"
        and proof["state"] == "failed"
        and proof.get("report_sha256") is None
        and detail
        and detail.get("error")
        and detail.get("pdf_receipts"),
        "Missing failed PDF evidence",
    )
    index = load(run / "http-receipt-hashes.json")
    require(
        len(index) == proof["transport_requests"],
        "The parser failure receipt inventory changed",
    )
    seen, checked = set(), set()
    for envelope in detail["pdf_receipts"]:
        item = envelope["receipt"]
        key = item["request"]
        require(
            key not in seen
            and index.get(key) == fingerprint(json_bytes(envelope))
            and envelope["sha256"] == fingerprint(json_bytes(item))
            and item["status"] == 200
            and item["byte_length"] > 0,
            "The failed PDF does not match an original HTTP receipt",
        )
        seen.add(key)
        sha = item["sha256"]
        require(sha in blobs, "Original failed PDF bytes are missing")
        if sha not in checked:
            raw = blobs[sha].read_bytes()
            require(
                raw.startswith(b"%PDF")
                and len(raw) == item["byte_length"]
                and fingerprint(raw) == sha,
                "Original failed PDF bytes changed",
            )
            require(
                parser_error(raw) == detail["error"],
                "The saved PDF produced a different error",
            )
        checked.add(sha)
    require(checked == set(blobs), "Unexpected PDF in the failure certificate")


@contextmanager
def use_archive_failure_verifier():
    """Install the extra offline verifier in this operator process for the registered publication chain.

    The frozen process calls recovery.attempt directly. This scoped adapter retains that exact
    verifier for every other outcome. A damaged PDF can only produce a verified failed attempt.
    """
    require(
        recovery.attempt is registered_attempt,
        "An unexpected research verifier is installed",
    )
    recovery.attempt = attempt
    try:
        yield
    finally:
        recovery.attempt = registered_attempt


def recover_one(row, cfg, ext):
    source = runner.RESEARCH / row["task_id"] / "research"
    record = recovery.OUTPUT / row["task_id"] / "research/run.json"
    cap = recovery.second_route(cfg, runner.route(row, cfg)[1])[2]
    with use_archive_failure_verifier():
        return pacing.capture_attempt(
            record,
            row,
            recovery.ticket(row, cfg, ext, source),
            cap,
            lambda: recovery.recover_one(row, cfg, ext),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    check = sub.add_parser("verify")
    check.add_argument("questions", type=Path)
    check.add_argument("task_id")
    retry = sub.add_parser("recover")
    retry.add_argument("questions", type=Path)
    retry.add_argument("task_id")
    pack = sub.add_parser("export")
    pack.add_argument("questions", type=Path)
    pack.add_argument("directory", type=Path)
    args = parser.parse_args()
    cfg = runner.locked()
    ext = recovery.register(cfg)
    if args.action == "recover":
        row = next(
            r for r in runner.tasks(args.questions) if r["task_id"] == args.task_id
        )
        first = load(runner.RESEARCH / row["task_id"] / "research/run.json")
        require(
            first["state"] == "failed" and first.get("failure") == "PdfReadError",
            "This recovery entry point is for an original PDF parsing failure",
        )
        record = recovery.OUTPUT / row["task_id"] / "research/run.json"
        cap = recovery.second_route(cfg, runner.route(row, cfg)[1])[2]
        with budget.ProjectBudget().reserve(record, cap):
            print(json.dumps(recover_one(row, cfg, ext), indent=2))
        raise SystemExit(0)
    with use_archive_failure_verifier():
        if args.action == "verify":
            row = next(
                r for r in runner.tasks(args.questions) if r["task_id"] == args.task_id
            )
            answer, audit, _ = attempt(
                runner.RESEARCH / args.task_id,
                row,
                cfg["archive_version"],
                runner.assignment(row, cfg),
                runner.route(row, cfg),
                recovery.identifiers(),
            )
            require(
                answer is None and "archive_failure" in audit,
                "Expected an authenticated failed PDF",
            )
            print(json.dumps(audit, indent=2))
        else:
            edit = editorial.locked(cfg, ext)
            prior = titled_publication.locked(edit)
            spec = budget_publication.locked(prior)
            print(
                json.dumps(
                    budget_publication.export(
                        args.questions, args.directory, cfg, ext, edit, prior, spec
                    ),
                    indent=2,
                )
            )
