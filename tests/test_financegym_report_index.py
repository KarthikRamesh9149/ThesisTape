import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import report_index
from scripts.sol_baseline import fingerprint, json_bytes


class ReportIndexTests(unittest.TestCase):
    def setUp(self):
        self.memo = (
            "# Research\n\nA quoted </script><b>source</b> with a Unicode en dash –.\n"
        )
        self.report = {
            "question": "What was public?",
            "cutoff": "2023-11-02",
            "report": self.memo,
            "num_docs_retrieved": 2,
        }
        self.proof = {
            "task_id": "a" * 24,
            "state": "qualified",
            "estimated_cost_usd": "0.2314",
            "report_sha256": fingerprint(self.memo.encode()),
        }

    def test_reader_preserves_submitted_text_as_inert_data(self):
        html = report_index.render([self.report], [self.proof])
        payload = re.search(
            r'<script id="data" type="application/json">(.+?)</script>', html
        )[1]
        self.assertNotIn("<", payload)
        (row,) = json.loads(payload)
        self.assertEqual(row["report"], self.memo)
        self.assertEqual(row["state"], "qualified")
        self.assertEqual(row["cost"], "0.2314")
        with self.assertRaisesRegex(ValueError, "verified final text"):
            report_index.render(
                [{**self.report, "report": self.memo.strip()}], [self.proof]
            )
        with self.assertRaises(ValueError):
            report_index.render([self.report], [])

    def test_changed_sealed_input_never_replaces_the_readers_existing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            answers = folder / "answers.jsonl"
            audit_file = folder / "audit.json"
            output = folder / "reader.html"
            answers.write_bytes(json_bytes(self.report))
            audit_file.write_bytes(json_bytes({"tasks_audited": [self.proof]}))
            (folder / "manifest.json").write_bytes(
                json_bytes(
                    {
                        "files": {
                            p.name: fingerprint(p.read_bytes())
                            for p in (answers, audit_file)
                        }
                    }
                )
            )
            output.write_bytes(b"previous revision")
            answers.write_bytes(answers.read_bytes() + b" ")
            with (
                patch.object(report_index.submission, "verify_export") as verifier,
                self.assertRaisesRegex(ValueError, "sealed submission"),
            ):
                report_index.write(folder / "questions", folder, output)
            verifier.assert_not_called()
            self.assertEqual(output.read_bytes(), b"previous revision")
