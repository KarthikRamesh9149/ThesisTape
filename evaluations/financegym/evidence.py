"""Seal a portable, offline replay of the FinanceGym reports without exporting operator credentials or identities."""

import argparse
import json
import re
import tempfile
import zipfile
from itertools import batched
from pathlib import Path

from evaluations.paired.confirmation import write_once, zipped
from evaluations.paired.corpus import require
from evaluations.paired.registration import REPO
from scripts.open_desk_report import SavedArchive
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes

from . import recovery, runner, submission

FOLDER = REPO / "evaluations/financegym/results"
PORTABLE = re.compile(
    r"(?:run\.json|result\.json|memo\.md|(?:searches|counts)/[0-9a-f]{64}\.json)"
)
ENTRY = re.compile(r"([a-z0-9]{24})/(primary|recovery)/(.+)")


def portable(run):
    files = [
        p
        for p in (run / "run.json", run / "result.json", run / "memo.md")
        if p.is_file()
    ]
    files += [p for name in ("counts", "searches") for p in (run / name).glob("*.json")]
    entries = {}
    private = (
        load(run / "provider-ids.json") if (run / "provider-ids.json").is_file() else {}
    )
    ids = [v.encode() for stage in private.values() for v in stage.values() if v]
    for path in files:
        name, raw = str(path.relative_to(run)), path.read_bytes()
        require(
            not path.is_symlink()
            and path.resolve().is_relative_to(run.resolve())
            and PORTABLE.fullmatch(name),
            "Unexpected research artifact",
        )
        require(
            all(v not in raw for v in ids),
            "Private receipt identity in a portable file",
        )
        entries[name] = raw
    receipts = run.parent / "operator-http"
    index = {
        p.stem: fingerprint(p.read_bytes()) for p in sorted(receipts.glob("*.json"))
    }
    entries["http-receipt-hashes.json"] = json_bytes(index)
    return entries


def seal(questions, directory, dest):
    _, audit = submission.paper(questions, directory)
    records = audit["tasks_audited"]
    shards = {}
    for number, group in enumerate(batched(records, 25), 1):
        entries = {}
        for rec in group:
            case = rec["task_id"]
            locations = [("primary", runner.RESEARCH / case / "research")]
            if len(rec.get("attempts", ())) == 2:
                locations.append(("recovery", recovery.OUTPUT / case / "research"))
            for kind, run in locations:
                for name, raw in portable(run).items():
                    entries[f"{case}/{kind}/{name}"] = raw
        bundle = zipped(entries)
        name = f"receipts-{number:02d}.zip"
        write_once(dest / name, bundle)
        shards[name] = fingerprint(bundle)
    files = {}
    for name in ("answers.jsonl", "audit.json", "system-paper.md"):
        raw = (directory / name).read_bytes()
        write_once(dest / name, raw)
        files[name] = fingerprint(raw)
    manifest = {
        "tasks": len(records),
        "files": files,
        "shards": shards,
        "registration_sha256": audit["registration_sha256"],
        "operator_checks": "Every original HTTP response and private provider identity was checked when the submission was exported. Portable replay uses the original admitted passages and recorded generations.",
    }
    write_once(dest / "manifest.json", json_bytes(manifest))
    return verify(questions, dest)


def verify(questions, root):
    cfg, audit = submission.verify_export(questions, root)
    manifest = load(root / "manifest.json")
    require(
        manifest["tasks"] == 400
        and manifest["registration_sha256"] == audit["registration_sha256"],
        "Unexpected research registration",
    )
    require(
        set(manifest["files"]) == {"answers.jsonl", "audit.json", "system-paper.md"}
        and len(manifest["shards"]) == 16,
        "Incomplete finance research archive",
    )
    for name, expected in manifest["files"].items():
        require(
            fingerprint((root / name).read_bytes()) == expected,
            "Published artifact changed",
        )
    rows = runner.tasks(questions)
    reports = [
        json.loads(raw) for raw in (root / "answers.jsonl").read_bytes().splitlines()
    ]
    seen, attempts = set(), 0
    for number, group in enumerate(
        batched(zip(rows, reports, audit["tasks_audited"], strict=True), 25), 1
    ):
        name = f"receipts-{number:02d}.zip"
        bundle = root / name
        require(
            fingerprint(bundle.read_bytes()) == manifest["shards"][name],
            "Portable research shard changed",
        )
        with tempfile.TemporaryDirectory() as t, zipfile.ZipFile(bundle) as z:
            allowed = {
                (row["task_id"], mode)
                for row, _, proof in group
                for mode in (
                    ("primary", "recovery")
                    if len(proof.get("attempts", ())) == 2
                    else ("primary",)
                )
            }
            require(
                len(set(z.namelist())) == len(z.namelist()),
                "Duplicate portable research file",
            )
            for entry in z.namelist():
                m = ENTRY.fullmatch(entry)
                require(
                    m
                    and (m[1], m[2]) in allowed
                    and (
                        PORTABLE.fullmatch(m[3]) or m[3] == "http-receipt-hashes.json"
                    ),
                    "Unexpected portable research entry",
                )
                z.extract(entry, t)
            for row, report, proof in group:
                case = row["task_id"]
                require(case not in seen, "A task was replayed twice")
                seen.add(case)
                chain = proof.get("attempts", [proof])
                for index, evidence in enumerate(chain):
                    mode = "recovery" if index else "primary"
                    run = Path(t) / case / mode
                    receipt_index = load(run / "http-receipt-hashes.json")
                    require(
                        len(receipt_index) == evidence["transport_requests"]
                        and all(
                            re.fullmatch("[0-9a-f]{64}", k)
                            and re.fullmatch("[0-9a-f]{64}", v)
                            for k, v in receipt_index.items()
                        ),
                        "Transport evidence inventory changed",
                    )
                    rec = load(run / "run.json")
                    if "original_run_sha256" in evidence:
                        require(
                            fingerprint((run / "run.json").read_bytes())
                            == evidence["original_run_sha256"],
                            "A retained attempt changed",
                        )
                    require(
                        rec["research_profile"] == evidence["profile"]
                        and rec["question"] == row["question"]
                        and rec["cutoff"] == row["cutoff"],
                        "The reproduced assignment differs",
                    )
                    attempts += 1
                engine = (
                    recovery.second_route(cfg, chain[0]["profile"])
                    if len(chain) == 2
                    else runner.route(row, cfg)
                )[0]
                saved = engine.audit(
                    run, SavedArchive(row["cutoff"], cfg["archive_version"]), record=rec
                )
                item = runner.answer(row, saved, (run / "memo.md").read_bytes())
                require(
                    item["report"] == report["report"],
                    "A reproduced report differs from the offered submission",
                )
    require(len(seen) == 400, "Research archive does not cover all 400 questions")
    return {
        "replayed_published_reports": len(seen),
        "retained_attempts": attempts,
        "answers_sha256": audit["answers_sha256"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    sp = parser.add_subparsers(dest="action", required=True)
    pack = sp.add_parser("seal")
    pack.add_argument("questions", type=Path)
    pack.add_argument("submission", type=Path)
    check = sp.add_parser("verify")
    check.add_argument("questions", type=Path)
    for cmd in (pack, check):
        cmd.add_argument("--archive", type=Path, default=FOLDER)
    args = parser.parse_args()
    result = (
        seal(args.questions, args.submission, args.archive)
        if args.action == "seal"
        else verify(args.questions, args.archive)
    )
    print(json.dumps(result, indent=2))
