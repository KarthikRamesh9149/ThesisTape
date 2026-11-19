"""Fund a source-bound final writer and independent reviewer only for otherwise unanswered assignments."""

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
from scripts import source_completion as engine
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key
from scripts.team_meter import verify_ledger

from . import recovery, reviewed_release, runner

REGISTRATION = Path(__file__).with_name("editorial-registration.json")
POLICY = Path(__file__).with_name("editorial-policy.md")
ROOT = REPO / "runs/financegym-editorial"


def registration(cfg, ext):
    prior = reviewed_release.locked(cfg, ext)
    return {
        "version": 1,
        "reviewed_release_sha256": fingerprint(json_bytes(prior)),
        "additional_cap_usd": str(engine.CAP),
        "policy_sha256": fingerprint(committed(POLICY)),
        "sources": {
            str(p.relative_to(REPO)): fingerprint(committed(p))
            for p in (Path(__file__), Path(engine.__file__))
        },
    }


def locked(cfg, ext):
    spec = registration(cfg, ext)
    require(
        json.loads(committed(REGISTRATION)) == spec,
        "Funded source completion differs from its registered procedure",
    )
    return spec


def folders(row):
    return tuple(
        base / row["task_id"] / "research"
        for base in (runner.RESEARCH, recovery.OUTPUT)
    )


def eligible(row, cfg):
    originals = folders(row)
    if not all(recovery.needs_research(p.parent) for p in originals):
        return False
    second = load(originals[1] / "run.json")
    return (
        reviewed_release.approved_draft(
            row, originals[1], second, cfg["archive_version"]
        )
        is None
    )


def ticket(row, spec, chain):
    return {
        "task_id": row["task_id"],
        "editorial_registration_sha256": fingerprint(json_bytes(spec)),
        "original_investigations": [r["original_run_sha256"] for r in chain],
    }


def run_one(row, cfg, ext, spec):
    require(
        eligible(row, cfg),
        "An already publishable investigation cannot receive additional funding",
    )
    chain, _ = reviewed_release.unresolved(row, cfg, ext, recovery.identifiers())
    original = folders(row)
    out = ROOT / row["task_id"] / "research"
    provider = OpenAIResponses(project_key(REPO / ".env.local"))
    for old in original:
        provider = runner.DistinctProvider(provider, old)
    rec = engine.run(
        row,
        cfg["archive_version"],
        original,
        out,
        ticket(row, spec, chain),
        CountJournal(out / "counts", provider),
    )
    return {
        k: rec[k]
        for k in ("state", "estimated_model_cost_usd", "unreconciled_reservation_usd")
    }


def resolve(row, cfg, ext, spec, ids):
    directory = ROOT / row["task_id"] / "research"
    if not eligible(row, cfg):
        require(
            not (directory / "run.json").exists(),
            "Unexpected funded editing of a publishable report",
        )
        return reviewed_release.resolve(row, cfg, ext, ids)
    chain, editions = reviewed_release.unresolved(row, cfg, ext, ids)
    rec = load(directory / "run.json")
    verify_private(directory, rec, ids)
    original = folders(row)
    prev_requests = {
        call["request_sha256"]
        for f in original
        for call in load(f / "run.json")["calls"].values()
    }
    prev_inputs = {p.stem for f in original for p in (f / "counts").glob("*.json")}
    require(
        not prev_requests & {c["request_sha256"] for c in rec["calls"].values()}
        and not prev_inputs & {p.stem for p in (directory / "counts").glob("*.json")},
        "Editorial generation repeated an original request",
    )
    require(
        not list((directory / "searches").glob("*.json"))
        and not list((directory.parent / "operator-http").glob("*.json")),
        "Funded editing cannot perform new retrieval",
    )
    saved, memo = engine.audit(
        row, cfg["archive_version"], original, directory, ticket(row, spec, chain)
    )
    require(memo is not None, "The source editor has no approved material")
    answer, visible = runner.reviewed_answer(row, saved, memo)
    paid, held = verify_ledger(rec)
    elapsed = round(
        (
            datetime.fromisoformat(rec["finished_at"])
            - datetime.fromisoformat(rec["created_at"])
        ).total_seconds(),
        3,
    )
    edit = {
        "profile": engine.PROFILE,
        "state": visible["release"],
        "report_sha256": visible["memo_sha256"],
        "source_report_sha256": saved["memo_sha256"],
        "original_run_sha256": fingerprint((directory / "run.json").read_bytes()),
        "estimated_cost_usd": str(paid),
        "outstanding_usd": str(sum(held.values(), Decimal(0))),
        "elapsed_s": elapsed,
        "provider_generations": len(rec["calls"]),
        "transport_requests": 0,
        "attribution_review": visible.get("attribution_review"),
    }
    answer.update(
        num_docs_retrieved=len(editions),
        elapsed_s=round(sum(c["elapsed_s"] for c in chain) + elapsed, 3),
    )
    return answer, {
        "task_id": row["task_id"],
        "primary_profile": chain[0]["profile"],
        "profile": engine.PROFILE,
        "state": visible["release"],
        "report_sha256": visible["memo_sha256"],
        "attempts": chain,
        "editorial": edit,
        "estimated_cost_usd": str(
            sum(Decimal(a["estimated_cost_usd"]) for a in [*chain, edit])
        ),
        "outstanding_usd": str(
            sum(Decimal(a["outstanding_usd"]) for a in [*chain, edit])
        ),
    }


def export(questions, dest, cfg, ext, spec):
    ids, rows = recovery.identifiers(), []
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        area = Path(tmp) / "submission"
        area.mkdir()
        with (area / "answers.jsonl").open("xb") as f:
            for row in runner.tasks(questions):
                answer, proof = resolve(row, cfg, ext, spec, ids)
                f.write(
                    json.dumps(
                        answer, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                    + b"\n"
                )
                rows.append(proof)
        validation = runner.validate_file(questions, area / "answers.jsonl")
        primary = sum(len(r["attempts"]) == 1 for r in rows)
        retained = sum("reviewed_publication" in r for r in rows)
        funded = sum("editorial" in r for r in rows)
        summary = {
            "tasks": len(rows),
            "verified_provider_generations": len(ids["response_id"]),
            "answers_sha256": validation["answers_sha256"],
            "registration_sha256": fingerprint(json_bytes(cfg)),
            "recovery_registration_sha256": fingerprint(json_bytes(ext)),
            "reviewed_release_registration_sha256": spec["reviewed_release_sha256"],
            "editorial_registration_sha256": fingerprint(json_bytes(spec)),
            "primary_releases": primary,
            "recovery_releases": len(rows) - primary - retained - funded,
            "reviewed_first_draft_releases": retained,
            "editorial_releases": funded,
            "editorial_cost_usd": str(
                sum(
                    (
                        Decimal(r["editorial"]["estimated_cost_usd"])
                        for r in rows
                        if "editorial" in r
                    ),
                    Decimal(0),
                )
            ),
            "qualified": sum(r["state"] == "qualified" for r in rows),
            "total_research_cost_usd": str(
                sum(Decimal(r["estimated_cost_usd"]) for r in rows)
            ),
            "outstanding_usd": str(sum(Decimal(r["outstanding_usd"]) for r in rows)),
        }
        (area / "audit.json").write_bytes(
            json_bytes({**summary, "tasks_audited": rows})
        )
        if dest.exists():
            require(
                all(
                    (dest / name).read_bytes() == (area / name).read_bytes()
                    for name in ("answers.jsonl", "audit.json")
                ),
                "Existing submission differs",
            )
        else:
            area.rename(dest)
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    actions = p.add_subparsers(dest="action", required=True)
    actions.add_parser("register")
    for name in ("run", "export"):
        s = actions.add_parser(name)
        s.add_argument("questions", type=Path)
        if name == "run":
            s.add_argument("--task-id", required=True)
        else:
            s.add_argument("output", type=Path)
    args = p.parse_args()
    cfg = runner.locked()
    ext = recovery.register(cfg)
    if args.action == "register":
        output = registration(cfg, ext)
        write_once(REGISTRATION, json_bytes(output))
    else:
        spec = locked(cfg, ext)
        if args.action == "run":
            selected = next(
                r for r in runner.tasks(args.questions) if r["task_id"] == args.task_id
            )
            output = run_one(selected, cfg, ext, spec)
        else:
            output = export(args.questions, args.output, cfg, ext, spec)
    print(json.dumps(output, indent=2))
