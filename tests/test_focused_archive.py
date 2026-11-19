import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from evaluations.financegym.selection import choose, profile_for
from scripts.archive_receipts import RecordedHttp
from scripts.focused_archive import FocusedArchive, check_source, verify_transport
from scripts.packet_archive import SavedSearches
from scripts.sol_baseline import json_bytes


class DatedPassages(unittest.TestCase):
    def test_reopen_the_exact_archived_bytes_and_reproduce_context_for_each_subquestion(
        self,
    ):
        url = "https://agency.example.org/award"
        day = "2024-02-29"
        question = (
            "What financing was proposed, and which conditions govern disbursement?"
        )
        html = b"<html><title>Preliminary agreement</title><p>The preliminary financing is USD 8 million in proposed grants.</p><p>The award conditions include an audit and a signed agreement before disbursement.</p></html>"

        def transport(u, **kwargs):
            host = urlsplit(u).hostname
            if host == "lite.duckduckgo.com":
                return f'<a class="result-link" href="{url}">Current live title</a>'.encode()
            if host == "archive.org":
                return json_bytes(
                    {
                        "archived_snapshots": {
                            "closest": {
                                "available": True,
                                "status": "200",
                                "timestamp": "20240229080000",
                                "url": "https://web.archive.org/web/20240229080000/"
                                + url,
                            }
                        }
                    }
                )
            if host == "web.archive.org":
                return html
            raise AssertionError("Unexpected endpoint")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            run = root / "case"
            run.mkdir()
            (run / "run.json").write_bytes(
                json_bytes(
                    {
                        "cutoff": day,
                        "question": question,
                        "archive_version": FocusedArchive.VERSION,
                    }
                )
            )
            gateway = FocusedArchive(
                day,
                root / "cache",
                question=question,
                http=RecordedHttp(root / "http", transport=transport),
            )
            result = SavedSearches(gateway, run / "searches").search(
                "preliminary amount", max_date=day, titles=[], filing_terms="", limit=6
            )
            self.assertEqual(result["source_count"], 1)
            self.assertNotIn("Current live title", str(result))
            doc = result["sources"][0]
            self.assertIn("#:~:text=", doc["url"])
            self.assertIn("before disbursement", " ".join(doc["blocks"]))
            self.assertEqual(
                verify_transport(run, root / "http")["transport_requests"], 3
            )
            for item, cutoff in (
                ({**doc, "blocks": ["a different passage"]}, day),
                ({**doc, "url": doc["url"] + "new"}, day),
                (doc, "2024-02-28"),
            ):
                with self.subTest(cutoff=cutoff), self.assertRaises(ValueError):
                    check_source(item, cutoff)

    def test_independent_selection_keeps_an_inconclusive_team_out_of_forecasts(self):
        d = {
            "advance": False,
            "cutoff_violations": 0,
            "mixed": "mixed",
            "single": "single_astra",
        }
        financial = {
            "arms": {
                "evidence_team_v4": {"cases": 24, "both": 19},
                "quantitative_astra": {"cases": 24, "both": 14},
            },
            "paired_90pct_interval": [0, 0.3],
        }
        m = choose(d, financial)
        self.assertEqual(
            profile_for("How much revenue was reported?", m), "single_astra"
        )
        m = choose(d, {**financial, "paired_90pct_interval": [0.04, 0.3]})
        self.assertEqual(
            profile_for("How much revenue was reported?", m), "evidence_team_v4"
        )
        self.assertEqual(
            profile_for("How much revenue could the issuer generate next year?", m),
            "single_astra",
        )
        self.assertEqual(
            profile_for("Which shareholders approved the sale?", m), "single_astra"
        )
        with self.assertRaises(ValueError):
            choose({**d, "cutoff_violations": 1}, financial)
