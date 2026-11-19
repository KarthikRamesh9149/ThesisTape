import json
import unittest
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from evaluations.forecast_specialist import experiment as ev
from scripts.baseline_evidence import strip_forecast_metadata
from scripts.disney_hulu_case import CaseError
from scripts.edgar_exhibits import sections, submission_exhibit
from scripts.forecast_specialist import analyze
from scripts.retrieval_gateway import Corpus
from scripts.sol_baseline import json_bytes
from scripts.sol_research import search_numbers
from scripts.team_report import render_candidate
from scripts.team_research import case_for, research


class ForecastTests(unittest.TestCase):
    def test_reporting_metadata_cannot_supply_a_financial_amount(self):
        from decimal import Decimal

        def numeric_content(value, hits):
            return {
                Decimal(v) for v in search_numbers(strip_forecast_metadata(value, hits))
            }

        hits = list(self.opened.values())
        self.assertEqual(
            numeric_content("On 24 May 2023, NVIDIA reported FY2024 Q1 results.", hits),
            set(),
        )
        self.assertIn(
            Decimal(24), numeric_content("The company paid USD 24 billion.", hits)
        )
        self.assertIn(
            Decimal(24), numeric_content("Shipments arrived on 24 May 2023.", hits)
        )
        self.assertIn(Decimal(1), numeric_content("Revenue rose 1%.", hits))
        self.assertIn(Decimal(3), numeric_content("Q3 sales recovered.", hits))

    def setUp(self):
        self.case = case_for("nvidia")
        self.corpus = Corpus(self.case.archive)
        self.s = json.loads((self.case.archive / "catalog.json").read_bytes())[
            "sources"
        ][0]
        with self.corpus.session(self.case.cutoff) as gateway:
            (hit,) = gateway.read(
                self.s["url"],
                locator="Second-quarter revenue outlook",
                max_date=self.case.cutoff,
            )
        self.opened = {"P1": hit}

    def test_original_exhibits_and_evaluator_outcome_have_separate_inventories(self):
        self.assertEqual(
            self.corpus.verify(), {"nvda-q1fy24pr": 4, "nvda-q1fy24cfocommentary": 4}
        )
        path = ev.EVALUATOR / "outcome"
        later = json.loads((path / "catalog.json").read_bytes())["sources"][0]
        self.assertFalse(
            (self.case.archive / "objects" / (later["sha256"] + ".htm")).exists()
        )
        self.assertEqual(
            [p.name for p in (self.case.archive.parent / "agent").iterdir()],
            ["question.json"],
        )
        # Even if an operator supplies the later archive, its search and direct reads cannot cross the cutoff.
        corpus = Corpus(path)
        with corpus.session("2023-08-24") as gateway:
            self.assertTrue(gateway.read(later["url"], max_date=gateway.cutoff))
        with corpus.session(self.case.cutoff) as gateway:
            self.assertEqual(
                gateway.search("NVIDIA reported revenue", max_date=gateway.cutoff), ()
            )
            self.assertEqual(gateway.read(later["url"], max_date=gateway.cutoff), ())
        with self.corpus.session("2023-05-24T20:22:17Z") as gateway:
            self.assertEqual(gateway.read(self.s["url"], max_date=gateway.cutoff), ())
        outcome = ev.actual_outcome(ev.protocol())
        self.assertEqual(
            (
                outcome["below_boundary_usd_billions"],
                outcome["above_boundary_usd_billions"],
                outcome["actual_usd_billions"],
                outcome["bucket"],
            ),
            ("10.7800", "11.2200", "13.51", "above"),
        )

    def test_wrong_exhibit_index_or_hindsight_numbers_fail_verification(self):
        objects = self.case.archive / "objects"
        contents = (objects / (self.s["sha256"] + ".htm")).read_bytes()
        index = (objects / (self.s["selection"]["index_sha256"] + ".htm")).read_bytes()
        with self.assertRaisesRegex(ValueError, "byte size"):
            list(sections(self.s, contents, index.replace(b">306664<", b">306665<")))
        wrong_title = {**self.s, "title": self.s["title"].replace("Q1", "Q3")}
        with self.assertRaisesRegex(ValueError, "printed fiscal period"):
            list(sections(wrong_title, contents, index))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            submission_exhibit(contents + contents, "q1fy24pr.htm")
        point = {
            "text": "This quarter could reflect USD 13.51 billion.",
            "evidence_ids": ["P1"],
        }
        response = {
            "choice": "above",
            **dict.fromkeys(
                ("base_and_horizon", "causal_driver", "alternative", "revision_signal"),
                point,
            ),
            "historical_comparison": None,
        }
        ask = Mock(return_value=json.dumps(response))
        with self.assertRaisesRegex(CaseError, "printed"):
            analyze(self.case, self.opened, ask)
        self.assertEqual(
            [c.args[0] for c in ask.call_args_list],
            ["forecast.analyze", "forecast.repair"],
        )

    def test_claims_bind_choice_and_source_references(self):
        p = {
            "text": "The issuer guided to USD 11 billion, plus or minus 2%.",
            "citations": [
                {"passage_id": "P1", "quote": "$11.00 billion, plus or minus 2%"}
            ],
        }
        draft = {
            "choice": "within",
            **dict.fromkeys(
                ("known_facts", "base_case", "alternative", "revision_signal"), p
            ),
        }
        item = render_candidate(self.case, self.opened, "write", json_bytes(draft))
        self.assertIn(b"## Base case (within)", item.memo)
        self.assertTrue(
            item.claims()["base_case"]["text"].startswith("Base case (within)")
        )
        bad = deepcopy(draft)
        bad["choice"] = "other"
        with self.assertRaisesRegex(CaseError, "options"):
            render_candidate(self.case, self.opened, "write", json_bytes(bad))
        ask = Mock()
        with self.assertRaisesRegex(CaseError, "forecast question"):
            research(case_for("disney"), self.corpus, ask, route="forecast")
        ask.assert_not_called()

    def test_withheld_forecast_receives_no_paid_grade(self):
        _, grade = ev.grade_workflow(
            None,
            {"memo_sha256": None, "corpus_sha256": self.corpus.digest, "pricing": {}},
            {},
            ev.protocol(),
        )
        ask = Mock()
        output, trace = grade(ask)
        self.assertTrue(all(not m["awarded"] for m in trace["grades"].values()))
        self.assertEqual(json.loads(output), trace["grades"])
        ask.assert_not_called()
        with TemporaryDirectory() as temp, self.assertRaises(CaseError):
            ev.check_export(Path(temp), "memo.md", b"unrecorded")
