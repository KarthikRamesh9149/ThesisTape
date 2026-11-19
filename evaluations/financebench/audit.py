"""Reopen the frozen issuer packets and independently verify every trial's provider receipts."""

import io
import json
import zipfile
from decimal import Decimal
from threading import Lock

from evaluations.paired.confirmation import committed, write_once
from evaluations.paired.event_audit import verify_private
from scripts import quantitative_engine
from scripts.disney_hulu_case import CaseError
from scripts.packet_archive import PacketArchive, SavedSearches
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import reserve
from scripts.team_meter import funded_limit, recorded_output, verify_ledger

from .study import FOLDER, ROOT, locked, packet, ticket


def failed(directory, rec, gateway):
    calls = {**rec["calls"], **rec.get("skipped_calls", {})}
    seen, lock = set(), Lock()
    spent, unresolved = verify_ledger(rec, partial=True)
    if unresolved or (directory / "memo.md").exists():
        raise ValueError("Unexpected reserved funds or an unpublished memo")

    def ask(stage, prompt, desired, *, keep_usd=None):
        with lock:
            if stage in calls:
                if stage in seen:
                    raise ValueError("Repeated historical call")
                seen.add(stage)
                call = calls[stage]
                if "provider" in call and "output" not in call:
                    recorded_output(
                        {**call, "output": None}, stage, prompt, desired, keep_usd
                    )
                    if rec["failure"] != "The provider response did not complete":
                        raise ValueError("Different provider failure")
                    raise CaseError(rec["failure"])
                return recorded_output(call, stage, prompt, desired, keep_usd)
            key = fingerprint(json_bytes(prompt))
            count = load(directory / "counts" / (key + ".json"))
            if count["prompt_sha256"] != key:
                raise ValueError("Original preflight differs")
            available = Decimal(rec["budget_usd"]) - spent - (keep_usd or 0)
            cap = funded_limit(
                stage, prompt["model"], count["tokens"], available, desired
            )
            reserve(
                count["tokens"], available, max_output_tokens=cap, model=prompt["model"]
            )
            raise ValueError("An allegedly refused request is affordable")

    try:
        quantitative_engine.research(
            rec["question"],
            rec["cutoff"],
            SavedSearches(gateway, directory / "searches", offline=True),
            ask,
            profile=rec["research_profile"],
        )
    except CaseError as exc:
        if str(exc) != rec["failure"] or seen != set(calls):
            raise ValueError("Original failure or prior requests differ") from exc
    else:
        raise ValueError("Failed assignment produced a result")


def verify():
    spec = locked()
    index = json.loads(committed(FOLDER / "reports.json"))
    blob = committed(FOLDER / "reports.zip")
    if index["archive_sha256"] != fingerprint(blob) or index[
        "registration_sha256"
    ] != fingerprint(json_bytes(spec)):
        raise ValueError("The sealed cohort changed")
    data = {r["case_id"]: r for r in spec["cases"]}
    identities = {"request_id": set(), "response_id": set()}
    settled = Decimal(0)
    counts = {
        "cases": len(data),
        "assignments": 0,
        "released": 0,
        "failed_reproduced": 0,
        "dated_searches": 0,
        "provider_generations": 0,
    }
    if len({(r["case_id"], r["arm"]) for r in index["rows"]}) != 48:
        raise ValueError("Expected 48 distinct cohort assignments")
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        for row in index["rows"]:
            cid, arm = row["case_id"], row["arm"]
            folder = ROOT / cid / arm
            record = load(folder / "run.json")
            manifest = data[cid]
            raw = archive.read(cid + "/" + arm + "/run.json")
            if (
                raw != json_bytes(record)
                or fingerprint(raw) != row["record_sha256"]
                or record["assignment"] != ticket(spec, manifest, arm)
            ):
                raise ValueError("Cohort identity or private/public record differs")
            verify_private(folder, record, identities)
            paid, held = verify_ledger(record, partial=True)
            if paid != Decimal(row["settled_usd"]) or held:
                raise ValueError("Unverified project spending")
            settled += paid
            corpus = packet(manifest)
            gateway = PacketArchive(corpus)
            if row["state"] == "failed":
                failed(folder, record, gateway)
                counts["failed_reproduced"] += 1
            else:
                result = quantitative_engine.audit(folder, gateway, record=record)
                counts["released"] += int(bool(result["memo_sha256"]))
            for path in (folder / "searches").glob("*.json"):
                search = load(path)["body"]
                if search["request"]["max_date"] != spec["cutoff"] or any(
                    s["proof"]["original_sha256"] != manifest["pdf_sha256"]
                    for s in search["sources"]
                ):
                    raise ValueError(
                        "A source outside the original dated packet reached a research worker"
                    )
                counts["dated_searches"] += 1
            counts["assignments"] += 1
    counts.update(
        provider_generations=len(identities["response_id"]),
        confirmed_model_cost_usd=str(settled),
        unconfirmed_usd="0",
        cutoff_violations=0,
        archive_sha256=fingerprint(blob),
    )
    write_once(FOLDER / "audit.json", json_bytes(counts))
    return counts


if __name__ == "__main__":
    print(json.dumps(verify(), indent=2))
