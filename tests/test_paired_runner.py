"""Exercise the six real workflows with a fixture transport. These outputs are never scored."""

import json
import tempfile
import threading
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from evaluations.paired import corpus, registration, runner
from scripts.disney_hulu_case import CaseError
from scripts.paired_packet import PacketCorpus
from scripts.paired_research import ARMS
from scripts.paired_tools import Operation, calculate
from scripts.sol_provider import LUNA, TERRA, Result
from scripts.team_meter import verify_ledger


class FixtureTransport:
    """Return schema-shaped decisions from the input to test routing, citations and durable replay."""

    def __init__(self):
        self.requests = []
        self.lock = threading.Lock()

    def count(self, payload):
        return 900

    def generate(self, payload):
        with self.lock:
            self.requests.append(payload)
        data = payload["input"]
        if isinstance(data, list):
            data = data[0]["content"][0]["text"]
        data = json.loads(data)
        if "tools" in payload:
            if not data.get("searches"):
                name, args = (
                    "search_archive",
                    {"query": "Consolidated financial forecast profit attributable to owners of parent"},
                )
            else:
                name, args = (
                    "read_passages",
                    {"passage_ids": [r["id"] for r in data["searches"][-1]["results"][:2]]},
                )
            item = {"type": "function_call", "name": name, "arguments": json.dumps(args)}
        else:
            kind = payload["text"]["format"]["name"]
            if kind == "Assignments":
                value = {"filings": "Find the forecast and prior year.", "contrary": "Identify supply risks."}
            elif kind == "Selections":
                value = {
                    "findings": [
                        {"span_id": p["spans"][0]["id"], "kind": "fact"}
                        for p in data["passages"]
                        if p["spans"]
                    ]
                }
            elif kind == "Decision":
                value = {"action": "write", "instruction": "Use the dated evidence."}
            elif kind == "ToolPlan":
                value = {
                    "calculations": [],
                    "inspect_page": data["passages"][0]["id"],
                    "visual_question": "Read the page heading.",
                }
            elif kind == "PageReading":
                value = {"observation": "Fixture reading from the supplied page."}
            elif kind == "Outlook":
                p = {
                    "text": "Fixture forecast based on the opened evidence.",
                    "evidence_ids": [data["passages"][0]["id"]],
                }
                value = {
                    "choice": "within",
                    "base_and_horizon": p,
                    "causal_driver": p,
                    "alternative": p,
                    "revision_signal": p,
                    "historical_comparison": None,
                }
            elif kind == "ResearchMemo":
                hit = data["passages"][0]
                p = {
                    "text": "Fixture paragraph for the routing test.",
                    "citations": [{"passage_id": hit["id"], "quote": hit["text"][:80].strip()}],
                    "calculation_ids": [],
                }
                value = {
                    "facts": [p],
                    "analysis": p,
                    "alternative_and_signal": p,
                    "direction": "within" if data["forecast_rule"] else None,
                }
            elif kind == "DisputeReview":
                value = {
                    "findings": [
                        {
                            "claim_id": i,
                            "verdict": "supported",
                            "reason": "Fixture review.",
                            "evidence_ids": [c["citations"][0]["passage_id"]],
                        }
                        for i, c in data["claims"].items()
                    ],
                    "addresses_question": True,
                    "coverage_reason": "Fixture coverage.",
                    "conflict": {
                        "kind": "none",
                        "treatment": "not_applicable",
                        "issue": "Fixture conflict check.",
                        "witnesses": [],
                        "controlling_passage": None,
                    },
                }
            else:
                raise AssertionError(kind)
            item = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(value)}],
            }
        body = {
            "id": f"fixture-{id(payload)}",
            "status": "completed",
            "service_tier": "default",
            "model": payload["model"],
            "usage": {"input_tokens": 900, "input_tokens_details": {}, "output_tokens": 100},
            "output": [item],
        }
        return Result(body, "fixture-request", 0)


class PairedRunnerTests(unittest.TestCase):
    def setUp(self):
        self.storage = tempfile.TemporaryDirectory()
        self.addCleanup(self.storage.cleanup)
        self.root = Path(self.storage.name)
        proto, self.rows = registration.schedule()
        self.row = self.rows[0]
        self.manifest = {"schedule": self.rows, "budgets_usd": proto["budget_usd"]}
        self.packet = self.root / "input"
        corpus.export_development(self.row["case_id"], self.packet)
        self.corpus = PacketCorpus(self.packet, self.row["packet_sha256"])

    def ticket(self, arm):
        return runner.admission(self.manifest, self.row, arm)

    def test_six_arms_dispatch_registered_models_through_meter_and_replay(self):
        self.assertEqual(self.row["case_id"], "nintendo-fy2021-guidance")
        providers = {}
        for arm in self.row["arms"]:
            with self.subTest(arm=arm):
                provider = providers[arm] = FixtureTransport()
                ticket = self.ticket(arm)
                record = runner.work(self.corpus, ticket, self.root / arm, provider)
                self.assertEqual(record["state"], "complete", record.get("failure"))
                spent, pending = verify_ledger(record)
                self.assertLess(spent, Decimal("0.20"))
                self.assertEqual(pending, {})
                calls = record["calls"]
                policy = ARMS[arm]
                self.assertEqual("lead.plan" in calls, not policy.single and not policy.fixed_plan)
                self.assertEqual("lead.decide.1" in calls, not policy.single and not policy.fixed_plan)
                self.assertEqual("forecast.analyze" in calls, not policy.single and policy.forecast)
                self.assertEqual("review.initial" in calls, not policy.single)
                self.assertEqual(calls["visual.read"]["model"], policy.model_override or TERRA)
                if not policy.single:
                    self.assertEqual(calls["filings.extract"]["model"], policy.model_override or LUNA)
                if arm in ("sol_team", "single_sol", "single_astra"):
                    self.assertEqual({call["model"] for call in calls.values()}, {policy.model_override})
                self.assertTrue(all(p["reasoning"]["effort"] != "none" for p in provider.requests))
                memo = runner.replay_assignment(
                    self.root / arm, self.corpus, runner.config(self.corpus, ticket), record
                )
                self.assertEqual(memo, (self.root / arm / "memo.md").read_bytes())
                n = len(provider.requests)
                runner.work(self.corpus, ticket, self.root / arm, provider)
                self.assertEqual(len(provider.requests), n)

        def prompts(arm):
            return sorted(
                json.dumps({k: v for k, v in p.items() if k != "model"}, sort_keys=True)
                for p in providers[arm].requests
            )

        self.assertEqual(prompts("single_astra"), prompts("single_sol"))
        self.assertEqual(prompts("sol_team"), prompts("mixed"))

    def test_one_full_budget_per_assignment_and_no_restart_after_exhaustion(self):
        class TooLarge(FixtureTransport):
            def count(self, _):
                return 110000

        provider = TooLarge()
        ticket = self.ticket("single_sol")
        blocked = runner.work(self.corpus, ticket, self.root / "single_sol", provider)
        self.assertEqual(blocked["state"], "budget_exhausted")
        self.assertEqual(provider.requests, [])
        clean = FixtureTransport()
        runner.work(self.corpus, ticket, self.root / "single_sol", clean)
        self.assertEqual(clean.requests, [])
        other = runner.work(self.corpus, self.ticket("single_astra"), self.root / "single_astra", clean)
        self.assertEqual(other["state"], "complete")
        self.assertEqual(other["budget_usd"], "0.20")

    def test_unknown_dispatch_survives_restart_without_a_second_generation(self):
        class Disconnected(FixtureTransport):
            def generate(self, payload):
                self.requests.append(payload)
                raise CaseError("Unconfirmed provider response")

        provider = Disconnected()
        ticket = self.ticket("single_sol")
        for _ in range(2):
            record = runner.work(self.corpus, ticket, self.root / "single_sol", provider)
            self.assertEqual(record["state"], "needs_reconciliation")
            self.assertGreater(Decimal(record["unreconciled_reservation_usd"]), 0)
        self.assertEqual(len(provider.requests), 1)

    def test_terminal_restart_recovers_timing_and_missing_memo_without_generation(self):
        ticket = self.ticket("single_sol")
        directory = self.root / "run"
        record = runner.work(self.corpus, ticket, directory, FixtureTransport())
        memo = (directory / "memo.md").read_bytes()
        for key in ("finished_at", "wall_ms", "rate_limit_backoff_ms"):
            del record[key]
        (directory / "memo.md").unlink()
        runner.publish(directory / "run.json", runner.json_bytes(record))
        fresh = FixtureTransport()
        recovered = runner.work(self.corpus, ticket, directory, fresh)
        self.assertEqual(fresh.requests, [])
        self.assertEqual((directory / "memo.md").read_bytes(), memo)
        self.assertGreaterEqual(recovered["wall_ms"], 0)
        self.assertEqual(runner.load(directory / "run.json")["finished_at"], recovered["finished_at"])

    def test_replay_rejects_changed_private_receipt(self):
        ticket = self.ticket("single_sol")
        directory = self.root / "run"
        record = runner.work(self.corpus, ticket, directory, FixtureTransport())
        receipt_path = next((directory / "receipts").glob("*.json"))
        envelope = runner.load(receipt_path)
        envelope["receipt"]["checkpoint_id"] = "different-checkpoint"
        envelope["sha256"] = runner.fingerprint(runner.json_bytes(envelope["receipt"]))
        runner.publish(receipt_path, runner.json_bytes(envelope))
        with self.assertRaisesRegex(CaseError, "receipt"):
            runner.replay_assignment(directory, self.corpus, runner.config(self.corpus, ticket), record)

    def test_packet_integrity_cutoff_and_evaluator_isolation(self):
        with self.assertRaisesRegex(CaseError, "cutoff"):
            self.corpus.session("2099-01-01T00:00:00Z")
        (self.packet / "scoring.json").write_text("later evidence")
        with self.assertRaisesRegex(CaseError, "exactly"):
            PacketCorpus(self.packet, self.row["packet_sha256"])
        (self.packet / "scoring.json").unlink()
        pdf = next((self.packet / "objects").iterdir())
        pdf.write_bytes(pdf.read_bytes() + b"changed")
        with self.assertRaisesRegex(ValueError, "SHA-256"):
            PacketCorpus(self.packet, self.row["packet_sha256"])

    def test_registered_order_reuse_and_blind_admission(self):
        factual = next(r for r in self.rows if len(r["arms"]) == 5)
        self.assertEqual(sum(len(r["arms"]) for r in self.rows), 68)
        with self.assertRaisesRegex(CaseError, "independent"):
            runner.admission(self.manifest, factual, "mixed_no_forecast")
        with self.assertRaisesRegex(CaseError, "registered development"):
            runner.event_row(self.manifest, "blind-case")
        with (
            patch.object(runner, "LIVE_ROOT", self.root / "live"),
            self.assertRaisesRegex(CaseError, "earlier"),
        ):
            runner.run_event(self.manifest, self.rows[1]["case_id"])

    def test_numeric_calculations_use_printed_operands_and_transitive_provenance(self):
        from types import SimpleNamespace

        opened = {
            "P1": SimpleNamespace(text="The amount fell from 1,200 to (150)."),
            "P2": SimpleNamespace(text="The comparison was 1 ¾ and 300. Another value was −1¾."),
        }

        def op(a, b, action):
            return Operation(label="Test", unit="units", left=a, right=b, operator=action)

        result = calculate(
            [op("P1:1,200", "P1:(150)", "add"), op("C1", "P2:300", "divide"), op("P2:1 ¾", "C2", "multiply")],
            opened,
        )
        self.assertEqual(Decimal(result["C3"]["value"]), Decimal("6.125"))
        self.assertEqual(result["C3"]["evidence_ids"], ["P1", "P2"])
        signed = calculate([op("P2:−1¾", "P2:1 ¾", "add")], opened)
        self.assertEqual(Decimal(signed["C1"]["value"]), 0)
        for invalid in ("P1:200", "P1:150", "C2", "P9:300"):
            with self.subTest(invalid=invalid), self.assertRaises(CaseError):
                calculate([op(invalid, "P2:300", "add")], opened)

    def test_registered_question_or_cap_cannot_be_swapped_at_replay(self):
        ticket = self.ticket("single_sol")
        record = runner.work(self.corpus, ticket, self.root / "run", FixtureTransport())
        for key in ("budget_usd", "arm", "packet_sha256"):
            with self.subTest(key=key), self.assertRaises(CaseError):
                changed = deepcopy(record)
                changed[key] = "other"
                runner.replay_assignment(
                    self.root / "run", self.corpus, runner.config(self.corpus, ticket), changed
                )
