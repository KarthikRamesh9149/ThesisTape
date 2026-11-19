from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from evaluations.financegym import budget_routes as routes
from evaluations.financegym import submission
from scripts.sol_baseline import fingerprint, json_bytes


class IndependentBudgetTests(TestCase):
    a, b, c, d = (char * 24 for char in "abcd")

    @staticmethod
    def candidate(publishers, total, revision=True):
        return {
            "original_run_sha256": "7" * 64,
            "publisher_editions": publishers,
            "source_editions": total,
            "unfunded_revision": revision,
        }

    def test_selects_fewest_source_rich_cases_and_preserves_difficult_assignments(self):
        cases = {
            self.b: self.candidate(4, 6),
            self.a: self.candidate(4, 6),
            self.c: self.candidate(6, 9, False),
            self.d: self.candidate(0, 9),
        }

        def choose(balance, observed="0.33"):
            return routes.select(cases, Decimal(balance), Decimal(observed))

        self.assertEqual(choose("1.62"), [])
        self.assertEqual(choose("1.61"), [self.a])
        self.assertEqual(choose("1.51"), [self.a, self.b])
        self.assertEqual(choose("0"), [self.a, self.b])
        self.assertEqual(choose("0", "0.22"), [])
        cases[self.b]["source_editions"] += 1
        self.assertEqual(choose("1.61"), [self.b])
        cases[self.a] = self.candidate(1, 1)
        self.assertEqual(choose("0"), [self.b])

    def create_plan(self):
        return {
            "version": 1,
            "policy_sha256": routes.policy_sha(),
            "reference_only": False,
            "budget": {
                "owner_limit_usd": "140",
                "admission_limit_usd": "139",
                "available_usd": "0.6",
                "active_full_allowances_usd": "0",
                "settled_estimate_usd": "138.39",
                "outstanding_provider_usd": "0.01",
            },
            "observed_independent_costs_usd": {self.b: "0.32"},
            "unanswered": {self.a: self.candidate(2, 3)},
            "alternate_tasks": [self.a],
        }

    def test_allocation_rejects_changed_source_counts_choices_cost_and_reference(self):
        plan = self.create_plan()
        self.assertEqual(routes.validate(plan), plan)
        for modify in (
            lambda p: p.update(alternate_tasks=[]),
            lambda p: p["budget"].update(settled_estimate_usd="138.30"),
            lambda p: p["budget"].update(active_full_allowances_usd="0.45"),
            lambda p: p["unanswered"][self.a].update(publisher_editions=4),
            lambda p: p["unanswered"][self.a].update(original_run_sha256="invalid"),
            lambda p: p.update(reference_only=True),
            lambda p: p.update(reference_only="true"),
        ):
            with self.subTest(change=modify):
                other = deepcopy(plan)
                modify(other)
                with self.assertRaises(ValueError):
                    routes.validate(other)

    def test_balance_uses_all_original_charges_and_only_preexisting_secondaries(self):
        plan = self.create_plan()
        cfg = {
            "profiles_by_task": {self.a: "single_astra", self.b: "single_astra"},
            "recorded_prior_project_usd": "137.61",
            "outstanding_prior_reservations_usd": "0.01",
        }

        def stage(paid):
            return {"estimated_cost_usd": paid, "outstanding_usd": "0"}

        first = {
            **stage("0.20"),
            "original_run_sha256": "7" * 64,
            "eligible_sources": 3,
            "report_sha256": None,
        }
        selected = routes.tag(plan, self.a)
        proofs = [
            {
                "task_id": self.a,
                "attempts": [first, stage("0.19")],
                "primary_draft_review": stage("0.01"),
                "independent_budget_route": selected,
            },
            {
                "task_id": self.b,
                "attempts": [stage("0.25"), stage("0.30")],
                "editorial": stage("0.02"),
            },
        ]
        routes.verify_accounts(plan, cfg, proofs)
        for modify in (
            lambda p: p[0]["attempts"][0].update(estimated_cost_usd="0.18"),
            lambda p: p[0].pop("primary_draft_review"),
            lambda p: p[1].pop("editorial"),
            lambda p: p[0]["attempts"][0].update(original_run_sha256="6" * 64),
            lambda p: p[0].pop("independent_budget_route"),
            lambda p: p.append(p[0]),
        ):
            other = deepcopy(proofs)
            modify(other)
            with self.assertRaises(ValueError):
                routes.verify_accounts(plan, cfg, other)

    def test_recovery_ticket_is_bound_to_the_original_and_route_recovers_after_an_error(
        self,
    ):
        plan = self.create_plan()
        with TemporaryDirectory() as temp:
            folder = Path(temp)
            raw = json_bytes({"question": "First record"})
            (folder / "run.json").write_bytes(raw)
            plan["unanswered"][self.a]["original_run_sha256"] = fingerprint(raw)
            selected = routes.tag(plan, self.a)
            row = {"task_id": self.a}
            with (
                self.assertRaisesRegex(RuntimeError, "exit the case"),
                routes.for_case(row, selected),
            ):
                self.assertEqual(
                    routes.recovery.second_route({}, "single_astra")[1:],
                    ("mixed", "0.30"),
                )
                expected = routes.ordinary_ticket(row, {}, {}, folder)
                self.assertEqual(
                    routes.recovery.ticket(row, {}, {}, folder),
                    {**expected, "independent_budget_route": selected},
                )
                with self.assertRaisesRegex(ValueError, "Another task"):
                    routes.recovery.ticket({"task_id": self.b}, {}, {}, folder)
                raise RuntimeError("exit the case")
            self.assertIs(routes.recovery.second_route, routes.ordinary_route)
            self.assertIs(routes.recovery.ticket, routes.ordinary_ticket)
            (folder / "run.json").write_bytes(b"{}")
            with self.assertRaisesRegex(ValueError, "original investigation changed"):
                routes.ticket(row, {}, {}, folder, selected)
            self.assertIsNone(routes.tag(plan, self.b))

    def test_export_rejects_an_unregistered_model_or_claimed_reroute_without_a_second_attempt(
        self,
    ):
        plan = self.create_plan()
        selected = routes.tag(plan, self.a)
        cfg = {
            "profiles_by_task": {self.a: "single_astra"},
            "unanswered_recovery": {"profile": "evidence_team_v4", "cap_usd": "0.45"},
        }
        first = {
            "profile": "single_astra",
            "state": "failed",
            "report_sha256": None,
            "estimated_cost_usd": "0.20",
            "outstanding_usd": "0",
        }
        second = {
            **first,
            "profile": "mixed",
            "state": "qualified",
            "report_sha256": "published",
            "estimated_cost_usd": "0.15",
        }
        proof = {
            "task_id": self.a,
            "primary_profile": "single_astra",
            "profile": "mixed",
            "state": "qualified",
            "report_sha256": "published",
            "estimated_cost_usd": "0.35",
            "outstanding_usd": "0",
            "attempts": [first, second],
            "independent_budget_route": selected,
        }
        self.assertTrue(
            submission.verified_selection(
                cfg, proof, "extension", selected_recovery=selected
            )
        )
        self.assertFalse(submission.verified_selection(cfg, proof, "extension"))
        self.assertFalse(
            submission.verified_selection(
                cfg,
                {**proof, "attempts": [first]},
                "extension",
                selected_recovery=selected,
            )
        )
        wrong = {
            **proof,
            "attempts": [first, {**second, "profile": "evidence_team_v4"}],
        }
        self.assertFalse(
            submission.verified_selection(
                cfg, wrong, "extension", selected_recovery=selected
            )
        )

    def test_cannot_allocate_while_a_primary_question_is_outstanding(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            with (
                patch.object(
                    routes.runner, "tasks", return_value=[{"task_id": self.a}]
                ),
                patch.object(
                    routes.editorial,
                    "folders",
                    return_value=(root / "primary", root / "recovery"),
                ),
                patch.object(routes.budget, "ProjectBudget") as spending,
                self.assertRaisesRegex(ValueError, "Settle all primary"),
            ):
                routes.prepare(root / "questions", root / "plan")
            spending.assert_not_called()
            self.assertFalse((root / "plan").exists())
