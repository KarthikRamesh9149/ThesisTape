"""Replay the full masked assessment, its original source witnesses, and all grading charges offline."""

import argparse
import io
import json
import re
import zipfile
from decimal import Decimal
from threading import Lock

from pydantic import ValidationError

from scripts.research_team import check_configuration
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, generation_request, json_bytes
from scripts.sol_provider import ASTRA
from scripts.team_meter import recorded_output, verify_ledger
from scripts.team_report import typed_prompt

from . import grading
from .confirmation import REVISION, committed, write_once, zipped
from .corpus import require
from .event_audit import verify_private
from .grading_recovery import (
    OUTPUT_LIMIT,
    TOTAL_LIMIT,
    exhausted,
    verify_extended_ledger,
)
from .production import FOLDER
from .registration import REPO


def correction(payload, raw, call):
    for named_source in (True, False):
        try:
            grading.validate(raw, payload, publication_ids=named_source)
        except ValueError as exc:
            issue = (
                "Return the exact structured schema"
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            candidate = typed_prompt(
                ASTRA,
                grading.RUBRIC,
                {**payload, "grading_validation": issue, "previous_grading": raw},
                grading.Assessment,
                effort="medium",
            )
            if (
                fingerprint(
                    json_bytes(
                        generation_request(
                            candidate, max_output_tokens=call["max_output_tokens"]
                        )
                    )
                )
                == call["request_sha256"]
            ):
                return candidate
    raise ValueError("A masked correction differs from its recorded prompt")


def evaluate(payload, previous, extra):
    prompt = typed_prompt(
        ASTRA, grading.RUBRIC, payload, grading.Assessment, effort="medium"
    )
    spec = {
        "grading_request_sha256": fingerprint(json_bytes(prompt)),
        "grading_model": ASTRA,
    }
    check_configuration(previous, spec)
    cost, balance = verify_ledger(previous, partial=True)
    require(not balance, "An original grader has an unconfirmed request")
    stages = ["grade.asof"] + (
        ["grade.correct"] if "grade.correct" in previous["calls"] else []
    )
    require(set(stages) == set(previous["calls"]), "Unaccounted grading call")
    raw = None
    for stage in stages:
        call = previous["calls"][stage]
        if stage == "grade.correct":
            prompt = correction(payload, raw, call)
        raw = recorded_output(
            {**call, "output": call.get("output")}, stage, prompt, 4096
        )
    record = previous
    if extra:
        require(
            raw is None
            and len(exhausted(previous)) == 1
            and exhausted(previous)[0][0] == stages[-1],
            "An accepted assessment cannot be replaced",
        )
        continuation = {
            **spec,
            "earlier_grade_sha256": fingerprint(json_bytes(previous)),
            "continued_request_sha256": previous["calls"][stages[-1]]["request_sha256"],
            "output_limit": OUTPUT_LIMIT,
        }
        check_configuration(extra, continuation)
        extra_cost, unresolved = verify_extended_ledger(extra)
        require(
            not unresolved and Decimal(extra["budget_usd"]) == TOTAL_LIMIT - cost,
            "The combined evaluator allowance changed",
        )
        cost += extra_cost
        calls = extra["calls"]
        require(
            set(calls) in ({"grade.continue"}, {"grade.continue", "grade.correct"}),
            "Unexpected continued grader assignment",
        )
        raw = recorded_output(
            calls["grade.continue"], "grade.continue", prompt, OUTPUT_LIMIT
        )
        if "grade.correct" in calls:
            patch = correction(payload, raw, calls["grade.correct"])
            raw = recorded_output(
                calls["grade.correct"], "grade.correct", patch, OUTPUT_LIMIT
            )
        record = extra
    answer = grading.validate(raw, payload)
    require(
        record["state"] == "complete"
        and record["assessment_sha256"] == fingerprint(json_bytes(answer)),
        "Masked assessment differs from its checked original",
    )
    return answer, str(cost)


def capture(revision):
    folder = revision / "blind"
    grades = json.loads(committed(folder / "asof-scores.json"))
    identities = {"request_id": set(), "response_id": set()}
    entries = {}
    for row in grades["rows"]:
        if Decimal(row["evaluator_cost_usd"]) == 0:
            continue
        for name in ("grade", "grade-overflow"):
            directory = (
                REPO
                / "runs/study-grading-v2"
                / revision.name
                / "blind"
                / row["alias"]
                / name
            )
            saved = directory / "run.json"
            if name == "grade" or saved.is_file():
                verify_private(directory, load(saved), identities)
                entries[row["alias"] + "/" + name + ".json"] = saved.read_bytes()
    write_once(folder / "grading-receipts.zip", zipped(entries))
    result = verify(revision)
    require(
        result["verified_generations"] == len(identities["response_id"]),
        "Private generation count differs from the portable evaluation",
    )
    write_once(folder / "grading-audit.json", json_bytes(result))
    return result


def verify(revision):
    folder = revision / "blind"
    scores = committed(folder / "asof-scores.json")
    archive = (folder / "grading-receipts.zip").read_bytes()
    seen, prices, calls = set(), [], []
    lock = Lock()
    with zipfile.ZipFile(io.BytesIO(archive)) as z:
        names = set(z.namelist())
        require(
            len(names) == len(z.namelist())
            and all(
                re.fullmatch(r"[a-f0-9]{16}/grade(?:-overflow)?\.json", n)
                for n in names
            ),
            "Unexpected receipt in the evaluation archive",
        )

        def replay(payload, path):
            base = path.parent.name
            primary, overflow = base + "/grade.json", base + "/grade-overflow.json"
            previous = json.loads(z.read(primary))
            current = json.loads(z.read(overflow)) if overflow in names else None
            assessment, price = evaluate(payload, previous, current)
            receipts = {primary} | ({overflow} if current else set())
            with lock:
                require(not receipts & seen, "Repeated masked assessment")
                seen.update(receipts)
                prices.append(Decimal(price))
                calls.append(
                    len(previous["calls"]) + (len(current["calls"]) if current else 0)
                )
            return assessment, price

        grading.grade_asof("blind", revision, grader=replay)
        require(seen == names, "Unmatched evaluator records in the archive")
    result = {
        "locked_assignments": len(json.loads(scores)["rows"]),
        "paid_assessments": len(prices),
        "verified_generations": sum(calls),
        "confirmed_grading_usd": str(sum(prices)),
        "asof_scores_sha256": fingerprint(scores),
        "grading_receipts_sha256": fingerprint(archive),
    }
    proof = folder / "grading-audit.json"
    if proof.exists():
        require(
            load(proof) == result, "Grading evidence differs from its recorded audit"
        )
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=("capture", "verify"))
    p.add_argument("study", choices=("legacy", "production"))
    args = p.parse_args()
    print(
        json.dumps(
            (capture if args.action == "capture" else verify)(
                FOLDER if args.study == "production" else REVISION
            ),
            indent=2,
        )
    )
