"""Meter and replay one Sol analyst working through the shared dated archive."""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from time import perf_counter

from .baseline_evidence import ResearchCase
from .disney_hulu_case import CaseError
from .retrieval_gateway import Corpus
from .sol_baseline import fingerprint, generation_request, json_bytes, save_run
from .sol_provider import (
    PRICING_URL,
    RATES,
    OpenAIResponses,
    reserve,
    structured_text,
    usage_cost,
)
from .sol_research import PROMPT_VERSION, research, tool_decision


def configuration(case: ResearchCase, corpus: Corpus) -> dict:
    return {
        "schema_version": 2,
        "prompt_version": PROMPT_VERSION,
        "case_id": case.id,
        "question": case.question,
        "cutoff": case.cutoff.isoformat(),
        "corpus_sha256": corpus.digest,
        "pricing": {"status": "estimate_from_usage", "per_million_usd": RATES, "source_url": PRICING_URL},
        "paid_tool_charges_usd": "0",
    }


def request_record(stage, payload, input_tokens, budget_remaining) -> dict:
    limit = payload["max_output_tokens"]
    reservation = reserve(input_tokens, budget_remaining, max_output_tokens=limit, model=payload["model"])
    return {
        "stage": stage,
        "request_sha256": fingerprint(json_bytes(payload)),
        "max_output_tokens": limit,
        "input_tokens_preflight": input_tokens,
        "reservation_usd": format(reservation, "f"),
    }


def check_usage(call, cost):
    if (
        cost > Decimal(call["reservation_usd"])
        or call["provider"]["usage"]["output_tokens"] > call["max_output_tokens"]
    ):
        raise CaseError("Provider usage exceeded the authorized reservation or output limit")


def run(
    case: ResearchCase, destination: Path, budget: Decimal, provider: OpenAIResponses, *, corpus=None
) -> dict:
    start = perf_counter()
    if not budget.is_finite() or budget <= 0:
        raise CaseError("The USD budget must be a positive finite amount")
    corpus = corpus if corpus is not None else Corpus()
    try:
        destination.mkdir(parents=True, mode=0o700, exist_ok=False)
    except FileExistsError as error:
        raise CaseError("Use a new directory to avoid dispatching an existing run twice") from error
    record = {
        **configuration(case, corpus),
        "run_id": destination.name,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "state": "running",
        "budget_usd": format(budget, "f"),
        "calls": [],
        "estimated_model_cost_usd": "0",
        "unreconciled_reservation_usd": "0",
    }
    save_run(destination, record)
    provider_ids = []
    spent = Decimal(0)

    def ask(stage, prompt, limit):
        nonlocal spent
        payload = generation_request(prompt, max_output_tokens=limit)
        call = request_record(stage, payload, provider.count(prompt), budget - spent)
        record["calls"].append(call)
        record["unreconciled_reservation_usd"] = call["reservation_usd"]
        save_run(destination, record)
        result = provider.generate(payload)
        body = result.body
        call["provider"] = {field: body.get(field) for field in ("model", "status", "service_tier", "usage")}
        call["provider"]["elapsed_ms"] = result.elapsed_ms
        ids = {"request_id": result.request_id, "response_id": body.get("id")}
        call["provider"]["ids_sha256"] = fingerprint(json_bytes(ids))
        provider_ids.append(ids)
        try:
            cost = usage_cost(body)
            spent += cost
            call["estimated_model_cost_usd"] = format(cost, "f")
            record.update(estimated_model_cost_usd=format(spent, "f"), unreconciled_reservation_usd="0")
        finally:
            save_run(destination, record)
            # Keep identifiers for billing reconciliation even if pricing or validation fails.
            ids_file = destination / ".provider-ids.tmp"
            ids_file.write_bytes(json_bytes(provider_ids))
            ids_file.replace(destination / "provider-ids.json")
        check_usage(call, cost)
        call["output"] = tool_decision(body) if stage == "discovery" else structured_text(body)
        save_run(destination, record)
        return call["output"]

    try:
        memo, evidence = research(case, corpus, ask)
        temporary = destination / ".memo.tmp"
        temporary.write_bytes(memo)
        temporary.replace(destination / "memo.md")
        record.update(state="complete", memo_sha256=fingerprint(memo), **evidence)
    except (ValueError, TypeError, KeyError, AttributeError, OSError) as error:
        record.update(
            state="failed",
            failure=str(error)
            if isinstance(error, (ValueError, OSError))
            else "Invalid provider response shape",
        )
        raise CaseError(record["failure"]) from None
    finally:
        record["elapsed_ms"] = round((perf_counter() - start) * 1000)
        save_run(destination, record)
    return record


def replay(case: ResearchCase, record: dict, *, corpus=None) -> bytes:
    corpus = corpus if corpus is not None else Corpus()
    if record.get("state") != "complete" or any(
        record.get(k) != v for k, v in configuration(case, corpus).items()
    ):
        raise CaseError("The saved gateway run is incomplete or its case, corpus, pricing or prompt changed")
    spent = Decimal(0)
    budget = Decimal(record["budget_usd"])
    if not isinstance(record.get("calls"), list):
        raise CaseError("Expected a list of model calls in gateway replay")
    remaining = iter(record["calls"])
    exhausted = object()

    def ask(stage, prompt, limit):
        nonlocal spent
        call = next(remaining, None)
        if not isinstance(call, dict):
            raise CaseError("Missing model call in gateway replay")
        expected = request_record(
            stage,
            generation_request(prompt, max_output_tokens=limit),
            call["input_tokens_preflight"],
            budget - spent,
        )
        if any(call.get(k) != v for k, v in expected.items()) or call["provider"]["status"] != "completed":
            raise CaseError("The model request or response status changed during replay")
        cost = usage_cost(call["provider"])
        check_usage(call, cost)
        if cost != Decimal(call["estimated_model_cost_usd"]):
            raise CaseError("The saved call cost disagrees with provider usage")
        spent += cost
        return call["output"]

    memo, evidence = research(case, corpus, ask)
    if (
        next(remaining, exhausted) is not exhausted
        or spent != Decimal(record["estimated_model_cost_usd"])
        or Decimal(record["unreconciled_reservation_usd"]) != 0
    ):
        raise CaseError("The saved model calls or their total spend do not agree")
    if any(record.get(k) != v for k, v in evidence.items()) or fingerprint(memo) != record["memo_sha256"]:
        raise CaseError("The lookup, sources, calculation or final memo changed during replay")
    return memo
