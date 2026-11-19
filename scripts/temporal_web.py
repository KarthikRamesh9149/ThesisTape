"""Dated research over historical web captures, SEC disclosures, and fixed encyclopedia revisions."""

import io
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import ClassVar
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pypdf import PdfReader

from .disney_hulu_case import CaseError
from .run_checkpoint import publish
from .sol_baseline import fingerprint, json_bytes

USER_AGENT = "ThesisTape/1.0 (financial research; https://github.com/KarthikRamesh9149/ThesisTape)"
SERVICE_HOSTS = {
    "lite.duckduckgo.com",
    "archive.org",
    "web.archive.org",
    "en.wikipedia.org",
    "efts.sec.gov",
    "www.sec.gov",
    "data.sec.gov",
}
WORD = re.compile(r"[a-z0-9]+", re.IGNORECASE)
STOP = frozenset(
    [
        "and",
        "are",
        "for",
        "the",
        "from",
        "with",
        "that",
        "into",
        "how",
        "what",
        "has",
        "can",
        "its",
        "was",
        "versus",
        "this",
        "who",
        "their",
        "will",
        "annual",
        "report",
        "after",
        "before",
        "around",
        "should",
        "have",
        "which",
        "financial",
        "earnings",
        "company",
        "investor",
        "expected",
        "public",
        "statements",
        "had",
        "been",
        "s",
        "to",
        "in",
        "of",
        "an",
        "on",
        "as",
        "at",
        "or",
        "a",
        "is",
        "by",
    ]
)
MAX_BODY = 24_000_000
MAX_BLOCK = 2200
RATE_LOCK = threading.Lock()
NEXT = {}
DELAYS = {
    "efts.sec.gov": 0.5,
    "data.sec.gov": 0.5,
    "www.sec.gov": 0.5,
    "lite.duckduckgo.com": 1.2,
    "archive.org": 0.75,
    "web.archive.org": 0.6,
    "en.wikipedia.org": 0.25,
}
log = logging.getLogger(__name__)


def safe_origin(url):
    if (
        not isinstance(url, str)
        or len(url) > 2300
        or any(c.isspace() or ord(c) < 32 for c in url)
    ):
        return False
    try:
        parts = urlsplit(url)
        return (
            parts.scheme in {"https", "http"}
            and parts.username is None
            and parts.password is None
            and parts.port in (None, 80, 443)
            and bool(
                re.fullmatch(r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,63}", parts.hostname or "")
            )
            and not (parts.hostname or "").endswith(
                (".internal", ".local", ".localhost")
            )
        )
    except ValueError:
        return False


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def cutoff_day(value):
    try:
        result = date.fromisoformat(value)
    except (ValueError, TypeError) as e:
        raise CaseError("Use an exact YYYY-MM-DD research cutoff") from e
    if result.isoformat() != value:
        raise CaseError("Use an exact YYYY-MM-DD research cutoff")
    return result


class PublicHttp:
    """Only fixed public archive/discovery hosts are fetched. Publisher URLs are archive arguments."""

    def __call__(self, url, *, max_bytes=MAX_BODY):
        host = urlsplit(url).hostname
        if urlsplit(url).scheme != "https" or host not in SERVICE_HOSTS:
            raise CaseError("Unapproved archive service")
        with RATE_LOCK:
            now = time.monotonic()
            slot = max(now, NEXT.get(host, now))
            NEXT[host] = slot + DELAYS[host]
        time.sleep(max(0, slot - now))
        req = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Encoding": "gzip",
                "Accept": "*/*",
            },
        )
        with build_opener(NoRedirect()).open(req, timeout=15) as resp:
            if resp.status != 200:
                raise CaseError("Incomplete archive response")
            body = resp.read(max_bytes + 1)
            if len(body) > max_bytes:
                raise CaseError("Source exceeds the archive byte limit")
            if resp.headers.get("Content-Encoding", "").lower() == "gzip":
                import gzip

                with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
                    body = gz.read(max_bytes + 1)
                if len(body) > max_bytes:
                    raise CaseError("Decoded archive exceeds the size limit")
            return body


def words(value):
    return {
        s.lower() for s in WORD.findall(value) if len(s) > 1 and s.lower() not in STOP
    }


def rank_blocks(blocks, query, limit=5):
    terms = words(query)

    def score(row):
        text = row[1]
        terms_hit = len(terms & words(text))
        return (terms_hit, int(bool(re.search(r"\d[%.,\d]|\$\d", text))), -row[0])

    choices = sorted(enumerate(blocks), key=score, reverse=True)[:limit]
    return [b for _, b in sorted(choices)]


class ResultLinks(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.urls = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag != "a" or "result-link" not in attrs.get("class", "").split():
            return
        url = attrs.get("href", "")
        redirect = urlsplit(url)
        if redirect.hostname == "duckduckgo.com":
            candidates = parse_qs(redirect.query).get("uddg", [])
            url = candidates[0] if len(candidates) == 1 else ""
        if safe_origin(url) and url not in self.urls:
            self.urls.append(url)


class Article(HTMLParser):
    SKIP: ClassVar = {
        "nav",
        "header",
        "footer",
        "aside",
        "script",
        "style",
        "form",
        "iframe",
        "template",
        "noscript",
        "svg",
    }
    TEXT: ClassVar = {"h1", "h2", "h3", "p", "li", "tr", "td"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.suppressed = []
        self.block = None
        self.current = []
        self.texts = []
        self.title = ""

    def close_block(self):
        value = " ".join("".join(self.current).split())
        if self.block == "title":
            self.title = value
        elif len(value) >= 25 and value not in self.texts:
            self.texts.append(value)
        self.block = None
        self.current = []

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.suppressed.append(tag)
        if self.suppressed:
            return
        if tag in self.TEXT or tag == "title":
            self.close_block()
            self.block = tag
        elif tag == "br":
            self.current.append(" ")

    def handle_endtag(self, tag):
        if self.suppressed:
            if tag == self.suppressed[-1]:
                self.suppressed.pop()
            return
        if self.block == tag:
            self.close_block()

    def handle_data(self, data):
        if self.block and not self.suppressed:
            self.current.append(data)


def blocks_of(lines):
    seen = set()
    parts = []
    for line in lines:
        line = " ".join(line.split())
        if len(line) < 25 or line in seen:
            continue
        seen.add(line)
        # Preserve exact source wording in contiguous spans, also for unusually large paragraphs.
        while len(line) > MAX_BLOCK:
            end = line.rfind(" ", 0, MAX_BLOCK)
            if end <= 0:
                end = MAX_BLOCK
            parts.append(line[:end])
            line = line[end:].lstrip()
        if line:
            parts.append(line)
    return parts


def article(raw):
    if raw.startswith(b"%PDF"):
        reader = PdfReader(io.BytesIO(raw), strict=False)
        if len(reader.pages) > 320:
            raise CaseError("PDF exceeds the page limit")
        blocks = blocks_of((p.extract_text() or "") for p in reader.pages)
        return "Archived PDF", blocks
    parser = Article()
    parser.feed(raw.decode("utf-8", errors="replace"))
    parser.close_block()
    return parser.title, blocks_of(parser.texts)


def doc(title, url, dated, kind, proof, blocks):
    return {
        "title": title[:350],
        "url": url,
        "available": dated,
        "kind": kind,
        "proof": proof,
        "blocks": blocks,
    }


def checked_doc(item, cutoff):
    if (
        not isinstance(item, dict)
        or not safe_origin(item.get("url"))
        or not item.get("blocks")
    ):
        raise CaseError("Expected a dated nonempty source")
    proof = item["proof"]
    available = cutoff_day(item["available"])
    if available > cutoff_day(cutoff):
        raise CaseError("Edition exceeds the research cutoff")
    if not re.fullmatch("[a-f0-9]{64}", proof["original_sha256"]):
        raise CaseError("Invalid source fingerprint")
    if item["kind"] == "wayback":
        stamp = proof["capture"]
        if (
            not re.fullmatch(r"\d{14}", stamp)
            or datetime.strptime(stamp, "%Y%m%d%H%M%S").date() != available
            or not safe_origin(proof["original_url"])
            or item["url"]
            != "https://web.archive.org/web/" + stamp + "/" + proof["original_url"]
        ):
            raise CaseError("Invalid archived URL or capture date")
    elif item["kind"] == "wikimedia_revision":
        ts = datetime.fromisoformat(proof["timestamp"]).astimezone(UTC)
        if (
            type(proof["revision"]) is not int
            or proof["revision"] <= 0
            or ts.date() != available
            or item["url"]
            != f"https://en.wikipedia.org/w/index.php?oldid={proof['revision']}"
        ):
            raise CaseError("Invalid dated revision")
    elif item["kind"] in {"sec_filing", "sec_xbrl"}:
        accession = proof["accession"]
        if (
            not re.fullmatch(r"\d{10}-\d{2}-\d{6}", accession)
            or proof["filed"] != item["available"]
            or not re.fullmatch(r"[1-9]\d{0,9}", proof["cik"])
        ):
            raise CaseError("Invalid accession provenance")
        prefix = f"https://www.sec.gov/Archives/edgar/data/{proof['cik']}/{accession.replace('-', '')}/"
        if not item["url"].startswith(prefix) or not re.fullmatch(
            r"[\w.-]+\.html?", item["url"][len(prefix) :], re.IGNORECASE
        ):
            raise CaseError("Invalid filing URL")
    else:
        raise CaseError("Unknown source provenance")
    return item


def candidates(http, query, max_date):
    day = cutoff_day(max_date)
    window = day - timedelta(days=365)
    # The discovery service receives the date window; no snippets, result titles or raw URLs reach a model.
    raw = http(
        "https://lite.duckduckgo.com/lite/?"
        + urlencode({"q": query, "df": f"{window}..{day}"}),
        max_bytes=1000000,
    )
    parser = ResultLinks()
    parser.feed(raw.decode(errors="replace"))
    return parser.urls


def archived(http, url, max_date):
    if not safe_origin(url):
        return None
    end = cutoff_day(max_date)
    for offset in (0, 14, 90):
        ts = (end - timedelta(days=offset)).strftime("%Y%m%d") + "235959"
        lookup = json.loads(
            http(
                "https://archive.org/wayback/available?"
                + urlencode({"url": url, "timestamp": ts}),
                max_bytes=30000,
            )
        )
        s = lookup.get("archived_snapshots", {}).get("closest", {})
        stamp = s.get("timestamp", "")
        if (
            str(s.get("status")) != "200"
            or s.get("available") is not True
            or not re.fullmatch(r"\d{14}", stamp)
            or stamp > end.strftime("%Y%m%d") + "235959"
        ):
            continue
        snap = urlsplit(s.get("url", ""))
        prefix = "/web/" + stamp + "/"
        if snap.hostname != "web.archive.org" or not snap.path.startswith(prefix):
            continue
        original = snap.path[len(prefix) :] + ("?" + snap.query if snap.query else "")

        def identity(v):
            u = urlsplit(v)
            return (u.hostname, unquote(u.path).rstrip("/"), u.query)

        if not safe_origin(original) or identity(original) != identity(url):
            continue
        raw = http("https://web.archive.org/web/" + stamp + "id_/" + original)
        title, blocks = article(raw)
        if not blocks:
            continue
        dated = datetime.strptime(stamp, "%Y%m%d%H%M%S").date().isoformat()
        source = doc(
            title or urlsplit(original).hostname,
            "https://web.archive.org/web/" + stamp + "/" + original,
            dated,
            "wayback",
            {
                "capture": stamp,
                "original_url": original,
                "original_sha256": fingerprint(raw),
            },
            blocks,
        )
        return checked_doc(source, max_date)
    return None


def wiki(http, title, max_date, *, redirect=True):
    day = cutoff_day(max_date)
    if not title.strip() or len(title) > 180 or "|" in title or ":" in title:
        return None, []
    params = {
        "action": "query",
        "format": "json",
        "formatversion": "2",
        "prop": "revisions",
        "rvstart": f"{day}T23:59:59Z",
        "rvdir": "older",
        "rvlimit": "1",
        "rvprop": "ids|timestamp|content",
        "rvslots": "main",
        "titles": title,
    }
    raw = http(
        "https://en.wikipedia.org/w/api.php?" + urlencode(params), max_bytes=3000000
    )
    pages = json.loads(raw).get("query", {}).get("pages", [])
    rev = pages[0].get("revisions", []) if len(pages) == 1 else []
    if not rev:
        return None, []
    r = rev[0]
    stamp = r["timestamp"]
    when = datetime.fromisoformat(stamp).astimezone(UTC).date()
    if when > day:
        return None, []
    text = r["slots"]["main"].get("content", "")
    target = re.match(r"\s*#redirect\s*\[\[([^]|#]+)", text, re.IGNORECASE)
    if target:
        return (
            wiki(http, target[1], max_date, redirect=False) if redirect else (None, [])
        )
    # Use stored wikitext, without expanding templates against the present-day encyclopedia.
    lines = [
        line
        for line in text.splitlines()
        if len(line.strip()) > 55 and line[0].isalpha()
    ]
    blocks = blocks_of(lines)
    if not blocks:
        return None, []
    urls = [
        unescape(s).rstrip(".,;")
        for s in re.findall(r"https?://[^\s|\]}<>{\"']+", text)
    ]
    urls = list(
        dict.fromkeys(
            u
            for u in urls
            if safe_origin(u)
            and not urlsplit(u).hostname.endswith(
                ("wikipedia.org", "wikimedia.org", "archive.org")
            )
        )
    )
    source = doc(
        title,
        f"https://en.wikipedia.org/w/index.php?oldid={r['revid']}",
        when.isoformat(),
        "wikimedia_revision",
        {
            "revision": r["revid"],
            "timestamp": stamp,
            "original_sha256": fingerprint(text.encode()),
        },
        blocks,
    )
    return checked_doc(source, max_date), urls


def sec_candidates(http, term, max_date):
    day = cutoff_day(max_date)
    if not term.strip():
        return []
    p = {
        "q": term[:180],
        "dateRange": "custom",
        "startdt": str(day - timedelta(days=550)),
        "enddt": str(day),
        "from": 0,
    }
    raw = http(
        "https://efts.sec.gov/LATEST/search-index?" + urlencode(p), max_bytes=3500000
    )
    result = []
    for hit in json.loads(raw).get("hits", {}).get("hits", []):
        s = hit.get("_source", {})
        filed = s.get("file_date", "")
        acc, name_sep, name = hit.get("_id", "").partition(":")
        if not (
            name_sep
            and re.fullmatch(r"\d{10}-\d{2}-\d{6}", acc)
            and re.fullmatch(r"[\w.-]+\.html?", name, re.IGNORECASE)
            and cutoff_day(filed) <= day
            and (s.get("adsh") or acc) == acc
        ):
            continue
        ciks = [
            str(int(c))
            for c in s.get("ciks", [])
            if re.fullmatch(r"\d{1,10}", c) and int(c) > 0
        ]
        if not ciks:
            continue
        result.append(
            {"cik": ciks[0], "accession": acc, "filed": filed, "filename": name}
        )
    return result


def sec_doc(http, s, max_date):
    if cutoff_day(s["filed"]) > cutoff_day(max_date):
        return None
    url = f"https://www.sec.gov/Archives/edgar/data/{s['cik']}/{s['accession'].replace('-', '')}/{s['filename']}"
    raw = http(url)
    if any(
        token in raw[:18000].lower()
        for token in (
            b"request rate threshold exceeded",
            b"undeclared automated tool",
            b"access denied",
            b"verify you are human",
        )
    ):
        raise CaseError("Filing service returned an access page")
    title, blocks = article(raw)
    if not blocks:
        return None
    return checked_doc(
        doc(
            title or f"SEC filing {s['accession']}",
            url,
            s["filed"],
            "sec_filing",
            {**s, "original_sha256": fingerprint(raw)},
            blocks,
        ),
        max_date,
    )


CONCEPTS = (
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "NetIncomeLoss",
    "OperatingIncomeLoss",
    "GrossProfit",
    "Assets",
    "CashAndCashEquivalentsAtCarryingValue",
    "NetCashProvidedByUsedInOperatingActivities",
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "EarningsPerShareDiluted",
    "LongTermDebtCurrent",
    "LongTermDebtNoncurrent",
    "ResearchAndDevelopmentExpense",
    "CommonStocksIncludingAdditionalPaidInCapital",
)


def sec_facts(http, cik, max_date):
    day = cutoff_day(max_date)
    if not re.fullmatch(r"[1-9]\d{0,9}", cik):
        return []
    raw = http(
        f"https://data.sec.gov/api/xbrl/companyfacts/CIK{int(cik):010d}.json",
        max_bytes=50000000,
    )
    facts = json.loads(raw, parse_float=Decimal)
    if int(facts["cik"]) != int(cik):
        return []
    grouped = {}
    for concept in CONCEPTS:
        for unit, reported in (
            facts.get("facts", {})
            .get("us-gaap", {})
            .get(concept, {})
            .get("units", {})
            .items()
        ):
            for f in reported:
                if (
                    not re.fullmatch(r"\d{10}-\d{2}-\d{6}", f["accn"])
                    or f.get("form")
                    not in (
                        "10-Q",
                        "10-K",
                        "20-F",
                        "40-F",
                        "10-Q/A",
                        "10-K/A",
                        "20-F/A",
                    )
                    or cutoff_day(f["filed"]) > day
                    or cutoff_day(f["end"]) > cutoff_day(f["filed"])
                    or f["filed"] < str(day - timedelta(days=545))
                ):
                    continue
                key = (f["filed"], f["accn"], f["form"])
                grouped.setdefault(key, []).append(
                    (f["end"], concept, unit, f.get("start"), str(f["val"]))
                )
    docs = []
    for (filed, acc, form), items in sorted(grouped.items(), reverse=True)[:3]:
        latest = max(x[0] for x in items)
        facts = sorted(
            {
                (end, tag, unit, start or "", v)
                for end, tag, unit, start, v in items
                if end == latest
            }
        )
        lines = [
            f"{tag} = {v} {unit}; {'period ' + start + ' to ' + end if start else 'as of ' + end}. SEC {form} filed {filed}."
            for end, tag, unit, start, v in facts
        ]
        if not lines:
            continue
        url = f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm"
        # Hash only the vintage-specific extracted rows. Later filings and today's entity label stay out of the evidence.
        proof = {
            "accession": acc,
            "filed": filed,
            "cik": cik,
            "original_sha256": fingerprint(json_bytes(facts)),
        }
        docs.append(
            checked_doc(
                doc(
                    f"SEC {form}, accession {acc}", url, filed, "sec_xbrl", proof, lines
                ),
                max_date,
            )
        )
    return docs


class ArchiveGateway:
    """One task owns a dated search interface. Raw discovery results stay inside this class."""

    VERSION = "dated-web-v1"

    def __init__(self, cutoff, cache, http=None):
        cutoff_day(cutoff)
        self.cutoff = cutoff
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.http = http or PublicHttp()

    def search(self, query, *, max_date, titles=(), filing_terms="", limit=6):
        if max_date != self.cutoff:
            raise CaseError("Every search must use the task's exact cutoff")
        if (
            not isinstance(query, str)
            or not 1 <= len(query.strip()) <= 450
            or not 1 <= limit <= 12
            or len(titles) > 4
        ):
            raise CaseError("Invalid search query or result limit")
        request = {
            "version": self.VERSION,
            "query": query,
            "max_date": max_date,
            "titles": list(titles),
            "filing_terms": filing_terms,
            "limit": limit,
        }
        key = fingerprint(json_bytes(request))
        path = self.cache / (key + ".json")
        if path.exists():
            envelope = json.loads(path.read_bytes())
            body = envelope["body"]
            if (
                envelope["sha256"] != fingerprint(json_bytes(body))
                or body["request"] != request
            ):
                raise CaseError(
                    "Cached retrieval differs from the requested query and cutoff"
                )
            for source in body["sources"]:
                checked_doc(source, max_date)
            return body
        failures = {}

        def optional(name, fn, *args):
            try:
                return fn(*args)
            except (
                HTTPError,
                URLError,
                TimeoutError,
                OSError,
                ValueError,
                KeyError,
                TypeError,
                RuntimeError,
            ) as exc:
                failures[name] = (
                    str(exc.code) if isinstance(exc, HTTPError) else type(exc).__name__
                )
                return None

        docs = []
        dated_links = []
        filing = []
        with ThreadPoolExecutor(max_workers=5) as workers:
            discover = workers.submit(
                optional, "search_engine", candidates, self.http, query, max_date
            )
            securities = (
                workers.submit(
                    optional,
                    "sec_index",
                    sec_candidates,
                    self.http,
                    filing_terms,
                    max_date,
                )
                if filing_terms
                else None
            )
            wikis = [
                workers.submit(
                    optional, f"revision_{i}", wiki, self.http, title, max_date
                )
                for i, title in enumerate(titles)
            ]
            for task in wikis:
                result = task.result()
                if result:
                    source, refs = result
                    if source:
                        docs.append(source)
                    dated_links.extend(refs)
            links = discover.result() or []
            if securities:
                filing = securities.result() or []

        def link_score(url):
            terms = words(unquote(url).replace("-", " "))
            return (len(terms & words(query)), int(url.endswith(".pdf")))

        selected_links = list(
            dict.fromkeys(
                links[:4] + sorted(dated_links, key=link_score, reverse=True)[:3]
            )
        )
        # Only the archived bytes or accession-specific sources can leave the retrieval boundary.
        with ThreadPoolExecutor(max_workers=6) as workers:
            captures = [
                workers.submit(
                    optional, f"capture_{i}", archived, self.http, url, max_date
                )
                for i, url in enumerate(selected_links[:5])
            ]
            edgars = [
                workers.submit(optional, f"filing_{i}", sec_doc, self.http, s, max_date)
                for i, s in enumerate(filing[:2])
            ]
            ciks = list(dict.fromkeys(s["cik"] for s in filing))[:1]
            structured = [
                workers.submit(optional, "xbrl", sec_facts, self.http, cik, max_date)
                for cik in ciks
            ]
            for task in edgars + captures:
                result = task.result()
                if result:
                    docs.append(result)
            for task in structured:
                docs.extend(task.result() or [])
        checked = {}
        for source in docs:
            checked_doc(source, max_date)
            blocks = rank_blocks(source["blocks"], query, limit=6)
            checked[source["url"]] = {**source, "blocks": blocks}

        def relevance(s):
            overlap = len(
                words(query) & words(s["title"] + " " + " ".join(s["blocks"]))
            )
            return (overlap, s["kind"] != "wikimedia_revision", s["available"])

        ordered = sorted(checked.values(), key=relevance, reverse=True)[:limit]
        body = {
            "request": request,
            "sources": ordered,
            "source_count": len(ordered),
            "service_failures": dict(sorted(failures.items())),
        }
        # The hash binds the exact observations, including an empty search, on future replays.
        publish(
            path, json_bytes({"body": body, "sha256": fingerprint(json_bytes(body))})
        )
        return body
