"""Run or replay the Sol-led financial research team."""

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock

from .claim_review import VERSION as REVIEW_VERSION
from .disney_hulu_case import CaseError, read_saved_file
from .forecast_specialist import VERSION as FORECAST_VERSION
from .material_dispute import VERSION as DISPUTE_VERSION
from .sol_baseline import PROJECT_ROOT, fingerprint, generation_request, json_bytes
from .sol_provider import (
    ASTRA,
    LUNA,
    MODEL,
    MODEL_RATES,
    TERRA,
    OpenAIResponses,
    project_key,
)
from .team_meter import Meter, verify_ledger
from .team_research import VERSION, Case, case_for, research
from .visual_reader import ROUTES as TABLE_ROUTES
from .visual_reader import VERSION as VISUAL_VERSION

CASES = ("disney", "tradeweb", "tradeweb-before", "toyota", "nvidia", "toyota-buybacks", "toyota-buybacks-actual",
         "twitter-deal", "twitter-price")
ROUTES = ("team", "single", "reviewed", "forecast", "dispute", *TABLE_ROUTES)


def configuration(case, corpus, route):
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
        **({"dispute_version": DISPUTE_VERSION} if route == "dispute" or case.source_urls else {}),
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
    case: Case, route: str, directory: Path, budget: Decimal, provider, *, corpus=None
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


def execute(config, directory, budget, provider, workflow, *, artifact_name="memo.md"):
    if not budget.is_finite() or budget <= 0:
        raise CaseError("Choose a positive finite USD budget")
    try:
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError as error:
        raise CaseError("Use a fresh output directory for a paid run") from error
    record = {
        **config,
        "run_id": directory.name,
        "state": "running",
        "budget_usd": format(budget, "f"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    meter = Meter(directory, record, provider)
    try:
        memo, trace = workflow(meter)
        verify_ledger(record)
        state, digest = release_state(
            memo, trace, review_required="review_version" in config
        )
        record.update(state=state, memo_sha256=digest, **trace)
        if memo is not None:
            tmp = directory / ".memo.tmp"
            tmp.write_bytes(memo)
            meter.save()
            tmp.replace(directory / artifact_name)
    except Exception as error:
        record.update(
            state="failed",
            failure=str(error)
            if isinstance(error, CaseError)
            else type(error).__name__,
        )
        raise
    finally:
        record["elapsed_ms"] = meter.elapsed()
        meter.save()
    return record


def replay(case: Case, record: dict, *, corpus=None):
    corpus = corpus if corpus is not None else case.corpus()
    route = record["route"]
    if route not in ROUTES:
        raise CaseError("Unknown research route")
    return reproduce(
        record,
        configuration(case, corpus, route),
        lambda ask: research(case, corpus, ask, route=route),
    )


def recorded_output(call, stage, prompt, limit):
    payload = generation_request(prompt, max_output_tokens=call["max_output_tokens"])
    if (
        call["request_sha256"] != fingerprint(json_bytes(payload))
        or call["model"] != prompt["model"]
        or call.get("requested_output_tokens", call["max_output_tokens"]) != limit
    ):
        raise CaseError(f"Request fingerprint or route changed for {stage}")
    return call["output"]


def reproduce(record, config, workflow):
    if record.get("state") not in ("complete", "withheld") or any(
        record.get(k) != v for k, v in config.items()
    ):
        raise CaseError(
            "The run is incomplete or its configuration, case or corpus changed"
        )
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
                # Conditional spending decisions run after the earlier assignments have joined.
                dispatch = "skip" if stage in skips else "reserve"
                offset = record["events"].index([dispatch, stage])
                earlier = {s for event, s in record["events"][:offset] if event in ("settle", "skip")}
                if earlier != seen:
                    raise CaseError("Conditional spending decision is out of order")
            seen.add(stage)
        if entry.get("protected_usd") != (str(keep_usd) if keep_usd is not None else None):
            raise CaseError("Protected review allowance changed")
        return recorded_output({**entry, "output": None} if stage in skips else entry, stage, prompt, limit)

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
    live.add_argument("--case", choices=CASES, required=True)
    live.add_argument("--route", choices=ROUTES)
    live.add_argument("--budget-usd", default="0.05")
    live.add_argument("--output-dir", type=Path, required=True)
    saved = cmds.add_parser("replay")
    saved.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            provider = OpenAIResponses(project_key(PROJECT_ROOT / ".env.local"))
            case = case_for(args.case)
            record = run(
                case,
                args.route or ("visual" if case.table else "dispute" if case.source_urls else "team"),
                args.output_dir,
                Decimal(args.budget_usd),
                provider,
            )
            if record["state"] == "withheld":
                print(
                    f"Memo withheld after review. See {args.output_dir / 'run.json'}.",
                    file=sys.stderr,
                )
                return 2
            print(
                f"Saved {args.output_dir / 'memo.md'} in {record['elapsed_ms']} ms. Estimated USD {record['estimated_model_cost_usd']}."
            )
        else:
            record = json.loads(read_saved_file(args.run_dir / "run.json"))
            for key in CASES:
                case = case_for(key)
                if case.id == record["case_id"]:
                    memo = replay(case, record)
                    saved_memo = args.run_dir / "memo.md"
                    if saved_memo.exists() and (
                        memo is None or read_saved_file(saved_memo) != memo
                    ):
                        raise CaseError(
                            "The saved memo differs from the replayed release"
                        )
                    if memo is None:
                        print("Replayed the withheld decision.", file=sys.stderr)
                        return 2
                    sys.stdout.buffer.write(memo)
                    break
            else:
                raise CaseError("Unknown saved case")
    except (OSError, ValueError, KeyError, InvalidOperation) as error:
        print(f"Research error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
