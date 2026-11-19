"""Compare the historical desk export to an offline release using both verified reviewer decisions."""

import json
from pathlib import Path

from scripts import quantitative_engine
from scripts.open_desk_report import SavedArchive, inspect
from scripts.packet_archive import SavedSearches
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint
from scripts.team_meter import recorded_output

from .ecb import saved_browser_run

FOLDER = Path(__file__).with_suffix("")


def compare(root, browser):
    record = load(root / "run.json")
    info, report = inspect(root, record)
    if (
        info["state"],
        info["spent"],
        info["reserved"],
        info["call_count"],
        len(report.passages),
        len(report.memo),
        report.passages[0].citation_url,
        fingerprint(report.memo),
    ) != (
        browser["state"],
        browser["spent"],
        browser["reserved"],
        browser["calls"],
        browser["passages"],
        browser["download_bytes"],
        browser["first_source"],
        browser["download_sha256"],
    ):
        raise ValueError("The browser export and original verified memo disagree")
    seen = set()

    def reuse(stage, prompt, limit, *, keep_usd=None):
        if stage in seen:
            raise ValueError("A saved generation would be used twice")
        seen.add(stage)
        return recorded_output(record["calls"][stage], stage, prompt, limit, keep_usd)

    raw, revised = quantitative_engine.research(
        record["question"],
        record["cutoff"],
        SavedSearches(SavedArchive(record["cutoff"]), root / "searches", offline=True),
        reuse,
        profile="evidence_team_v4",
    )
    if seen != set(record["calls"]) or not revised["reviews"][-1]["selected"]:
        raise ValueError(
            "An original request changed or the corrected draft was discarded"
        )
    return raw, {
        "original_memo_sha256": fingerprint(report.memo),
        "revised_memo_sha256": fingerprint(raw),
        "state": revised["release"],
        "matching_generation_receipts": len(seen),
        "additional_generation_cost_usd": "0",
        "reviews": revised["reviews"],
    }


def verify():
    with saved_browser_run(FOLDER) as (root, browser):
        raw, result = compare(root, browser)
        if raw != (FOLDER / "revised.md").read_bytes() or result != load(
            FOLDER / "comparison.json"
        ):
            raise ValueError("Revised memo or comparison changed")
        if (root / "memo.md").read_bytes() != (
            FOLDER / "browser-export.md"
        ).read_bytes():
            raise ValueError("The published original differs from the browser export")
        return result


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
