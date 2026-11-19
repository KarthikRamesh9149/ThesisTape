import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path
from threading import Barrier, Event

from evaluations.financegym.budget import BudgetUnavailable, ProjectBudget


class ProjectBudgetTests(unittest.TestCase):
    def test_parallel_investigators_cannot_spend_the_same_project_balance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = lambda: {
                "settled_estimate_usd": "138.55",
                "outstanding_usd": "0.10",
            }
            barrier, acquired, finish = Barrier(2), Event(), Event()

            def attempt(i):
                gate = ProjectBudget(root / "limits", account=account)
                barrier.wait(timeout=10)
                try:
                    with gate.reserve(root / f"{i}.json", "0.30"):
                        acquired.set()
                        finish.wait(timeout=10)
                        return "accepted"
                except BudgetUnavailable:
                    finish.set()
                    return "declined"

            with ThreadPoolExecutor(max_workers=2) as pool:
                result = list(pool.map(attempt, [1, 2]))
            self.assertTrue(acquired.is_set())
            self.assertCountEqual(result, ["accepted", "declined"])
            status = ProjectBudget(root / "limits", account=account).status()
            self.assertEqual(Decimal(status["available_usd"]), Decimal("0.05"))

    def test_restart_reuses_one_allowance_and_preserves_unreconciled_exposure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            account = {"settled_estimate_usd": "138.50", "outstanding_usd": "0.05"}
            gate = ProjectBudget(root / "limits", account=lambda: account)
            record = root / "run.json"
            with self.assertRaises(InterruptedError), gate.reserve(record, "0.40"):
                raise InterruptedError
            self.assertEqual(Decimal(gate.status()["available_usd"]), Decimal("0.05"))
            with (
                self.assertRaisesRegex(ValueError, "change"),
                gate.reserve(record, "0.45"),
            ):
                self.fail("The allowance changed")
            saved = {
                "state": "qualified",
                "budget_usd": "0.40",
                "events": [],
                "calls": {},
                "estimated_model_cost_usd": "0",
                "unreconciled_reservation_usd": "0",
            }
            with gate.reserve(record, "0.40"):
                record.write_text(json.dumps(saved))
            self.assertEqual(Decimal(gate.status()["available_usd"]), Decimal("0.45"))
            with (
                self.assertRaisesRegex(ValueError, "finished"),
                gate.reserve(record, "0.40"),
            ):
                self.fail("A completed investigation ran twice")
            with self.assertRaises(ValueError):
                ProjectBudget(root / "limits", ceiling="140")
