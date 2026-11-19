"""Expose the paired evaluator's dated PDF packet through the production research interface."""

from threading import Lock

from .disney_hulu_case import CaseError
from .retrieval_sources import instant
from .sol_baseline import fingerprint, json_bytes


class PacketArchive:
    VERSION = "paired-pdf-archive-v1"

    def __init__(self, corpus):
        self.corpus = corpus
        self.cutoff = corpus.case.cutoff
        self.forecast_rule = corpus.case.forecast_rule
        self.lock = Lock()
        self.eligible = {}

    def check_source(self, source, max_date):
        if (
            max_date != self.cutoff
            or source["kind"] != "publisher_pdf"
            or instant(source["proof"]["available_by"]) > instant(max_date)
        ):
            raise CaseError("PDF is outside its assigned date boundary")
        with self.lock:
            known = self.eligible.get(source["url"])
        if not known:
            with self.corpus.session(max_date) as session:
                hits = session.read(source["url"], max_date=max_date)
            if len(hits) != 1:
                raise CaseError(
                    "PDF citation did not reopen exactly one source passage"
                )
            known = self.source(hits[0])
        if known != source:
            raise CaseError("PDF passage or page changed")
        return source

    def source(self, hit):
        return {
            "title": f"{hit.title}, {hit.locator}",
            "url": hit.citation_url,
            "available": hit.published[:10],
            "kind": "publisher_pdf",
            "proof": {
                "original_sha256": hit.sha256,
                "original_url": hit.url,
                "locator": hit.locator,
                "source_id": hit.source_id,
                "available_by": hit.available_by_utc,
                "packet_sha256": self.corpus.digest,
            },
            "blocks": [hit.text],
        }

    def search(self, query, *, max_date, titles, filing_terms, limit):
        if max_date != self.cutoff:
            raise CaseError("Every search requires the exact assigned cutoff")
        # Sealed cases use the same original publisher editions in each arm. Every underlying
        # retrieval is separately bound to that case's date, including supplementary table queries.
        queries = list(dict.fromkeys([query[:350], self.corpus.case.question[:350]]))
        with self.corpus.session(max_date) as session:
            rankings = [
                session.search(q, max_date=max_date, limit=min(limit, 10))
                for q in queries
            ]
        hits = list(
            dict.fromkeys(h for pair in zip(*rankings, strict=False) for h in pair)
        )
        hits.extend(h for group in rankings for h in group if h not in hits)
        docs = [self.source(h) for h in hits[:limit]]
        with self.lock:
            self.eligible.update({s["url"]: s for s in docs})
        return {
            "request": {
                "version": self.VERSION,
                "query": query,
                "max_date": max_date,
                "titles": list(titles),
                "filing_terms": filing_terms,
                "limit": limit,
            },
            "sources": docs,
            "source_count": len(docs),
            "service_failures": {},
        }


class SavedSearches:
    def __init__(self, gateway, folder, *, offline=False):
        self.gateway = gateway
        self.folder = folder
        self.offline = offline
        self.forecast_rule = getattr(gateway, "forecast_rule", None)
        self.check_source = getattr(gateway, "check_source", None)
        if self.check_source is None:
            from .temporal_web import checked_doc

            self.check_source = checked_doc

    def search(self, query, *, max_date, titles, filing_terms, limit):
        from .run_checkpoint import load, publish

        request = {
            "version": self.gateway.VERSION,
            "query": query,
            "max_date": max_date,
            "titles": list(titles),
            "filing_terms": filing_terms,
            "limit": limit,
        }
        if self.gateway.cutoff != max_date:
            raise CaseError("Stored research uses a different cutoff")
        path = self.folder / (fingerprint(json_bytes(request)) + ".json")
        if path.exists():
            stored = load(path)
            result = stored["body"]
            if (
                stored["sha256"] != fingerprint(json_bytes(result))
                or result["request"] != request
            ):
                raise CaseError("Stored search changed")
        elif self.offline:
            raise CaseError("Offline replay requires the original saved search")
        else:
            result = self.gateway.search(
                query,
                max_date=max_date,
                titles=titles,
                filing_terms=filing_terms,
                limit=limit,
            )
            if result["request"] != request:
                raise CaseError("Source service returned a different search")
            self.folder.mkdir(exist_ok=True, parents=True)
            publish(
                path,
                json_bytes({"sha256": fingerprint(json_bytes(result)), "body": result}),
            )
        for source in result["sources"]:
            self.check_source(source, max_date)
        return result
