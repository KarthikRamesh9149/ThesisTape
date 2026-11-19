import json
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from evaluations.material_dispute import experiment as ev
from scripts.baseline_evidence import check_claim_numbers
from scripts.disney_hulu_case import CaseError
from scripts.material_dispute import REVIEW_ALLOWANCE
from scripts.research_team import release_state, replay, run
from scripts.sol_provider import ASTRA, MODEL, Result
from scripts.team_meter import verify_ledger
from scripts.team_research import case_for, research


class DisputeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = case_for("twitter-deal")
        cls.corpus = cls.case.corpus()
        cls.rules = ev.load(cls.case.archive.parent / "evaluator/protocol.json")
        with cls.corpus.session(cls.case.cutoff) as s:
            hits = [
                h
                for url in cls.case.source_urls
                for h in s.read(url, max_date=s.cutoff)
            ]
        cls.opened = {f"P{i}": h for i, h in enumerate(hits, 1)}

    def author(self, changed=False):
        def paragraph(text, *ids):
            return {
                "text": text,
                "citations": [
                    {"passage_id": k, "quote": self.opened[k].text[:75].strip()}
                    for k in ids
                ],
            }

        return {
            "paragraphs": [
                paragraph(
                    "The buyer alleged that Twitter denied access to information and invoked its right to terminate.",
                    "P1",
                    "P2",
                ),
                paragraph(
                    ("Twitter disputed the notice. " if changed else "")
                    + "Twitter denied a breach and sought enforcement at USD 54.20 per share. Whether the deal would close remained unresolved.",
                    "P3",
                    "P4",
                ),
            ],
            "filing_history": None,
        }

    def scenario(
        self,
        *,
        route="dispute",
        kind="material",
        treatment="qualified",
        witnesses=None,
        advice=None,
        revised_treatment="qualified",
        skip=None,
        same_revision=False,
        case=None,
    ):
        calls = {}

        def ask(stage, prompt, limit, *, keep_usd=None):
            self.assertNotIn(stage, calls)
            calls[stage] = (prompt, keep_usd)
            context = json.loads(prompt["input"])
            if stage == skip:
                return None
            if stage in ("lead.memo", "lead.revise"):
                return json.dumps(
                    self.author(changed=stage == "lead.revise" and not same_revision)
                )
            if stage == "dispute.adjudicate":
                self.assertEqual(keep_usd, REVIEW_ALLOWANCE)
                self.assertEqual(prompt["model"], ASTRA)
                return json.dumps(
                    advice
                    or {
                        "disposition": "unresolved",
                        "recommendation": "retain",
                        "assessment": "The parties dispute liability and closing.",
                        "evidence_ids": ["P2", "P4"],
                        "controlling_passage": None,
                    }
                )
            self.assertEqual(prompt["model"], MODEL)
            return json.dumps(
                {
                    "findings": [
                        {
                            "claim_id": key,
                            "verdict": "supported",
                            "reason": "Supported by the cited original.",
                            "evidence_ids": list(
                                dict.fromkeys(
                                    c["passage_id"] for c in claim["citations"]
                                )
                            ),
                        }
                        for key, claim in context["claims"].items()
                    ],
                    "addresses_question": True,
                    "coverage_reason": "Reviewed the requested terms and cutoff.",
                    "conflict": {
                        "kind": kind,
                        "treatment": treatment
                        if stage == "review.initial"
                        else revised_treatment,
                        "issue": "Whether the buyer effectively terminated the transaction.",
                        "witnesses": witnesses
                        if witnesses is not None
                        else (["P2", "P4"] if kind == "material" else []),
                        "controlling_passage": "P4" if kind == "reconciled" else None,
                    },
                }
            )

        memo, trace = research(case or self.case, self.corpus, ask, route=route)
        release_state(memo, trace, review_required=True)
        return memo, trace, calls

    def test_two_verified_witnesses_and_equal_blind_author_and_reviewer_inputs(self):
        memo, control, off = self.scenario(route="reviewed")
        answer, trace, on = self.scenario()
        self.assertEqual(answer, memo)
        self.assertEqual(control["evidence"], trace["evidence"])
        self.assertEqual(off["lead.memo"], on["lead.memo"])
        self.assertEqual(off["review.initial"], on["review.initial"])
        self.assertEqual(set(on) - set(off), {"dispute.adjudicate"})
        decision = trace["dispute"]
        self.assertEqual(decision["status"], "adjudicated")
        self.assertEqual(set(decision["witnesses"]), {"P2", "P4"})
        self.assertEqual(
            len({x["original_sha256"] for x in decision["witnesses"].values()}), 2
        )
        self.assertTrue(
            all(e["max_date"] == self.case.cutoff for e in decision["retrieval"])
        )
        self.assertEqual(len(decision["retrieval"]), 4)
        for prompt, _ in on.values():
            obj = json.loads(prompt["input"])
            self.assertFalse(
                {"criteria", "pass_rule", "any_source", "evaluator", "score", "arm"}
                & obj.keys()
            )

    def test_nonmaterial_missing_and_superseded_cases_do_not_dispatch(self):
        for kind, treatment in (
            ("none", "not_applicable"),
            ("missing", "qualified"),
            ("reconciled", "definitive"),
        ):
            memo, trace, calls = self.scenario(kind=kind, treatment=treatment)
            self.assertIsNotNone(memo)
            self.assertNotIn("dispute.adjudicate", calls)
            self.assertEqual(trace["dispute"]["status"], kind)
        for witnesses in (["P1", "P2"], ["P2", "P2"], ["P2", "future"]):
            with self.assertRaises(CaseError):
                self.scenario(witnesses=witnesses)

    def test_advice_cannot_release_a_changed_or_unqualified_claim(self):
        advice = {
            "disposition": "resolved",
            "recommendation": "revise",
            "assessment": "Consider the alleged resolution.",
            "evidence_ids": ["P2", "P4", "P3"],
            "controlling_passage": "P3",
        }
        memo, trace, calls = self.scenario(
            advice=advice, revised_treatment="definitive"
        )
        self.assertIsNone(memo)
        self.assertEqual(trace["review"]["status"], "withheld")
        self.assertNotIn(
            "specialist_assessment", json.loads(calls["review.recheck"][0]["input"])
        )
        self.assertTrue(
            calls["lead.revise"][0]["input"].find("specialist_assessment") >= 0
        )
        memo, trace, _ = self.scenario(advice=advice)
        self.assertIsNotNone(memo)
        first, last = trace["review"]["rounds"]
        self.assertFalse(first["approved"])
        self.assertTrue(last["approved"])
        self.assertNotEqual(first["memo_sha256"], last["memo_sha256"])
        self.assertEqual(last["memo_sha256"], trace["review"]["released_memo_sha256"])
        for params in (
            {"skip": "review.recheck"},
            {"skip": "lead.revise"},
            {"same_revision": True},
        ):
            self.assertIsNone(self.scenario(advice=advice, **params)[0])

    def test_bad_specialist_evidence_never_reaches_the_lead_as_advice(self):
        bad = {
            "disposition": "resolved",
            "recommendation": "retain",
            "assessment": "Judgment issued.",
            "evidence_ids": ["P2", "P4"],
            "controlling_passage": "future",
        }
        _, trace, calls = self.scenario(advice=bad)
        self.assertEqual(trace["dispute"]["status"], "invalid_advice")
        self.assertIsNone(trace["dispute"]["advice"])
        self.assertNotIn("lead.revise", calls)
        _, trace, calls = self.scenario(
            treatment="definitive", skip="dispute.adjudicate"
        )
        self.assertEqual(trace["dispute"]["status"], "insufficient_budget")
        self.assertIn("review.recheck", calls)

    def test_cutoff_and_original_document_binding(self):
        ev.verify_sources(self.case, self.corpus, self.rules)
        earlier = replace(self.case, cutoff="2022-07-11T20:38:35Z")
        prompts = []

        def early(stage, prompt, limit):
            prompts.append(prompt["input"])
            if stage == "lead.memo":
                p = self.author()["paragraphs"][0]
                p["text"] += (
                    " The seller's position and closing could not be established from the available record."
                )
                return json.dumps({"paragraphs": [p], "filing_history": None})
            return json.dumps(
                {
                    "findings": [
                        {
                            "claim_id": "paragraph_1",
                            "verdict": "supported",
                            "reason": "Qualified.",
                            "evidence_ids": ["P1", "P2"],
                        }
                    ],
                    "addresses_question": True,
                    "coverage_reason": "Missing response.",
                    "conflict": {
                        "kind": "missing",
                        "treatment": "qualified",
                        "issue": "Only the buyer's letter is available.",
                        "witnesses": [],
                        "controlling_passage": None,
                    },
                }
            )

        memo, trace = research(earlier, self.corpus, early, route="dispute")
        self.assertIsNotNone(memo)
        self.assertEqual(trace["dispute"]["status"], "missing")
        serialized = json.dumps(prompts) + json.dumps(trace)
        for pid in ("P3", "P4"):
            h = self.opened[pid]
            for private in (h.url, h.title, h.sha256, h.text[:75]):
                self.assertNotIn(private, serialized)
        self.assertEqual([len(e["results"]) for e in trace["source_reads"]], [2, 0, 0])

    def test_per_share_unit_cannot_be_substituted_for_an_aggregate_or_foreign_amount(
        self,
    ):
        day = date(2022, 7, 11)
        check_claim_numbers("USD 54.20 per share", self.opened["P3"].text, day)
        for text, original in (
            ("USD 54.20 billion", self.opened["P3"].text),
            ("USD 54.20 per share", "The value was USD 54.20 million."),
            ("USD 54.20 per share", "The price was C$54.20 per share."),
            ("USD 45.20 per share", self.opened["P3"].text),
        ):
            with self.assertRaises(CaseError):
                check_claim_numbers(text, original, day)

    def test_real_meter_skips_unaffordable_escalation_and_replay_checks_the_decision(
        self,
    ):
        _, _, scripted = self.scenario()
        draft = self.author()

        def response(payload):
            if payload["model"] == ASTRA:
                self.fail("Low budget dispatched Astra")
            if payload["text"]["format"]["name"] == "Brief":
                return draft
            claims = json.loads(payload["input"])["claims"]
            return {
                "findings": [
                    {
                        "claim_id": k,
                        "verdict": "supported",
                        "reason": "Supported.",
                        "evidence_ids": [c["passage_id"] for c in v["citations"]],
                    }
                    for k, v in claims.items()
                ],
                "addresses_question": True,
                "coverage_reason": "Complete.",
                "conflict": {
                    "kind": "material",
                    "treatment": "qualified",
                    "issue": "Whether the buyer effectively terminated the transaction.",
                    "witnesses": ["P2", "P4"],
                    "controlling_passage": None,
                },
            }

        class Provider:
            def count(self, prompt):
                return 1000

            def generate(self, payload):
                body = {
                    "model": MODEL,
                    "service_tier": "default",
                    "status": "completed",
                    "id": "local-fake",
                    "usage": {
                        "input_tokens": 1000,
                        "output_tokens": 140,
                        "input_tokens_details": {},
                    },
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [
                                {
                                    "type": "output_text",
                                    "text": json.dumps(response(payload)),
                                }
                            ],
                        }
                    ],
                }
                return Result(body, "test-request", 0)

        with TemporaryDirectory() as tmp:
            d = Path(tmp) / "low"
            rec = run(
                self.case, "dispute", d, Decimal("0.05"), Provider(), corpus=self.corpus
            )
            self.assertEqual(
                replay(self.case, rec, corpus=self.corpus), (d / "memo.md").read_bytes()
            )
            self.assertEqual(rec["events"][-1], ["skip", "dispute.adjudicate"])
            self.assertEqual(rec["unreconciled_reservation_usd"], "0")
            tampered = deepcopy(rec)
            tampered["budget_usd"] = "0.30"
            with self.assertRaisesRegex(CaseError, "affordable"):
                verify_ledger(tampered)
            tampered = deepcopy(rec)
            tampered["skipped_calls"]["dispute.adjudicate"]["request_sha256"] = "0" * 64
            with self.assertRaisesRegex(CaseError, "fingerprint"):
                replay(self.case, tampered, corpus=self.corpus)
        self.assertEqual(scripted["dispute.adjudicate"][1], REVIEW_ALLOWANCE)
