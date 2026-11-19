"""Cutoff-checked retrieval and cited memo validation for the Disney baseline."""

import json
import re
from calendar import month_name
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from urllib.parse import quote

from pydantic import BaseModel, ConfigDict, ValidationError

from .disney_hulu_case import (
    CASE_FILES,
    SAVED_CASE,
    CaseError,
    checked_bytes,
    filing_blocks,
    read_saved_file,
    require_entries,
)


@dataclass(frozen=True)
class Passage:
    id: str
    text: str


@dataclass(frozen=True)
class ResearchCase:
    id: str
    question: str
    cutoff: date
    agent_dir: Path


@dataclass(frozen=True)
class RetrievedEvidence:
    source: dict
    passages: tuple[Passage, ...]
    trace: dict


def load_case(agent_dir: Path) -> ResearchCase:
    require_entries(agent_dir, CASE_FILES["agent"])
    manifest_bytes = read_saved_file(agent_dir / "manifest.json")
    if manifest_bytes != read_saved_file(SAVED_CASE / "agent/manifest.json"):
        raise CaseError("Agent manifest differs from the verified Disney case")
    manifest = json.loads(manifest_bytes)
    return ResearchCase(manifest["case_id"], manifest["question"], date.fromisoformat(manifest["cutoff"]), agent_dir)


def retrieve(case: ResearchCase, *, max_date: date) -> RetrievedEvidence:
    """Read the pinned EDGAR item through the controller's single evidence entry point."""
    if max_date != case.cutoff:
        raise CaseError("Retrieval max_date must equal the case cutoff")
    if load_case(case.agent_dir) != case:
        raise CaseError("Case changed between intake and retrieval")
    source = json.loads(read_saved_file(SAVED_CASE / "agent/manifest.json"))["source"]
    if date.fromisoformat(source["filing_date"]) > max_date:
        raise CaseError("The filing was unavailable by the requested cutoff")

    name = source["file"]
    original = checked_bytes({name: read_saved_file(case.agent_dir / name)}, name, source["sha256"])
    texts = (text for text in filing_blocks(original, source) if text)
    passages = tuple(Passage(f"P{index}", text) for index, text in enumerate(texts, start=1))
    if not passages:
        raise CaseError("No passages in the admitted filing item")
    trace = {
        "operation": "read_archived_edgar_item",
        "max_date": max_date.isoformat(),
        "accession": source["accession"],
        "passage_ids": [passage.id for passage in passages],
    }
    return RetrievedEvidence(source, passages, trace)


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Citation(StrictOutput):
    passage_id: str
    quote: str


class Paragraph(StrictOutput):
    text: str
    citations: list[Citation]


class Amount(StrictOutput):
    expression: str
    citation: Citation


class Reconciliation(StrictOutput):
    gross: Amount
    deductions: Amount
    reported: Amount


class Draft(StrictOutput):
    expected_payment: Paragraph
    valuation_basis: Paragraph
    appraisal_and_uncertainty: Paragraph
    reconciliation: Reconciliation


MONEY = re.compile(r"(?:\$|USD )(\d+(?:\.\d+)?) (million|billion)\b")
SCALE = {"million": Decimal(1000000), "billion": Decimal(1000000000)}
NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?")
PERCENT = re.compile(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", re.IGNORECASE)
UNSAFE_PROSE = re.compile(r"https?://|www\.|[\x00-\x1f\x7f<>\[\]`]", re.IGNORECASE)


def amount_and_resolution(expression: str) -> tuple[Decimal, Decimal]:
    match = MONEY.fullmatch(expression)
    if not match or len(match[1]) > 18:
        raise CaseError("Use a source amount in dollars, millions or billions as printed, with its scale")
    digits, unit = match.groups()
    amount = Decimal(digits) * SCALE[unit]
    resolution = SCALE[unit] * Decimal(1).scaleb(-len(digits.partition(".")[2]))
    if amount <= 0:
        raise CaseError("Reconciliation amounts must be positive")
    return amount, resolution


def citation_text(citation: Citation, passages: dict[str, str]) -> str:
    passage = passages.get(citation.passage_id, "")
    snippet = citation.quote
    if not 12 <= len(snippet) <= 220 or snippet != snippet.strip() or snippet not in passage:
        raise CaseError("A citation quote does not match its admitted passage")
    return passage


def check_paragraph(paragraph: Paragraph, passages: dict[str, str], cutoff: date) -> str:
    text = paragraph.text.strip()
    if not 1 <= len(text) <= 950 or UNSAFE_PROSE.search(text):
        raise CaseError("Memo paragraphs must contain plain text and no model-supplied links")
    if not 1 <= len(paragraph.citations) <= 4:
        raise CaseError("Each memo paragraph needs one to four citations")
    corpus = " ".join(citation_text(citation, passages) for citation in paragraph.citations)
    source_amounts = {amount_and_resolution(match[0])[0] for match in MONEY.finditer(corpus)}
    if re.search(r"\$|\b(?:USD|dollars)\b", MONEY.sub("", text), re.IGNORECASE):
        raise CaseError("A money claim is missing a recognised currency or scale")
    if any(amount_and_resolution(match[0])[0] not in source_amounts for match in MONEY.finditer(text)):
        raise CaseError("A monetary claim does not match a figure in its cited evidence")
    month = month_name[cutoff.month]
    cutoff_forms = (cutoff.isoformat(), f"{cutoff.day} {month} {cutoff.year}", f"{month} {cutoff.day}, {cutoff.year}",
                    f"{cutoff.day} {month}", f"{month} {cutoff.day}")
    as_of_date = "|".join(re.escape(value) for value in cutoff_forms)
    # The intake supports the date of an as-of judgement. A transaction dated
    # that day still needs independent support from the cited passage.
    framing = r"(?:as of|(?:not (?:yet )?known|unknown|undetermined|unresolved) on)"
    claims = re.sub(rf"\b{framing} (?:{as_of_date})(?![\w/-]|,?\s+\d{{4}}\b)", "", text, flags=re.IGNORECASE)

    def numbers(value: str) -> set[Decimal]:
        return {Decimal(n.replace(",", "")) for n in NUMBER.findall(value)}

    if (
        numbers(claims) - numbers(corpus)
        or set(map(Decimal, PERCENT.findall(claims))) - set(map(Decimal, PERCENT.findall(corpus)))
    ):
        raise CaseError("A numerical claim does not appear in its cited evidence")
    return text


def check_reconciliation(reconciliation: Reconciliation, passages: dict[str, str]) -> dict:
    def cited_amount(amount: Amount, role: str, context: str) -> tuple[Decimal, Decimal]:
        text = citation_text(amount.citation, passages)
        expressions = {match[0] for match in MONEY.finditer(amount.citation.quote)}
        if not amount.expression.startswith("$") or amount.expression not in expressions:
            raise CaseError("Each calculation input must appear exactly in its citation quote")
        if context.format(amount.expression) not in text:
            raise CaseError(f"The calculation input is not the disclosed {role}")
        return amount_and_resolution(amount.expression)

    # Bind each operand to its role in this filing. Valid dollar amounts can still be swapped.
    gross, gross_resolution = cited_amount(
        reconciliation.gross, "guaranteed purchase price", "{} (the “guaranteed purchase price”)"
    )
    deductions, deductions_resolution = cited_amount(
        reconciliation.deductions, "unpaid contribution deduction", "currently approximately {}"
    )
    reported, reported_resolution = cited_amount(
        reconciliation.reported, "expected initial payment", "Disney expects it will pay NBCU approximately {}"
    )
    calculated = gross - deductions
    difference = reported - calculated
    tolerance = (gross_resolution + deductions_resolution + reported_resolution) / 2
    if calculated <= 0 or abs(difference) > tolerance:
        raise CaseError("The calculation cannot reconcile the disclosed amounts at their quoted precision")
    return {
        "operation": "subtract",
        "currency": "USD",
        "gross": format(gross, "f"),
        "deductions": format(deductions, "f"),
        "calculated": format(calculated, "f"),
        "reported": format(reported, "f"),
        "reported_minus_calculated": format(difference, "f"),
        "rounding_tolerance": format(tolerance, "f"),
    }


def source_link(source: dict, citation: Citation, label: str) -> str:
    fragment = quote(citation.quote, safe="").replace("-", "%2D")
    return f"[{label}]({source['source_url']}#:~:text={fragment})"


def render(case: ResearchCase, evidence: RetrievedEvidence, model_text: str) -> tuple[str, dict]:
    source = evidence.source
    reference = (
        f"[The Walt Disney Company Form 8-K, {source['section']}]({source['source_url']}) · "
        f"Filed {source['filing_date']} · Accession {source['accession']}"
    )
    return render_report(
        case, {p.id: p.text for p in evidence.passages}, model_text,
        lambda ref, label: source_link(source, ref, label or f"Disney 8-K, {ref.passage_id}"),
        lambda _: [reference],
    )


def render_report(
    case: ResearchCase, passages: dict[str, str], model_text: str,
    link_for: Callable[[Citation, str | None], str], references: Callable[[list[str]], list[str]],
) -> tuple[str, dict]:
    try:
        draft = Draft.model_validate_json(model_text)
    except ValidationError as error:
        raise CaseError("Sol output did not match the memo schema") from error
    calculation = check_reconciliation(draft.reconciliation, passages)
    reported = Decimal(calculation["reported"])
    payment_figures = {amount_and_resolution(match[0])[0] for match in MONEY.finditer(draft.expected_payment.text)}
    if reported not in payment_figures:
        raise CaseError("Expected-payment paragraph must include the reconciled reported figure")

    cited: dict[str, None] = {}

    def link(ref: Citation, label: str | None = None) -> str:
        cited[ref.passage_id] = None
        return link_for(ref, label)

    lines = [
        "# Disney and Hulu",
        "",
        f"**As of {case.cutoff.isoformat()}**",
        "",
        case.question,
        "",
    ]
    for heading, paragraph in (
        ("Expected initial payment", draft.expected_payment),
        ("Valuation basis", draft.valuation_basis),
        ("Appraisal and outstanding uncertainty", draft.appraisal_and_uncertainty),
    ):
        text = check_paragraph(paragraph, passages, case.cutoff)
        citations = list(dict.fromkeys((ref.passage_id, ref.quote) for ref in paragraph.citations))
        links = " ".join(dict.fromkeys(link(Citation(passage_id=pid, quote=snippet)) for pid, snippet in citations))
        lines.extend((f"## {heading}", "", f"{text} {links}", ""))

    def dollars(value: str) -> str:
        return f"USD {Decimal(value):,.0f}"

    operands = draft.reconciliation
    lines.extend((
        "## Arithmetic check",
        "",
        f"- Guaranteed purchase price **{dollars(calculation['gross'])}**. "
        + link(operands.gross.citation, "Disclosed gross"),
        f"- Unpaid capital contributions **{dollars(calculation['deductions'])}**. "
        + link(operands.deductions.citation, "Disclosed deduction"),
        f"- Subtracting the two rounded inputs gives **{dollars(calculation['calculated'])}**.",
        f"- Disney's reported estimate was **{dollars(calculation['reported'])}**. "
        + link(operands.reported.citation, "Disclosed estimate"),
        "",
    ))
    gap = dollars(format(abs(Decimal(calculation["reported_minus_calculated"])), "f"))
    if Decimal(calculation["reported_minus_calculated"]):
        lines.extend((
            (f"The **{gap}** difference fits within the precision of the published figures. "
             "The reported estimate is the figure used in this memo."),
            "",
        ))
    sources = references(list(cited))
    lines.extend(("## Primary source" if len(sources) == 1 else "## Primary sources", "", *sources, ""))
    return "\n".join(lines), calculation
