"""Apply the owner's total budget to unanswered questions while preserving every original result."""

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
from evaluations.paired.product_recovery import interrupted_receipts
from evaluations.paired.registration import REPO
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key
from scripts.team_meter import verify_ledger

from . import (
    budget,
    draft_review,
    editorial,
    recovery,
    runner,
)
from . import (
    titled_publication as previous,
)

POLICY = Path(__file__).with_name("budget-review-policy.md")
REGISTRATION = POLICY.with_name("budget-review-registration.json")


def source_spec(prior):
    return {
        "version": 1,
        "prior_title_registration_sha256": prior,
        "per_review_cap_usd": str(draft_review.CAP),
        "sources": {
            str(p.relative_to(REPO)): fingerprint(committed(p))
            for p in (POLICY, Path(__file__), Path(draft_review.__file__))
        },
    }


def register(prior):
    spec = source_spec(prior)
    spec["previous_independent_investigations"] = sorted(
        p.parents[1].name for p in recovery.OUTPUT.glob("*/research/run.json")
    )
    write_once(REGISTRATION, json_bytes(spec))
    return spec


def locked(prior):
    spec = json.loads(committed(REGISTRATION))
    expected = source_spec(prior)
    require(
        all(spec.get(k) == v for k, v in expected.items()),
        "Prospective budget review changed",
    )
    ids = spec["previous_independent_investigations"]
    require(sorted(set(ids)) == ids, "Repeated or unsorted prior investigation")
    return spec


def directory(key):
    # The project budget already counts every charged run under this parent.
    return editorial.ROOT / ("primary-draft-" + key) / "research"


def assignment(row, original, spec):
    return {
        "task_id": row["task_id"],
        "budget_review_registration_sha256": fingerprint(json_bytes(spec)),
        "original_run_sha256": fingerprint((original / "run.json").read_bytes()),
    }


def eligible(row, cfg, spec):
    if row["task_id"] in spec["previous_independent_investigations"]:
        return False
    src = runner.RESEARCH / row["task_id"] / "research"
    return (src / "run.json").is_file() and draft_review.candidate(
        row, src, cfg["archive_version"]
    ) is not None


def run_one(row, cfg, spec):
    key = row["task_id"]
    src = runner.RESEARCH / key / "research"
    dst = directory(key)
    require(eligible(row, cfg, spec), "The original draft cannot receive this review")
    require(
        not (recovery.OUTPUT / key / "research/run.json").exists(),
        "Independent investigation already dispatched",
    )
    if (dst / "run.json").is_file() and load(dst / "run.json")[
        "state"
    ] in runner.FINAL | {"needs_reconciliation"}:
        return load(dst / "run.json")["state"]
    answer, _, _ = recovery.attempt(
        src.parent,
        row,
        cfg["archive_version"],
        runner.assignment(row, cfg),
        runner.route(row, cfg),
        recovery.identifiers(),
    )
    require(answer is None, "An accepted original cannot receive another review")
    provider = runner.DistinctProvider(
        OpenAIResponses(project_key(REPO / ".env.local")), src
    )
    with budget.ProjectBudget().reserve(dst / "run.json", draft_review.CAP):
        rec = draft_review.run(
            row,
            cfg["archive_version"],
            src,
            dst,
            assignment(row, src, spec),
            CountJournal(dst / "counts", provider),
        )
    return rec["state"]


def review_info(row, cfg, spec, src, dst):
    rec = load(dst / "run.json")
    require(
        row["task_id"] not in spec["previous_independent_investigations"],
        "An established independent investigation was replaced",
    )
    saved, memo = draft_review.audit(
        row, cfg["archive_version"], src, dst, assignment(row, src, spec)
    )
    paid, held = verify_ledger(rec, partial=True)
    elapsed = round(
        (
            datetime.fromisoformat(rec["finished_at"])
            - datetime.fromisoformat(rec["created_at"])
        ).total_seconds(),
        3,
    )
    require(elapsed >= 0, "Invalid elapsed review time")
    proof = {
        "profile": draft_review.PROFILE,
        "state": rec["state"],
        "report_sha256": fingerprint(memo) if memo else None,
        "original_run_sha256": fingerprint((dst / "run.json").read_bytes()),
        "primary_draft_sha256": rec["primary_draft_sha256"],
        "registration_sha256": fingerprint(json_bytes(spec)),
        "estimated_cost_usd": str(paid),
        "outstanding_usd": str(sum(held.values(), Decimal(0))),
        "elapsed_s": elapsed,
        "provider_generations": sum("confirmed_ms" in c for c in rec["calls"].values()),
        "transport_requests": 0,
    }
    return saved, memo, proof


def resolve(row, cfg, ext, editor, prior, spec, ids):
    key = row["task_id"]
    src = runner.RESEARCH / key / "research"
    dst = directory(key)
    if not (dst / "run.json").exists():
        require(
            not eligible(row, cfg, spec), "An eligible primary draft review is missing"
        )
        return previous.resolve(row, cfg, ext, editor, prior, ids)
    saved, memo, review = review_info(row, cfg, spec, src, dst)
    rec = load(dst / "run.json")
    if rec["state"] == "needs_reconciliation":
        draft = draft_review.candidate(row, src, cfg["archive_version"])
        interrupted_receipts(
            dst,
            rec,
            ids,
            lambda ask: ask(
                draft_review.STAGE,
                draft_review.prompt(draft),
                draft_review.REVIEW_LIMIT,
            ),
        )
    else:
        verify_private(dst, rec, ids)
    if memo is not None:
        require(
            not (recovery.OUTPUT / key / "research/run.json").exists(),
            "A released independent draft review cannot be rerun",
        )
        earlier, original, editions = recovery.attempt(
            src.parent,
            row,
            cfg["archive_version"],
            runner.assignment(row, cfg),
            runner.route(row, cfg),
            ids,
        )
        require(
            earlier is None and original["state"] == "failed",
            "Released original was replaced",
        )
        answer, visible = runner.reviewed_answer(row, saved, memo)
        require(
            visible["release"] == "qualified", "Draft review must qualify its answer"
        )
        answer.update(elapsed_s=original["elapsed_s"], num_docs_retrieved=len(editions))
        proof = {
            "task_id": key,
            "primary_profile": original["profile"],
            "profile": draft_review.PROFILE,
            "state": "qualified",
            "report_sha256": review["report_sha256"],
            "estimated_cost_usd": original["estimated_cost_usd"],
            "outstanding_usd": original["outstanding_usd"],
            "attempts": [original],
        }
    else:
        answer, proof = previous.resolve(row, cfg, ext, editor, prior, ids)
    answer["elapsed_s"] = round(answer["elapsed_s"] + review["elapsed_s"], 3)
    proof = {
        **proof,
        "primary_draft_review": review,
        "estimated_cost_usd": str(
            Decimal(proof["estimated_cost_usd"]) + Decimal(review["estimated_cost_usd"])
        ),
        "outstanding_usd": str(
            Decimal(proof["outstanding_usd"]) + Decimal(review["outstanding_usd"])
        ),
    }
    return answer, proof


def export(questions, dest, cfg, ext, editor, prior, spec):
    ids = recovery.identifiers()
    dest.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=dest.parent) as temp:
        area = Path(temp) / "export"
        area.mkdir()
        proofs = []
        with (area / "answers.jsonl").open("xb") as stream:
            for row in runner.tasks(questions):
                answer, proof = resolve(row, cfg, ext, editor, prior, spec, ids)
                proofs.append(proof)
                stream.write(
                    json.dumps(
                        answer, ensure_ascii=False, separators=(",", ":")
                    ).encode()
                    + b"\n"
                )
        check = runner.validate_file(questions, area / "answers.jsonl")
        resumed = sum(len(p["attempts"]) == 2 for p in proofs)
        edits = [p["editorial"] for p in proofs if "editorial" in p]
        retained = sum("reviewed_publication" in p for p in proofs)
        reviews = [
            p["primary_draft_review"] for p in proofs if "primary_draft_review" in p
        ]
        reviewed = sum(r["state"] == "qualified" for r in reviews)
        money = lambda items, field: str(
            sum((Decimal(p[field]) for p in items), Decimal(0))
        )
        summary = {
            "tasks": len(proofs),
            "answers_sha256": check["answers_sha256"],
            "verified_provider_generations": len(ids["response_id"]),
            "registration_sha256": fingerprint(json_bytes(cfg)),
            "recovery_registration_sha256": fingerprint(json_bytes(ext)),
            "reviewed_release_registration_sha256": editor["reviewed_release_sha256"],
            "editorial_registration_sha256": fingerprint(json_bytes(editor)),
            "title_registration_sha256": prior,
            "budget_review_registration_sha256": fingerprint(json_bytes(spec)),
            "primary_releases": len(proofs) - resumed - reviewed,
            "recovery_releases": resumed - len(edits) - retained,
            "reviewed_first_draft_releases": retained,
            "editorial_releases": len(edits),
            "editorial_cost_usd": money(edits, "estimated_cost_usd"),
            "title_lookup_releases": sum("title_lookup" in p for p in proofs),
            "primary_draft_review_attempts": len(reviews),
            "primary_draft_review_releases": reviewed,
            "primary_draft_review_cost_usd": money(reviews, "estimated_cost_usd"),
            "qualified": sum(p["state"] == "qualified" for p in proofs),
            "total_research_cost_usd": money(proofs, "estimated_cost_usd"),
            "outstanding_usd": money(proofs, "outstanding_usd"),
        }
        (area / "audit.json").write_bytes(
            json_bytes({**summary, "tasks_audited": proofs})
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
    return summary


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="operation", required=True)
    sub.add_parser("register")
    for name in ("run", "export"):
        cmd = sub.add_parser(name)
        cmd.add_argument("questions", type=Path)
        if name == "run":
            cmd.add_argument("--task-id", required=True)
        else:
            cmd.add_argument("directory", type=Path)
    a = p.parse_args()
    cfg = runner.locked()
    ext = recovery.register(cfg)
    editor = editorial.locked(cfg, ext)
    prior = previous.locked(editor)
    if a.operation == "register":
        print(fingerprint(json_bytes(register(prior))))
    else:
        spec = locked(prior)
        if a.operation == "run":
            row = next(
                r for r in runner.tasks(a.questions) if r["task_id"] == a.task_id
            )
            print(json.dumps(run_one(row, cfg, spec)))
        else:
            print(
                json.dumps(
                    export(a.questions, a.directory, cfg, ext, editor, prior, spec),
                    indent=2,
                )
            )
