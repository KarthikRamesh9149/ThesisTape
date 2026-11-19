"""Run the separately registered paired study with durable per-assignment receipts."""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

from scripts.disney_hulu_case import CaseError, read_saved_file
from scripts.paired_packet import PacketCorpus
from scripts.paired_v2 import PATCH_OUTPUT_TOKENS, VERSION, workflow
from scripts.research_team import check_configuration, finish, new_record, reproduce
from scripts.run_checkpoint import (
    PauseRequested,
    load,
    own_run,
    pause_on_signal,
    publish,
)
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import BudgetExhausted
from scripts.team_meter import Meter, verify_ledger

from .corpus import export_development, require
from .registration import REPO, ROOT, git, registered
from .runner import TERMINAL, admission, config, record_finish_time, summary

REVISION = ROOT / "revisions/grounding_v2"
NEW_FILES = (
    "scripts/paired_grounding.py",
    "scripts/paired_v2.py",
    "evaluations/paired/study.py",
)
CAP = "0.30"


def registration(commit):
    prior = registered()
    return {
        "study": VERSION,
        "code_revision": commit,
        "parent_manifest_sha256": fingerprint(json_bytes(prior)),
        "runtime_sha256": {p: fingerprint((REPO / p).read_bytes()) for p in NEW_FILES},
        "budgets_usd": {
            "per_case_arm": CAP,
            "development_ceiling": str(68 * Decimal(CAP)),
            "blind_ceiling": str(48 * Decimal(CAP)),
        },
        "schedule": prior["schedule"],
        "policy": {
            "inherited": "All other settings and endpoints follow the parent manifest.",
            "patch_output_limit": PATCH_OUTPUT_TOKENS,
            "cap_amendment": "Equal prospective ceiling for every new arm on development and blind cases; the historical full repair needed USD 0.293626 including prior usage.",
            "development_disclosure": "The Nintendo event and its previously generated drafts were used to develop the new validator. No v1 response is reused in scored v2 assignments.",
        },
    }


def locked():
    path = REVISION / "registration.json"
    saved = load(path)
    require(
        registration(saved["code_revision"]) == saved,
        "Study settings or source differ from registration",
    )
    require(
        git("show", "HEAD:" + str(path.relative_to(REPO))) == path.read_bytes(),
        "Commit the registration before generation",
    )
    registered(for_generation=True)
    for name, expected in saved["runtime_sha256"].items():
        require(
            fingerprint(git("show", saved["code_revision"] + ":" + name)) == expected,
            "Registered revision differs from source",
        )
    return saved


def work(corpus, ticket, directory, provider=None, *, pause=None, replay=False):
    cfg = {**config(corpus, ticket), "policy_version": VERSION}
    path = directory / "run.json"
    if not path.is_file():
        require(
            not replay and not directory.exists(), "Missing or incomplete run directory"
        )
        with tempfile.TemporaryDirectory(dir=directory.parent) as tmp:
            staged = Path(tmp) / directory.name
            record = new_record(cfg, staged, Decimal(ticket["per_case_arm_usd"]))
            Meter(staged, record, provider)
            staged.rename(directory)
    record = load(path)
    check_configuration(record, cfg)
    require(
        record["budget_usd"] == cfg["per_case_arm_usd"],
        "Run allowance differs from registration",
    )

    def execute(ask):
        return workflow(corpus, ticket["arm"], ask)

    meter = Meter(directory, record, provider, continuing=True, pause=pause)
    if record["state"] in TERMINAL and not Decimal(
        record["unreconciled_reservation_usd"]
    ):
        terminal_time = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        meter.restore()
        verify_ledger(record, partial=True)
        if record["state"] in {"complete", "withheld"}:
            memo = reproduce(record, cfg, execute)
            target = directory / "memo.md"
            if target.exists() or target.is_symlink():
                require(read_saved_file(target) == memo, "Memo differs from replay")
            elif memo is not None:
                publish(target, memo)
        if "finished_at" not in record:
            record_finish_time(record, terminal_time)
            publish(path, json_bytes(record))
        return record
    require(not replay, "Cannot replay an unfinished assignment as terminal")
    try:
        meter.restore()
        record.pop("failure", None)
        record["state"] = "running"
        finish(cfg, meter, execute, "memo.md")
    except PauseRequested:
        raise
    except (ValueError, KeyError, ArithmeticError, OSError) as exc:
        record["state"] = (
            "needs_reconciliation"
            if meter.pending
            else ("budget_exhausted" if isinstance(exc, BudgetExhausted) else "failed")
        )
        record["failure"] = (
            str(exc) if isinstance(exc, CaseError) else type(exc).__name__
        )
    if record["state"] in TERMINAL:
        record_finish_time(record, datetime.now(UTC))
    meter.save()
    return record


def child():
    message = json.load(sys.stdin)
    ticket = message["ticket"]
    here = Path.cwd()
    corpus = PacketCorpus(here, ticket["packet_sha256"])
    with pause_on_signal() as pause:
        record = work(
            corpus, ticket, here.parent / "run", pause=pause, replay=message["replay"]
        )
    print(json.dumps(summary(record)))


def assignment(ticket, workspace, export, replay):
    workspace.mkdir(parents=True, exist_ok=True)
    with own_run(workspace):
        packet = workspace / "input"
        if not packet.exists():
            export(ticket["case_id"], packet)
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                "from evaluations.paired.study import child; child()",
            ],
            cwd=packet,
            env={**os.environ, "PYTHONPATH": str(REPO)},
            input=json.dumps({"ticket": ticket, "replay": replay}),
            text=True,
            stdout=subprocess.PIPE,
            check=True,
        )
        return json.loads(result.stdout)


def run_schedule(manifest, split, export, *, replay=False):
    root = REPO / "runs" / f"paired-{split}-v2"
    root.mkdir(parents=True, exist_ok=True)
    with own_run(root):
        for row in manifest["schedule"]:
            for arm in row["arms"]:
                ticket = admission(manifest, row, arm)
                result = assignment(ticket, root / row["case_id"] / arm, export, replay)
                print(json.dumps(result, sort_keys=True), flush=True)
                require(
                    result["state"] in TERMINAL
                    and Decimal(result["unreconciled_reservation_usd"]) == 0,
                    "Unsettled assignment needs reconciliation before continuing",
                )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["register", "run", "replay"])
    args = parser.parse_args()
    if args.command == "register":
        print(
            json_bytes(
                registration(git("rev-parse", "HEAD").decode().strip())
            ).decode(),
            end="",
        )
        return
    run_schedule(
        locked(), "development", export_development, replay=args.command == "replay"
    )


if __name__ == "__main__":
    main()
