"""Validate financial numbers separately from a case's dates, comparison rule and PDF locators."""

import re
from calendar import month_abbr, month_name
from collections import defaultdict
from datetime import UTC, date, datetime
from decimal import Decimal

from .baseline_evidence import PER_SHARE, PERCENT
from .disney_hulu_case import CaseError
from .paired_research import amounts, explicit_losses

FLAGS = re.IGNORECASE
MONTH = r"(?:" + "|".join((*month_name[1:], *month_abbr[1:], "Sept")) + r")\.?"
DAY = rf"(?:\d{{4}}-\d{{2}}-\d{{2}}|{MONTH}\s+\d{{1,2}}(?:,?\s+\d{{4}})?|\d{{1,2}}\s+{MONTH}(?:\s+\d{{4}})?)"
CLOCK = r"(?:(?:T|\s+(?:at\s+)?)(?P<hour>\d{1,2}):(?P<minute>\d{2})(?::(?P<second>\d{2}))?\s*(?P<zone>Z|UTC|GMT|[+-]\d{2}:?\d{2})?)?"
CUTOFF = re.compile(
    rf"(?P<prefix>\b(?:as (?:of|at)|(?:research )?cutoff(?: date)?(?: (?:was|is|of|on))?)\s+)"
    rf"(?P<day>{DAY}){CLOCK}(?![\w/+\-]|:\d|,?\s+\d{{4}}\b)",
    FLAGS,
)
DATED_OUTLOOK = re.compile(
    rf"\b(?P<day>{DAY})(?=\s+(?:base case|research cutoff)\b)", FLAGS
)
FISCAL = re.compile(r"\b(?:FY\s*\d{4}|Q[1-4])\b", FLAGS)
NUM = r"\d+(?:\.\d+)?"
PAGE_LIST = r"\d+(?:\s*(?:[–—-]|,\s*(?:and\s+)?|\band\s+)\s*\d+)*"
PAGES = re.compile(
    rf"\b(?:PDF\s+)?(?:pp?\.\s*|pages?\s+|p(?=\d))(?P<pages>{PAGE_LIST})"
    r"(?:\s*[,·]?\s*(?:§|section\s+)\s*(?P<section>\d+))?(?![\w.]\d|\w)",
    FLAGS,
)
LOCATOR = re.compile(r"PDF p(\d+) §(\d+)")
UNIT = r"(?:percentage\s+points?|percent\s+points?|basis\s+points?|bps?\b|ratio\s+points?|percent\b|%)"
QUANTITY = re.compile(rf"(?<![\w.,])(?P<n>{NUM})\s*(?P<unit>{UNIT})", FLAGS)
BAND_LEAD = re.compile(
    r"(?:±|\+\s*/\s*-|plus or minus|minus or plus|\bwithin(?: means)?|"
    r"\b(?:band|tolerance|threshold)(?:\s+(?:is|was|of|at))?)\s*$",
    FLAGS,
)
BAND_TAIL = re.compile(r"^\s*(?:inclusive\s+)?(?:band|tolerance|threshold)\b", FLAGS)
BOUNDARY = re.compile(
    r"(?:no more than|more than|less than|at most|at least|exactly|outside|beyond)\s*$",
    FLAGS,
)
FACTOR = re.compile(
    rf"(?P<op>[×*])\s*(?:\(\s*1\s*(?P<sign>[+−-])\s*(?P<offset>{NUM})(?P<pct>%)?\s*\)|(?P<mult>{NUM})(?![\w.]))"
)
BAND_CONTEXT = re.compile(
    r"\b(?:within|band|threshold|tolerance|inclusive|lower edge|upper edge|endpoints?)\b",
    FLAGS,
)
PERIOD_END = re.compile(
    rf"\b(?:fiscal |financial )?year\s+(?:ended|ending)\s+(?P<date>{DAY}|{MONTH}\s+\d{{4}})\b",
    FLAGS,
)
MONTH_YEAR = re.compile(rf"(?P<month>{MONTH})\s+(?P<year>\d{{4}})\b", FLAGS)
BY_HORIZON = re.compile(
    rf"\b(?:by|through|year ending)\s+(?P<day>{MONTH}\s+\d{{4}}|{DAY})\b", FLAGS
)
PUBLICATION = re.compile(
    rf"\b(?P<day>{DAY})(?=\s+(?:consolidated\s+)?(?:forecast table|release|filing|statement)\b)",
    FLAGS,
)


def calendar_day(text, year):
    text = text.replace(",", "").replace(".", "").replace("Sept ", "Sep ").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return date.fromisoformat(text)
    if not re.search(r"\b\d{4}$", text):
        text += f" {year}"
    for fmt in ("%B %d %Y", "%b %d %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    raise CaseError("Invalid calendar date in cutoff")


def without_cutoff(text, case, hits):
    cutoff = datetime.fromisoformat(case.cutoff).astimezone(UTC)

    def check(m):
        try:
            day = calendar_day(m["day"], cutoff.year)
            if m.groupdict().get("hour") is not None:
                zone = m["zone"]
                if not zone:
                    raise CaseError("A timed cutoff needs its timezone")
                offset = "+00:00" if zone.upper() in {"Z", "UTC", "GMT"} else zone
                instant = datetime.fromisoformat(
                    f"{day.isoformat()}T{int(m['hour']):02}:{m['minute']}:{m['second'] or '00'}{offset}"
                )
                correct = instant == cutoff
            else:
                correct = day == cutoff.date()
        except ValueError as exc:
            raise CaseError("Invalid research cutoff date or time") from exc
        if not correct and m.groupdict().get("prefix", "").strip().lower() in {
            "as of",
            "as at",
        }:
            # A dated balance-sheet figure may refer to a distinct historical reporting day.
            # Require an explicitly dated original and a balance-sheet subject in the same sentence.
            around = (
                text[max(text.rfind(".", 0, m.start()) + 1, 0) : m.end()]
                + text[m.end() :].split(".", 1)[0]
            )
            if (
                not m.groupdict().get("hour")
                and day < cutoff.date()
                and re.search(
                    r"\b(?:assets|liabilities|cash balance|balance sheet|outstanding shares)\b",
                    around,
                    FLAGS,
                )
                and not re.search(
                    r"\b(?:base case|cutoff|as.of judgment)\b", around, FLAGS
                )
            ):
                for hit in hits:
                    for dated in re.finditer(
                        rf"\bas (?:of|at)\s+({DAY})\b", hit.text, FLAGS
                    ):
                        if (
                            re.search(r"\d{4}", dated[1])
                            and calendar_day(dated[1], day.year) == day
                        ):
                            return " "
        if not correct:
            raise CaseError("Research cutoff differs from the registered instant")
        return " "

    result = DATED_OUTLOOK.sub(check, CUTOFF.sub(check, text))
    if re.search(
        rf"\b(?:as (?:of|at)|(?:research )?cutoff(?: date)?(?: is| of| on)?)\s+(?:\d|{MONTH}\s+\d)",
        result,
        FLAGS,
    ):
        raise CaseError("Unrecognized research cutoff")
    return result


def without_pages(text, hits):
    by_document = defaultdict(set)
    for hit in hits:
        where = LOCATOR.fullmatch(hit.locator)
        if where:
            by_document[hit.sha256].add((int(where[1]), int(where[2])))

    def check(m):
        raw = m["pages"]
        pages = {int(value) for value in re.findall(r"\d+", raw)}
        for start, end in re.findall(r"(\d+)\s*[-–—]\s*(\d+)", raw):
            lo, hi = int(start), int(end)
            if hi < lo or hi - lo >= len(hits):
                raise CaseError("PDF page range is outside the cited passages")
            pages.update(range(lo, hi + 1))
        section = int(m["section"]) if m["section"] else None
        if section is not None and len(pages) != 1:
            raise CaseError("A section must identify a single cited PDF page")
        if not any(
            pages <= {p for p, s in locs if section is None or s == section}
            for locs in by_document.values()
        ):
            raise CaseError("PDF locator differs from the cited passages")
        return " "

    result = PAGES.sub(check, text)
    if re.search(
        r"\b(?:PDF\s+)?(?:pp?\.\s*|pages?\s+|p(?=\d))\s*[-+]?\d", result, FLAGS
    ):
        raise CaseError("Invalid PDF locator")
    return result


def normalized_band(n, unit, rule):
    unit = unit.lower()
    if rule["mode"] == "relative" and unit in {"%", "percent"}:
        return n / 100
    if rule["mode"] == "absolute":
        if rule["unit"] in {"percent", "annualized percent"}:
            if unit.startswith(("basis", "bp")):
                return n / 100
            if unit.startswith("percent") and "point" in unit:
                return n
        elif rule["unit"] == "ratio" and unit.startswith("ratio"):
            return n
    raise CaseError("Comparison band unit differs from the registered rule")


def without_band(text, rule):
    def quantity(m):
        before, after = text[: m.start()], text[m.end() :]
        clause = (
            re.split(r"[.;](?!\d)", before)[-1]
            + m[0]
            + re.split(r"[.;](?!\d)", after)[0]
        )
        boundary = (
            BOUNDARY.search(before)
            and re.match(r"\s+(?:above|below|either side)\b", after, FLAGS)
            and re.search(
                r"\b(?:guide|guidance|within|base case|tolerance|the band)\b",
                clause,
                FLAGS,
            )
        )
        lead = BAND_LEAD.search(before)
        signed = lead and lead[0].strip().lower() in {
            "±",
            "+/-",
            "plus or minus",
            "minus or plus",
        }
        anchored = lead and (
            not signed
            or re.search(
                r"\b(?:within|band|tolerance|threshold)(?:\s+(?:is|of|at|inclusive))?\s*$",
                before[: lead.start()],
                FLAGS,
            )
            or re.match(
                r"\s+of (?:the guide|guidance|that amount|the comparator)\b",
                after,
                FLAGS,
            )
        )
        if not (
            (anchored and BAND_CONTEXT.search(clause))
            or BAND_TAIL.search(after)
            or boundary
        ):
            return m[0]
        if not rule or normalized_band(Decimal(m["n"]), m["unit"], rule) != Decimal(
            rule["tolerance"]
        ):
            raise CaseError("Comparison band differs from the registered tolerance")
        if re.search(
            r"\b(?:exclusive|excluding (?:both |the )?endpoints)\b", clause, FLAGS
        ):
            raise CaseError("The registered comparison band includes its endpoints")
        return " "

    # A comparator coefficient is permitted only inside an explicit band formula. The base amount
    # remains in the claim, so it must independently match a source or a checked calculation.
    def factor(m):
        if not rule or rule["mode"] != "relative" or not BAND_CONTEXT.search(text):
            return m[0]
        tolerance = Decimal(rule["tolerance"])
        if m["offset"] is not None:
            value = Decimal(m["offset"]) / (100 if m["pct"] else 1)
            valid = value == tolerance
        else:
            value = Decimal(m["mult"])
            valid = value in (1 - tolerance, 1 + tolerance)
        if not valid:
            raise CaseError(
                "Comparison multiplier differs from the registered tolerance"
            )
        return " "

    # Keep the currency and base amount in a bracketed product, e.g. JPY (100 × 1.02) million.
    text = re.sub(
        rf"\((\d[\d,.]*)\s*({FACTOR.pattern})\s*\)",
        lambda m: m[1] if FACTOR.sub(factor, m[2]).isspace() else m[0],
        text,
    )
    text = FACTOR.sub(factor, text)
    return QUANTITY.sub(quantity, text)


def without_horizon(text, case, opened):
    # A question may specify only “year ending March 2021”. A day-level claim also
    # needs the actual fiscal period end in an opened original from this case.
    periods = set()
    exact = set()
    for m in PERIOD_END.finditer(case.question):
        value = m["date"]
        month = MONTH_YEAR.fullmatch(value)
        if month:
            day = calendar_day("1 " + value, int(month["year"]))
        elif re.search(r"\d{4}", value):
            day = calendar_day(value, int(case.cutoff[:4]))
            exact.add(day)
        else:
            continue
        periods.add((day.year, day.month))
    if not periods:
        return text
    for hit in opened:
        for m in PERIOD_END.finditer(hit.text):
            if not MONTH_YEAR.fullmatch(m["date"]) and re.search(r"\d{4}", m["date"]):
                day = calendar_day(m["date"], int(case.cutoff[:4]))
                if (day.year, day.month) in periods:
                    exact.add(day)

    def check(m):
        value = m["day"]
        month = MONTH_YEAR.fullmatch(value)
        if month:
            day = calendar_day("1 " + value, int(month["year"]))
            return " " if (day.year, day.month) in periods else m[0]
        if any(calendar_day(value, day.year) == day for day in exact):
            return " "
        return m[0]

    return BY_HORIZON.sub(check, text)


def financial_text(text, case, hits, opened):
    text = without_band(
        without_pages(without_cutoff(text, case, hits), hits), case.forecast_rule
    )
    text = without_horizon(text, case, opened)

    def publication(m):
        days = [date.fromisoformat(h.published[:10]) for h in hits]
        if not any(calendar_day(m["day"], day.year) == day for day in days):
            raise CaseError("Publication date differs from the cited original")
        return " "

    text = PUBLICATION.sub(publication, text)
    years = set(re.findall(r"\b(?:19|20)\d\d\b|(?<=FY)\d{4}\b", case.question, FLAGS))

    def period(m):
        fiscal = m[0].upper().replace(" ", "")
        if fiscal.startswith("FY") and fiscal[2:] in years:
            return " "
        if fiscal.startswith("Q") and re.search(rf"\b{fiscal}\b", case.question, FLAGS):
            return " "
        return m[0]

    return FISCAL.sub(period, text)


def table_percentages(text):
    """Recover signed percentage cells from financial tables that label their year-on-year columns."""
    result = set()
    if re.search(r"percentages .*? (?:increase|decrease)", text, FLAGS):
        header = ""
        for line in text.splitlines():
            if line.startswith("Year ") and "|" in line:
                if "%" in header and _UNIT.search(header):
                    result.update(
                        Decimal(n) for n in re.findall(r"\((\d+\.\d+)\)", line)
                    )
            else:
                header = line
    return result


_CURRENCIES = {
    "JPY": ("JPY", "yen", "¥"),
    "USD": ("USD", "US$", "$", "dollars"),
    "EUR": ("EUR", "euros", "euro", "€"),
    "GBP": ("GBP", "pounds", "£"),
}
_CODES = {word.lower(): code for code, words in _CURRENCIES.items() for word in words}
_CURRENCY = (
    "(?:" + "|".join(re.escape(c) for c in sorted(_CODES, key=len, reverse=True)) + ")"
)
_SCALES = {
    "thousand": 1000,
    "million": 1000000,
    "billion": 1000000000,
    "trillion": 1000000000000,
}
_SCALE = "(?:" + "|".join(_SCALES) + ")"
_VALUE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_MONEY = re.compile(
    rf"(?<!\w)(?P<sign>[-−])?(?:(?P<c1>{_CURRENCY})\s*(?P<sign1>[-−])?(?P<v1>{_VALUE})\s+(?P<s1>{_SCALE})\b|"
    rf"(?P<v2>{_VALUE})\s+(?P<s2>{_SCALE})\s+(?P<c2>{_CURRENCY})(?!\w))",
    FLAGS,
)
_UNIT = re.compile(
    rf"(?P<currency>{_CURRENCY})\s+(?P<scale>{_SCALE})|(?P<scale2>{_SCALE})\s+(?P<currency2>{_CURRENCY})",
    FLAGS,
)


def money(m):
    return (
        _CODES[(m["c1"] or m["c2"]).lower()],
        Decimal((m["v1"] or m["v2"]).replace(",", ""))
        * _SCALES[(m["s1"] or m["s2"]).lower()]
        * (-1 if m["sign"] or m["sign1"] else 1),
    )


def supported_money(hits, visual, calculations):
    values = set()
    for text in [h.text for h in hits] + ([visual] if visual else []):
        values.update(money(m) for m in _MONEY.finditer(" ".join(text.split())))
        header = ""
        for line in text.splitlines():
            if not line.startswith("Year "):
                header = line
                continue
            table_unit = _UNIT.search(header)
            if table_unit:
                code = _CODES[
                    (table_unit["currency"] or table_unit["currency2"]).lower()
                ]
                scale = _SCALES[(table_unit["scale"] or table_unit["scale2"]).lower()]
                # In a mixed, collapsed layout, admit unambiguous grouped integer amount cells.
                for cell in re.findall(
                    r"(?:^|\|)\s*(\d{1,3}(?:,\d{3})+)(?=\s*(?:\||\(|$))", line
                ):
                    values.add((code, Decimal(cell.replace(",", "")) * scale))
    for calc in calculations:
        unit = _UNIT.fullmatch(calc["unit"])
        if unit:
            code = _CODES[(unit["currency"] or unit["currency2"]).lower()]
            scale = _SCALES[(unit["scale"] or unit["scale2"]).lower()]
            value = Decimal(calc["value"])
            values.update(
                (code, n * scale) for n in (value, *(round(value, p) for p in range(5)))
            )
    return values


def check_numbers(text, case, hits, *, opened=None, calculations=(), visual=None):
    claims = financial_text(text, case, hits, opened if opened is not None else hits)
    evidence = "\n".join(h.text for h in hits)
    if visual:
        evidence += "\n" + visual
    supported = amounts(evidence)
    percents = {Decimal(x) for x in PERCENT.findall(evidence)}
    for hit in hits:
        percents.update(table_percentages(hit.text))
    losses = explicit_losses(evidence)
    for n in re.findall(
        r"(?:decreased|fell|declined)(?: at an annual rate of| by)? (\d+(?:\.\d+)?)",
        evidence,
        FLAGS,
    ):
        losses.add(-Decimal(n))
    for calc in calculations:
        signed = Decimal(calc["value"])
        rounded = {signed, *(round(signed, p) for p in range(5))}
        supported.update(abs(n) for n in rounded)
        if signed < 0:
            losses.update(rounded)
        if calc["unit"].lower() in {"%", "percent", "percentage", "percentage points"}:
            percents.update(abs(n) for n in rounded)
    if set(map(Decimal, PER_SHARE.findall(claims))) - set(
        map(Decimal, PER_SHARE.findall(evidence))
    ):
        raise CaseError("Unsupported per-share price in cited evidence")
    monetary = supported_money(hits, visual, calculations)

    def check_money(m):
        code, value = money(m)
        decline = re.search(
            r"\b(?:decline|decrease|drop|reduction|loss|fell|decreased)(?: of| by)?\s*$",
            claims[: m.start()],
            FLAGS,
        )
        if (code, value) not in monetary and not (
            decline and value > 0 and (code, -value) in monetary
        ):
            raise CaseError(
                "Unsupported monetary amount, currency or scale in cited evidence: "
                + m[0]
            )
        return " "

    claims = _MONEY.sub(check_money, claims)
    missing = amounts(claims) - supported
    unsupported_rates = {Decimal(x) for x in PERCENT.findall(claims)} - percents
    if missing or unsupported_rates or explicit_losses(claims) - losses:
        figures = sorted(
            str(n)
            for n in missing | unsupported_rates | (explicit_losses(claims) - losses)
        )
        raise CaseError(
            "Unsupported financial figure in cited evidence or checked calculations: "
            + ", ".join(figures)
        )
