"""Bind an observed Poppler timeout to its exact historical model requests and PDF page."""

import argparse
import ast
import re
import subprocess
from pathlib import Path

from scripts.paired_packet import PacketCorpus
from scripts.paired_v2 import workflow
from scripts.run_checkpoint import load
from scripts.sol_baseline import json_bytes
from scripts.team_meter import recorded_output, verify_ledger

from .confirmation import write_once
from .corpus import require
from .study import REVISION
from .terminal_audit import verify_failure


def record(folder, stderr, split):
    rec = load(folder / "run/run.json")
    require(
        rec["state"] == "failed" and rec["failure"] == "TimeoutExpired",
        "Expected the original terminal host failure",
    )
    _, owed = verify_ledger(rec, partial=True)
    require(
        not owed and not (folder / "run/memo.md").exists(),
        "Unexpected provider exposure or memo",
    )
    matches = re.findall(
        r"^subprocess\.TimeoutExpired: Command '(\[.*?\])' timed out after (\d+) seconds$",
        stderr,
        re.M,
    )
    require(bool(matches), "The actual subprocess traceback is missing")
    cmd, timeout = ast.literal_eval(matches[-1][0]), int(matches[-1][1])
    require(
        len(cmd) == 10
        and str(cmd[2]).isdigit()
        and int(cmd[2]) > 0
        and cmd
        == [
            "pdftoppm",
            "-f",
            cmd[2],
            "-l",
            cmd[2],
            "-singlefile",
            "-scale-to",
            "1400",
            "-png",
            "-",
        ]
        and timeout == 30,
        "Unexpected renderer invocation",
    )
    arm, cid = folder.name, folder.parent.name
    ticket = rec.get("assignment") or rec
    corpus = PacketCorpus(folder / "input", ticket["packet_sha256"])
    require(corpus.case.id == cid, "Case differs from traceback assignment")
    entries = {**rec["calls"], **rec.get("skipped_calls", {})}
    seen, incident = set(), {}

    def ask(stage, prompt, desired, *, keep_usd=None):
        require(stage not in seen, "Duplicate historical request")
        seen.add(stage)
        return recorded_output(entries[stage], stage, prompt, desired, keep_usd)

    def inspect_page(source, page):
        require(
            str(page) == cmd[2] and seen == set(entries),
            "The traceback is inconsistent with the frozen requests",
        )
        incident.update(
            checkpoint_id=rec["checkpoint_id"],
            exception="TimeoutExpired",
            command=cmd,
            timeout_s=timeout,
            page=int(page),
            source_sha256=source["sha256"],
            result="No memo. Replay verifies all earlier model requests and the specific original and page at the observed renderer failure.",
        )
        raise subprocess.TimeoutExpired(cmd, timeout)

    corpus.page_bytes = inspect_page
    try:
        workflow(corpus, rec.get("arm", arm), ask)
    except subprocess.TimeoutExpired:
        pass
    require(bool(incident), "The original failed call was not reached")
    verify_failure(rec, corpus, rec.get("arm", arm), workflow, incident)
    target = REVISION / "host-incidents" / split / cid / (arm + ".json")
    write_once(target, json_bytes(incident))
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("folder", type=Path)
    parser.add_argument("log", type=Path)
    parser.add_argument("--split", choices=("blind", "development"), default="blind")
    args = parser.parse_args()
    print(record(args.folder, args.log.read_text(), args.split))
