"""Research an analyst's question, resolve missing inputs, and independently review the reader's actual memo."""

import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from pydantic import Field

from . import research_desk_agent as core
from . import research_terms
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


class CalculationReview(core.Review):
    decisions: list[core.Decision] = Field(max_length=17)


class Gaps(StrictOutput):
    searches: list[core.Search] = Field(max_length=2)


def render(
    question,
    cutoff,
    paragraphs,
    passages,
    calculations,
    qualified,
    *,
    inline_locators=False,
):
    lines = [
        "# Investment research memo",
        "",
        f"As of {cutoff}",
        "",
        "**Research question**",
        question,
        "",
    ]
    if qualified:
        lines += [
            "Some parts of the question could not be established from eligible evidence.",
            "",
        ]
    used = []
    named = {}

    def citation(pid):
        p = passages[pid]
        n = named.setdefault(p["url"], len(named) + 1)
        locator = p["proof"].get("locator") if inline_locators else None
        label = f"{n}, {locator}" if locator else str(n)
        return f"[{label}]({p['url']})"

    for section in core.SECTIONS:
        group = [p for p in paragraphs if p["section"] == section]
        if group:
            lines += ["## " + section, ""]
            for p in group:
                lines += [
                    p["text"]
                    + " "
                    + " ".join(dict.fromkeys(citation(pid) for pid in p["pids"])),
                    "",
                ]
                used.extend(p["calculation_ids"])
    if used:
        lines += ["## Checked calculations", ""]
        required = set(used)
        for i, c in reversed(calculations.items()):
            if i in required:
                required.update(x for x in (c["left"], c["right"]) if x in calculations)
        for i, c in calculations.items():
            if i in required:
                lines += [
                    "- "
                    + math.formula(i, c)
                    + " "
                    + " ".join(
                        dict.fromkeys(citation(pid) for pid in c["evidence_ids"])
                    )
                ]
        lines.append("")
    if named:
        lines += ["## Sources", ""]
        for url, n in named.items():
            p = next(v for v in passages.values() if v["url"] == url)
            title = re.sub(r"[\[\]\r\n]", "", p["title"])
            lines.append(f"{n}. [{title}]({url}), available by {p['available']}.")
    return ("\n".join(lines).rstrip() + "\n").encode()


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


def review(
    draft, context, passages, calculations, ask, stage, *, revision=1, single=False
):
    compiled, rejected = {}, {}
    question, cutoff = context["question"], context["cutoff"]
    for i, p in enumerate(draft.paragraphs, 1):
        key = f"A{i}"
        try:
            p = mark_identifiers(p, passages)
            compiler = (
                research_terms.compile_paragraph
                if revision >= 2
                else core.compile_paragraph
            )
            compiled[key] = compiler(
                p,
                question,
                cutoff,
                passages,
                calculations,
                context.get("forecast_rule"),
            )
        except CaseError as exc:
            rejected[key] = str(exc)
    if revision >= 3:
        compiled.update(math.residual_rows(compiled, calculations, passages))
    if not compiled:
        return [], rejected, ["No publishable evidence paragraph."]
    if single:
        return list(compiled.values()), rejected, []
    used = {pid for p in compiled.values() for pid in p["pids"]}
    visible = render(
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
    reviewer = REVIEW
    if revision >= 2:
        material.pop("decision")
        material.pop("requirements")
        reviewer += " Evaluate completeness against the user's original question. A planner's research tactics are optional. If the question asks for issuer figures, one controlling issuer document can support them. An independent source is required only when explicitly requested or when the cited document cannot support a material assertion. Interpret the supplied comparison tolerance as a rule defined by the user, not an issuer forecast. Distinguish a missing requested fact from a suggested extra."
    if hasattr(draft, "direction"):
        material["direction"] = draft.direction
    schema = CalculationReview if revision >= 3 else core.Review
    if revision >= 3:
        reviewer += " K entries are calculator-derived passages. Decide each against the source rows and requested metric, actor, units and periods. Reject an irrelevant calculation or wrong metric. Read the verified formula and its operands as part of the memo."
    verdict = schema.model_validate_json(
        ask(
            stage,
            typed_prompt(MODEL, reviewer, material, schema, effort="medium"),
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


def prefer_repair(initial, revised):
    """Compare supported sections after the reviewer has removed rejected paragraphs."""
    good, blocked, missing = initial
    repaired, rejected, gaps = revised
    if not repaired:
        return False
    if not good:
        return True
    before = {row["section"] for row in good}
    after = {row["section"] for row in repaired}
    if not (before & {"Answer", "Outlook"}) <= after:
        return False
    if "Answer" in after and not (rejected or gaps):
        return True
    if not (blocked or missing):
        return False

    # Missing items assess the whole draft, including material the reviewer subsequently
    # removed. An empty missing list does not establish that the surviving text answered it.
    def answers(rows):
        return {p["text"] for p in rows if p["section"] == "Answer"}

    if len(answers(repaired)) > len(answers(good)) and len(rejected) < len(blocked):
        return True
    if len(answers(repaired)) < len(answers(good)) and len(rejected) > len(blocked):
        return False
    return (len(gaps), len(rejected), -len(after)) <= (
        len(missing),
        len(blocked),
        -len(before),
    )


def research(question, cutoff, gateway, ask, *, revision=1, single=False):
    rule = getattr(gateway, "forecast_rule", None)
    verify = getattr(gateway, "check_source", checked_doc)
    task = {"question": question, "cutoff": cutoff}
    if rule:
        task["forecast_rule"] = rule
    planner = PLAN
    if revision >= 2:
        planner += " Keep answer requirements limited to what the user actually requested. Independent corroboration can be a search strategy without being an answer requirement. Financial inputs requested by the user need verified sources. A requested period and the user's definition of a forecast band are task parameters."
    plan = Plan.model_validate_json(
        ask(
            "orchestrator.plan",
            typed_prompt(MODEL, planner, task, Plan, effort="medium"),
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

    if single:
        operations, scenario = maths().operations, []
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            m, o = pool.submit(maths), pool.submit(outlook)
            operations, scenario = m.result().operations, o.result()
    calculations, errors = math.compile_plan(
        operations, passages, rule, normalize_labels=revision >= 3
    )
    writing = {
        **evidence,
        "checked_calculations": calculations,
        "unavailable_calculations": errors,
        "outlook": scenario,
    }
    kind = core.ForecastDraft if rule else core.Draft
    policy = WRITE
    if revision >= 2:
        policy += " A time period in the question and the user's inclusive comparison rule are definitions for this task. Describe them as the requested period or the question's tolerance. Do not describe either as an unverified financial premise. {{BAND}} renders only the numerical tolerance, for example ±2%. Include a full calendar date only if it occurs in a cited source or verbatim in the question. Put the verified prior and current figures in the first paragraph when a comparison is requested."
    if rule:
        policy += " Use {{BAND}} for the task's threshold; use {{Cn}} for computed bounds. State the selected direction itself in the Outlook text and the direction field."
    raw = ask(
        "lead.memo", typed_prompt(ASTRA, policy, writing, kind, effort="medium"), 3600
    )
    draft = kind.model_validate_json(raw)
    good, blocked, missing = review(
        draft,
        context,
        passages,
        calculations,
        ask,
        "review.initial",
        revision=revision,
        single=single,
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
            **({} if single else {"keep_usd": Decimal("0.045")}),
        )
        if raw:
            candidate = kind.model_validate_json(raw)
            repaired, rejected, gaps = review(
                candidate,
                context,
                passages,
                calculations,
                ask,
                "review.recheck",
                revision=revision,
                single=single,
            )
            # A failed rewrite cannot discard an already verified first draft.
            initial_coverage = {p["section"] for p in good}
            new_coverage = {p["section"] for p in repaired}
            use_repair = repaired and (
                not good
                or ("Answer" in new_coverage or "Answer" not in initial_coverage)
                and (len(gaps), len(rejected), -len(new_coverage))
                <= (len(missing), len(blocked), -len(initial_coverage))
            )
            if revision >= 4:
                use_repair = prefer_repair(
                    (good, blocked, missing), (repaired, rejected, gaps)
                )
            if use_repair:
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
    memo = render(
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
