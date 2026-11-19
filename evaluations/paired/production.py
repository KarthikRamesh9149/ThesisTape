"""Compare the analyst product and matched single-model controls on frozen, isolated publisher packets."""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from decimal import Decimal
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from scripts import research_desk_agent as product
from scripts.packet_archive import PacketArchive
from scripts.paired_packet import PacketCorpus, packet_digest
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key
from scripts.team_meter import verify_ledger

from . import analysis, corpus, grading
from .confirmation import (
    committed,
    export_blind,
    ordering,
    registry,
    write_once,
    zipped,
)
from .event_audit import verify_private
from .product_audit import CountJournal, replay_failure
from .registration import REPO, ROOT, git, registered

FOLDER = ROOT / "revisions/analyst_product"
ARMS = ("mixed", "single_sol", "single_astra")
CAP = "0.30"
FILES = (
    "scripts/research_desk_agent.py",
    "scripts/packet_archive.py",
    "scripts/temporal_web.py",
    "evaluations/paired/production.py",
    "evaluations/paired/product_audit.py",
)
EVALUATOR_FILES = (
    "evaluations/paired/grading.py",
    "evaluations/paired/analysis.py",
    "evaluations/paired/confirmation.py",
)
FINISHED = {"complete", "qualified", "withheld", "insufficient_evidence", "failed"}


def registration(commit):
    base = registered()
    return {
        "version": "analyst-product-1",
        "code_revision": commit,
        "parent_manifest_sha256": fingerprint(json_bytes(base)),
        "code_sha256": {f: fingerprint((REPO / f).read_bytes()) for f in FILES},
        "reference_evaluator_sha256": {
            f: fingerprint(git("show", commit + ":" + f)) for f in EVALUATOR_FILES
        },
        "grader": "fresh masked Astra, as-of evidence and verified excerpts; lock before outcome",
        "arms": list(ARMS),
        "common_cap_usd": CAP,
        "development_ceiling_usd": "10.80",
        "blind_ceiling_usd": "14.40",
        "policy": "All three arms use the same compiler, search interface, originals and arithmetic tools. Mixed uses Sol planning, two Luna researchers, Sol outlook, Astra authorship and fresh Sol review. Each single uses its named model for planning, arithmetic and authorship including one correction. A report can retain supported paragraphs after review.",
        "blind_policy": "Two finalists, same fixed question, one attempt per case and arm. Freeze this production and the earlier comparison before blind outcomes. The production comparison is primary for the public application. The earlier study remains a separate control experiment.",
        "schedule": [
            {**r, "arms": [a for a in r["arms"] if a in ARMS]} for r in base["schedule"]
        ],
    }


def locked():
    path = FOLDER / "registration.json"
    save = json.loads(committed(path))
    corpus.require(
        registration(save["code_revision"]) == save,
        "Production research policy differs from its registration",
    )
    for f, v in save["code_sha256"].items():
        corpus.require(
            fingerprint(git("show", save["code_revision"] + ":" + f)) == v,
            "Registered code hash differs",
        )
    return save


def schedule(split):
    reg = locked()
    if split == "development":
        return reg["schedule"]
    f = json.loads(committed(FOLDER / "finalists.json"))
    corpus.require(
        f["development_scores_sha256"]
        == fingerprint(committed(FOLDER / "development/scores.json"))
        and f["study_registration_sha256"] == fingerprint(json_bytes(reg)),
        "Changed finalist decision",
    )
    # Lock the incumbent and production finalists before any research worker receives a blind input.
    committed(ROOT / "revisions/grounding_v2/finalists.json")
    seed = load(ROOT / "protocol.json")["order_seed"]
    rows = []
    for c in sorted(
        registry("blind", FOLDER), key=lambda c: ordering(seed, "blind", c["case_id"])
    ):
        q, s = corpus.agent_fields(c)
        cid = c["case_id"]
        rows.append(
            {
                "case_id": cid,
                "packet_sha256": packet_digest(
                    corpus.json_bytes(q), corpus.json_bytes(s)
                ),
                "arms": sorted(
                    [f["mixed"], f["single"]],
                    key=lambda a: ordering(seed, "blind", cid, a),
                ),
            }
        )
    return rows


def worker():
    ticket = json.load(sys.stdin)
    packet = PacketCorpus(Path.cwd(), ticket["packet_sha256"])
    gateway = PacketArchive(packet)
    directory = Path.cwd().parent / "run"
    try:
        result = product.run(
            packet.case.question,
            packet.case.cutoff,
            directory,
            None,
            CAP,
            provider=CountJournal(
                directory / "counts", OpenAIResponses(project_key(REPO / ".env.local"))
            ),
            gateway=gateway,
            profile=ticket["arm"],
            assignment=ticket,
        )
        status = result["release"]
    except (ValueError, OSError, ArithmeticError):
        if not (directory / "run.json").is_file():
            raise
        status = load(directory / "run.json")["state"]
    record = load(directory / "run.json")
    print(
        json.dumps(
            {
                "case_id": ticket["case_id"],
                "arm": ticket["arm"],
                "state": status,
                "calls": len(record["calls"]),
                "elapsed_ms": record["elapsed_ms"],
                "cost_usd": record["estimated_model_cost_usd"],
                "reserved_usd": record["unreconciled_reservation_usd"],
                "failure": record.get("failure"),
            }
        )
    )


def run_cases(split):
    reg = locked()
    root = REPO / "runs" / ("analyst-product-" + split)
    root.mkdir(exist_ok=True, parents=True)
    with own_run(root):
        for row in schedule(split):
            for arm in row["arms"]:
                cid = row["case_id"]
                loc = root / cid / arm
                loc.mkdir(exist_ok=True, parents=True)
                packet = loc / "input"
                if not packet.exists():
                    export = (
                        corpus.export_development
                        if split == "development"
                        else partial(export_blind, revision=FOLDER)
                    )
                    export(cid, packet)
                ticket = {
                    "study": fingerprint(json_bytes(reg)),
                    "split": split,
                    "case_id": cid,
                    "packet_sha256": row["packet_sha256"],
                    "arm": arm,
                }
                record_path = loc / "run/run.json"
                if record_path.is_file():
                    record = load(record_path)
                    corpus.require(record["assignment"] == ticket, "Assignment changed")
                    if (
                        record["state"] in FINISHED
                        and Decimal(record["unreconciled_reservation_usd"]) == 0
                    ):
                        verify_ledger(record, partial=True)
                        continue
                r = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        "-c",
                        "from evaluations.paired.production import worker;worker()",
                    ],
                    env={**os.environ, "PYTHONPATH": str(REPO)},
                    cwd=packet,
                    input=json.dumps(ticket),
                    text=True,
                    capture_output=True,
                    check=True,
                )
                print(r.stdout.strip(), flush=True)
                output = json.loads(r.stdout)
                corpus.require(
                    output["state"] in FINISHED
                    and Decimal(output["reserved_usd"]) == 0,
                    "Unconfirmed provider response; preserve the reservation",
                )


def lock_reports(split):
    base = locked()
    rows = []
    entries = {}
    ids = {k: set() for k in ("request_id", "response_id")}
    root = REPO / "runs" / ("analyst-product-" + split)
    info = {k["case_id"]: k for k in registry(split, FOLDER)}
    seed = load(ROOT / "protocol.json")["order_seed"]
    prev = None
    for row in schedule(split):
        cid = row["case_id"]
        for arm in row["arms"]:
            loc = root / cid / arm
            run = loc / "run"
            c = PacketCorpus(loc / "input", row["packet_sha256"])
            rec = load(run / "run.json")
            ticket = {
                "study": fingerprint(json_bytes(base)),
                "split": split,
                "case_id": cid,
                "packet_sha256": row["packet_sha256"],
                "arm": arm,
            }
            corpus.require(
                rec["assignment"] == ticket
                and rec["state"] in FINISHED
                and rec["budget_usd"] == CAP,
                "Unexpected research assignment",
            )
            spent, pending = verify_ledger(rec, partial=True)
            corpus.require(
                not pending and spent <= Decimal(CAP), "Incomplete production ledger"
            )
            verify_private(run, rec, ids)
            alias = ordering(seed, split, cid, "product-mask/" + arm)[:16]
            raw = (run / "run.json").read_bytes()
            entries[alias + "/run.json"] = raw
            if rec["state"] == "failed":
                result = {}
                memo = None
                replay_failure(run, rec, PacketArchive(c))
                # Finite, dated packet is the sole model-visible source for failed assignments as well.
                for search in (run / "searches").glob("*.json"):
                    body = load(search)["body"]
                    corpus.require(
                        body["request"]["max_date"] == c.case.cutoff,
                        "Invalid failed-search cutoff",
                    )
                    for s in body["sources"]:
                        PacketArchive(c).check_source(s, c.case.cutoff)
            else:
                result = product.audit(run, PacketArchive(c))
                entries[alias + "/result.json"] = json_bytes(result)
                memo = (run / "memo.md").read_bytes() if result["memo_sha256"] else None
                if memo:
                    entries[alias + "/memo.md"] = memo
            for search in sorted((run / "searches").glob("*.json")):
                entries[alias + "/searches/" + search.name] = search.read_bytes()
            for count in sorted((run / "counts").glob("*.json")):
                entries[alias + "/counts/" + count.name] = count.read_bytes()
            start = datetime.fromisoformat(rec["created_at"])
            finish = (
                datetime.fromisoformat(result["finished_at"])
                if result
                else datetime.fromisoformat(rec["finished_at"])
            )
            corpus.require(
                finish >= start and (prev is None or start >= prev),
                "Invalid timing or case order",
            )
            prev = finish
            rows.append(
                {
                    "case_id": cid,
                    "arm": arm,
                    "alias": alias,
                    "stratum": info[cid]["stratum"],
                    "publishers": sorted(
                        {urlsplit(s["url"]).hostname for s in info[cid]["sources"]}
                    ),
                    "forward": bool(c.case.forecast_rule),
                    "direction": result.get("direction"),
                    "state": rec["state"],
                    "qualified": rec["state"] == "qualified",
                    "cost_usd": str(spent),
                    "elapsed_ms": round((finish - start).total_seconds() * 1000),
                    "calls": len(rec["calls"]),
                    "cutoff_violations": 0,
                    "record_sha256": fingerprint(raw),
                    "memo_sha256": fingerprint(memo) if memo else None,
                }
            )
    bundle = zipped(entries)
    write_once(FOLDER / split / "reports.zip", bundle)
    write_once(
        FOLDER / split / "reports.json",
        json_bytes(
            {
                "study": base["version"],
                "split": split,
                "records_sha256": fingerprint(bundle),
                "rows": rows,
            }
        ),
    )
    print(
        f"Locked {len(rows)} assignments and {sum(bool(r['memo_sha256']) for r in rows)} reports"
    )


def select():
    scores = committed(FOLDER / "development/scores.json")
    stats, team, single = analysis.select(json.loads(scores)["rows"], ("mixed",))
    result = {
        "study_registration_sha256": fingerprint(json_bytes(locked())),
        "development_scores_sha256": fingerprint(scores),
        "mixed": team,
        "single": single,
        "development": stats,
    }
    write_once(FOLDER / "finalists.json", json_bytes(result))
    print(json.dumps(result, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "operation",
        choices=["register", "run", "lock", "asof", "outcome", "select", "decide"],
    )
    parser.add_argument(
        "split", nargs="?", choices=["development", "blind"], default="development"
    )
    args = parser.parse_args()
    if args.operation == "register":
        print(
            json_bytes(
                registration(git("rev-parse", "HEAD").decode().strip())
            ).decode(),
            end="",
        )
    elif args.operation == "run":
        run_cases(args.split)
    elif args.operation == "lock":
        lock_reports(args.split)
    elif args.operation == "asof":
        grading.grade_asof(args.split, FOLDER)
    elif args.operation == "outcome":
        grading.score(args.split, FOLDER)
    elif args.operation == "select":
        select()
    else:
        f = json.loads(committed(FOLDER / "finalists.json"))
        raw = committed(FOLDER / "blind/scores.json")
        result = analysis.blind_stats(
            json.loads(raw)["rows"],
            f["mixed"],
            f["single"],
            load(ROOT / "protocol.json"),
        )
        result["scores_sha256"] = fingerprint(raw)
        write_once(FOLDER / "blind/decision.json", json_bytes(result))
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
