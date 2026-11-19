"""Reconstruct a failed paired assignment up to the recorded error or verified external boundary."""

import re
import subprocess
from decimal import Decimal
from threading import Lock

from scripts.disney_hulu_case import CaseError
from scripts.sol_provider import MILLION, MODEL_RATES, reservation_cost
from scripts.team_meter import check_decision_order, funded_limit, recorded_output

from .corpus import require


def verify_failure(record, packet, arm, workflow, environment=None):
    entries = {**record["calls"], **record.get("skipped_calls", {})}
    seen, mutex = set(), Lock()
    message = record["failure"]

    class VerifiedBoundary(Exception):
        pass

    def ask(stage, prompt, desired, *, keep_usd=None):
        with mutex:
            if stage not in entries:
                match = re.fullmatch(
                    r"Required reservation USD ([0-9.]+) exceeds budget USD ([0-9.]+)",
                    message,
                )
                if record["state"] != "budget_exhausted" or match is None:
                    raise AssertionError(
                        "An unexpected provider call would be made after the recorded failure"
                    )
                cost, remaining = map(Decimal, match.groups())
                exact_remaining = Decimal(record["budget_usd"]) - Decimal(
                    record["estimated_model_cost_usd"]
                )
                require(
                    remaining == exact_remaining
                    and cost > remaining
                    and keep_usd is None,
                    "Unsupported budget refusal",
                )
                rates = MODEL_RATES[prompt["model"]]
                # A refused generation above its case allowance uses the full requested output limit.
                count = (cost * MILLION - desired * Decimal(rates["output"])) / Decimal(
                    rates["cache_write_30m"]
                )
                require(
                    count >= 0
                    and count == int(count)
                    and funded_limit(
                        stage, prompt["model"], int(count), remaining, desired
                    )
                    == desired
                    and reservation_cost(int(count), desired, prompt["model"]) == cost,
                    "Reservation cannot be reconstructed from its reported upper bound",
                )
                raise VerifiedBoundary
            require(stage not in seen, "Duplicate failed-run dispatch")
            if keep_usd is not None:
                check_decision_order(record, stage, seen)
            entry = entries[stage]
            seen.add(stage)
            if "output" not in entry and "checked_ms" not in entry:
                recorded_output(
                    {**entry, "output": None}, stage, prompt, desired, keep_usd
                )
                require(
                    entry.get("provider") and record["state"] == "failed",
                    "A failed provider response has no receipt",
                )
                raise VerifiedBoundary
            return recorded_output(entry, stage, prompt, desired, keep_usd)

    if environment:
        require(
            environment["checkpoint_id"] == record["checkpoint_id"]
            and environment["exception"] == message == "TimeoutExpired",
            "Mismatched host incident",
        )

        def page_bytes(source, page):
            require(
                source["sha256"] == environment["source_sha256"]
                and int(page) == environment["page"],
                "Renderer failure occurred on another document or page",
            )
            raise subprocess.TimeoutExpired(
                environment["command"], environment["timeout_s"]
            )

        packet.page_bytes = page_bytes

    try:
        workflow(packet, arm, ask)
    except VerifiedBoundary:
        pass
    except (
        ValueError,
        KeyError,
        OSError,
        ArithmeticError,
        subprocess.TimeoutExpired,
    ) as exc:
        observed = str(exc) if isinstance(exc, CaseError) else type(exc).__name__
        require(observed == message, "Failed replay stopped at a different error")
        if isinstance(exc, subprocess.TimeoutExpired):
            require(environment is not None, "Missing record of the host timeout")
    else:
        raise ValueError("A failed assignment unexpectedly produced a research result")
    require(seen == set(entries), "Some failed-run requests could not be reproduced")
