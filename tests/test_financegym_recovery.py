import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import recovery, submission
from evaluations.open_research import fomc
from scripts.sol_baseline import json_bytes


class ConditionalRecoveryTests(unittest.TestCase):
    def test_live_qualified_reports_are_ineligible_even_when_their_coverage_differs(
        self,
    ):
        with (
            tempfile.TemporaryDirectory() as t,
            zipfile.ZipFile(fomc.FOLDER / "runs.zip") as z,
        ):
            loc = Path(t)
            run = loc / "research"
            run.mkdir()
            for profile in fomc.PROFILES:
                for name in ("run.json", "result.json", "memo.md"):
                    (run / name).write_bytes(z.read(profile + "/" + name))
                self.assertFalse(recovery.needs_research(loc))
            (run / "run.json").write_bytes(json_bytes({"state": "failed"}))
            self.assertTrue(recovery.needs_research(loc))
            (run / "run.json").write_bytes(json_bytes({"state": "running"}))
            self.assertFalse(recovery.needs_research(loc))

    def test_no_second_dispatch_when_the_full_audit_returns_an_existing_answer(self):
        manifest = {
            "profiles_by_task": {"x": "single_astra"},
            "selection": {"general": "single_astra", "statement_analysis": None},
            "case_limits_usd": {"single_astra": "0.30"},
            "archive_version": "test",
        }
        with (
            patch.object(
                recovery, "attempt", return_value=({"report": "Retain"}, {}, set())
            ),
            patch.object(recovery.runner, "research_once") as paid,
        ):
            with self.assertRaisesRegex(ValueError, "cannot be rerun"):
                recovery.recover_one(
                    {"task_id": "x", "question": "An intact original"}, manifest, {}
                )
            paid.assert_not_called()

    def test_export_rejects_erasure_of_original_cost_or_substitution_of_an_accepted_answer(
        self,
    ):
        config = {
            "profiles_by_task": {"x": "single_astra"},
            "unanswered_recovery": {"profile": "evidence_team_v4", "cap_usd": "0.45"},
        }
        original = {
            "profile": "single_astra",
            "state": "failed",
            "report_sha256": None,
            "estimated_cost_usd": "0.10",
            "outstanding_usd": "0.015",
        }
        second = {
            "profile": "evidence_team_v4",
            "state": "qualified",
            "report_sha256": "abc",
            "estimated_cost_usd": "0.20",
            "outstanding_usd": "0",
        }
        report = {
            **second,
            "task_id": "x",
            "primary_profile": "single_astra",
            "estimated_cost_usd": "0.30",
            "outstanding_usd": "0.015",
            "attempts": [original, second],
        }
        self.assertTrue(submission.verified_selection(config, report, "registered"))
        self.assertFalse(
            submission.verified_selection(
                config, {**report, "estimated_cost_usd": "0.20"}, "registered"
            )
        )
        self.assertFalse(
            submission.verified_selection(
                config, {**report, "outstanding_usd": "0"}, "registered"
            )
        )
        self.assertFalse(
            submission.verified_selection(
                config,
                {**report, "attempts": [{**original, "state": "complete"}, second]},
                "registered",
            )
        )
        self.assertFalse(submission.verified_selection(config, report, None))
