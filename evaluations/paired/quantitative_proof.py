"""Seal and independently score the two development trials chosen for their earlier numerical omissions."""

import argparse
import io
import json
import tempfile
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scripts.packet_archive import PacketArchive
from scripts.paired_packet import PacketCorpus
from scripts.quantitative_engine import audit
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key

from .confirmation import committed, write_once, zipped
from .corpus import export_development, require
from .event_audit import verify_private
from .grading import grade_one, key_file, masked_context
from .registration import ROOT

CASES = ("nintendo-fy2021-guidance", "canon-fy2022-interim-outlook")
HERE = ROOT / "revisions/quantitative_trials"


def pack(directory):
    entries = {}
    rows = []
    ids = {k: set() for k in ("response_id", "request_id")}
    for cid in CASES:
        folder = directory / cid
        run = folder / "run"
        record = load(run / "run.json")
        packet = PacketCorpus(folder / "input", record["assignment"]["packet_sha256"])
        result = audit(run, PacketArchive(packet))
        verify_private(run, record, ids)
        for pattern in (
            "run.json",
            "result.json",
            "memo.md",
            "searches/*.json",
            "counts/*.json",
        ):
            for path in sorted(run.glob(pattern)):
                entries[cid + "/" + path.relative_to(run).as_posix()] = (
                    path.read_bytes()
                )
        rows.append(
            {
                "case_id": cid,
                "state": result["release"],
                "memo_sha256": result["memo_sha256"],
                "amount_usd": result["model_cost_usd"],
                "original_generation_receipts": len(record["calls"]),
                "cap_usd": record["budget_usd"],
                "direction": result.get("direction"),
                "new_searches": len(result["followups"]),
            }
        )
    data = zipped(entries)
    write_once(HERE / "reports.zip", data)
    write_once(
        HERE / "reports.json",
        json_bytes(
            {
                "purpose": "Two previously used development cases; numerical verification engineering trials.",
                "report_set_sha256": fingerprint(data),
                "rows": rows,
            }
        ),
    )
    return rows


def replay():
    raw = committed(HERE / "reports.zip")
    index = json.loads(committed(HERE / "reports.json"))
    require(fingerprint(raw) == index["report_set_sha256"], "Changed frozen reports")
    results = {}
    with (
        zipfile.ZipFile(io.BytesIO(raw)) as archive,
        tempfile.TemporaryDirectory() as td,
    ):
        for cid in CASES:
            path = Path(td) / cid
            packet = path / "input"
            export_development(cid, packet)
            run = path / "run"
            for name in archive.namelist():
                leading, sep, relative = name.partition("/")
                if leading != cid:
                    continue
                require(
                    sep
                    and not Path(relative).is_absolute()
                    and ".." not in Path(relative).parts,
                    "Invalid bundle",
                )
                target = run / relative
                target.parent.mkdir(exist_ok=True, parents=True)
                target.write_bytes(archive.read(name))
            record = load(run / "run.json")
            gateway = PacketArchive(
                PacketCorpus(packet, record["assignment"]["packet_sha256"])
            )
            result = audit(run, gateway)
            research = {
                **result,
                "evidence": {
                    pid: {
                        "source_id": h["proof"]["source_id"],
                        "title": h["title"],
                        "locator": h["proof"]["locator"],
                        "published": h["available"],
                        "citation_url": h["url"],
                        "text": h["text"],
                    }
                    for pid, h in result["evidence"].items()
                },
            }
            memo = (run / "memo.md").read_bytes()
            require(
                fingerprint(memo)
                == next(r["memo_sha256"] for r in index["rows"] if r["case_id"] == cid),
                "Mismatch in memo",
            )
            results[cid] = (research, memo)
    return results


def score_asof(grade_root, key_file):
    reports = replay()
    facts = key_file_data("asof.json")

    def score(cid):
        research, memo = reports[cid]
        payload = masked_context(research, memo, facts["cases"][cid]["facts"])
        provider = OpenAIResponses(project_key(key_file)) if key_file else None
        assessment, amount = grade_one(
            payload, grade_root / cid / "grade", provider=provider
        )
        return {
            "case_id": cid,
            "memo_sha256": fingerprint(memo),
            "grader_usd": amount,
            "assessment": assessment,
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        rows = list(pool.map(score, CASES))
    write_once(HERE / "asof.json", json_bytes(rows))
    return [
        {
            "case": r["case_id"],
            "awards": [p["criterion"] for p in r["assessment"]["points"] if p["award"]],
            "grader_usd": r["grader_usd"],
        }
        for r in rows
    ]


def key_file_data(member):
    # These two original development cases already have their contemporaneous assessments committed.
    if member == "outcomes.json":
        committed(HERE / "asof.json")
    return key_file("development", member)


def outcomes():
    notes = json.loads(committed(HERE / "asof.json"))
    reports = json.loads(committed(HERE / "reports.json"))["rows"]
    observations = key_file_data("outcomes.json")
    summary = []
    for row, note in zip(reports, notes, strict=True):
        cid = row["case_id"]
        require(
            cid == note["case_id"] and row["memo_sha256"] == note["memo_sha256"],
            "Changed as-of grade",
        )
        grade = note["assessment"]
        summary.append(
            {
                **row,
                "F": sum(
                    p["award"]
                    for p in grade["points"]
                    if p["criterion"].startswith("F")
                ),
                "R": sum(
                    p["award"]
                    for p in grade["points"]
                    if p["criterion"].startswith("R")
                ),
                "O": int(row["direction"] == observations[cid]["label"]),
                "observed_direction": observations[cid]["label"],
                "supported_claims": sum(
                    c["verdict"] == "supported" for c in grade["claims"]
                ),
                "material_claims": sum(
                    c["verdict"] != "nonmaterial" for c in grade["claims"]
                ),
                "grader_usd": note["grader_usd"],
            }
        )
    write_once(HERE / "results.json", json_bytes(summary))
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["pack", "verify", "asof", "outcomes"])
    p.add_argument("--runs", type=Path)
    p.add_argument("--key-file", type=Path)
    a = p.parse_args()
    if a.command == "pack":
        value = pack(a.runs)
    elif a.command == "verify":
        value = {
            k: {"sha256": fingerprint(m), "state": r["release"]}
            for k, (r, m) in replay().items()
        }
    elif a.command == "asof":
        value = score_asof(a.runs, a.key_file)
    else:
        value = outcomes()
    print(json.dumps(value, indent=2))
