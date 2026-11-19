"""One exclusive run directory and a shared spend limit for concurrent assignments."""

from decimal import Decimal
from pathlib import Path
from threading import Lock
from time import perf_counter

from .disney_hulu_case import CaseError
from .run_checkpoint import (
    PauseRequested,
    load,
    load_receipt,
    publish,
    receipt_path,
    save_receipt,
)
from .sol_baseline import PROJECT_ROOT, fingerprint, generation_request, json_bytes
from .sol_gateway import check_usage, request_record
from .sol_provider import (
    MILLION,
    MODEL_RATES,
    OpenAIResponses,
    project_key,
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


def recorded_output(call, stage, prompt, limit, keep_usd=None):
    payload = generation_request(prompt, max_output_tokens=call["max_output_tokens"])
    if (
        call["request_sha256"] != fingerprint(json_bytes(payload))
        or call["model"] != prompt["model"]
        or call.get("requested_output_tokens", call["max_output_tokens"]) != limit
        or call.get("protected_usd") != (str(keep_usd) if keep_usd is not None else None)
    ):
        raise CaseError(f"Request fingerprint or route changed for {stage}")
    if "output" not in call and "checked_ms" not in call:
        raise CaseError(f"Assignment {stage} has no saved output")
    return call.get("output")


def check_decision_order(record, stage, seen):
    # Conditional assignments run after the preceding parallel work has joined.
    event = "skip" if stage in record.get("skipped_calls", {}) else "reserve"
    offset = record["events"].index([event, stage])
    earlier = {s for action, s in record["events"][:offset] if action in ("settle", "skip")}
    if earlier != seen:
        raise CaseError("Conditional spending decision is out of order")


def provider_receipt(record, stage, call, response, tools, when):
    body = response.body
    provider = {k: body.get(k) for k in ("model", "status", "service_tier", "usage")}
    if body.get("incomplete_details"):
        provider["incomplete_details"] = body["incomplete_details"]
    ids = {"request_id": response.request_id, "response_id": body.get("id")}
    provider.update(elapsed_ms=response.elapsed_ms, ids_sha256=fingerprint(json_bytes(ids)))
    receipt = {
        "checkpoint_id": record.get("checkpoint_id"), "stage": stage,
        "request_sha256": call["request_sha256"], "received_ms": when,
        "provider": provider, "ids": ids,
    }
    try:
        receipt["output"] = tool_decision(body) if tools else structured_text(body)
    except (ValueError, KeyError, AttributeError, TypeError) as error:
        receipt["output_error"] = str(error) if isinstance(error, CaseError) else "Invalid provider output"
    return receipt


class Meter:
    def __init__(self, directory: Path, record: dict, provider: OpenAIResponses, *, continuing=False, pause=None):
        self.directory = directory
        self.record = record
        self.provider = provider
        self.budget = Decimal(record["budget_usd"])
        self.pause = pause
        self.seen = set()
        self.ids = {}
        self.lock = Lock()
        self.stopped = False
        self.start = perf_counter()
        if continuing:
            self.spent, self.pending = verify_ledger(record, partial=True)
            self.offset_ms = max([record.get("elapsed_ms", 0)] + [
                c.get(k, 0) for c in [*record["calls"].values(), *record.get("skipped_calls", {}).values()]
                for k in ("dispatched_ms", "confirmed_ms", "checked_ms")
            ])
        else:
            self.spent, self.pending, self.offset_ms = Decimal(0), {}, 0
            self.record.update(calls={}, events=[])
            self.save()

    def elapsed(self):
        return self.offset_ms + round((perf_counter() - self.start) * 1000)

    def save(self):
        self.record.update(
            elapsed_ms=self.elapsed(),
            estimated_model_cost_usd=format(self.spent, "f"),
            unreconciled_reservation_usd=format(sum(self.pending.values(), Decimal(0)), "f"),
        )
        publish(self.directory / "run.json", json_bytes(self.record))

    def check_dispatch(self):
        if self.pause is not None and self.pause.is_set():
            raise PauseRequested
        if self.stopped:
            raise CaseError("A stopped assignment cannot dispatch")

    def restore(self):
        """Reconcile all outstanding charges before permitting any new provider request."""
        ids_path = self.directory / "provider-ids.json"
        self.ids = load(ids_path) if ids_path.exists() else {}
        if set(self.ids) - self.record["calls"].keys():
            raise CaseError("Provider identifiers have no matching assignment")
        recovered = []
        for stage, call in self.record["calls"].items():
            path = receipt_path(self.directory, stage)
            if path.parent.is_symlink():
                raise CaseError("Invalid private receipts directory")
            if path.exists():
                receipt = load_receipt(path)
                if (
                    receipt["checkpoint_id"] != self.record.get("checkpoint_id")
                    or receipt["stage"] != stage or receipt["request_sha256"] != call["request_sha256"]
                    or fingerprint(json_bytes(receipt["ids"])) != receipt["provider"]["ids_sha256"]
                    or (stage in self.ids and self.ids[stage] != receipt["ids"])
                ):
                    raise CaseError(f"Private receipt does not match {stage}")
                if stage in self.pending:
                    self.settle(stage, receipt)
                    recovered.append(stage)
                elif (call.get("receipt_sha256") != fingerprint(json_bytes(receipt))
                      or call["provider"] != receipt["provider"] or call.get("output") != receipt.get("output")):
                    raise CaseError(f"Saved output or receipt changed for {stage}")
                self.ids[stage] = receipt["ids"]
            elif stage not in self.pending and stage not in self.record.get("legacy_settled", []):
                raise CaseError(f"Missing private provider receipt for {stage}")
            if stage not in self.pending:
                if "output" not in call:
                    raise CaseError(f"{stage} was billed without usable output; its charge is retained")
                if stage not in self.ids or fingerprint(json_bytes(self.ids[stage])) != call["provider"]["ids_sha256"]:
                    raise CaseError(f"Missing or inconsistent operator identifiers for {stage}")
        if self.pending:
            stages = ", ".join(sorted(self.pending))
            amount = sum(self.pending.values())
            self.record["state"] = "needs_reconciliation"
            self.save()
            raise CaseError(f"Unconfirmed provider dispatch ({stages}); USD {amount:f} remains reserved. Resume sent no new generation.")
        if self.ids:
            publish(ids_path, json_bytes(self.ids))
        return recovered

    def settle(self, stage, receipt):
        call = self.record["calls"][stage]
        provider = receipt["provider"]
        self.ids[stage] = receipt["ids"]
        publish(self.directory / "provider-ids.json", json_bytes(self.ids))
        self.offset_ms += max(0, receipt["received_ms"] - self.elapsed())
        call.update(provider=provider, confirmed_ms=self.elapsed(), receipt_sha256=fingerprint(json_bytes(receipt)))
        cost = usage_cost(provider, expected_model=call["model"])
        self.spent += cost
        del self.pending[stage]
        self.record["events"].append(["settle", stage])
        call["estimated_model_cost_usd"] = format(cost, "f")
        try:
            check_usage(call, cost)
            if "output_error" in receipt:
                raise CaseError(receipt["output_error"])
            call["output"] = receipt["output"]
        finally:
            self.save()

    def __call__(self, stage: str, prompt: dict, limit: int, *, keep_usd: Decimal | None = None):
        model = prompt["model"]
        with self.lock:
            self.check_dispatch()
            if stage in self.seen:
                raise CaseError(f"Duplicate assignment {stage} cannot dispatch")
            skips = self.record.get("skipped_calls", {})
            if stage in self.record["calls"] or stage in skips:
                if keep_usd is not None:
                    check_decision_order(self.record, stage, self.seen)
                self.seen.add(stage)
                entry = skips[stage] if stage in skips else self.record["calls"][stage]
                return recorded_output(entry, stage, prompt, limit, keep_usd)
            self.seen.add(stage)
            if self.provider is None:
                self.provider = OpenAIResponses(project_key(PROJECT_ROOT / ".env.local"))
        tokens = self.provider.count(prompt)
        with self.lock:
            self.check_dispatch()
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
                receipt = provider_receipt(self.record, stage, call, response, "tools" in prompt, self.elapsed())
                save_receipt(self.directory, receipt)
                self.settle(stage, receipt)
                if self.pause is not None and self.pause.is_set():
                    raise PauseRequested
                return call["output"]
        except Exception:
            with self.lock:
                self.stopped = True
                self.save()
            raise


def verify_ledger(record: dict, *, partial=False):
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
            if cost != Decimal(call["estimated_model_cost_usd"]) or (not partial and call["provider"]["status"] != "completed"):
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
        (pending and not partial)
        or dispatched != set(record["calls"])
        or skipped != set(record.get("skipped_calls", {}))
        or spent != Decimal(record["estimated_model_cost_usd"])
        or Decimal(record["unreconciled_reservation_usd"]) != sum(pending.values())
    ):
        raise CaseError("The saved ledger does not reconcile")
    return spent, pending
