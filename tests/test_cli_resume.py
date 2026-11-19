import json
import shutil
import subprocess
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event, Lock
from unittest.mock import Mock, patch

from scripts import research_team as runner
from scripts.disney_hulu_case import CaseError
from scripts.run_checkpoint import PauseRequested, load, own_run, publish, receipt_path
from scripts.sol_baseline import (
    PROJECT_ROOT,
    fingerprint,
    generation_request,
    json_bytes,
)
from scripts.sol_provider import Result
from scripts.team_meter import Meter, verify_ledger
from scripts.team_research import case_for


class SavedProvider:
    """Feed previously observed model outputs through the normal provider boundary."""

    def __init__(self, template):
        self.calls = template["calls"]
        self.counted = []
        self.generated = []
        self.lock = Lock()
        self.on_generation = lambda stage: None

    def count(self, prompt):
        for stage, call in self.calls.items():
            payload = generation_request(
                prompt, max_output_tokens=call["max_output_tokens"]
            )
            if fingerprint(json_bytes(payload)) == call["request_sha256"]:
                self.counted.append(stage)
                return call["input_tokens_preflight"]
        raise AssertionError("Unexpected prompt")

    def generate(self, payload):
        key = fingerprint(json_bytes(payload))
        stage, call = next(
            (s, c) for s, c in self.calls.items() if c["request_sha256"] == key
        )
        with self.lock:
            if stage in self.generated:
                raise AssertionError("Duplicate generation")
            self.generated.append(stage)
        self.on_generation(stage)
        output = call["output"]
        if isinstance(output, str):
            item = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": output}],
            }
        else:
            item = {
                "type": "function_call",
                "name": output["name"],
                "arguments": json.dumps(output["arguments"]),
            }
        body = {**call["provider"], "id": "offline-" + stage, "output": [item]}
        return Result(body, "request-" + stage, 0)


class ResumeTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.directory = Path(tmp.name) / "case"
        self.template = load(
            PROJECT_ROOT / "evaluations/material_dispute/trial/price-only/run.json"
        )
        self.provider = SavedProvider(self.template)
        self.case = case_for("twitter-price")
        self.pause = Event()

    def start(self):
        return runner.run(
            self.case,
            "dispute",
            self.directory,
            Decimal("0.08"),
            self.provider,
            pause=self.pause,
        )

    def pause_first(self):
        self.provider.on_generation = lambda stage: self.pause.set()
        with self.assertRaises(PauseRequested):
            self.start()
        self.provider.on_generation = lambda stage: None
        return load(self.directory / "run.json")

    def test_graceful_pause_only_dispatches_missing_work_and_terminal_resume_is_idempotent(
        self,
    ):
        before = self.pause_first()
        self.assertEqual(before["state"], "paused")
        self.assertEqual(list(before["calls"]), ["lead.memo"])
        self.assertFalse((self.directory / "memo.md").exists())
        final = runner.resume(self.directory, self.provider)
        self.assertEqual(self.provider.counted, ["lead.memo", "review.initial"])
        self.assertEqual(self.provider.generated, self.provider.counted)
        self.assertEqual(final["calls"]["lead.memo"], before["calls"]["lead.memo"])
        self.assertEqual(
            final["estimated_model_cost_usd"], self.template["estimated_model_cost_usd"]
        )
        memo = (self.directory / "memo.md").read_bytes()
        self.assertEqual(memo, runner.replay(self.case, final))
        path = self.directory / "run.json"
        timestamp = path.stat().st_mtime_ns
        (self.directory / "memo.md").unlink()
        for _ in range(2):
            self.assertEqual(runner.resume(self.directory, Mock(spec=[])), final)
            self.assertEqual((self.directory / "memo.md").read_bytes(), memo)
            self.assertEqual(path.stat().st_mtime_ns, timestamp)
        (self.directory / "memo.md").write_bytes(b"changed")
        with self.assertRaisesRegex(CaseError, "saved memo differs"):
            runner.resume(self.directory, Mock(spec=[]))

    def test_receipt_survives_death_before_settlement(self):
        with (
            patch.object(Meter, "settle", side_effect=SystemExit),
            self.assertRaises(SystemExit),
        ):
            self.start()
        raw = load(self.directory / "run.json")
        self.assertEqual(list(raw["events"]), [["reserve", "lead.memo"]])
        self.assertGreater(Decimal(raw["unreconciled_reservation_usd"]), 0)
        final = runner.resume(self.directory, self.provider)
        self.assertEqual(final["resumptions"][0]["recovered_receipts"], ["lead.memo"])
        self.assertEqual(self.provider.counted, ["lead.memo", "review.initial"])
        self.assertEqual(final["unreconciled_reservation_usd"], "0")
        self.assertEqual(
            final["estimated_model_cost_usd"], self.template["estimated_model_cost_usd"]
        )

    def test_unconfirmed_dispatch_stays_reserved_across_restarts(self):
        self.provider.on_generation = Mock(
            side_effect=CaseError("No confirmed response")
        )
        with self.assertRaisesRegex(CaseError, "No confirmed"):
            self.start()
        original = load(self.directory / "run.json")
        for _ in range(2):
            with self.assertRaisesRegex(CaseError, "remains reserved"):
                runner.resume(self.directory, Mock(spec=[]))
            latest = load(self.directory / "run.json")
            self.assertEqual(latest["events"], original["events"])
            self.assertEqual(latest["state"], "needs_reconciliation")
            spent, pending = verify_ledger(latest, partial=True)
            self.assertEqual(spent, 0)
            self.assertEqual(
                pending,
                {
                    "lead.memo": Decimal(
                        original["calls"]["lead.memo"]["reservation_usd"]
                    )
                },
            )
        self.assertFalse((self.directory / "memo.md").exists())

    def test_changed_identity_budget_request_output_or_private_receipt_stops_before_dispatch(
        self,
    ):
        self.pause_first()
        for mutation in ("budget", "cutoff", "request", "output", "receipt", "missing"):
            folder = self.directory.parent / mutation
            shutil.copytree(self.directory, folder)
            record = load(folder / "run.json")
            path = receipt_path(folder, "lead.memo")
            if mutation == "budget":
                record["budget_usd"] = "100"
            elif mutation == "cutoff":
                record["cutoff"] = "2025-01-01"
            elif mutation == "request":
                record["calls"]["lead.memo"]["request_sha256"] = "0" * 64
            elif mutation == "output":
                record["calls"]["lead.memo"]["output"] = "{}"
            elif mutation == "receipt":
                envelope = load(path)
                envelope["receipt"]["output"] = "{}"
                publish(path, json_bytes(envelope))
            else:
                path.unlink()
            publish(folder / "run.json", json_bytes(record))
            with self.subTest(mutation=mutation), self.assertRaises(CaseError):
                runner.resume(folder, Mock(spec=[]))

    def test_exclusive_owner_rejects_another_cli_and_releases_on_exit(self):
        self.pause_first()
        with own_run(self.directory):
            contender = subprocess.run(
                [
                    sys.executable,
                    "-B",
                    "-m",
                    "scripts.research_team",
                    "resume",
                    "--run-dir",
                    str(self.directory),
                ],
                capture_output=True,
                check=False,
            )
        self.assertEqual(contender.returncode, 1)
        self.assertIn(b"Another process owns", contender.stderr)
        self.assertEqual(
            runner.resume(self.directory, self.provider)["state"], "complete"
        )

    def test_resumed_budget_and_previously_skipped_assignment(self):
        before = self.pause_first()
        expensive = Mock()
        expensive.count.return_value = 28500
        with self.assertRaisesRegex(CaseError, "exceeds budget"):
            runner.resume(self.directory, expensive)
        expensive.generate.assert_not_called()
        after = load(self.directory / "run.json")
        self.assertEqual(after["events"], before["events"])
        self.assertEqual(
            after["estimated_model_cost_usd"], before["estimated_model_cost_usd"]
        )
        folder = self.directory.parent / "skip"
        folder.mkdir()
        meter = Meter(folder, {"budget_usd": "0.05"}, expensive)
        prompt = {"model": "gpt-6.2-astra"}
        self.assertIsNone(
            meter("dispute.adjudicate", prompt, 1536, keep_usd=Decimal("0.06"))
        )
        restored = Meter(
            folder, load(folder / "run.json"), Mock(spec=[]), continuing=True
        )
        restored.restore()
        self.assertIsNone(
            restored("dispute.adjudicate", prompt, 1536, keep_usd=Decimal("0.06"))
        )
        self.assertEqual(restored.record["events"], [["skip", "dispute.adjudicate"]])

    def test_legacy_settled_call_is_checked_and_reused(self):
        record = self.pause_first()
        for field in ("specification_sha256", "checkpoint_id"):
            del record[field]
        del record["calls"]["lead.memo"]["receipt_sha256"]
        receipt_path(self.directory, "lead.memo").unlink()
        publish(self.directory / "run.json", json_bytes(record))
        final = runner.resume(self.directory, self.provider)
        self.assertEqual(final["legacy_settled"], ["lead.memo"])
        self.assertEqual(self.provider.counted, ["lead.memo", "review.initial"])
        self.assertEqual(
            runner.replay(self.case, final), (self.directory / "memo.md").read_bytes()
        )

    def test_parallel_workers_finish_in_flight_and_resume_without_duplicate_calls(self):
        template = load(PROJECT_ROOT / "evaluations/first-team/disney-team/run.json")
        provider = SavedProvider(template)
        barrier = Barrier(2, timeout=8)

        def stop_two(stage):
            if stage in ("filings.search.1", "contrary.search.1"):
                barrier.wait()
                self.pause.set()

        provider.on_generation = stop_two
        case = case_for("disney")
        with self.assertRaises(PauseRequested):
            runner.run(
                case,
                "team",
                self.directory,
                Decimal("0.05"),
                provider,
                pause=self.pause,
            )
        paused = load(self.directory / "run.json")
        self.assertEqual(
            set(paused["calls"]), {"lead.plan", "filings.search.1", "contrary.search.1"}
        )
        self.assertEqual(paused["unreconciled_reservation_usd"], "0")
        provider.on_generation = lambda stage: None
        final = runner.resume(self.directory, provider)
        self.assertCountEqual(provider.generated, template["calls"])
        self.assertCountEqual(provider.counted, template["calls"])
        self.assertEqual(final["memo_sha256"], template["memo_sha256"])
        self.assertEqual(
            runner.replay(case, final), (self.directory / "memo.md").read_bytes()
        )
