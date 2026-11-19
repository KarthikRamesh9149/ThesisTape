"""Prepare the frozen paired review for a human finance analyst, and score actual completed edits."""

import argparse
import csv
import io
import json
import statistics
import zipfile
from decimal import Decimal
from pathlib import Path

from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes

from .confirmation import committed, input_zip, ordering, write_once, zipped
from .corpus import pdf_name, require
from .grading import locked_inputs
from .production import FOLDER
from .registration import ROOT

COLUMNS = (
    "report_id",
    "active_minutes",
    "substantive_corrections",
    "missing_evidence",
    "usable_as_delivered",
    "edit_notes",
)


def assignments(rows, seed):
    strata = sorted({r["stratum"] for r in rows})
    require(len(strata) == 6, "Expected six blind strata")
    result = []
    for stratum in strata:
        cases = {r["case_id"] for r in rows if r["stratum"] == stratum}
        ordered = sorted(cases, key=lambda c: ordering(seed, "blind", c))[:2]
        require(len(ordered) == 2, "Missing paired human-review cases")
        first_team = int(ordering(seed, "review", stratum)[:2], 16) % 2
        for i, case in enumerate(ordered):
            pair = sorted(
                (r for r in rows if r["case_id"] == case),
                key=lambda r: r["arm"] != "mixed",
            )
            require(
                len(pair) == 2
                and pair[0]["arm"] == "mixed"
                and pair[1]["arm"].startswith("single_"),
                "Invalid analyst-review pair",
            )
            if (i + first_team) % 2:
                pair.reverse()
            result.extend(pair)
    return result


def sheet(data=()):
    buf = io.StringIO(newline="")
    writer = csv.DictWriter(buf, fieldnames=COLUMNS)
    writer.writeheader()
    writer.writerows(data)
    return buf.getvalue().encode()


def prepare(directory):
    index, bundle = locked_inputs("blind", FOLDER)
    rows = assignments(index["rows"], load(ROOT / "protocol.json")["order_seed"])
    packet = {
        "instructions.md": (
            b"# Financial analyst correction study\n\n"
            b"Work through the 24 reports in the numbered order. For each one, use its question, cutoff and original publisher documents. "
            b"Start your timer when you begin reading. Pause it for breaks. Edit the report to the standard you would use for an investment research briefing. "
            b"Record active minutes, the number of substantive corrections, missing evidence, whether it was usable as delivered (yes or no), and a short description of your edits. "
            b"If an assignment has no released report, record whether a usable answer would need to be written from scratch. Save your edited report beside the original. "
            b"Record your name, relevant financial research qualifications, and review date in reviewer.json. The paired design repeats events; stay within the dated documents.\n"
        )
    }
    key = []
    with bundle, input_zip("blind") as sources:
        data = {r["case_id"]: r for r in json.loads(sources.read("inputs.json"))}
        for i, r in enumerate(rows, 1):
            label = f"report-{i:02d}"
            case = data[r["case_id"]]
            packet[label + "/question.md"] = (
                case["question"] + "\n\nCutoff " + case["cutoff"] + "\n"
            ).encode()
            packet[label + "/report.md"] = (
                bundle.read(r["alias"] + "/memo.md")
                if r["memo_sha256"]
                else b"No report was released for this assignment.\n"
            )
            for n, s in enumerate(case["sources"], 1):
                name = pdf_name(s)
                doc = sources.read(name)
                require(fingerprint(doc) == s["sha256"], "Dated publisher PDF changed")
                packet[label + f"/sources/{n}.pdf"] = doc
            key.append(
                {
                    "report_id": label,
                    "case_id": r["case_id"],
                    "arm": r["arm"],
                    "memo_sha256": r["memo_sha256"],
                }
            )
    packet["observations.csv"] = sheet([{"report_id": k["report_id"]} for k in key])
    packet["reviewer.json"] = json_bytes(
        {"name": "", "financial_research_qualifications": "", "review_date": ""}
    )
    raw = zipped(packet)
    directory.mkdir(parents=True, exist_ok=True)
    write_once(directory / "analyst-packet.zip", raw)
    write_once(
        directory / "operator-key.json",
        json_bytes(
            {
                "packet_sha256": fingerprint(raw),
                "reports_sha256": fingerprint(committed(FOLDER / "blind/reports.json")),
                "assignments": key,
            }
        ),
    )
    return {
        "blinded_reports": len(key),
        "paired_events": len(key) // 2,
        "packet_sha256": fingerprint(raw),
    }


def assess(root, completed):
    key = load(root / "operator-key.json")
    require(
        fingerprint((root / "analyst-packet.zip").read_bytes()) == key["packet_sha256"],
        "Changed human-review packet",
    )
    with zipfile.ZipFile(root / "analyst-packet.zip") as originals:
        initial = originals.read("observations.csv")
    reviewer = load(completed / "reviewer.json")
    require(
        all(
            isinstance(reviewer.get(k), str) and reviewer[k].strip()
            for k in ("name", "financial_research_qualifications", "review_date")
        ),
        "Supply the human analyst's identity, qualifications and review date",
    )
    raw = (completed / "observations.csv").read_bytes()
    require(raw != initial, "The observation sheet has not been completed")
    reader = csv.DictReader(io.StringIO(raw.decode("utf-8-sig")))
    items = list(reader)
    require(
        tuple(reader.fieldnames or []) == COLUMNS
        and [r["report_id"] for r in items]
        == [k["report_id"] for k in key["assignments"]],
        "Missing, duplicated or reordered analyst observation",
    )
    cases = {}
    for k, row in zip(key["assignments"], items, strict=True):
        minutes = Decimal(row["active_minutes"])
        require(
            minutes.is_finite()
            and minutes >= 0
            and row["substantive_corrections"].isdigit()
            and row["usable_as_delivered"].lower() in {"yes", "no"}
            and row["edit_notes"].strip(),
            "Incomplete analyst observation",
        )
        cases.setdefault(k["case_id"], {})[k["arm"]] = {
            "minutes": minutes,
            "usable": row["usable_as_delivered"].lower() == "yes",
            "corrections": int(row["substantive_corrections"]),
        }
    deltas = [
        p["mixed"]["minutes"] - next(v["minutes"] for k, v in p.items() if k != "mixed")
        for p in cases.values()
    ]
    return {
        "human_reviewer": reviewer,
        "packet_sha256": key["packet_sha256"],
        "observations_sha256": fingerprint(raw),
        "events": len(cases),
        "median_paired_minutes_mixed_minus_single": str(statistics.median(deltas)),
        "paired_minutes_mixed_minus_single": [str(x) for x in deltas],
        "observations": {
            cid: {
                arm: {**obs, "minutes": str(obs["minutes"])}
                for arm, obs in pair.items()
            }
            for cid, pair in cases.items()
        },
        "design": "Each analyst sees the same event twice; reading order alternates within strata.",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "assess"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--completed", type=Path)
    args = parser.parse_args()
    if args.action == "assess" and not args.completed:
        parser.error("assess requires --completed")
    print(
        json.dumps(
            prepare(args.folder)
            if args.action == "prepare"
            else assess(args.folder, args.completed),
            indent=2,
        )
    )
