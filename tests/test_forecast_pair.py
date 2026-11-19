import json
import re
import shutil
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

from evaluations.forecast_specialist import experiment as ev
from scripts import research_team as runner
from scripts.disney_hulu_case import CaseError
from scripts.retrieval_gateway import Corpus, Evidence
from scripts.sol_baseline import generation_request, json_bytes
from scripts.team_research import research

PAIR = ev.PROOF / "pair"


class ForecastPairTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.control = ev.load_run(PAIR / "control")
        cls.treatment = ev.load_run(PAIR / "specialist")
        cls.rules = ev.protocol()
        cls.cap = Decimal(cls.rules["case_cap_usd"])
        cls.corpus = Corpus(ev.CASE.archive)
        cls.history = ev.history_for(PAIR, cls.corpus, cls.cap)
        cls.opened = {k: Evidence(**v) for k, v in cls.control["evidence"].items()}

    def eligible_calls(self, label):
        calls = ev.shared_calls(self.history["control"])
        if label == "control":
            calls["lead.memo"] = self.history["control"]["calls"]["lead.memo"]
            calls.update(self.control["calls"])
        else:
            calls["forecast.analyze"] = self.history["note"]["calls"][
                "forecast.analyze"
            ]
            calls["lead.memo"] = self.history["writer"]["calls"]["lead.memo"]
            calls.update(self.treatment["calls"])
        return calls

    def test_both_arms_and_grades_replay_with_equal_effective_budgets(self):
        results = ev.trial(PAIR)
        self.assertEqual(results, json.loads((PAIR / "results.json").read_bytes()))
        for receipt in (self.control, self.treatment):
            self.assertEqual(
                Decimal(receipt["budget_usd"]) + Decimal(receipt["prepaid_usd"]),
                self.cap,
            )
        for k in ev.PAIRED_FIELDS:
            self.assertEqual(self.control[k], self.treatment[k])
        calls = self.treatment["calls"]
        self.assertNotIn("forecast.analyze", calls)
        self.assertEqual(
            json.loads(self.history["note"]["calls"]["forecast.analyze"]["output"]),
            self.treatment["forecast_specialist"],
        )
        self.assertFalse(any(s.startswith(ev.SHARED) for s in calls))
        for label in ("control", "specialist"):
            arm = results["arms"][label]
            self.assertLessEqual(Decimal(arm["effective_cost_usd"]), self.cap)
            self.assertEqual(
                arm["outcome_out_of_1"],
                int(arm["base_call"] == results["outcome"]["bucket"]),
            )
        actual_spend = sum(
            Decimal(ev.load_run(PAIR / name)["estimated_model_cost_usd"])
            for name in (
                "control",
                "history/control",
                "history/note",
                "history/writer",
                "specialist",
                "grade-control",
                "grade-specialist",
            )
        )
        self.assertEqual(actual_spend, Decimal(results["pair_and_grading_usd"]))
        self.assertTrue(
            all(
                Decimal(ev.load_run(PAIR / d)["unreconciled_reservation_usd"]) == 0
                for d in (
                    "control",
                    "history/control",
                    "history/note",
                    "history/writer",
                    "specialist",
                    "grade-control",
                    "grade-specialist",
                )
            )
        )

    def test_new_pair_can_fork_without_any_recovery_receipts(self):
        cfg, available, workflow = ev.funded_fork(
            self.history["control"], self.corpus, self.cap, "forecast"
        )
        self.assertEqual(available + Decimal(cfg["shared_cost_usd"]), self.cap)
        self.assertEqual(cfg["history_sha256"], {})
        calls = self.eligible_calls("specialist")
        ask = Mock(
            side_effect=lambda s, p, n: runner.recorded_output(calls[s], s, p, n)
        )
        memo, trace = workflow(ask)
        self.assertEqual(memo, (PAIR / "specialist/memo.md").read_bytes())
        self.assertEqual(trace["review"], self.treatment["review"])
        self.assertEqual(
            {c.args[0] for c in ask.call_args_list},
            {"forecast.analyze", "lead.memo", "review.initial"},
        )

    def test_cumulative_spend_counts_unique_confirmed_generations(self):
        usage = json.loads((ev.PROOF / "usage.json").read_bytes())
        prior = json.loads(
            (ev.PROOF.parent / "citation_review/usage.json").read_bytes()
        )
        seen = set()
        total = Decimal(0)
        for label, summary in usage["attempts"].items():
            r = ev.load_run(PAIR / label)
            self.assertEqual(summary["generations"], len(r["calls"]))
            self.assertEqual(summary["estimated_usd"], r["estimated_model_cost_usd"])
            for c in r["calls"].values():
                digest = c["provider"]["ids_sha256"]
                self.assertNotIn(digest, seen)
                seen.add(digest)
            total += Decimal(summary["estimated_usd"])
        self.assertEqual(total, Decimal(usage["checkpoint_usd"]))
        self.assertEqual(usage["prior_project_usd"], prior["project_recorded_usd"])
        self.assertEqual(
            total + Decimal(usage["prior_project_usd"]),
            Decimal(usage["project_recorded_usd"]),
        )

    def test_specialist_is_the_only_extra_input_and_grading_is_route_blind(self):
        by_arm = {}

        def capture(label, workflow):
            by_arm[label] = {}

            def testing(ask):
                def inspect(stage, prompt, limit):
                    payload = json_bytes(generation_request(prompt))
                    for forbidden in (
                        b"000104581023000171",
                        b"0001045810-23-000171",
                        b"q2fy24pr.htm",
                        b"13.51",
                        b"13,507",
                        b"outcome_cutoff",
                        b"actual_usd_billions",
                    ):
                        self.assertNotIn(forbidden, payload)
                    by_arm[label][stage] = prompt
                    return ask(stage, prompt, limit)

                return workflow(inspect)

            return testing

        memos = {}
        for label, route in (("control", "reviewed"), ("specialist", "forecast")):
            workflow = capture(
                label, lambda ask, r=route: research(ev.CASE, self.corpus, ask, route=r)
            )
            calls = self.eligible_calls(label)
            memos[label], trace = workflow(
                lambda stage, prompt, limit, c=calls: runner.recorded_output(
                    c[stage], stage, prompt, limit
                )
            )
            self.assertEqual(memos[label], (PAIR / label / "memo.md").read_bytes())
            self.assertEqual(trace["review"], ev.load_run(PAIR / label)["review"])
        control_memo, treatment_memo = memos["control"], memos["specialist"]
        control_author = deepcopy(by_arm["control"]["lead.memo"])
        treatment_author = deepcopy(by_arm["specialist"]["lead.memo"])
        base = json.loads(control_author.pop("input"))
        advanced = json.loads(treatment_author.pop("input"))
        self.assertEqual(
            advanced.pop("forecast_specialist"), self.treatment["forecast_specialist"]
        )
        self.assertEqual(control_author, treatment_author)
        self.assertEqual(base, advanced)
        self.assertEqual(
            set(json.loads(by_arm["specialist"]["forecast.analyze"]["input"])),
            {"question", "cutoff", "passages"},
        )
        for label, record, memo in (
            ("control", self.control, control_memo),
            ("specialist", self.treatment, treatment_memo),
        ):
            for stage, prompt in by_arm[label].items():
                if stage.startswith("review."):
                    self.assertEqual(
                        set(json.loads(prompt["input"])),
                        {"claims", "question", "cutoff", "passages", "memo"},
                    )
            cfg, grade = ev.grade_workflow(memo, record, self.opened, self.rules)
            receipt = ev.load_run(PAIR / ("grade-" + label))
            runner.reproduce(receipt, cfg, capture("grade-" + label, grade))
        prompts = [
            deepcopy(by_arm["grade-" + label]["grader.score"])
            for label in ("control", "specialist")
        ]
        contexts = [json.loads(p.pop("input")) for p in prompts]
        for prompt, context in zip(prompts, contexts, strict=True):
            item = prompt["text"]["format"]["schema"]["$defs"]["Mark"]["properties"][
                "span_ids"
            ]["items"]
            self.assertEqual(set(item.pop("enum")), set(context["report_spans"]))
        self.assertEqual(prompts[0], prompts[1])
        for context in contexts:
            self.assertEqual(
                set(context),
                {
                    "question",
                    "cutoff",
                    "report",
                    "report_spans",
                    "criteria",
                    "dated_passages",
                },
            )
            del context["report"], context["report_spans"]
        self.assertEqual(contexts[0], contexts[1])

    def test_warmed_later_exhibit_cannot_change_a_live_research_request(self):
        with TemporaryDirectory() as temp:
            directory = Path(temp) / "mixed"
            shutil.copytree(ev.CASE.archive, directory)
            late = ev.EVALUATOR / "outcome"
            shutil.copytree(late / "objects", directory / "objects", dirs_exist_ok=True)
            c = json.loads((directory / "catalog.json").read_bytes())
            later = json.loads((late / "catalog.json").read_bytes())["sources"][0]
            c["sources"].append(later)
            (directory / "catalog.json").write_bytes(json_bytes(c))
            mixed = Corpus(directory)
            with mixed.session("2023-08-24") as after:
                self.assertTrue(after.read(later["url"], max_date=after.cutoff))
            with mixed.session(ev.CASE.cutoff) as before:
                self.assertEqual(before.read(later["url"], max_date=before.cutoff), ())
            for route, receipt in (
                ("reviewed", self.control),
                ("forecast", self.treatment),
            ):
                # The mixed catalog has its own fingerprint. Check each paid request against
                # the original receipt, independent of that intentionally different catalog.
                calls = self.eligible_calls(
                    "control" if route == "reviewed" else "specialist"
                )

                def dispatch(stage, prompt, limit, active=calls):
                    return runner.recorded_output(active[stage], stage, prompt, limit)

                memo, trace = research(ev.CASE, mixed, dispatch, route=route)
                self.assertEqual(trace["review"], receipt["review"])
                if memo is not None:
                    for link in re.findall(r"\]\((https://[^)]+)\)", memo.decode()):
                        with mixed.session(ev.CASE.cutoff) as cutoff:
                            self.assertTrue(cutoff.read(link, max_date=cutoff.cutoff))

    def test_grade_rejects_untraceable_awards_or_duplicate_criteria(self):
        memo = (PAIR / "control/memo.md").read_bytes()
        _, workflow = ev.grade_workflow(memo, self.control, self.opened, self.rules)
        raw = json.loads(
            ev.load_run(PAIR / "grade-control")["calls"]["grader.score"]["output"]
        )
        for defect in ("duplicate", "unknown"):
            data = deepcopy(raw)
            if defect == "duplicate":
                data["marks"][1]["criterion"] = data["marks"][0]["criterion"]
            else:
                data["marks"][0].update(awarded=True, span_ids=["injected"])
            with self.subTest(defect=defect), self.assertRaises(CaseError):
                workflow(Mock(return_value=json_bytes(data)))
