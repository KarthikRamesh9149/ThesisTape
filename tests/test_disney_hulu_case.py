import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts import disney_hulu_case as case


SCRIPT = Path(case.__file__).resolve()


def snapshot(directory):
    return {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in directory.rglob("*")
        if path.is_file()
    }


class DisneyHuluCaseTests(unittest.TestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.workspace = Path(workspace.name)

    def run_case(self, *arguments):
        return subprocess.run(
            [sys.executable, str(SCRIPT), *map(str, arguments)],
            capture_output=True,
            text=True,
        )

    def flat_filings(self):
        destination = self.workspace / "filings"
        destination.mkdir()
        for path in case.SAVED_CASE.glob("*/*.htm"):
            shutil.copyfile(path, destination / path.name)
        return destination

    def test_saved_case_verifies_edgar_dates_hashes_and_all_four_citations(self):
        result = self.run_case("verify")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EDGAR filing date 2023-11-01", result.stdout)
        self.assertIn("EDGAR filing date 2025-06-09", result.stdout)
        self.assertIn("Both EDGAR indexes verified. Resolved 3 rubric citations and 1 later-outcome citation.", result.stdout)
        manifest = json.loads((case.SAVED_CASE / "agent/manifest.json").read_bytes())
        self.assertEqual(manifest["cutoff"], "2023-11-02")
        self.assertEqual(
            manifest["question"],
            "As of 2 November 2023, what did Disney expect to pay for NBCUniversal’s stake in Hulu, "
            "and what could change the final amount?",
        )
        self.assertEqual(manifest["source"]["accession"], "0001744489-23-000206")

    def test_imports_of_the_same_saved_filings_match_byte_for_byte(self):
        filings = self.flat_filings()
        first, second = self.workspace / "first", self.workspace / "second"
        expected = snapshot(case.SAVED_CASE)
        for output in (first, second, first):
            result = self.run_case("import", "--filings-dir", filings, "--output-dir", output)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(snapshot(output), expected)
        self.assertEqual(snapshot(first), snapshot(second))

    def test_verifier_detects_a_changed_source_file(self):
        candidate = shutil.copytree(case.SAVED_CASE, self.workspace / "changed")
        filing = candidate / "agent/dis-20231101.htm"
        original = filing.read_bytes()
        changed = original.replace(b"$8.61 billion", b"$8.62 billion", 1)
        self.assertNotEqual(changed, original)
        filing.write_bytes(changed)
        result = self.run_case("verify", "--case-dir", candidate)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("SHA-256 mismatch for agent/dis-20231101.htm", result.stderr)

    def test_colocated_2025_filing_and_metadata_stay_out_of_agent_input(self):
        filings = self.flat_filings()
        evaluator_metadata = (case.SAVED_CASE / "evaluator/manifest.json").read_bytes()
        (filings / "manifest.json").write_bytes(evaluator_metadata)
        shutil.copyfile(case.SAVED_CASE / "evaluator/scoring.json", filings / "scoring.json")
        output = self.workspace / "mixed-import"
        result = self.run_case("import", "--filings-dir", filings, "--output-dir", output)
        self.assertEqual(result.returncode, 0, result.stderr)

        agent = snapshot(output / "agent")
        self.assertEqual(set(agent), {"manifest.json", "dis-20231101.htm"})
        self.assertEqual(agent, snapshot(case.SAVED_CASE / "agent"))
        later = json.loads(evaluator_metadata)["source"]
        later_bytes = (filings / later["file"]).read_bytes()
        self.assertEqual((output / "evaluator" / later["file"]).read_bytes(), later_bytes)
        for data in agent.values():
            for forbidden in (later_bytes, later["title"].encode(), later["source_url"].encode(), b"$438.7 million"):
                self.assertNotIn(forbidden, data)

    def test_later_filing_under_the_allowed_filename_is_rejected(self):
        filings = self.flat_filings()
        shutil.copyfile(filings / "dis-20250609.htm", filings / "dis-20231101.htm")
        output = self.workspace / "wrong-filing"
        result = self.run_case("import", "--filings-dir", filings, "--output-dir", output)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("SHA-256 mismatch for agent/dis-20231101.htm", result.stderr)
        self.assertFalse(output.exists())

    def test_verifier_rejects_an_extra_file_in_agent_input(self):
        candidate = shutil.copytree(case.SAVED_CASE, self.workspace / "extra")
        shutil.copyfile(candidate / "evaluator/dis-20250609.htm", candidate / "agent/unlisted.htm")
        result = self.run_case("verify", "--case-dir", candidate)
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("must contain exactly these entries: manifest.json, dis-20231101.htm", result.stderr)

    def test_text_from_another_item_does_not_resolve_a_rubric_citation(self):
        manifest = json.loads((case.SAVED_CASE / "agent/manifest.json").read_bytes())
        source = manifest["source"]
        data = (case.SAVED_CASE / "agent" / source["file"]).read_bytes()
        anchor = "Cover Page Interactive Data File"
        self.assertIn(anchor.encode(), data)  # This appears in Item 9.01.
        citation = {"source_id": source["id"], "section": source["section"], "anchors": [anchor]}
        with self.assertRaisesRegex(case.CaseError, "Citation anchor is missing"):
            case.verify_citation(citation, source, case.filing_item(data, source))
