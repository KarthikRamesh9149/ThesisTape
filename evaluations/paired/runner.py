"""Run or replay one registered development event with private per-arm checkpoints."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.disney_hulu_case import CaseError, read_saved_file
from scripts.paired_packet import PacketCorpus
from scripts.paired_research import ARMS, workflow
from scripts.research_team import check_configuration, finish, new_record, reproduce
from scripts.run_checkpoint import (
    PauseRequested,
    load,
    own_run,
    pause_on_signal,
    publish,
)
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import BudgetExhausted
from scripts.team_meter import Meter, verify_ledger

from .corpus import export_development
from .registration import REPO, registered

LIVE_ROOT = REPO / "runs/paired-development-v1"
TERMINAL = {"complete", "withheld", "failed", "budget_exhausted"}


def admission(manifest, row, arm):
    if arm not in row["arms"]:
        raise CaseError("This arm has no independent assignment on this event")
    return {
        "case_id": row["case_id"],
        "packet_sha256": row["packet_sha256"],
        "arm": arm,
        "run_manifest_sha256": fingerprint(json_bytes(manifest)),
        "per_case_arm_usd": manifest["budgets_usd"]["per_case_arm"],
    }


def config(corpus, ticket):
    arm = ARMS[ticket["arm"]]
    if corpus.case.id != ticket["case_id"] or corpus.digest != ticket["packet_sha256"]:
        raise CaseError("Assignment does not match its packet")
    return {
        **ticket,
        **corpus.case.intake(),
        "route": ticket["arm"],
        "bindings": arm.bindings(),
        "schema_version": 1,
        "corpus_sha256": corpus.digest,
        "paid_tool_charges_usd": "0",
        **({} if arm.single else {"review_version": "paired-source-review-v1"}),
    }


def replay_assignment(directory, corpus, cfg, record):
    check_configuration(record, cfg)
    if record["budget_usd"] != cfg["per_case_arm_usd"]:
        raise CaseError("Allowance differs from the registered cap")
    if record["state"] not in ("complete", "withheld"):
        verify_ledger(record, partial=True)
        return None
    # A study run retains its private provider receipts and the matching local IDs.
    Meter(directory, record, None, continuing=True).restore()
    memo = reproduce(record, cfg, lambda ask: workflow(corpus, cfg["arm"], ask))
    artifact = directory / "memo.md"
    if artifact.exists() or artifact.is_symlink():
        if read_saved_file(artifact) != memo:
            raise CaseError("Stored report differs from replay")
    elif memo is not None:
        publish(artifact, memo)
    return memo


def record_finish_time(record, when):
    created = datetime.fromisoformat(record["created_at"])
    record["finished_at"] = when.isoformat()
    record["wall_ms"] = max(0, round((when - created).total_seconds() * 1000))
    record["rate_limit_backoff_ms"] = 0  # The registered transport performs no automatic retries.


def work(corpus, ticket, directory, provider=None, *, pause=None):
    """One owner per assignment. Settled calls replay and unknown dispatches retain their money."""
    cfg = config(corpus, ticket)
    path = directory / "run.json"
    budget = Decimal(ticket["per_case_arm_usd"])
    if not path.exists():
        # Publish a complete empty ledger before any provider can be constructed or called.
        with tempfile.TemporaryDirectory(dir=directory.parent) as tmp:
            staged = Path(tmp) / directory.name
            record = new_record(cfg, staged, budget)
            Meter(staged, record, provider)
            staged.rename(directory)
    record = load(path)
    check_configuration(record, cfg)
    if Decimal(record["budget_usd"]) != budget:
        raise CaseError("This run's allowance has changed")
    if record["state"] in TERMINAL and not Decimal(record["unreconciled_reservation_usd"]):
        terminal_write = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        replay_assignment(directory, corpus, cfg, record)
        if "finished_at" not in record:
            # A hard stop can separate finish()'s durable terminal record from the timing write.
            record_finish_time(record, terminal_write)
            publish(path, json_bytes(record))
        return record
    meter = Meter(directory, record, provider, continuing=True, pause=pause)
    try:
        meter.restore()
        record.pop("failure", None)
        record["state"] = "running"
        finish(cfg, meter, lambda ask: workflow(corpus, ticket["arm"], ask), "memo.md")
    except PauseRequested:
        raise
    except (ValueError, KeyError, ArithmeticError, OSError) as exc:
        if meter.pending:
            record["state"] = "needs_reconciliation"
        else:
            record["state"] = "budget_exhausted" if isinstance(exc, BudgetExhausted) else "failed"
        record["failure"] = str(exc) if isinstance(exc, CaseError) else type(exc).__name__
    if record["state"] in TERMINAL:
        record_finish_time(record, datetime.now(UTC))
    meter.save()
    return record


def child():
    """Only the ticket and this event's packet are passed into this fresh interpreter."""
    message = json.load(sys.stdin)
    corpus = PacketCorpus(Path.cwd(), message["ticket"]["packet_sha256"])
    ticket = message["ticket"]
    if message["mode"] == "probe":
        from .readiness import probe

        result = probe(corpus, ticket, message["counts"])
    elif message["mode"] == "replay":
        directory = Path.cwd().parent / "run"
        record = load(directory / "run.json")
        replay_assignment(directory, corpus, config(corpus, ticket), record)
        result = summary(record)
    elif message["mode"] == "run":
        with pause_on_signal() as pause:
            result = summary(work(corpus, ticket, Path.cwd().parent / "run", pause=pause))
    else:
        raise CaseError("Unknown worker operation")
    print(json.dumps(result, sort_keys=True))


def summary(record):
    return {
        k: record[k]
        for k in (
            "case_id",
            "arm",
            "state",
            "budget_usd",
            "estimated_model_cost_usd",
            "unreconciled_reservation_usd",
            "elapsed_ms",
            "failure",
            "memo_sha256",
            "wall_ms",
        )
        if k in record
    }


def isolated(ticket, root, mode, *, counts=None):
    workspace = root / ticket["case_id"] / ticket["arm"]
    workspace.mkdir(parents=True, exist_ok=True)
    with own_run(workspace):
        packet = workspace / "input"
        if not packet.exists():
            export_development(ticket["case_id"], packet)
        message = {"mode": mode, "ticket": ticket, "counts": counts}
        # Models get typed requests only; their only evidence source is this packet.
        result = subprocess.run(
            [sys.executable, "-B", "-c", "from evaluations.paired.runner import child; child()"],
            cwd=packet,
            env={**os.environ, "PYTHONPATH": str(REPO)},
            input=json.dumps(message),
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        return json.loads(result.stdout)


def event_row(manifest, cid):
    for row in manifest["schedule"]:
        if row["case_id"] == cid:
            return row
    raise CaseError("Choose a registered development event")


def run_event(manifest, cid, *, replay=False):
    row = event_row(manifest, cid)
    LIVE_ROOT.mkdir(parents=True, exist_ok=True)
    with own_run(LIVE_ROOT):
        if not replay:
            for prior in manifest["schedule"][: manifest["schedule"].index(row)]:
                for arm in prior["arms"]:
                    path = LIVE_ROOT / prior["case_id"] / arm / "run/run.json"
                    record = load(path) if path.is_file() else {}
                    if record.get("state") not in TERMINAL or Decimal(record["unreconciled_reservation_usd"]):
                        raise CaseError(f"Complete the earlier registered event {prior['case_id']} first")
        results = {}
        for arm in row["arms"]:
            result = isolated(admission(manifest, row, arm), LIVE_ROOT, "replay" if replay else "run")
            results[arm] = result
            if result["state"] not in TERMINAL or Decimal(result["unreconciled_reservation_usd"]):
                break
        if "mixed_no_forecast" not in row["arms"] and "mixed" in results:
            results["mixed_no_forecast"] = {
                "reused_from": "mixed",
                "memo_sha256": results["mixed"].get("memo_sha256"),
                "extra_dispatches": 0,
                "extra_cost_usd": "0",
            }
        return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest="command", required=True)
    probe = subs.add_parser("preflight", help="Verify the first event with saved or live input-token counts")
    probe.add_argument(
        "--live-counts", action="store_true", help="Call the input-token endpoint; no generations"
    )
    for operation in ("run-event", "replay-event"):
        sub = subs.add_parser(
            operation, help="One paid event" if operation == "run-event" else "Verify saved results offline"
        )
        sub.add_argument("case_id")
    args = parser.parse_args(argv)
    manifest = registered(for_generation=args.command == "run-event")
    if args.command == "preflight":
        from .readiness import preflight

        result = preflight(manifest, live=args.live_counts)
    else:
        result = run_event(manifest, args.case_id, replay=args.command == "replay-event")
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.command != "preflight" and any(
        r.get("state") not in (None, "complete", "withheld") for r in result.values()
    ):
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
