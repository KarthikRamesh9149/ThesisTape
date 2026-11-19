"""One exclusive run directory and a shared spend limit for concurrent assignments."""

from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import perf_counter

from .disney_hulu_case import CaseError
from .sol_baseline import fingerprint, generation_request, json_bytes, save_run
from .sol_gateway import check_usage, request_record
from .sol_provider import (
    MILLION,
    MODEL_RATES,
    OpenAIResponses,
    reservation_cost,
    structured_text,
    usage_cost,
)
from .sol_research import tool_decision


def funded_limit(stage, model, input_tokens, available, desired):
    if stage not in ("lead.memo", "lead.memo_repair", "lead.revise", "review.initial", "review.recheck",
                     "forecast.analyze", "forecast.repair", "dispute.adjudicate"):
        return desired
    rates = MODEL_RATES[model]
    affordable = int(
        (available * MILLION - input_tokens * Decimal(rates["cache_write_30m"])) / Decimal(rates["output"])
    )
    # Keep enough output for the structured report or review.
    return min(desired, affordable) if affordable >= min(768, desired) else desired


class Meter:
    def __init__(self, directory: Path, record: dict, provider: OpenAIResponses):
        self.directory = directory
        self.record = record
        self.provider = provider
        self.budget = Decimal(record["budget_usd"])
        self.spent = Decimal(0)
        self.pending: dict[str, Decimal] = {}
        self.ids = {}
        self.lock = Lock()
        self.stopped = False
        self.start = perf_counter()
        self.record.update(
            calls={}, events=[], estimated_model_cost_usd="0", unreconciled_reservation_usd="0"
        )
        self.save()

    def elapsed(self):
        return round((perf_counter() - self.start) * 1000)

    def save(self):
        self.record.update(
            estimated_model_cost_usd=format(self.spent, "f"),
            unreconciled_reservation_usd=format(sum(self.pending.values(), Decimal(0)), "f"),
        )
        save_run(self.directory, self.record)

    def __call__(self, stage: str, prompt: dict, limit: int, *, keep_usd: Decimal | None = None):
        model = prompt["model"]
        tokens = self.provider.count(prompt)
        with self.lock:
            if self.stopped or stage in self.record["calls"] or stage in self.record.get("skipped_calls", {}):
                raise CaseError("A stopped or duplicate assignment cannot dispatch")
            available = self.budget - self.spent - sum(self.pending.values())
            if keep_usd is not None:
                if not keep_usd.is_finite() or keep_usd < 0:
                    raise CaseError("Invalid protected review allowance")
                available -= keep_usd
            granted = funded_limit(stage, model, tokens, available, limit)
            payload = generation_request(prompt, max_output_tokens=granted)
            if keep_usd is not None and reservation_cost(tokens, granted, model) > available:
                self.record.setdefault("skipped_calls", {})[stage] = {
                    "stage": stage, "model": model, "input_tokens_preflight": tokens,
                    "max_output_tokens": granted, "requested_output_tokens": limit,
                    "request_sha256": fingerprint(json_bytes(payload)), "protected_usd": str(keep_usd),
                    "checked_ms": self.elapsed(),
                }
                self.record["events"].append(["skip", stage])
                self.save()
                return None
            call = request_record(stage, payload, tokens, available)
            if keep_usd is not None:
                call["protected_usd"] = str(keep_usd)
            if limit != granted:
                call["requested_output_tokens"] = limit
            self.pending[stage] = Decimal(call["reservation_usd"])
            self.record["calls"][stage] = call
            self.record["events"].append(["reserve", stage])
            call.update(model=model, dispatched_ms=self.elapsed())
            self.save()
        try:
            response = self.provider.generate(payload)
            with self.lock:
                body = response.body
                receipt = {k: body.get(k) for k in ("model", "status", "service_tier", "usage")}
                if body.get("incomplete_details"):
                    receipt["incomplete_details"] = body["incomplete_details"]
                ids = {"request_id": response.request_id, "response_id": body.get("id")}
                self.ids[stage] = ids
                receipt.update(elapsed_ms=response.elapsed_ms, ids_sha256=fingerprint(json_bytes(ids)))
                call.update(provider=receipt, confirmed_ms=self.elapsed())
                try:
                    cost = usage_cost(body, expected_model=model)
                    self.spent += cost
                    del self.pending[stage]
                    self.record["events"].append(["settle", stage])
                    call["estimated_model_cost_usd"] = format(cost, "f")
                finally:
                    self.save()
                    ids_path = self.directory / ".provider-ids.tmp"
                    ids_path.write_bytes(json_bytes(self.ids))
                    ids_path.replace(self.directory / "provider-ids.json")
                check_usage(call, cost)
                call["output"] = tool_decision(body) if "tools" in prompt else structured_text(body)
                self.save()
                return call["output"]
        except Exception:
            with self.lock:
                self.stopped = True
                self.save()
            raise


def verify_ledger(record: dict) -> None:
    budget = Decimal(record["budget_usd"])
    if not budget.is_finite() or budget <= 0:
        raise CaseError("Invalid run budget")
    spent = Decimal(0)
    pending = {}
    dispatched = set()
    skipped = set()
    last_ms = 0
    for event, stage in record["events"]:
        entries = record.get("skipped_calls", {}) if event == "skip" else record["calls"]
        if stage not in entries:
            raise CaseError("Ledger event has no provider call")
        call = entries[stage]
        model = call["model"]
        if model not in MODEL_RATES or call["stage"] != stage:
            raise CaseError("Invalid route or model in the ledger")
        if event in ("reserve", "skip") and stage not in dispatched | skipped:
            protected = Decimal(call.get("protected_usd", "0"))
            if not protected.is_finite() or protected < 0:
                raise CaseError("Invalid protected review allowance")
            available = budget - spent - sum(pending.values()) - protected
            requested = call.get("requested_output_tokens", call["max_output_tokens"])
            granted = funded_limit(stage, model, call["input_tokens_preflight"], available, requested)
            if granted != call["max_output_tokens"]:
                raise CaseError("The output allowance differs from the remaining budget")
            amount = reservation_cost(call["input_tokens_preflight"], granted, model)
            if event == "skip":
                if "protected_usd" not in call or amount <= available:
                    raise CaseError("Skipped assignment was affordable")
                skipped.add(stage)
            else:
                if amount > available or amount != Decimal(call["reservation_usd"]):
                    raise CaseError("Saved reservation differs from the applicable model rates or budget")
                pending[stage] = amount
                dispatched.add(stage)
        elif event == "settle" and stage in pending:
            cost = usage_cost(call["provider"], expected_model=model)
            check_usage(call, cost)
            if cost != Decimal(call["estimated_model_cost_usd"]) or call["provider"]["status"] != "completed":
                raise CaseError("Provider receipt does not support the saved cost or completion")
            spent += cost
            del pending[stage]
        else:
            raise CaseError("Out-of-order or duplicate spend event")
        stamp = call[{"reserve": "dispatched_ms", "settle": "confirmed_ms", "skip": "checked_ms"}[event]]
        if type(stamp) is not int or stamp < last_ms or stamp > record.get("elapsed_ms", stamp):
            raise CaseError("Provider event timing does not agree with the run clock")
        if event == "settle":
            duration = call["provider"]["elapsed_ms"]
            if type(duration) is not int or not 0 <= duration <= stamp - call["dispatched_ms"] + 2:
                raise CaseError("Provider latency exceeds the measured call interval")
        last_ms = stamp
    if (
        pending
        or dispatched != set(record["calls"])
        or skipped != set(record.get("skipped_calls", {}))
        or spent != Decimal(record["estimated_model_cost_usd"])
        or Decimal(record["unreconciled_reservation_usd"]) != 0
    ):
        raise CaseError("The saved ledger does not reconcile")
