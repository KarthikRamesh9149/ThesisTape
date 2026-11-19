"""Durable execution of the quantitative research profiles, retaining the original registered research engine."""

import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from . import research_desk_agent as archive
from .disney_hulu_case import CaseError
from .packet_archive import SavedSearches
from .research_team import check_configuration, new_record
from .reviewed_research import research as review_workflow
from .run_checkpoint import PauseRequested, load, own_run, publish
from .sol_baseline import fingerprint, json_bytes
from .sol_provider import MODEL
from .team_meter import Meter, recorded_output, verify_ledger
from .temporal_web import ArchiveGateway


def configuration(question, cutoff, budget, gateway, profile, assignment):
    if profile not in ("evidence_team", "evidence_team_v2", "evidence_team_v3"):
        raise CaseError("Unknown quantitative research profile")
    config = archive.configuration(
        question, cutoff, budget, gateway, "mixed", assignment
    )
    return {**config, "research_profile": profile, "worker_model": MODEL}


def research(question, cutoff, gateway, dispatch, *, profile):
    return review_workflow(
        question,
        cutoff,
        gateway,
        dispatch,
        revision={"evidence_team": 1, "evidence_team_v2": 2, "evidence_team_v3": 3}[
            profile
        ],
    )


def audit(output, gateway, *, record=None):
    record = record or load(output / "run.json")
    cfg = configuration(
        record["question"],
        record["cutoff"],
        Decimal(record["budget_usd"]),
        gateway,
        record["research_profile"],
        record["assignment"],
    )
    check_configuration(record, cfg)
    settled, owed = verify_ledger(record)
    if owed:
        raise CaseError("Research charges are unsettled")
    response = load(output / "result.json")
    if fingerprint(json_bytes(response)) != record["result_sha256"]:
        raise CaseError("Research result changed")
    seen = set()
    entries = {**record["calls"], **record.get("skipped_calls", {})}

    def ask(stage, prompt, limit, *, keep_usd=None):
        if stage not in entries or stage in seen:
            raise CaseError("Missing or duplicate saved request")
        seen.add(stage)
        return recorded_output(entries[stage], stage, prompt, limit, keep_usd)

    memo, trace = research(
        record["question"],
        record["cutoff"],
        SavedSearches(gateway, output / "searches", offline=True),
        ask,
        profile=record["research_profile"],
    )
    if (
        seen != set(entries)
        or any(response.get(k) != v for k, v in trace.items())
        or response["question"] != record["question"]
        or response["cutoff"] != record["cutoff"]
        or Decimal(response["model_cost_usd"]) != settled
        or record["state"] != trace["release"]
        or (fingerprint(memo) if memo else None) != response["memo_sha256"]
        or (
            (output / "memo.md").read_bytes() if (output / "memo.md").exists() else None
        )
        != memo
    ):
        raise CaseError(
            "Verified research, evidence or released text differs from the saved report"
        )
    return response


def run(
    question,
    cutoff,
    output,
    cache,
    limit,
    *,
    provider=None,
    gateway=None,
    profile="evidence_team_v3",
    assignment=None,
    queued=False,
    pause=None,
):
    budget = Decimal(limit)
    cfg = configuration(question, cutoff, budget, gateway, profile, assignment)
    output.parent.mkdir(parents=True, exist_ok=True)
    lock = output.parent / ("." + output.name + ".lock")
    lock.mkdir(mode=0o700, exist_ok=True)
    with own_run(lock):
        if not output.exists():
            with tempfile.TemporaryDirectory(dir=output.parent) as tmp:
                staged = Path(tmp) / output.name
                record = new_record(cfg, staged, budget)
                record["state"] = "queued" if queued else "running"
                Meter(staged, record, provider)
                staged.rename(output)
        record = load(output / "run.json")
        check_configuration(record, cfg)
        if queued:
            return record
        gateway = gateway or ArchiveGateway(cutoff, cache)
        if record.get("result_sha256"):
            return audit(output, gateway, record=record)
        meter = Meter(output, record, provider, continuing=True, pause=pause)
        meter.restore()
        record.update(state="running")
        record.pop("failure", None)
        meter.save()
        try:
            memo, trace = research(
                question,
                cutoff,
                SavedSearches(gateway, output / "searches"),
                meter,
                profile=profile,
            )
            settled, owed = verify_ledger(record)
            if owed or meter.seen != set(record["calls"]) | set(
                record.get("skipped_calls", {})
            ):
                raise CaseError("Unsettled research ledger")
            if memo:
                publish(output / "memo.md", memo)
            result = {
                **trace,
                "question": question,
                "cutoff": cutoff,
                "memo_sha256": fingerprint(memo) if memo else None,
                "model_cost_usd": str(settled),
                "started_at": record["created_at"],
                "finished_at": datetime.now(UTC).isoformat(),
            }
            data = json_bytes(result)
            publish(output / "result.json", data)
            record.update(state=trace["release"], result_sha256=fingerprint(data))
            return result
        except PauseRequested:
            record["state"] = "paused"
            raise
        except Exception as exc:
            record.update(
                state="needs_reconciliation" if meter.pending else "failed",
                failure=str(exc) if isinstance(exc, CaseError) else type(exc).__name__,
            )
            raise
        finally:
            record["finished_at"] = datetime.now(UTC).isoformat()
            meter.save()


def main():
    import argparse
    import json

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--question", required=True)
    parser.add_argument("--cutoff", required=True)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--archive", type=Path, default=archive.ROOT / "runs/dated-web")
    parser.add_argument("--budget-usd", required=True, type=Decimal)
    args = parser.parse_args()
    result = run(args.question, args.cutoff, args.output, args.archive, args.budget_usd)
    print(
        json.dumps(
            {k: result[k] for k in ("release", "memo_sha256", "model_cost_usd")},
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
