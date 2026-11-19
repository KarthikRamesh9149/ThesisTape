import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from scripts import temporal_web as web
from scripts.research_desk_agent import Paragraph, compile_paragraph, literal_in

DAY = "2024-08-01"
ORIGINAL = "https://example.org/issuer/releases/announced"


class TemporalTests(unittest.TestCase):
    def test_future_snapshot_title_url_and_body_are_never_returned(self):
        calls = []

        def http(url, **limits):
            calls.append(url)
            u = urlsplit(url)
            if u.hostname == "lite.duckduckgo.com":
                self.assertEqual(parse_qs(u.query)["df"], ["2023-08-02..2024-08-01"])
                return f'<a class="result-link" href="{ORIGINAL}">SECRET FUTURE HEADLINE</a>'.encode()
            if u.hostname == "archive.org":
                return json.dumps(
                    {
                        "archived_snapshots": {
                            "closest": {
                                "available": True,
                                "status": "200",
                                "timestamp": "20240802000000",
                                "url": "https://web.archive.org/web/20240802000000/"
                                + ORIGINAL,
                            }
                        }
                    }
                ).encode()
            self.fail("The later document must never be requested")

        with tempfile.TemporaryDirectory() as d:
            g = web.ArchiveGateway(DAY, Path(d), http=http)
            result = g.search("issuer releases announced", max_date=DAY)
            self.assertEqual(result["sources"], [])
            self.assertNotIn("SECRET", str(result))
            self.assertNotIn(ORIGINAL, str(result))
            count = len(calls)
            self.assertEqual(
                g.search("issuer releases announced", max_date=DAY), result
            )
            self.assertEqual(len(calls), count)
            for date in ("2024-08-02", "2024-07-31"):
                with self.assertRaisesRegex(ValueError, "exact cutoff"):
                    g.search("issuer releases announced", max_date=date)
            with self.assertRaises(TypeError):
                g.search("issuer releases announced")

    def test_original_capture_revision_cache_and_redirect(self):
        def http(url, **kw):
            host = urlsplit(url).hostname
            if host == "lite.duckduckgo.com":
                return f'<a class="result-link" href="{ORIGINAL}">Current metadata</a>'.encode()
            if host == "archive.org":
                return json.dumps(
                    {
                        "archived_snapshots": {
                            "closest": {
                                "status": "200",
                                "available": True,
                                "timestamp": "20240701000000",
                                "url": "https://web.archive.org/web/20240701000000/"
                                + ORIGINAL,
                            }
                        }
                    }
                ).encode()
            if host == "web.archive.org":
                return b"<title>Issuer release</title><p>The board declared USD 25 million in dividends.</p><aside><p>Unrelated sidebar with market data.</p></aside>"
            if host == "en.wikipedia.org":
                query = parse_qs(urlsplit(url).query)
                self.assertEqual(query["rvstart"], [DAY + "T23:59:59Z"])
                return json.dumps(
                    {
                        "query": {
                            "pages": [
                                {
                                    "title": "Later public title SECRET",
                                    "revisions": [
                                        {
                                            "timestamp": "2024-06-01T10:00:00Z",
                                            "revid": 123,
                                            "slots": {
                                                "main": {
                                                    "content": "This historical page describes the issuer and its dividend policy before the decision."
                                                }
                                            },
                                        }
                                    ],
                                }
                            ]
                        }
                    }
                ).encode()
            self.fail(host)

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            g = web.ArchiveGateway(DAY, root, http)
            result = g.search(
                "issuer dividend decision", max_date=DAY, titles=["Issuer"]
            )
            self.assertEqual(len(result["sources"]), 2)
            self.assertNotIn("SECRET", str(result))
            self.assertNotIn("sidebar", str(result))
            later = deepcopy(result["sources"][0])
            later["available"] = "2024-08-02"
            with self.assertRaises(ValueError):
                web.checked_doc(later, DAY)
            p = next(root.glob("*.json"))
            v = json.loads(p.read_bytes())
            v["body"]["sources"][0]["blocks"][0] = "Changed"
            p.write_text(json.dumps(v))
            with self.assertRaisesRegex(ValueError, "Cached"):
                g.search("issuer dividend decision", max_date=DAY, titles=["Issuer"])

    def test_sec_facts_keep_only_eligible_accession(self):
        def http(url, **kw):
            d = {
                "cik": 1234,
                "entityName": "SECRET New Name",
                "facts": {
                    "us-gaap": {
                        "Revenues": {
                            "units": {
                                "USD": [
                                    {
                                        "accn": "0000001234-24-000001",
                                        "filed": "2024-07-20",
                                        "form": "10-Q",
                                        "start": "2024-04-01",
                                        "end": "2024-06-30",
                                        "val": 875,
                                    },
                                    {
                                        "accn": "0000001234-24-000002",
                                        "filed": "2024-10-20",
                                        "form": "10-Q/A",
                                        "start": "2024-04-01",
                                        "end": "2024-06-30",
                                        "val": 991,
                                    },
                                ]
                            }
                        }
                    }
                },
            }
            return json.dumps(d).encode()

        found = web.sec_facts(http, "1234", DAY)
        self.assertEqual(len(found), 1)
        self.assertIn("875 USD", str(found[0]["blocks"]))
        self.assertNotIn("991", str(found))
        self.assertNotIn("SECRET", str(found))
        self.assertNotIn("0000001234-24-000002", str(found))

    def test_compile_preserves_signed_and_whole_figures_and_citation_ownership(self):
        for part, source in (
            ("200", "-200"),
            ("200", "(200)"),
            ("35", "35.19"),
            ("200", "1,200"),
            ("00", "200"),
        ):
            self.assertFalse(literal_in(part, source))
        passages = {
            "P1": {
                "text": "The issuer announced a dividend of USD 25 million. Net cash used was USD 8 million."
            }
        }
        ref = {"passage": "P1", "quote": "dividend of USD 25 million."}

        def render(text):
            p = Paragraph(section="Answer", text=text, citations=[ref])
            return compile_paragraph(p, "What was proposed in 2024?", DAY, passages, {})

        self.assertIn(
            "USD 25 million", render("The dividend was {{P1|USD 25 million}}.")["text"]
        )
        for text in (
            "The dividend was USD 30 million.",
            "The dividend was {{P1|USD 30 million}}.",
            "The dividend was {{P2|USD 25 million}}.",
            "The dividend was {{C1}}.",
        ):
            with self.assertRaises(ValueError):
                render(text)
