"""The development candidate. The registered first comparison continues to use paired_research."""

from dataclasses import asdict
from decimal import Decimal

from pydantic import Field, ValidationError

from .baseline_evidence import UNSAFE_PROSE, StrictOutput, citation_text
from .disney_hulu_case import CaseError
from .forecast_specialist import INSTRUCTIONS, Outlook
from .material_dispute import review_dispute
from .paired_grounding import check_numbers
from .paired_research import (
    ARMS,
    WRITER,
    PairedCandidate,
    ResearchMemo,
    formula,
    paragraphs,
)
from .paired_tools import inspect_evidence
from .sol_baseline import fingerprint
from .sol_provider import MILLION, MODEL, MODEL_RATES
from .sol_research import passage_context
from .team_report import typed_prompt
from .team_research import lookup, qualified_memo, team_evidence
from .visual_reader import with_page

VERSION = "paired-grounding-v2"
PATCH_OUTPUT_TOKENS = 768


class ParagraphIssue(CaseError):
    def __init__(self, key, error):
        super().__init__(f"{key}: {error}")
        self.key = key


class RepairText(StrictOutput):
    text: str = Field(min_length=1, max_length=1400)


def validate_forecast(case, opened, raw):
    outlook = Outlook.model_validate_json(raw)
    if outlook.choice not in (*case.forecast_choices, "undetermined"):
        raise CaseError("Forecast choice is outside the registered outcomes")
    for key in (
        "base_and_horizon",
        "causal_driver",
        "alternative",
        "revision_signal",
        "historical_comparison",
    ):
        point = getattr(outlook, key)
        if point is None:
            continue
        ids = point.evidence_ids
        if (
            len(set(ids)) != len(ids)
            or set(ids) - opened.keys()
            or not point.text.strip()
            or UNSAFE_PROSE.search(point.text)
        ):
            raise CaseError(f"Invalid evidence or text in {key}")
        try:
            check_numbers(
                point.text, case, [opened[i] for i in ids], opened=opened.values()
            )
        except CaseError as error:
            raise ParagraphIssue(key, error) from error
    return outlook.model_dump()


def forecast(case, opened, ask):
    context = {**case.intake(), "passages": passage_context(opened)}
    for stage in ("forecast.analyze", "forecast.repair"):
        prompt = typed_prompt(MODEL, INSTRUCTIONS, context, Outlook, effort="medium")
        schema = prompt["text"]["format"]["schema"]
        schema["properties"]["choice"]["enum"] = [
            *case.forecast_choices,
            "undetermined",
        ]
        schema["$defs"]["Point"]["properties"]["evidence_ids"]["items"]["enum"] = list(
            opened
        )
        raw = ask(stage, prompt, 2048)
        try:
            return validate_forecast(case, opened, raw)
        except (CaseError, ValidationError) as error:
            if stage == "forecast.repair":
                raise
            context["validation_feedback"] = (
                str(error)
                if isinstance(error, CaseError)
                else "Use the forecast schema."
            )


def build_candidate(case, opened, coverage, operations, visual, raw):
    draft = ResearchMemo.model_validate_json(raw)
    if not case.forecast_choices and draft.direction is not None:
        raise CaseError("A factual case has no forward direction")
    lines = [f"# {case.title}", "", f"As of {case.cutoff}", "", case.question, ""]
    if coverage == "qualify":
        lines.extend(["Partial coverage.", ""])
    if case.forecast_choices:
        lines.extend(["Forecast direction: " + (draft.direction or "undetermined"), ""])
    source_text = {pid: hit.text for pid, hit in opened.items()}
    used = set()
    previous = None
    for key, heading, p in paragraphs(draft):
        if (
            not 1 <= len(p.citations) <= 4
            or not 1 <= len(p.text.strip()) <= 1400
            or UNSAFE_PROSE.search(p.text)
        ):
            raise CaseError(
                "Each paragraph needs plain text and one to four original citations"
            )
        for citation in p.citations:
            citation_text(citation, source_text)
        ids = {c.passage_id for c in p.citations}
        hits = [opened[i] for i in dict.fromkeys(c.passage_id for c in p.citations)]
        for ident in p.calculation_ids:
            if (
                ident not in operations
                or not set(operations[ident]["evidence_ids"]) <= ids
            ):
                raise CaseError(
                    "A calculation must cite each operand's original passage"
                )
        try:
            check_numbers(
                p.text,
                case,
                hits,
                opened=opened.values(),
                calculations=[operations[i] for i in p.calculation_ids],
                visual=visual["observation"]
                if visual and visual["passage_id"] in ids
                else None,
            )
        except CaseError as error:
            raise ParagraphIssue(key, error) from error
        used.update(p.calculation_ids)
        if heading != previous:
            lines.extend([f"## {heading}", ""])
            previous = heading
        links = " ".join(f"[{h.title}, {h.locator}]({h.citation_url})" for h in hits)
        lines.extend([p.text.strip() + " " + links, ""])
    for ident, result in reversed(operations.items()):
        if ident in used:
            used.update(
                ref for ref in (result["left"], result["right"]) if ref in operations
            )
    checked = {i: v for i, v in operations.items() if i in used}
    if checked:
        lines.extend(["## Checked calculations", ""])
        for ident, result in checked.items():
            links = " ".join(
                f"[{i}, {opened[i].locator}]({opened[i].citation_url})"
                for i in result["evidence_ids"]
            )
            lines.append("- " + formula(ident, result) + " " + links)
        lines.append("")
    return PairedCandidate(draft, "\n".join(lines).encode(), checked)


def repair_input_capacity(model, remaining):
    rates = MODEL_RATES[model]
    money = (
        Decimal(remaining) * MILLION - Decimal(rates["output"]) * PATCH_OUTPUT_TOKENS
    )
    return max(0, int(money / Decimal(rates["cache_write_30m"])))


PATCH = """Correct only the supplied paragraph. Retain supported findings and qualify any gap.
Use only its fixed original citations, supplied calculations and registered case terms. Omit an
unsupported figure. Return the replacement text. Its citations and calculation IDs stay attached."""


def repair_request(case, opened, draft, issue, operations, visual):
    p = next(p for key, _, p in paragraphs(draft) if key == issue.key)
    data = {
        **case.intake(),
        "paragraph": p.text,
        "error": str(issue),
        "cited_originals": [
            {"locator": opened[c.passage_id].locator, "quote": c.quote}
            for c in p.citations
        ],
        "checked_calculations": {i: operations[i] for i in p.calculation_ids},
    }
    if visual and visual["passage_id"] in {c.passage_id for c in p.citations}:
        data["verified_page_reading"] = visual["observation"]
    return typed_prompt(MODEL, PATCH, data, RepairText, effort="low")


def write(case, opened, context, writer_prompt, build, ask):
    raw = ask("lead.memo", writer_prompt(context), 2560)
    try:
        return build(raw)
    except ParagraphIssue as issue:
        draft = ResearchMemo.model_validate_json(raw)
        p = next(p for key, _, p in paragraphs(draft) if key == issue.key)
        prompt = repair_request(
            case,
            opened,
            draft,
            issue,
            context["checked_calculations"],
            context["visual_reading"],
        )
        patch = RepairText.model_validate_json(
            ask("lead.memo_repair", prompt, PATCH_OUTPUT_TOKENS)
        )
        p.text = patch.text
        return build(draft.model_dump_json())
    except (ValidationError, CaseError) as error:
        feedback = (
            str(error)
            if isinstance(error, CaseError)
            else "Use the full report schema."
        )
        return build(
            ask(
                "lead.memo_repair",
                writer_prompt({**context, "validation_feedback": feedback}),
                2560,
            )
        )


def workflow(corpus, arm_id, dispatch):
    """Use the original roles, typed tools and review pipeline with the revised validators."""
    case, arm = corpus.case, ARMS[arm_id]
    if arm_id == "mixed_no_forecast" and not case.forecast_choices:
        raise CaseError(
            "A factual event reuses its mixed result for the forecast ablation"
        )

    def ask(stage, prompt, limit, **kw):
        if prompt.get("reasoning", {}).get("effort") == "none":
            prompt = {**prompt, "reasoning": {"effort": "low"}}
        return dispatch(
            stage,
            {**prompt, "model": arm.model_override or prompt["model"]},
            limit,
            **kw,
        )

    if arm.single:
        result = lookup(case, corpus, ask, "lead", case.question, "lead", MODEL)
        work, opened, assignments, reports, decisions = (
            [result],
            result.opened,
            {},
            [],
            [],
        )
        coverage = "write" if opened else "qualify"
    else:
        assignments, work, opened, reports, coverage, decisions = team_evidence(
            case, corpus, ask, fixed_plan=arm.fixed_plan
        )
    trace = {
        "assignments": assignments,
        "workers": [w.record() for w in work],
        "decisions": decisions,
        "evidence": {p: asdict(h) for p, h in opened.items()},
        "coverage": coverage,
    }
    if not opened:
        memo = qualified_memo(case, work)
        if not arm.single:
            trace["review"] = {
                "status": "no_financial_claims",
                "rounds": [],
                "released_memo_sha256": fingerprint(memo),
            }
        return memo, {**trace, "coverage": "qualify"}

    calculations, visual, image = inspect_evidence(case, corpus, opened, ask)
    context = {
        **case.intake(),
        "passages": passage_context(opened),
        "checked_calculations": calculations,
        "visual_reading": visual,
        "coverage": coverage,
    }
    if not arm.single:
        context["worker_reports"] = reports
        if decisions:
            context["lead_direction"] = decisions[-1]["instruction"]
        if arm.forecast and case.forecast_choices:
            context["forecast_specialist"] = trace["forecast_specialist"] = forecast(
                case, opened, ask
            )

    def writer_prompt(data):
        prompt = typed_prompt(MODEL, WRITER, data, ResearchMemo, effort="medium")
        return with_page(prompt, image) if image else prompt

    def build(raw):
        return build_candidate(case, opened, coverage, calculations, visual, raw)

    candidate = write(case, opened, context, writer_prompt, build, ask)
    trace.update(
        calculator=calculations, visual=visual, calculation=candidate.calculation
    )
    if arm.single:
        return candidate.memo, {**trace, "direction": candidate.draft.direction}

    def reviewer(stage, prompt, limit, **kw):
        return ask(
            stage,
            with_page(prompt, image)
            if image and stage.startswith("review.")
            else prompt,
            limit,
            **kw,
        )

    def revision(first, advice):
        return writer_prompt(
            {
                **context,
                "draft": first["draft"],
                "review": first["assessment"],
                "instruction": "Correct the cited draft using the verified evidence.",
                "advice": advice,
            }
        ), build

    memo, arithmetic, review, dispute = review_dispute(
        case,
        corpus,
        opened,
        coverage,
        candidate,
        reviewer,
        enabled=True,
        revision=revision,
    )
    return memo, {
        **trace,
        "review": review,
        "dispute": dispute,
        "calculation": arithmetic,
        "direction": review["rounds"][-1]["draft"]["direction"]
        if memo is not None
        else None,
    }
