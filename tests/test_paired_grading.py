import json
import unittest
from copy import deepcopy

from evaluations.paired import analysis, grading


class EvaluatorTests(unittest.TestCase):
    def test_masked_evidence_and_award_require_exact_witnesses(self):
        url = "https://example.org/release.pdf#page=2"
        record = {
            "question": "What did the issuer disclose?",
            "cutoff": "2024-01-01",
            "arm": "secret-treatment",
            "evidence": {
                "P1": {
                    "source_id": "doc",
                    "title": "Original",
                    "published": "2023-01-01",
                    "locator": "PDF p2 §1",
                    "citation_url": url,
                    "text": "The company declared an ordinary dividend.",
                }
            },
        }
        memo = (
            "# Research\n\n## Findings\n\nAn ordinary dividend was declared. [Original]("
            + url
            + ")\n"
        ).encode()
        payload = grading.masked_context(record, memo, [])
        self.assertNotIn("secret-treatment", json.dumps(payload))
        w = {
            "paragraph": "p1",
            "report_quote": "ordinary dividend",
            "source_id": "P1",
            "source_quote": "declared an ordinary dividend",
        }
        result = {
            "points": grading.empty("Absent.")["points"],
            "claims": [{"verdict": "supported", "witness": w}],
        }
        result["points"][0].update(award=True, witness=w)
        self.assertEqual(grading.validate(json.dumps(result), payload), result)
        publication = deepcopy(result)
        for witness in [
            publication["points"][0]["witness"],
            publication["claims"][0]["witness"],
        ]:
            witness["source_id"] = "doc"
        self.assertEqual(grading.validate(json.dumps(publication), payload), result)
        with self.assertRaises(ValueError):
            grading.validate(json.dumps(publication), payload, publication_ids=False)
        for field, value in (
            ("source_id", "P2"),
            ("report_quote", "it paid"),
            ("source_quote", "special dividend"),
        ):
            changed = deepcopy(result)
            changed["points"][0]["witness"][field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                grading.validate(json.dumps(changed), payload)

    def test_outstanding_provider_charge_cannot_manufacture_cost_savings(self):
        common = {"F": 3, "R": 2, "O": 1}
        mixed = {
            **common,
            "cost_usd": "0.20",
            "confirmed_cost_usd": "0.14",
            "unreconciled_usd": "0.06",
        }
        single = {
            **common,
            "cost_usd": "0.21",
            "confirmed_cost_usd": "0.12",
            "unreconciled_usd": "0.09",
        }
        result = analysis.differences([(mixed, single)])
        self.assertEqual(result["cost_range"], [-0.07, 0.08])
        self.assertEqual(result["cost"], 0.08)

    def test_bootstrap_zero_quality_loss_and_cost_strictness(self):
        rows = []
        for i in range(24):
            for arm, price in (("mixed", "0.02"), ("single", "0.03")):
                rows.append(
                    {
                        "case_id": f"c{i:02}",
                        "stratum": str(i // 4),
                        "arm": arm,
                        "F": 2,
                        "R": 1,
                        "O": int(i % 2 == 0) if i < 16 else None,
                        "forward": i < 16,
                        "cost_usd": price,
                        "elapsed_ms": 400,
                        "memo_sha256": "h",
                        "qualified": False,
                        "material_claims": 2,
                        "supported_claims": 1,
                        "cutoff_violations": 0,
                        "publishers": ["example.com"],
                    }
                )
        spec = {"bootstrap_draws": 200, "bootstrap_seed": 42}
        r = analysis.blind_stats(rows, "mixed", "single", spec)
        self.assertTrue(r["advance"])
        self.assertEqual(r["quality_90pct_interval"], [0, 0])
        self.assertAlmostEqual(r["cost_usd_90pct_interval"][1], -0.01)
        self.assertIsNone(r["per_event"]["c23"]["quality"])
        self.assertIsNone(
            r["leave_one_recurring_publisher_out"]["example.com"]["quality"]
        )
        rows[0]["cutoff_violations"] = 1
        self.assertFalse(analysis.blind_stats(rows, "mixed", "single", spec)["advance"])
        rows[0]["cutoff_violations"] = 0
        for row in rows:
            row["cost_usd"] = "0.03"
        self.assertFalse(analysis.blind_stats(rows, "mixed", "single", spec)["advance"])
