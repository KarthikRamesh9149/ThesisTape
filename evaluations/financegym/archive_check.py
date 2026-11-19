"""Prove a dated search from original HTTP responses, independently of model generation."""

import argparse
import json
from pathlib import Path

from scripts.archive_receipts import RecordedHttp, check_retrieval
from scripts.focused_archive import FocusedArchive, verify_transport
from scripts.packet_archive import SavedSearches
from scripts.run_checkpoint import publish
from scripts.sol_baseline import json_bytes
from scripts.temporal_web import ArchiveGateway

CUTOFF = "2024-06-07"
QUERY = "European Central Bank monetary policy decisions 6 June 2024 key ECB interest rates deposit facility"


def check(root, live, *, focused=False):
    root = Path(root)
    run, receipts = root / "case", root / "operator-http"
    if live:
        run.mkdir(parents=True, exist_ok=True)
        gateway = (
            FocusedArchive(
                CUTOFF, root / "cache", question=QUERY, http=RecordedHttp(receipts)
            )
            if focused
            else ArchiveGateway(CUTOFF, root / "cache", http=RecordedHttp(receipts))
        )
        manifest = (
            {"cutoff": CUTOFF, "question": QUERY, "archive_version": gateway.VERSION}
            if focused
            else {"cutoff": CUTOFF}
        )
        if (run / "run.json").exists() and (
            run / "run.json"
        ).read_bytes() != json_bytes(manifest):
            raise ValueError(
                "Use a separate folder for a different source-check configuration"
            )
        publish(run / "run.json", json_bytes(manifest))
        SavedSearches(gateway, run / "searches").search(
            QUERY,
            max_date=CUTOFF,
            titles=["European Central Bank"],
            filing_terms="",
            limit=5,
        )
    if (
        focused
        and json.loads((run / "run.json").read_bytes()).get("archive_version")
        != FocusedArchive.VERSION
    ):
        raise ValueError("The source audit was produced by a different archive")
    result = (
        verify_transport(run, receipts) if focused else check_retrieval(run, receipts)
    )
    if not result["eligible_sources"]:
        raise ValueError("The dated source test returned no eligible source")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--focused", action="store_true")
    args = parser.parse_args()
    print(json.dumps(check(args.directory, args.live, focused=args.focused), indent=2))
