import copy
import json
import shutil
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stdout
from dataclasses import asdict
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from scripts.disney_hulu_case import SAVED_CASE
from scripts.retrieval_archive import restore
from scripts.retrieval_eval import evaluate
from scripts.retrieval_gateway import ARCHIVE, Corpus
from scripts.retrieval_gateway import main as gateway_cli
from scripts.retrieval_sources import compact


class RetrievalGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = json.loads((ARCHIVE / "catalog.json").read_bytes())
        cls.sources = {s["id"]: s for s in cls.catalog["sources"]}
        cls.corpus = Corpus()

    def variant(self, catalog):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        shutil.copytree(ARCHIVE / "objects", root / "objects")
        (root / "catalog.json").write_text(json.dumps(catalog))
        return root

    def mixed_disney(self):
        catalog = copy.deepcopy(self.catalog)
        later = json.loads((SAVED_CASE / "evaluator/manifest.json").read_bytes())
        s = later["source"]
        proof = next(i for i in later["edgar_indexes"] if i["source_id"] == s["id"])
        stamp = "2025-06-09T16:16:48-04:00"
        entry = {
            "id": s["id"],
            "title": "Disney June 2025 Hulu appraisal outcome",
            "family": s["id"],
            "edition": s["accession"],
            "url": s["source_url"],
            "proof_url": proof["source_url"],
            "sha256": s["sha256"],
            "kind": "edgar",
            "published": stamp,
            "version_at": stamp,
            "selection": {
                "index_sha256": proof["sha256"],
                "document_title": s["title"],
                "form": "8-K",
                "sections": [s["section"]],
            },
        }
        catalog["sources"].append(entry)
        root = self.variant(catalog)
        for asset in (s, proof):
            shutil.copyfile(
                SAVED_CASE / "evaluator" / asset["file"], root / "objects" / (asset["sha256"] + ".htm")
            )
        return Corpus(root), entry

    def test_every_operation_requires_the_original_cutoff(self):
        with self.corpus.session("2023-11-02") as case:
            for operation in (case.search, case.read):
                with self.assertRaises(TypeError):
                    operation("Hulu")
                for override in ("2023-11-01", "2025-11-02", "", None):
                    with self.assertRaisesRegex(ValueError, "case cutoff"):
                        operation("Hulu", max_date=override)
            with self.assertRaises(AttributeError):
                case.cutoff = "2025-11-02"
            self.assertEqual(case.trace, [])

    def test_mixed_archive_is_identical_to_clean_even_after_a_later_run(self):
        mixed, future = self.mixed_disney()
        with mixed.session("2025-06-09") as later:
            outcome = later.read(future["url"], max_date=later.cutoff)
            self.assertTrue(any("$438.7 million" in h.text for h in outcome))
            later.search("Hulu appraisal", max_date=later.cutoff)

        def transcript(corpus):
            with corpus.session("2023-11-02") as case:
                answers = []
                for query in ("Hulu appraisal expected payment", future["title"], future["url"], "438.7"):
                    answers.append([asdict(h) for h in case.search(query, max_date=case.cutoff)])
                for url in (future["url"], outcome[0].citation_url, "https://unknown.invalid/missing"):
                    self.assertEqual(case.read(url, max_date=case.cutoff), ())
                self.assertTrue(any("$8.61 billion" in h["text"] for h in answers[0]))
                return json.dumps([answers, case.trace])

        with patch.object(mixed, "_blocks", wraps=mixed._blocks) as touched:
            observed = transcript(mixed)
        self.assertNotIn(future["id"], [c.args[0]["id"] for c in touched.call_args_list])
        self.assertEqual(observed, transcript(self.corpus))
        for secret in (
            future["id"],
            future["edition"],
            future["title"],
            future["sha256"],
            future["url"],
            "$438.7 million",
            "dis-20250609",
            "scoring.json",
        ):
            self.assertNotIn(secret, observed)

    def test_release_time_not_event_date_and_same_day_amendment(self):
        first, later = (self.sources[name] for name in ("tradeweb-8k", "tradeweb-8ka"))
        for cutoff, original_ok, amendment_ok in (
            ("2023-10-25", False, False),
            ("2023-10-26T11:01:24Z", False, False),
            ("2023-10-26T11:01:25Z", True, False),
            ("2023-10-26T13:16:38Z", True, False),
            ("2023-10-26T15:16:39+02:00", True, True),
        ):
            with self.subTest(cutoff=cutoff), self.corpus.session(cutoff) as case:
                original = case.read(first["url"], max_date=cutoff)
                corrected = case.read(later["url"], max_date=cutoff)
                self.assertEqual(bool(original), original_ok)
                self.assertEqual(bool(corrected), amendment_ok)
                if original:
                    self.assertIn("October 25, 2023", original[0].text)
                result = case.search("Tradeweb adjusted expenses", max_date=cutoff)
                if amendment_ok:
                    self.assertTrue(any("$695 million" in h.text for h in result))
                    self.assertFalse(any(h.source_id == first["id"] for h in result))
                else:
                    self.assertNotIn(later["edition"], str(result) + str(case.trace))

    def test_real_gdp_vintage_and_cover_end(self):
        early = self.sources["bea-q3-advance"]
        late = self.sources["bea-q3-second"]
        for cutoff, expected in (
            ("2023-10-25", None),
            ("2023-11-29T13:29:59Z", early),
            ("2023-11-29T13:30:00Z", late),
            ("2023-12-21T13:30:00Z", None),
        ):
            with self.subTest(cutoff=cutoff), self.corpus.session(cutoff) as case:
                hits = case.search(
                    "real gross domestic product third quarter annual rate", max_date=cutoff, limit=20
                )
                vintages = {h.source_id for h in hits if h.source_id.startswith("bea-")}
                self.assertEqual(vintages, {expected["id"]} if expected else set())
                if expected == early:
                    self.assertTrue(any("rate of 4.9 percent" in h.text for h in hits))
                    self.assertEqual(case.read(late["url"], max_date=cutoff), ())
                if expected == late:
                    self.assertTrue(any("rate of 5.2 percent" in h.text for h in hits))
                    self.assertTrue(case.read(early["url"], max_date=cutoff))

    def test_concurrent_workers_share_a_case_without_crossing_vintages(self):
        query = "real gross domestic product third quarter annual rate"
        corpus = Corpus()
        with corpus.session("2023-11-02") as early, corpus.session("2023-11-29") as late:
            with ThreadPoolExecutor(max_workers=4) as pool:
                cases = [early, late, early, late] * 2
                futures = [pool.submit(case.search, query, max_date=case.cutoff) for case in cases]
                results = [f.result() for f in futures]
            self.assertEqual(results[::2], [results[0]] * 4)
            self.assertEqual(results[1::2], [results[1]] * 4)
            for case, edition, hits in [
                (early, "bea-q3-advance", results[0]),
                (late, "bea-q3-second", results[1]),
            ]:
                self.assertEqual({h.source_id for h in hits if h.source_id.startswith("bea")}, {edition})
                self.assertEqual([e["max_date"] for e in case.trace], [case.cutoff] * 4)
        with self.assertRaisesRegex(ValueError, "closed"):
            early.search(query, max_date=early.cutoff)

    def test_unknown_publication_or_version_cannot_reach_a_model(self):
        for field, value in (
            ("published", None),
            ("version_at", ""),
            ("version_at", "2023-11-05T01:30:00"),
            ("published", "2023-11-01T16:30:15+00:60"),
            ("published", "2023-11-01T16:30:15-00:00"),
            ("timezone", "Missing/Zone"),
        ):
            s = copy.deepcopy(self.sources["toyota-fy2023"])
            s[field] = value
            corpus = Corpus(self.variant({**self.catalog, "sources": [s]}))
            with self.subTest(field=field, value=value), corpus.session("2025-11-05") as case:
                self.assertFalse(case.read(s["url"], max_date=case.cutoff))
                self.assertFalse(case.search("Toyota FY2023", max_date=case.cutoff))
                self.assertNotIn(s["title"], str(case.trace))

    def test_date_only_uses_publisher_day_end_without_inventing_a_release_time(self):
        url = self.sources["toyota-fy2023"]["url"]
        for when, admitted in (
            ("2023-05-10T03:00:00Z", False),
            ("2023-05-10T15:00:00Z", True),
            ("2023-05-09", False),
            ("2023-05-10", True),
        ):
            with self.subTest(when=when), self.corpus.session(when) as case:
                self.assertEqual(bool(case.read(url, max_date=when)), admitted)

    def test_mutable_page_and_misdated_embargo_or_revision_fail_closed(self):
        for id_, changes in (
            ("bea-q3-advance", {"url": "https://www.bea.gov/latest.pdf"}),
            ("toyota-fy2023", {"version_at": "2022-05-10"}),
            (
                "bea-q3-advance",
                {"published": "2023-10-26T07:00:00-04:00", "version_at": "2023-10-26T07:00:00-04:00"},
            ),
            ("wikinews-lehman", {"url": "https://en.wikinews.org/wiki/Lehman_Brothers_files_for_bankruptcy"}),
            ("wikinews-lehman", {"version_at": "2008-09-15T00:00:00Z"}),
            (
                "tradeweb-8ka",
                {"published": "2023-10-25T07:01:25-04:00", "version_at": "2023-10-25T07:01:25-04:00"},
            ),
        ):
            s = {**self.sources[id_], **changes}
            corpus = Corpus(self.variant({**self.catalog, "sources": [s]}))
            with self.subTest(changes=changes), corpus.session("2023-11-02") as case:
                self.assertFalse(case.read(s["url"], max_date=case.cutoff))
                self.assertFalse(case.search(s["title"], max_date=case.cutoff))
                with self.assertRaisesRegex(ValueError, "Failed to verify"):
                    corpus.verify()

    def test_misdated_amendment_cannot_hide_the_verified_original(self):
        first, later = (copy.deepcopy(self.sources[k]) for k in ("tradeweb-8k", "tradeweb-8ka"))
        later["published"] = later["version_at"] = "2023-10-26T12:00:00Z"
        corpus = Corpus(self.variant({**self.catalog, "sources": [first, later]}))
        with corpus.session("2023-10-26T12:30:00Z") as case:
            hits = case.search("Tradeweb Refinitiv data schedules", max_date=case.cutoff)
            self.assertEqual({h.source_id for h in hits}, {first["id"]})
            self.assertFalse(case.read(later["url"], max_date=case.cutoff))

    def test_each_redirect_hop_target_and_interval_is_checked(self):
        early = self.sources["disney-hulu"]["url"]
        future = self.sources["bea-q3-second"]["url"]
        start, middle = "https://example.org/archive", "https://example.org/edition"
        since = "2023-11-01T00:00:00Z"

        def hop(from_, to, start_at=since, **kw):
            return {"from": from_, "to": to, "from_at": start_at, **kw}

        for middle_rules, ok in (
            ([hop(middle, early)], True),
            ([hop(middle, future)], False),
            ([hop(middle, early, "2023-12-01T00:00:00Z")], False),
            ([hop(middle, early, until="2023-11-02T00:00:00Z")], False),
            ([hop(middle, start)], False),
            ([hop(middle, early), hop(middle, future)], False),
        ):
            catalog = {**self.catalog, "redirects": [hop(start, middle), *middle_rules]}
            with Corpus(self.variant(catalog)).session("2023-11-02") as case:
                found = case.read(start, max_date=case.cutoff)
                self.assertEqual(bool(found), ok)
                if ok:
                    self.assertEqual(found, case.read(early, max_date=case.cutoff))
                for address in (start, middle, future):
                    self.assertNotIn(address, str(case.trace))

    def test_real_redirect_is_only_available_from_its_recorded_observation(self):
        (rule,) = self.catalog["redirects"]
        for cutoff, ok in (("2008-09-16", False), ("2026-11-19", True)):
            with self.corpus.session(cutoff) as case:
                self.assertEqual(bool(case.read(rule["from"], max_date=cutoff)), ok)
                self.assertTrue(case.read(rule["to"], max_date=cutoff))

    def test_warm_source_and_proof_cannot_mask_disk_tampering_in_a_new_case(self):
        s = self.sources["disney-hulu"]
        for digest in (s["sha256"], s["selection"]["index_sha256"]):
            root = self.variant({**self.catalog, "sources": [s]})
            corpus = Corpus(root)
            with corpus.session("2023-11-02") as warm:
                self.assertTrue(warm.read(s["url"], max_date=warm.cutoff))
            (root / "objects" / (digest + ".htm")).write_bytes(b"changed source")
            with corpus.session("2023-11-02") as case:
                self.assertEqual(case.read(s["url"], max_date=case.cutoff), ())
                self.assertEqual(case.search("Disney", max_date=case.cutoff), ())
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                corpus.verify()

    def test_one_citation_resolves_exactly_one_passage_and_preserves_table_layout(self):
        with self.corpus.session("2023-05-10") as case:
            hits = case.read(self.sources["toyota-fy2023"]["url"], max_date=case.cutoff)
            table = next(h for h in hits if h.locator == "PDF p2 §2")
            self.assertEqual(case.read(table.citation_url, max_date=case.cutoff), (table,))
            self.assertIn("#page=2&search=", table.citation_url)
            self.assertIn("Million yen", table.text)
            self.assertIn("rounded to the nearest million yen", table.text)
            self.assertIn("(Note)", table.text)
            self.assertRegex(table.text, r"FY2023\s+37,154,298\s+18\.4\s+2,725,025\s+-9\.0")
            self.assertIn(
                "(forecast)", case.read(table.url, locator="PDF p2 §5", max_date=case.cutoff)[0].text
            )
            for h in hits:
                phrase = unquote(urlsplit(h.citation_url).fragment.split("search=", 1)[1]).strip('"')
                self.assertIn(phrase, compact(h.text))
            self.assertFalse(case.read(table.citation_url, max_date=case.cutoff, locator="PDF p3 §1"))

    def test_unregistered_links_invalid_queries_and_missing_fragments(self):
        with self.corpus.session("2023-11-02") as case:
            for ref in (
                "file:///etc/hosts",
                "https://unregistered.example",
                self.sources["disney-hulu"]["url"] + "#invented",
            ):
                self.assertEqual(case.read(ref, max_date=case.cutoff), ())
            self.assertFalse(case.search('"" * -- OR', max_date=case.cutoff))
            with self.assertRaisesRegex(ValueError, "limit"):
                case.search("Disney", limit=-1, max_date=case.cutoff)

    def test_cli_returns_identical_denial_for_a_known_future_and_unknown_url(self):
        observed = []
        for url in (self.sources["bea-q3-second"]["url"], "https://unknown.invalid/"):
            output = StringIO()
            with redirect_stdout(output):
                gateway_cli(["read", "--as-of", "2023-11-02", url])
            observed.append(json.loads(output.getvalue()))
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(
            observed[0],
            {"results": [], "trace": [{"operation": "read", "max_date": "2023-11-02", "results": []}]},
        )

    def test_failed_publisher_import_leaves_no_partial_archive_and_preserves_a_destination(self):
        with TemporaryDirectory() as temp:
            dest = Path(temp) / "archive"
            with (
                patch("scripts.retrieval_archive.fetch", return_value=b"changed release"),
                self.assertRaisesRegex(ValueError, "SHA-256"),
            ):
                restore(dest)
            self.assertEqual(list(Path(temp).iterdir()), [])
            dest.mkdir()
            (dest / "keep.txt").write_bytes(b"existing")
            with patch("scripts.retrieval_archive.fetch") as fetch:
                with self.assertRaisesRegex(ValueError, "new directory"):
                    restore(dest)
                fetch.assert_not_called()
            self.assertEqual((dest / "keep.txt").read_bytes(), b"existing")

    def test_committed_evaluation_and_all_gold_locators_are_reproducible(self):
        expected = json.loads((ARCHIVE.parent / "evaluations/retrieval/results.json").read_bytes())
        self.assertEqual(evaluate(), expected)
        self.assertEqual(expected["total"]["citation_checks"], 60)
        self.assertEqual(expected["cases"][0]["gold_ranks"].count(None), 0)
