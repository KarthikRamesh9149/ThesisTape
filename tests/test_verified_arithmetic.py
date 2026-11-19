import unittest
from decimal import Decimal

from scripts import research_desk_agent as core
from scripts import reviewed_research as reviewed
from scripts import verified_arithmetic as math
from scripts.disney_hulu_case import CaseError
from scripts.paired_research import formula


class VerifiedArithmetic(unittest.TestCase):
    def setUp(self):
        self.passages = {
            "P1": {
                "text": "Prior year profit was 258,641 million. Current guide 200,000 million. COVID-19 affected production."
            },
            "P2": {
                "text": "The price of $848.02 included an estimated premium of 29 percent."
            },
        }
        self.rule = {"mode": "relative", "tolerance": "0.02", "unit": "million yen"}

    def op(self, operator, left, right=None):
        return math.Calculation(
            metric="Illustrative computation",
            unit="units",
            operator=operator,
            left=left,
            right=right,
        )

    def test_bands_and_dependency_preserve_only_supported_arithmetic(self):
        result, errors = math.compile_plan(
            [
                self.op("subtract", "P1:200,000", "P1:258,641"),
                self.op("divide", "P2:848.02", "1.29"),
                self.op("band_lower", "P1:200,000"),
                self.op("band_upper", "P1:200,000"),
                self.op("subtract", "C4", "C3"),
                self.op("add", "C2", "P1:200,000"),
            ],
            self.passages,
            self.rule,
        )
        self.assertEqual(
            {k: Decimal(v["value"]) for k, v in result.items()},
            {"C1": -58641, "C3": 196000, "C4": 204000, "C5": 8000},
        )
        self.assertEqual(set(errors), {"C2", "C6"})
        self.assertIn(
            "200,000 − |200,000| × 2% (question tolerance)", formula("C3", result["C3"])
        )
        self.assertEqual(result["C5"]["evidence_ids"], ["P1"])

    def test_premium_base_and_absolute_band_show_their_source(self):
        result, errors = math.compile_plan(
            [
                self.op("price_before_premium", "P2:848.02", "P2:29"),
                self.op("band_upper", "P2:848.02"),
            ],
            self.passages,
            {"mode": "absolute", "tolerance": "0.05"},
        )
        self.assertFalse(errors)
        self.assertEqual(
            Decimal(result["C1"]["value"]).quantize(Decimal("0.01")), Decimal("657.38")
        )
        self.assertEqual(Decimal(result["C2"]["value"]), Decimal("848.07"))
        self.assertIn("848.02 ÷ (1 + 29 ÷ 100)", formula("C1", result["C1"]))

    def test_unbound_or_altered_numbers_do_not_survive(self):
        result, errors = math.compile_plan(
            [
                self.op("band_lower", "P1:200,001"),
                self.op("band_upper", "P1:200,000"),
                self.op("price_before_premium", "P2:848.02", "P2:2"),
            ],
            self.passages,
            None,
        )
        self.assertFalse(result)
        self.assertEqual(len(errors), 3)
        para = core.Paragraph(
            section="Evidence",
            text="COVID-19 affected production and costs increased by 99%.",
            citations=[core.Ref(passage="P1", quote="COVID-19 affected production.")],
        )
        with self.assertRaises(CaseError):
            core.compile_paragraph(
                reviewed.mark_identifiers(para, self.passages),
                "What happened?",
                "2020-05-08",
                self.passages,
                {},
            )
        para = para.model_copy(update={"text": "COVID-19 affected production."})
        compiled = core.compile_paragraph(
            reviewed.mark_identifiers(para, self.passages),
            "What happened?",
            "2020-05-08",
            self.passages,
            {},
        )
        self.assertEqual(compiled["text"], para.text)

    def test_a_source_cannot_supply_a_figure_through_a_label(self):
        op = self.op("subtract", "P1:200,000", "P1:258,641").model_copy(
            update={"metric": "Claimed profit 8000"}
        )
        result, errors = math.compile_plan([op], self.passages, self.rule)
        self.assertFalse(result)
        self.assertIn("C1", errors)
