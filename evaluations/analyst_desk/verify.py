"""Verify the saved browser trial and CLI memo offline. Optionally match private receipts."""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from scripts.desk_report import inspect_record
from scripts.disney_hulu_case import CaseError, read_saved_file
from scripts.run_checkpoint import load, load_receipt, receipt_path
from scripts.sol_baseline import PROJECT_ROOT, fingerprint, json_bytes

PROOF = Path(__file__).resolve().parent / "proof"
PRIOR = PROJECT_ROOT / "evaluations/cli_resume/proof/results.json"
RUBRIC = PROJECT_ROOT / "cases/tradeweb-expenses-2023-10-26/evaluator/scoring.json"


def require(condition, message):
    if not condition:
        raise CaseError(message)


def verify(operator=None):
    record = load(PROOF / "run.json")
    observed = load(PROOF / "browser.json")
    prior = load(PRIOR)
    info, release = inspect_record(PROOF, record)
    require(
        release and info["state"] == "complete" and info["review"] == "approved",
        "The memo did not pass review",
    )
    require(
        record["run_id"] == observed["id"] and record["collection"] == "archive",
        "Different input or run",
    )
    require(
        record["question"] == observed["question"]
        and record["cutoff"] == observed["cutoff"] == "2023-10-27T00:15:00Z",
        "Different question or cutoff",
    )
    require(Decimal(info["budget"]) == Decimal("0.10"), "Different spending limit")
    path = "/api/runs/" + record["run_id"]
    require(
        observed["writes"]
        == [["/api/runs", 202], [path + "/pause", 202], [path + "/resume", 202]],
        "Different browser actions",
    )
    require(
        {"running", "paused", "complete"} <= set(observed["observed_states"])
        and observed["saw_reservation"],
        "Missing live state observations",
    )
    require(
        len(record["resumptions"]) == 1
        and record["resumptions"][0]["already_settled"]
        == observed["settled_at_pause"]
        == ["lead.plan"],
        "The completed plan was not reused",
    )
    stages = set(record["calls"])
    require(
        len(stages) == observed["calls"]
        and {c["stage"] for c in info["activity"] if c["state"] == "Completed"}
        == stages,
        "Unsettled calls",
    )
    require(
        len({c["provider"]["ids_sha256"] for c in record["calls"].values()})
        == len(stages),
        "Repeated provider identifiers",
    )
    require(
        any(d["label"] == "Write the memo" for d in info["decisions"]),
        "Missing lead decision",
    )
    require(
        info["reserved"] == "0" and info["spent"] == observed["spend_usd"],
        "Cost or reservation mismatch",
    )
    require(Decimal(info["spent"]) <= Decimal(info["budget"]), "Run exceeded its limit")
    memo = read_saved_file(PROOF / "memo.md")
    exported = {"bytes": len(memo), "sha256": fingerprint(memo)}
    require(
        release.memo == memo
        and observed["exported"] == exported
        and observed["unchanged_after_inspection"],
        "Browser export differs from CLI replay",
    )
    rubric = load(RUBRIC)
    sources = {p.locator: p for p in release.passages}
    require(
        {c["section"] for c in rubric["criteria"]} <= sources.keys(),
        "Required filing passages are missing",
    )
    selected = next(
        (
            p
            for p in release.passages
            if p.citation_url == observed["visible_source_url"]
        ),
        None,
    )
    require(
        selected
        and selected.url == rubric["source"]["url"]
        and "695 million" in selected.text
        and "659 million" in selected.text,
        "Wrong original passage for the corrected amounts",
    )
    if operator is not None:
        require(
            record == load(operator / "run.json"), "Operator run differs from the proof"
        )
        ids = load(operator / "provider-ids.json")
        require(
            set(ids) == stages
            and len(list((operator / "receipts").glob("*.json"))) == len(stages),
            "Unexpected private receipt count",
        )
        require(
            len({pair["response_id"] for pair in ids.values()}) == len(stages),
            "Repeated response ID",
        )
        for stage, call in record["calls"].items():
            receipt = load_receipt(receipt_path(operator, stage))
            require(
                receipt["ids"] == ids[stage]
                and all(ids[stage].values())
                and fingerprint(json_bytes(ids[stage]))
                == call["provider"]["ids_sha256"]
                and fingerprint(json_bytes(receipt)) == call["receipt_sha256"]
                and receipt["request_sha256"] == call["request_sha256"],
                "Private receipt does not match the recorded assignment",
            )
        contents = b"\n".join(p.read_bytes() for p in PROOF.iterdir() if p.is_file())
        require(
            all(
                value.encode() not in contents
                for pair in ids.values()
                for value in pair.values()
            ),
            "Private provider identifier in portable proof",
        )
    cumulative = Decimal(prior["project_recorded_usd"]) + Decimal(info["spent"])
    return {
        "approved": True,
        "completed_calls": len(stages),
        "resumed_after": observed["settled_at_pause"],
        "reopened_passages": len(sources),
        "browser_and_cli_export": exported,
        "estimated_run_usd": info["spent"],
        "unreconciled_usd": info["reserved"],
        "estimated_project_usd": str(cumulative),
        "project_cap_usd": prior["project_cap_usd"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operator-run",
        type=Path,
        help="Check the private local provider IDs and receipts as well",
    )
    args = parser.parse_args()
    print(json.dumps(verify(args.operator_run), indent=2))


if __name__ == "__main__":
    main()
