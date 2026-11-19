from copy import deepcopy
from datetime import date
from decimal import Decimal
import json
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import unittest
from urllib.parse import unquote, urlsplit

try:
    import pydantic  # noqa: F401
except ModuleNotFoundError as error:
    raise unittest.SkipTest("Install requirements-baseline.txt to test the Sol baseline") from error

from scripts import baseline_evidence as evidence
from scripts import sol_baseline as baseline
from scripts import sol_provider as provider
from scripts.disney_hulu_case import CaseError, SAVED_CASE


def citation(passage_id, quote):
    return {"passage_id": passage_id, "quote": quote}


def draft():
    return {
        "expected_payment": {
            "text": "Disney expected to pay NBCUniversal about USD 8.61 billion by 1 December 2023 if all the capital contributions remained unpaid.",
            "citations": [citation("P1", "Disney expects it will pay NBCU approximately $8.61 billion")],
        },
        "valuation_basis": {
            "text": "USD 27.5 billion was the guaranteed floor for all of Hulu's equity.",
            "citations": [citation("P1", "Hulu’s equity fair value or a guaranteed floor value of $27.5 billion")],
        },
        "appraisal_and_uncertainty": {
            "text": "An appraisal above the floor could require an additional payment to NBCUniversal. The final price remained unknown.",
            "citations": [citation("P2", "If the equity fair value is finally determined to be greater than the guaranteed floor value")],
        },
        "reconciliation": {
            "gross": {"expression": "$9.17 billion", "citation": citation("P1", "approximately $9.17 billion")},
            "deductions": {"expression": "$567 million", "citation": citation("P1", "currently approximately $567 million")},
            "reported": {"expression": "$8.61 billion", "citation": citation("P1", "approximately $8.61 billion")},
        },
    }


class RecordedInterface:
    def __init__(self):
        self.count_calls = []
        self.generation_calls = []
        self.response = {
            "id": "resp_test",
            "model": provider.MODEL,
            "service_tier": "default",
            "status": "completed",
            "usage": {
                "input_tokens": 1400,
                "input_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 80},
                "output_tokens": 250,
            },
            "output": [
                {"type": "reasoning"},
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": json.dumps(draft())}],
                },
            ],
        }

    def count(self, payload):
        self.count_calls.append(deepcopy(payload))
        return 1400

    def generate(self, payload):
        self.generation_calls.append(deepcopy(payload))
        return provider.Result(self.response, "req_test", 850)


class SolBaselineTests(unittest.TestCase):
    def setUp(self):
        scratch = TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.directory = Path(scratch.name)
        self.case = evidence.load_case(SAVED_CASE / "agent")
        self.evidence = evidence.retrieve(self.case, max_date=self.case.cutoff)

    def test_prompt_contains_only_admitted_sources_and_cutoff(self):
        payload = baseline.generation_request(baseline.build_prompt(self.case, self.evidence))
        data = json.loads(payload["input"])
        self.assertEqual(data["cutoff"], "2023-11-02")
        self.assertEqual(data["filing_date"], "2023-11-01")
        self.assertIn("$8.61 billion", data["passages"][0]["text"])
        later = json.loads((SAVED_CASE / "evaluator/manifest.json").read_bytes())["source"]
        outbound = json.dumps(payload)
        for excluded in (later["title"], later["source_url"], later["id"], "$438.7 million"):
            self.assertNotIn(excluded, outbound)
        self.assertNotIn("tools", payload)
        self.assertFalse(payload["store"])

    def test_gateway_rejects_override_and_changes_to_pinned_source(self):
        with self.assertRaisesRegex(CaseError, "must equal"):
            evidence.retrieve(self.case, max_date=date(2025, 6, 10))
        folder = shutil.copytree(self.case.agent_dir, self.directory / "agent")
        loaded = evidence.load_case(folder)
        shutil.copyfile(SAVED_CASE / "evaluator/dis-20250609.htm", folder / "dis-20231101.htm")
        with self.assertRaisesRegex(CaseError, "SHA-256 mismatch"):
            evidence.retrieve(loaded, max_date=loaded.cutoff)

    def test_gateway_cannot_load_the_evaluator_or_a_modified_manifest(self):
        with self.assertRaises(CaseError):
            evidence.load_case(SAVED_CASE / "evaluator")
        folder = shutil.copytree(self.case.agent_dir, self.directory / "agent")
        path = folder / "manifest.json"
        manifest = json.loads(path.read_bytes())
        manifest["cutoff"] = "2025-06-10"
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(CaseError, "manifest differs"):
            evidence.load_case(folder)

    def test_memo_arithmetic_and_passage_links(self):
        memo, arithmetic = evidence.render(self.case, self.evidence, json.dumps(draft()))
        self.assertEqual(Decimal(arithmetic["calculated"]), Decimal("8603000000"))
        self.assertEqual(Decimal(arithmetic["reported_minus_calculated"]), Decimal("7000000"))
        self.assertEqual(Decimal(arithmetic["rounding_tolerance"]), Decimal("10500000"))
        self.assertIn("USD 8,603,000,000", memo)
        links = re.findall(r"\]\(([^)]+)\)", memo)
        self.assertEqual(len(links), 7)
        for link in links:
            self.assertEqual(link.split("#")[0], self.evidence.source["source_url"])
            fragment = urlsplit(link).fragment
            if fragment:
                snippet = unquote(fragment.removeprefix(":~:text="))
                self.assertTrue(any(snippet in passage.text for passage in self.evidence.passages))

    def test_memo_rejects_invented_citations_hindsight_and_wrong_scales(self):
        for invalid, expected_error in (
            (("quote", "This text never appeared in the filing"), "citation quote"),
            (
                ("text", "Disney expected USD 8.61 billion. An additional USD 438.7 million was due in 2025."),
                "figure in its cited evidence",
            ),
            (("text", "Disney expected to pay USD 8.61 million."), "include the reconciled"),
            (("text", "Disney expected USD 8.61 billion and a 50% stake."), "numerical claim"),
            (("text", "USD 8.61 billion. See https://example.org/."), "plain text"),
        ):
            with self.subTest(invalid=invalid):
                changed = draft()
                key, value = invalid
                if key == "quote":
                    changed["expected_payment"]["citations"][0]["quote"] = value
                else:
                    changed["expected_payment"]["text"] = value
                with self.assertRaisesRegex(CaseError, expected_error):
                    evidence.render(self.case, self.evidence, json.dumps(changed))

    def test_accepts_cutoff_framing_and_equivalent_decimal_notation(self):
        adjusted = draft()
        adjusted["expected_payment"]["text"] = (
            "As of 2 November 2023, Disney expected USD 8.61 billion assuming USD 567.0 million remained unpaid."
        )
        memo, _ = evidence.render(self.case, self.evidence, json.dumps(adjusted))
        self.assertIn("USD 567.0 million", memo)

    def test_rejects_swapped_operands_even_when_the_subtraction_reconciles(self):
        wrong = draft()
        values = wrong["reconciliation"]
        values["deductions"], values["reported"] = values["reported"], values["deductions"]
        with self.assertRaisesRegex(CaseError, "not the disclosed unpaid contribution deduction"):
            evidence.render(self.case, self.evidence, json.dumps(wrong))

    def test_rejects_an_inconsistent_calculation(self):
        wrong = draft()
        values = wrong["reconciliation"]
        original = values["reported"]["expression"]
        changed = "$8.79 billion"
        values["reported"]["expression"] = changed
        values["reported"]["citation"]["quote"] = values["reported"]["citation"]["quote"].replace(original, changed)
        passages = {p.id: p.text.replace(original, changed) for p in self.evidence.passages}
        with self.assertRaisesRegex(CaseError, "cannot reconcile"):
            evidence.check_reconciliation(evidence.Reconciliation.model_validate(values), passages)

    def test_reservation_checks_exact_rates_and_declines_a_small_budget(self):
        fake = RecordedInterface()
        self.assertEqual(provider.usage_cost(fake.response), Decimal("0.00515"))
        self.assertEqual(provider.reserve(1400, Decimal("0.05")), Decimal("0.04446"))
        with self.assertRaisesRegex(CaseError, "exceeds budget"):
            baseline.run(self.case, self.directory / "declined", Decimal("0.042"), fake)
        self.assertEqual(len(fake.count_calls), 1)
        self.assertEqual(fake.generation_calls, [])
        self.assertFalse((self.directory / "declined").exists())

    def test_end_to_end_saved_response_replays_byte_for_byte(self):
        fake, run_dir = RecordedInterface(), self.directory / "first"
        record = baseline.run(self.case, run_dir, Decimal("0.05"), fake)
        self.assertEqual(len(fake.count_calls), 1)
        self.assertEqual(len(fake.generation_calls), 1)
        self.assertEqual(fake.generation_calls[0], baseline.generation_request(fake.count_calls[0]))
        self.assertEqual(record["provider"]["request_id"], "req_test")
        self.assertEqual(record["retrieval"]["max_date"], self.case.cutoff.isoformat())
        self.assertEqual(record["state"], "complete")
        self.assertEqual(baseline.replay(self.case, run_dir), (run_dir / "memo.md").read_bytes())
        with self.assertRaisesRegex(CaseError, "new run directory"):
            baseline.run(self.case, run_dir, Decimal("0.05"), fake)
        self.assertEqual(len(fake.generation_calls), 1)
        record["calculation"]["reported"] = "8600000000"
        baseline.save_run(run_dir, record)
        with self.assertRaisesRegex(CaseError, "does not match"):
            baseline.replay(self.case, run_dir)

    def test_incomplete_provider_response_preserves_cost_and_prevents_replay(self):
        fake, run_dir = RecordedInterface(), self.directory / "failed"
        fake.response["status"] = "incomplete"
        with self.assertRaisesRegex(CaseError, "did not complete"):
            baseline.run(self.case, run_dir, Decimal("0.05"), fake)
        record = json.loads((run_dir / "run.json").read_bytes())
        self.assertEqual(record["state"], "failed")
        self.assertEqual(Decimal(record["estimated_model_cost_usd"]), Decimal("0.00515"))
        self.assertFalse((run_dir / "memo.md").exists())
        with self.assertRaisesRegex(CaseError, "not complete"):
            baseline.replay(self.case, run_dir)

    def test_unconfirmed_submission_retains_reservation_without_retry(self):
        class UnconfirmedInterface(RecordedInterface):
            def generate(self, payload):
                super().generate(payload)
                raise CaseError("No confirmed response")

        fake, run_dir = UnconfirmedInterface(), self.directory / "unconfirmed"
        with self.assertRaisesRegex(CaseError, "No confirmed response"):
            baseline.run(self.case, run_dir, Decimal("0.05"), fake)
        record = json.loads((run_dir / "run.json").read_bytes())
        self.assertEqual(record["state"], "failed")
        self.assertEqual(record["unreconciled_reservation_usd"], record["reservation_usd"])
        self.assertEqual(len(fake.generation_calls), 1)
        self.assertFalse((run_dir / "memo.md").exists())

    def test_replay_rejects_an_edited_cost_estimate(self):
        run_dir = self.directory / "cost"
        record = baseline.run(self.case, run_dir, Decimal("0.05"), RecordedInterface())
        record["estimated_model_cost_usd"] = "0.001"
        baseline.save_run(run_dir, record)
        with self.assertRaisesRegex(CaseError, "usage and cost"):
            baseline.replay(self.case, run_dir)
