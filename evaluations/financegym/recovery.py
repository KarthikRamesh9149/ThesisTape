"""Verify unsuccessful primary investigations and recover an answer under the registered conditional policy."""

import argparse
import json
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from itertools import batched
from pathlib import Path

from evaluations.financebench.audit import failed as quantitative_failure
from evaluations.paired.confirmation import committed, write_once
from evaluations.paired.corpus import require
from evaluations.paired.event_audit import verify_private
from evaluations.paired.product_audit import replay_failure
from evaluations.paired.product_recovery import interrupted_receipts
from evaluations.paired.registration import REPO
from scripts import quantitative_engine
from scripts import research_desk_agent as general
from scripts.focused_archive import verify_transport
from scripts.open_desk_report import SavedArchive
from scripts.packet_archive import SavedSearches
from scripts.question_attribution import publish
from scripts.research_team import check_configuration
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_meter import verify_ledger

from . import runner

REGISTRATION = REPO / "evaluations/financegym/recovery-registration.json"
OUTPUT = REPO / "runs/financegym-recovery"


def register(base):
    sources = [
        "evaluations/financegym/recovery.py",
        "evaluations/financebench/audit.py",
        "evaluations/paired/product_recovery.py",
        "evaluations/paired/product_audit.py",
    ]
    manifest = {
        "primary_registration_sha256": fingerprint(json_bytes(base)),
        "recovery_policy": base["unanswered_recovery"],
        "sources": {name: fingerprint(committed(REPO / name)) for name in sources},
    }
    if REGISTRATION.exists():
        require(
            load(REGISTRATION) == manifest,
            "The registered recovery implementation changed",
        )
    return manifest


def ticket(row, base, extension, original):
    return {
        **runner.assignment(row, base),
        "recovery_registration_sha256": fingerprint(json_bytes(extension)),
        "original_run_sha256": fingerprint((original / "run.json").read_bytes()),
    }


def second_route(base, original_profile):
    policy = base["unanswered_recovery"]
    if original_profile != policy["profile"]:
        return quantitative_engine, policy["profile"], policy["cap_usd"]
    profile = base["selection"]["general"]
    require(profile != original_profile, "The recovery researcher must be independent")
    return general, profile, base["case_limits_usd"][profile]


def attempt(loc, row, version, assigned, route, ids):
    run = loc / "research"
    rec = load(run / "run.json")
    engine, profile, cap = route
    gateway = SavedArchive(row["cutoff"], version)
    check_configuration(
        rec,
        engine.configuration(
            row["question"], row["cutoff"], Decimal(cap), gateway, profile, assigned
        ),
    )
    spent, held = verify_ledger(rec, partial=True)
    reserved = sum(held.values(), Decimal(0))
    require(spent + reserved <= Decimal(cap), "An attempt exceeded its own ceiling")
    published, visible, original_hash = None, None, None
    state = rec["state"]
    if state == "needs_reconciliation":

        def execute(ask):
            return engine.research(
                row["question"],
                row["cutoff"],
                SavedSearches(gateway, run / "searches", offline=True),
                ask,
                profile=profile,
            )

        interrupted_receipts(run, rec, ids, execute)
    else:
        require(not held, "A finished investigation still carries an active call")
        verify_private(run, rec, ids)
        if state == "failed":
            if engine is quantitative_engine:
                quantitative_failure(run, rec, gateway)
            else:
                replay_failure(run, rec, gateway)
        else:
            require(
                state in {"complete", "qualified", "insufficient_evidence", "withheld"},
                "Original investigation has not finished",
            )
            saved = engine.audit(run, gateway, record=rec)
            original_hash = saved["memo_sha256"]
            if original_hash:
                original = (run / "memo.md").read_bytes()
                visible, raw = publish(saved, original)
                if raw:
                    published = runner.answer(row, saved, original)
                state = visible["release"]
    searches = sorted((run / "searches").glob("*.json"))
    if searches:
        http = verify_transport(run, loc / "operator-http")
    else:
        require(
            not list((loc / "operator-http").glob("*.json")),
            "An unaccounted partial search needs investigation",
        )
        http = {
            "searches": 0,
            "transport_requests": 0,
            "eligible_sources": 0,
            "eligible_passage_urls": 0,
        }
    editions = {
        s["url"].split("#:~:text=")[0]
        for p in searches
        for s in load(p)["body"]["sources"]
    }
    elapsed = (
        datetime.fromisoformat(rec["finished_at"])
        - datetime.fromisoformat(rec["created_at"])
    ).total_seconds()
    require(elapsed >= 0, "Invalid recorded research duration")
    audit = {
        "profile": profile,
        "state": state,
        "research_state": rec["state"],
        "original_run_sha256": fingerprint((run / "run.json").read_bytes()),
        "source_report_sha256": original_hash,
        "report_sha256": visible["memo_sha256"] if visible else None,
        "attribution_review": visible.get("attribution_review") if visible else None,
        "estimated_cost_usd": str(spent),
        "outstanding_usd": str(reserved),
        "elapsed_s": round(elapsed, 3),
        "failure": rec.get("failure"),
        **http,
    }
    return published, audit, editions


def identifiers():
    return {"request_id": set(), "response_id": set()}


def needs_research(loc):
    """A fast skip for accepted originals. Every dispatched recovery still receives a complete primary audit."""
    run = loc / "research"
    if not (run / "run.json").exists():
        return False
    state = load(run / "run.json")["state"]
    if state in {"complete", "qualified"}:
        saved = load(run / "result.json")
        _, memo = publish(saved, (run / "memo.md").read_bytes())
        return memo is None
    return state in {
        "failed",
        "withheld",
        "insufficient_evidence",
        "needs_reconciliation",
    }


def recover_one(row, base, extension):
    primary = runner.RESEARCH / row["task_id"]
    original = runner.route(row, base)
    answer, _, _ = attempt(
        primary,
        row,
        base["archive_version"],
        runner.assignment(row, base),
        original,
        identifiers(),
    )
    require(answer is None, "An accepted primary answer cannot be rerun")
    return runner.research_once(
        OUTPUT / row["task_id"],
        row,
        ticket(row, base, extension, primary / "research"),
        *second_route(base, original[1]),
        distinct_from=primary / "research",
    )


def run(questions, base, extension, workers):
    require(1 <= workers <= 12, "Choose one to twelve concurrent cases")
    OUTPUT.mkdir(parents=True, exist_ok=True)
    rows = [
        r
        for r in runner.tasks(questions)
        if needs_research(runner.RESEARCH / r["task_id"])
    ]
    with own_run(OUTPUT), ThreadPoolExecutor(max_workers=workers) as pool:
        for group in batched(rows, workers):
            results = list(pool.map(lambda r: recover_one(r, base, extension), group))
            for item in results:
                print(json.dumps(item), flush=True)
            require(
                not any(
                    not r.get("already_recorded")
                    and (
                        r["state"] == "needs_reconciliation"
                        or r.get("provider_failure")
                    )
                    for r in results
                ),
                "Provider incident during recovery. All current attempts have been retained.",
            )


def resolve(row, base, extension, ids):
    primary = runner.RESEARCH / row["task_id"]
    first_route = runner.route(row, base)
    result, first, editions = attempt(
        primary,
        row,
        base["archive_version"],
        runner.assignment(row, base),
        first_route,
        ids,
    )
    attempts = [first]
    extra = OUTPUT / row["task_id"]
    if result is None:
        require(
            (extra / "research/run.json").is_file(),
            f"Recovery missing for {row['task_id']}",
        )
        result, second, additional = attempt(
            extra,
            row,
            base["archive_version"],
            ticket(row, base, extension, primary / "research"),
            second_route(base, first_route[1]),
            ids,
        )
        require(
            result is not None,
            f"No publishable evidence-backed report for {row['task_id']} after recovery",
        )
        first_prompts = {p.stem for p in (primary / "research/counts").glob("*.json")}
        second_prompts = {p.stem for p in (extra / "research/counts").glob("*.json")}
        require(
            not first_prompts & second_prompts,
            "A recovery duplicated an original prompt",
        )
        a, b = [load(folder / "research/run.json") for folder in (primary, extra)]
        require(
            not {c["request_sha256"] for c in a["calls"].values()}
            & {c["request_sha256"] for c in b["calls"].values()},
            "Duplicate model generation across attempts",
        )
        attempts.append(second)
        editions |= additional
    else:
        require(
            not (extra / "research/run.json").exists(),
            "Unexpected recovery for an accepted original",
        )
    money = sum(Decimal(a["estimated_cost_usd"]) for a in attempts)
    pending = sum(Decimal(a["outstanding_usd"]) for a in attempts)
    result.update(
        num_docs_retrieved=len(editions),
        elapsed_s=round(sum(a["elapsed_s"] for a in attempts), 3),
    )
    return result, {
        "task_id": row["task_id"],
        "primary_profile": first_route[1],
        "profile": attempts[-1]["profile"],
        "state": attempts[-1]["state"],
        "report_sha256": attempts[-1]["report_sha256"],
        "estimated_cost_usd": str(money),
        "outstanding_usd": str(pending),
        "attempts": attempts,
    }


def export(questions, base, extension, dest):
    ids, audits = identifiers(), []
    dest.parent.mkdir(exist_ok=True, parents=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as temporary:
        folder = Path(temporary) / "submission"
        folder.mkdir()
        with (folder / "answers.jsonl").open("xb") as out:
            for row in runner.tasks(questions):
                result, proof = resolve(row, base, extension, ids)
                out.write(
                    json.dumps(
                        result, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                    + b"\n"
                )
                audits.append(proof)
        verified = runner.validate_file(questions, folder / "answers.jsonl")
        summary = {
            "tasks": len(audits),
            "verified_provider_generations": len(ids["response_id"]),
            "answers_sha256": verified["answers_sha256"],
            "registration_sha256": fingerprint(json_bytes(base)),
            "recovery_registration_sha256": fingerprint(json_bytes(extension)),
            "primary_releases": sum(len(r["attempts"]) == 1 for r in audits),
            "recovery_releases": sum(len(r["attempts"]) == 2 for r in audits),
            "qualified": sum(r["state"] == "qualified" for r in audits),
            "total_research_cost_usd": str(
                sum(Decimal(r["estimated_cost_usd"]) for r in audits)
            ),
            "outstanding_usd": str(sum(Decimal(r["outstanding_usd"]) for r in audits)),
        }
        (folder / "audit.json").write_bytes(
            json_bytes({**summary, "tasks_audited": audits})
        )
        if dest.exists():
            for name in ("answers.jsonl", "audit.json"):
                require(
                    (dest / name).read_bytes() == (folder / name).read_bytes(),
                    "Existing submission differs",
                )
        else:
            folder.rename(dest)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    commands.add_parser("register")
    for name in ("run", "export"):
        cmd = commands.add_parser(name)
        cmd.add_argument("questions", type=Path)
        if name == "run":
            cmd.add_argument("--workers", type=int, default=4)
        else:
            cmd.add_argument("destination", type=Path)
    args = parser.parse_args()
    base = runner.locked()
    extension = register(base)
    if args.action == "register":
        write_once(REGISTRATION, json_bytes(extension))
        print(json.dumps(extension, indent=2))
        return
    require(
        json.loads(committed(REGISTRATION)) == extension,
        "Commit the recovery registration before use",
    )
    if args.action == "run":
        run(args.questions, base, extension, args.workers)
    else:
        print(
            json.dumps(
                export(args.questions, base, extension, args.destination), indent=2
            )
        )


if __name__ == "__main__":
    main()
