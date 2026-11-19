"""Run registered questions in small waves and give a rate-limited public archive time to recover."""

import argparse
import json
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from itertools import islice
from pathlib import Path

from evaluations.paired.corpus import require
from scripts.run_checkpoint import load, own_run

from . import runner


def transport_status(folders):
    counts = Counter()
    for folder in folders:
        for p in (folder / "operator-http").glob("*.json"):
            receipt = load(p)["receipt"]
            counts[str(receipt.get("status", "transport_error"))] += 1
    return counts


def next_wave(workers, maximum, previous_throttled, status):
    total = sum(status.values())
    throttled = status["429"]
    if not throttled:
        return min(workers + 1, maximum), 0, False
    if 10 * throttled >= total:
        return max(1, workers - 1) if previous_throttled else workers, 60, True
    return workers, 20, True


def run(questions, maximum=3, *, root=None, sleep=time.sleep):
    require(1 <= maximum <= 4, "Choose between one and four concurrent investigations")
    root = root or runner.RESEARCH
    root.mkdir(parents=True, exist_ok=True)
    cfg = runner.locked()
    with own_run(root), ThreadPoolExecutor(max_workers=maximum) as pool:

        def pending(row):
            f = root / row["task_id"] / "research/run.json"
            return not f.is_file() or load(f)["state"] not in runner.FINAL | {
                "needs_reconciliation"
            }

        rows = iter(r for r in runner.tasks(questions) if pending(r))
        width, previous = maximum, False
        while group := list(islice(rows, width)):
            states = list(pool.map(lambda r: runner.capture_one(root, r, cfg), group))
            for state in states:
                print(json.dumps(state), flush=True)
            require(
                not any(
                    s["state"] == "needs_reconciliation" or s.get("provider_failure")
                    for s in states
                ),
                "A model service interruption stopped new cases after the current group settled",
            )
            status = transport_status(root / r["task_id"] for r in group)
            width, delay, previous = next_wave(width, maximum, previous, status)
            print(
                json.dumps(
                    {
                        "completed_group": [r["task_id"] for r in group],
                        "archive_http": dict(status),
                        "next_concurrency": width,
                        "cooldown_seconds": delay,
                    }
                ),
                flush=True,
            )
            if delay:
                sleep(delay)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("questions", type=Path)
    p.add_argument("--workers", type=int, default=3, choices=range(1, 5))
    args = p.parse_args()
    run(args.questions, args.workers)
