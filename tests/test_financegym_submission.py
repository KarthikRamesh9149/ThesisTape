import tempfile
import unittest
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import submission
from scripts.sol_baseline import fingerprint


class MaintainerPackage(unittest.TestCase):
    def test_model_list_includes_the_recovery_investigator_when_editing_finishes_the_report(
        self,
    ):
        cfg = {"models": [submission.ASTRA]}
        audit = {
            "tasks_audited": [
                {
                    "profile": "source_completion",
                    "attempts": [
                        {"profile": "single_astra"},
                        {"profile": "evidence_team_v4"},
                    ],
                }
            ]
        }
        self.assertEqual(
            submission.model_names(cfg, audit),
            sorted([submission.ASTRA, submission.MODEL]),
        )

    def test_local_email_round_trip_preserves_the_complete_file_and_is_repeatable(self):
        raw = b'{"question":"Currency amounts in different units?","report":"A cited report."}\n'
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            (out / "answers.jsonl").write_bytes(raw)
            with patch.object(
                submission,
                "paper",
                return_value=(
                    {"models": ["verified-model"]},
                    {"answers_sha256": fingerprint(raw), "qualified": 0},
                ),
            ):
                for _ in range(2):
                    submission.draft(
                        None,
                        out,
                        "researcher@example.org",
                        "Research Group",
                        "https://example.org/system-paper",
                        "2026-11-20",
                    )
                data = BytesParser(policy=policy.default).parsebytes(
                    (out / "submission.eml").read_bytes()
                )
                self.assertEqual(data["To"], submission.RECIPIENT)
                self.assertEqual(data["Subject"], "FinanceGym submission: ThesisTape")
                (part,) = data.iter_attachments()
                self.assertEqual(part.get_payload(decode=True), raw)
                self.assertEqual(part.get_filename(), "answers.jsonl")
                with self.assertRaises(ValueError):
                    submission.draft(
                        None,
                        out,
                        "researcher@example.org",
                        "Research Group",
                        "file:///private/project",
                        "2026-11-20",
                    )
