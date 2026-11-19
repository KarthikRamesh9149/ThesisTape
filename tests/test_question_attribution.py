import tempfile
import unittest
import zipfile
from pathlib import Path

from evaluations.open_research import fomc
from scripts.disney_hulu_case import CaseError
from scripts.question_attribution import publish, supplied, words
from scripts.run_checkpoint import load


class QuestionAttributionTests(unittest.TestCase):
    def test_original_wording_including_sign_and_units(self):
        question = words(
            "As of 2 November 2023, with a ±2% tolerance, assess the “preliminary offer” to buy Hulu."
        )
        for sentence in (
            "The question specifies a ±2% tolerance.",
            "The ±2% tolerance is an unverified question premise.",
            "The question states “preliminary offer”.",
            "The question asks how policy affected credit.",
            "The cutoff is 2 November 2023.",
        ):
            with self.subTest(sentence=sentence):
                self.assertTrue(supplied(sentence, question))
        for sentence in (
            "The question specifies a ±3% tolerance.",
            "The question specifies a 2% tolerance.",
            "The question states “final offer”.",
            "The question assumes the offer was already accepted.",
            "The range of four and three-quarters to five percent is an unverified question premise.",
            "The guidance change supplied in the question—from “ongoing increases” to “some additional policy firming”—was material.",
            "According to the question, 2 billion was already owed.",
            "The question assumes 2.",
        ):
            with self.subTest(sentence=sentence):
                self.assertFalse(supplied(sentence, question))

    def test_actual_desk_and_submission_keep_identical_reproducible_evidence(self):
        arms = fomc.verify()["profiles"]
        self.assertEqual(arms["single_astra"]["affected_paragraphs"], [1, 2])
        self.assertEqual(arms["single_astra"]["publication_state"], "qualified")
        self.assertEqual(
            arms["mixed"]["original_sha256"], arms["mixed"]["published_sha256"]
        )
        single = (fomc.FOLDER / "single_astra.md").read_text()
        self.assertNotIn("supplied in the question", single)
        self.assertNotIn("four and three-quarters", single)
        self.assertIn("25 basis points", single)
        self.assertIn("4-3/4 to 5 percent", (fomc.FOLDER / "mixed.md").read_text())

    def test_unverified_input_or_no_surviving_paragraph_cannot_be_published(self):
        with (
            tempfile.TemporaryDirectory() as d,
            zipfile.ZipFile(fomc.FOLDER / "runs.zip") as z,
        ):
            path = Path(z.extract("single_astra/result.json", d))
            original = load(path)
            memo = z.read("single_astra/memo.md")
            with self.assertRaises(CaseError):
                publish(original, memo + b".")
            remaining = {**original, "paragraphs": [original["paragraphs"][1]]}
            result, output = publish(remaining, memo)
            self.assertIsNone(output)
            self.assertIsNone(result["memo_sha256"])
            self.assertEqual(result["release"], "withheld")
            self.assertEqual(
                result["attribution_review"]["source_memo_sha256"],
                original["memo_sha256"],
            )
