import unittest

from evaluations.paired.grading_recovery import exhausted, verify_extended_ledger
from scripts import sol_provider


class MaskedGraderContinuation(unittest.TestCase):
    def test_real_masked_witnesses_and_grader_costs_reproduce_offline(self):
        from evaluations.paired import grade_evidence

        checked = grade_evidence.verify(grade_evidence.REVISION)
        self.assertEqual(checked["locked_assignments"], 48)
        self.assertEqual(checked["verified_generations"], 7)
        self.assertEqual(checked["confirmed_grading_usd"], "0.762824")

    def test_large_grader_limit_is_isolated_from_the_original_research_runtime(self):
        from decimal import Decimal

        record = {
            "grading_model": sol_provider.ASTRA,
            "output_limit": 8192,
            "earlier_grade_sha256": "a" * 64,
            "budget_usd": "0.79",
            "estimated_model_cost_usd": "0",
            "unreconciled_reservation_usd": "0",
            "events": [],
            "calls": {},
        }
        self.assertEqual(verify_extended_ledger(record), (Decimal(0), {}))
        with self.assertRaisesRegex(ValueError, "Invalid model output limit"):
            sol_provider.reservation_cost(5000, 8192, sol_provider.ASTRA)

    def test_only_a_confirmed_token_limit_without_an_assessment_qualifies(self):
        provider = {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "usage": {"output_tokens": 4096},
        }
        call = {"max_output_tokens": 4096, "provider": provider}
        self.assertEqual(
            exhausted({"calls": {"grade.asof": call}}), [("grade.asof", call)]
        )
        for other in (
            {"max_output_tokens": 4096},
            {**call, "output": "usable"},
            {**call, "provider": {**provider, "status": "completed"}},
            {
                **call,
                "provider": {**provider, "incomplete_details": {"reason": "other"}},
            },
            {**call, "provider": {**provider, "usage": {"output_tokens": 1024}}},
        ):
            self.assertFalse(exhausted({"calls": {"grade.asof": other}}))
