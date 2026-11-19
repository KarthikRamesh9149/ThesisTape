import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import editorial, submission
from evaluations.open_research import fomc
from scripts import source_completion
from scripts.run_checkpoint import load


class SourceCompletionTests(unittest.TestCase):
    def test_editorial_audit_preserves_earlier_costs_and_rejects_retrieval_or_a_hidden_charge(
        self,
    ):
        cfg = {
            "profiles_by_task": {"x": "single_astra"},
            "unanswered_recovery": {"profile": "evidence_team_v4", "cap_usd": "0.45"},
        }
        initial = {
            "profile": "single_astra",
            "state": "failed",
            "report_sha256": None,
            "estimated_cost_usd": "0.20",
            "outstanding_usd": "0",
        }
        independent = {
            **initial,
            "profile": "evidence_team_v4",
            "estimated_cost_usd": "0.08",
        }
        edit = {
            "profile": "source_completion",
            "state": "qualified",
            "report_sha256": "written",
            "transport_requests": 0,
            "estimated_cost_usd": "0.14",
            "outstanding_usd": "0",
            "provider_generations": 3,
        }
        proof = {
            "task_id": "x",
            "attempts": [initial, independent],
            "editorial": edit,
            "primary_profile": "single_astra",
            "profile": "source_completion",
            "state": "qualified",
            "report_sha256": "written",
            "estimated_cost_usd": "0.42",
            "outstanding_usd": "0",
        }

        def valid(p, funding="funded"):
            return submission.verified_selection(
                cfg, p, "independent", "offline", funding
            )

        self.assertTrue(valid(proof))
        self.assertFalse(valid(proof, None))
        for delta in (
            {"editorial": {**edit, "transport_requests": 1}},
            {"estimated_cost_usd": "0.14"},
            {
                "editorial": {**edit, "estimated_cost_usd": "0.60"},
                "estimated_cost_usd": "0.88",
            },
            {
                "attempts": [
                    initial,
                    {**independent, "state": "qualified", "report_sha256": "existing"},
                ]
            },
        ):
            with self.subTest(delta=delta):
                self.assertFalse(valid({**proof, **delta}))

    def test_real_fed_searches_bind_both_originals_and_cutoff(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            zipfile.ZipFile(fomc.FOLDER / "runs.zip") as z,
        ):
            root = Path(temp)
            for name in z.namelist():
                if "/searches/" in name or name.endswith("/run.json"):
                    z.extract(name, root)
            folders = [root / "single_astra", root / "mixed"]
            rec = load(folders[0] / "run.json")
            args = (rec["question"], rec["cutoff"], rec["archive_version"], folders)
            inputs, passages, searches = source_completion.materials(*args)
            self.assertEqual(len(inputs), 2)
            self.assertTrue(passages and searches)
            self.assertTrue(
                all(p["available"] <= rec["cutoff"] for p in passages.values())
            )
            self.assertTrue(all(s["max_date"] == rec["cutoff"] for s in searches))
            path = next((folders[0] / "searches").glob("*.json"))
            original = path.read_bytes()
            changed = json.loads(original)
            changed["body"]["request"]["max_date"] = "2026-01-01"
            path.write_text(json.dumps(changed))
            with self.assertRaisesRegex(ValueError, "cutoff"):
                source_completion.materials(*args)
            path.write_bytes(original)
            with self.assertRaisesRegex(ValueError, "different question"):
                source_completion.materials("Substituted question", *args[1:])

    def test_existing_publication_prevents_opening_a_provider(self):
        with (
            patch.object(editorial, "eligible", return_value=False),
            patch.object(editorial, "OpenAIResponses") as provider,
        ):
            with self.assertRaisesRegex(ValueError, "additional funding"):
                editorial.run_one({}, {}, {}, {})
            provider.assert_not_called()
