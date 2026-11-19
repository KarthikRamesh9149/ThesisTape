"""Replay archived development results and optionally match the private operator receipts."""

import argparse
import json
import tempfile
from collections import Counter
from datetime import datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from threading import Lock

from scripts.disney_hulu_case import CaseError, read_saved_file
from scripts.paired_packet import PacketCorpus
from scripts.paired_research import workflow
from scripts.research_team import check_configuration, reproduce
from scripts.run_checkpoint import load, load_receipt, receipt_path
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import BudgetExhausted, reserve
from scripts.team_meter import (
    check_decision_order,
    funded_limit,
    recorded_output,
    verify_ledger,
)

from .corpus import export_development, require
from .registration import ROOT, registered
from .runner import TERMINAL, admission, config, event_row

RESULTS = ROOT / "results"


def verify_private(directory, record, identities):
    require(load(directory / "run.json") == record, "Operator record differs from the archive")
    calls = record["calls"]
    ids_file = directory / "provider-ids.json"
    ids = load(ids_file) if ids_file.is_file() else {}
    expected = {receipt_path(directory, stage) for stage in calls}
    require(
        set(ids) == set(calls) and set((directory / "receipts").glob("*.json")) == expected,
        "Private receipt inventory differs from settled assignments",
    )
    portable = json_bytes(record)
    for stage, call in calls.items():
        receipt = load_receipt(receipt_path(directory, stage))
        require(
            receipt["checkpoint_id"] == record["checkpoint_id"]
            and receipt["stage"] == stage
            and receipt["ids"] == ids[stage]
            and fingerprint(json_bytes(receipt)) == call["receipt_sha256"]
            and receipt["request_sha256"] == call["request_sha256"]
            and receipt["provider"] == call["provider"]
            and receipt.get("output") == call.get("output")
            and fingerprint(json_bytes(ids[stage])) == call["provider"]["ids_sha256"],
            "Private receipt differs from its recorded request, output or usage",
        )
        for kind in ("request_id", "response_id"):
            value = ids[stage][kind]
            require(
                isinstance(value, str) and value and value not in identities[kind],
                "Missing or repeated provider identifier",
            )
            require(value.encode() not in portable, "Private provider identifier in portable record")
            identities[kind].add(value)


def replay_failure(record, corpus, arm, *, count_rejected=None):
    """Check every saved request up to the failure. This callback has no provider transport."""
    entries = {**record["calls"], **record.get("skipped_calls", {})}
    seen, lock = set(), Lock()

    class Boundary(Exception):
        pass

    def ask(stage, prompt, limit, *, keep_usd=None):
        with lock:
            if stage not in entries:
                if record["state"] == "budget_exhausted" and count_rejected is not None:
                    require(
                        keep_usd is None and seen == set(entries), "Unexpected concurrent budget refusal"
                    )
                    available = Decimal(record["budget_usd"]) - Decimal(record["estimated_model_cost_usd"])
                    tokens = count_rejected(stage, prompt)
                    model = prompt["model"]
                    granted = funded_limit(stage, model, tokens, available, limit)
                    reserve(tokens, available, max_output_tokens=granted, model=model)
                    raise AssertionError("The refused generation is affordable on replay")
                raise AssertionError(f"Unexpected request while replaying failure: {stage}")
            entry = entries[stage]
            require(stage not in seen, "Duplicate assignment during failure replay")
            if keep_usd is not None:
                check_decision_order(record, stage, seen)
            seen.add(stage)
            if "output" not in entry and "checked_ms" not in entry:
                # Check the original prompt fingerprint before stopping at an unusable response.
                recorded_output({**entry, "output": None}, stage, prompt, limit, keep_usd)
                raise Boundary("replayed to unusable provider response")
            return recorded_output(entry, stage, prompt, limit, keep_usd)

    try:
        workflow(corpus, arm, ask)
    except Boundary as exc:
        result = str(exc)
    except BudgetExhausted as exc:
        require(str(exc) == record["failure"], "Budget refusal differs from the recorded upper bound")
        result = "reproduced budget stop"
    except (ValueError, KeyError, ArithmeticError, OSError) as exc:
        observed = str(exc) if isinstance(exc, CaseError) else type(exc).__name__
        require(observed == record["failure"], "Terminal failure changed during offline replay")
        result = "reproduced terminal failure"
    else:
        raise ValueError("A registered failure unexpectedly returned a report")
    require(seen == set(entries), "Offline replay did not reach every saved assignment")
    return result


def checked_count(path, stage, prompt):
    receipt = load(path)
    require(
        receipt["stage"] == stage
        and receipt["model"] == prompt["model"]
        and receipt["prompt_sha256"] == fingerprint(json_bytes(prompt)),
        "Budget recount is for a different request",
    )
    return receipt["input_tokens"]


def audit_event(case_id, *, operator=None):
    manifest = registered()
    row = event_row(manifest, case_id)
    folder = RESULTS / case_id
    identities = {kind: set() for kind in ("request_id", "response_id")}
    checkpoints, receipt_hashes, arms = set(), set(), []
    previous_end = None
    with tempfile.TemporaryDirectory() as tmp:
        packet = Path(tmp) / "input"
        export_development(case_id, packet)
        for arm in row["arms"]:
            corpus = PacketCorpus(packet, row["packet_sha256"])
            cfg = config(corpus, admission(manifest, row, arm))
            location = folder / arm
            raw = read_saved_file(location / "run.json")
            record = json.loads(raw)
            check_configuration(record, cfg)
            require(
                record["budget_usd"] == cfg["per_case_arm_usd"] and record["state"] in TERMINAL,
                "Unregistered cap or incomplete assignment",
            )
            require(record["checkpoint_id"] not in checkpoints, "Shared checkpoint across configurations")
            checkpoints.add(record["checkpoint_id"])
            settled, pending = verify_ledger(record, partial=True)
            require(
                not pending and settled <= Decimal(cfg["per_case_arm_usd"]),
                "Unreconciled or excessive usage",
            )
            require(Decimal(record["paid_tool_charges_usd"]) == 0, "Unpriced paid tool in this study")
            start, end = (datetime.fromisoformat(record[key]) for key in ("created_at", "finished_at"))
            require(
                (previous_end is None or start >= previous_end)
                and end >= start
                and round((end - start).total_seconds() * 1000) == record["wall_ms"],
                "Invalid run timing or order",
            )
            previous_end = end
            if operator is not None:
                verify_private(operator / case_id / arm / "run", record, identities)
            memo = None
            if record["state"] in ("complete", "withheld"):
                memo = reproduce(record, cfg, partial(workflow, corpus, arm))
                replayed = "reproduced report" if memo is not None else "reproduced withholding decision"
            else:
                replayed = replay_failure(
                    record,
                    corpus,
                    arm,
                    count_rejected=partial(checked_count, location / "budget-stop.json"),
                )
            path = location / "memo.md"
            release_sha256 = fingerprint(memo) if memo is not None else None
            require(
                (read_saved_file(path) if path.exists() else None) == memo
                and record.get("memo_sha256") == release_sha256,
                "Archived memo differs from replay",
            )
            calls = list(record["calls"].values())
            models, tokens = Counter(), Counter()
            for call in calls:
                p = call["provider"]
                require(p["ids_sha256"] not in receipt_hashes, "A provider response was reused")
                receipt_hashes.add(p["ids_sha256"])
                models[p["model"]] += 1
                usage = p["usage"]
                tokens.update(
                    input=usage["input_tokens"],
                    output=usage["output_tokens"],
                    cached=usage["input_tokens_details"].get("cached_tokens", 0),
                    cache_write=usage["input_tokens_details"].get("cache_write_tokens", 0),
                )
            arms.append(
                {
                    "arm": arm,
                    "state": record["state"],
                    "release_sha256": release_sha256,
                    "direction": record.get("direction"),
                    "failure": record.get("failure"),
                    "offline_replay": replayed,
                    "record_sha256": fingerprint(raw),
                    "calls": len(calls),
                    "models": dict(models),
                    "tokens": dict(tokens),
                    "settled_usd": str(settled),
                    "outstanding_usd": record["unreconciled_reservation_usd"],
                    "cap_usd": record["budget_usd"],
                    "wall_ms": record["wall_ms"],
                }
            )
    require(len(receipt_hashes) == sum(a["calls"] for a in arms), "Provider receipt count mismatch")
    return {
        "case_id": case_id,
        "cutoff": corpus.case.cutoff,
        "packet_sha256": row["packet_sha256"],
        "run_manifest_sha256": fingerprint(json_bytes(manifest)),
        "arms": arms,
        "generation_calls": len(receipt_hashes),
        "released_reports": sum(a["release_sha256"] is not None for a in arms),
        "settled_usd": str(sum(Decimal(a["settled_usd"]) for a in arms)),
        "outstanding_usd": str(sum(Decimal(a["outstanding_usd"]) for a in arms)),
        "cap_usd": str(sum(Decimal(a["cap_usd"]) for a in arms)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument(
        "--operator-root", type=Path, help="Also check private operator receipts and identifiers"
    )
    args = parser.parse_args()
    result = audit_event(args.case_id, operator=args.operator_root)
    require(
        result == load(RESULTS / args.case_id / "results.json"),
        "Recomputed result differs from archived totals",
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
