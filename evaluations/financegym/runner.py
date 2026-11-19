"""Freeze, run and export a full FinanceGym submission using the analyst research engine."""

import argparse
import json
import math
import re
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from decimal import Decimal
from importlib.metadata import version
from pathlib import Path

from evaluations.paired import corpus
from evaluations.paired.analysis import blind_stats
from evaluations.paired.confirmation import committed, write_once
from evaluations.paired.event_audit import verify_private
from evaluations.paired.product_audit import CountJournal
from evaluations.paired.production import FOLDER
from evaluations.paired.registration import REPO, ROOT, RUNTIME_FILES, git
from scripts import quantitative_engine
from scripts import research_desk_agent as engine
from scripts.archive_receipts import RecordedHttp
from scripts.disney_hulu_case import read_saved_file
from scripts.focused_archive import FocusedArchive, verify_transport
from scripts.open_desk_report import SavedArchive
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import (
    ASTRA,
    LUNA,
    MODEL,
    MODEL_RATES,
    OpenAIResponses,
    project_key,
)
from scripts.team_meter import verify_ledger
from scripts.temporal_web import cutoff_day

from .selection import choose, profile_for

PUBLIC = load(ROOT / "protocol.json")["official"]
REGISTER = REPO / "evaluations/financegym/registration.json"
RESEARCH = REPO / "runs/financegym"
FILES = sorted(
    set(RUNTIME_FILES)
    | {
        "scripts/research_desk_agent.py",
        "scripts/temporal_web.py",
        "scripts/focused_archive.py",
        "scripts/packet_archive.py",
        "scripts/archive_receipts.py",
        "scripts/open_desk_report.py",
        "evaluations/financegym/runner.py",
        "evaluations/financegym/selection.py",
        "evaluations/financegym/selection.md",
        "scripts/quantitative_engine.py",
        "scripts/reviewed_research.py",
        "scripts/research_terms.py",
        "scripts/verified_arithmetic.py",
    }
)
FINAL = {"complete", "qualified", "withheld", "insufficient_evidence", "failed"}


def tasks(path):
    raw = read_saved_file(path)
    corpus.require(
        fingerprint(raw) == PUBLIC["sha256"],
        "Benchmark file differs from the pinned public release",
    )
    rows = [json.loads(line) for line in raw.splitlines()]
    corpus.require(
        len(rows) == 400
        and len({r["task_id"] for r in rows}) == 400
        and len({r["question"] for r in rows}) == 400,
        "Expected the 400 unique original tasks",
    )
    for r in rows:
        corpus.require(
            set(r) == {"task_id", "question", "cutoff"}
            and re.fullmatch(r"[a-z0-9]{24}", r["task_id"])
            and isinstance(r["question"], str)
            and r["question"].strip(),
            "Invalid original benchmark task",
        )
        cutoff_day(r["cutoff"])
    return rows


def runtime():
    return {
        "sources": {f: fingerprint((REPO / f).read_bytes()) for f in FILES},
        "dependencies": {d: version(d) for d in ("pydantic", "pypdf")},
        "rates_usd_per_million": MODEL_RATES,
    }


def register(path, cap, public_proof):
    from evaluations.financebench import grading

    from .archive_check import check
    from .spend import report

    cases = tasks(path)
    cutoff_checks = check(public_proof, False, focused=True)
    if REGISTER.is_file():
        existing = locked()
        general = existing["selection"]["general"]
        corpus.require(
            existing["eligibility"]["source_audit"] == cutoff_checks
            and Decimal(existing["case_limits_usd"][general]) == cap,
            "The supplied limit or public source proof differs from registration",
        )
        return existing
    score_file = FOLDER / "blind/scores.json"
    finalists = json.loads(committed(FOLDER / "finalists.json"))
    scores = committed(score_file)
    decision = json.loads(committed(FOLDER / "blind/decision.json"))
    calc = blind_stats(
        json.loads(scores)["rows"],
        finalists["mixed"],
        finalists["single"],
        load(ROOT / "protocol.json"),
    )
    corpus.require(
        decision == {**calc, "scores_sha256": fingerprint(scores)},
        "Independent paired results differ from their signed score records",
    )
    corpus.require(
        cap == Decimal("0.30"), "Use the tested USD 0.30 general-route limit"
    )
    pilot = load(REPO / "evaluations/open_research/ecb/browser.json")
    financial = grading.grade(live=False)
    policy = choose(decision, financial)
    profile = policy["general"]
    counts = Counter(profile_for(row["question"], policy) for row in cases)
    limits = {profile: str(cap)}
    mean = {profile: Decimal(str(decision["metrics"][profile]["mean_cost_usd"]))}
    if policy["statement_analysis"]:
        selected = policy["statement_analysis"]
        limits[selected] = "0.45"
        mean[selected] = (
            Decimal(financial["arms"][selected]["mean_settled_usd"])
            + Decimal(financial["arms"][selected]["unconfirmed_usd"]) / 24
        )
    models = {MODEL, ASTRA} if policy["statement_analysis"] else set()
    models.update(
        {
            "mixed": (MODEL, LUNA, ASTRA),
            "single_astra": (ASTRA,),
            "single_sol": (MODEL,),
        }[profile]
    )
    code = runtime()
    head = git("rev-parse", "HEAD").decode().strip()
    for f, h in code["sources"].items():
        corpus.require(
            fingerprint(git("show", head + ":" + f)) == h,
            "Commit the candidate before registration",
        )
    previous = report()
    expected_cost = sum(mean[p] * n for p, n in counts.items())
    previous_exposure = Decimal(previous["settled_estimate_usd"]) + Decimal(
        previous["outstanding_usd"]
    )
    m = {
        "study": "FinanceGym full public set",
        "code_revision": head,
        "runtime": code,
        "tasks": len(cases),
        "dataset": PUBLIC,
        "selection": policy,
        "archive_version": FocusedArchive.VERSION,
        "profiles_by_task": {
            r["task_id"]: profile_for(r["question"], policy) for r in cases
        },
        "case_limits_usd": limits,
        "estimated_research_usd": str(expected_cost),
        "recorded_prior_project_usd": previous["settled_estimate_usd"],
        "outstanding_prior_reservations_usd": previous["outstanding_usd"],
        "project_estimate_including_prior_reservations_usd": str(
            previous_exposure + expected_cost
        ),
        "total_research_ceiling_usd": str(
            sum(Decimal(limits[p]) * n for p, n in counts.items())
        ),
        "conditions": "One original question and cutoff per assignment. All model-visible searches require its exact max_date. Preserve every attempt and exposure; never repeat an unconfirmed dispatch.",
        "archives": "Independent operator-only HTTP receipts, query snapshots, original-byte offline reconstruction and verified model request replay per report.",
        "models": sorted(models),
        "eligibility": {
            "comparison_sha256": fingerprint(committed(FOLDER / "blind/decision.json")),
            "financial_comparison_sha256": fingerprint(
                committed(grading.FOLDER / "results.json")
            ),
            "source_audit": cutoff_checks,
            "browser_pilot_cost_usd": pilot["spent"],
            "blind_mean_cost_usd": decision["metrics"][profile]["mean_cost_usd"],
            "blind_p95_cost_usd": decision["metrics"][profile]["p95_cost_usd"],
        },
    }
    write_once(REGISTER, json_bytes(m))
    return m


def locked():
    m = json.loads(committed(REGISTER))
    corpus.require(
        m["runtime"] == runtime() and m["dataset"] == PUBLIC and m["tasks"] == 400,
        "Official candidate differs from its registration",
    )
    corpus.require(
        len(m["profiles_by_task"]) == 400
        and sum(
            Decimal(m["case_limits_usd"][p]) for p in m["profiles_by_task"].values()
        )
        == Decimal(m["total_research_ceiling_usd"]),
        "Inconsistent run ceiling",
    )
    return m


def route(row, manifest):
    profile = profile_for(row["question"], manifest["selection"])
    corpus.require(
        profile == manifest["profiles_by_task"][row["task_id"]],
        "Official route differs from its preregistered assignment",
    )
    return (
        (quantitative_engine if profile == "evidence_team_v4" else engine),
        profile,
        manifest["case_limits_usd"][profile],
    )


def assignment(row, m):
    return {
        "benchmark_sha256": PUBLIC["sha256"],
        "candidate_sha256": fingerprint(json_bytes(m)),
        "task_id": row["task_id"],
    }


def capture_one(root, row, m):
    loc = root / row["task_id"]
    directory = loc / "research"
    http = loc / "operator-http"
    loc.mkdir(parents=True, exist_ok=True)
    ticket = assignment(row, m)
    selected, profile, cap = route(row, m)
    if (directory / "run.json").exists():
        rec = load(directory / "run.json")
        corpus.require(
            rec["assignment"] == ticket
            and rec["budget_usd"] == cap
            and rec["research_profile"] == profile,
            "Conflicting official run in the task directory",
        )
        if rec["state"] in FINAL or rec["state"] == "needs_reconciliation":
            return {
                k: rec[k]
                for k in (
                    "state",
                    "estimated_model_cost_usd",
                    "unreconciled_reservation_usd",
                )
            } | {"task_id": row["task_id"]}
    try:
        archive = FocusedArchive(
            row["cutoff"],
            loc / "cache",
            question=row["question"],
            http=RecordedHttp(http),
        )
        selected.run(
            row["question"],
            row["cutoff"],
            directory,
            None,
            cap,
            gateway=archive,
            provider=CountJournal(
                directory / "counts", OpenAIResponses(project_key(REPO / ".env.local"))
            ),
            profile=profile,
            assignment=ticket,
        )
    except (OSError, ValueError, ArithmeticError):
        if not (directory / "run.json").exists():
            raise
    rec = load(directory / "run.json")
    spent, pending = verify_ledger(rec, partial=True)
    corpus.require(
        spent + sum(pending.values()) <= Decimal(cap),
        "Run overspent its registered maximum",
    )
    return {
        "task_id": row["task_id"],
        "state": rec["state"],
        "estimated_model_cost_usd": str(spent),
        "unreconciled_reservation_usd": str(sum(pending.values(), Decimal(0))),
    }


def run(path, root, workers, limit):
    rows, m = tasks(path), locked()
    corpus.require(
        1 <= workers <= 12 and 1 <= limit <= 400,
        "Choose 1–12 parallel cases and up to 400 tasks",
    )
    root.mkdir(parents=True, exist_ok=True)
    with own_run(root), ThreadPoolExecutor(max_workers=workers) as pool:
        for status in pool.map(lambda row: capture_one(root, row, m), rows[:limit]):
            print(json.dumps(status), flush=True)


def answer(row, saved, memo):
    corpus.require(
        saved["question"] == row["question"] and saved["cutoff"] == row["cutoff"],
        "Original task text or date changed",
    )
    corpus.require(
        saved["release"] in {"qualified", "complete"}
        and saved["paragraphs"]
        and fingerprint(memo) == saved["memo_sha256"],
        "Task has no released, verified memo",
    )
    docs = set()
    for search in saved["searches"]:
        corpus.require(
            search["max_date"] == row["cutoff"], "A search differs from the task cutoff"
        )
        docs.update(search["admitted_urls"])
    corpus.require(docs, "No eligible sources reached the agent")
    cited = {
        saved["evidence"][pid]["url"] for p in saved["paragraphs"] for pid in p["pids"]
    }
    body = memo.decode("utf-8")
    corpus.require(
        bool(cited) and cited <= docs and all("](" + u + ")" in body for u in cited),
        "A citation was not retrieved or does not appear in the released report",
    )
    elapsed = (
        datetime.fromisoformat(saved["finished_at"])
        - datetime.fromisoformat(saved["started_at"])
    ).total_seconds()
    corpus.require(math.isfinite(elapsed) and elapsed >= 0, "Invalid elapsed time")
    return {
        "question": row["question"],
        "cutoff": row["cutoff"],
        "report": body,
        "num_docs_retrieved": len({u.split("#:~:text=")[0] for u in docs}),
        "elapsed_s": round(elapsed, 3),
    }


def export(path, root, dest):
    cases, m = tasks(path), locked()
    corpus.require(
        dest.parent.is_dir(), "Create the destination parent directory first"
    )
    audits = []
    provider_ids = {"request_id": set(), "response_id": set()}
    with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
        out = Path(temp) / "submission"
        out.mkdir()
        with (out / "answers.jsonl").open("xb") as file:
            for row in cases:
                folder = root / row["task_id"]
                directory = folder / "research"
                record = load(directory / "run.json")
                selected, profile, cap = route(row, m)
                corpus.require(
                    record["assignment"] == assignment(row, m)
                    and record["research_profile"] == profile
                    and record["budget_usd"] == cap,
                    "Task differs from the registered system",
                )
                verify_private(directory, record, provider_ids)
                saved = selected.audit(
                    directory, SavedArchive(row["cutoff"], m["archive_version"])
                )
                http = verify_transport(directory, folder / "operator-http")
                item = answer(row, saved, read_saved_file(directory / "memo.md"))
                file.write(
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode()
                    + b"\n"
                )
                audits.append(
                    {
                        "task_id": row["task_id"],
                        "profile": profile,
                        "state": saved["release"],
                        "estimated_cost_usd": saved["model_cost_usd"],
                        "report_sha256": saved["memo_sha256"],
                        **http,
                    }
                )
        validate_file(path, out / "answers.jsonl")
        audit = {
            "tasks": len(audits),
            "verified_provider_generations": len(provider_ids["response_id"]),
            "answers_sha256": fingerprint((out / "answers.jsonl").read_bytes()),
            "registration_sha256": fingerprint(json_bytes(m)),
            "total_research_cost_usd": str(
                sum(Decimal(r["estimated_cost_usd"]) for r in audits)
            ),
            "qualified": sum(r["state"] == "qualified" for r in audits),
            "tasks_audited": audits,
        }
        (out / "audit.json").write_bytes(json_bytes(audit))
        if dest.exists():
            corpus.require(
                all(
                    (dest / name).read_bytes() == (out / name).read_bytes()
                    for name in ("answers.jsonl", "audit.json")
                ),
                "Existing submission differs",
            )
        else:
            out.rename(dest)
        return {k: v for k, v in audit.items() if k != "tasks_audited"}


def validate_file(public_path, answers):
    rows = tasks(public_path)
    output = [json.loads(l) for l in read_saved_file(answers).splitlines()]
    corpus.require(len(output) == 400, "A full submission requires exactly 400 reports")
    for r, a in zip(rows, output, strict=True):
        corpus.require(
            a["question"] == r["question"]
            and a["cutoff"] == r["cutoff"]
            and isinstance(a["report"], str)
            and re.search(r"\]\(https://[^\s]+\)", a["report"]),
            "Question, cutoff or report citation does not match the full public set",
        )
    return {
        "rows": 400,
        "shares_original_order": True,
        "answers_sha256": fingerprint(answers.read_bytes()),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="action", required=True)
    for name in ("preflight", "register", "run", "export", "validate"):
        cmd = sub.add_parser(name)
        cmd.add_argument("questions", type=Path)
        if name == "register":
            cmd.add_argument("--cap-usd", type=Decimal, required=True)
            cmd.add_argument("--source-proof", type=Path, required=True)
        if name == "export":
            cmd.add_argument("--runs", type=Path, default=RESEARCH)
        if name == "run":
            cmd.add_argument("--workers", type=int, default=4)
            cmd.add_argument("--limit", type=int, default=400)
        if name in {"validate", "export"}:
            cmd.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.action == "preflight":
        rows = tasks(args.questions)
        dates = sorted({r["cutoff"] for r in rows})
        result = {
            "unique_tasks": len(rows),
            "unique_dates": len(dates),
            "earliest": dates[0],
            "latest": dates[-1],
            "sha256": PUBLIC["sha256"],
        }
    elif args.action == "register":
        result = register(args.questions, args.cap_usd, args.source_proof)
    elif args.action == "run":
        return run(args.questions, RESEARCH, args.workers, args.limit)
    elif args.action == "validate":
        result = validate_file(args.questions, args.output)
    else:
        result = export(args.questions, args.runs, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
