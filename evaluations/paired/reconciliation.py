"""Close a lost response from a uniquely matched provider usage export without issuing it twice."""

import argparse
import csv
import io
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from scripts.run_checkpoint import load, load_receipt, own_run, publish, receipt_path
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import usage_cost
from scripts.team_meter import verify_ledger

from .corpus import export_development, require
from .registration import REPO
from .runner import TERMINAL, admission
from .study import REVISION, assignment, locked

FIELDS = (
    "num_model_requests",
    "input_tokens",
    "output_tokens",
    "input_cached_tokens",
    "input_cache_write_30m_tokens",
    "input_cache_write_12h_tokens",
)


def bucket(row):
    values = {key: Decimal(row[key]) for key in FIELDS}
    require(
        all(n.is_finite() and n >= 0 and n == int(n) for n in values.values()),
        "Invalid provider counts",
    )
    return {
        "start_time": int(row["start_time"]),
        "end_time": int(row["end_time"]),
        "model": row["model"],
        "batch": row["batch"],
        "service_tier": row["service_tier"],
        "account_scope_sha256": fingerprint(
            json_bytes([row["project_id"], row["api_key_id"]])
        ),
        **{key: int(n) for key, n in values.items()},
    }


def cost(row):
    details = {
        "cached_tokens": row["input_cached_tokens"],
        "cache_write_tokens": row["input_cache_write_30m_tokens"],
        "cache_write_30m_tokens": row["input_cache_write_30m_tokens"],
        "cache_write_12h_tokens": row["input_cache_write_12h_tokens"],
    }
    usage = {
        "input_tokens": row["input_tokens"],
        "output_tokens": row["output_tokens"],
        "total_tokens": row["input_tokens"] + row["output_tokens"],
        "input_tokens_details": details,
    }
    return usage_cost(
        {"model": row["model"], "service_tier": row["service_tier"], "usage": usage},
        expected_model=row["model"],
    )


def reconcile_record(record, anchor, target):
    spent, pending = verify_ledger(record, partial=True)
    require(
        len(pending) == 1 and record["state"] == "needs_reconciliation",
        "Expected precisely one unresolved dispatch",
    )
    stage, allowance = next(iter(pending.items()))
    lost = record["calls"][stage]
    require(
        "provider" not in lost and "output" not in lost,
        "A full local response already exists",
    )
    start = datetime.fromisoformat(record["created_at"])
    dispatched = (start + timedelta(milliseconds=lost["dispatched_ms"])).timestamp()
    end = (start + timedelta(milliseconds=record["elapsed_ms"])).timestamp()
    require(
        anchor["start_time"] == target["start_time"] - 60
        and all(
            r["end_time"] == r["start_time"] + 60
            and r["model"] == lost["model"]
            and r["service_tier"] == "default"
            and r["batch"] == "False"
            for r in (anchor, target)
        )
        and anchor["account_scope_sha256"] == target["account_scope_sha256"]
        and target["start_time"] <= end
        and target["end_time"] > dispatched,
        "Usage does not match the call's minute, account scope, model or tier",
    )
    preceding = []
    for name, call in record["calls"].items():
        if name != stage and call["model"] == lost["model"]:
            first = (start + timedelta(milliseconds=call["dispatched_ms"])).timestamp()
            last = (start + timedelta(milliseconds=call["confirmed_ms"])).timestamp()
            if first >= anchor["start_time"] and last < anchor["end_time"]:
                preceding.append(call["provider"]["usage"])
            require(
                last < target["start_time"] or first >= target["end_time"],
                "Another known call overlaps the target minute",
            )
    require(
        preceding
        and anchor["num_model_requests"] == len(preceding)
        and all(
            anchor[key] == sum(u[key] for u in preceding)
            for key in ("input_tokens", "output_tokens")
        )
        and anchor["input_cached_tokens"]
        == sum(u["input_tokens_details"].get("cached_tokens", 0) for u in preceding)
        and anchor["input_cache_write_30m_tokens"]
        + anchor["input_cache_write_12h_tokens"]
        == sum(
            u["input_tokens_details"].get("cache_write_tokens", 0) for u in preceding
        ),
        "Adjacent minute does not match the earlier authenticated receipts exactly",
    )
    require(
        target["num_model_requests"] == 1
        and target["input_tokens"] == lost["input_tokens_preflight"]
        and 0 <= target["output_tokens"] <= lost["max_output_tokens"],
        "Cannot uniquely attribute the provider usage",
    )
    charge = cost(target)
    require(charge <= allowance, "Provider charge exceeds its reservation")
    return {
        "stage": stage,
        "request_sha256": lost["request_sha256"],
        "held_usd": str(allowance),
        "usage_usd": str(charge),
        "settled_total_usd": str(spent + charge),
        "outstanding_usd": "0",
        "research_state": "failed",
        "reason": "Provider billed the call; the research process received no recoverable output.",
    }


def verify_resolution(record, resolution):
    require(
        resolution["checkpoint_id"] == record["checkpoint_id"]
        and resolution["original_record_sha256"] == fingerprint(json_bytes(record))
        and resolution["settlement"]
        == reconcile_record(
            record, resolution["previous_minute"], resolution["target_minute"]
        ),
        "Billing resolution differs from the recorded assignment",
    )
    finished = datetime.fromisoformat(resolution["resolved_at"])
    require(
        finished
        >= datetime.fromisoformat(record["created_at"])
        + timedelta(milliseconds=record["elapsed_ms"]),
        "Resolution predates the failed run",
    )
    return resolution["settlement"]


def replay_lost(record, resolution, corpus, arm):
    """Verify the missing request fingerprint before stopping. Supply no substitute response."""
    from scripts.paired_v2 import workflow
    from scripts.team_meter import recorded_output

    stage = verify_resolution(record, resolution)["stage"]
    calls = {**record["calls"], **record.get("skipped_calls", {})}
    seen = set()

    class Lost(Exception):
        pass

    def dispatch(name, prompt, limit, *, keep_usd=None):
        require(
            name in calls and name not in seen,
            "Unexpected request before the missing output",
        )
        seen.add(name)
        if name == stage:
            # recorded_output verifies the request before returning. Discard the placeholder here.
            recorded_output(
                {**calls[name], "output": None}, name, prompt, limit, keep_usd
            )
            raise Lost
        return recorded_output(calls[name], name, prompt, limit, keep_usd)

    try:
        workflow(corpus, arm, dispatch)
    except Lost:
        require(seen == set(calls), "The missing output was not the terminal dispatch")
        return
    raise ValueError("Replayed a missing provider output")


def create(csv_path, run, output):
    record = load(run / "run.json")
    _, pending = verify_ledger(record, partial=True)
    stage = next(iter(pending))
    require(not receipt_path(run, stage).exists(), "Restore the existing receipt first")
    for name, call in record["calls"].items():
        if name != stage:
            receipt = load_receipt(receipt_path(run, name))
            require(
                fingerprint(json_bytes(receipt)) == call["receipt_sha256"]
                and receipt["request_sha256"] == call["request_sha256"],
                "Known call receipt changed",
            )
    raw = csv_path.read_bytes()
    rows = [
        bucket(r)
        for r in csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
        if r["num_model_requests"] and r["model"] == record["calls"][stage]["model"]
    ]
    matched = []
    for target in rows:
        for previous in rows:
            if (
                previous["end_time"] != target["start_time"]
                or previous["account_scope_sha256"] != target["account_scope_sha256"]
            ):
                continue
            try:
                matched.append(
                    (previous, target, reconcile_record(record, previous, target))
                )
            except ValueError:
                continue
    require(len(matched) == 1, "Export has no unique match")
    previous, target, settlement = matched[0]
    resolution = {
        "version": 1,
        "source": "OpenAI project Analytics CSV, Responses and Chat Completions, minute buckets",
        "csv_sha256": fingerprint(raw),
        "original_record_sha256": fingerprint(json_bytes(record)),
        "checkpoint_id": record["checkpoint_id"],
        "previous_minute": previous,
        "target_minute": target,
        "settlement": settlement,
        "resolved_at": datetime.now(UTC).isoformat(),
    }
    if output.exists():
        saved = load(output)
        require(
            saved["csv_sha256"] == fingerprint(raw), "Conflicting settlement export"
        )
        verify_resolution(record, saved)
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        publish(output, json_bytes(resolution))
    return settlement


def sidecar(split, cid, arm):
    return REVISION / "settlements" / split / cid / (arm + ".json")


def continue_study():
    manifest = locked()
    root = REPO / "runs/paired-development-v2"
    root.mkdir(exist_ok=True, parents=True)
    with own_run(root):
        for row in manifest["schedule"]:
            for arm in row["arms"]:
                cid = row["case_id"]
                workspace = root / cid / arm
                path = sidecar("development", cid, arm)
                if path.is_file():
                    from .confirmation import committed

                    resolution = json.loads(committed(path))
                    print(
                        json.dumps(
                            {
                                "case_id": cid,
                                "arm": arm,
                                **verify_resolution(
                                    load(workspace / "run/run.json"), resolution
                                ),
                            }
                        ),
                        flush=True,
                    )
                    continue
                record = assignment(
                    admission(manifest, row, arm), workspace, export_development, False
                )
                print(json.dumps(record), flush=True)
                require(
                    record["state"] in TERMINAL
                    and Decimal(record["unreconciled_reservation_usd"]) == 0,
                    "An unmatched dispatch must be reconciled before continuing",
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    settle = subs.add_parser("settle")
    settle.add_argument("cid")
    settle.add_argument("arm")
    settle.add_argument("csv", type=Path)
    subs.add_parser("continue")
    args = parser.parse_args()
    if args.command == "continue":
        continue_study()
        return
    manifest = locked()
    row = next(r for r in manifest["schedule"] if r["case_id"] == args.cid)
    admission(manifest, row, args.arm)
    root = REPO / "runs/paired-development-v2"
    with own_run(root):
        print(
            json.dumps(
                create(
                    args.csv,
                    root / args.cid / args.arm / "run",
                    sidecar("development", args.cid, args.arm),
                ),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
