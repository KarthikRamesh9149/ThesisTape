"""Independently review the supported paragraphs of a primary draft whose optional revision ran out of funds."""

from datetime import UTC, datetime
from decimal import Decimal

from evaluations.paired.corpus import require
from scripts import research_desk_agent as core
from scripts.disney_hulu_case import CaseError
from scripts.open_desk_report import SavedArchive
from scripts.packet_archive import SavedSearches
from scripts.question_attribution import publish as check_attribution
from scripts.research_team import check_configuration, new_record
from scripts.run_checkpoint import load, own_run, publish
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import MODEL
from scripts.team_meter import Meter, recorded_output, verify_ledger
from scripts.team_report import typed_prompt

CAP = Decimal("0.075")
PROFILE = "independent_primary_draft_review"
STAGE = "review.primary_draft"
REVIEW_LIMIT = 1800


def usable(paragraphs):
    sections = {p["section"] for p in paragraphs}
    return "Answer" in sections and len(sections) >= 2


def candidate(row, run, version):
    record = load(run / "run.json")
    calls = record["calls"]
    if (
        record["state"] != "failed"
        or record["research_profile"] != "single_astra"
        or not record.get("failure", "").startswith("Required reservation USD")
        or not calls.get("lead.memo", {}).get("output")
        or "lead.revise" in calls
    ):
        return None
    used = set()

    def replay(stage, prompt, limit, *, keep_usd=None):
        require(stage not in used, "Repeated initial draft stage")
        used.add(stage)
        if stage == "lead.revise":
            key = fingerprint(json_bytes(prompt))
            preflight = run / "counts" / (key + ".json")
            require(
                preflight.is_file() and load(preflight)["prompt_sha256"] == key,
                "Missing the original unaffordable revision preflight",
            )
            return None
        require(
            stage in calls and calls[stage]["provider"]["status"] == "completed",
            "An original draft call did not complete",
        )
        return recorded_output(calls[stage], stage, prompt, limit, keep_usd)

    search = SavedSearches(
        SavedArchive(row["cutoff"], version), run / "searches", offline=True
    )
    memo, result = core.research(
        row["question"], row["cutoff"], search, replay, profile="single_astra"
    )
    require(
        used == set(calls) | {"lead.revise"}
        and result["release"] in {"withheld", "qualified"},
        "The original failure was not an optional revision",
    )
    if not memo:
        return None
    draft, filtered = check_attribution(
        {
            **result,
            "question": row["question"],
            "cutoff": row["cutoff"],
            "memo_sha256": fingerprint(memo),
        },
        memo,
    )
    return draft if filtered and usable(draft["paragraphs"]) else None


def prompt(draft):
    paragraphs = {f"A{i}": p for i, p in enumerate(draft["paragraphs"], 1)}
    cited = {pid for p in paragraphs.values() for pid in p["pids"]}
    data = {
        "question": draft["question"],
        "cutoff": draft["cutoff"],
        "paragraphs": paragraphs,
        "originals": core.brief_sources(draft["evidence"], cited),
        "calculations": draft["calculations"],
    }
    return typed_prompt(MODEL, core.REVIEW, data, core.Review, effort="medium")


def outcome(draft, raw):
    judgment = core.Review.model_validate_json(raw)
    paragraphs = draft["paragraphs"]
    decisions = {d.paragraph: d for d in judgment.decisions}
    require(
        len(decisions) == len(judgment.decisions)
        and set(decisions) == {f"A{i}" for i in range(1, len(paragraphs) + 1)},
        "The independent reviewer must decide every proposed paragraph exactly once",
    )
    approved = [p for i, p in enumerate(paragraphs, 1) if decisions[f"A{i}"].accept]
    if not usable(approved):
        return None, None
    memo = core.render(
        draft["question"],
        draft["cutoff"],
        approved,
        draft["evidence"],
        draft["calculations"],
        True,
    )
    saved = {
        **draft,
        "paragraphs": approved,
        "release": "qualified",
        "memo_sha256": fingerprint(memo),
        "independent_review": judgment.model_dump(),
    }
    return check_attribution(saved, memo)


def config(row, identity, version, draft):
    return {
        "question": row["question"],
        "cutoff": row["cutoff"],
        "archive_version": version,
        "research_profile": PROFILE,
        "assignment": identity,
        "primary_draft_sha256": draft["memo_sha256"],
        "reviewer_model": MODEL,
    }


def run(row, version, original, directory, identity, provider):
    draft = candidate(row, original, version)
    require(
        draft is not None,
        "The original draft has no eligible answer and supporting section",
    )
    cfg = config(row, identity, version, draft)
    directory.parent.mkdir(parents=True, exist_ok=True)
    with own_run(directory.parent):
        if directory.exists():
            record = load(directory / "run.json")
            check_configuration(record, cfg)
            if record["state"] in {
                "failed",
                "needs_reconciliation",
                "qualified",
                "withheld",
            }:
                return record
            meter = Meter(directory, record, provider, continuing=True)
            meter.restore()
        else:
            record = new_record(cfg, directory, CAP)
            meter = Meter(directory, record, provider)
        try:
            raw = meter(STAGE, prompt(draft), REVIEW_LIMIT)
            saved, memo = outcome(draft, raw)
            require(meter.seen == {STAGE}, "Unexpected paid review step")
            paid, _ = verify_ledger(record)
            if memo:
                publish(directory / "memo.md", memo)
            finish = datetime.now(UTC).isoformat()
            result = {
                "release": "qualified" if memo else "withheld",
                "memo_sha256": fingerprint(memo) if memo else None,
                "approved_paragraphs": len(saved["paragraphs"]) if memo else 0,
                "model_cost_usd": str(paid),
            }
            publish(directory / "result.json", json_bytes(result))
            record.update(
                state=result["release"],
                finished_at=finish,
                result_sha256=fingerprint(json_bytes(result)),
            )
        except Exception as e:
            record.update(
                state="needs_reconciliation" if meter.pending else "failed",
                finished_at=datetime.now(UTC).isoformat(),
                failure=str(e) if isinstance(e, CaseError) else type(e).__name__,
            )
            raise
        finally:
            meter.save()
        return record


def audit(row, version, original, directory, identity):
    record = load(directory / "run.json")
    draft = candidate(row, original, version)
    require(draft is not None, "Original qualifying draft does not reproduce")
    check_configuration(record, config(row, identity, version, draft))
    require(Decimal(record["budget_usd"]) == CAP, "Independent review ceiling changed")
    paid, held = verify_ledger(record, partial=True)
    require(
        not (directory / "searches").exists()
        and not (directory.parent / "operator-http").exists(),
        "A primary draft reviewer cannot retrieve new documents",
    )
    require(
        set(record["calls"]) <= {STAGE}, "Unexpected model call in primary draft review"
    )
    stage = record["calls"].get(STAGE)
    request = prompt(draft)
    if stage:
        raw = recorded_output(
            {**stage, "output": stage.get("output")}, STAGE, request, REVIEW_LIMIT
        )
    else:
        key = fingerprint(json_bytes(request))
        require(
            (directory / "counts" / (key + ".json")).is_file()
            or str(record.get("failure", "")).startswith("OpenAI "),
            "No recorded preflight or provider failure for the draft review",
        )
        raw = None
    state = record["state"]
    saved = memo = None
    if state in {"qualified", "withheld"}:
        require(stage and raw and not held, "Missing completed independent review")
        saved, memo = outcome(draft, raw)
        result = {
            "release": "qualified" if memo else "withheld",
            "memo_sha256": fingerprint(memo) if memo else None,
            "approved_paragraphs": len(saved["paragraphs"]) if memo else 0,
            "model_cost_usd": str(paid),
        }
        require(
            result["release"] == state
            and result == load(directory / "result.json")
            and fingerprint(json_bytes(result)) == record["result_sha256"],
            "Independent draft decisions changed",
        )
    else:
        require(
            state in {"failed", "needs_reconciliation"}, "Draft review is still active"
        )
    published = (
        (directory / "memo.md").read_bytes()
        if (directory / "memo.md").is_file()
        else None
    )
    require(published == memo, "The independently reviewed text changed")
    if saved is not None:
        saved = {
            **saved,
            "started_at": record["created_at"],
            "finished_at": record["finished_at"],
        }
    return saved, memo
