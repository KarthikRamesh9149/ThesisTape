"""Reproduce the dated ECB report presented and downloaded through the public-source analyst desk."""

import json
from pathlib import Path

from scripts.desk_snapshot import saved_browser_run
from scripts.open_desk_report import inspect
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint

FOLDER = Path(__file__).with_suffix("")


def verify_browser(folder, expected_searches):
    with saved_browser_run(folder) as (root, browser):
        info, report = inspect(root, load(root / "run.json"))
        result = load(root / "result.json")
        if (
            len(result["searches"]) != expected_searches
            or any(s["max_date"] != info["cutoff"] for s in result["searches"])
            or len(report.memo) != browser["download_bytes"]
            or fingerprint(report.memo) != browser["download_sha256"]
            or info["state"] != browser["state"]
            or info["spent"] != browser["spent"]
            or info["call_count"] != browser["calls"]
            or len(report.passages) != browser["passages"]
            or report.passages[0].citation_url != browser["first_source"]
        ):
            raise ValueError("The reproduced report differs from the browser's output")
    return browser


def verify():
    return verify_browser(FOLDER, 3)


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
