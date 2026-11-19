"""Reproduce the public March 2023 monetary policy trial and its question-attribution check."""

import argparse
import json
import re
import tempfile
import zipfile
from pathlib import Path

from evaluations.financegym.runner import reviewed_answer
from evaluations.paired.confirmation import write_once, zipped
from evaluations.paired.event_audit import verify_private
from scripts import research_desk_agent
from scripts.focused_archive import VERSION, verify_transport
from scripts.open_desk_report import SavedArchive, inspect
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes

FOLDER = Path(__file__).with_suffix("")
PROFILES = ("single_astra", "mixed")
QUESTION = "As of 22 March 2023, how did the Federal Reserve change its federal funds target range, and how did banking stress affect its policy guidance and the outlook for credit?"
CUTOFF = "2023-03-22"


def check(run, profile):
    rec = load(run / "run.json")
    if (rec["question"], rec["cutoff"], rec["research_profile"], rec["budget_usd"]) != (
        QUESTION,
        CUTOFF,
        profile,
        "0.30",
    ):
        raise ValueError("The live assignment changed")
    saved = research_desk_agent.audit(run, SavedArchive(CUTOFF, VERSION))
    item, view = reviewed_answer(
        {"question": QUESTION, "cutoff": CUTOFF}, saved, (run / "memo.md").read_bytes()
    )
    desk, download = inspect(run, rec)
    if download.memo != item["report"].encode() or view["release"] != desk["state"]:
        raise ValueError("Analyst desk and submission export disagree")
    return download.memo, {
        "research_state": rec["state"],
        "publication_state": view["release"],
        "original_sha256": saved["memo_sha256"],
        "published_sha256": view["memo_sha256"],
        "settled_usd": desk["spent"],
        "reserved_usd": desk["reserved"],
        "generations": desk["call_count"],
        "publication_cost_usd": "0",
        "affected_paragraphs": view.get("attribution_review", {}).get(
            "affected_paragraphs", []
        ),
    }


def capture(local):
    ids = {"request_id": set(), "response_id": set()}
    entries, observed = {}, {}
    for profile in PROFILES:
        case = local / profile
        run = case / "research"
        verify_private(run, load(run / "run.json"), ids)
        raw, state = check(run, profile)
        observed[profile] = {
            **state,
            "retrieval": verify_transport(run, case / "operator-http"),
        }
        for p in [run / n for n in ("run.json", "memo.md", "result.json")] + sorted(
            (run / "searches").glob("*.json")
        ):
            entries[f"{profile}/{p.relative_to(run)}"] = p.read_bytes()
        write_once(FOLDER / (profile + ".md"), raw)
    bundle = zipped(entries)
    write_once(FOLDER / "runs.zip", bundle)
    write_once(
        FOLDER / "evidence.json",
        json_bytes({"runs_sha256": fingerprint(bundle), "profiles": observed}),
    )


def verify():
    proof = load(FOLDER / "evidence.json")
    bundle = FOLDER / "runs.zip"
    if fingerprint(bundle.read_bytes()) != proof["runs_sha256"]:
        raise ValueError("Original research snapshot changed")
    with tempfile.TemporaryDirectory() as tmp, zipfile.ZipFile(bundle) as archive:
        for name in archive.namelist():
            if not re.fullmatch(
                r"(?:single_astra|mixed)/(?:run\.json|memo\.md|result\.json|searches/[0-9a-f]{64}\.json)",
                name,
            ):
                raise ValueError("Unexpected entry in the portable snapshot")
            archive.extract(name, tmp)
        for profile in PROFILES:
            raw, state = check(Path(tmp) / profile, profile)
            expected = {
                k: v for k, v in proof["profiles"][profile].items() if k != "retrieval"
            }
            if state != expected or raw != (FOLDER / (profile + ".md")).read_bytes():
                raise ValueError(
                    "Published historical case differs from its verified research"
                )
    return proof


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    if args.capture:
        capture(args.capture)
    print(json.dumps(verify(), indent=2))
