"""Continue the frozen production schedule around an isolated missing provider response, without redispatch."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from functools import partial
from threading import Lock
from urllib.parse import urlsplit

from scripts.disney_hulu_case import CaseError
from scripts.packet_archive import PacketArchive, SavedSearches
from scripts.paired_packet import PacketCorpus
from scripts.research_desk_agent import audit, research
from scripts.run_checkpoint import load, load_receipt, own_run, receipt_path
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_meter import recorded_output, verify_ledger

from . import corpus, production
from .confirmation import export_blind, ordering, registry, write_once, zipped
from .event_audit import verify_private
from .product_audit import replay_failure
from .registration import REPO, ROOT


def ticket(reg, split, row, arm):
    return {
        "study": fingerprint(json_bytes(reg)),
        "split": split,
        "case_id": row["case_id"],
        "packet_sha256": row["packet_sha256"],
        "arm": arm,
    }


def continue_split(split, *, allow_new_team=True):
    reg = production.locked()
    root = REPO / "runs" / ("analyst-product-" + split)
    root.mkdir(exist_ok=True, parents=True)
    with own_run(root):
        for row in production.schedule(split):
            for arm in row["arms"]:
                cfg = ticket(reg, split, row, arm)
                folder = root / row["case_id"] / arm
                packet = folder / "input"
                folder.mkdir(exist_ok=True, parents=True)
                if not packet.exists():
                    export = (
                        corpus.export_development
                        if split == "development"
                        else partial(export_blind, revision=production.FOLDER)
                    )
                    export(row["case_id"], packet)
                path = folder / "run/run.json"
                if path.is_file():
                    r = load(path)
                    corpus.require(r["assignment"] == cfg, "Conflicting frozen intake")
                    _, unconfirmed = verify_ledger(r, partial=True)
                    if r["state"] == "needs_reconciliation" and unconfirmed:
                        print(
                            json.dumps(
                                {
                                    "case_id": row["case_id"],
                                    "arm": arm,
                                    "state": "original attempt retained",
                                    "held_usd": r["unreconciled_reservation_usd"],
                                }
                            ),
                            flush=True,
                        )
                        continue
                    if r["state"] in production.FINISHED and not unconfirmed:
                        continue
                if not allow_new_team and not arm.startswith("single_"):
                    return
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        "from evaluations.paired.production import worker;worker()",
                    ],
                    cwd=packet,
                    env={**os.environ, "PYTHONPATH": str(REPO)},
                    input=json.dumps(cfg),
                    text=True,
                    capture_output=True,
                    check=False,
                )
                if result.returncode:
                    raise RuntimeError(
                        result.stderr.strip().splitlines()[-1]
                        if result.stderr
                        else "The research worker stopped"
                    )
                print(result.stdout.strip(), flush=True)
                if json.loads(result.stdout)["state"] == "needs_reconciliation":
                    # Keep the task and its reservation. Check the provider issue before admitting another new case.
                    raise CaseError(
                        "An additional dispatch was interrupted; its original record is preserved"
                    )


def inspect_interruption(folder, rec, gateway, identities):
    def execute(ask):
        return research(
            rec["question"],
            rec["cutoff"],
            SavedSearches(gateway, folder / "searches", offline=True),
            ask,
            profile=rec["research_profile"],
        )

    return interrupted_receipts(folder, rec, identities, execute)


def interrupted_receipts(folder, rec, identities, execute):
    spent, pending = verify_ledger(rec, partial=True)
    corpus.require(
        rec["state"] == "needs_reconciliation" and pending and rec.get("failure"),
        "Expected a recorded, unresolved response",
    )
    ids = (
        load(folder / "provider-ids.json")
        if (folder / "provider-ids.json").is_file()
        else {}
    )
    confirmed = set(rec["calls"]) - set(pending)
    corpus.require(
        set(ids) == confirmed
        and {receipt_path(folder, s) for s in confirmed}
        == set((folder / "receipts").glob("*.json")),
        "Incomplete or unaccounted operator receipts",
    )
    for s in confirmed:
        c = rec["calls"][s]
        receipt = load_receipt(receipt_path(folder, s))
        corpus.require(
            receipt["stage"] == s
            and receipt["checkpoint_id"] == rec["checkpoint_id"]
            and fingerprint(json_bytes(receipt)) == c["receipt_sha256"]
            and receipt["request_sha256"] == c["request_sha256"]
            and receipt["provider"] == c["provider"]
            and receipt["ids"] == ids[s]
            and receipt.get("output") == c.get("output")
            and fingerprint(json_bytes(ids[s])) == c["provider"]["ids_sha256"],
            "Settled provider receipt changed",
        )
        for k in identities:
            value = ids[s][k]
            corpus.require(
                isinstance(value, str) and value and value not in identities[k],
                "Repeated provider receipt",
            )
            identities[k].add(value)
    seen = set()
    lock = Lock()

    class Missing(Exception):
        pass

    def ask(stage, prompt, limit, *, keep_usd=None):
        with lock:
            corpus.require(
                stage not in seen
                and (stage in rec["calls"] or stage in rec.get("skipped_calls", {})),
                "Unexpected replay dispatch",
            )
            seen.add(stage)
            if stage in pending:
                recorded_output(
                    {**rec["calls"][stage], "output": None},
                    stage,
                    prompt,
                    limit,
                    keep_usd,
                )
                raise Missing
            return recorded_output(
                (rec["calls"] | rec.get("skipped_calls", {}))[stage],
                stage,
                prompt,
                limit,
                keep_usd,
            )

    try:
        execute(ask)
    except Missing:
        pass
    else:
        raise ValueError("Interrupted run unexpectedly released a report")
    corpus.require(
        seen == set(rec["calls"]) | set(rec.get("skipped_calls", {}))
        and not (folder / "memo.md").exists()
        and not (folder / "result.json").exists(),
        "Interrupted run has unmatched work or a released artifact",
    )
    return spent, sum(pending.values(), Decimal(0))


def lock_split(split):
    reg = production.locked()
    root = REPO / "runs" / ("analyst-product-" + split)
    rows = []
    entries = {}
    ids = {k: set() for k in ("request_id", "response_id")}
    caseinfo = {c["case_id"]: c for c in registry(split, production.FOLDER)}
    seed = load(ROOT / "protocol.json")["order_seed"]
    prev = None
    for case in production.schedule(split):
        cid = case["case_id"]
        for arm in case["arms"]:
            folder = root / cid / arm
            run = folder / "run"
            packet = PacketCorpus(folder / "input", case["packet_sha256"])
            gateway = PacketArchive(packet)
            rec = load(run / "run.json")
            corpus.require(
                rec["assignment"] == ticket(reg, split, case, arm)
                and rec["budget_usd"] == production.CAP,
                "Unregistered production assignment",
            )
            result = {}
            memo = None
            held = Decimal(0)
            state = rec["state"]
            if state == "needs_reconciliation":
                spent, held = inspect_interruption(run, rec, gateway, ids)
                state = "provider_interrupted"
            else:
                spent, pending = verify_ledger(rec, partial=True)
                corpus.require(
                    state in production.FINISHED and not pending,
                    "Incomplete assignment",
                )
                verify_private(run, rec, ids)
                if state == "failed":
                    replay_failure(run, rec, gateway)
                else:
                    result = audit(run, gateway)
                    if result["memo_sha256"]:
                        memo = (run / "memo.md").read_bytes()
            corpus.require(
                spent + held <= Decimal(production.CAP),
                "Exposure exceeds the registered spending cap",
            )
            alias = ordering(seed, split, cid, "product-mask/" + arm)[:16]
            raw = (run / "run.json").read_bytes()
            entries[alias + "/run.json"] = raw
            if result:
                entries[alias + "/result.json"] = json_bytes(result)
            if memo:
                entries[alias + "/memo.md"] = memo
            for directory in ("searches", "counts"):
                for p in sorted((run / directory).glob("*.json")):
                    entries[alias + "/" + directory + "/" + p.name] = p.read_bytes()
            start = datetime.fromisoformat(rec["created_at"])
            end = datetime.fromisoformat(
                result.get("finished_at") or rec["finished_at"]
            )
            corpus.require(
                end >= start and (prev is None or start >= prev),
                "Wrong case ordering or timing",
            )
            prev = end
            rows.append(
                {
                    "case_id": cid,
                    "arm": arm,
                    "alias": alias,
                    "stratum": caseinfo[cid]["stratum"],
                    "publishers": sorted(
                        {urlsplit(s["url"]).hostname for s in caseinfo[cid]["sources"]}
                    ),
                    "forward": bool(packet.case.forecast_rule),
                    "direction": result.get("direction"),
                    "state": state,
                    "qualified": state == "qualified",
                    "cost_usd": str(spent + held),
                    "confirmed_cost_usd": str(spent),
                    "unreconciled_usd": str(held),
                    "elapsed_ms": round((end - start).total_seconds() * 1000),
                    "calls": len(rec["calls"]),
                    "cutoff_violations": 0,
                    "record_sha256": fingerprint(raw),
                    "memo_sha256": fingerprint(memo) if memo else None,
                }
            )
    raw = zipped(entries)
    base = production.FOLDER / split
    write_once(base / "reports.zip", raw)
    write_once(
        base / "reports.json",
        json_bytes(
            {
                "study": reg["version"],
                "split": split,
                "records_sha256": fingerprint(raw),
                "rows": rows,
                "cost_accounting": "cost_usd is the upper bound when a generation has no confirmed receipt. The charge remains reserved. Interrupted reports score zero.",
            }
        ),
    )
    print(
        f"Locked {len(rows)} assignments, {sum(bool(r['memo_sha256']) for r in rows)} reports, {sum(Decimal(r['unreconciled_usd']) for r in rows)} USD pending",
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["continue", "lock"])
    parser.add_argument("split", choices=["development", "blind"])
    parser.add_argument("--hold-new-teams", action="store_true")
    args = parser.parse_args()
    if args.action == "continue":
        continue_split(args.split, allow_new_team=not args.hold_new_teams)
    else:
        lock_split(args.split)
