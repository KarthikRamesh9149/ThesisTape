"""Lock complete research records and isolate the as-of and outcome grading phases."""

import argparse
import io
import json
import tempfile
import zipfile
from datetime import datetime, timedelta
from decimal import Decimal
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from scripts.paired_packet import PacketCorpus, packet_digest
from scripts.paired_v2 import workflow
from scripts.research_team import check_configuration, reproduce
from scripts.retrieval_sources import instant
from scripts.run_checkpoint import load, own_run, publish
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_meter import verify_ledger

from . import corpus as data
from .event_audit import verify_private
from .reconciliation import replay_lost, sidecar, verify_resolution
from .registration import REPO, ROOT, git
from .runner import TERMINAL, admission, config
from .study import REVISION, VERSION, assignment, locked
from .terminal_audit import verify_failure

PROTOCOL = ROOT / "protocol.json"


def committed(path):
    require_bytes = git("show", "HEAD:" + str(path.relative_to(REPO)))
    data.require(
        require_bytes == path.read_bytes(),
        f"Expected a committed, unchanged artifact at {path.name}",
    )
    return require_bytes


def write_once(path, raw):
    if path.exists():
        data.require(
            path.read_bytes() == raw, f"Immutable artifact differs at {path.name}"
        )
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    publish(path, raw)


def zipped(entries):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for name, raw in sorted(entries.items()):
            z.writestr(
                zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0)),
                raw,
                compress_type=zipfile.ZIP_DEFLATED,
            )
    return buf.getvalue()


def input_zip(split):
    name = (
        "development-sources.zip"
        if split == "development"
        else "evaluator/blind-inputs.zip"
    )
    return zipfile.ZipFile(
        io.BytesIO(data.checked_file(ROOT, load(ROOT / "freeze.json"), name))
    )


def registry(split, revision=REVISION):
    if split == "development":
        return json.loads(
            data.checked_file(ROOT, load(ROOT / "freeze.json"), "development.json")
        )
    data.require(split == "blind", "Invalid study partition")
    committed(revision / "finalists.json")
    with input_zip(split) as z:
        return json.loads(z.read("inputs.json"))


def export_blind(cid, output, revision=REVISION):
    selected = [c for c in registry("blind", revision) if c["case_id"] == cid]
    data.require(
        len(selected) == 1 and not output.exists(), "Unknown or duplicate blind packet"
    )
    q, sources = data.agent_fields(selected[0])
    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        input_zip("blind") as z,
        tempfile.TemporaryDirectory(dir=output.parent) as temp,
    ):
        packet = Path(temp) / "packet"
        (packet / "objects").mkdir(parents=True)
        (packet / "question.json").write_bytes(data.json_bytes(q))
        (packet / "sources.json").write_bytes(data.json_bytes(sources))
        for source in sources:
            raw = z.read(data.pdf_name(source))
            data.require(
                fingerprint(raw) == source["sha256"]
                and instant(source["published"], source["timezone"])
                <= instant(q["cutoff"]),
                "Blind source integrity issue",
            )
            (packet / data.pdf_name(source)).write_bytes(raw)
        PacketCorpus(
            packet, packet_digest(data.json_bytes(q), data.json_bytes(sources))
        )
        packet.rename(output)


def ordering(seed, split, event, arm=None):
    key = f"{seed}/{split}/{event}"
    return fingerprint((key if arm is None else f"{key}/{arm}").encode())


def split_manifest(split):
    base = locked()
    if split == "development":
        return base
    finalists = json.loads(committed(REVISION / "finalists.json"))
    data.require(
        finalists["study_registration_sha256"] == fingerprint(json_bytes(base))
        and finalists["development_scores_sha256"]
        == fingerprint(committed(REVISION / "development/scores.json")),
        "The finalist freeze no longer matches development",
    )
    seed = load(PROTOCOL)["order_seed"]
    rows = []
    for case in sorted(
        registry(split), key=lambda c: ordering(seed, split, c["case_id"])
    ):
        q, sources = data.agent_fields(case)
        event = case["case_id"]
        choices = [finalists["mixed"], finalists["single"]]
        rows.append(
            {
                "case_id": event,
                "packet_sha256": packet_digest(
                    data.json_bytes(q), data.json_bytes(sources)
                ),
                "arms": sorted(choices, key=lambda a: ordering(seed, split, event, a)),
            }
        )
    data.require(
        len(rows) == 24 and sum(len(r["arms"]) for r in rows) == 48,
        "Incomplete blind schedule",
    )
    return {
        **base,
        "split": split,
        "finalists_sha256": fingerprint(json_bytes(finalists)),
        "schedule": rows,
    }


def runtime_arm(arm, case):
    # A forecast-off finalist on a factual blind event runs the identical mixed policy.
    return "mixed" if arm == "mixed_no_forecast" and not case["forecast_rule"] else arm


def audit_evidence(record, corpus):
    sources = {s["url"]: s for s in corpus.sources}
    for hit in record.get("evidence", {}).values():
        source = sources.get(hit["url"])
        data.require(
            source is not None
            and source["sha256"] == hit["sha256"]
            and source["title"] == hit["title"]
            and instant(hit["available_by_utc"]) <= instant(corpus.case.cutoff),
            "Unapproved model-visible evidence",
        )
    # Every dispatched prompt, including those from unsuccessful runs, is checked in lock_reports.


def lock_reports(split):
    manifest = split_manifest(split)
    cases = {c["case_id"]: c for c in registry(split)}
    folder = REVISION / split
    root = REPO / "runs" / f"paired-{split}-v2"
    entries = {}
    rows = []
    identities = {k: set() for k in ("request_id", "response_id")}
    checkpoints = set()
    last = None
    seed = load(PROTOCOL)["order_seed"]
    for row in manifest["schedule"]:
        cid = row["case_id"]
        for arm in row["arms"]:
            actual = runtime_arm(arm, cases[cid])
            loc = root / cid / arm
            record = load(loc / "run/run.json")
            corpus = PacketCorpus(loc / "input", row["packet_sha256"])
            ticket = admission(manifest, row, arm)
            if actual != arm:
                ticket = {**ticket, "arm": actual, "study_arm": arm}
            cfg = {**config(corpus, ticket), "policy_version": VERSION}
            check_configuration(record, cfg)
            closed = sidecar(split, cid, arm)
            correction = json.loads(committed(closed)) if closed.is_file() else None
            interrupted = (
                split == "blind"
                and record["state"] == "needs_reconciliation"
                and correction is None
            )
            require_state = (
                record["state"] in TERMINAL or correction is not None or interrupted
            )
            data.require(
                require_state and record["budget_usd"] == cfg["per_case_arm_usd"],
                "Incomplete or mismatched assignment",
            )
            spent, owed = verify_ledger(record, partial=True)
            held = Decimal(0)
            start = datetime.fromisoformat(record["created_at"])
            if correction:
                settlement = verify_resolution(record, correction)
                spent = Decimal(settlement["settled_total_usd"])
                end = datetime.fromisoformat(correction["resolved_at"])
                state = settlement["research_state"]
                # The private CSV and five receipts are matched by the operator settlement command.
                # A portable copy keeps the original interrupted record and its separate billing evidence.
                entries[f"settlements/{correction['checkpoint_id']}.json"] = json_bytes(
                    correction
                )
            elif interrupted:
                from .product_recovery import interrupted_receipts

                spent, held = interrupted_receipts(
                    loc / "run", record, identities, partial(workflow, corpus, actual)
                )
                end = start + timedelta(milliseconds=record["elapsed_ms"])
                state = "provider_interrupted"
            else:
                data.require(not owed, "Unreconciled assignment")
                verify_private(loc / "run", record, identities)
                end = datetime.fromisoformat(record["finished_at"])
                state = record["state"]
            data.require(
                spent + held <= Decimal(cfg["per_case_arm_usd"]), "Allowance exceeded"
            )
            data.require(
                end >= start and (last is None or start >= last),
                "Runs differ from registered order",
            )
            last = end
            data.require(
                record["checkpoint_id"] not in checkpoints, "Shared assignment identity"
            )
            checkpoints.add(record["checkpoint_id"])
            audit_evidence(record, corpus)
            memo = None
            if correction:
                replay_lost(record, correction, corpus, actual)
            elif state in ("complete", "withheld"):
                memo = reproduce(record, cfg, partial(workflow, corpus, actual))
            elif not interrupted:
                incident = REVISION / "host-incidents" / split / cid / (arm + ".json")
                incident_record = (
                    json.loads(committed(incident)) if incident.is_file() else None
                )
                verify_failure(record, corpus, actual, workflow, incident_record)
                if incident_record:
                    entries["host-incidents/" + record["checkpoint_id"] + ".json"] = (
                        json_bytes(incident_record)
                    )
            memo_path = loc / "run/memo.md"
            data.require(
                (memo_path.read_bytes() if memo_path.is_file() else None) == memo,
                "Published memo mismatch",
            )
            alias = ordering(seed, split, cid, "mask/" + arm)[:16]
            raw = (loc / "run/run.json").read_bytes()
            entries[alias + "/run.json"] = raw
            if memo is not None:
                entries[alias + "/memo.md"] = memo
            rows.append(
                {
                    "case_id": cid,
                    "arm": arm,
                    "alias": alias,
                    "stratum": cases[cid]["stratum"],
                    "publishers": sorted(
                        {urlsplit(s["url"]).hostname for s in cases[cid]["sources"]}
                    ),
                    "forward": bool(cases[cid]["forecast_rule"]),
                    "direction": record.get("direction"),
                    "state": state,
                    "qualified": record.get("coverage") == "qualify",
                    "cost_usd": str(spent + held),
                    **(
                        {
                            "confirmed_cost_usd": str(spent),
                            "unreconciled_usd": str(held),
                        }
                        if held
                        else {}
                    ),
                    "elapsed_ms": round((end - start).total_seconds() * 1000),
                    "calls": len(record["calls"]),
                    "cutoff_violations": 0,
                    "record_sha256": fingerprint(raw),
                    "memo_sha256": fingerprint(memo) if memo else None,
                }
            )
    archive = zipped(entries)
    write_once(folder / "reports.zip", archive)
    index = {
        "study": VERSION,
        "split": split,
        "registration_sha256": fingerprint(json_bytes(manifest)),
        "records_sha256": fingerprint(archive),
        "rows": rows,
    }
    write_once(folder / "reports.json", json_bytes(index))
    return {
        "rows": len(rows),
        "released": sum(bool(x["memo_sha256"]) for x in rows),
        "calls": sum(x["calls"] for x in rows),
        "settled_usd": str(
            sum(Decimal(x.get("confirmed_cost_usd", x["cost_usd"])) for x in rows)
        ),
        "unreconciled_usd": str(
            sum(Decimal(x.get("unreconciled_usd", "0")) for x in rows)
        ),
    }


def run_blind(*, allow_new_team=True):
    manifest = split_manifest("blind")
    cases = {c["case_id"]: c for c in registry("blind")}
    root = REPO / "runs/paired-blind-v2"
    root.mkdir(parents=True, exist_ok=True)
    with own_run(root):
        for row in manifest["schedule"]:
            for arm in row["arms"]:
                ticket = admission(manifest, row, arm)
                actual = runtime_arm(arm, cases[row["case_id"]])
                if actual != arm:
                    ticket = {**ticket, "arm": actual, "study_arm": arm}
                directory = root / row["case_id"] / arm
                path = directory / "run/run.json"
                if path.is_file():
                    rec = load(path)
                    packet = PacketCorpus(directory / "input", row["packet_sha256"])
                    check_configuration(
                        rec, {**config(packet, ticket), "policy_version": VERSION}
                    )
                    _, pending = verify_ledger(rec, partial=True)
                    if (
                        rec["state"] == "needs_reconciliation"
                        and pending
                        and rec.get("failure")
                    ):
                        continue
                    if (
                        rec["state"] in TERMINAL
                        and not pending
                        and rec.get("finished_at")
                    ):
                        continue
                if not allow_new_team and not actual.startswith("single_"):
                    return
                result = assignment(ticket, directory, export_blind, False)
                print(json.dumps(result), flush=True)
                data.require(
                    result["state"] in TERMINAL
                    and Decimal(result["unreconciled_reservation_usd"]) == 0,
                    "A provider response is unconfirmed. Preserve it before continuing the schedule.",
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command", choices=["lock-development", "run-blind", "lock-blind"]
    )
    parser.add_argument(
        "--hold-new-teams",
        action="store_true",
        help="Stop before the next new team assignment during a provider incident",
    )
    args = parser.parse_args()
    if args.command == "run-blind":
        run_blind(allow_new_team=not args.hold_new_teams)
    else:
        print(json.dumps(lock_reports(args.command.removeprefix("lock-")), indent=2))


if __name__ == "__main__":
    main()
