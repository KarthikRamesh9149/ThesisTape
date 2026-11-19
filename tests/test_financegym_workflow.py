import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from evaluations.financegym import workflow
from scripts.sol_baseline import json_bytes


class CompletionTests(unittest.TestCase):
    @staticmethod
    def write(folder, state):
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "run.json").write_bytes(json_bytes({"state": state}))

    def test_funded_stages_follow_original_review_recovery_and_source_eligibility(self):
        row = {"task_id": "case"}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first, second, review = [
                root / name / "case/research" for name in ("first", "second", "review")
            ]
            edit = root / "editing/case/research"
            with (
                patch.object(
                    workflow.editorial, "folders", return_value=(first, second)
                ),
                patch.object(workflow.editorial, "ROOT", root / "editing"),
                patch.object(
                    workflow.recovery, "needs_research", return_value=True
                ) as required,
                patch.object(
                    workflow.budget_publication, "eligible", return_value=True
                ),
                patch.object(
                    workflow.budget_publication, "directory", return_value=review
                ),
                patch.object(
                    workflow.editorial, "eligible", return_value=False
                ) as editing,
                patch.object(
                    workflow.titled_publication, "needed", return_value=False
                ) as title,
            ):

                def selected():
                    return workflow.next_stage(row, {}, {})

                self.assertEqual(selected(), "primary")
                self.write(first, "failed")
                self.assertEqual(selected(), "draft_review")
                self.write(review, "qualified")
                self.assertEqual(selected(), "ready")
                self.write(review, "withheld")
                self.assertEqual(selected(), "recovery")
                self.write(second, "qualified")
                self.assertEqual(selected(), "ready")
                self.write(second, "failed")
                editing.return_value = True
                self.assertEqual(selected(), "editorial")
                title.return_value = True
                self.assertEqual(selected(), "title_lookup")
                self.write(edit, "qualified")
                self.assertEqual(selected(), "ready")
                self.write(edit, "failed")
                self.assertEqual(selected(), "unanswered")
                required.return_value = False
                self.assertEqual(selected(), "ready")

    def test_partial_primary_batch_cannot_dispatch_new_research_or_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gate = Mock(directory=root / "budgets")
            with (
                patch.object(workflow.runner, "RESEARCH", root / "primary"),
                patch.object(
                    workflow, "registrations", return_value=({}, {}, {}, {}, {})
                ),
                patch.object(
                    workflow.runner, "tasks", return_value=[{"task_id": "case"}]
                ),
                patch.object(workflow.budget, "ProjectBudget", return_value=gate),
                patch.object(workflow, "advance") as paid,
                patch.object(workflow.budget_publication, "export") as export,
                self.assertRaisesRegex(ValueError, "paced primary"),
            ):
                workflow.finish(
                    root / "questions", root / "submission", root / "proofs"
                )
            paid.assert_not_called()
            export.assert_not_called()
