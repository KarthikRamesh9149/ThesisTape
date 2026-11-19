import unittest
from decimal import Decimal

from scripts import research_desk_agent as core
from scripts import reviewed_research as reviewed
from scripts import verified_arithmetic as math
from scripts.disney_hulu_case import CaseError
from scripts.verified_arithmetic import formula


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


class TaskDates(unittest.TestCase):
    def test_full_date_must_come_from_an_eligible_source_or_the_question(self):
        from scripts.research_terms import compile_paragraph

        q = "Compare the result for March 2021, using the supplied tolerance."
        passages = {
            "P1": {
                "text": "Outlook for March 31, 2021. Profit was 200,000 million yen."
            }
        }

        def attempt(template):
            p = core.Paragraph(
                section="Answer",
                text=template,
                citations=[
                    core.Ref(passage="P1", quote="Profit was 200,000 million yen.")
                ],
            )
            return compile_paragraph(
                p,
                q,
                "2020-05-07",
                passages,
                {},
                {"mode": "relative", "tolerance": "0.02", "unit": "yen"},
            )["text"]

        self.assertEqual(
            attempt("The period ends {{Q|March 31, 2021}}."),
            "The period ends March 31, 2021.",
        )
        self.assertEqual(
            attempt("For {{Q|March 2021}}, the question defines a {{BAND}} tolerance."),
            "For March 2021, the question defines a ±2% tolerance.",
        )
        for template in (
            "The period ends {{Q|March 30, 2021}}.",
            "Profit was {{Q|200,001}} million.",
            "The original says {{P1|200,001}}.",
        ):
            with self.subTest(template=template), self.assertRaises(CaseError):
                attempt(template)


class FinancialLabels(unittest.TestCase):
    def test_periods_or_unverified_labels_cannot_discard_verified_operands_or_inject_a_number(
        self,
    ):
        passages = {
            "P1": {
                "text": "Net profit attributable to owners: 258,641 million in the prior year, 200,000 million in the current guidance."
            }
        }
        for metric in ("FY2020 change since Q1", "Pretend profit increased by 88"):
            op = math.Calculation(
                metric=metric,
                unit="JPY million",
                operator="subtract",
                left="P1:200,000",
                right="P1:258,641",
            )
            values, errors = math.compile_plan(
                [op], passages, None, normalize_labels=True
            )
            self.assertFalse(errors)
            self.assertEqual(Decimal(values["C1"]["value"]), -58641)
            rows = math.residual_rows({}, values, passages)
            self.assertIn("200,000 − 258,641", rows["K1"]["text"])
            self.assertNotIn("88", rows["K1"]["text"])
            for ref in rows["K1"]["citations"]:
                self.assertIn(ref["quote"], passages[ref["passage"]]["text"])
            self.assertFalse(
                math.residual_rows(
                    {"A1": {"calculation_ids": ["C1"]}}, values, passages
                )
            )
