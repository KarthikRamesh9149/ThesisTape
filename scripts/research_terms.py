"""Render dated task parameters without treating a question as evidence of a financial fact."""

import re
from datetime import datetime
from decimal import Decimal

from . import research_desk_agent as core
from .disney_hulu_case import CaseError


def calendar_term(value):
    if len(value) > 32 or not re.search(r"\b(?:19|20)\d{2}\b", value):
        return False
    for pattern in (
        "%B %Y",
        "%b %Y",
        "%B %d, %Y",
        "%b %d, %Y",
        "%d %B %Y",
        "%d %b %Y",
        "%Y-%m-%d",
    ):
        try:
            datetime.strptime(value, pattern)
        except ValueError:
            continue
        return True
    return False


def tolerance(rule):
    if not rule:
        raise CaseError("The question supplies no comparison threshold")
    v = Decimal(rule["tolerance"])
    if not v.is_finite() or v < 0:
        raise CaseError("Invalid task tolerance")
    if v == 0:
        return "zero change"
    if rule["mode"] == "relative":
        return f"±{format((100 * v).normalize(), 'f')}%"
    if rule["mode"] != "absolute":
        raise CaseError("Unknown comparison threshold")
    units = "percentage points" if "percent" in rule["unit"].lower() else "ratio points"
    return f"±{format(v.normalize(), 'f')} {units}"


def compile_paragraph(paragraph, question, cutoff, passages, calculations, rule):
    if "⟦" in paragraph.text or "⟧" in paragraph.text:
        raise CaseError("Unexpected reserved character in the draft")
    values = {}
    cited = [core.evidence_id(r, passages) for r in paragraph.citations]

    def insert(value):
        key = "⟦TERM" + chr(ord("A") + len(values)) + "⟧"
        values[key] = value
        return key

    def bind(match):
        ident, value, special = match.groups()
        if special == "BAND":
            return insert(tolerance(rule))
        if ident == "Q" and calendar_term(value):
            for pid in cited:
                if core.literal_in(value, core.clean(passages[pid]["text"])):
                    return "{{" + pid + "|" + value + "}}"
            if core.literal_in(value, question):
                return insert(value)
            raise CaseError("Date is absent from the question and the cited originals")
        return match[0]

    adjusted = paragraph.model_copy(
        update={"text": core.MARK.sub(bind, paragraph.text)}
    )
    result = core.compile_paragraph(
        adjusted, question, cutoff, passages, calculations, rule
    )
    for key, value in values.items():
        result["text"] = result["text"].replace(key, value)
    return result
