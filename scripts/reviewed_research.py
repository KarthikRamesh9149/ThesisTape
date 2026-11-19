"""Research an analyst's question, resolve missing inputs, and independently review the reader's actual memo."""

import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from pydantic import Field

from . import research_desk_agent as core
from . import verified_arithmetic as math
from .baseline_evidence import StrictOutput
from .disney_hulu_case import CaseError
from .sol_baseline import fingerprint, json_bytes
from .sol_provider import ASTRA, MODEL
from .team_report import typed_prompt
from .temporal_web import checked_doc

PROFILE = "evidence_team"
PLAN = (
    core.PLANNER
    + """ List each material part of the question as an explicit answer requirement. Start with
separate searches for the decisive quantitative facts and the actual comparable financial period when requested.
Name the likely original financial statement, table, announcement or decision as concretely as possible."""
)
GAPS = """Read the question, its requirements and the retrieved dated source excerpts. Identify up to two
essential gaps. For each, issue a short search targeted at the missing fact, table or contradictory evidence.
A reported result, management guidance, observed outcome and market expectation are different objects.
If a comparison is asked, locate both values for the same metric and the exact periods asked. Missing table
headings and period assignments matter. A forecast must rely on pre-cutoff evidence only. Choose a search
likely to find evidence absent from the current passages. Leave searches empty if evidence already suffices."""
WRITE = (
    core.WRITE
    + """ Answer each listed requirement explicitly in the opening paragraphs, then develop the
market/policy mechanism, relevant prior evidence, plausible alternative, expected time horizon and observable
reversal trigger. Use as much space as the question requires; avoid repetitive paragraphs. To report an amount
printed under a separate currency/scale header, use a marker for only the exact numeric token, for example
USD {{P1|100,000}} million, with evidence for that unit. Do not put invented currency signs, period or units
inside a literal marker. Use {{C1}} etc for the named computations, including band endpoints when useful.
A verified calculation needs references to all its input passages in that paragraph. The compiler displays its
operands, formula and links below the memo. Cite each material fact to its relevant table, passage and time.
Quote compact, unique, verbatim witnesses. Put one plain base-case direction in Outlook when requested."""
)
REVIEW = (
    core.REVIEW
    + """ Read the exact markdown that readers will receive, including its page links, source list
and checked calculations. Grade coverage against what the question actually asks. A valid calculation appendix
satisfies 'show inputs' and the page in a citation satisfies 'cite pages'. An additional calculation or detail
may be useful without being required. Mark a paragraph false only for a material error or unsupported material
claim. Report missing required coverage separately. Do not require that the independent future outcome be known.
Do not introduce false precision or a new task. Preserve supported, specific analyst judgments."""
)
IDS = re.compile(
    r"(?<![\w-])(?:COVID-19|(?:Form\s+)?(?:10-[KQ]|8-K|20-F|6-K)|FY\s?(?:19|20)\d{2}|[QH][1-4])(?![\w-])",
    re.IGNORECASE,
)


class Plan(core.Plan):
    requirements: list[str] = Field(min_length=1, max_length=8)


class Gaps(StrictOutput):
    searches: list[core.Search] = Field(max_length=2)


def mark_identifiers(paragraph, passages):
    """Bind exact identifiers like Form 10-K when a draft leaves them outside a source marker."""
    cited = list(
        dict.fromkeys(core.evidence_id(r, passages) for r in paragraph.citations)
    )

    def bind(m):
        for pid in cited:
            if core.literal_in(m[0], core.clean(passages[pid]["text"])):
                return "{{" + pid + "|" + m[0] + "}}"
        return m[0]

    # Leave existing markers untouched, including numbers in passage identifiers and previously checked dates.
    parts = re.split(r"(\{\{[^{}\n]*\}\})", paragraph.text)
    return paragraph.model_copy(
        update={
            "text": "".join(
                s if s.startswith("{{") else IDS.sub(bind, s) for s in parts
            )
        }
    )


def merge(results, question, validator):
    """Give each independent search its first result before selecting a second from any search."""
    cutoff = results[0]["request"]["max_date"]
    if any(r["request"]["max_date"] != cutoff for r in results):
        raise CaseError("Mismatched search cutoff")
    ordered = [
        r["sources"][rank]
        for rank in range(max(len(r["sources"]) for r in results))
        for r in results
        if rank < len(r["sources"])
    ]
    return core.merge_sources(
        [{"request": {"max_date": cutoff}, "sources": ordered}], question, validator
    )


def review(draft, context, passages, calculations, ask, stage):
    compiled, rejected = {}, {}
    question, cutoff = context["question"], context["cutoff"]
    for i, p in enumerate(draft.paragraphs, 1):
        key = f"A{i}"
        try:
            p = mark_identifiers(p, passages)
            compiled[key] = core.compile_paragraph(
                p,
                question,
                cutoff,
                passages,
                calculations,
                context.get("forecast_rule"),
            )
        except CaseError as exc:
            rejected[key] = str(exc)
    if not compiled:
        return [], rejected, ["No publishable evidence paragraph."]
    used = {pid for p in compiled.values() for pid in p["pids"]}
    visible = core.render(
        question,
        cutoff,
        list(compiled.values()),
        passages,
        calculations,
        False,
        inline_locators=True,
    ).decode()
    material = {
        **context,
        "memo": visible,
        "paragraphs": compiled,
        "originals": core.brief_sources(passages, used),
    }
    if hasattr(draft, "direction"):
        material["direction"] = draft.direction
    verdict = core.Review.model_validate_json(
        ask(
            stage,
            typed_prompt(MODEL, REVIEW, material, core.Review, effort="medium"),
            2000,
        )
    )
    ids = [d.paragraph for d in verdict.decisions]
    if set(ids) != set(compiled) or len(set(ids)) != len(ids):
        raise CaseError(
            "The independent reviewer must cover every released paragraph once"
        )
    for d in verdict.decisions:
        if not d.accept:
            rejected[d.paragraph] = d.reason
    return (
        [p for k, p in compiled.items() if k not in rejected],
        rejected,
        verdict.missing,
    )


def research(question, cutoff, gateway, ask):
    rule = getattr(gateway, "forecast_rule", None)
    verify = getattr(gateway, "check_source", checked_doc)
    task = {"question": question, "cutoff": cutoff}
    if rule:
        task["forecast_rule"] = rule
    plan = Plan.model_validate_json(
        ask(
            "orchestrator.plan",
            typed_prompt(MODEL, PLAN, task, Plan, effort="medium"),
            1400,
        )
    )
    if len({s.query.casefold() for s in plan.searches}) != len(plan.searches):
        raise CaseError("Research searches must be distinct")
    context = {**task, "decision": plan.decision, "requirements": plan.requirements}

    def search(job):
        return gateway.search(
            job.query,
            max_date=cutoff,
            titles=job.titles,
            filing_terms=job.filing_terms,
            limit=9,
        )

    with ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(search, plan.searches))
        first = merge(results, question, verify)
        gaps = Gaps.model_validate_json(
            ask(
                "orchestrator.coverage",
                typed_prompt(
                    MODEL,
                    GAPS,
                    {**context, "opened": core.brief_sources(first)},
                    Gaps,
                    effort="medium",
                ),
                850,
            )
        )
        prior = {s.query.casefold() for s in plan.searches}
        followups = []
        for s in gaps.searches:
            if s.query.casefold() not in prior:
                followups.append(s)
                prior.add(s.query.casefold())
        extra = list(pool.map(search, followups))
    passages = merge(extra + results, question, verify)
    trace = {
        "plan": plan.model_dump(),
        "followups": [s.model_dump() for s in followups],
        "searches": [
            {
                "max_date": r["request"]["max_date"],
                "query": r["request"]["query"],
                "response_sha256": fingerprint(json_bytes(r)),
                "admitted_urls": [s["url"] for s in r["sources"]],
                "service_failures": r["service_failures"],
            }
            for r in results + extra
        ],
        "evidence": passages,
    }
    if not passages:
        return None, {**trace, "release": "insufficient_evidence", "paragraphs": []}
    evidence = {**context, "originals": core.brief_sources(passages)}

    def maths():
        return math.Plan.model_validate_json(
            ask(
                "research.arithmetic",
                typed_prompt(
                    MODEL, math.INSTRUCTIONS, evidence, math.Plan, effort="medium"
                ),
                1350,
            )
        )

    def outlook():
        advice = (
            core.FORECAST
            + """ Seek the best contrary evidence and, where available, a dated example or quantitative base
rate. Explicitly distinguish the forecast from what the sources show. You are assessing the economic mechanism, independent of the author."""
        )
        return core.checked_findings(
            ask(
                "research.outlook",
                typed_prompt(
                    MODEL, advice, evidence, core.Observations, effort="medium"
                ),
                1550,
            ),
            passages,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        m, o = pool.submit(maths), pool.submit(outlook)
        operations, scenario = m.result().operations, o.result()
    calculations, errors = math.compile_plan(operations, passages, rule)
    writing = {
        **evidence,
        "checked_calculations": calculations,
        "unavailable_calculations": errors,
        "outlook": scenario,
    }
    kind = core.ForecastDraft if rule else core.Draft
    policy = WRITE
    if rule:
        policy += " Use {{BAND}} for the task's threshold; use {{Cn}} for computed bounds. State the selected direction itself in the Outlook text and the direction field."
    raw = ask(
        "lead.memo", typed_prompt(ASTRA, policy, writing, kind, effort="medium"), 3600
    )
    draft = kind.model_validate_json(raw)
    good, blocked, missing = review(
        draft, context, passages, calculations, ask, "review.initial"
    )
    reviews = [{"approved": len(good), "blocked": blocked, "missing": missing}]
    if blocked or missing:
        corrective = {
            **writing,
            "first_draft": draft.model_dump(),
            "unsupported_paragraphs": blocked,
            "missing_required_items": missing,
            "editor_assignment": "Return a complete replacement memo. Retain supported material verbatim when appropriate, fix the identified material defects, and remove duplication. Check all required periods, figures, assumptions and signals.",
        }
        raw = ask(
            "lead.revise",
            typed_prompt(ASTRA, policy, corrective, kind, effort="medium"),
            3200,
            keep_usd=Decimal("0.045"),
        )
        if raw:
            candidate = kind.model_validate_json(raw)
            repaired, rejected, gaps = review(
                candidate, context, passages, calculations, ask, "review.recheck"
            )
            # A failed rewrite cannot discard an already verified first draft.
            initial_coverage = {p["section"] for p in good}
            new_coverage = {p["section"] for p in repaired}
            if repaired and (
                not good
                or ("Answer" in new_coverage or "Answer" not in initial_coverage)
                and (len(gaps), len(rejected), -len(new_coverage))
                <= (len(missing), len(blocked), -len(initial_coverage))
            ):
                draft, good, blocked, missing = candidate, repaired, rejected, gaps
            reviews.append(
                {
                    "approved": len(repaired),
                    "blocked": rejected,
                    "missing": gaps,
                    "selected": draft is candidate,
                }
            )
    qualified = bool(
        blocked or missing or not any(p["section"] == "Answer" for p in good)
    )
    base = {
        **trace,
        "findings": {},
        "outlook": scenario,
        "calculations": calculations,
        "calculation_errors": errors,
        "paragraphs": good,
        "reviews": reviews,
        "release": "qualified" if qualified else "complete",
    }
    if not good:
        return None, {**base, "release": "withheld"}
    memo = core.render(
        question, cutoff, good, passages, calculations, qualified, inline_locators=True
    )
    if rule:
        choice = draft.direction
        if not any(
            p["section"] == "Outlook"
            and re.search(r"\b" + choice + r"\b", p["text"], re.IGNORECASE)
            for p in good
        ):
            choice = "undetermined"
        memo = memo.replace(
            b"\n\n**Research question**",
            f"\n\nForecast direction: {choice}\n\n**Research question**".encode(),
            1,
        )
        base["direction"] = choice
    return memo, base
