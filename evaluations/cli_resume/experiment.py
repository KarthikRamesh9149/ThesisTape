"""Interrupt a real CLI run twice, or verify the portable evidence offline."""

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

from evaluations.material_dispute.experiment import verify_report
from scripts import research_team
from scripts.baseline_evidence import PER_SHARE
from scripts.disney_hulu_case import CaseError, read_saved_file
from scripts.run_checkpoint import load, load_receipt, publish, receipt_path
from scripts.sol_baseline import PROJECT_ROOT, fingerprint, json_bytes
from scripts.team_meter import Meter, verify_ledger
from scripts.team_research import case_for

HERE = Path(__file__).resolve().parent
AUTHOR = "lead.memo"
REVIEW = "review.initial"
CAP = "0.08"
PHASES = ("start", "contender", "missing-receipt", "resume", "recover", "repeat")


def observed_cli(mode, log, args):
    # Observe the transport independently of the meter. Only body hashes and
    # endpoint names leave this process. Never read headers or record prompts.
    def observe(event, values):
        if mode == "offline" and event == "socket.connect":
            raise RuntimeError("Offline verification attempted a network connection")
        if event == "urllib.Request":
            url, data = values[:2]
            operation = url.removeprefix("https://api.openai.com/v1/")
            if operation not in ("responses", "responses/input_tokens"):
                raise RuntimeError("Unexpected evaluation transport")
            entry = {
                "operation": operation,
                "sha256": fingerprint(json_bytes(json.loads(data))),
            }
            with log.open("a") as out:
                out.write(json.dumps(entry) + "\n")
                out.flush()
                os.fsync(out.fileno())
            if mode == "offline":
                raise RuntimeError("Offline verification attempted a provider request")

    sys.addaudithook(observe)
    if mode == "kill-before-settlement":
        # Cut power to the worker at an exact function boundary after the
        # genuine receipt is durable and before the ledger consumes it.
        def kill_at_settlement(frame, event, arg):
            if (
                event == "call"
                and frame.f_code is Meter.settle.__code__
                and frame.f_locals["stage"] == REVIEW
            ):
                os.kill(os.getpid(), signal.SIGKILL)

        sys.setprofile(kill_at_settlement)
    return research_team.main(args)


def child(workspace, phase, mode, args):
    log = workspace / (phase + ".jsonl")
    log.touch(exist_ok=False)
    return subprocess.Popen(
        [
            sys.executable,
            "-B",
            "-m",
            "evaluations.cli_resume.experiment",
            "_child",
            mode,
            str(log),
            *args,
        ],
        cwd=PROJECT_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def expect_exit(process, code, *, diagnostic=None):
    out, err = process.communicate(timeout=200)
    if process.returncode != code or (diagnostic is not None and diagnostic not in err):
        raise CaseError(
            f"Expected exit {code}, received {process.returncode}: {(out + err).decode()[-1200:]}"
        )
    return process.returncode


def snapshot(directory, proof, name):
    data = read_saved_file(directory / "run.json")
    require(
        not (directory / "memo.md").exists(),
        "A memo was exported before review completed",
    )
    publish(proof / name, data)
    return json.loads(data)


def digest(path):
    return fingerprint(read_saved_file(path))


def require(condition, message):
    if not condition:
        raise CaseError(message)


def live(workspace):
    workspace = workspace.resolve()
    workspace.mkdir(parents=True, exist_ok=False, mode=0o700)
    folder, proof = workspace / "run", workspace / "proof"
    proof.mkdir()
    args = ["resume", "--run-dir", str(folder)]
    exits = {}
    first = child(
        workspace,
        "start",
        "online",
        [
            "run",
            "--case",
            "twitter-price",
            "--route",
            "dispute",
            "--budget-usd",
            CAP,
            "--output-dir",
            str(folder),
        ],
    )
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline and first.poll() is None:
        if '"operation": "responses"' in (workspace / "start.jsonl").read_text():
            break
        time.sleep(0.01)
    else:
        if first.poll() is None:
            first.send_signal(signal.SIGINT)
        out, err = first.communicate(timeout=200)
        raise CaseError(
            f"The initial generation was not observed: {(out + err).decode()[-1000:]}"
        )
    snapshot(folder, proof, "in-flight.json")
    first.send_signal(signal.SIGINT)
    contender = child(workspace, "contender", "offline", args)
    exits["contender"] = expect_exit(
        contender, 1, diagnostic=b"Another process owns this run"
    )
    exits["start"] = expect_exit(first, 130)
    snapshot(folder, proof, "paused.json")

    # An offline branch of the actual in-flight snapshot has no returned receipt.
    with tempfile.TemporaryDirectory(dir=workspace) as tmp:
        unknown = Path(tmp)
        shutil.copyfile(proof / "in-flight.json", unknown / "run.json")
        attempt = child(
            workspace,
            "missing-receipt",
            "offline",
            ["resume", "--run-dir", str(unknown)],
        )
        exits["missing-receipt"] = expect_exit(
            attempt, 1, diagnostic=b"remains reserved"
        )
        snapshot(unknown, proof, "unconfirmed.json")

    second = child(workspace, "resume", "kill-before-settlement", args)
    exits["resume"] = expect_exit(second, -signal.SIGKILL)
    snapshot(folder, proof, "before-recovery.json")
    receipt = load_receipt(receipt_path(folder, REVIEW))
    before = fingerprint(json_bytes(receipt))
    exits["recover"] = expect_exit(child(workspace, "recover", "offline", args), 0)
    artifacts = ("run.json", "memo.md", "provider-ids.json")
    recovered_hashes = {name: digest(folder / name) for name in artifacts}
    exits["repeat"] = expect_exit(child(workspace, "repeat", "offline", args), 0)
    ids = load(folder / "provider-ids.json")
    final = load(folder / "run.json")
    for stage, pair in ids.items():
        require(
            final["calls"][stage]["provider"]["ids_sha256"]
            == fingerprint(json_bytes(pair)),
            "Operator ID mismatch",
        )
        require(
            all(isinstance(i, str) and i for i in pair.values()),
            "Missing provider identifier",
        )
    observations = {
        "exits": exits,
        "pending_receipt_sha256": before,
        "after_recovery": recovered_hashes,
        "after_repeat": {name: digest(folder / name) for name in artifacts},
        "response_id_hashes": {
            stage: fingerprint(pair["response_id"].encode())
            for stage, pair in ids.items()
        },
        "http_requests": {
            phase: [
                json.loads(line)
                for line in (workspace / (phase + ".jsonl")).read_text().splitlines()
            ]
            for phase in PHASES
        },
    }
    publish(proof / "observations.json", json_bytes(observations))
    for name in ("run.json", "memo.md"):
        shutil.copyfile(folder / name, proof / name)
    result = check(proof)
    publish(proof / "results.json", json_bytes(result))
    return result


def check(folder):
    case = case_for("twitter-price")
    corpus = case.corpus()
    final, memo, opened = verify_report(folder, case, "dispute", CAP, corpus)
    config, _ = research_team.saved_workflow(case, final, corpus)
    require(
        memo is not None and final["review"]["status"] == "approved",
        "The resumed memo did not pass review",
    )
    prices = [
        price
        for p in final["review"]["rounds"][-1]["draft"]["paragraphs"]
        if any(
            opened[c["passage_id"]].source_id == "twitter-board" for c in p["citations"]
        )
        for price in PER_SHARE.findall(p["text"])
    ]
    require(
        {Decimal(p) for p in prices} == {Decimal("54.20")},
        "Missing cited per-share price",
    )
    sent, pause, kill, unknown = [
        load(folder / name)
        for name in (
            "in-flight.json",
            "paused.json",
            "before-recovery.json",
            "unconfirmed.json",
        )
    ]
    wanted = [
        ["reserve", AUTHOR],
        ["settle", AUTHOR],
        ["reserve", REVIEW],
        ["settle", REVIEW],
    ]
    for r, length, state in (
        (sent, 1, "running"),
        (pause, 2, "paused"),
        (kill, 3, "running"),
        (final, 4, "complete"),
        (unknown, 1, "needs_reconciliation"),
    ):
        research_team.check_configuration(r, config)
        verify_ledger(r, partial=r is not final)
        require(
            r["events"] == wanted[:length] and r["state"] == state,
            "Unexpected state or calls at interruption",
        )
        require(
            all(
                r[k] == final[k]
                for k in ("checkpoint_id", "budget_usd", "specification_sha256")
            ),
            "Run identity changed",
        )
        require(
            all(
                r["calls"][s]["request_sha256"] == final["calls"][s]["request_sha256"]
                for s in r["calls"]
            ),
            "Request changed",
        )
    require(
        unknown["calls"] == sent["calls"]
        and unknown["unreconciled_reservation_usd"]
        == sent["unreconciled_reservation_usd"],
        "Unknown charge changed",
    )
    require(
        pause["calls"][AUTHOR] == kill["calls"][AUTHOR] == final["calls"][AUTHOR],
        "Completed author call changed",
    )
    require(
        "output" not in kill["calls"][REVIEW] and "memo_sha256" not in kill,
        "The hard kill was too late",
    )
    require(
        final["resumptions"][-1]["recovered_receipts"] == [REVIEW],
        "Reviewer output was not recovered",
    )
    obs = load(folder / "observations.json")
    require(
        obs["exits"]
        == {
            "start": 130,
            "contender": 1,
            "missing-receipt": 1,
            "resume": -9,
            "recover": 0,
            "repeat": 0,
        },
        "Process behavior changed",
    )
    require(
        set(obs["response_id_hashes"]) == {AUTHOR, REVIEW}
        and len(set(obs["response_id_hashes"].values())) == 2,
        "Expected two distinct provider responses",
    )
    require(
        obs["pending_receipt_sha256"] == final["calls"][REVIEW]["receipt_sha256"],
        "Recovered a different receipt",
    )
    require(
        obs["after_repeat"] == obs["after_recovery"]
        and all(
            obs["after_repeat"][n] == digest(folder / n)
            for n in ("run.json", "memo.md")
        ),
        "Repeat resume changed the result",
    )
    for phase in PHASES:
        requests = obs["http_requests"][phase]
        if phase in ("start", "resume"):
            stage = AUTHOR if phase == "start" else REVIEW
            require(
                [r["operation"] for r in requests]
                == ["responses/input_tokens", "responses"],
                "Unexpected provider dispatch count",
            )
            require(
                requests[1]["sha256"] == final["calls"][stage]["request_sha256"],
                "HTTP and ledger request fingerprints differ",
            )
        else:
            require(not requests, f"{phase} made a provider request")
    prior = Decimal(
        load(HERE.parent / "material_dispute/usage.json")["project_recorded_usd"]
    )
    cost = Decimal(final["estimated_model_cost_usd"])
    return {
        "case_id": case.id,
        "budget_usd": CAP,
        "completed_generations": 2,
        "duplicate_generations": 0,
        "recovered_assignment": REVIEW,
        "unconfirmed_test_reservation_usd": unknown["unreconciled_reservation_usd"],
        "reservation_at_hard_kill_usd": kill["unreconciled_reservation_usd"],
        "final_unreconciled_usd": final["unreconciled_reservation_usd"],
        "memo_sha256": final["memo_sha256"],
        "checkpoint_usd": str(cost),
        "prior_project_usd": str(prior),
        "project_recorded_usd": str(prior + cost),
        "project_cap_usd": "100",
    }


def main():
    if sys.argv[1:2] == ["_child"]:
        return observed_cli(sys.argv[2], Path(sys.argv[3]), sys.argv[4:])
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("check").add_argument(
        "--proof", type=Path, default=HERE / "proof"
    )
    commands.add_parser("live").add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "live":
        result = live(args.output_dir)
    else:
        result = check(args.proof)
        require(
            result == load(args.proof / "results.json"),
            "Saved results differ from replay",
        )
    print(json_bytes(result).decode(), end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
