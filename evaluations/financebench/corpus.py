"""Reconstruct a company-disjoint sample from the authors' pinned public FinanceBench release."""

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import quote
from urllib.request import Request, urlopen

from evaluations.paired.confirmation import write_once
from evaluations.paired.registration import REPO
from scripts.paired_packet import packet_digest
from scripts.sol_baseline import fingerprint, json_bytes

REV = "cc39aeb4afdf33909ee1412188bf89035950c2eb"
AVAILABLE = "2024-12-03T17:29:01Z"
CUTOFF = "2024-12-04T00:00:00Z"
BASE = "https://raw.githubusercontent.com/patronus-ai/financebench/" + REV + "/"
API = "https://api.github.com/repos/patronus-ai/financebench/"
SEED = "ThesisTape/financebench/replication/1/"
TYPES = ("metrics-generated", "domain-relevant", "novel-generated")
CACHE = REPO / "runs/financebench-source"
PACKETS = REPO / "runs/financebench-input"
FOLDER = Path(__file__).resolve().parent


def fetch(url):
    req = Request(url, headers={"User-Agent": "ThesisTape benchmark reproducibility"})
    with urlopen(req, timeout=45) as response:
        return response.read()


def order(identifier):
    return fingerprint((SEED + identifier).encode())


def selected(rows, tree):
    issuers, chosen = set(), []
    for category in TYPES:
        group = []
        candidates = sorted(
            (r for r in rows if r["question_type"] == category),
            key=lambda r: order(r["financebench_id"]),
        )
        for r in candidates:
            file = "pdfs/" + r["doc_name"] + ".pdf"
            if r["company"] in issuers or file not in tree:
                continue
            issuers.add(r["company"])
            group.append(
                {
                    k: r[k]
                    for k in (
                        "financebench_id",
                        "company",
                        "doc_name",
                        "question_type",
                        "question",
                    )
                }
                | {"path": file}
            )
            if len(group) == 8:
                break
        if len(group) != 8:
            raise ValueError(
                "The pinned set must provide eight new issuers in every category"
            )
        chosen.extend(group)
    return sorted(chosen, key=lambda r: order(r["financebench_id"] + "/event"))


def original(relative, tree):
    local = CACHE / (Path(relative).name if relative.startswith("data/") else relative)
    raw = local.read_bytes() if local.exists() else fetch(BASE + quote(relative))
    blob = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
    if blob != tree[relative]["sha"] or len(raw) != tree[relative]["size"]:
        raise ValueError("Document differs from the pinned Git blob: " + relative)
    write_once(local, raw)
    return raw


def prepare():
    snapshot = CACHE / "frozen-tree.json"
    if not snapshot.is_file():
        commit = json.loads(fetch(API + "git/commits/" + REV))
        listing = json.loads(fetch(API + "git/trees/" + REV + "?recursive=1"))
        if (
            commit["committer"]["date"] != AVAILABLE
            or listing["sha"] != REV
            or listing.get("truncated")
        ):
            raise ValueError("An incomplete or different upstream release was returned")
        write_once(
            snapshot,
            json_bytes({"sha": REV, "available": AVAILABLE, "tree": listing["tree"]}),
        )
    frozen = json.loads(snapshot.read_bytes())
    if frozen["sha"] != REV or frozen["available"] != AVAILABLE:
        raise ValueError("Source revision differs")
    tree = {r["path"]: r for r in frozen["tree"]}
    raw = original("data/financebench_open_source.jsonl", tree)
    meta = original("data/financebench_document_information.jsonl", tree)
    items = [json.loads(line) for line in raw.splitlines()]
    docs = {row["doc_name"]: row for row in map(json.loads, meta.splitlines())}
    if len(items) != 150 or len({r["financebench_id"] for r in items}) != 150:
        raise ValueError(
            "Expected the 150 unique questions in the authors' public sample"
        )

    def packet(row):
        identifier, path = row["financebench_id"], row["path"]
        raw_pdf = original(path, tree)
        digest = fingerprint(raw_pdf)
        q = json_bytes(
            {
                "case_id": identifier,
                "question": row["question"],
                "cutoff": CUTOFF,
                "forecast_rule": None,
            }
        )
        src = json_bytes(
            [
                {
                    "id": "issuer-report",
                    "title": row["doc_name"].replace("_", " "),
                    "published": AVAILABLE,
                    "timezone": "UTC",
                    "url": BASE + quote(path),
                    "sha256": digest,
                }
            ]
        )
        folder = PACKETS / identifier
        for name, data in [
            ("question.json", q),
            ("sources.json", src),
            ("objects/" + digest + ".pdf", raw_pdf),
        ]:
            write_once(folder / name, data)
        return {
            "case_id": identifier,
            "company": row["company"],
            "type": row["question_type"],
            "doc_name": row["doc_name"],
            "source_url": BASE + quote(path),
            "original_publisher_url": docs[row["doc_name"]]["doc_link"],
            "git_blob": tree[path]["sha"],
            "pdf_sha256": digest,
            "packet_sha256": packet_digest(q, src),
        }

    with ThreadPoolExecutor(max_workers=4) as pool:
        cases = list(pool.map(packet, selected(items, tree)))
    return {
        "upstream_git_revision": REV,
        "copy_available_at": AVAILABLE,
        "cutoff": CUTOFF,
        "question_file_sha256": fingerprint(raw),
        "document_metadata_sha256": fingerprint(meta),
        "cases": cases,
    }


if __name__ == "__main__":
    data = prepare()
    print(
        json.dumps(
            {
                "issuers": len(data["cases"]),
                "packet_set_sha256": fingerprint(json_bytes(data)),
            },
            indent=2,
        )
    )
