import io
import json
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from http.client import HTTPResponse
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock, patch

from scripts.analyst_desk import EXAMPLES, Desk, Handler, local_id
from scripts.research_team import default_route, queue_research, saved_case
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import PROJECT_ROOT
from scripts.team_research import analyst_case, case_for


class Wire:
    """Exercise the real HTTP handler in memory so the suite can deny all sockets."""

    def __init__(self, data):
        self.incoming = io.BytesIO(data)
        self.outgoing = io.BytesIO()

    def makefile(self, *args):
        return self.incoming

    def sendall(self, data):
        self.outgoing.write(data)


class DeskTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.provider = Mock()
        self.desk = Desk(self.root, provider=self.provider)
        self.addCleanup(self.desk.close)
        self.data = {
            "request_id": "a" * 32,
            "question": "Which forecast did Tradeweb correct?",
            "cutoff": "2023-10-27T00:15:00Z",
            "collection": "archive",
            "budget": "0.10",
        }

    def request(
        self, url, *, body=None, host="127.0.0.1:8765", origin=None, token=True
    ):
        method = "GET" if body is None else "POST"
        payload = json.dumps(body).encode() if body is not None else b""
        headers = [f"{method} {url} HTTP/1.0", f"Host: {host}"]
        if origin:
            headers.append("Origin: " + origin)
        if body is not None:
            headers.extend(
                ["Content-Type: application/json", f"Content-Length: {len(payload)}"]
            )
            if token:
                headers.append("X-Desk-Token: " + self.desk.token)
        wire = Wire(("\r\n".join(headers) + "\r\n\r\n").encode() + payload)
        Handler(
            wire, ("127.0.0.1", 1), SimpleNamespace(desk=self.desk, server_port=8765)
        )
        response = HTTPResponse(Wire(wire.outgoing.getvalue()))
        response.begin()
        return response.status, response.headers, response.read()

    def assert_status(self, expected, url, **kwargs):
        code, _, body = self.request(url, **kwargs)
        self.assertEqual(code, expected, body[:240])
        return json.loads(body)

    def test_intake_identity_cutoff_and_routes(self):
        custom = analyst_case(self.data["question"], self.data["cutoff"])
        self.assertEqual(default_route(custom), "reviewed")
        with custom.corpus().session(custom.cutoff) as gateway:
            amendment = "https://www.sec.gov/Archives/edgar/data/1758730/000175873023000171/tw-20231026.htm"
            self.assertTrue(gateway.read(amendment, max_date=custom.cutoff))
        early = analyst_case(custom.question, "2023-10-26T13:16:38Z")
        with early.corpus().session(early.cutoff) as gateway:
            self.assertFalse(gateway.read(amendment, max_date=early.cutoff))
        self.assertNotEqual(custom.id, early.id)
        self.assertEqual(
            default_route(analyst_case(custom.question, "2022-07-11", "twitter")),
            "dispute",
        )
        self.assertEqual(default_route(case_for("tradeweb")), "team")
        self.assertEqual(default_route(case_for("toyota-buybacks")), "visual")

    def test_invalid_intakes_never_start_workers(self):
        for field, values in {
            "budget": ("0", "-3", "NaN", "101", "1e2", None),
            "question": ("   ", "a" * 2001, "\x00money"),
            "cutoff": ("2022-15-01", "2022-01-02T11:05:00", None),
            "collection": ("unknown", []),
            "request_id": ("short",),
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.assert_status(
                        400, "/api/runs", body={**self.data, field: value}
                    )
        self.assertEqual(list(self.root.iterdir()), [])
        self.provider.count.assert_not_called()

    def test_duplicate_post_while_active_and_after_restart(self):
        counting, release = Event(), Event()

        def count(_):
            counting.set()
            if not release.wait(5):
                raise AssertionError("Test did not release preflight")
            return 501

        self.provider.count.side_effect = count
        self.addCleanup(release.set)
        with ThreadPoolExecutor(2) as pool:
            results = list(
                pool.map(
                    lambda _: self.assert_status(202, "/api/runs", body=self.data),
                    range(2),
                )
            )
        self.assertEqual(results[0], results[1])
        self.assertTrue(counting.wait(5))
        key = results[0]["id"]
        self.assertEqual(list(self.root.iterdir()), [self.root / key])
        self.assert_status(409, "/api/runs", body={**self.data, "budget": "0.11"})
        detail = self.assert_status(200, "/api/runs/" + key)
        self.assertEqual(detail["state"], "running")
        self.assertTrue(detail["can_pause"])
        self.assertIsNone(detail["report"])
        self.assert_status(409, "/api/runs/" + key + "/memo.md")
        self.assert_status(202, "/api/runs/" + key + "/pause", body={})
        release.set()
        self.desk.close()
        record = load(self.root / key / "run.json")
        self.assertEqual((record["state"], record["calls"]), ("paused", {}))
        self.assertNotIn("resumptions", record)
        self.assertEqual(saved_case(record).question, self.data["question"])
        other = Desk(self.root, provider=self.provider)
        self.addCleanup(other.close)
        self.assertEqual(other.submit(self.data), results[0])
        self.assertTrue(other.inspect(key)[0]["can_resume"])
        self.provider.count.assert_called_once()
        self.provider.generate.assert_not_called()

    def test_budget_exhaustion_and_ownership_from_cli_queue(self):
        case = analyst_case(self.data["question"], self.data["cutoff"])
        folder = self.root / "from-cli"
        queue_research(case, folder, Decimal("0.000001"))
        key = local_id(folder)
        self.assertEqual(self.desk.library()[0]["id"], key)
        with own_run(folder):
            self.assert_status(400, "/api/runs/" + key + "/resume", body={})
        self.provider.count.return_value = 1000
        self.assert_status(202, "/api/runs/" + key + "/resume", body={})
        with self.desk.lock:
            job = self.desk.jobs.get(key)
        if job:
            job[0].join(5)
        detail = self.assert_status(200, "/api/runs/" + key)
        self.assertEqual(
            (
                detail["state"],
                detail["spent"],
                detail["reserved"],
                detail["call_count"],
            ),
            ("failed", "0", "0", 0),
        )
        self.assertIsNone(detail["report"])
        self.provider.generate.assert_not_called()

    def test_library_export_and_evidence_under_original_cutoff(self):
        library = self.assert_status(200, "/api/runs")
        self.assertEqual({r["id"] for r in library["runs"]}, set(EXAMPLES))
        run = "/api/runs/example-twitter"
        detail = self.assert_status(200, run)
        self.assertEqual(detail["sources"][2]["locator"], "Board statement")
        self.assertTrue(all(c["state"] == "Completed" for c in detail["activity"]))
        self.assertIn("Consult Astra", [d["label"] for d in detail["decisions"]])
        evidence = self.assert_status(200, run + "/passages/3")
        self.assertIn("Delaware Court of Chancery", evidence["text"])
        self.assertTrue(evidence["url"].startswith("https://www.sec.gov/Archives/"))
        self.assert_status(404, run + "/passages/99")
        status, headers, body = self.request(run + "/memo.md")
        self.assertEqual(status, 200)
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(
            body, (self.desk.directory("example-twitter") / "memo.md").read_bytes()
        )
        self.assert_status(409, run + "/resume", body={})
        toyota = self.assert_status(200, "/api/runs/example-toyota")
        self.assertIn("table", [b["kind"] for b in toyota["report"]])
        self.provider.count.assert_not_called()

    def test_unreleased_and_modified_records_do_not_export(self):
        folder = self.root / "cli-copy"
        folder.mkdir()
        shutil.copyfile(
            PROJECT_ROOT / "evaluations/cli_resume/proof/in-flight.json",
            folder / "run.json",
        )
        run = "/api/runs/" + local_id(folder)
        view = self.assert_status(200, run)
        self.assertEqual(view["sources"], [])
        self.assertIsNone(view["report"])
        self.assertGreater(Decimal(view["reserved"]), 0)
        for private_key in (
            "calls",
            "provider",
            "worker_reports",
            "evidence",
            "reviewer_payload",
        ):
            self.assertNotIn(private_key, view)
        self.assert_status(409, run + "/passages/1")
        shutil.copyfile(
            self.desk.directory("example-twitter") / "run.json", folder / "run.json"
        )
        (folder / "memo.md").write_text("Unreviewed change")
        self.assert_status(409, run + "/memo.md")
        self.assert_status(409, run + "/passages/1")
        (folder / "memo.md").unlink()
        record = load(folder / "run.json")
        record["cutoff"] = "2025-01-01"
        (folder / "run.json").write_text(json.dumps(record))
        self.assert_status(409, run)

    def test_browser_trial_replays_with_a_reviewer_correction_and_zero_generation(self):
        from evaluations.analyst_desk.verify import PROOF, verify

        with patch("scripts.team_meter.OpenAIResponses", side_effect=AssertionError):
            self.assertTrue(verify()["approved"])
            folder = self.root / "browser-case"
            shutil.copytree(PROOF, folder)
            path = "/api/runs/" + local_id(folder)
            detail = self.assert_status(200, path)
            self.assertEqual(detail["review"], "approved")
            self.assertIn(
                "Release reviewed memo", [d["label"] for d in detail["decisions"]]
            )
            # A working instruction asserted a forecast period that review found
            # unsupported. The activity UI exposes the action separately from claims.
            self.assertNotIn("2023 adjusted expense", json.dumps(detail["decisions"]))
            source = self.assert_status(200, path + "/passages/1")
            self.assertIn("$695 million", source["text"])

    def test_only_local_clients_can_initiate_work(self):
        with patch.object(self.desk, "submit") as start:
            self.assert_status(403, "/api/runs", body=self.data, token=False)
            self.assert_status(
                403, "/api/runs", body=self.data, origin="http://other.test"
            )
            self.assert_status(403, "/api/session", host="other.test:8765")
        start.assert_not_called()
        for url in (
            "/.env.local",
            "/api/runs/example-twitter/provider-ids.json",
            "/evaluations/scoring.json",
        ):
            self.assert_status(404, url)
        self.assertEqual(
            self.assert_status(200, "/api/session")["token"], self.desk.token
        )
