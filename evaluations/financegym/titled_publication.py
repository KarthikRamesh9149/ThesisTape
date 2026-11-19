"""Publish a qualified report when a case-sensitive historical entity lookup restores the missing sources."""

import argparse
import json
import tempfile
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from evaluations.paired.confirmation import committed, write_once
from evaluations.paired.corpus import require
from evaluations.paired.event_audit import verify_private
from evaluations.paired.product_audit import CountJournal
from evaluations.paired.registration import REPO
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key
from scripts.team_meter import verify_ledger

from . import budget, editorial, recovery, reviewed_release, runner, title_lookup

POLICY = Path(__file__).with_name("title-policy.md")
REGISTRATION = POLICY.with_name("title-registration.json")


def registration(editor):
    files = [POLICY, Path(__file__), Path(title_lookup.__file__)]
    return {
        "version": 1,
        "editorial_registration_sha256": fingerprint(json_bytes(editor)),
        "sources": {str(p.relative_to(REPO)): fingerprint(committed(p)) for p in files},
    }


def locked(editor):
    spec = registration(editor)
    require(
        spec == json.loads(committed(REGISTRATION)),
        "Historical title procedure differs from its registration",
    )
    return fingerprint(json_bytes(spec))


def needed(row, cfg):
    original = editorial.folders(row)
    return (
        all(
            (f / "run.json").is_file()
            and load(f / "run.json")["state"] in runner.FINAL | {"needs_reconciliation"}
            for f in original
        )
        and bool(title_lookup.requests(row, original))
        and editorial.eligible(row, cfg)
    )


def ticket(row, spec, chain, lookup):
    return {
        **editorial.ticket(row, spec, chain),
        "title_registration_sha256": lookup["registration_sha256"],
        "lookup_snapshot_sha256": lookup["snapshot_sha256"],
    }


def run_one(row, cfg, ext, editor, reg):
    require(needed(row, cfg), "A historical title retry is ineligible")
    original = editorial.folders(row)
    chain, _ = reviewed_release.unresolved(row, cfg, ext, recovery.identifiers())
    recovered, editions, lookup = title_lookup.acquire(row, original, reg)
    require(editions, "The title lookup recovered no eligible dated editions")
    folder = editorial.ROOT / row["task_id"] / "research"
    if (folder / "run.json").is_file() and load(folder / "run.json")[
        "state"
    ] in runner.FINAL | {"needs_reconciliation"}:
        return load(folder / "run.json")["state"]
    provider = OpenAIResponses(project_key(REPO / ".env.local"))
    for f in original:
        provider = runner.DistinctProvider(provider, f)
    with budget.ProjectBudget().reserve(folder / "run.json", editorial.engine.CAP):
        record = editorial.engine.run(
            row,
            cfg["archive_version"],
            [*original, recovered],
            folder,
            ticket(row, editor, chain, lookup),
            CountJournal(folder / "counts", provider),
        )
    return record["state"]


def resolve(row, cfg, ext, editor, reg, ids):
    if not needed(row, cfg):
        return editorial.resolve(row, cfg, ext, editor, ids)
    original = editorial.folders(row)
    chain, old_docs = reviewed_release.unresolved(row, cfg, ext, ids)
    require(not old_docs, "Earlier investigators had usable documents")
    recovered = title_lookup.ROOT / row["task_id"] / "research"
    require((recovered / "run.json").is_file(), "The title lookup has not finished")
    editions, duration = title_lookup.validate(row, original, recovered, reg)
    audit = title_lookup.verify_transport(recovered, recovered.parent / "operator-http")
    require(
        editions and len(editions) == audit["eligible_sources"],
        "A source-qualified report needs verified historical evidence",
    )
    lookup = {
        **audit,
        "elapsed_s": duration,
        "registration_sha256": reg,
        "snapshot_sha256": fingerprint((recovered / "run.json").read_bytes()),
        "model_generations": 0,
        "model_cost_usd": "0",
    }
    folder = editorial.ROOT / row["task_id"] / "research"
    rec = load(folder / "run.json")
    verify_private(folder, rec, ids)
    require(
        not list((folder / "searches").glob("*.json"))
        and not list((folder.parent / "operator-http").glob("*.json")),
        "Editing performed an unrecorded search",
    )
    prior = {
        call["request_sha256"]
        for f in original
        for call in load(f / "run.json")["calls"].values()
    }
    require(
        not prior.intersection(c["request_sha256"] for c in rec["calls"].values()),
        "Editing repeated a previously dispatched request",
    )
    saved, memo = editorial.engine.audit(
        row,
        cfg["archive_version"],
        [*original, recovered],
        folder,
        ticket(row, editor, chain, lookup),
    )
    require(memo is not None, "No independently approved source-restored report")
    answer, visible = runner.reviewed_answer(row, saved, memo)
    require(
        visible["release"] == "qualified", "Source-restored reports must be qualified"
    )
    paid, held = verify_ledger(rec)
    seconds = round(
        (
            datetime.fromisoformat(rec["finished_at"])
            - datetime.fromisoformat(rec["created_at"])
        ).total_seconds(),
        3,
    )
    edit = {
        "profile": editorial.engine.PROFILE,
        "state": "qualified",
        "report_sha256": visible["memo_sha256"],
        "source_report_sha256": saved["memo_sha256"],
        "original_run_sha256": fingerprint((folder / "run.json").read_bytes()),
        "estimated_cost_usd": str(paid),
        "outstanding_usd": str(sum(held.values(), Decimal(0))),
        "elapsed_s": seconds,
        "provider_generations": len(rec["calls"]),
        "transport_requests": 0,
        "attribution_review": visible.get("attribution_review"),
    }
    components = [*chain, edit]
    answer.update(
        num_docs_retrieved=len(editions),
        elapsed_s=round(sum(a["elapsed_s"] for a in components) + duration, 3),
    )
    return answer, {
        "task_id": row["task_id"],
        "primary_profile": chain[0]["profile"],
        "profile": edit["profile"],
        "state": edit["state"],
        "report_sha256": edit["report_sha256"],
        "attempts": chain,
        "editorial": edit,
        "title_lookup": lookup,
        "estimated_cost_usd": str(
            sum(Decimal(x["estimated_cost_usd"]) for x in components)
        ),
        "outstanding_usd": str(sum(Decimal(x["outstanding_usd"]) for x in components)),
    }


def export(questions, dest, cfg, ext, editor, reg):
    ids = recovery.identifiers()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
        area = Path(temp) / "export"
        area.mkdir()
        proofs = []
        with (area / "answers.jsonl").open("xb") as stream:
            for row in runner.tasks(questions):
                item, proof = resolve(row, cfg, ext, editor, reg, ids)
                proofs.append(proof)
                stream.write(
                    json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode()
                    + b"\n"
                )
        check = runner.validate_file(questions, area / "answers.jsonl")
        primary = sum(len(p["attempts"]) == 1 for p in proofs)
        edited = [p["editorial"] for p in proofs if "editorial" in p]
        retained = sum("reviewed_publication" in p for p in proofs)
        audit = {
            "tasks": len(proofs),
            "verified_provider_generations": len(ids["response_id"]),
            "answers_sha256": check["answers_sha256"],
            "registration_sha256": fingerprint(json_bytes(cfg)),
            "recovery_registration_sha256": fingerprint(json_bytes(ext)),
            "reviewed_release_registration_sha256": editor["reviewed_release_sha256"],
            "editorial_registration_sha256": fingerprint(json_bytes(editor)),
            "title_registration_sha256": reg,
            "title_lookup_releases": sum("title_lookup" in p for p in proofs),
            "primary_releases": primary,
            "recovery_releases": len(proofs) - primary - len(edited) - retained,
            "reviewed_first_draft_releases": retained,
            "editorial_releases": len(edited),
            "editorial_cost_usd": str(
                sum((Decimal(e["estimated_cost_usd"]) for e in edited), Decimal(0))
            ),
            "qualified": sum(p["state"] == "qualified" for p in proofs),
            "total_research_cost_usd": str(
                sum(Decimal(p["estimated_cost_usd"]) for p in proofs)
            ),
            "outstanding_usd": str(sum(Decimal(p["outstanding_usd"]) for p in proofs)),
        }
        (area / "audit.json").write_bytes(
            json_bytes({**audit, "tasks_audited": proofs})
        )
        if dest.exists():
            require(
                all(
                    (dest / n).read_bytes() == (area / n).read_bytes()
                    for n in ("answers.jsonl", "audit.json")
                ),
                "Existing submission differs",
            )
        else:
            area.rename(dest)
        return audit


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_subparsers(dest="command", required=True)
    actions.add_parser("register")
    for name in ("run", "export"):
        cmd = actions.add_parser(name)
        cmd.add_argument("questions", type=Path)
        if name == "run":
            cmd.add_argument("--task-id", required=True)
        else:
            cmd.add_argument("directory", type=Path)
    args = parser.parse_args()
    cfg = runner.locked()
    ext = recovery.register(cfg)
    editor = editorial.locked(cfg, ext)
    if args.command == "register":
        spec = registration(editor)
        write_once(REGISTRATION, json_bytes(spec))
        print(fingerprint(json_bytes(spec)))
    else:
        reg = locked(editor)
        if args.command == "run":
            task = next(
                r for r in runner.tasks(args.questions) if r["task_id"] == args.task_id
            )
            print(json.dumps(run_one(task, cfg, ext, editor, reg)))
        else:
            print(
                json.dumps(
                    export(args.questions, args.directory, cfg, ext, editor, reg),
                    indent=2,
                )
            )
