"""Run or replay a development-only engineering trial of the revised quantitative research route."""

import argparse
import json
from pathlib import Path

from scripts.packet_archive import PacketArchive
from scripts.paired_packet import PacketCorpus, packet_digest
from scripts.quantitative_engine import audit, run
from scripts.run_checkpoint import load
from scripts.sol_provider import OpenAIResponses, project_key

from . import corpus
from .product_audit import CountJournal


def trial(case_id, directory, cap, key_file, live, profile="evidence_team_v3"):
    packet = directory / "input"
    if not packet.exists() and live:
        directory.mkdir(parents=True, exist_ok=True)
        corpus.export_development(case_id, packet)
    digest = packet_digest(
        (packet / "question.json").read_bytes(), (packet / "sources.json").read_bytes()
    )
    case = PacketCorpus(packet, digest)
    corpus.require(case.case.id == case_id, "Unexpected development packet")
    archive = PacketArchive(case)
    output = directory / "run"
    if live:
        run(
            case.case.question,
            case.case.cutoff,
            output,
            None,
            cap,
            profile=profile,
            gateway=archive,
            provider=CountJournal(
                output / "counts", OpenAIResponses(project_key(key_file))
            ),
            assignment={
                "purpose": "development engineering trial",
                "packet_sha256": digest,
            },
        )
    result = audit(output, archive)
    record = load(output / "run.json")
    return {
        "case": case_id,
        "state": result["release"],
        "eligible_searches": len(result["searches"]),
        "model_calls": len(record["calls"]),
        "estimated_usd": result["model_cost_usd"],
        "cap": record["budget_usd"],
        "pending_usd": record["unreconciled_reservation_usd"],
        "memo_sha256": result["memo_sha256"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("case_id")
    parser.add_argument("directory", type=Path)
    parser.add_argument("--cap", default="0.45")
    parser.add_argument("--key-file", type=Path, default=Path(".env.local"))
    parser.add_argument("--live", action="store_true")
    parser.add_argument(
        "--profile",
        choices=("evidence_team", "evidence_team_v2", "evidence_team_v3"),
        default="evidence_team_v3",
    )
    a = parser.parse_args()
    print(
        json.dumps(
            trial(a.case_id, a.directory, a.cap, a.key_file, a.live, a.profile),
            indent=2,
        )
    )
