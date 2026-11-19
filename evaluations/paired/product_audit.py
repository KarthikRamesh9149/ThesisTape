"""Verify the exact requests, including a budget refusal that generated no response."""

from decimal import Decimal
from threading import Lock

from scripts import research_desk_agent as product
from scripts.disney_hulu_case import CaseError
from scripts.packet_archive import SavedSearches
from scripts.run_checkpoint import load, publish
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import BudgetExhausted, reserve
from scripts.team_meter import funded_limit, recorded_output, verify_ledger

from .corpus import require


class CountJournal:
    def __init__(self, folder, provider):
        self.folder = folder
        self.provider = provider

    def count(self, prompt):
        key = fingerprint(json_bytes(prompt))
        path = self.folder / (key + ".json")
        self.folder.mkdir(parents=True, exist_ok=True)
        if path.is_file():
            row = load(path)
            require(
                row["prompt_sha256"] == key and type(row["tokens"]) is int,
                "Invalid saved token count",
            )
            return row["tokens"]
        tokens = self.provider.count(prompt)
        publish(path, json_bytes({"prompt_sha256": key, "tokens": tokens}))
        return tokens

    def generate(self, payload):
        return self.provider.generate(payload)


def replay_failure(folder, rec, gateway):
    entries = {**rec["calls"], **rec.get("skipped_calls", {})}
    spent, pending = verify_ledger(rec, partial=True)
    require(not pending, "Resolve outstanding dispatches before replaying")
    seen, mutex = set(), Lock()

    class Unusable(Exception):
        pass

    def ask(stage, prompt, limit, *, keep_usd=None):
        with mutex:
            if stage in entries:
                require(stage not in seen, "Duplicate production assignment")
                entry = entries[stage]
                seen.add(stage)
                if "output" not in entry and "checked_ms" not in entry:
                    recorded_output(
                        {**entry, "output": None}, stage, prompt, limit, keep_usd
                    )
                    raise Unusable
                return recorded_output(entry, stage, prompt, limit, keep_usd)
            key = fingerprint(json_bytes(prompt))
            path = folder / "counts" / (key + ".json")
            if not path.is_file():
                require(
                    rec["failure"].startswith("OpenAI responses/input_tokens "),
                    "Missing preflight evidence",
                )
                raise CaseError(rec["failure"])
            row = load(path)
            require(
                row["prompt_sha256"] == key and type(row["tokens"]) is int,
                "Mismatched token count",
            )
            available = Decimal(rec["budget_usd"]) - spent - (keep_usd or 0)
            if available <= 0:
                raise BudgetExhausted(
                    "The assignment has no unreserved funds available"
                )
            granted = funded_limit(
                stage, prompt["model"], row["tokens"], available, limit
            )
            reserve(
                row["tokens"],
                available,
                max_output_tokens=granted,
                model=prompt["model"],
            )
            raise AssertionError("The undispatched request is affordable on replay")

    try:
        product.research(
            rec["question"],
            rec["cutoff"],
            SavedSearches(gateway, folder / "searches", offline=True),
            ask,
            profile=rec["research_profile"],
        )
    except Unusable:
        pass
    except (ValueError, ArithmeticError, OSError) as exc:
        observed = str(exc) if isinstance(exc, CaseError) else type(exc).__name__
        require(
            observed == rec["failure"],
            "The terminal error differs from its saved failure",
        )
    else:
        raise ValueError("A failed assignment produced a report during offline replay")
    require(
        seen == set(entries), "Offline replay did not verify all production requests"
    )
