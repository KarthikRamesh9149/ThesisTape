"""Verify publisher editions and extract addressable passages from pinned source bytes."""

import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from urllib.parse import quote, urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pypdf import PdfReader

from .disney_hulu_case import CaseError, filing_blocks, read_saved_file, verify_index
from .edgar_exhibits import sections as exhibit_sections

DAY = re.compile(r"\d{4}-\d{2}-\d{2}")
STAMP = re.compile(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)")
SUFFIX = {"edgar": ".htm", "pdf": ".pdf", "wikinews": ".json"}


def instant(value: str, day_zone: str | None = None) -> datetime:
    """Use the latest possible instant for a publisher that only gives a civil date."""
    if isinstance(value, str) and DAY.fullmatch(value) and day_zone:
        stamp = datetime.combine(date.fromisoformat(value), time.max, ZoneInfo(day_zone))
    elif isinstance(value, str) and STAMP.fullmatch(value) and not value.endswith("-00:00"):
        stamp = datetime.fromisoformat(value)
    else:
        raise ValueError("A dated release needs an unambiguous timezone and date")
    return stamp.astimezone(UTC)


def availability(source: dict) -> datetime | None:
    try:
        zone = source.get("timezone")
        return max(instant(source["published"], zone), instant(source["version_at"], zone))
    except (KeyError, TypeError, ValueError, ZoneInfoNotFoundError):
        return None


def object_bytes(directory: Path, digest: str, suffix: str) -> bytes:
    if not re.fullmatch(r"[a-f0-9]{64}", digest):
        raise ValueError("Invalid object digest")
    data = read_saved_file(directory / (digest + suffix))
    if sha256(data).hexdigest() != digest:
        raise ValueError("Archive object failed its SHA-256 check")
    return data


def compact(text: str) -> str:
    return " ".join(text.split())


@dataclass(frozen=True)
class Block:
    locator: str
    text: str
    fragment: str


def prose(locator: str, text: str) -> Block:
    text = compact(text)
    snippet = " ".join(text.split()[:15])
    fragment = ":~:text=" + quote(snippet, safe="").replace("-", "%2D")
    return Block(locator, text, fragment)


def edgar_blocks(source, original, objects):
    sel = source["selection"]
    accession = source["edition"]
    url = urlsplit(source["url"])
    parts = url.path.strip("/").split("/")
    if (
        url.netloc != "www.sec.gov"
        or len(parts) != 6
        or parts[:3] != ["Archives", "edgar", "data"]
        or not parts[3].isdigit()
        or parts[4] != accession.replace("-", "")
        or source["proof_url"] != source["url"].rsplit("/", 1)[0] + "/" + accession + "-index.htm"
    ):
        raise ValueError("EDGAR edition has an inconsistent archive address")
    index = object_bytes(objects, sel["index_sha256"], ".htm")
    match = re.search(rb'Accepted</div>\s*<div class="info">([\d: -]+)</div>', index)
    if not match:
        raise ValueError("Missing EDGAR acceptance time")
    accepted = datetime.strptime(match[1].decode(), "%Y-%m-%d %H:%M:%S").replace(
        tzinfo=ZoneInfo("America/New_York")
    )
    if accepted != instant(source["published"]) or accepted != instant(source["version_at"]):
        raise ValueError("EDGAR acceptance time differs from this edition")
    if "exhibit" in sel:
        for label, text in exhibit_sections(source, original, index):
            yield prose(label, text)
        return
    source_info = {
        "source_url": source["url"], "accession": accession, "file": parts[-1],
        "filing_date": source["published"][:10], "title": sel["document_title"],
    }
    verify_index(index, source_info, len(original), sel["form"])
    for section in sel["sections"]:
        paragraphs = filter(None, filing_blocks(original, {**source_info, "section": section}))
        for i, text in enumerate(paragraphs, 1):
            yield prose(f"{section} ¶{i}", text)


def table_sections(page: str, headings: list[str]):
    """Keep the lines, column spacing, units, periods and notes of each table together."""
    lines = page.splitlines()
    starts = []
    for heading in headings:
        matches = [i for i, line in enumerate(lines) if compact(line).startswith(heading)]
        if len(matches) != 1:
            raise ValueError("Table heading missing or ambiguous")
        starts.append(matches[0])
    if not starts or starts != sorted(set(starts)):
        raise ValueError("Invalid table section order")
    if starts[0] > 0:
        starts.insert(0, 0)
    for begin, end in zip(starts, starts[1:] + [len(lines)]):
        yield "\n".join(line.rstrip() for line in lines[begin:end]).strip("\n")


def verify_release_address(source, printed):
    """Each PDF adapter admits edition-specific publisher addresses and date evidence."""
    url = urlsplit(source["url"])
    proof = urlsplit(source["proof_url"])
    pub = source["published"]
    if url.hostname == "www.bea.gov":
        address = r"/sites/default/files/\d{4}-\d{2}/[A-Za-z0-9_]+\.pdf"
        eastern = instant(pub).astimezone(ZoneInfo("America/New_York"))
        meridiem = "a.m." if eastern.hour < 12 else "p.m."
        clock = f"{eastern.hour % 12 or 12}:{eastern.minute:02d} {meridiem}"
        valid = (
            proof.hostname == "www.bea.gov"
            and eastern.strftime("/%Y-%m/") in url.path
            and eastern.strftime("/news/%Y/") in proof.path
            and printed.startswith(clock + " " + eastern.tzname() + ",")
        )
    elif url.hostname == "global.toyota":
        address = r"/pages/global_toyota/ir/financial-results/\d{4}_[1-4]q_summary_en\.pdf"
        valid = (
            proof.hostname == "global.toyota"
            and "/archives/" in proof.path
            and DAY.fullmatch(pub)
            and source.get("timezone") == "Asia/Tokyo"
        )
    elif url.hostname == "www.bde.es":
        day = date.fromisoformat(pub)
        address = r"/f/webbe/.+/" + day.strftime("%y/pm%d%m%y") + r"en\.pdf"
        valid = (
            proof.hostname == "www.ecb.europa.eu"
            and day.strftime("mp%y%m%d") in proof.path
            and source.get("timezone") == "Europe/Berlin"
        )
    else:
        raise ValueError("No historical edition adapter for this PDF publisher")
    if not valid or not re.fullmatch(address, url.path) or url.query or proof.scheme != "https":
        raise ValueError("Publisher address or embargo does not identify this historical edition")


def pdf_blocks(source, original):
    sel = source["selection"]
    reader = PdfReader(BytesIO(original), strict=True)
    if not urlsplit(source["url"]).path.endswith(".pdf"):
        raise ValueError("Expected an edition-specific PDF address")
    dates = [stamp for stamp in (reader.metadata.modification_date, reader.metadata.creation_date) if stamp]
    version = instant(source["version_at"], source.get("timezone"))
    if not dates or any(stamp.tzinfo is None or stamp > version for stamp in dates):
        raise ValueError("The PDF version cannot be dated to the claimed availability")
    if any(type(p) is not int or p < 1 or p > len(reader.pages) for p in sel["pages"]):
        raise ValueError("Page selection falls outside this PDF")
    pages = {p: reader.pages[p - 1].extract_text(extraction_mode="layout") for p in sel["pages"]}
    printed = sel["dateline"]
    verify_release_address(source, printed)
    pubday = date.fromisoformat(source["published"][:10])
    day_forms = (f"{pubday:%B} {pubday.day}, {pubday.year}", f"{pubday.day} {pubday:%B %Y}")
    if not any(form in printed for form in day_forms) or printed not in compact(pages[sel["dateline_page"]]):
        raise ValueError("Publication dateline missing in the pinned PDF")
    for n, page in pages.items():
        headings = sel.get("headings", {}).get(str(n))
        chunks = table_sections(page, headings) if headings else map(compact, re.split(r"\n\s*\n", page))
        excluded = tuple(sel.get("exclude_blocks_starting_with", []))
        for i, text in enumerate(chunks, 1):
            if len(text.strip()) >= 36 and not compact(text).startswith(excluded):
                phrase = '"' + " ".join(text.split()[:8]) + '"'
                yield Block(f"PDF p{n} §{i}", text, f"page={n}&search=" + quote(phrase, safe=""))


def wiki_blocks(source, original):
    (page,) = json.loads(original)["query"]["pages"]
    (revision,) = page["revisions"]
    body = revision["slots"]["main"]["content"]
    rid = str(revision["revid"])
    revision_time = instant(revision["timestamp"])
    if (
        page["title"] != source["title"]
        or rid != source["edition"]
        or source["url"] != "https://en.wikinews.org/w/index.php?oldid=" + rid
        or revision_time != instant(source["version_at"])
        or revision_time != instant(source["published"])
        or "{{publish}}" not in body
    ):
        raise ValueError("Wikinews source is not the dated, published revision")
    # Read only article prose from the fixed revision. Never expand today's templates or links.
    body = re.split(r"(?m)^==|^\{\{haveyoursay\}\}", body, maxsplit=1)[0]
    article = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(("{{", "[[Image:", "[[File:", "<")):
            continue
        line = re.sub(r"\[\[([^\[\]]+)\]\]", lambda m: m[1].split("|")[-1].removeprefix("w:"), line)
        if "[[" in line or "{{" in line:
            raise ValueError("Unsupported markup in archived news prose")
        article.append(line.replace("''", ""))
    for i, paragraph in enumerate(article, 1):
        yield prose(f"Article ¶{i}", paragraph)


def extract(source: dict, objects: Path) -> tuple[Block, ...]:
    kind = source["kind"]
    original = object_bytes(objects, source["sha256"], SUFFIX[kind])
    if kind == "edgar":
        blocks = edgar_blocks(source, original, objects)
    elif kind == "pdf":
        blocks = pdf_blocks(source, original)
    else:
        blocks = wiki_blocks(source, original)
    blocks = tuple(blocks)
    if (
        not blocks
        or len({b.locator for b in blocks}) != len(blocks)
        or len({b.fragment for b in blocks}) != len(blocks)
    ):
        raise CaseError("Edition has missing or duplicate passage locators")
    return blocks
