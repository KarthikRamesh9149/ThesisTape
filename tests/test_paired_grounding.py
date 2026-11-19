import json
import tempfile
import unittest
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

from test_paired_runner import FixtureTransport

from evaluations.paired import grounding_replay, study
from evaluations.paired.corpus import export_development
from scripts.disney_hulu_case import CaseError
from scripts.paired_grounding import check_numbers
from scripts.paired_packet import PacketCase, PacketCorpus
from scripts.paired_v2 import (
    PATCH_OUTPUT_TOKENS,
    ParagraphIssue,
    ResearchMemo,
    build_candidate,
    repair_input_capacity,
    write,
)
from scripts.retrieval_gateway import Evidence
from scripts.sol_provider import ASTRA, BudgetExhausted, reserve


class GroundingTests(unittest.TestCase):
    def setUp(self):
        self.case = PacketCase(
            "example",
            "Forecast profit for the year ending March 2021.",
            "2020-05-08T12:00:00Z",
            {
                "metric": "net profit",
                "mode": "relative",
                "unit": "JPY million",
                "tolerance": "0.02",
            },
        )
        self.h = Evidence(
            "a",
            "Original filing",
            "2020",
            "2020-05-07",
            "2020-05-07",
            "2020-05-07T16:00:00Z",
            "https://example.org/a.pdf",
            "a",
            "PDF p3 §4",
            "The guide was 200.0 billion yen. The year ending March 31, 2021 is the forecast period. Assets as of March 31, 2020 were 5 billion yen. Orders rose 3%.",
            "https://example.org/a.pdf#page=3",
        )
        self.other = replace(
            self.h,
            sha256="b",
            locator="PDF p4 §1",
            text="Orders were flat and cash totaled 77 billion yen.",
        )

    def check(self, value, *, hits=None, case=None, **kwargs):
        check_numbers(
            value, case or self.case, [self.h] if hits is None else hits, **kwargs
        )

    def test_valid_case_terms_and_originals(self):
        for text in (
            "As of May 8, 2020, the guide was JPY 200,000 million.",
            "At the cutoff of 2020-05-08T08:00:00-04:00, the guide held.",
            "As of 8 May 2020 at 12:00 UTC, the outlook is within ±2% of guidance.",
            "The threshold is 2% inclusive. On PDF p. 3 §4 the guide is 200 billion yen.",
            "More than 2% above the guide would count as higher.",
            "For the year ending March 2021, by March 31, 2021, the forecast is within the band.",
            "Assets as of March 31, 2020 were JPY 5 billion.",
            "The May 7, 2020 filing reports that orders rose 3%.",
        ):
            with self.subTest(text=text):
                self.check(text)
        self.check("PDF pp. 3–4", hits=[self.h, replace(self.other, sha256="a")])
        for unit, tolerance, text in (
            ("percent", "0.25", "Within 25 basis points."),
            ("ratio", "0.1", "A 0.1 ratio point band."),
        ):
            self.check(
                text,
                case=replace(
                    self.case,
                    forecast_rule={
                        "mode": "absolute",
                        "unit": unit,
                        "tolerance": tolerance,
                    },
                ),
            )

    def test_altered_values_do_not_borrow_numbers_from_another_context(self):
        for text in (
            "As of May 7, 2020 the guide held.",
            "As of May 8, 2020 at 12:01 UTC, guide.",
            "As of May 8, 2020 at 12:00:77 UTC, guide.",
            "As of May 8, 2020 at 12:00, guide.",
            "The May 8, 2020 filing set the guide.",
            "The 3% band is inclusive.",
            "Within ±2 percentage points of the guide.",
            "The 2% band is exclusive.",
            "Revenue grew 2%.",
            "Profits grew ±2% while the band remained unchanged.",
            "The guide on PDF p. 4 was unchanged.",
            "PDF p3 §3",
            "PDF p3.4",
            "The company earned 2 billion yen.",
            "Profit was USD 200 billion.",
            "Guidance is JPY -200 billion.",
            "Guidance is -200 billion yen.",
            "Revised guidance is 200 million yen.",
            "The profit was JPY 77 billion.",
            "The threshold is 2%. The profit margin was 2%.",
        ):
            with self.subTest(text=text), self.assertRaises(CaseError):
                self.check(text, opened=[self.h, self.other])
        with self.assertRaises(CaseError):
            self.check("Original PDF pp. 3–4.", hits=[self.h, self.other])

    def test_patch_changes_only_failing_paragraph_and_rechecks_whole_candidate(self):
        p = {
            "text": "The guide was 201 billion yen.",
            "citations": [
                {"passage_id": "P1", "quote": "The guide was 200.0 billion yen."}
            ],
            "calculation_ids": [],
        }
        txt = "The guide was 200 billion yen."
        good = {**p, "text": txt}
        raw = {
            "facts": [p],
            "analysis": good,
            "alternative_and_signal": good,
            "direction": "within",
        }
        opened = {"P1": self.h}

        def build(s):
            return build_candidate(self.case, opened, "write", {}, None, s)

        def dispatch(stage, prompt, limit):
            if stage == "lead.memo":
                return json.dumps(raw)
            self.assertEqual((stage, limit), ("lead.memo_repair", PATCH_OUTPUT_TOKENS))
            data = json.loads(prompt["input"])
            self.assertEqual(
                data["cited_originals"],
                [{"locator": "PDF p3 §4", "quote": "The guide was 200.0 billion yen."}],
            )
            return json.dumps({"text": txt})

        ctx = {"checked_calculations": {}, "visual_reading": None}
        result = write(self.case, opened, ctx, lambda c: {}, build, dispatch)
        expected = {**raw, "facts": [good]}
        self.assertEqual(
            result.draft.model_dump(),
            ResearchMemo.model_validate(expected).model_dump(),
        )
        raw["analysis"] = p
        with self.assertRaisesRegex(ParagraphIssue, "analysis"):
            write(self.case, opened, ctx, lambda c: {}, build, dispatch)
        raw["facts"][0]["citations"] = [
            {"passage_id": "P1", "quote": "This text never appeared in the original."}
        ]
        with self.assertRaisesRegex(CaseError, "quote"):
            build(json.dumps(raw))

    def test_dollar_boundary(self):
        left = Decimal("0.058384")
        capacity = repair_input_capacity(ASTRA, left)
        self.assertEqual(capacity, 2766)
        self.assertLess(
            reserve(capacity, left, model=ASTRA, max_output_tokens=PATCH_OUTPUT_TOKENS),
            left,
        )
        with self.assertRaises(BudgetExhausted):
            reserve(
                capacity + 1, left, model=ASTRA, max_output_tokens=PATCH_OUTPUT_TOKENS
            )


class RevisedStudyTests(unittest.TestCase):
    def test_every_arm_completes_and_replays_without_provider(self):
        manifest = study.registration("fixture")
        row = manifest["schedule"][0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = root / "input"
            export_development(row["case_id"], packet)
            for arm in row["arms"]:
                with self.subTest(arm=arm):
                    corpus = PacketCorpus(packet, row["packet_sha256"])
                    ticket = study.admission(manifest, row, arm)
                    transport = FixtureTransport()
                    record = study.work(corpus, ticket, root / arm, transport)
                    self.assertEqual(record["state"], "complete", record.get("failure"))
                    self.assertEqual(record["budget_usd"], "0.30")
                    self.assertEqual(record["policy_version"], study.VERSION)
                    provider = FixtureTransport()
                    copy = study.work(corpus, ticket, root / arm, provider, replay=True)
                    self.assertEqual(copy["memo_sha256"], record["memo_sha256"])
                    self.assertFalse(provider.requests)

    def test_archived_replay_and_reservation(self):
        result = grounding_replay.experiment()
        self.assertEqual(
            result, json.loads((grounding_replay.REVISION / "replay.json").read_bytes())
        )
        rows = [s for a in result["arms"] for s in a["saved_stages"]]
        self.assertEqual((len(rows), sum(s["accepted"] for s in rows)), (11, 9))
        self.assertEqual(
            sum(
                a["revised_exact_path"]["status"] == "memo_reproduced"
                for a in result["arms"]
            ),
            1,
        )
        self.assertLess(Decimal(result["repair"]["maximum_total_usd"]), Decimal("0.20"))
