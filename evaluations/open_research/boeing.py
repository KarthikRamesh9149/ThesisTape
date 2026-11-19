"""Recheck the exact assembled Boeing pilot memo while reusing only identical historical requests."""

import argparse
import json
import tempfile
from decimal import Decimal
from pathlib import Path

from scripts import research_desk_agent as agent
from scripts.disney_hulu_case import CaseError
from scripts.research_team import new_record
from scripts.run_checkpoint import load, own_run
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_meter import Meter, recorded_output, verify_ledger
from scripts.temporal_web import checked_doc

ROOT = Path(__file__).resolve().parents[2]
PUBLIC = Path(__file__).resolve().parent / "boeing"
LOCAL = ROOT / "runs/open-research/boeing-spirit-pilot"
PATCH = ROOT / "runs/open-research/boeing-spirit-assembly"
SOURCE_COMMIT = "204a4c3"


class ReplayGateway:
    def __init__(self, searches):
        self.searches = searches

    def search(self, q, *, max_date, titles, filing_terms, limit):
        matches = [
            s
            for s in self.searches
            if s["request"]
            == {
                "version": "dated-web-v1",
                "query": q,
                "max_date": max_date,
                "titles": titles,
                "filing_terms": filing_terms,
                "limit": limit,
            }
        ]
        if len(matches) != 1:
            raise CaseError("Unknown or ambiguous archived search")
        for source in matches[0]["sources"]:
            checked_doc(source, max_date)
        return matches[0]


def save_pilot():
    from evaluations.paired.confirmation import write_once, zipped

    rec = load(LOCAL / "run.json")
    result = load(LOCAL / "result.json")
    verify_ledger(rec)
    if fingerprint(json_bytes(result)) != rec["result_sha256"]:
        raise CaseError("Pilot record changed")
    saved = {}
    for f in (ROOT / "runs/dated-web-transport/boeing").glob("*.json"):
        e = load(f)
        if e["sha256"] != fingerprint(json_bytes(e["body"])):
            raise CaseError("Search envelope changed")
        saved[e["sha256"]] = e["body"]
    search = [saved[s["response_sha256"]] for s in result["searches"]]
    write_once(
        PUBLIC / "pilot.zip",
        zipped(
            {
                "run.json": json_bytes(rec),
                "result.json": json_bytes(result),
                "memo.md": (LOCAL / "memo.md").read_bytes(),
                "searches.json": json_bytes(search),
            }
        ),
    )


def compare(*, live):
    import zipfile

    with zipfile.ZipFile(PUBLIC / "pilot.zip") as z:
        old = json.loads(z.read("run.json"))
        baseline = json.loads(z.read("result.json"))
        gateway = ReplayGateway(json.loads(z.read("searches.json")))
        if (
            fingerprint(z.read("memo.md")) != baseline["memo_sha256"]
            or fingerprint(z.read("result.json")) != old["result_sha256"]
        ):
            raise CaseError("Pilot artifact hash mismatch")
    historical, _ = verify_ledger(old)
    PATCH.parent.mkdir(parents=True, exist_ok=True)
    if live:
        if not PATCH.is_dir():
            with tempfile.TemporaryDirectory(dir=PATCH.parent) as tmp:
                staged = Path(tmp) / PATCH.name
                rec = new_record(
                    {
                        "role": "assembled memo review",
                        "source_checkpoint": old["checkpoint_id"],
                    },
                    staged,
                    Decimal("0.12"),
                )
                Meter(staged, rec, None)
                staged.rename(PATCH)
        private = load(PATCH / "run.json")
        meter = Meter(PATCH, private, None, continuing=True)
        meter.restore()
    else:
        private = load(PUBLIC / "review.json")
    seen = set()

    def dispatch(stage, prompt, limit, *, keep_usd=None):
        if stage in seen:
            raise CaseError("Duplicate stage in the pilot")
        seen.add(stage)
        if stage == "review.recheck":
            return (
                meter(stage, prompt, limit, keep_usd=keep_usd)
                if live
                else recorded_output(
                    private["calls"][stage], stage, prompt, limit, keep_usd
                )
            )
        return recorded_output(old["calls"][stage], stage, prompt, limit, keep_usd)

    memo, result = agent.research(old["question"], old["cutoff"], gateway, dispatch)
    if seen != set(old["calls"]) or not memo:
        raise CaseError("Incomplete revised pilot")
    extra, pending = verify_ledger(private)
    if pending:
        raise CaseError("Pending review charge")
    artifact = {
        "original_code_revision": SOURCE_COMMIT,
        "policy": agent.VERSION,
        "first_memo_sha256": baseline["memo_sha256"],
        "original_charge_usd": str(historical),
        "matching_historical_generations": len(seen) - 1,
        "new_generations": 1,
        "incremental_usd": str(extra),
        "release": result["release"],
        "reviews": result["reviews"],
        "admitted_source_editions": len(
            {v["url"] for v in result["evidence"].values()}
        ),
        "checked_searches": len(result["searches"]),
        "memo_sha256": fingerprint(memo),
        "research_run_estimate_usd": str(
            historical
            - Decimal(old["calls"]["review.recheck"]["estimated_model_cost_usd"])
            + extra
        ),
    }
    if live:
        from evaluations.paired.confirmation import write_once

        private.update(state="complete", memo_sha256=fingerprint(memo))
        meter.save()
        write_once(PUBLIC / "review.json", json_bytes(private))
        write_once(PUBLIC / "reviewed.md", memo)
        write_once(PUBLIC / "recheck.json", json_bytes(artifact))
    elif (
        artifact != load(PUBLIC / "recheck.json")
        or (PUBLIC / "reviewed.md").read_bytes() != memo
    ):
        raise CaseError("The revised report changed")
    return artifact


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=["capture", "recheck", "verify"])
    args = parser.parse_args()
    if args.phase == "capture":
        save_pilot()
        return
    if args.phase == "recheck":
        folder = PATCH.parent / ".boeing-recheck-lock"
        folder.mkdir(parents=True, exist_ok=True)
        with own_run(folder):
            result = compare(live=True)
    else:
        result = compare(live=False)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
