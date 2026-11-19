import tempfile
import unittest
from pathlib import Path

from evaluations.financegym import title_lookup
from scripts.sol_baseline import fingerprint, json_bytes


class CapitalizationRecovery(unittest.TestCase):
    def test_only_case_changes_from_an_entity_in_the_question_and_an_empty_search_are_admitted(
        self,
    ):
        self.assertIn("NNN Reit", title_lookup.variants("NNN REIT"))
        self.assertTrue(
            all(s.casefold() == "nnn reit" for s in title_lookup.variants("NNN REIT"))
        )
        row = {
            "question": "How does NNN REIT evaluate this proposal?",
            "cutoff": "2025-06-25",
        }
        with tempfile.TemporaryDirectory() as temp:
            dirs = [Path(temp) / n for n in ("primary", "recovery")]
            query = {
                "query": "NNN REIT dividend announcement",
                "titles": ["NNN REIT", "CFO made-up premise"],
                "max_date": row["cutoff"],
                "version": title_lookup.FocusedArchive.VERSION,
                "limit": 6,
                "filing_terms": "AFFO",
            }
            body = {"request": query, "sources": []}
            for f in dirs:
                (f / "searches").mkdir(parents=True)
                target = f / "searches" / (fingerprint(json_bytes(query)) + ".json")
                target.write_bytes(
                    json_bytes({"body": body, "sha256": fingerprint(json_bytes(body))})
                )
            changes = title_lookup.requests(row, dirs)
            self.assertEqual(len(changes), 1)
            self.assertEqual(
                {k: v for k, v in changes[0].items() if k != "titles"},
                {k: v for k, v in query.items() if k != "titles"},
            )
            self.assertTrue(
                all(x.casefold() == "nnn reit" for x in changes[0]["titles"])
            )
            with self.assertRaises(ValueError):
                title_lookup.requests({**row, "cutoff": "2025-06-26"}, dirs)
            body["sources"] = [{"original": "any admitted source"}]
            target.write_bytes(
                json_bytes({"body": body, "sha256": fingerprint(json_bytes(body))})
            )
            self.assertEqual(title_lookup.requests(row, dirs), [])
