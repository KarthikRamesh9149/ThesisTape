"""Publish the independently approved portion of a failed optional revision. No network or model calls."""

import argparse
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from evaluations.paired.confirmation import committed, write_once
from evaluations.paired.corpus import require
from scripts import quantitative_engine
from scripts.open_desk_report import SavedArchive
from scripts.packet_archive import SavedSearches
from scripts.question_attribution import publish
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_meter import recorded_output

from . import recovery, runner

POLICY = Path(__file__).with_name("reviewed-release.md")
REGISTRATION = POLICY.with_name("reviewed-release-registration.json")


def registration(base, extension):
    return {
        "version": 1,
        "primary_registration_sha256": fingerprint(json_bytes(base)),
        "independent_recovery_sha256": fingerprint(json_bytes(extension)),
        "policy_sha256": fingerprint(committed(POLICY)),
        "implementation_sha256": fingerprint(committed(Path(__file__))),
        "additional_model_cost_usd": "0",
    }


def locked(base, extension):
    result = registration(base, extension)
    require(
        json.loads(committed(REGISTRATION)) == result,
        "Reviewed release differs from its registration",
    )
    return result


def approved_draft(row, run, rec, version):
    if (
        rec["state"] not in {"failed", "needs_reconciliation"}
        or rec["research_profile"] != "evidence_team_v4"
    ):
        return None
    calls = rec["calls"]
    if not calls.get("review.initial", {}).get("output"):
        return None
    entries = calls | rec.get("skipped_calls", {})
    used = set()

    def replay(stage, prompt, limit, *, keep_usd=None):
        require(stage not in used, "Repeated stage in reviewed-draft replay")
        used.add(stage)
        if stage == "lead.revise":
            if stage in entries:
                recorded_output(
                    {**entries[stage], "output": None}, stage, prompt, limit, keep_usd
                )
            else:
                key = fingerprint(json_bytes(prompt))
                path = run / "counts" / (key + ".json")
                require(
                    (path.is_file() and load(path)["prompt_sha256"] == key)
                    or str(rec.get("failure", "")).startswith(
                        "OpenAI responses/input_tokens "
                    ),
                    "No evidence that an optional revision reached preflight",
                )
            # The router takes its documented optional-rewrite branch. The charged
            # response (including any incomplete output) stays in the original ledger.
            return None
        require(
            stage in calls
            and calls[stage].get("provider", {}).get("status") == "completed"
            and calls[stage].get("output"),
            "A required step has no completed provider response",
        )
        return recorded_output(calls[stage], stage, prompt, limit, keep_usd)

    search = SavedSearches(
        SavedArchive(row["cutoff"], version), run / "searches", offline=True
    )
    memo, trace = quantitative_engine.research(
        row["question"], row["cutoff"], search, replay, profile=rec["research_profile"]
    )
    require(
        "lead.revise" in used and "review.initial" in used,
        "No independent review before an optional revision",
    )
    if not memo:
        return None
    require(
        trace["release"] == "qualified" and len(trace["reviews"]) == 1,
        "Expected a qualified publication based on one existing review",
    )
    raw = {
        **trace,
        "question": row["question"],
        "cutoff": row["cutoff"],
        "memo_sha256": fingerprint(memo),
        "started_at": rec["created_at"],
        "finished_at": rec["finished_at"],
    }
    visible, published = publish(raw, memo)
    if published is None:
        return None
    answer = runner.answer(row, raw, memo)
    review = trace["reviews"][0]
    evidence = {
        "method": "independently_reviewed_first_draft",
        "source_run_sha256": fingerprint((run / "run.json").read_bytes()),
        "review_receipt_sha256": calls["review.initial"]["receipt_sha256"],
        "approved_paragraphs": review["approved"],
        "published_paragraphs": len(visible["paragraphs"]),
        "remaining_coverage_gaps": review["missing"],
        "original_failure": rec.get("failure"),
        "additional_model_generations": 0,
        "additional_model_cost_usd": "0",
        "report_sha256": fingerprint(answer["report"].encode()),
    }
    return answer, visible, evidence


def unresolved(row, cfg, extension, ids):
    primary = runner.RESEARCH / row["task_id"]
    secondary = recovery.OUTPUT / row["task_id"]
    routes = [runner.route(row, cfg)]
    routes.append(recovery.second_route(cfg, routes[0][1]))
    assigned = [
        runner.assignment(row, cfg),
        recovery.ticket(row, cfg, extension, primary / "research"),
    ]
    chain, editions, prompts, calls = [], set(), set(), set()
    for loc, ticket, route in zip((primary, secondary), assigned, routes, strict=True):
        answer, attempt, sources = recovery.attempt(
            loc, row, cfg["archive_version"], ticket, route, ids
        )
        require(answer is None, "An existing publishable report cannot be replaced")
        editions.update(sources)
        run = loc / "research"
        new_prompts = {p.stem for p in (run / "counts").glob("*.json")}
        new_calls = {
            c["request_sha256"] for c in load(run / "run.json")["calls"].values()
        }
        require(
            not prompts & new_prompts and not calls & new_calls,
            "A recovery repeated the original request",
        )
        prompts.update(new_prompts)
        calls.update(new_calls)
        chain.append(attempt)
    return chain, editions


def resolve(row, cfg, extension, ids):
    primary = runner.RESEARCH / row["task_id"]
    secondary = recovery.OUTPUT / row["task_id"]
    if not recovery.needs_research(primary) or not recovery.needs_research(secondary):
        return recovery.resolve(row, cfg, extension, ids)
    chain, editions = unresolved(row, cfg, extension, ids)
    run = secondary / "research"
    published = approved_draft(row, run, load(run / "run.json"), cfg["archive_version"])
    require(
        published is not None,
        f"No independently approved draft available for {row['task_id']}",
    )
    answer, visible, evidence = published
    answer.update(
        num_docs_retrieved=len(editions),
        elapsed_s=round(sum(a["elapsed_s"] for a in chain), 3),
    )
    return answer, {
        "task_id": row["task_id"],
        "primary_profile": chain[0]["profile"],
        "profile": chain[-1]["profile"],
        "state": visible["release"],
        "report_sha256": evidence["report_sha256"],
        "estimated_cost_usd": str(sum(Decimal(a["estimated_cost_usd"]) for a in chain)),
        "outstanding_usd": str(sum(Decimal(a["outstanding_usd"]) for a in chain)),
        "attempts": chain,
        "reviewed_publication": evidence,
    }


def export(questions, dest):
    cfg = runner.locked()
    ext = recovery.register(cfg)
    reg = locked(cfg, ext)
    ids, audits = recovery.identifiers(), []
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        folder = Path(tmp) / "submission"
        folder.mkdir()
        with (folder / "answers.jsonl").open("xb") as file:
            for row in runner.tasks(questions):
                answer, proof = resolve(row, cfg, ext, ids)
                file.write(
                    json.dumps(
                        answer, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                    + b"\n"
                )
                audits.append(proof)
        checked = runner.validate_file(questions, folder / "answers.jsonl")
        extra = sum("reviewed_publication" in r for r in audits)
        primary = sum(len(r["attempts"]) == 1 for r in audits)
        summary = {
            "tasks": len(audits),
            "verified_provider_generations": len(ids["response_id"]),
            "answers_sha256": checked["answers_sha256"],
            "registration_sha256": fingerprint(json_bytes(cfg)),
            "recovery_registration_sha256": fingerprint(json_bytes(ext)),
            "reviewed_release_registration_sha256": fingerprint(json_bytes(reg)),
            "primary_releases": primary,
            "recovery_releases": len(audits) - primary - extra,
            "reviewed_first_draft_releases": extra,
            "qualified": sum(r["state"] == "qualified" for r in audits),
            "total_research_cost_usd": str(
                sum(Decimal(r["estimated_cost_usd"]) for r in audits)
            ),
            "outstanding_usd": str(sum(Decimal(r["outstanding_usd"]) for r in audits)),
        }
        (folder / "audit.json").write_bytes(
            json_bytes({**summary, "tasks_audited": audits})
        )
        if dest.exists():
            for name in ("answers.jsonl", "audit.json"):
                require(
                    (dest / name).read_bytes() == (folder / name).read_bytes(),
                    "Existing submission differs",
                )
        else:
            folder.rename(dest)
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    sub.add_parser("register")
    e = sub.add_parser("export")
    e.add_argument("questions", type=Path)
    e.add_argument("directory", type=Path)
    args = p.parse_args()
    if args.action == "register":
        base = runner.locked()
        ext = recovery.register(base)
        require(
            json.loads(committed(recovery.REGISTRATION)) == ext,
            "Independent investigation is unregistered",
        )
        value = registration(base, ext)
        write_once(REGISTRATION, json_bytes(value))
    else:
        value = export(args.questions, args.directory)
    print(json.dumps(value, indent=2))
