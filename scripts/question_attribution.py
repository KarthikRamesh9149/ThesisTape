"""Publish only question attributions that can be traced to the analyst's original wording."""

import re
import unicodedata

from . import research_desk_agent as core
from .disney_hulu_case import CaseError
from .sol_baseline import fingerprint

VERSION = "question-attribution-v1"
QUESTION = r"(?:the |this |original |research |user['’]s )*(?:question|prompt)"
ATTRIBUTION = re.compile(
    rf"\b(?:{QUESTION}(?:['’]s)?\s+(?:premise|assumption|assumes|posits|states|"
    rf"specifies|supplies|gives|provides|stipulates)\b|"
    rf"(?:supplied|given|provided|stated|specified|posited|stipulated)\s+(?:by|in)\s+{QUESTION}\b|"
    rf"(?:premise|assumption)\s+(?:of|in|from)\s+{QUESTION}\b|according to\s+{QUESTION}\b)",
    re.IGNORECASE,
)
QUOTES = re.compile(r'“([^”]+)”|"([^"]+)"|‘([^’]+)’')
SENTENCE = re.compile(r'(?<=[.!?])\s+(?=[A-Z“"])')


def words(text):
    normalized = unicodedata.normalize("NFKC", text.casefold()).replace("−", "-")
    return re.findall(r"[±+%$€£¥-]|\d+(?:[.,/]\d+)*|[^\W\d_]+", normalized)


def occurs(phrase, question):
    size = len(phrase)
    if not size or (
        size == 1
        and phrase[0][0].isdigit()
        and not re.fullmatch(r"(?:19|20)\d\d", phrase[0])
    ):
        return False
    for i in range(len(question) - size + 1):
        if question[i : i + size] != phrase:
            continue
        if (
            phrase[0][0].isdigit()
            and i
            and question[i - 1] in {"±", "+", "-", "$", "€", "£", "¥"}
        ):
            continue
        if phrase[-1][-1].isdigit() and question[i + size : i + size + 1] in (
            ["%"],
            ["-"],
        ):
            continue
        return True
    return False


def supplied(sentence, question):
    """Require the attributed phrase, including signs, units or quoted wording, to occur in the question."""
    match = ATTRIBUTION.search(sentence)
    if not match:
        return True
    quoted = [next(v for v in groups if v) for groups in QUOTES.findall(sentence)]
    if any(not occurs(words(q), question) for q in quoted):
        return False
    before, after = sentence[: match.start()], sentence[match.end() :]
    active = re.search(
        r"(?:assumes|posits|states|specifies|supplies|gives|provides|stipulates)$",
        match[0],
        re.IGNORECASE,
    )
    # Find the actual object of the attribution. A date or a quoted word elsewhere
    # in the question is insufficient proof for a broader claim about a company.
    suffix = after.lstrip(" ,:—–")
    subject_after = (
        active
        or match[0].lower().startswith("according to")
        or re.match(
            r"(?:(?:here|itself)\s+)?(?:is|was)\s+(?:that\s+)?", suffix, re.IGNORECASE
        )
    )
    if subject_after:
        part = re.split(r"[,;:—–]", suffix, maxsplit=1)[0]
        part = re.sub(
            r"^(?:(?:here|itself)\s+)?(?:(?:is|was)\s+)?(?:that\s+)?",
            "",
            part,
            flags=re.IGNORECASE,
        )
    else:
        part = re.split(r"[,;:—–]", before)[-1]
        part = re.sub(
            r"\s+(?:(?:is|was|remains)\s+)?(?:an?\s+)?(?:unverified\s+)?$",
            "",
            part,
            flags=re.IGNORECASE,
        )
    part = re.sub(r"^(?:the|a|an)\s+", "", part.strip(), flags=re.IGNORECASE)
    return occurs(words(part), question)


def publish(saved, memo):
    """Return a derivative of a verified release. Preserve every original response and its hash."""
    if fingerprint(memo) != saved["memo_sha256"]:
        raise CaseError("The research memo differs from its recorded original")
    question = words(saved["question"])
    kept, removed = [], []
    for index, p in enumerate(saved["paragraphs"]):
        sentences = SENTENCE.split(p["text"])
        first = next(
            (i for i, s in enumerate(sentences) if not supplied(s, question)), None
        )
        if first is None:
            kept.append(p)
            continue
        removed.append(index + 1)
        # A subsequent sentence can depend on the discarded premise. A calculation
        # is attributed to an entire paragraph, so keep it only in an intact one.
        if first and not p["calculation_ids"]:
            kept.append({**p, "text": " ".join(sentences[:first])})
    if not removed:
        return saved, memo
    qualified = bool(kept)
    updated = {
        **saved,
        "release": "qualified" if qualified else "withheld",
        "paragraphs": kept,
        "attribution_review": {
            "version": VERSION,
            "source_memo_sha256": saved["memo_sha256"],
            "affected_paragraphs": removed,
        },
    }
    if not kept:
        return {**updated, "memo_sha256": None}, None
    arguments = (
        saved["question"],
        saved["cutoff"],
        kept,
        saved["evidence"],
        saved["calculations"],
        True,
    )
    if "calculation_errors" in saved:
        from .reviewed_research import render

        raw = render(*arguments, inline_locators=True)
    else:
        raw = core.render(*arguments)
    if "direction" in saved:
        direction = saved["direction"]
        if not any(
            p["section"] == "Outlook"
            and re.search(
                r"\b" + re.escape(direction) + r"\b", p["text"], re.IGNORECASE
            )
            for p in kept
        ):
            direction = "undetermined"
        updated["direction"] = direction
        raw = raw.replace(
            b"\n\n**Research question**",
            f"\n\nForecast direction: {direction}\n\n**Research question**".encode(),
            1,
        )
    return {**updated, "memo_sha256": fingerprint(raw)}, raw
