import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import evidence, recovery, reviewed_release, submission
from evaluations.open_research import fomc
from scripts.sol_baseline import json_bytes


class ConditionalRecoveryTests(unittest.TestCase):
    def test_first_draft_requires_an_existing_independent_approval_and_failed_run(self):
        for state, profile, calls in [
            ("qualified", "evidence_team_v4", {}),
            ("failed", "single_astra", {"review.initial": {"output": "unused"}}),
            ("failed", "evidence_team_v4", {"review.initial": {}}),
        ]:
            with self.subTest(state=state, profile=profile):
                rec = {"state": state, "research_profile": profile, "calls": calls}
                self.assertIsNone(
                    reviewed_release.approved_draft({}, Path("."), rec, "unused")
                )

    def test_portable_costs_duration_and_editions_match_the_real_fed_trial(self):
        with (
            tempfile.TemporaryDirectory() as t,
            zipfile.ZipFile(fomc.FOLDER / "runs.zip") as archive,
        ):
            for name in archive.namelist():
                if name.startswith("single_astra/"):
                    archive.extract(name, t)
            root = Path(t) / "single_astra"
            record = json.loads((root / "run.json").read_bytes())
            audit = {"estimated_cost_usd": "0.201976", "elapsed_s": 64.025}
            editions, seconds = evidence.attempt_metadata(root, record, audit)
            self.assertEqual((len(editions), seconds), (9, 64.025))
            for field, changed in (
                ("estimated_cost_usd", "0.20"),
                ("outstanding_usd", "0.001"),
                ("elapsed_s", 0),
            ):
                with self.subTest(field=field), self.assertRaises(ValueError):
                    evidence.attempt_metadata(root, record, {**audit, field: changed})
            path = next((root / "searches").glob("*.json"))
            search = json.loads(path.read_bytes())
            search["body"]["sources"][0]["available"] = "2026-11-20"
            path.write_bytes(json_bytes(search))
            with self.assertRaisesRegex(ValueError, "cutoff"):
                evidence.attempt_metadata(root, record, audit)

    def test_origin_metrics_count_only_sources_cited_in_released_paragraphs(self):
        visible = {
            "paragraphs": [{"pids": ["a", "a"]}],
            "evidence": {"a": {"kind": "wikimedia_revision"}, "b": {"kind": "wayback"}},
        }
        self.assertEqual(evidence.cited_kinds(visible), {"wikimedia_revision"})

    def test_portable_archive_includes_only_explicit_evidence_and_checks_private_ids(
        self,
    ):
        with tempfile.TemporaryDirectory() as t:
            run = Path(t) / "research"
            run.mkdir()
            (run / "provider-ids.json").write_bytes(
                json_bytes({"stage": {"response_id": "private-test-identity"}})
            )
            (run / ".env.local").write_text("test sentinel")
            (run / "run.json").write_text("{}")
            self.assertEqual(
                set(evidence.portable(run)), {"run.json", "http-receipt-hashes.json"}
            )
            (run / "run.json").write_text("private-test-identity")
            with self.assertRaisesRegex(ValueError, "Private receipt identity"):
                evidence.portable(run)

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

    def test_an_offline_publication_must_retain_both_failures_and_zero_incremental_cost(
        self,
    ):
        cfg = {
            "profiles_by_task": {"x": "single_astra"},
            "unanswered_recovery": {"profile": "evidence_team_v4", "cap_usd": "0.45"},
        }
        a = {
            "state": "failed",
            "profile": "single_astra",
            "report_sha256": None,
            "estimated_cost_usd": "0.10",
            "outstanding_usd": "0",
        }
        b = {**a, "profile": "evidence_team_v4", "original_run_sha256": "source"}
        publication = {
            "method": "independently_reviewed_first_draft",
            "source_run_sha256": "source",
            "report_sha256": "report",
            "approved_paragraphs": 3,
            "published_paragraphs": 2,
            "additional_model_generations": 0,
            "additional_model_cost_usd": "0",
        }
        proof = {
            "attempts": [a, b],
            "task_id": "x",
            "primary_profile": "single_astra",
            "profile": "evidence_team_v4",
            "state": "qualified",
            "report_sha256": "report",
            "estimated_cost_usd": "0.20",
            "outstanding_usd": "0",
            "reviewed_publication": publication,
        }
        self.assertTrue(
            submission.verified_selection(cfg, proof, "recovery", "publication")
        )
        self.assertFalse(submission.verified_selection(cfg, proof, "recovery"))
        for changed in (
            {"attempts": [a, {**b, "report_sha256": "already released"}]},
            {"reviewed_publication": {**publication, "source_run_sha256": "other"}},
            {"reviewed_publication": {**publication, "published_paragraphs": 4}},
            {
                "reviewed_publication": {
                    **publication,
                    "additional_model_generations": 1,
                }
            },
            {"state": "complete"},
        ):
            with self.subTest(changed=changed):
                self.assertFalse(
                    submission.verified_selection(
                        cfg, {**proof, **changed}, "recovery", "publication"
                    )
                )
