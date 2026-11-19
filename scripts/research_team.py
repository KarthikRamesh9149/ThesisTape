"""Run, resume or replay the Sol-led financial research team."""

import argparse
import shlex
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock
from uuid import uuid4

from .claim_review import VERSION as REVIEW_VERSION
from .disney_hulu_case import CaseError, read_saved_file
from .forecast_specialist import VERSION as FORECAST_VERSION
from .material_dispute import LEGACY_VERSION
from .material_dispute import VERSION as DISPUTE_VERSION
from .run_checkpoint import (
    PauseRequested,
    load,
    own_run,
    pause_on_signal,
    publish,
    specification,
    sync_directory,
)
from .sol_baseline import fingerprint, json_bytes
from .sol_provider import (
    ASTRA,
    LUNA,
    MODEL,
    MODEL_RATES,
    TERRA,
)
from .team_meter import Meter, check_decision_order, recorded_output, verify_ledger
from .team_research import COLLECTIONS, VERSION, Case, analyst_case, case_for, research
from .visual_reader import ROUTES as TABLE_ROUTES
from .visual_reader import VERSION as VISUAL_VERSION

CASES = ("disney", "tradeweb", "tradeweb-before", "toyota", "nvidia", "toyota-buybacks", "toyota-buybacks-actual",
         "twitter-deal", "twitter-price")
ROUTES = ("team", "single", "reviewed", "forecast", "dispute", *TABLE_ROUTES)


def default_route(case: Case) -> str:
    if case.table:
        return "visual"
    if case.source_urls:
        return "dispute"
    return "reviewed" if case.collection else "team"


def configuration(case, corpus, route, *, dispute_version=DISPUTE_VERSION):
    models = (MODEL, LUNA)
    if case.source_urls:
        models = (MODEL,)
    if route == "dispute":
        models = (*models, ASTRA)
    if route in TABLE_ROUTES:
        if not case.table:
            raise CaseError("The table route needs a located source table")
        models = (MODEL, TERRA) if route == "visual" else (MODEL,)
    return {
        "schema_version": 3,
        "prompt_version": VERSION,
        **({"source_file_sha256": fingerprint(json_bytes(list(case.source_urls)))} if case.source_urls else {}),
        **({"dispute_version": dispute_version} if route == "dispute" or case.source_urls else {}),
        **({"table_version": VISUAL_VERSION, "review_version": VISUAL_VERSION,
            "table": asdict(case.table)} if route in TABLE_ROUTES else {}),
        **(
            {"review_version": REVIEW_VERSION}
            if route in ("reviewed", "forecast", "dispute")
            else {}
        ),
        **(
            {
                "forecast_version": FORECAST_VERSION,
                "forecast_choices": list(case.forecast_choices),
            }
            if case.forecast_choices
            else {}
        ),
        "case_id": case.id,
        **({"collection": case.collection} if case.collection else {}),
        **case.intake(),
        "route": route,
        "corpus_sha256": corpus.digest,
        "paid_tool_charges_usd": "0",
        "pricing": {
            "status": "estimate_from_usage",
            "per_million_usd": {model: MODEL_RATES[model] for model in models},
            "sources": [
                f"https://developers.openai.com/api/docs/models/{model}"
                for model in models
            ],
        },
    }


def run(
    case: Case, route: str, directory: Path, budget: Decimal, provider, *, corpus=None, pause=None
):
    if route not in ROUTES:
        raise CaseError("Choose a supported research route")
    corpus = corpus if corpus is not None else case.corpus()
    return execute(
        configuration(case, corpus, route),
        directory,
        budget,
        provider,
        lambda ask: research(case, corpus, ask, route=route),
        pause=pause,
    )


def release_state(memo, trace, *, review_required):
    digest = fingerprint(memo) if memo is not None else None
    review = trace.get("review")
    if review is None:
        if review_required:
            raise CaseError("This route requires a review decision before release")
        if memo is None:
            raise CaseError("A missing report must have a withheld decision")
        return "complete", digest
    rounds = review["rounds"]
    if review["released_memo_sha256"] != digest:
        raise CaseError("The report differs from the reviewed release")
    if (
        memo is None
        and review["status"] == "withheld"
        and rounds
        and not any(r["approved"] for r in rounds)
    ):
        return "withheld", None
    if (
        memo is not None
        and review["status"] == "approved"
        and rounds
        and rounds[-1]["approved"]
        and rounds[-1]["memo_sha256"] == digest
    ):
        return "complete", digest
    if (
        memo is not None
        and review["status"] == "no_financial_claims"
        and not rounds
        and trace.get("evidence") == {}
        and trace.get("coverage") == "qualify"
    ):
        return "complete", digest
    raise CaseError("No review decision authorizes this release")


def new_record(config, directory, budget):
    if not budget.is_finite() or budget <= 0:
        raise CaseError("Choose a positive finite USD budget")
    try:
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
        sync_directory(directory.parent)
    except FileExistsError as error:
        raise CaseError("Use a fresh output directory for a paid run") from error
    record = {
        **config,
        "run_id": directory.name,
        "state": "running",
        "budget_usd": format(budget, "f"),
        "created_at": datetime.now(UTC).isoformat(),
        "checkpoint_id": uuid4().hex,
    }
    record["specification_sha256"] = specification(config, record)
    return record


def queue_research(case, directory, budget):
    """Persist the accepted intake before an HTTP client receives its run ID."""
    config = configuration(case, case.corpus(), default_route(case))
    record = new_record(config, directory, budget)
    record["state"] = "queued"
    with own_run(directory):
        Meter(directory, record, None)
    return record


def execute(config, directory, budget, provider, workflow, *, artifact_name="memo.md", pause=None):
    record = new_record(config, directory, budget)
    with own_run(directory):
        meter = Meter(directory, record, provider, pause=pause)
        return finish(config, meter, workflow, artifact_name)


def finish(config, meter, workflow, artifact_name):
    record = meter.record
    try:
        memo, trace = workflow(meter)
        if meter.seen != set(record["calls"]) | set(record.get("skipped_calls", {})):
            raise CaseError("The workflow did not use every saved assignment")
        verify_ledger(record)
        state, digest = release_state(
            memo, trace, review_required="review_version" in config
        )
        record.update(state=state, memo_sha256=digest, **trace)
    except (PauseRequested, KeyboardInterrupt):
        record.update(state="paused")
        raise
    except Exception as error:
        record.update(
            state="failed",
            failure=str(error)
            if isinstance(error, CaseError)
            else type(error).__name__,
        )
        raise
    finally:
        meter.save()
    if memo is not None:
        publish(meter.directory / artifact_name, memo)
    return record


def saved_workflow(case, record, corpus):
    corpus = corpus if corpus is not None else case.corpus()
    route = record["route"]
    if route not in ROUTES:
        raise CaseError("Unknown research route")
    version = record.get("dispute_version", DISPUTE_VERSION)
    if version not in (DISPUTE_VERSION, LEGACY_VERSION):
        raise CaseError("Unknown dispute review version")
    return (
        configuration(case, corpus, route, dispute_version=version),
        lambda ask: research(case, corpus, ask, route=route, dispute_version=version),
    )


def replay(case: Case, record: dict, *, corpus=None):
    return reproduce(record, *saved_workflow(case, record, corpus))


def check_configuration(record, config):
    if any(record.get(k) != v for k, v in config.items()):
        raise CaseError("Saved configuration, case or corpus changed")
    if ("specification_sha256" in record or "checkpoint_id" in record) and (
        record.get("specification_sha256") != specification(config, record)
    ):
        raise CaseError("Saved run identity, configuration or budget changed")


def saved_case(record):
    if "collection" in record:
        case = analyst_case(record["question"], record["cutoff"], record["collection"])
        if case.id != record["case_id"]:
            raise CaseError("The saved analyst intake changed")
        return case
    for key in CASES:
        case = case_for(key)
        if case.id == record["case_id"]:
            return case
    raise CaseError("Unknown saved research case")


def resume_terminal(directory, record):
    memo = replay(saved_case(record), record)
    path = directory / "memo.md"
    if path.exists() or path.is_symlink():
        if read_saved_file(path) != memo:
            raise CaseError("The saved memo differs from the reviewed release")
    elif memo is not None:
        publish(path, memo)
    return record


def resume(directory, provider=None, *, pause=None):
    if directory.is_symlink() or not directory.is_dir():
        raise CaseError("Expected an existing local run directory")
    path = directory / "memo.md"
    record = load(directory / "run.json")
    if record["state"] == "withheld" or (record["state"] == "complete" and path.is_file()):
        return resume_terminal(directory, record)
    with own_run(directory):
        record = load(directory / "run.json")
        if record["state"] in ("complete", "withheld"):
            return resume_terminal(directory, record)
        case = saved_case(record)
        config, workflow = saved_workflow(case, record, case.corpus())
        check_configuration(record, config)
        if record["state"] not in ("queued", "running", "paused", "failed", "needs_reconciliation"):
            raise CaseError("Unknown saved run state")
        if path.exists() or path.is_symlink():
            raise CaseError("An unfinished run already has an exported memo")
        meter = Meter(directory, record, provider, continuing=True, pause=pause)
        if "checkpoint_id" not in record:
            # Older runs saved normalized results and operator IDs separately.
            record.update(checkpoint_id=uuid4().hex, legacy_settled=sorted(set(record["calls"]) - meter.pending.keys()))
            record["specification_sha256"] = specification(config, record)
            meter.save()
        prior = sorted(set(record["calls"]) - meter.pending.keys())
        recovered = meter.restore()
        if record["state"] != "queued":
            record.setdefault("resumptions", []).append(
                {
                    "at": datetime.now(UTC).isoformat(),
                    "already_settled": prior,
                    "recovered_receipts": recovered,
                }
            )
        record.pop("failure", None)
        record["state"] = "running"
        meter.save()
        return finish(config, meter, workflow, "memo.md")


def reproduce(record, config, workflow):
    if record.get("state") not in ("complete", "withheld"):
        raise CaseError("The run is incomplete")
    check_configuration(record, config)
    verify_ledger(record)
    seen = set()
    lock = Lock()

    def ask(stage, prompt, limit, *, keep_usd=None):
        skips = record.get("skipped_calls", {})
        with lock:
            entry = record["calls"].get(stage, skips.get(stage))
            if entry is None or stage in seen:
                raise CaseError("Missing or duplicate model call during replay")
            if keep_usd is not None:
                check_decision_order(record, stage, seen)
            seen.add(stage)
        return recorded_output(entry, stage, prompt, limit, keep_usd)

    memo, trace = workflow(ask)
    state, digest = release_state(
        memo, trace, review_required="review_version" in config
    )
    if (
        seen != set(record["calls"]) | set(record.get("skipped_calls", {}))
        or digest != record["memo_sha256"]
        or record["state"] != state
        or any(record.get(k) != v for k, v in trace.items())
    ):
        raise CaseError("Replayed evidence, decisions or memo changed")
    return memo


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="command", required=True)
    live = cmds.add_parser("run")
    intake = live.add_mutually_exclusive_group(required=True)
    intake.add_argument("--case", choices=CASES)
    intake.add_argument("--question", help="Research a question against a dated source collection")
    live.add_argument("--cutoff", help="UTC date or timestamp with timezone, for --question")
    live.add_argument("--collection", choices=COLLECTIONS, help="Source collection for --question (default: archive)")
    live.add_argument("--route", choices=ROUTES)
    live.add_argument("--budget-usd", default="0.05")
    live.add_argument("--output-dir", type=Path, required=True)
    saved = cmds.add_parser("replay")
    saved.add_argument("--run-dir", type=Path, required=True)
    cont = cmds.add_parser("resume", help="Continue this run under its original spending limit")
    cont.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command in ("run", "resume"):
            directory = args.output_dir if args.command == "run" else args.run_dir
            with pause_on_signal() as pause:
                if args.command == "resume":
                    record = resume(directory, pause=pause)
                else:
                    if args.case and (args.cutoff is not None or args.collection is not None):
                        raise CaseError("A predefined case supplies its own cutoff and source collection")
                    case = (
                        case_for(args.case) if args.case else
                        analyst_case(args.question, args.cutoff, args.collection or "archive")
                    )
                    record = run(
                        case,
                        args.route or default_route(case),
                        directory, Decimal(args.budget_usd), None, pause=pause,
                    )
            if record["state"] == "withheld":
                print(
                    f"Memo withheld after review. See {directory / 'run.json'}.",
                    file=sys.stderr,
                )
                return 2
            print(
                f"Saved {directory / 'memo.md'} in {record['elapsed_ms']} ms. Estimated USD {record['estimated_model_cost_usd']}."
            )
        else:
            record = load(args.run_dir / "run.json")
            memo = replay(saved_case(record), record)
            saved_memo = args.run_dir / "memo.md"
            if saved_memo.exists() and read_saved_file(saved_memo) != memo:
                raise CaseError("The saved memo differs from the replayed release")
            if memo is None:
                print("Replayed the withheld decision.", file=sys.stderr)
                return 2
            sys.stdout.buffer.write(memo)
    except PauseRequested:
        print(f"Run paused. Resume with: python -m scripts.research_team resume --run-dir {shlex.quote(str(directory))}", file=sys.stderr)
        return 130
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError, KeyError, InvalidOperation) as error:
        print(f"Research error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
