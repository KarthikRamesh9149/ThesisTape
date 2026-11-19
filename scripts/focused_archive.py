"""Retrieve neighboring passages from the exact public editions already admitted by the dated gateway."""

import math
import re
from collections import Counter
from functools import partial
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from .archive_receipts import check_retrieval
from .disney_hulu_case import CaseError
from .run_checkpoint import load
from .sol_baseline import fingerprint
from .temporal_web import MAX_BLOCK, ArchiveGateway, article, checked_doc, words

VERSION = "dated-web-context-v1"
CLAUSES = re.compile(
    r"(?:[?;]|,?\s+(?:and\s+)?(?=(?:what|which|how|when|whether|why|where)\b))",
    re.IGNORECASE,
)


def terms(text):
    def stem(w):
        if len(w) >= 7 and w.endswith("ional"):
            return w[:-2]
        if len(w) >= 5 and w.endswith("ies"):
            return w[:-3] + "y"
        if len(w) >= 4 and w.endswith("s") and not w.endswith("ss"):
            return w[:-1]
        return w

    return {stem(w) for w in words(text)}


def facets(question):
    return [
        part
        for part in CLAUSES.split(question)
        if len(terms(part)) >= 2
        and not re.fullmatch(r"\s*As of .*?\d\s*", part, re.IGNORECASE)
    ]


def anchored(source, excerpt):
    tokens = excerpt.split()
    fragment = "#:~:text=" + quote(
        " ".join(tokens if len(tokens) <= 18 else tokens[:9]), safe=""
    )
    if len(tokens) > 18:
        fragment += "," + quote(" ".join(tokens[-9:]), safe="")
    if len(source["url"] + fragment) > 2300:
        return {**source, "blocks": [excerpt]}
    return {
        **source,
        "url": source["url"] + fragment,
        "blocks": [excerpt],
        "proof": {**source["proof"], "passage_sha256": fingerprint(excerpt.encode())},
    }


def check_source(item, cutoff):
    if "passage_sha256" not in item["proof"]:
        return checked_doc(item, cutoff)
    source_url, fragment, _ = item["url"].partition("#:~:text=")
    if not fragment or len(item["blocks"]) != 1:
        raise CaseError("Invalid original passage citation")
    proof = {k: v for k, v in item["proof"].items() if k != "passage_sha256"}
    original = checked_doc({**item, "url": source_url, "proof": proof}, cutoff)
    if anchored(original, item["blocks"][0]) != item:
        raise CaseError("Passage or source locator changed")
    return item


def passages(blocks, query, question, limit=6):
    """Rank each requested clause separately and include adjacent original paragraphs when they fit."""
    if not blocks:
        return []
    features = [terms(b) for b in blocks]
    frequency = Counter(word for row in features for word in row)
    clauses = facets(question)
    targets = [terms(s) for s in [query, *clauses, question]]
    ranks = []
    for target in targets:

        def rank(i, target=target):
            shared = target & features[i]
            return (
                sum(
                    math.log1p((len(blocks) + 0.5) / (frequency[w] + 0.5))
                    for w in sorted(shared)
                )
                / (1 + 0.0002 * len(blocks[i])),
                -i,
            )

        ranks.append(
            sorted(
                (i for i, b in enumerate(features) if b & target),
                key=rank,
                reverse=True,
            )[:limit]
        )
    chosen = []
    covered = set()
    for depth in range(limit):
        for sequence in ranks:
            if depth >= len(sequence):
                continue
            i = sequence[depth]
            if i in covered:
                continue
            first = last = i
            size = len(blocks[i])
            for adjacent in (i + 1, i - 1):
                if (
                    0 <= adjacent < len(blocks)
                    and adjacent not in covered
                    and size + len(blocks[adjacent]) + 2 <= MAX_BLOCK
                ):
                    first, last = min(first, adjacent), max(last, adjacent)
                    size += len(blocks[adjacent]) + 2
            chosen.append("\n\n".join(blocks[first : last + 1]))
            covered.update(range(first, last + 1))
            if len(chosen) == limit:
                return chosen
    return chosen or list(blocks[:1])


class FocusedArchive:
    VERSION = VERSION
    check_source = staticmethod(check_source)

    def __init__(self, cutoff, cache, *, question, http=None):
        self.cutoff = cutoff
        self.question = question
        self.underlying = ArchiveGateway(
            cutoff, Path(cache) / "original-search", http=http
        )
        self.originals = {}
        self.locks = {}
        self.guard = Lock()

    def original(self, source):
        proof = source["proof"]
        key = (source["url"], proof["original_sha256"])
        with self.guard:
            lock = self.locks.setdefault(key, Lock())
        with lock:
            if key not in self.originals:
                url = (
                    "https://web.archive.org/web/"
                    + proof["capture"]
                    + "id_/"
                    + proof["original_url"]
                    if source["kind"] == "wayback"
                    else source["url"]
                )
                raw = self.underlying.http(url)
                if fingerprint(raw) != proof["original_sha256"]:
                    raise CaseError(
                        "The cited edition changed while reopening its paragraphs"
                    )
                _, blocks = article(raw)
                self.originals[key] = blocks
            return self.originals[key]

    def search(self, query, *, max_date, titles=(), filing_terms="", limit=6):
        found = self.underlying.search(
            query,
            max_date=max_date,
            titles=titles,
            filing_terms=filing_terms,
            limit=limit,
        )
        candidates, heads = [], []
        for s in found["sources"]:
            checked_doc(s, max_date)
            if s["kind"] in {"sec_filing", "wayback"}:
                group = [
                    anchored(s, b)
                    for b in passages(self.original(s), query, self.question)
                ]
            else:
                group = [s]
            heads.append(group[0])
            candidates.extend(group)
        if not candidates:
            return {**found, "request": {**found["request"], "version": VERSION}}
        sets = [terms(" ".join(s["blocks"])) for s in candidates]
        df = Counter(w for features in sets for w in features)
        selected = []

        def add(s):
            if len(selected) < limit and all(t["url"] != s["url"] for t in selected):
                selected.append(s)

        for clause in [query, *facets(self.question)]:
            tokens = terms(clause)

            def hit(i, tokens=tokens):
                return sum(
                    math.log1p((len(sets) + 1) / (1 + df[w]))
                    for w in sorted(tokens & sets[i])
                )

            indices = [i for i in range(len(sets)) if sets[i] & tokens]
            if indices:
                add(candidates[max(indices, key=hit)])
        for s in [*heads, *candidates]:
            add(s)
        sources = [check_source(s, max_date) for s in selected]
        return {
            **found,
            "request": {**found["request"], "version": VERSION},
            "sources": sources,
            "source_count": len({s["url"].split("#:~:text=")[0] for s in sources}),
        }


def verify_transport(directory, http):
    r = load(directory / "run.json")
    if r["archive_version"] != VERSION:
        return check_retrieval(directory, http)
    gateway = partial(FocusedArchive, question=r["question"])
    evidence = check_retrieval(directory, http, gateway_type=gateway)
    editions = {
        s["url"].split("#:~:text=")[0]
        for p in (directory / "searches").glob("*.json")
        for s in load(p)["body"]["sources"]
    }
    return {
        **evidence,
        "eligible_passage_urls": evidence["eligible_sources"],
        "eligible_sources": len(editions),
    }
