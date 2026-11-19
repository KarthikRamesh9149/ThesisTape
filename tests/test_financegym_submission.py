import json
import tempfile
import unittest
from decimal import Decimal
from email import policy
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import submission
from scripts.sol_baseline import fingerprint


class MaintainerPackage(unittest.TestCase):
    def test_budget_review_cannot_replace_a_success_or_hide_the_cost_of_a_rejected_review(
        self,
    ):
        cfg = {
            "profiles_by_task": {"a": "single_astra"},
            "unanswered_recovery": {"profile": "evidence_team_v4", "cap_usd": "0.45"},
        }
        original = {
            "profile": "single_astra",
            "state": "failed",
            "report_sha256": None,
            "estimated_cost_usd": "0.24",
            "outstanding_usd": "0",
        }
        review = {
            "profile": "independent_primary_draft_review",
            "state": "qualified",
            "report_sha256": "digest",
            "estimated_cost_usd": "0.02",
            "outstanding_usd": "0",
            "registration_sha256": "approved-budget",
            "transport_requests": 0,
            "provider_generations": 1,
        }
        proof = {
            "task_id": "a",
            "primary_profile": "single_astra",
            "profile": review["profile"],
            "state": "qualified",
            "report_sha256": "digest",
            "attempts": [original],
            "primary_draft_review": review,
            "estimated_cost_usd": "0.26",
            "outstanding_usd": "0",
        }

        def valid(row):
            return submission.verified_selection(
                cfg, row, "secondary", budget_registration="approved-budget"
            )

        self.assertTrue(valid(proof))
        self.assertFalse(
            valid({**proof, "attempts": [{**original, "state": "complete"}]})
        )
        self.assertFalse(valid({**proof, "estimated_cost_usd": "0.24"}))
        self.assertFalse(
            valid(
                {
                    **proof,
                    "primary_draft_review": {**review, "estimated_cost_usd": "0.08"},
                    "estimated_cost_usd": "0.32",
                }
            )
        )
        second = {
            **original,
            "profile": "evidence_team_v4",
            "state": "qualified",
            "report_sha256": "digest-2",
            "estimated_cost_usd": "0.31",
        }
        fallback = {
            **proof,
            "attempts": [original, second],
            "profile": second["profile"],
            "report_sha256": "digest-2",
            "primary_draft_review": {
                **review,
                "state": "withheld",
                "report_sha256": None,
            },
            "estimated_cost_usd": "0.57",
        }
        self.assertTrue(valid(fallback))
        self.assertFalse(valid({**fallback, "estimated_cost_usd": "0.55"}))

    def test_cost_and_duration_percentiles_count_each_original_question_once(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            rows = [{"elapsed_s": i * 10} for i in range(20, 0, -1)]
            audit = {
                "tasks_audited": [
                    {"estimated_cost_usd": str(Decimal(i) / 100)} for i in range(1, 21)
                ]
            }
            answers = folder / "answers.jsonl"
            answers.write_text("\n".join(map(json.dumps, rows)))
            self.assertEqual(
                submission.per_question_usage(folder, audit),
                {
                    "mean_cost_usd": Decimal("0.105"),
                    "p95_cost_usd": Decimal("0.19"),
                    "mean_seconds": 105,
                    "p95_seconds": 190,
                },
            )
            rows[0]["elapsed_s"] = -1
            answers.write_text("\n".join(map(json.dumps, rows)))
            with self.assertRaisesRegex(ValueError, "duration"):
                submission.per_question_usage(folder, audit)

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
