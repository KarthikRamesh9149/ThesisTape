"""Run or verify the preregistered FinanceBench research comparison."""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from importlib.metadata import version

from evaluations.paired.confirmation import committed, write_once, zipped
from evaluations.paired.product_audit import CountJournal
from evaluations.paired.registration import REPO, git
from scripts import quantitative_engine
from scripts.disney_hulu_case import CaseError
from scripts.packet_archive import PacketArchive
from scripts.paired_packet import PacketCorpus
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key
from scripts.team_meter import verify_ledger

from .corpus import FOLDER, PACKETS, SEED, order, prepare

ROOT = REPO / "runs/financebench-research"
ARMS = ("evidence_team_v4", "quantitative_astra")
CAP = "0.45"
TERMINAL = {
    "complete",
    "qualified",
    "withheld",
    "insufficient_evidence",
    "failed",
    "needs_reconciliation",
}
FILES = tuple(
    "scripts/" + name + ".py"
    for name in (
        "quantitative_engine",
        "reviewed_research",
        "research_terms",
        "verified_arithmetic",
        "research_desk_agent",
        "packet_archive",
        "paired_packet",
        "retrieval_gateway",
        "sol_provider",
        "team_meter",
        "research_team",
        "team_report",
        "run_checkpoint",
    )
) + (
    "evaluations/financebench/corpus.py",
    "evaluations/financebench/study.py",
    "evaluations/financebench/protocol.md",
)


def runtime():
    return {
        "sha256": {p: fingerprint((REPO / p).read_bytes()) for p in FILES},
        "libraries": {p: version(p) for p in ("pydantic", "pypdf")},
    }


def register():
    current = runtime()
    head = git("rev-parse", "HEAD").decode().strip()
    for name, h in current["sha256"].items():
        if fingerprint(git("show", head + ":" + name)) != h:
            raise ValueError(
                "Commit the research procedures before freezing the sample"
            )
    spec = {
        "name": "Public FinanceBench replication",
        "sampling_seed": SEED,
        "arms": list(ARMS),
        "same_cap_usd": CAP,
        "maximum_research_usd": "21.60",
        "code_revision": head,
        "runtime": current,
        **prepare(),
    }
    write_once(FOLDER / "registration.json", json_bytes(spec))
    return spec


def locked():
    spec = json.loads(committed(FOLDER / "registration.json"))
    if (
        spec["runtime"] != runtime()
        or spec["arms"] != list(ARMS)
        or spec["same_cap_usd"] != CAP
    ):
        raise ValueError("Research or evaluation procedure differs from registration")
    return spec


def packet(row):
    return PacketCorpus(PACKETS / row["case_id"], row["packet_sha256"])


def ticket(spec, row, arm):
    return {
        "registration_sha256": fingerprint(json_bytes(spec)),
        "packet_sha256": row["packet_sha256"],
        "case_id": row["case_id"],
        "arm": arm,
    }


def run_event(spec, row):
    case = packet(row)
    for arm in sorted(ARMS, key=lambda s: order(row["case_id"] + "/" + s)):
        folder = ROOT / row["case_id"] / arm
        path = folder / "run.json"
        assignment = ticket(spec, row, arm)
        seen = load(path) if path.is_file() else None
        if seen and seen["assignment"] != assignment:
            raise ValueError("Existing assignment differs")
        if seen is None or seen["state"] not in TERMINAL:
            try:
                quantitative_engine.run(
                    case.case.question,
                    case.case.cutoff,
                    folder,
                    None,
                    CAP,
                    profile=arm,
                    gateway=PacketArchive(case),
                    provider=CountJournal(
                        folder / "counts",
                        OpenAIResponses(project_key(REPO / ".env.local")),
                    ),
                    assignment=assignment,
                )
            except (CaseError, ValueError, OSError):
                if not path.is_file() or load(path)["state"] not in TERMINAL:
                    raise
        record = load(path)
        paid, held = verify_ledger(record, partial=True)
        outstanding = sum(held.values(), Decimal(0))
        if paid + outstanding > Decimal(CAP) or record["state"] not in TERMINAL:
            raise ValueError("Invalid final state or spending")
        print(
            json.dumps(
                {
                    "case_id": row["case_id"],
                    "arm": arm,
                    "state": record["state"],
                    "settled_usd": str(paid),
                    "unconfirmed_usd": str(outstanding),
                }
            ),
            flush=True,
        )


def run(workers):
    if not 1 <= workers <= 6:
        raise ValueError("Use between one and six concurrent issuer pairs")
    spec = locked()
    ROOT.mkdir(parents=True, exist_ok=True)
    with own_run(ROOT), ThreadPoolExecutor(max_workers=workers) as pool:
        for _ in pool.map(lambda r: run_event(spec, r), spec["cases"]):
            pass


def seal():
    spec = locked()
    archive, index = {}, []
    for row in spec["cases"]:
        case = packet(row)
        for arm in ARMS:
            folder = ROOT / row["case_id"] / arm
            record = load(folder / "run.json")
            if (
                record["assignment"] != ticket(spec, row, arm)
                or record["state"] not in TERMINAL
            ):
                raise ValueError("A registered assignment is incomplete or mismatched")
            paid, held = verify_ledger(record, partial=True)
            outstanding = sum(held.values(), Decimal(0))
            if paid + outstanding > Decimal(CAP):
                raise ValueError("Assignment exceeds its cap")
            entry = {
                "case_id": row["case_id"],
                "arm": arm,
                "state": record["state"],
                "settled_usd": str(paid),
                "unconfirmed_usd": str(outstanding),
                "calls": len(record["calls"]),
                "record_sha256": fingerprint(json_bytes(record)),
            }
            prefix = row["case_id"] + "/" + arm + "/"
            archive[prefix + "run.json"] = json_bytes(record)
            if record.get("result_sha256"):
                result = quantitative_engine.audit(
                    folder, PacketArchive(case), record=record
                )
                archive[prefix + "result.json"] = json_bytes(result)
                for f in (folder / "searches").glob("*.json"):
                    archive[prefix + "searches/" + f.name] = f.read_bytes()
                if result["memo_sha256"]:
                    raw = (folder / "memo.md").read_bytes()
                    archive[prefix + "memo.md"] = raw
                    entry["memo_sha256"] = fingerprint(raw)
            elif (folder / "memo.md").exists():
                raise ValueError("Unverified report present on unfinished assignment")
            else:
                entry["failure"] = record.get("failure")
            index.append(entry)
    bundle = zipped(archive)
    write_once(FOLDER / "reports.zip", bundle)
    write_once(
        FOLDER / "reports.json",
        json_bytes(
            {
                "registration_sha256": fingerprint(json_bytes(spec)),
                "archive_sha256": fingerprint(bundle),
                "rows": index,
            }
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("register", "run", "seal"))
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()
    if args.action == "register":
        spec = register()
        print(len(spec["cases"]), "issuers frozen")
    elif args.action == "run":
        run(args.workers)
    else:
        seal()
