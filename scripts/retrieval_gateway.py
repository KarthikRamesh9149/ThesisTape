"""Search and read verified historical editions under a controller-bound cutoff."""

import argparse
import json
import re
import sqlite3
from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from threading import Lock
from urllib.parse import urldefrag, urlsplit

from .edgar_exhibits import printed_heading_date
from .retrieval_sources import SUFFIX, availability, extract, instant, object_bytes

ARCHIVE = Path(__file__).resolve().parents[1] / "retrieval"
STOP_WORDS = (
    "a an and are as at be by did do does for from how in into is it its of on or that "
    "the their them to was were what when which who will with would"
)
STOP = frozenset(STOP_WORDS.split())


class EditionError(ValueError):
    """Operator diagnostic for an edition that could not be verified."""


@dataclass(frozen=True)
class Evidence:
    source_id: str
    title: str
    edition: str
    published: str
    version_at: str
    available_by_utc: str
    url: str
    sha256: str
    locator: str
    text: str
    citation_url: str


class Corpus:
    """Trusted control-plane catalog. Only a dated session is exposed to tools."""

    def __init__(self, directory: Path = ARCHIVE, *, objects: Path | None = None):
        data = (directory / "catalog.json").read_bytes()
        catalog = json.loads(data)
        if catalog["schema_version"] != 1:
            raise ValueError("Unsupported retrieval catalog")
        self.digest = sha256(data).hexdigest()
        self._objects = directory / "objects" if objects is None else objects
        self._page_images = directory / "objects"
        self._sources = {}
        self._redirects = {}
        self._cache = {}
        self._cache_lock = Lock()
        ids = set()
        for source in catalog["sources"]:
            url = source["url"]
            if not self._https(url) or url in self._sources or source["id"] in ids:
                raise ValueError("Invalid or duplicate edition address or ID")
            self._sources[url] = source
            ids.add(source["id"])
        for redirect in catalog["redirects"]:
            url = redirect["from"]
            if not self._https(url) or not self._https(redirect["to"]) or url in self._sources:
                raise ValueError("Invalid redirect address")
            self._redirects.setdefault(url, []).append(redirect)

    @staticmethod
    def _https(url):
        parts = urlsplit(url)
        return (
            parts.scheme == "https"
            and bool(parts.hostname)
            and not parts.username
            and not parts.password
            and not parts.fragment
        )

    def _blocks(self, source):
        # Recheck hashes on a new session, even if another session warmed extraction.
        key = source["id"]
        with self._cache_lock:
            try:
                if key in self._cache:
                    object_bytes(self._objects, source["sha256"], SUFFIX[source["kind"]])
                    if source["kind"] == "edgar":
                        object_bytes(self._objects, source["selection"]["index_sha256"], ".htm")
                else:
                    self._cache[key] = extract(source, self._objects)
                return self._cache[key]
            except Exception as error:
                raise EditionError(f"Failed to verify {key}: {error}") from error

    def page_bytes(self, source, page):
        entry = source.get("page_images", {}).get(page)
        if source["kind"] != "pdf" or not entry:
            return None
        if entry["pdf_sha256"] != source["sha256"]:
            raise EditionError("Page image belongs to a different PDF edition")
        object_bytes(self._objects, source["sha256"], ".pdf")
        data = object_bytes(self._page_images, entry["sha256"], ".png")
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise EditionError("Invalid registered PNG")
        return data

    def verify(self):
        """Operator-only diagnostics. This output is never included in agent results."""
        counts = {}
        for source in self._sources.values():
            try:
                if availability(source) is None or not self._https(source["proof_url"]):
                    raise ValueError("Missing publication or version proof")
                if "search_until" in source and instant(source["search_until"]) <= availability(source):
                    raise ValueError("Invalid vintage coverage interval")
            except (ValueError, KeyError) as error:
                raise ValueError(f"Failed to verify {source['id']}: {error}") from error
            counts[source["id"]] = len(self._blocks(source))
        for redirects in self._redirects.values():
            for r in redirects:
                since = instant(r["from_at"])
                if "until" in r and instant(r["until"]) <= since:
                    raise ValueError("Invalid redirect interval")
        return counts

    def session(self, cutoff: str):
        return DatedSession(self, cutoff)


def active_redirect(rule, when):
    try:
        return instant(rule["from_at"]) <= when and ("until" not in rule or when < instant(rule["until"]))
    except (ValueError, KeyError):
        return False


class DatedSession:
    """An immutable cutoff, an eligible-only FTS index, and a model-visible trace."""

    def __init__(self, corpus: Corpus, cutoff: str):
        self._when = instant(cutoff, "UTC")
        self._cutoff = cutoff
        self._corpus = corpus
        self._trace = []
        self._eligible = {}
        self._loaded = {}
        self._rows = {}
        self._db = None
        self._lock = Lock()
        self._closed = False
        for url, source in corpus._sources.items():
            available = availability(source)
            if available is not None and available <= self._when:
                self._eligible[url] = (available, source)

    @property
    def cutoff(self):
        return self._cutoff

    @property
    def trace(self):
        with self._lock:
            return [dict(event, results=list(event["results"])) for event in self._trace]

    def _authorize(self, max_date):
        if self._closed:
            raise ValueError("Retrieval session is closed")
        if max_date != self.cutoff:
            raise ValueError("max_date must match the controller's case cutoff")

    def _finish(self, operation, evidence):
        # Never echo caller-supplied guesses, denied URLs, snippets or redirect chains.
        self._trace.append(
            {
                "operation": operation,
                "max_date": self.cutoff,
                "results": [h.source_id + "/" + h.locator for h in evidence],
            }
        )
        return tuple(evidence)

    def _evidence(self, url):
        if url not in self._eligible:
            return ()
        if url not in self._loaded:
            available, source = self._eligible[url]
            try:
                blocks = self._corpus._blocks(source)
            except EditionError:
                # A corrupt/unverifiable edition is indistinguishable from a missing one
                # to the agent. The operator's verify command retains the diagnostic.
                blocks = ()
            self._loaded[url] = tuple(
                Evidence(
                    source["id"],
                    source["title"],
                    source["edition"],
                    source["published"],
                    source["version_at"],
                    available.isoformat(),
                    url,
                    source["sha256"],
                    b.locator,
                    b.text,
                    url + "#" + b.fragment,
                )
                for b in blocks
            )
        return self._loaded[url]

    def _build_index(self):
        # Even BM25 document frequency must be computed without later editions.
        latest = {}
        for url, (when, source) in self._eligible.items():
            if "search_until" in source:
                try:
                    if self._when >= instant(source["search_until"]):
                        continue
                except ValueError:
                    continue
            # An unverified date or PDF cannot displace a verified edition in its family.
            if not self._evidence(url):
                continue
            family = source["family"]
            if family not in latest or latest[family][0] < when:
                latest[family] = (when, [source])
            elif latest[family][0] == when:
                latest[family][1].append(source)
        # Multiple workers may share a case. The session lock owns this connection.
        db = sqlite3.connect(":memory:", check_same_thread=False)
        try:
            db.execute(
                "CREATE VIRTUAL TABLE passages USING fts5(title, locator, body, tokenize='porter unicode61')"
            )
            for _, editions in sorted(latest.values(), key=lambda item: item[1][0]["id"]):
                if len(editions) != 1:
                    continue  # Two competing versions with the same timestamp are ambiguous.
                (source,) = editions
                for hit in self._loaded[source["url"]]:
                    row = db.execute(
                        "INSERT INTO passages VALUES (?, ?, ?)", (hit.title, hit.locator, hit.text)
                    )
                    self._rows[row.lastrowid] = hit
        except BaseException:
            db.close()
            raise
        self._db = db

    def _ranked(self, query, limit):
        terms = [w for w in re.findall(r"[^\W_]+", query.lower()) if w not in STOP]
        if not terms:
            return []
        if self._db is None:
            self._build_index()
        match = " OR ".join('"' + t + '"' for t in dict.fromkeys(terms[:64]))
        rows = self._db.execute(
            "SELECT rowid FROM passages WHERE passages MATCH ? "
            "ORDER BY bm25(passages, 0.35, 0.35, 1.0), rowid LIMIT ?",
            (match, limit),
        )
        return [row[0] for row in rows]

    def search(self, query: str, *, max_date: str, limit: int = 5) -> tuple[Evidence, ...]:
        with self._lock:
            self._authorize(max_date)
            if type(limit) is not int or not 1 <= limit <= 20:
                raise ValueError("Search limit must be between 1 and 20")
            query = query[:2000]
            ranked = [self._ranked(query, limit)]
            # Give each explicit subquestion space in top-k when separate passages answer them.
            clauses = re.split(
                r"\band\s+(?=(?:what|which|how|when|where|why|who)\b)", query, maxsplit=2, flags=re.IGNORECASE
            )
            if len(clauses) > 1:
                ranked.extend(self._ranked(clause, limit) for clause in clauses)
            rows = dict.fromkeys(r[i] for i in range(limit) for r in ranked if i < len(r))
            return self._finish("search", [self._rows[row] for row in list(rows)[:limit]])

    def read(self, url: str, *, max_date: str, locator: str | None = None) -> tuple[Evidence, ...]:
        with self._lock:
            self._authorize(max_date)
            return self._read(url, locator)

    def exhibit_dates(self, passage: Evidence, *, max_date: str):
        with self._lock:
            self._authorize(max_date)
            hits = self._read(passage.citation_url, passage.locator)
            if hits != (passage,):
                self._finish("exhibit_dates", [])
                return None
            source = self._eligible[passage.url][1]
            if source["kind"] != "edgar" or "exhibit" not in source["selection"]:
                self._finish("exhibit_dates", [])
                return None
            # Check original and index again even when the session previously read this passage.
            self._corpus._blocks(source)
            original = object_bytes(self._corpus._objects, passage.sha256, ".htm")
            self._finish("exhibit_dates", hits)
            return {"edgar_accepted_at": source["published"], "printed_heading_date": printed_heading_date(original)}

    def page_image(self, passage: Evidence, *, max_date: str) -> bytes | None:
        """Return only the registered page pixels for a freshly verified, eligible PDF passage."""
        with self._lock:
            self._authorize(max_date)
            hits = self._read(passage.citation_url, passage.locator)
            page = re.fullmatch(r"PDF p([1-9]\d*) §[1-9]\d*", passage.locator)
            if hits != (passage,) or page is None:
                self._finish("page_image", [])
                return None
            source = self._eligible[passage.url][1]
            data = self._corpus.page_bytes(source, page[1])
            if data is None:
                self._finish("page_image", [])
                return None
            self._finish("page_image", hits)
            return data

    def _read(self, url, locator):
        # Exact, catalogued URL matching. No live network or fallback to a mutable page.
        url, _, fragment = url.partition("#")
        seen = set()
        while url in self._corpus._redirects:
            if url in seen or len(seen) >= 8:
                return self._finish("read", [])
            seen.add(url)
            targets = [r["to"] for r in self._corpus._redirects[url] if active_redirect(r, self._when)]
            if len(targets) != 1:
                return self._finish("read", [])
            url = targets[0]
        evidence = [
            e
            for e in self._evidence(url)
            if (locator is None or e.locator == locator)
            and (not fragment or urldefrag(e.citation_url).fragment == fragment)
        ]
        return self._finish("read", evidence)

    def close(self):
        with self._lock:
            self._closed = True
            if self._db is not None:
                self._db.close()
                self._db = None
            self._rows.clear()
            self._loaded.clear()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("verify")
    for name in ("search", "read"):
        sub = commands.add_parser(name)
        sub.add_argument("--as-of", required=True)
        sub.add_argument("value", metavar="QUERY" if name == "search" else "URL")
        if name == "search":
            sub.add_argument("--limit", type=int, default=5)
        else:
            sub.add_argument("--locator")
    args = parser.parse_args(argv)
    try:
        corpus = Corpus()
        if args.command == "verify":
            output = {"catalog_sha256": corpus.digest, "passages_by_edition": corpus.verify()}
        else:
            with corpus.session(args.as_of) as case:
                if args.command == "search":
                    hits = case.search(args.value, max_date=case.cutoff, limit=args.limit)
                else:
                    hits = case.read(args.value, max_date=case.cutoff, locator=args.locator)
                output = {"results": [asdict(h) for h in hits], "trace": case.trace}
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"Retrieval error: {error}\n")
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
