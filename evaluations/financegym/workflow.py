"""Finish the registered FinanceGym candidate from its durable research and project spend records."""

import argparse
import json
from collections import Counter
from pathlib import Path

from evaluations.paired.corpus import require
from scripts.run_checkpoint import load, own_run

from . import (
    archive_failure,
    budget,
    budget_publication,
    budget_routes,
    editorial,
    evidence,
    recovery,
    runner,
    spend,
    titled_publication,
)


def registrations():
    cfg = runner.locked()
    ext = recovery.register(cfg)
    edit = editorial.locked(cfg, ext)
    title = titled_publication.locked(edit)
    return cfg, ext, edit, title, budget_publication.locked(title)


def state(folder):
    path = folder / "run.json"
    return load(path)["state"] if path.is_file() else None


def next_stage(row, cfg, spec):
    key = row["task_id"]
    first, second = editorial.folders(row)
    if state(first) not in budget.TERMINAL:
        return "primary"
    if not recovery.needs_research(first.parent):
        return "ready"
    if budget_publication.eligible(row, cfg, spec):
        reviewed = state(budget_publication.directory(key))
        if reviewed not in budget.TERMINAL:
            return "draft_review"
        if reviewed == "qualified":
            return "ready"
    if state(second) not in budget.TERMINAL:
        return "recovery"
    if not editorial.eligible(row, cfg):
        return "ready"
    edited = state(editorial.ROOT / key / "research")
    if edited in budget.TERMINAL:
        return "ready" if edited == "qualified" else "unanswered"
    return "title_lookup" if titled_publication.needed(row, cfg) else "editorial"


def inventory(rows, cfg, spec):
    return dict(Counter(next_stage(row, cfg, spec) for row in rows))


def advance(questions, row, stage, registration, allocation=None):
    cfg, ext, edit, title, spec = registration
    key = row["task_id"]
    if stage == "draft_review":
        folder = budget_publication.directory(key)
    elif stage == "recovery":
        folder = recovery.OUTPUT / key / "research"
    else:
        require(
            stage in {"title_lookup", "editorial"},
            "Unknown supplemental research stage",
        )
        folder = editorial.ROOT / key / "research"
    try:
        if stage == "draft_review":
            budget_publication.run_one(row, cfg, spec)
        elif stage == "title_lookup":
            with (
                archive_failure.use_archive_failure_verifier(),
                budget_routes.for_case(row, budget_routes.tag(allocation, key)),
            ):
                titled_publication.run_one(row, cfg, ext, edit, title)
        else:
            budget.run_registered_stage(questions, key, stage)
    except (OSError, ValueError):
        # A legitimately settled failure can advance to an independent route. An
        # error before the checkpoint or a conflicting completed record must surface.
        if state(folder) not in {"failed", "needs_reconciliation"}:
            raise
    rec = load(folder / "run.json")
    print(
        json.dumps(
            {
                "task_id": key,
                "stage": stage,
                "state": rec["state"],
                "stage_usd": rec["estimated_model_cost_usd"],
            }
        ),
        flush=True,
    )
    require(rec["state"] in budget.TERMINAL, "Supplemental research did not settle")
    require(
        rec["state"] != "needs_reconciliation"
        and not str(rec.get("failure", "")).startswith("OpenAI "),
        "A provider incident stopped new research. The original receipt and reservation were retained.",
    )


def finish(questions, destination, receipts):
    registration = registrations()
    cfg, _, _, _, spec = registration
    rows = runner.tasks(questions)
    guard = budget.ProjectBudget()
    lock = guard.directory / "completion"
    lock.mkdir(mode=0o700, parents=True, exist_ok=True)
    with own_run(lock):
        require(
            not any(
                state(runner.RESEARCH / r["task_id"] / "research")
                not in budget.TERMINAL
                for r in rows
            ),
            "Finish or resume the paced primary investigations first",
        )
        # Give every eligible existing draft one independent review before buying new investigations.
        for row in rows:
            if next_stage(row, cfg, spec) == "draft_review":
                advance(questions, row, "draft_review", registration)
        allocation = budget_routes.prepare(questions)
        print(
            json.dumps(
                {
                    "remaining_independent": len(allocation["unanswered"]),
                    "assigned_mixed": len(allocation["alternate_tasks"]),
                    "available_at_allocation_usd": allocation["budget"][
                        "available_usd"
                    ],
                }
            ),
            flush=True,
        )
        for stage in ("recovery", "title_lookup", "editorial"):
            for row in rows:
                if next_stage(row, cfg, spec) == stage:
                    advance(questions, row, stage, registration, allocation)
        outstanding = {
            r["task_id"]: stage
            for r in rows
            if (stage := next_stage(r, cfg, spec)) != "ready"
        }
        require(
            not outstanding,
            f"Questions still require evidence or a release decision: {outstanding}",
        )
        budget_routes.export(questions, destination, registration, allocation)
        verified = evidence.seal(questions, destination, receipts)
        spend.snapshot_charges()
    return {"verified": verified, "project_budget": guard.status()}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="operation", required=True)
    for name in ("status", "finish"):
        p = actions.add_parser(name)
        p.add_argument("questions", type=Path)
        if name == "finish":
            p.add_argument("destination", type=Path)
            p.add_argument("--evidence-directory", type=Path, default=evidence.FOLDER)
    args = parser.parse_args()
    if args.operation == "status":
        rows = runner.tasks(args.questions)
        cfg, _, _, _, spec = registrations()
        summary = {
            "questions": len(rows),
            "next_stage": inventory(rows, cfg, spec),
            "project_budget": budget.ProjectBudget().status(),
        }
        manifest = evidence.FOLDER / "manifest.json"
        if manifest.is_file():
            sealed = load(manifest)
            summary["sealed_candidate"] = {
                "reports": sealed["tasks"],
                "answers_sha256": sealed["files"]["answers.jsonl"],
            }
        print(json.dumps(summary, indent=2))
    else:
        print(
            json.dumps(
                finish(args.questions, args.destination, args.evidence_directory),
                indent=2,
            )
        )
