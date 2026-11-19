"""Reopen the Toyota source, replay both provider receipts and grade the released reports offline."""

import argparse
import json
import re
import subprocess
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.disney_hulu_case import CaseError, read_saved_file
from scripts.research_team import replay
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.team_research import case_for
from scripts.visual_reader import ambiguity, open_pages

HERE = Path(__file__).resolve().parent


def load(path):
    return json.loads(read_saved_file(path))


def verify_source(case, corpus, rules, *, render=False):
    if rules["cutoff"] != case.cutoff:
        raise CaseError("The case cutoff differs from the frozen protocol")
    with corpus.session(case.cutoff) as session:
        pages = open_pages(session, case.table)
        if pages is None:
            raise CaseError("Could not reopen both Toyota passages")
        png = session.page_image(pages.table, max_date=case.cutoff)
    if (
        pages.table.sha256 != rules["pdf_sha256"]
        or png is None
        or fingerprint(png) != rules["page29_png_sha256"]
        or "435.6149.9" not in pages.table.text
        or ambiguity(pages.table.text, case.table.row_anchor)
        != "adjacent numeric cells joined in extraction"
        or ambiguity(pages.table.text, "[actual purchase]") is not None
    ):
        raise CaseError(
            "The frozen source, extraction collision or clear adjacent row changed"
        )
    if render:
        pdf = case.archive_objects / (rules["pdf_sha256"] + ".pdf")
        with TemporaryDirectory() as tmp:
            out = Path(tmp) / "page"
            subprocess.run(
                [
                    "pdftoppm",
                    "-f",
                    "29",
                    "-l",
                    "29",
                    "-scale-to",
                    "2500",
                    "-png",
                    "-singlefile",
                    "-hide-annotations",
                    str(pdf),
                    str(out),
                ],
                check=True,
            )
            if out.with_suffix(".png").read_bytes() != png:
                raise CaseError(
                    "Rerender differs from the registered PNG. The frozen renderer is Poppler 26.05.0"
                )
    return pages


def label(value):
    return " ".join(re.findall(r"[a-z0-9]+", (value or "").casefold()))


def grade(record, memo, pages, rules):
    expected = rules["criteria"]
    if memo is None:
        return {"criteria": dict.fromkeys(expected, False), "points": 0, "pass": False}
    cell = record["review"]["rounds"][-1]["candidate"]
    body = memo.decode()
    row = label(cell["row"])
    group = label(cell["column_group"]).replace(" ", "")
    column = label(cell["column"])
    note_ids = set(cell["note_ids"])
    checks = {
        "value": cell["value"] == expected["value"]["expected"],
        "row": row in {label(s) for s in expected["row"]["labels"]},
        "column": group == expected["column"]["group"].lower()
        and column in (label(expected["column"]["label"]), "12 months 21 4 22 3"),
        "unit": label(cell["unit"]) in {label(s) for s in expected["unit"]["labels"]},
    }
    note_rules = {k: v["note"] for k, v in expected.items() if "note" in v}
    for key, n in note_rules.items():
        citation = f"[{pages.notes.locator}]({pages.notes.citation_url})"
        checks[key] = (
            n in note_ids and f"**Note {n}.** {pages.definitions[n]} {citation}" in body
        )
    passed = (
        all(checks.values())
        and note_ids == set(note_rules.values())
        and "(" + pages.table.citation_url + ")" in body
    )
    return {"criteria": checks, "points": sum(checks.values()), "pass": passed}


def replay_artifact(root, case, corpus, pages):
    record = load(root / "run.json")
    memo = replay(case, record, corpus=corpus)
    if record.get("evidence") != pages.record():
        raise CaseError("Report did not use the frozen dated passages")
    path = root / "memo.md"
    if (memo is None) != (not path.exists()) or (
        memo is not None and read_saved_file(path) != memo
    ):
        raise CaseError("Released artifact differs from the replay")
    if memo is not None:
        links = re.findall(r"\]\((https://[^\s)]+)\)", memo.decode())
        with corpus.session(case.cutoff) as session:
            for url in links:
                if session.read(url, max_date=case.cutoff) not in (
                    (pages.table,),
                    (pages.notes,),
                ):
                    raise CaseError(
                        "A rendered citation failed to reopen its original dated passage"
                    )
    return record, memo


def compare(directory, case, corpus, rules, pages):
    arms = {}
    for arm in ("table-text", "visual"):
        root = directory / arm
        record, memo = replay_artifact(root, case, corpus, pages)
        if record["route"] != arm or Decimal(record["budget_usd"]) != Decimal(
            rules["budget_usd_per_arm"]
        ):
            raise CaseError("Route or spending limit differs from the frozen pair")
        visual = record["visual_reader"]
        wanted = rules["page29_png_sha256"] if arm == "visual" else None
        if visual["image_sha256"] != wanted:
            raise CaseError("Unexpected evidence modality in the paired trial")
        calls = record["calls"]
        arms[arm] = {
            "state": record["state"],
            **grade(record, memo, pages, rules),
            "model_generations": len(calls),
            "elapsed_ms": record["elapsed_ms"],
            "estimated_usd": record["estimated_model_cost_usd"],
            "stages_usd": {k: v["estimated_model_cost_usd"] for k, v in calls.items()},
            "unreconciled_usd": record["unreconciled_reservation_usd"],
        }
    control, visual = (
        Decimal(arms[arm]["estimated_usd"]) for arm in ("table-text", "visual")
    )
    return {
        "case_id": case.id,
        "budget_usd_per_arm": rules["budget_usd_per_arm"],
        "arms": arms,
        "incremental_visual_route_usd": str(visual - control),
        "pair_usd": str(control + visual),
    }


def checkpoint(pair, routing_dir, corpus, rules, pages):
    case = case_for("toyota-buybacks-actual")
    control = load(case.archive.parent / "evaluator/clear-row.json")
    record, memo = replay_artifact(routing_dir, case, corpus, pages)
    if (
        record["cutoff"] != control["cutoff"]
        or record["route"] != "visual"
        or Decimal(record["budget_usd"]) != Decimal(control["budget_usd"])
    ):
        raise CaseError("Routing check differs from its frozen case or budget")
    expected = {k: v for k, v in rules["criteria"].items() if k != "basis"}
    expected.update(
        value={"expected": control["value"]},
        row={"labels": control["row_labels"]},
        exclusions={"note": control["notes"][0]},
    )
    result = grade(record, memo, pages, {"criteria": expected})
    calls = record["calls"]
    skipped = (
        not record["visual_reader"]["requested"]
        and record["visual_reader"]["image_sha256"] is None
        and not any(k.startswith("visual.") for k in calls)
    )
    prior = Decimal(
        load(HERE.parent / "forecast_specialist/usage.json")["project_recorded_usd"]
    )
    total = Decimal(pair["pair_usd"]) + Decimal(record["estimated_model_cost_usd"])
    return {
        "pair_usd": pair["pair_usd"],
        "clear_row": {
            **result,
            "pass": result["pass"] and skipped,
            "skipped_image": skipped,
            "model_generations": len(calls),
            "estimated_usd": record["estimated_model_cost_usd"],
            "unreconciled_usd": record["unreconciled_reservation_usd"],
            "elapsed_ms": record["elapsed_ms"],
        },
        "checkpoint_usd": str(total),
        "prior_project_usd": str(prior),
        "project_recorded_usd": str(prior + total),
        "project_cap_usd": "100",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-only", action="store_true")
    parser.add_argument(
        "--render",
        action="store_true",
        help="Independently regenerate the registered PNG with Poppler",
    )
    parser.add_argument("--pair-dir", type=Path, default=HERE / "pair")
    parser.add_argument(
        "--routing-dir", type=Path, help="Also replay a separate clear-row control"
    )
    args = parser.parse_args()
    case = case_for("toyota-buybacks")
    rules = load(case.archive.parent / "evaluator/protocol.json")
    corpus = case.corpus()
    pages = verify_source(case, corpus, rules, render=args.render)
    if args.source_only:
        print(
            "Verified the dated table, page image, notes, merged numeric cells and clear adjacent row."
        )
        return
    results = compare(args.pair_dir, case, corpus, rules, pages)
    saved = args.pair_dir / "results.json"
    if saved.exists() and load(saved) != results:
        raise CaseError("Published results differ from the recomputed scores or costs")
    routing_dir = args.routing_dir or (
        HERE / "clear-row" if args.pair_dir == HERE / "pair" else None
    )
    if routing_dir is not None:
        usage = checkpoint(results, routing_dir, corpus, rules, pages)
        if routing_dir == HERE / "clear-row" and load(HERE / "usage.json") != usage:
            raise CaseError(
                "Saved checkpoint costs or clear-row result differ from replay"
            )
        results = {"pair": results, "checkpoint": usage}
    print(json_bytes(results).decode(), end="")


if __name__ == "__main__":
    main()
