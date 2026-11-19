"""Paired evaluation of the production reviewer on Toyota's published forecast."""

import argparse
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from pathlib import Path

from scripts.claim_review import review_candidate
from scripts.disney_hulu_case import SAVED_CASE, CaseError, read_saved_file
from scripts.research_team import configuration, execute, replay, reproduce
from scripts.retrieval_gateway import Corpus, Evidence
from scripts.sol_baseline import PROJECT_ROOT, fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key
from scripts.team_report import render_candidate
from scripts.team_research import case_for

PROOF = Path(__file__).resolve().parent
RUBRIC = SAVED_CASE.parent / "toyota-outlook-2023-05-11/evaluator/scoring.json"
METRIC = re.compile(r"\boperating(?=\s+(?:income|profit)\b)", re.IGNORECASE)


def change_metric(candidate, case, opened, action):
    draft = candidate.draft.model_dump()
    for i, paragraph in enumerate(draft["paragraphs"], 1):
        text, n = METRIC.subn(
            lambda m: "Net" if m[0][0].isupper() else "net", paragraph["text"], count=1
        )
        if n:
            paragraph["text"] = text
            return f"paragraph_{i}", render_candidate(
                case, opened, action, json_bytes(draft)
            )
    raise CaseError(
        "The original draft has no eligible metric label for the prespecified change"
    )


def from_control(directory):
    case, corpus = case_for("toyota"), Corpus()
    raw = read_saved_file(directory / "run.json")
    record = json.loads(raw)
    if record["route"] != "reviewed" or record.get("state") != "complete":
        raise CaseError("Expected a completed production review run")
    if replay(case, record, corpus=corpus) != read_saved_file(directory / "memo.md"):
        raise CaseError("The control memo differs from its replay")
    opened = {pid: Evidence(**h) for pid, h in record["evidence"].items()}
    action = record["coverage"]
    first = record["review"]["rounds"][0]
    original = render_candidate(case, opened, action, json_bytes(first["draft"]))
    if any(first[k] != v for k, v in original.version().items()):
        raise CaseError("The first candidate differs from its review record")
    affected, changed = change_metric(original, case, opened, action)
    config = {
        **configuration(case, corpus, "reviewed"),
        "route": "review-challenge",
        "control_run_sha256": fingerprint(raw),
        "scoring_sha256": fingerprint(read_saved_file(RUBRIC)),
        "original_draft_sha256": first["draft_sha256"],
        "affected_claim": affected,
    }

    def workflow(ask):
        memo, calculation, review = review_candidate(
            case, corpus, opened, action, changed, ask
        )
        return memo, {"review": review, "calculation": calculation}

    return config, workflow, record


def describes_forecast(draft):
    paragraphs = [p["text"].lower() for p in draft["paragraphs"]]
    amount = r"(?:\b3,000,000\s+million\s+(?:yen|jpy)\b|(?:\bjpy\s*|¥\s*)3,000,000\s+million\b)"
    outlook = r"\b(?:forecast\w*|outlook|guidance|expect\w*|project\w*|anticipat\w*|estimat\w*)\b"
    return any(
        METRIC.search(sentence)
        and re.search(amount, sentence)
        and re.search(outlook, text)
        and "2024" in text
        for text in paragraphs
        for sentence in re.split(r"(?<=[.!?])\s+", text)
    )


def score(control, changed):
    rubric = json.loads(read_saved_file(RUBRIC))
    with Corpus().session(control["cutoff"]) as gateway:
        hits = gateway.read(
            rubric["source_url"], locator=rubric["locator"], max_date=gateway.cutoff
        )
    if (
        len(hits) != 1
        or hits[0].sha256 != rubric["source_sha256"]
        or not all(a in hits[0].text for a in rubric["anchors"])
    ):
        raise CaseError("The evaluator's forecast table failed source verification")
    positive = control["review"]["rounds"]
    rounds = changed["review"]["rounds"]
    first = rounds[0]
    target = changed["affected_claim"]
    finding = next(
        f for f in first["assessment"]["findings"] if f["claim_id"] == target
    )
    passage_ids = {pid for pid, h in control["evidence"].items() if h == vars(hits[0])}
    reason = finding["reason"].lower()
    checks = {
        "accept_supported_original": (
            len(positive) == 1
            and positive[0]["approved"]
            and describes_forecast(positive[0]["draft"])
        ),
        "identify_wrong_metric_in_cited_table": (
            not first["approved"]
            and finding["verdict"] in ("contradicted", "insufficient")
            and bool(passage_ids.intersection(finding["evidence_ids"]))
            and "operating" in reason
            and "net" in reason
        ),
        "correct_and_recheck_exact_released_memo": (
            len(rounds) == 2
            and rounds[1]["approved"]
            and describes_forecast(rounds[1]["draft"])
            and first["memo_sha256"]
            != rounds[1]["memo_sha256"]
            == changed["memo_sha256"]
            and changed["review"]["released_memo_sha256"] == changed["memo_sha256"]
            and set(changed["calls"])
            == {"review.initial", "lead.revise", "review.recheck"}
        ),
    }
    return checks


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "check"))
    parser.add_argument("--control-dir", type=Path, default=PROOF / "control")
    parser.add_argument("--challenge-dir", type=Path, default=PROOF / "challenge")
    parser.add_argument("--budget-usd", type=Decimal, default=Decimal("0.06"))
    args = parser.parse_args(argv)
    try:
        config, workflow, control = from_control(args.control_dir)
        if args.command == "run":
            provider = OpenAIResponses(project_key(PROJECT_ROOT / ".env.local"))
            execute(config, args.challenge_dir, args.budget_usd, provider, workflow)
        changed = json.loads(read_saved_file(args.challenge_dir / "run.json"))
        memo = reproduce(changed, config, workflow)
        path = args.challenge_dir / "memo.md"
        if (memo is None and path.exists()) or (
            memo is not None and read_saved_file(path) != memo
        ):
            raise CaseError("Challenge release differs from replay")
        checks = score(control, changed)
        print(json.dumps(checks, indent=2))
        return 0 if all(checks.values()) else 1
    except (ValueError, OSError, KeyError, InvalidOperation) as error:
        print(f"Evaluation error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
