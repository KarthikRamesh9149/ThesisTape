import json
import unittest

from evaluations.financegym import draft_review


class DraftReviewContracts(unittest.TestCase):
    def test_only_original_accepted_text_can_be_released_and_needs_an_answer_and_support(
        self,
    ):
        draft = {
            "question": "What would change a cash flow outlook?",
            "cutoff": "2025-02-01",
            "paragraphs": [
                {"section": s, "text": t, "pids": ["P1"], "calculation_ids": []}
                for s, t in [
                    ("Answer", "Cash flow depends on collections."),
                    ("Evidence", "The issuer reported operating cash flow."),
                    ("Outlook", "Collections could slow."),
                ]
            ],
            "evidence": {
                "P1": {
                    "title": "Issuer cash flow",
                    "available": "2025-01-01",
                    "url": "https://example.org/filing",
                }
            },
            "calculations": {},
        }

        def result(accept):
            return json.dumps(
                {
                    "decisions": [
                        {
                            "paragraph": f"A{i}",
                            "accept": i in accept,
                            "reason": "Source checked.",
                        }
                        for i in range(1, 4)
                    ],
                    "missing": [],
                }
            )

        accepted, memo = draft_review.outcome(draft, result({1, 2}))
        self.assertEqual(accepted["release"], "qualified")
        self.assertEqual(
            [p["text"] for p in accepted["paragraphs"]],
            [p["text"] for p in draft["paragraphs"][:2]],
        )
        self.assertNotIn(b"Collections could slow.", memo)
        for allowed in ({1}, {2, 3}, set()):
            self.assertEqual(draft_review.outcome(draft, result(allowed)), (None, None))
        with self.assertRaisesRegex(ValueError, "every proposed paragraph"):
            draft_review.outcome(draft, json.dumps({"decisions": [], "missing": []}))
