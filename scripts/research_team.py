"""Run or replay the Sol-led financial research team."""

import argparse
import json
import sys
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from threading import Lock

from .disney_hulu_case import CaseError, read_saved_file
from .retrieval_gateway import Corpus
from .sol_baseline import PROJECT_ROOT, fingerprint, generation_request, json_bytes
from .sol_provider import MODEL_RATES, OpenAIResponses, project_key
from .team_meter import Meter, verify_ledger
from .team_research import VERSION, Case, case_for, research


def configuration(case, corpus, route):
    return {
        "schema_version": 3,
        "prompt_version": VERSION,
        "case_id": case.id,
        **case.intake(),
        "route": route,
        "corpus_sha256": corpus.digest,
        "paid_tool_charges_usd": "0",
        "pricing": {
            "status": "estimate_from_usage",
            "per_million_usd": MODEL_RATES,
            "sources": [f"https://developers.openai.com/api/docs/models/{model}" for model in MODEL_RATES],
        },
    }


def run(case: Case, route: str, directory: Path, budget: Decimal, provider, *, corpus=None):
    if route not in ("team", "single") or not budget.is_finite() or budget <= 0:
        raise CaseError("Choose a supported route and a positive finite USD budget")
    corpus = corpus if corpus is not None else Corpus()
    try:
        directory.mkdir(parents=True, exist_ok=False, mode=0o700)
    except FileExistsError as error:
        raise CaseError("Use a fresh output directory for a paid run") from error
    record = {
        **configuration(case, corpus, route),
        "run_id": directory.name,
        "state": "running",
        "budget_usd": format(budget, "f"),
        "created_at": datetime.now(UTC).isoformat(),
    }
    meter = Meter(directory, record, provider)
    try:
        memo, trace = research(case, corpus, meter, route=route)
        verify_ledger(record)
        tmp = directory / ".memo.tmp"
        tmp.write_bytes(memo)
        tmp.replace(directory / "memo.md")
        record.update(state="complete", memo_sha256=fingerprint(memo), **trace)
    except Exception as error:
        record.update(
            state="failed", failure=str(error) if isinstance(error, CaseError) else type(error).__name__
        )
        raise
    finally:
        record["elapsed_ms"] = meter.elapsed()
        meter.save()
    return record


def replay(case: Case, record: dict, *, corpus=None):
    corpus = corpus if corpus is not None else Corpus()
    route = record["route"]
    if record.get("state") != "complete" or any(
        record.get(k) != v for k, v in configuration(case, corpus, route).items()
    ):
        raise CaseError("The run is incomplete or its configuration, case or corpus changed")
    verify_ledger(record)
    seen = set()
    lock = Lock()

    def ask(stage, prompt, limit):
        with lock:
            if stage not in record["calls"] or stage in seen:
                raise CaseError("Missing or duplicate model call during replay")
            seen.add(stage)
        call = record["calls"][stage]
        payload = generation_request(prompt, max_output_tokens=call["max_output_tokens"])
        if (
            call["request_sha256"] != fingerprint(json_bytes(payload))
            or call["model"] != prompt["model"]
            or call.get("requested_output_tokens", call["max_output_tokens"]) != limit
        ):
            raise CaseError(f"Request fingerprint or route changed for {stage}")
        return call["output"]

    memo, trace = research(case, corpus, ask, route=route)
    if (
        seen != set(record["calls"])
        or fingerprint(memo) != record["memo_sha256"]
        or any(record.get(k) != v for k, v in trace.items())
    ):
        raise CaseError("Replayed evidence, decisions or memo changed")
    return memo


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    cmds = parser.add_subparsers(dest="command", required=True)
    live = cmds.add_parser("run")
    live.add_argument("--case", choices=("disney", "tradeweb", "tradeweb-before"), required=True)
    live.add_argument("--route", choices=("team", "single"), default="team")
    live.add_argument("--budget-usd", default="0.05")
    live.add_argument("--output-dir", type=Path, required=True)
    saved = cmds.add_parser("replay")
    saved.add_argument("--run-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "run":
            provider = OpenAIResponses(project_key(PROJECT_ROOT / ".env.local"))
            record = run(case_for(args.case), args.route, args.output_dir, Decimal(args.budget_usd), provider)
            print(
                f"Saved {args.output_dir / 'memo.md'} in {record['elapsed_ms']} ms. Estimated USD {record['estimated_model_cost_usd']}."
            )
        else:
            record = json.loads(read_saved_file(args.run_dir / "run.json"))
            for key in ("disney", "tradeweb", "tradeweb-before"):
                case = case_for(key)
                if case.id == record["case_id"]:
                    sys.stdout.buffer.write(replay(case, record))
                    break
            else:
                raise CaseError("Unknown saved case")
    except (OSError, ValueError, KeyError, InvalidOperation) as error:
        print(f"Research error: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
