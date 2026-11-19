"""Offline, operator-only passage retrieval evaluation on marked historical sources."""

import argparse
import json
from hashlib import sha256
from pathlib import Path

from .retrieval_gateway import ARCHIVE, Corpus
from .retrieval_sources import compact

EVALUATION = Path(__file__).resolve().parents[1] / "evaluations" / "retrieval"


def evaluate():
    corpus = Corpus()
    passage_counts = corpus.verify()
    urls = {s["id"]: s["url"] for s in json.loads((ARCHIVE / "catalog.json").read_bytes())["sources"]}
    labels = (EVALUATION / "cases.json").read_bytes()
    suite = json.loads(labels)
    k = suite["top_k"]
    total = {
        "questions": 0,
        "first_hit_marked": 0,
        "passages_found": 0,
        "passages_marked": 0,
        "citation_checks": 0,
    }
    groups, runs = {}, []
    for task in suite["cases"]:
        cutoff = task["cutoff"]
        with corpus.session(cutoff) as session:
            hits = session.search(task["question"], max_date=cutoff, limit=k)
            keys = [(hit.source_id, hit.locator) for hit in hits]
            ranks = []
            for target in task["expected"]:
                key = (target["source"], target["locator"])
                original = session.read(urls[key[0]], locator=key[1], max_date=cutoff)
                if len(original) != 1 or not all(
                    anchor in compact(original[0].text) for anchor in target["anchors"]
                ):
                    raise ValueError(f"Unresolved reference passage in {task['id']}: {key}")
                ranks.append(keys.index(key) + 1 if key in keys else None)
            for hit in hits:
                opened = session.read(hit.citation_url, max_date=cutoff)
                by_locator = session.read(hit.url, locator=hit.locator, max_date=cutoff)
                if opened != (hit,) or by_locator != opened:
                    raise ValueError(f"Citation did not open its original passage in {task['id']}")
            measures = {
                "questions": 1,
                "first_hit_marked": int(1 in ranks),
                "passages_found": sum(r is not None for r in ranks),
                "passages_marked": len(ranks),
                "citation_checks": len(hits),
            }
            group = groups.setdefault(task["group"], dict.fromkeys(total, 0))
            for name, count in measures.items():
                total[name] += count
                group[name] += count
            runs.append(
                {
                    "id": task["id"],
                    "cutoff": cutoff,
                    "gold_ranks": ranks,
                    "retrieved": [{"source": id_, "locator": locator} for id_, locator in keys],
                }
            )
    return {
        "schema_version": 1,
        "top_k": k,
        "catalog_sha256": corpus.digest,
        "labels_sha256": sha256(labels).hexdigest(),
        "passages_by_edition": passage_counts,
        "total": total,
        "groups": groups,
        "cases": runs,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Compare with the committed results")
    args = parser.parse_args()
    report = evaluate()
    if args.check:
        if report != json.loads((EVALUATION / "results.json").read_bytes()):
            parser.exit(1, "Observed retrieval results differ from the saved report\n")
        print(json.dumps(report["total"], sort_keys=True))
    else:
        print(json.dumps(report, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
