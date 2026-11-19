"""Real frozen sources, development packet isolation, and registration integrity."""

import copy
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from evaluations.paired import corpus
from evaluations.paired.exclusion import audit_official


def snapshot(path):
    return {p.relative_to(path).as_posix(): p.read_bytes() for p in path.rglob("*") if p.is_file()}


class PairedFreezeTests(unittest.TestCase):
    def test_entire_registered_corpus_and_primary_source_citations_offline(self):
        with patch("socket.socket.connect", side_effect=AssertionError("unexpected network")):
            counts = corpus.verify()
        self.assertEqual(
            (12, 24, 8, 16),
            tuple(counts[k] for k in ("development", "blind", "development_forward", "blind_forward")),
        )
        self.assertEqual(108, counts["factual_criteria"])
        self.assertEqual(24, counts["outcome_pdfs"])

    def test_dev_only_install_exports_exact_allowlists_reproducibly(self):
        # A research-side export must work even when the grader's archives do not exist.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for filename in ("freeze.json", "development.json", "development-sources.zip"):
                shutil.copyfile(corpus.ROOT / filename, root / filename)
            cases = corpus.read_json(root / "development.json")
            with zipfile.ZipFile(root / "development-sources.zip") as inputs:
                for case in cases:
                    with self.subTest(case=case["case_id"]):
                        out = root / case["case_id"]
                        corpus.export_development(case["case_id"], out, root)
                        expected = {
                            "question.json": corpus.json_bytes(
                                {k: case[k] for k in ("case_id", "question", "cutoff", "forecast_rule")}
                            ),
                            "sources.json": corpus.json_bytes(case["sources"]),
                        }
                        for src in case["sources"]:
                            name = corpus.pdf_name(src)
                            expected[name] = inputs.read(name)
                        self.assertEqual(snapshot(out), expected)
                        duplicate = root / "repeat" / case["case_id"]
                        corpus.export_development(case["case_id"], duplicate, root)
                        self.assertEqual(snapshot(duplicate), expected)
            with self.assertRaisesRegex(ValueError, "fresh"):
                corpus.export_development(cases[0]["case_id"], root / cases[0]["case_id"], root)
            with zipfile.ZipFile(corpus.ROOT / "evaluator/blind-inputs.zip") as blind:
                for case in json.loads(blind.read("inputs.json")):
                    with self.assertRaisesRegex(ValueError, "development"):
                        corpus.export_development(case["case_id"], root / "forbidden", root)
            self.assertFalse((root / "forbidden").exists())

    def test_keys_outcomes_and_publisher_listings_stay_out_of_agent_packets(self):
        cases = corpus.read_json(corpus.ROOT / "development.json")
        with zipfile.ZipFile(corpus.ROOT / "evaluator/development-grading.zip") as grading:
            observations = json.loads(grading.read("outcomes.json"))
            rubrics = json.loads(grading.read("asof.json"))
        later = [s for outcome in observations.values() for s in outcome["sources"]]
        for key in rubrics["cases"].values():
            self.assertNotIn("actual", key)
            self.assertNotIn("label", key)
            self.assertNotIn("forecast", key)
            self.assertNotIn("later_sources", key)
        private_markers = [s[field].encode() for s in later for field in ("sha256", "url")]
        private_markers.extend(p["sha256"].encode() for p in rubrics["publication_proofs"].values())
        with tempfile.TemporaryDirectory() as tmp:
            for case in cases:
                directory = Path(tmp) / case["case_id"]
                corpus.export_development(case["case_id"], directory)
                files = snapshot(directory)
                self.assertEqual(
                    {s["id"] for s in case["sources"]}, {s["id"] for s in json.loads(files["sources.json"])}
                )
                content = b"\n".join(files.values())
                for marker in private_markers:
                    self.assertFalse(marker in content, (case["case_id"], marker))
                if case["case_id"] in observations:
                    for source in observations[case["case_id"]]["sources"]:
                        self.assertFalse(
                            source["title"].encode() in content, (case["case_id"], source["title"])
                        )

    def test_mutated_source_fails_even_if_zip_container_is_repacked(self):
        case = corpus.read_json(corpus.ROOT / "development.json")[0]
        source = case["sources"][0]
        with zipfile.ZipFile(corpus.ROOT / "development-sources.zip") as archive:
            name = corpus.pdf_name(source)
            raw = archive.read(name)
        damaged = io.BytesIO()
        with zipfile.ZipFile(damaged, "w") as archive:
            archive.writestr(name, raw + b"\nchanged")
        with (
            zipfile.ZipFile(damaged) as archive,
            self.assertRaisesRegex(ValueError, "Invalid primary source"),
        ):
            corpus.validate_source(source, archive, {}, None, case["cutoff"])

    def test_date_checks_reject_future_source_and_late_pdf_edition(self):
        case = corpus.read_json(corpus.ROOT / "development.json")[0]
        source = case["sources"][0]
        with zipfile.ZipFile(corpus.ROOT / "development-sources.zip") as archive:
            with self.assertRaisesRegex(ValueError, "Future edition"):
                corpus.validate_source(source, archive, {}, None, "2000-01-01T00:00:00Z")
            changed = {**source, "published": "2000-01-01"}
            with self.assertRaisesRegex(ValueError, "postdates"):
                corpus.validate_source(changed, archive, {}, None)

    def test_unresolvable_citation_and_wrong_partition_rejected(self):
        pages = {"filing": lambda page: corpus.normalized("Operating profit 100")}
        ref = {"source": "filing", "page": 1, "anchors": ["Operating profit", "100"]}
        self.assertEqual(1, corpus.check_refs([ref], {"filing"}, pages, "case"))
        with self.assertRaisesRegex(ValueError, "Unresolved"):
            corpus.check_refs([{**ref, "anchors": ["net sales"]}], {"filing"}, pages, "case")
        with self.assertRaisesRegex(ValueError, "partition"):
            corpus.check_refs([ref], {"other"}, pages, "case")

    def test_forecast_bands_are_inclusive_and_use_decimal_arithmetic(self):
        for actual, want in (("97.999", "lower"), ("98", "within"), ("102", "within"), ("102.001", "higher")):
            self.assertEqual(want, corpus.direction("100", actual, "0.02", "relative"))
        self.assertEqual("within", corpus.direction("2.50", "2.55", "0.05", "absolute"))
        self.assertEqual("higher", corpus.direction("-1.5", "-1.4", "0", "absolute"))
        self.assertEqual(corpus.numeric_anchor("1 ¾"), corpus.numeric_anchor("1.75"))
        self.assertEqual(corpus.numeric_anchor("decreased 1.5"), corpus.numeric_anchor("-1.5"))

    def test_protocol_budget_and_official_hash(self):
        protocol = corpus.read_json(corpus.ROOT / "protocol.json")
        changed = copy.deepcopy(protocol)
        changed["budget_usd"]["per_case_arm"] = "0.21"
        with self.assertRaisesRegex(ValueError, "ceiling"):
            corpus.check_protocol(changed)
        with self.assertRaisesRegex(ValueError, "exact pinned"):
            audit_official(b"another benchmark", protocol, [], {})

    def test_tampered_dev_registry_cannot_emit_a_partial_packet(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shutil.copyfile(corpus.ROOT / "freeze.json", root / "freeze.json")
            (root / "development.json").write_text("[]")
            with self.assertRaisesRegex(ValueError, "Changed frozen artifact"):
                corpus.export_development("example", root / "output", root)
            self.assertFalse((root / "output").exists())
