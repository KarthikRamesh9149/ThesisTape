"""Continue the same masked assessment when the provider attests that its original output limit was exhausted."""

import json
import subprocess
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from pydantic import ValidationError

from scripts import sol_provider
from scripts.disney_hulu_case import CaseError
from scripts.research_team import check_configuration, new_record
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint, generation_request, json_bytes
from scripts.sol_provider import ASTRA
from scripts.team_meter import Meter, verify_ledger
from scripts.team_report import typed_prompt

from . import grading
from .corpus import require
from .event_audit import verify_private
from .production import FOLDER

TOTAL_LIMIT = Decimal("1.00")
OUTPUT_LIMIT = 8192


def exhausted(record):
    return [
        (stage, call)
        for stage, call in record["calls"].items()
        if call.get("provider", {}).get("status") == "incomplete"
        and call["provider"].get("incomplete_details", {}).get("reason")
        == "max_output_tokens"
        and call["provider"]["usage"]["output_tokens"] == call["max_output_tokens"]
        and "output" not in call
    ]


def grade_one(payload, path, provider=None):
    original = path / "run.json"
    if not original.exists() or not exhausted(load(original)):
        try:
            return grading.grade_one(payload, path, provider)
        except CaseError:
            if not original.exists() or not exhausted(load(original)):
                raise
    require(
        provider is None,
        "The isolated live grader uses the project's registered provider",
    )
    response = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "evaluations.paired.grading_recovery",
            "continue",
            str(path),
        ],
        input=json_bytes(payload),
        stdout=subprocess.PIPE,
        check=True,
    )
    return json.loads(response.stdout)


def verify_extended_ledger(record, *, partial=False):
    response = subprocess.run(
        [
            sys.executable,
            "-B",
            "-m",
            "evaluations.paired.grading_recovery",
            "account",
            "partial" if partial else "complete",
        ],
        input=json_bytes(record),
        stdout=subprocess.PIPE,
        check=True,
    )
    amount, held = json.loads(response.stdout)
    return Decimal(amount), {s: Decimal(v) for s, v in held.items()}


def continue_grade(payload, path):
    original = path / "run.json"
    with own_run(path.parent):
        old = load(original)
        prompt = typed_prompt(
            ASTRA, grading.RUBRIC, payload, grading.Assessment, effort="medium"
        )
        config = {
            "grading_request_sha256": fingerprint(json_bytes(prompt)),
            "grading_model": ASTRA,
        }
        check_configuration(old, config)
        previous, unknown = verify_ledger(old, partial=True)
        require(
            not unknown and len(exhausted(old)) == 1,
            "Only a confirmed truncated assessment can be continued",
        )
        identities = {"request_id": set(), "response_id": set()}
        verify_private(path, old, identities)
        stage, truncated = exhausted(old)[0]
        if stage == "grade.correct":
            earlier = old["calls"]["grade.asof"]["output"]
            try:
                grading.validate(earlier, payload)
            except ValueError as exc:
                issue = (
                    "Return the exact structured schema"
                    if isinstance(exc, ValidationError)
                    else str(exc)
                )
                request = typed_prompt(
                    ASTRA,
                    grading.RUBRIC,
                    {
                        **payload,
                        "grading_validation": issue,
                        "previous_grading": earlier,
                    },
                    grading.Assessment,
                    effort="medium",
                )
            else:
                raise ValueError(
                    "A complete valid assessment is ineligible for continuation"
                )
        else:
            require(stage == "grade.asof", "Unexpected incomplete assignment")
            request = prompt
        original_request = generation_request(
            request, max_output_tokens=truncated["max_output_tokens"]
        )
        require(
            fingerprint(json_bytes(original_request)) == truncated["request_sha256"]
            and OUTPUT_LIMIT > truncated["max_output_tokens"],
            "The continued grading request differs from its original or adds no output space",
        )
        directory = path.parent / "grade-overflow"
        budget = TOTAL_LIMIT - previous
        continuation = {
            **config,
            "earlier_grade_sha256": fingerprint(original.read_bytes()),
            "continued_request_sha256": truncated["request_sha256"],
            "output_limit": OUTPUT_LIMIT,
        }
        if not directory.exists():
            with tempfile.TemporaryDirectory(dir=path.parent) as t:
                staged = Path(t) / "new"
                record = new_record(continuation, staged, budget)
                Meter(staged, record, None)
                staged.rename(directory)
        record = load(directory / "run.json")
        check_configuration(record, continuation)
        require(
            Decimal(record["budget_usd"]) == budget,
            "Cumulative grading ceiling changed",
        )
        meter = Meter(directory, record, None, continuing=True)
        meter.restore()
        raw = meter("grade.continue", request, OUTPUT_LIMIT)
        try:
            assessment = grading.validate(raw, payload)
        except ValueError as exc:
            issue = (
                "Return the exact structured schema"
                if isinstance(exc, ValidationError)
                else str(exc)
            )
            patch = typed_prompt(
                ASTRA,
                grading.RUBRIC,
                {**payload, "grading_validation": issue, "previous_grading": raw},
                grading.Assessment,
                effort="medium",
            )
            assessment = grading.validate(
                meter("grade.correct", patch, OUTPUT_LIMIT), payload
            )
        extra, pending = verify_ledger(record)
        require(
            not pending
            and meter.seen == set(record["calls"])
            and previous + extra <= TOTAL_LIMIT,
            "Continuation has an unresolved charge or exceeds its combined ceiling",
        )
        record.update(
            state="complete", assessment_sha256=fingerprint(json_bytes(assessment))
        )
        meter.save()
        verify_private(directory, record, identities)
        return assessment, str(previous + extra)


if __name__ == "__main__":
    if len(sys.argv) == 1:
        grading.grade_asof("blind", FOLDER, grader=grade_one)
        print(json.dumps({"split": "blind", "graded": 48}))
    else:
        role, value = sys.argv[1:]
        require(role in {"account", "continue"}, "Unknown isolated grading operation")
        # Only this short-lived evaluator process uses the extended output ceiling.
        # Every research process retains the frozen 4,096-token limit and original tariffs.
        sol_provider.MAX_OUTPUT_TOKENS = OUTPUT_LIMIT
        material = json.load(sys.stdin)
        if role == "account":
            require(
                material["grading_model"] == ASTRA
                and material["output_limit"] == OUTPUT_LIMIT
                and "earlier_grade_sha256" in material
                and Decimal(material["budget_usd"]) <= TOTAL_LIMIT,
                "Expected a registered extended grading ledger",
            )
            paid, held = verify_ledger(material, partial=value == "partial")
            print(json.dumps([str(paid), {k: str(v) for k, v in held.items()}]))
        else:
            print(json.dumps(continue_grade(material, Path(value))))
