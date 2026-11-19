"""Allocate the remaining independent investigations using measured costs and fixed research profiles."""

import argparse
import json
import re
from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path

from evaluations.paired.confirmation import write_once
from evaluations.paired.corpus import require
from evaluations.paired.registration import REPO
from scripts import research_desk_agent
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes

from . import (
    archive_failure,
    budget,
    budget_publication,
    editorial,
    recovery,
    runner,
    titled_publication,
)

POLICY = Path(__file__).with_name("recovery-budget-policy.md")
ALLOCATION = REPO / "runs/financegym-budget/recovery-allocation.json"
FILENAME = "recovery-allocation.json"
ALTERNATE = "mixed"
CAP = "0.30"
EXPECTED = Decimal("0.23")
BUFFER = Decimal("0.30")
ordinary_route = recovery.second_route
ordinary_ticket = recovery.ticket


def policy_sha():
    return fingerprint(
        json_bytes(
            {
                p.name: fingerprint(p.read_bytes())
                for p in (
                    POLICY,
                    Path(__file__),
                    POLICY.with_name("recovery-reference-costs.json"),
                )
            }
        )
    )


def select(cases, available, observed):
    projected = Decimal(len(cases)) * observed + BUFFER
    ranked = sorted(
        cases,
        key=lambda key: (
            -cases[key]["publisher_editions"],
            -cases[key]["source_editions"],
            key,
        ),
    )
    choices = []
    for key in ranked:
        if projected <= available or observed <= EXPECTED:
            break
        c = cases[key]
        if (
            c["unfunded_revision"]
            and c["publisher_editions"] > 0
            and c["source_editions"] >= 2
        ):
            choices.append(key)
            projected -= observed - EXPECTED
    return sorted(choices)


def candidate_record(folder):
    rec = load(folder / "run.json")
    docs = {
        s["url"].split("#:~:text=")[0]: s["kind"]
        for p in (folder / "searches").glob("*.json")
        for s in load(p)["body"]["sources"]
    }
    return {
        "original_run_sha256": fingerprint((folder / "run.json").read_bytes()),
        "source_editions": len(docs),
        "publisher_editions": sum(
            k in {"wayback", "sec_filing", "sec_xbrl"} for k in docs.values()
        ),
        "unfunded_revision": rec["state"] == "failed"
        and str(rec.get("failure", "")).startswith("Required reservation USD")
        and "lead.memo" in rec["calls"]
        and "lead.revise" not in rec["calls"],
    }


def validate(value):
    require(
        value["version"] == 1
        and type(value.get("reference_only")) is bool
        and value["policy_sha256"] == policy_sha(),
        "Independent research budget policy differs",
    )
    b = value["budget"]
    avail, charged, held = (
        Decimal(b[k])
        for k in ("available_usd", "settled_estimate_usd", "outstanding_provider_usd")
    )
    require(
        all(amount.is_finite() for amount in (avail, charged, held))
        and Decimal(b["owner_limit_usd"]) == budget.OWNER_LIMIT
        and Decimal(b["admission_limit_usd"]) == budget.ADMISSION_LIMIT
        and Decimal(b["active_full_allowances_usd"]) == 0
        and avail == budget.ADMISSION_LIMIT - charged - held
        and min(avail, charged, held) >= 0,
        "Independent allocation has a different available balance",
    )
    prior = value["observed_independent_costs_usd"]
    require(
        prior
        and all(re.fullmatch("[a-z0-9]{24}", key) for key in prior)
        and all(Decimal(v).is_finite() and Decimal(v) >= 0 for v in prior.values()),
        "Missing or invalid independent research cost history",
    )
    observed = sum(Decimal(x) for x in prior.values()) / len(prior)
    cases = value["unanswered"]
    require(
        (
            prior == reference_costs()
            if value["reference_only"]
            else not set(cases).intersection(prior)
        )
        and all(re.fullmatch("[a-z0-9]{24}", key) for key in cases)
        and all(
            type(c["unfunded_revision"]) is bool
            and re.fullmatch("[0-9a-f]{64}", c["original_run_sha256"])
            and type(c["publisher_editions"]) is int
            and type(c["source_editions"]) is int
            and 0 <= c["publisher_editions"] <= c["source_editions"]
            for c in cases.values()
        ),
        "Invalid original sources or repeated independent assignment",
    )
    require(
        value["alternate_tasks"] == select(cases, avail, observed),
        "Selected independent researchers differ from the measured budget rule",
    )
    return value


def verify_accounts(plan, cfg, proofs):
    by_id = {p["task_id"]: p for p in proofs}
    require(
        len(by_id) == len(proofs) and set(by_id) == set(cfg["profiles_by_task"]),
        "The funding audit must include every original question exactly once",
    )
    prior = plan["observed_independent_costs_usd"]
    known = set() if plan["reference_only"] else set(prior)
    if plan["reference_only"]:
        require(prior == reference_costs(), "Historical comparison charges changed")
    cases = plan["unanswered"]
    secondary = {key for key, p in by_id.items() if len(p.get("attempts", ())) == 2}
    require(
        known | set(cases) == secondary and not known.intersection(cases),
        "The budget allocation lost or added an independent investigation",
    )
    paid = Decimal(cfg["recorded_prior_project_usd"])
    reserved = Decimal(cfg["outstanding_prior_reservations_usd"])
    for key, item in by_id.items():
        stages = [item["attempts"][0]]
        if "primary_draft_review" in item:
            stages.append(item["primary_draft_review"])
        if key in known:
            extras = [item["attempts"][1]]
            if "editorial" in item:
                extras.append(item["editorial"])
            require(
                sum(Decimal(a["estimated_cost_usd"]) for a in extras)
                == Decimal(prior[key]),
                "An independent reference expense differs from its own ledger",
            )
            stages += extras
        if key in cases:
            first = item["attempts"][0]
            require(
                first["original_run_sha256"] == cases[key]["original_run_sha256"]
                and first["eligible_sources"] == cases[key]["source_editions"]
                and first["report_sha256"] is None,
                "Budget decision used a different primary investigation",
            )
        require(
            item.get("independent_budget_route") == tag(plan, key),
            "Incorrectly attributed budget route",
        )
        paid += sum(Decimal(a["estimated_cost_usd"]) for a in stages)
        reserved += sum(Decimal(a["outstanding_usd"]) for a in stages)
    require(
        paid == Decimal(plan["budget"]["settled_estimate_usd"])
        and reserved == Decimal(plan["budget"]["outstanding_provider_usd"]),
        "The amount available when research was allocated differs from the original bills",
    )


def prepare(questions, path=ALLOCATION):
    if path.is_file():
        return validate(load(path))
    cfg = runner.locked()
    ext = recovery.register(cfg)
    spec = budget_publication.locked(
        titled_publication.locked(editorial.locked(cfg, ext))
    )
    cases, prior = {}, {}
    for row in runner.tasks(questions):
        key = row["task_id"]
        first, second = editorial.folders(row)
        require(
            (first / "run.json").is_file()
            and load(first / "run.json")["state"] in budget.TERMINAL,
            "Settle all primary investigations before allocating independent research",
        )
        if (second / "run.json").is_file():
            rec = load(second / "run.json")
            require(
                rec["state"] in budget.TERMINAL,
                "An independent investigation is still running",
            )
            cost = Decimal(rec["estimated_model_cost_usd"])
            edit = editorial.ROOT / key / "research/run.json"
            if edit.is_file():
                r = load(edit)
                require(
                    r["state"] in budget.TERMINAL, "A source editor is still running"
                )
                cost += Decimal(r["estimated_model_cost_usd"])
            prior[key] = str(cost)
            continue
        if not recovery.needs_research(first.parent):
            continue
        if budget_publication.eligible(row, cfg, spec):
            path_review = budget_publication.directory(key) / "run.json"
            require(
                path_review.is_file() and load(path_review)["state"] in budget.TERMINAL,
                "Finish the eligible independent draft reviews before allocating funds",
            )
            if load(path_review)["state"] == "qualified":
                continue
        cases[key] = candidate_record(first)
    balance = budget.ProjectBudget().status()
    # A fresh reproduction has no already-completed independent cohort. Use the measured
    # reference captured during this study, never add those separate reference costs to its bill.
    observed = prior or reference_costs()
    mean = sum(Decimal(v) for v in observed.values()) / len(observed)
    plan = {
        "version": 1,
        "policy_sha256": policy_sha(),
        "budget": balance,
        "unanswered": cases,
        "observed_independent_costs_usd": observed,
        "reference_only": not bool(prior),
        "alternate_tasks": select(cases, Decimal(balance["available_usd"]), mean),
    }
    validate(plan)
    write_once(path, json_bytes(plan))
    return plan


def reference_costs():
    return load(POLICY.with_name("recovery-reference-costs.json"))


def tag(plan, key):
    if plan is None or key not in plan["alternate_tasks"]:
        return None
    return {
        "profile": ALTERNATE,
        "case_limit_usd": CAP,
        "policy_sha256": plan["policy_sha256"],
        "allocation_sha256": fingerprint(json_bytes(plan)),
        "original_run_sha256": plan["unanswered"][key]["original_run_sha256"],
    }


def route(base, original_profile, selected):
    if selected is None:
        return ordinary_route(base, original_profile)
    require(
        selected["profile"] == ALTERNATE
        and selected["case_limit_usd"] == CAP
        and selected["policy_sha256"] == policy_sha()
        and ALTERNATE != original_profile,
        "A funded researcher differs from the budget policy",
    )
    return research_desk_agent, ALTERNATE, CAP


def ticket(row, base, ext, original, selected):
    standard = ordinary_ticket(row, base, ext, original)
    if selected is None:
        return standard
    require(
        standard["original_run_sha256"] == selected["original_run_sha256"],
        "The selected original investigation changed",
    )
    return {**standard, "independent_budget_route": selected}


@contextmanager
def for_case(row, selected):
    """Bind only this serial operator's secondary ticket and model route inside the frozen workflow."""
    if selected is None:
        yield
        return
    require(
        recovery.ticket is ordinary_ticket and recovery.second_route is ordinary_route,
        "An independent route is already active",
    )

    def assigned(r, base, ext, original):
        require(
            r["task_id"] == row["task_id"],
            "Another task entered the active recovery route",
        )
        return ticket(r, base, ext, original, selected)

    recovery.second_route = lambda base, profile: route(base, profile, selected)
    recovery.ticket = assigned
    try:
        yield
    finally:
        recovery.second_route, recovery.ticket = ordinary_route, ordinary_ticket


def export(questions, destination, registration, plan):
    original = budget_publication.resolve

    def verified(row, *arguments):
        selected = tag(plan, row["task_id"])
        with for_case(row, selected):
            answer, proof = original(row, *arguments)
        if selected:
            require(
                len(proof["attempts"]) == 2
                and proof["attempts"][1]["profile"] == ALTERNATE,
                "The budget-selected researcher was not used",
            )
            proof = {**proof, "independent_budget_route": selected}
        return answer, proof

    budget_publication.resolve = verified
    try:
        with archive_failure.use_archive_failure_verifier():
            report = budget_publication.export(questions, destination, *registration)
    finally:
        budget_publication.resolve = original
    write_once(destination / FILENAME, json_bytes(plan))
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("questions", type=Path)
    p.add_argument("--output", type=Path, default=ALLOCATION)
    a = p.parse_args()
    plan = prepare(a.questions, a.output)
    print(
        json.dumps(
            {
                "unanswered": len(plan["unanswered"]),
                "mixed_team": len(plan["alternate_tasks"]),
                "budget": plan["budget"],
                "sha256": fingerprint(json_bytes(plan)),
            },
            indent=2,
        )
    )
