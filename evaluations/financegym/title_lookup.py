"""Retry empty dated searches with spelling-preserving capitalization variants of the original entity."""

import re
from datetime import UTC, datetime

from evaluations.paired.confirmation import write_once
from evaluations.paired.corpus import require
from evaluations.paired.registration import REPO
from scripts.archive_receipts import RecordedHttp
from scripts.focused_archive import FocusedArchive, check_source, verify_transport
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint, json_bytes

ROOT = REPO / "runs/financegym-title-lookups"


def variants(title):
    tokens = title.split()
    result = []
    for i in reversed(range(len(tokens))):
        alternate = " ".join([*tokens[:i], tokens[i].title(), *tokens[i + 1 :]])
        if (
            alternate != title
            and alternate.casefold() == title.casefold()
            and alternate not in result
        ):
            result.append(alternate)
    return result[:3]


def requests(row, originals):
    answer = []
    for folder in originals:
        files = sorted((folder / "searches").glob("*.json"))
        if not files:
            return []
        for p in files:
            envelope = load(p)
            body = envelope["body"]
            req = body["request"]
            require(
                req["max_date"] == row["cutoff"]
                and fingerprint(json_bytes(body)) == envelope["sha256"]
                and fingerprint(json_bytes(req)) == p.stem,
                "Original source search changed",
            )
            if body["sources"]:
                return []
            titles = []
            for title in req["titles"]:
                if re.search(
                    r"(?<!\w)" + re.escape(title) + r"(?!\w)",
                    row["question"],
                    re.IGNORECASE,
                ):
                    titles.extend(variants(title))
            if titles:
                new = {**req, "titles": list(dict.fromkeys(titles))[:4]}
                if new not in answer:
                    answer.append(new)
    return answer[:3]


def validate(row, originals, folder, registration):
    info = load(folder / "run.json")
    searches = requests(row, originals)
    require(
        searches
        and info["kind"] == "historical_title_lookup"
        and info["question"] == row["question"]
        and info["cutoff"] == row["cutoff"]
        and info["registration_sha256"] == registration
        and info["archive_version"] == FocusedArchive.VERSION,
        "Unexpected historical title lookup",
    )
    require(
        set((folder / "searches").glob("*.json"))
        == {
            folder / "searches" / (fingerprint(json_bytes(q)) + ".json")
            for q in searches
        },
        "Original dated title lookup requests differ",
    )
    docs = set()
    for req in searches:
        f = folder / "searches" / (fingerprint(json_bytes(req)) + ".json")
        envelope = load(f)
        body = envelope["body"]
        require(
            body["request"] == req
            and envelope["sha256"] == fingerprint(json_bytes(body)),
            "Historical title lookup result changed",
        )
        docs.update(
            check_source(s, row["cutoff"])["url"].split("#:~:text=")[0]
            for s in body["sources"]
        )
    elapsed = round(
        (
            datetime.fromisoformat(info["finished_at"])
            - datetime.fromisoformat(info["created_at"])
        ).total_seconds(),
        3,
    )
    require(elapsed >= 0, "Invalid lookup duration")
    return docs, elapsed


def acquire(row, originals, registration):
    base = ROOT / row["task_id"]
    folder = base / "research"
    base.mkdir(parents=True, exist_ok=True)
    with own_run(base):
        plan = requests(row, originals)
        require(plan, "No empty dated entity search qualifies")
        if not (folder / "run.json").is_file():
            start_file = base / "started-utc.txt"
            if not start_file.is_file():
                write_once(start_file, datetime.now(UTC).isoformat().encode())
            (folder / "searches").mkdir(parents=True, exist_ok=True)
            archive = FocusedArchive(
                row["cutoff"],
                base / "cache",
                question=row["question"],
                http=RecordedHttp(base / "operator-http"),
            )
            for req in plan:
                found = archive.search(
                    **{k: v for k, v in req.items() if k != "version"}
                )
                require(found["request"] == req, "Lookup changed its request")
                write_once(
                    folder / "searches" / (fingerprint(json_bytes(req)) + ".json"),
                    json_bytes(
                        {"body": found, "sha256": fingerprint(json_bytes(found))}
                    ),
                )
            info = {
                "kind": "historical_title_lookup",
                "question": row["question"],
                "cutoff": row["cutoff"],
                "archive_version": FocusedArchive.VERSION,
                "registration_sha256": registration,
                "created_at": start_file.read_text(),
                "finished_at": datetime.now(UTC).isoformat(),
            }
            write_once(folder / "run.json", json_bytes(info))
        editions, elapsed = validate(row, originals, folder, registration)
        proof = verify_transport(folder, base / "operator-http")
        require(
            proof["eligible_sources"] == len(editions), "Lookup source count differs"
        )
        return (
            folder,
            editions,
            {
                **proof,
                "elapsed_s": elapsed,
                "snapshot_sha256": fingerprint((folder / "run.json").read_bytes()),
                "registration_sha256": registration,
                "model_generations": 0,
                "model_cost_usd": "0",
            },
        )
