"""Reconcile measured project generation costs once per registered assignment and public pilot."""

import json
from decimal import Decimal

from evaluations.paired.reconciliation import sidecar, verify_resolution
from evaluations.paired.registration import REPO, ROOT
from scripts.run_checkpoint import load
from scripts.team_meter import verify_ledger


def report():
    money, reservations, counts = {}, {}, {}

    def add(group, paid, held=Decimal(0), count=0):
        money[group] = money.get(group, Decimal(0)) + Decimal(paid)
        reservations[group] = reservations.get(group, Decimal(0)) + Decimal(held)
        counts[group] = counts.get(group, 0) + count

    # The protocol records the cumulative Step 1–5 estimate, including the earlier acceptance trials.
    add(
        "Through Step 5",
        load(ROOT / "protocol.json")["budget_usd"]["prior_project_estimate"],
    )
    add(
        "First Nintendo pilot",
        load(ROOT / "results/nintendo-fy2021-guidance/results.json")["settled_usd"],
        count=6,
    )
    boeing = load(REPO / "evaluations/open_research/boeing/recheck.json")
    add(
        "Public Boeing pilot and recheck",
        Decimal(boeing["original_charge_usd"]) + Decimal(boeing["incremental_usd"]),
        count=2,
    )
    add(
        "Public ECB desk trial",
        load(REPO / "evaluations/open_research/ecb/browser.json")["spent"],
        count=1,
    )
    seen = set()

    def include(group, file, resolution=None):
        rec = load(file)
        if rec["checkpoint_id"] in seen:
            raise ValueError("Run identity appears twice in the project account")
        seen.add(rec["checkpoint_id"])
        spent, held = verify_ledger(rec, partial=True)
        if resolution and resolution.is_file():
            resolved = verify_resolution(rec, load(resolution))
            spent = Decimal(resolved["settled_total_usd"])
            held = {"settled": Decimal(resolved["outstanding_usd"])}
        add(group, spent, sum(held.values(), Decimal(0)), 1)

    boj = REPO / "runs/open-research/boj-march2024/research/run.json"
    if boj.is_file():
        include("Public Bank of Japan source trial", boj)
    for p in (REPO / "runs").glob("quantitative-development*/*/run/run.json"):
        include("Engineering trials for financial calculations", p)
    for p in (REPO / "runs").glob("quantitative-grading*/*/grade/run.json"):
        include("Masked engineering-trial graders", p)
    for split in ("development", "blind"):
        for p in (REPO / "runs" / ("paired-" + split + "-v2")).glob("*/*/run/run.json"):
            include(
                "Incumbent " + split,
                p,
                sidecar(split, p.parents[2].name, p.parents[1].name),
            )
        for p in (REPO / "runs" / ("analyst-product-" + split)).glob(
            "*/*/run/run.json"
        ):
            include("Analyst product " + split, p)
    for p in (REPO / "runs/study-grading-v2").glob("*/*/*/grade/run.json"):
        include("Masked study graders", p)
    for p in (REPO / "runs/financegym").glob("*/research/run.json"):
        include("Official research", p)
    return {
        "settled_estimate_usd": str(sum(money.values())),
        "outstanding_usd": str(sum(reservations.values())),
        "groups": {
            k: {
                "paid_usd": str(v),
                "reserved_usd": str(reservations[k]),
                "assignments": counts[k] if k != "Through Step 5" else None,
            }
            for k, v in money.items()
        },
        "basis": "Recorded Standard-rate usage for project assignments and reconciled lost responses. Independent of invoice credits; excludes local hardware and network costs.",
    }


if __name__ == "__main__":
    print(json.dumps(report(), indent=2))
