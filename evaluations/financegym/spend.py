"""Reconcile measured project generation costs once per registered assignment and public pilot."""

import argparse
import json
import re
from decimal import Decimal
from pathlib import Path

from evaluations.paired.grading_recovery import verify_extended_ledger
from evaluations.paired.reconciliation import sidecar, verify_resolution
from evaluations.paired.registration import REPO, ROOT
from scripts.run_checkpoint import load, publish
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_meter import verify_ledger

SNAPSHOT = Path(__file__).with_name("cost-records.json")


def saved_charges(path):
    value = load(path)
    records = value["assignments"]
    if value.get("version") != 1 or value.get("sha256") != fingerprint(
        json_bytes(records)
    ):
        raise ValueError("Project cost snapshot changed")
    for key, charge in records.items():
        if not re.fullmatch(r"[0-9a-f]{64}", key) or not charge.get("group"):
            raise ValueError("Invalid project cost identity")
        for field in ("paid_usd", "reserved_usd"):
            amount = Decimal(charge[field])
            if not amount.is_finite() or amount < 0:
                raise ValueError("Invalid historical project charge")
    return records


def match_charge(archived, token, group, paid):
    prior = archived.pop(token, None)
    if prior and (prior["group"] != group or Decimal(prior["paid_usd"]) > paid):
        raise ValueError("A live ledger lost an already recorded project charge")
    return prior


def report(*, capture=None, snapshot=SNAPSHOT):
    money, reservations, counts = {}, {}, {}
    archived = saved_charges(snapshot) if snapshot is not None else {}

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
    add(
        "Public TSMC desk trial",
        load(REPO / "evaluations/open_research/tsmc/browser.json")["spent"],
        count=1,
    )
    add(
        "Public TSMC desk trial with reopened originals",
        load(REPO / "evaluations/open_research/tsmc/original-passages/browser.json")[
            "spent"
        ],
        count=1,
    )
    seen = set()

    def include(group, file, resolution=None, *, ledger=verify_ledger):
        rec = load(file)
        if rec["checkpoint_id"] in seen:
            raise ValueError("Run identity appears twice in the project account")
        seen.add(rec["checkpoint_id"])
        spent, held = ledger(rec, partial=True)
        if resolution and resolution.is_file():
            resolved = verify_resolution(rec, load(resolution))
            spent = Decimal(resolved["settled_total_usd"])
            held = {"settled": Decimal(resolved["outstanding_usd"])}
        reserved = sum(held.values(), Decimal(0))
        key = fingerprint(rec["checkpoint_id"].encode())
        prior = match_charge(archived, key, group, spent)
        add(group, spent, reserved, 1)
        # A snapshot carries incurred development expenses and final benchmark
        # assignments. Active benchmark calls remain protected by the live gate.
        running_benchmark = (rec.get("assignment") or {}).get("task_id") and rec[
            "state"
        ] in {"running", "paused", "queued"}
        if capture is not None and not running_benchmark:
            capture[key] = {
                "group": group,
                "paid_usd": str(spent),
                "reserved_usd": str(reserved),
            }
        elif capture is not None and prior:
            capture[key] = prior

    boj = REPO / "runs/open-research/boj-march2024/research/run.json"
    if boj.is_file():
        include("Public Bank of Japan source trial", boj)
    for p in (REPO / "runs/public-fomc-march2023").glob("*/research/run.json"):
        include("Public March 2023 Federal Reserve validation", p)
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
    for p in (REPO / "runs/study-grading-v2").glob("*/*/*/grade-overflow/run.json"):
        include(
            "Continuation of truncated masked assessments",
            p,
            ledger=verify_extended_ledger,
        )
    for p in (REPO / "runs/financegym").glob("*/research/run.json"):
        include("Official research", p)
    for p in (REPO / "runs/financegym-recovery").glob("*/research/run.json"):
        include("Independent recovery of unanswered official tasks", p)
    for p in (REPO / "runs/financegym-editorial").glob("*/research/run.json"):
        group = (
            "Independent review of otherwise unanswered primary drafts"
            if p.parents[1].name.startswith("primary-draft-")
            else "Funded editing of otherwise unanswered official tasks"
        )
        include(group, p)
    for p in (REPO / "runs/financebench-research").glob("*/*/run.json"):
        include("FinanceBench independent research", p)
    for p in (REPO / "runs/financebench-grading").glob("*/grade/run.json"):
        include("FinanceBench masked grading", p)
    for key, charge in archived.items():
        add(charge["group"], charge["paid_usd"], charge["reserved_usd"], 1)
        if capture is not None:
            capture[key] = charge
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


def snapshot_charges(path=SNAPSHOT):
    records = {}
    report(capture=records, snapshot=path if path.exists() else None)
    blob = {
        "version": 1,
        "sha256": fingerprint(json_bytes(records)),
        "assignments": records,
    }
    publish(path, json_bytes(blob))
    return {"assignments": len(records), "sha256": blob["sha256"]}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--snapshot",
        action="store_true",
        help="Save portable cumulative charges from the verified provider ledgers",
    )
    args = parser.parse_args()
    print(json.dumps(snapshot_charges() if args.snapshot else report(), indent=2))
