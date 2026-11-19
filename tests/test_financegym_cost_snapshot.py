import tempfile
import unittest
import zipfile
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from evaluations.financegym import spend
from scripts.run_checkpoint import load
from scripts.sol_baseline import json_bytes


class PortableCostTests(unittest.TestCase):
    def test_checkout_without_raw_runs_retains_costs_and_restoring_a_real_run_counts_once(
        self,
    ):
        repo = spend.REPO
        charges = spend.saved_charges(spend.SNAPSHOT)
        with tempfile.TemporaryDirectory() as tmp:
            checkout = Path(tmp)
            (checkout / "evaluations").symlink_to(
                repo / "evaluations", target_is_directory=True
            )
            with patch.object(spend, "REPO", checkout):
                fixed = spend.report(snapshot=None)
                before = spend.report()
                for key, field in (
                    ("settled_estimate_usd", "paid_usd"),
                    ("outstanding_usd", "reserved_usd"),
                ):
                    expected = Decimal(fixed[key]) + sum(
                        Decimal(v[field]) for v in charges.values()
                    )
                    self.assertEqual(Decimal(before[key]), expected)
                doc = checkout / "runs/public-fomc-march2023/mixed/research/run.json"
                doc.parent.mkdir(parents=True)
                with zipfile.ZipFile(
                    checkout / "evaluations/open_research/fomc/runs.zip"
                ) as archive:
                    doc.write_bytes(archive.read("mixed/run.json"))
                self.assertEqual(before, spend.report())
                records = {}
                spend.report(capture=records)
                self.assertEqual(records, charges)

    def test_changed_snapshot_or_weaker_receipt_cannot_reduce_the_project_bill(self):
        bad = {"group": "Official research", "paid_usd": "0.10", "reserved_usd": "0"}
        for group, settled in (
            ("Official research", "0.01"),
            ("Different assignment", "0.10"),
        ):
            with self.assertRaisesRegex(ValueError, "already recorded"):
                spend.match_charge({"id": bad}, "id", group, Decimal(settled))
        with tempfile.TemporaryDirectory() as t:
            f = Path(t) / "costs.json"
            original = load(spend.SNAPSHOT)
            f.write_bytes(json_bytes({**original, "assignments": {}}))
            with self.assertRaisesRegex(ValueError, "snapshot changed"):
                spend.saved_charges(f)
