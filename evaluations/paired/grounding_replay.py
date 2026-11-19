"""Audit the revised validation against the genuine Nintendo requests, without generating responses."""

import argparse
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from scripts import paired_research, paired_v2
from scripts.disney_hulu_case import CaseError
from scripts.paired_packet import PacketCorpus
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import ASTRA, OpenAIResponses, project_key, reserve
from scripts.team_meter import recorded_output

from .corpus import export_development, require
from .event_audit import RESULTS, audit_event, checked_count
from .registration import REPO, ROOT, registered
from .runner import event_row

CASE = "nintendo-fy2021-guidance"
REVISION = ROOT / "revisions/grounding_v2"
STAGES = {"forecast.analyze", "forecast.repair", "lead.memo", "lead.memo_repair"}


class AdditionalRequest(Exception):
    """No matching historical response. Offline replay stops here."""


def trace(record, corpus, arm, workflow, validation=None):
    entries = {**record["calls"], **record.get("skipped_calls", {})}
    seen = set()

    def dispatch(stage, prompt, limit, *, keep_usd=None):
        if stage not in entries:
            raise AdditionalRequest(stage)
        try:
            raw = recorded_output(entries[stage], stage, prompt, limit, keep_usd)
        except CaseError as exc:
            raise AdditionalRequest(stage) from exc
        if stage in seen:
            raise CaseError("Repeated request during replay")
        seen.add(stage)
        if validation is not None and stage in STAGES:
            ctx = prompt["input"]
            if isinstance(ctx, list):
                ctx = ctx[0]["content"][0]["text"]
            validation(stage, raw, json.loads(ctx), entries[stage])
        return raw

    try:
        memo, detail = workflow(corpus, arm, dispatch)
    except AdditionalRequest as exc:
        return {
            "status": "new_request_required",
            "stage": str(exc),
            "matched_requests": len(seen),
        }
    except CaseError as exc:
        return {
            "status": "validation_stopped",
            "error": str(exc),
            "matched_requests": len(seen),
        }
    return {
        "status": "memo_reproduced" if memo else "withheld",
        "matched_requests": len(seen),
        "memo_sha256": fingerprint(memo) if memo else None,
        "checked_calculations": len(detail.get("calculation") or {}),
    }


def originals(corpus):
    result = {}
    with corpus.session(corpus.case.cutoff) as session:
        for source in corpus.sources:
            for h in session.read(source["url"], max_date=corpus.case.cutoff):
                key = (h.title, h.published, h.locator, h.text)
                require(key not in result, "Ambiguous source identity")
                result[key] = h
    return result


def compact_request(case, opened, ctx, raw):
    # This single diagnostic mutation measures a prospective repair, independent of historical scoring.
    draft = paired_research.ResearchMemo.model_validate_json(raw)
    target = draft.facts[0]
    require(
        target.text.count("of 58.6 billion yen") == 1,
        "Changed archived Astra diagnostic",
    )
    target.text = target.text.replace("of 58.6 billion yen", "of 59.9 billion yen")
    try:
        paired_v2.build_candidate(
            case,
            opened,
            ctx["coverage"],
            ctx["checked_calculations"],
            ctx["visual_reading"],
            draft.model_dump_json(),
        )
    except paired_v2.ParagraphIssue as issue:
        require(issue.key == "fact_1", "Mutation must fail the affected claim")
        prompt = paired_v2.repair_request(
            case,
            opened,
            draft,
            issue,
            ctx["checked_calculations"],
            ctx["visual_reading"],
        )
        return {**prompt, "model": ASTRA}
    raise CaseError("Mutated financial amount escaped validation")


def experiment(*, live_count=False):
    original = audit_event(CASE)
    require(
        json.loads((RESULTS / CASE / "results.json").read_bytes()) == original,
        "Original event changed",
    )
    row = event_row(registered(), CASE)
    variants, prompt = [], None
    with tempfile.TemporaryDirectory() as tmp:
        packet = Path(tmp) / "input"
        export_development(CASE, packet)

        def corpus():
            return PacketCorpus(packet, row["packet_sha256"])

        registry = originals(corpus())
        for arm in row["arms"]:
            record = json.loads((RESULTS / CASE / arm / "run.json").read_bytes())
            stages = []
            run = corpus()

            def judge(stage, raw, ctx, entry, *, run=run, stages=stages, arm=arm):
                nonlocal prompt
                opened = {
                    p["id"]: registry[
                        tuple(p[k] for k in ("title", "published", "locator", "text"))
                    ]
                    for p in ctx["passages"]
                }
                try:
                    if stage.startswith("forecast."):
                        paired_v2.validate_forecast(run.case, opened, raw)
                    else:
                        paired_v2.build_candidate(
                            run.case,
                            opened,
                            ctx["coverage"],
                            ctx["checked_calculations"],
                            ctx["visual_reading"],
                            raw,
                        )
                    result = {"accepted": True}
                except CaseError as exc:
                    result = {"accepted": False, "reason": str(exc)}
                stages.append(
                    {
                        "stage": stage,
                        "request_sha256": entry["request_sha256"],
                        "output_sha256": fingerprint(raw.encode()),
                        **result,
                    }
                )
                if (arm, stage) == ("single_astra", "lead.memo"):
                    prompt = compact_request(run.case, opened, ctx, raw)

            prior = trace(record, run, arm, paired_research.workflow, judge)
            require(
                prior["matched_requests"]
                == len(record["calls"]) + len(record.get("skipped_calls", {})),
                "Incomplete historical replay",
            )
            variants.append(
                {
                    "arm": arm,
                    "saved_stages": stages,
                    "revised_exact_path": trace(
                        record, corpus(), arm, paired_v2.workflow
                    ),
                }
            )

    digest = fingerprint(json_bytes(prompt))
    if live_count:
        # A count provides an exact reservation input. It never dispatches a generation.
        count = OpenAIResponses(project_key(REPO / ".env.local")).count(prompt)
        return {
            "stage": "lead.memo_repair",
            "endpoint": "POST /v1/responses/input_tokens",
            "input_tokens": count,
            "model": ASTRA,
            "prompt_sha256": digest,
            "scope": "prospective single-paragraph repair of one deliberately altered value",
        }
    count = checked_count(REVISION / "repair-count.json", "lead.memo_repair", prompt)
    base = Decimal(
        next(r["settled_usd"] for r in original["arms"] if r["arm"] == "single_astra")
    )
    left = Decimal("0.20") - base
    reservation = reserve(
        count, left, max_output_tokens=paired_v2.PATCH_OUTPUT_TOKENS, model=ASTRA
    )
    return {
        "version": paired_v2.VERSION,
        "case_id": CASE,
        "original_results_sha256": fingerprint(json_bytes(original)),
        "historical_generations": original["generation_calls"],
        "new_generations": 0,
        "arms": variants,
        "repair": {
            "original_settled_usd": str(base),
            "remaining_usd": str(left),
            "new_request_sha256": digest,
            "counted_input_tokens": count,
            "output_token_limit": paired_v2.PATCH_OUTPUT_TOKENS,
            "reserved_usd": str(reservation),
            "maximum_total_usd": str(base + reservation),
            "max_affordable_input_tokens": paired_v2.repair_input_capacity(ASTRA, left),
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--live-count", action="store_true")
    group.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    result = experiment(live_count=args.live_count)
    if args.verify:
        require(
            json.loads((REVISION / "replay.json").read_bytes()) == result,
            "Replay differs from registered observations",
        )
        print(
            "Historical requests, validation decisions and prospective repair reservation verified."
        )
    else:
        print(json_bytes(result).decode(), end="")


if __name__ == "__main__":
    main()
