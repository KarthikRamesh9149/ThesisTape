"""Complete and independently review a memo from already retrieved, point-in-time sources."""

from datetime import UTC, datetime
from decimal import Decimal

from pydantic import Field

from . import research_desk_agent as core
from . import reviewed_research as reviewed
from . import verified_arithmetic as arithmetic
from .disney_hulu_case import CaseError
from .open_desk_report import SavedArchive
from .research_team import check_configuration, new_record
from .run_checkpoint import load, own_run, publish
from .sol_baseline import fingerprint, json_bytes
from .sol_provider import ASTRA, MODEL
from .team_meter import Meter, recorded_output, verify_ledger
from .team_report import typed_prompt

PROFILE = "source_completion"
CAP = Decimal("0.55")
WRITE = (
    reviewed.WRITE
    + """
Prepare a concise final memorandum from the combined dated extracts of two investigations. Answer the
question directly, explain the principal mechanism, an alternative and a falsifying observation. When
only the question asserts a premise, state the conditional conclusion and identify the missing confirmation.
Do not repeat an unverified numeric premise as fact. Each paragraph should make one central claim, cite
one or two short exact witnesses, and stay under 90 words. Keep the entire draft under 420 words.
Do not describe the research workflow or invent facts to fill a gap."""
)


class ShortDraft(core.Draft):
    paragraphs: list[core.Paragraph] = Field(min_length=1, max_length=5)


def materials(question, cutoff, version, folders):
    gateway = SavedArchive(cutoff, version)
    inventory, bodies = [], []
    for folder in folders:
        record = load(folder / "run.json")
        if (record["question"], record["cutoff"], record["archive_version"]) != (
            question,
            cutoff,
            version,
        ):
            raise CaseError(
                "Saved investigation has a different question, cutoff or source service"
            )
        searches = []
        for p in sorted((folder / "searches").glob("*.json")):
            item = load(p)
            body = item["body"]
            if (
                fingerprint(json_bytes(body)) != item["sha256"]
                or fingerprint(json_bytes(body["request"])) != p.stem
                or body["request"]["max_date"] != cutoff
                or body["request"]["version"] != version
            ):
                raise CaseError("Saved source response or original cutoff changed")
            searches.append(item["sha256"])
            bodies.append(body)
        inventory.append(
            {
                "run_sha256": fingerprint((folder / "run.json").read_bytes()),
                "searches": searches,
            }
        )
    if not bodies:
        raise CaseError("Both investigations have no dated search result")
    passages = reviewed.merge(bodies, question, gateway.check_source)
    if not passages:
        raise CaseError("Both investigations have no eligible source passages")
    searches = [
        {
            "query": b["request"]["query"],
            "max_date": cutoff,
            "response_sha256": fingerprint(json_bytes(b)),
            "admitted_urls": [s["url"] for s in b["sources"]],
            "service_failures": b["service_failures"],
        }
        for b in bodies
    ]
    return inventory, passages, searches


def compose(question, cutoff, passages, searches, ask):
    context = {
        "question": question,
        "cutoff": cutoff,
        "decision": "Complete the analyst's original question using the available dated records.",
        "requirements": [question],
    }
    material = {
        **context,
        "originals": core.brief_sources(passages),
        "scope": "Combined original source editions only.",
    }
    arithmetic_plan = arithmetic.Plan.model_validate_json(
        ask(
            "completion.arithmetic",
            typed_prompt(
                MODEL, arithmetic.INSTRUCTIONS, material, arithmetic.Plan, effort="low"
            ),
            1300,
        )
    )
    calculations, errors = arithmetic.compile_plan(
        arithmetic_plan.operations, passages, None, normalize_labels=True
    )
    writing = {
        **material,
        "checked_calculations": calculations,
        "calculation_errors": errors,
    }
    draft = ShortDraft.model_validate_json(
        ask(
            "lead.memo",
            typed_prompt(ASTRA, WRITE, writing, ShortDraft, effort="medium"),
            3500,
        )
    )
    approved, rejected, missing = reviewed.review(
        draft, context, passages, calculations, ask, "review.initial", revision=4
    )
    history = [{"approved": len(approved), "blocked": rejected, "missing": missing}]
    if not approved:
        repair = {
            **writing,
            "rejected": rejected,
            "missing": missing,
            "draft": draft.model_dump(),
            "editor_assignment": "Replace unsupported text with a short, directly evidenced answer. Use conditional analysis where precise inputs are absent.",
        }
        raw = ask(
            "lead.revise",
            typed_prompt(ASTRA, WRITE, repair, ShortDraft, effort="low"),
            2500,
            keep_usd=Decimal("0.07"),
        )
        if raw:
            updated = ShortDraft.model_validate_json(raw)
            approved, rejected, missing = reviewed.review(
                updated,
                context,
                passages,
                calculations,
                ask,
                "review.recheck",
                revision=4,
            )
            history.append(
                {"approved": len(approved), "blocked": rejected, "missing": missing}
            )
    trace = {
        "question": question,
        "cutoff": cutoff,
        "searches": searches,
        "evidence": passages,
        "calculations": calculations,
        "calculation_errors": errors,
        "paragraphs": approved,
        "reviews": history,
        "release": "qualified" if approved else "withheld",
    }
    if not approved:
        return None, trace
    return reviewed.render(
        question, cutoff, approved, passages, calculations, True, inline_locators=True
    ), trace


def configuration(row, version, inventory, ticket):
    return {
        "workflow": "dated-source-completion-v1",
        "research_profile": PROFILE,
        "question": row["question"],
        "cutoff": row["cutoff"],
        "archive_version": version,
        "assignment": ticket,
        "admitted_source_inventory": inventory,
        "author_model": ASTRA,
        "reviewer_model": MODEL,
    }


def run(row, version, folders, directory, ticket, provider):
    inventory, passages, searches = materials(
        row["question"], row["cutoff"], version, folders
    )
    cfg = configuration(row, version, inventory, ticket)
    directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with own_run(directory.parent):
        if directory.is_dir():
            rec = load(directory / "run.json")
            check_configuration(rec, cfg)
            if rec["state"] in {
                "qualified",
                "withheld",
                "failed",
                "needs_reconciliation",
            }:
                return rec
            meter = Meter(directory, rec, provider, continuing=True)
            meter.restore()
        else:
            rec = new_record(cfg, directory, CAP)
            meter = Meter(directory, rec, provider)
        try:
            memo, trace = compose(
                row["question"], row["cutoff"], passages, searches, meter
            )
            if meter.seen != set(rec["calls"]) | set(rec.get("skipped_calls", {})):
                raise CaseError("Unmatched generation in source completion")
            paid, held = verify_ledger(rec)
            if held:
                raise CaseError("Unreconciled generation in source completion")
            finished = datetime.now(UTC).isoformat()
            result = {
                **trace,
                "started_at": rec["created_at"],
                "finished_at": finished,
                "model_cost_usd": str(paid),
                "memo_sha256": fingerprint(memo) if memo else None,
            }
            if memo:
                publish(directory / "memo.md", memo)
            publish(directory / "result.json", json_bytes(result))
            rec.update(
                state=trace["release"],
                result_sha256=fingerprint(json_bytes(result)),
                finished_at=finished,
            )
        except Exception as e:
            rec.update(
                state="needs_reconciliation" if meter.pending else "failed",
                failure=str(e) if isinstance(e, CaseError) else type(e).__name__,
                finished_at=datetime.now(UTC).isoformat(),
            )
            raise
        finally:
            meter.save()
        return rec


def audit(row, version, folders, directory, ticket):
    inventory, passages, searches = materials(
        row["question"], row["cutoff"], version, folders
    )
    rec = load(directory / "run.json")
    check_configuration(rec, configuration(row, version, inventory, ticket))
    if Decimal(rec["budget_usd"]) != CAP:
        raise CaseError("Source completion ceiling changed")
    spent, pending = verify_ledger(rec)
    if pending:
        raise CaseError("Source completion has an outstanding call")
    entries = rec["calls"] | rec.get("skipped_calls", {})
    used = set()

    def ask(stage, prompt, limit, *, keep_usd=None):
        if stage not in entries or stage in used:
            raise CaseError("Unrecorded or repeated editorial generation")
        used.add(stage)
        return recorded_output(entries[stage], stage, prompt, limit, keep_usd)

    memo, trace = compose(row["question"], row["cutoff"], passages, searches, ask)
    saved = load(directory / "result.json")
    expected = {
        **trace,
        "started_at": rec["created_at"],
        "finished_at": rec["finished_at"],
        "model_cost_usd": str(spent),
        "memo_sha256": fingerprint(memo) if memo else None,
    }
    raw = (
        (directory / "memo.md").read_bytes()
        if (directory / "memo.md").is_file()
        else None
    )
    if (
        used != set(entries)
        or raw != memo
        or saved != expected
        or fingerprint(json_bytes(saved)) != rec["result_sha256"]
        or rec["state"] != trace["release"]
    ):
        raise CaseError(
            "Editorial charges, evidence, review or report differ from the original"
        )
    return saved, memo
