import unittest

from evaluations.open_research import boeing, ecb, tsmc
from scripts.research_desk_agent import Draft, Paragraph, assemble_revision


class InvestmentMemoTests(unittest.TestCase):
    def test_recheck_assembles_approved_and_revised_claims_once(self):
        def paragraph(text):
            return Paragraph(
                section="Answer",
                text=text,
                citations=[{"passage": "P1", "quote": "Original release"}],
            )

        keep = paragraph("Accepted earlier conclusion.")
        bad = paragraph("An inaccurate prior conclusion.")
        replacement = paragraph("A supported new conclusion.")
        before = Draft(paragraphs=[keep, bad])
        after = assemble_revision(
            before, {"A2": "wrong"}, Draft(paragraphs=[keep, replacement, replacement])
        )
        self.assertEqual(after.paragraphs, [keep, replacement])
        self.assertEqual(before.paragraphs, [keep, bad])

    def test_paid_boeing_report_and_whole_memo_review_replay_offline(self):
        observed = boeing.compare(live=False)
        self.assertEqual(observed["checked_searches"], 3)
        self.assertEqual(observed["matching_historical_generations"], 8)
        self.assertEqual(observed["reviews"][-1]["approved"], 4)

    def test_ecb_public_desk_export_with_dated_sources_replays_offline(self):
        proof = ecb.verify()
        self.assertEqual(proof["calls"], 9)
        self.assertEqual(proof["state"], "complete")
        self.assertEqual(proof["page_errors"], [])

    def test_tsmc_release_keeps_both_reviewed_funding_amounts_without_redispatch(self):
        checked = tsmc.verify()
        self.assertEqual(checked["matching_generation_receipts"], 8)
        self.assertEqual(checked["additional_generation_cost_usd"], "0")
        self.assertEqual(checked["state"], "qualified")
        body = (tsmc.FOLDER / "revised.md").read_text().split("## Sources")[0]
        self.assertIn("$6.6 billion", body)
        self.assertIn("$5 billion", body)
        self.assertIn("$65 billion", body)

    def test_archived_terms_and_milestones_survive_the_browser_export(self):
        proof = tsmc.verify_focused()
        self.assertEqual(
            proof["retrieval_replayed"],
            {
                "searches": 5,
                "transport_requests": 60,
                "eligible_sources": 11,
                "eligible_passage_urls": 32,
            },
        )
        answer = (
            (tsmc.FOLDER / "original-passages/memo.md")
            .read_text()
            .split("## Sources")[0]
        )
        for term in (
            "$6.6 billion",
            "$5 billion",
            "$65 billion",
            "due diligence",
            "milestones",
            "25%",
        ):
            self.assertIn(term, answer)
