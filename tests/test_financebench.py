import json
import unittest

from evaluations.financebench import grading
from evaluations.financebench.study import ARMS


class PublishedReferenceTests(unittest.TestCase):
    def test_grader_uses_only_an_answer_paragraph_and_its_own_cited_page(self):
        material = {
            "paragraphs": {
                "p1": {"text": "Annual revenue was 5.0 million.", "cited": ["P2"]}
            },
            "cited_passages": {
                "P1": {"text": "Revenue 9.0 million"},
                "P2": {"text": "Annual revenue 5.0 million"},
            },
        }
        verdict = {
            "correct": True,
            "supported": True,
            "answer_quote": "5.0 million",
            "answer_paragraph": "p1",
            "source_witnesses": [
                {
                    "paragraph": "p1",
                    "report_quote": "5.0 million",
                    "passage": "P2",
                    "source_quote": "revenue 5.0 million",
                }
            ],
            "reason": "The units and period agree.",
        }
        self.assertTrue(grading.validate(json.dumps(verdict), material)["correct"])
        for update in (
            {"answer_paragraph": "p2"},
            {"answer_quote": "50 million"},
            {"source_witnesses": []},
            {
                "source_witnesses": [
                    {
                        **verdict["source_witnesses"][0],
                        "passage": "P1",
                        "source_quote": "Revenue 9.0 million",
                    }
                ]
            },
        ):
            with self.subTest(update=update), self.assertRaises(ValueError):
                grading.validate(json.dumps({**verdict, **update}), material)

    def test_all_twenty_four_pairs_count_including_unreleased_reports(self):
        rows = []
        for i in range(24):
            for j, arm in enumerate(ARMS):
                passed = i < 12 and j == 0
                rows.append(
                    {
                        "case_id": str(i),
                        "arm": arm,
                        "type": "numerical",
                        "state": "complete" if passed else "withheld",
                        "correct": passed,
                        "supported": passed,
                        "primary": passed,
                        "settled_usd": "0.10",
                        "unconfirmed_usd": "0",
                    }
                )
        summary = grading.summary(rows)
        self.assertEqual(summary["difference"], 0.5)
        self.assertEqual(summary["arms"][ARMS[1]]["cases"], 24)
        self.assertEqual(summary["mcnemar_exact_two_sided_p"], 2 / 4096)
        with self.assertRaises(ValueError):
            grading.summary(rows[1:])
