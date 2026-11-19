import json
import unittest
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from evaluations.citation_review import experiment
from scripts import research_team as runner
from scripts import team_research as team
from scripts.claim_review import review_candidate, review_once
from scripts.disney_hulu_case import CaseError
from scripts.retrieval_gateway import Corpus, Evidence
from scripts.sol_baseline import fingerprint, generation_request, json_bytes
from scripts.sol_provider import MODEL
from scripts.team_meter import funded_limit
from scripts.team_report import render_candidate

PROOF = experiment.PROOF


def saved(arm):
    return json.loads((PROOF / arm / "run.json").read_bytes())


class ClaimReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.control, cls.challenge = saved("control"), saved("challenge")
        cls.case, cls.corpus = team.case_for("toyota"), Corpus()
        cls.opened = {key: Evidence(**v) for key, v in cls.control["evidence"].items()}
        cls.first = cls.control["review"]["rounds"][0]
        cls.good = render_candidate(
            cls.case, cls.opened, "write", json_bytes(cls.first["draft"])
        )

    def test_live_pair_replays_and_matches_the_prespecified_rubric(self):
        control = runner.replay(self.case, self.control)
        self.assertEqual(control, (PROOF / "control/memo.md").read_bytes())
        config, workflow, _ = experiment.from_control(PROOF / "control")
        corrected = runner.reproduce(self.challenge, config, workflow)
        self.assertEqual(corrected, (PROOF / "challenge/memo.md").read_bytes())
        self.assertEqual(
            fingerprint(corrected), self.challenge["review"]["released_memo_sha256"]
        )
        self.assertEqual(
            list(experiment.score(self.control, self.challenge).values()),
            [True, True, True],
        )
        self.assertEqual(
            len(
                {
                    self.control["calls"]["review.initial"]["provider"]["ids_sha256"],
                    *(
                        x["provider"]["ids_sha256"]
                        for x in self.challenge["calls"].values()
                    ),
                }
            ),
            4,
        )

    def test_review_context_is_fresh_and_only_the_metric_changes_between_arms(self):
        observed = {}

        def capture(arm, workflow):
            def observe(ask):
                def dispatch(stage, prompt, limit):
                    if stage.startswith("review.") or stage == "lead.revise":
                        observed[arm, stage] = deepcopy(prompt)
                    return ask(stage, prompt, limit)

                return workflow(dispatch)

            return observe

        runner.reproduce(
            self.control,
            runner.configuration(self.case, self.corpus, "reviewed"),
            capture(
                "control",
                lambda ask: team.research(
                    self.case, self.corpus, ask, route="reviewed"
                ),
            ),
        )
        config, workflow, _ = experiment.from_control(PROOF / "control")
        runner.reproduce(self.challenge, config, capture("challenge", workflow))
        contexts = []
        for key in (
            ("control", "review.initial"),
            ("challenge", "review.initial"),
            ("challenge", "review.recheck"),
        ):
            prompt = deepcopy(observed[key])
            data = json.loads(prompt.pop("input"))
            self.assertEqual(
                set(data), {"claims", "cutoff", "memo", "passages", "question"}
            )
            self.assertEqual(data["passages"][0]["text"], self.opened["P1"].text)
            self.assertEqual(prompt["model"], MODEL)
            request = generation_request(prompt)
            self.assertFalse(request["store"])
            self.assertNotIn("previous_response_id", request)
            self.assertNotIn("tools", request)
            contexts.append((prompt, data))
        self.assertEqual(contexts[0][0], contexts[1][0])
        self.assertEqual(contexts[1][0], contexts[2][0])
        positive, negative = deepcopy(contexts[0][1]), contexts[1][1]
        paragraph = positive["claims"]["paragraph_1"]
        original = paragraph["text"]
        paragraph["text"] = original.replace("operating income", "net income", 1)
        positive["memo"] = positive["memo"].replace(original, paragraph["text"], 1)
        self.assertEqual(positive, negative)
        self.assertEqual(
            contexts[2][1]["memo"].encode(), (PROOF / "challenge/memo.md").read_bytes()
        )
        revision = json.loads(observed["challenge", "lead.revise"]["input"])
        self.assertEqual(
            revision["review"], self.challenge["review"]["rounds"][0]["assessment"]
        )

    def test_reviewer_opens_exact_evidence_and_rejects_future_or_modified_input_before_dispatch(
        self,
    ):
        ask = Mock(side_effect=AssertionError("paid call must not dispatch"))
        for case, source in (
            (replace(self.case, cutoff="2023-05-09"), self.opened["P1"]),
            (self.case, replace(self.opened["P1"], text="Changed content")),
            (self.case, replace(self.opened["P1"], sha256="0" * 64)),
        ):
            with (
                self.subTest(case=case.cutoff),
                self.assertRaisesRegex(CaseError, "reopen"),
            ):
                review_once(
                    case, self.corpus, {"P1": source}, self.good, ask, "review.initial"
                )
        ask.assert_not_called()

    def test_review_cannot_approve_a_claim_using_only_a_different_open_passage(self):
        with self.corpus.session(self.case.cutoff) as archive:
            (extra,) = archive.read(
                self.opened["P1"].url, locator="PDF p2 §2", max_date=archive.cutoff
            )
        finding = {**self.first["assessment"]["findings"][0], "evidence_ids": ["P2"]}
        response = {**self.first["assessment"], "findings": [finding]}
        with self.assertRaisesRegex(CaseError, "claim's citation"):
            review_once(
                self.case,
                self.corpus,
                {**self.opened, "P2": extra},
                self.good,
                Mock(return_value=json_bytes(response)),
                "review.initial",
            )

    def test_dated_search_with_no_financial_claims_needs_no_paid_reviewer(self):
        before = json.loads(
            (PROOF.parent / "first-team/tradeweb-before/run.json").read_bytes()
        )
        case = team.case_for("tradeweb-before")
        ask = Mock(side_effect=lambda stage, *_: before["calls"][stage]["output"])
        memo, trace = team.research(case, self.corpus, ask, route="reviewed")
        self.assertEqual(trace["review"]["status"], "no_financial_claims")
        self.assertEqual(trace["review"]["rounds"], [])
        self.assertEqual(
            runner.release_state(memo, trace, review_required=True),
            ("complete", before["memo_sha256"]),
        )
        self.assertEqual(
            {call.args[0] for call in ask.call_args_list}, set(before["calls"])
        )

    def test_review_requires_complete_unique_claims_and_relevant_evidence(self):
        draft = deepcopy(self.first["draft"])
        draft["paragraphs"].append(deepcopy(draft["paragraphs"][0]))
        two = render_candidate(self.case, self.opened, "write", json_bytes(draft))
        base = self.first["assessment"]["findings"][0]
        for findings in (
            [base],
            [base, base],
            [base, {**base, "claim_id": "wrong"}],
            [base, {**base, "claim_id": "paragraph_2", "evidence_ids": ["P8"]}],
            [base, {**base, "claim_id": "paragraph_2", "evidence_ids": ["P1", "P1"]}],
            [base, {**base, "claim_id": "paragraph_2", "reason": " "}],
        ):
            response = {**self.first["assessment"], "findings": findings}
            with self.subTest(findings=findings), self.assertRaises(CaseError):
                review_once(
                    self.case,
                    self.corpus,
                    self.opened,
                    two,
                    Mock(return_value=json_bytes(response)),
                    "review.initial",
                )
        fields = self.first["assessment"]
        rejected = review_once(
            self.case,
            self.corpus,
            self.opened,
            self.good,
            lambda *_: json_bytes({**fields, "addresses_question": False}),
            "review.initial",
        )
        self.assertFalse(rejected["approved"])

    def test_unfixed_claim_and_rejected_second_review_withhold_the_memo(self):
        _, changed = experiment.change_metric(
            self.good, self.case, self.opened, "write"
        )
        rejected = self.challenge["calls"]["review.initial"]["output"]
        for replacement, expected in (
            (changed, ["review.initial", "lead.revise"]),
            (self.good, ["review.initial", "lead.revise", "review.recheck"]),
        ):
            with self.subTest(expected=expected):
                ask = Mock(
                    side_effect=[
                        rejected,
                        replacement.draft.model_dump_json(),
                        rejected,
                    ]
                )
                memo, _, trace = review_candidate(
                    self.case, self.corpus, self.opened, "write", changed, ask
                )
                self.assertIsNone(memo)
                self.assertEqual(
                    [call.args[0] for call in ask.call_args_list], expected
                )
                self.assertEqual(trace["status"], "withheld")
                with TemporaryDirectory() as tmp:
                    directory = Path(tmp) / "run"
                    result = runner.execute(
                        {"review_version": "test"},
                        directory,
                        Decimal("0.05"),
                        None,
                        Mock(return_value=(memo, {"review": trace})),
                    )
                    self.assertEqual(result["state"], "withheld")
                    self.assertFalse((directory / "memo.md").exists())

    def test_even_a_post_review_punctuation_edit_cannot_be_exported(self):
        for memo, trace in (
            (self.good.memo + b"!", {"review": self.control["review"]}),
            (self.good.memo, {}),
        ):
            with self.subTest(trace=bool(trace)), TemporaryDirectory() as tmp:
                directory = Path(tmp) / "run"
                with self.assertRaisesRegex(CaseError, "review"):
                    runner.execute(
                        {"review_version": "test"},
                        directory,
                        Decimal("0.05"),
                        None,
                        Mock(return_value=(memo, trace)),
                    )
                self.assertFalse((directory / "memo.md").exists())
                self.assertEqual(
                    json.loads((directory / "run.json").read_bytes())["state"], "failed"
                )

    def test_numeric_validator_keeps_grouped_amounts_whole(self):
        draft = deepcopy(self.first["draft"])
        for wrong in ("3,000", "3,000,000,000"):
            p = draft["paragraphs"][0]
            p["text"] = self.first["draft"]["paragraphs"][0]["text"].replace(
                "3,000,000", wrong
            )
            with (
                self.subTest(wrong=wrong),
                self.assertRaisesRegex(CaseError, "numerical claim"),
            ):
                render_candidate(self.case, self.opened, "write", json_bytes(draft))

    def test_budget_stops_review_before_generation_or_export(self):
        provider = Mock()
        provider.count.return_value = 1000
        for stage in ("review.initial", "lead.revise", "review.recheck"):
            self.assertEqual(
                funded_limit(stage, MODEL, 1000, Decimal("0.0145"), 2048), 1200
            )
        with TemporaryDirectory() as tmp:
            directory = Path(tmp) / "run"
            with self.assertRaisesRegex(CaseError, "exceeds budget"):
                runner.execute(
                    {"review_version": "test"},
                    directory,
                    Decimal("0.001"),
                    provider,
                    lambda ask: review_candidate(
                        self.case, self.corpus, self.opened, "write", self.good, ask
                    ),
                )
            self.assertFalse((directory / "memo.md").exists())
            receipt = json.loads((directory / "run.json").read_bytes())
            self.assertEqual(receipt["state"], "failed")
            self.assertEqual(receipt["estimated_model_cost_usd"], "0")
        provider.generate.assert_not_called()

    def test_replay_rejects_modified_verdict_request_cost_and_draft(self):
        for path, value in (
            (("review", "rounds", 0, "approved"), False),
            (("review", "rounds", 0, "draft_sha256"), "invalid"),
            (("calls", "review.initial", "request_sha256"), "invalid"),
            (("calls", "review.initial", "estimated_model_cost_usd"), "0"),
        ):
            record = deepcopy(self.control)
            node = record
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(CaseError):
                runner.replay(self.case, record)

    def test_existing_disney_memo_claim_inventory_includes_the_calculation(self):
        record = json.loads(
            (PROOF.parent / "first-team/disney-team/run.json").read_bytes()
        )
        case = team.case_for("disney")
        inputs = {pid: Evidence(**h) for pid, h in record["evidence"].items()}
        calls = record["calls"]
        raw = calls.get("lead.memo_repair", calls["lead.memo"])["output"]
        candidate = render_candidate(case, inputs, record["coverage"], raw)
        self.assertEqual(fingerprint(candidate.memo), record["memo_sha256"])
        claims = candidate.claims()
        self.assertEqual(
            set(claims),
            {
                "arithmetic",
                "valuation_basis",
                "expected_payment",
                "appraisal_and_uncertainty",
            },
        )
        self.assertIn(
            "Subtracting the two rounded inputs", claims["arithmetic"]["text"]
        )
        self.assertEqual(len(claims["arithmetic"]["citations"]), 3)

    def test_all_paid_attempts_reconcile_to_project_spend(self):
        usage = json.loads((PROOF / "usage.json").read_bytes())
        prior = json.loads(
            (PROOF.parent / "first-team/development-usage.json").read_bytes()
        )
        total = Decimal(0)
        for arm, record in (("control", self.control), ("challenge", self.challenge)):
            line = usage["attempts"][arm]
            self.assertEqual(line["paid_generations"], len(record["calls"]))
            self.assertEqual(line["estimated_usd"], record["estimated_model_cost_usd"])
            self.assertEqual(Decimal(line["unreconciled_reservation_usd"]), 0)
            total += Decimal(line["estimated_usd"])
        self.assertEqual(total, Decimal(usage["checkpoint_usd"]))
        self.assertEqual(usage["prior_recorded_usd"], prior["project_recorded_usd"])
        self.assertEqual(
            total + Decimal(usage["prior_recorded_usd"]),
            Decimal(usage["project_recorded_usd"]),
        )


if __name__ == "__main__":
    unittest.main()
