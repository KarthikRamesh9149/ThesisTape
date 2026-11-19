"""Reserve an investigation's full allowance against the owner's total project budget."""

import argparse
import json
import sqlite3
from contextlib import closing, contextmanager
from decimal import Decimal
from pathlib import Path

from evaluations.paired.registration import REPO
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint
from scripts.team_meter import verify_ledger

from . import spend

OWNER_LIMIT = Decimal("140.00")
# Keep one dollar between modeled API usage and the owner's limit.
ADMISSION_LIMIT = OWNER_LIMIT - Decimal("1.00")
TERMINAL = {
    "complete",
    "qualified",
    "withheld",
    "insufficient_evidence",
    "failed",
    "needs_reconciliation",
}


class BudgetUnavailable(ValueError):
    pass


class ProjectBudget:
    def __init__(
        self, directory=None, *, account=spend.report, ceiling=ADMISSION_LIMIT
    ):
        self.directory = directory or REPO / "runs/financegym-budget"
        self.account = account
        self.ceiling = Decimal(ceiling)
        if not self.ceiling.is_finite() or not 0 < self.ceiling <= ADMISSION_LIMIT:
            raise ValueError("Invalid project spending ceiling")

    @contextmanager
    def transaction(self):
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with closing(
            sqlite3.connect(
                self.directory / "reservations.sqlite3",
                timeout=30,
                isolation_level=None,
            )
        ) as db:
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute(
                    "CREATE TABLE IF NOT EXISTS reservations (record TEXT PRIMARY KEY, allowance TEXT NOT NULL)"
                )
                yield db
                db.commit()
            except BaseException:
                db.rollback()
                raise

    @staticmethod
    def terminal(record, allowance):
        if not record.is_file():
            return False
        saved = load(record)
        if Decimal(saved["budget_usd"]) != Decimal(allowance):
            raise ValueError(
                "The investigation ceiling differs from its reserved allowance"
            )
        if saved["state"] not in TERMINAL:
            return False
        verify_ledger(saved, partial=True)
        return True

    def balance(self, db):
        # Retire final assignments before taking the spend snapshot. A running
        # assignment's entire cap remains reserved even if some calls already settled.
        held = Decimal(0)
        for path, allowance in list(
            db.execute("SELECT record, allowance FROM reservations")
        ):
            if self.terminal(Path(path), allowance):
                db.execute("DELETE FROM reservations WHERE record = ?", (path,))
            else:
                held += Decimal(allowance)
        total = self.account()
        paid = Decimal(total["settled_estimate_usd"])
        unresolved = Decimal(total["outstanding_usd"])
        if any(
            not value.is_finite() or value < 0 for value in (paid, unresolved, held)
        ):
            raise ValueError("Cannot reconcile the project balance")
        return {
            "owner_limit_usd": str(OWNER_LIMIT),
            "admission_limit_usd": str(self.ceiling),
            "settled_estimate_usd": str(paid),
            "outstanding_provider_usd": str(unresolved),
            "active_full_allowances_usd": str(held),
            "available_usd": str(self.ceiling - paid - unresolved - held),
        }

    def status(self):
        with self.transaction() as db:
            return self.balance(db)

    @contextmanager
    def reserve(self, record, amount):
        path = Path(record).resolve()
        allowance = Decimal(amount)
        if not allowance.is_finite() or allowance <= 0:
            raise ValueError("A positive finite investigation allowance is required")
        lock = self.directory / "assignments" / fingerprint(str(path).encode())
        lock.mkdir(mode=0o700, parents=True, exist_ok=True)
        with own_run(lock):
            with self.transaction() as db:
                balance = self.balance(db)
                if self.terminal(path, allowance):
                    raise ValueError("This investigation has already finished")
                previous = db.execute(
                    "SELECT allowance FROM reservations WHERE record = ?", (str(path),)
                ).fetchone()
                if previous:
                    if Decimal(previous[0]) != allowance:
                        raise ValueError(
                            "Cannot change an existing project reservation"
                        )
                else:
                    if allowance > Decimal(balance["available_usd"]):
                        raise BudgetUnavailable(
                            f"The project has USD {balance['available_usd']} available for a USD {allowance} investigation"
                        )
                    db.execute(
                        "INSERT INTO reservations VALUES (?, ?)",
                        (str(path), str(allowance)),
                    )
            try:
                yield
            finally:
                # A process can resume the same reservation after a crash. A closed
                # ledger accounts for its actual cost and unresolved provider charges.
                with self.transaction() as db:
                    if self.terminal(path, allowance):
                        db.execute(
                            "DELETE FROM reservations WHERE record = ?", (str(path),)
                        )


def run_registered_stage(questions, task_id, stage):
    from . import budget_routes, runner

    rows = {row["task_id"]: row for row in runner.tasks(questions)}
    if task_id not in rows:
        raise ValueError("Unknown registered task")
    row = rows[task_id]
    plan = (
        budget_routes.validate(load(budget_routes.ALLOCATION))
        if budget_routes.ALLOCATION.is_file()
        else None
    )
    selected = budget_routes.tag(plan, task_id)
    with budget_routes.for_case(row, selected):
        return funded_stage(row, stage)


def funded_stage(row, stage):
    from . import (
        archive_failure,
        budget_publication,
        editorial,
        recovery,
        runner,
        titled_publication,
    )

    task_id = row["task_id"]
    cfg = runner.locked()
    extension = recovery.register(cfg)
    budget = ProjectBudget()
    if stage == "recovery":
        record = recovery.OUTPUT / task_id / "research/run.json"
        cap = recovery.second_route(cfg, runner.route(row, cfg)[1])[2]
        if not recovery.needs_research(runner.RESEARCH / task_id):
            raise ValueError("The original investigation already has a report")
        if budget_publication.REGISTRATION.is_file():
            editor = editorial.locked(cfg, extension)
            spec = budget_publication.locked(titled_publication.locked(editor))
            if budget_publication.eligible(row, cfg, spec):
                review_file = budget_publication.directory(task_id) / "run.json"
                if (
                    not review_file.is_file()
                    or load(review_file)["state"] not in TERMINAL
                ):
                    raise ValueError(
                        "The registered independent draft review must finish first"
                    )
                if load(review_file)["state"] == "qualified":
                    raise ValueError(
                        "The original draft already passed independent review"
                    )
        with budget.reserve(record, cap):
            return archive_failure.recover_one(row, cfg, extension)
    if not editorial.eligible(row, cfg):
        raise ValueError("This investigation does not need funded editing")
    originals = editorial.folders(row)
    editorial.engine.materials(
        row["question"], row["cutoff"], cfg["archive_version"], originals
    )
    specification = editorial.locked(cfg, extension)
    with (
        budget.reserve(
            editorial.ROOT / task_id / "research/run.json", editorial.engine.CAP
        ),
        archive_failure.use_archive_failure_verifier(),
    ):
        return editorial.run_one(row, cfg, extension, specification)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="command", required=True)
    actions.add_parser("status")
    for action in ("recovery", "editorial"):
        cmd = actions.add_parser(action)
        cmd.add_argument("questions", type=Path)
        cmd.add_argument("--task-id", required=True)
    args = parser.parse_args()
    result = (
        ProjectBudget().status()
        if args.command == "status"
        else run_registered_stage(args.questions, args.task_id, args.command)
    )
    print(json.dumps(result, indent=2))
