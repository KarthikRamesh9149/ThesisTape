import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import editorial
from evaluations.open_research import fomc
from scripts import source_completion
from scripts.run_checkpoint import load


class SourceCompletionTests(unittest.TestCase):
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
