"""Project a research checkpoint into analyst-visible activity, report and citations."""

import json
import re
from dataclasses import dataclass

from .disney_hulu_case import CaseError, read_saved_file
from .research_team import check_configuration, replay, saved_case, saved_workflow
from .team_meter import verify_ledger

TOKENS = re.compile(r"\[([^\]\n]+)\]\((https://[^\s)]+)\)|\*\*([^*\n]+)\*\*")
ROUTE_LABELS = {
    "reviewed": "Independent review",
    "team": "Research team",
    "single": "Single analyst",
    "dispute": "Dispute review",
    "forecast": "Forecast review",
    "visual": "Visual review",
    "table-text": "Table review",
}
ROLES = {
    "lead": "Lead analyst",
    "filings": "Filings researcher",
    "contrary": "Contrary evidence",
    "review": "Independent reviewer",
    "dispute": "Dispute specialist",
    "forecast": "Forecast specialist",
    "visual": "Visual specialist",
}
ACTIONS = {
    "write": "Write the memo",
    "qualify": "Qualify the conclusion",
    "check_filings": "Recheck filings",
    "check_contrary": "Recheck contrary evidence",
}


def activity(record):
    settled = {stage for action, stage in record["events"] if action == "settle"}
    rows = []
    for action, stage in record["events"]:
        if action == "settle":
            continue
        skip = action == "skip"
        call = (record["skipped_calls"] if skip else record["calls"])[stage]
        parts = stage.removeprefix("followup.").split(".")
        state = "Skipped" if skip else "In flight"
        if stage in settled:
            state = (
                "Completed"
                if call.get("provider", {}).get("status") == "completed"
                and "output" in call
                else "Unusable response"
            )
        elif not skip and record["state"] in ("failed", "needs_reconciliation"):
            state = "Unconfirmed"
        rows.append(
            {
                "stage": stage,
                "role": ROLES.get(parts[0], parts[0]),
                "model": call["model"],
                "state": state,
                "cost": call.get("estimated_model_cost_usd"),
                "reserved": call.get("reservation_usd"),
                "elapsed_ms": call.get("provider", {}).get("elapsed_ms"),
            }
        )
    return rows


def routing(record):
    decisions = []
    for event, stage in record["events"]:
        if event != "settle" or not stage.startswith("lead.decide."):
            continue
        try:
            data = json.loads(record["calls"][stage].get("output", ""))
        except (ValueError, TypeError):
            continue
        if isinstance(data, dict) and data.get("action") in ACTIONS:
            decisions.append(
                {
                    "label": ACTIONS[data["action"]],
                    # The lead's working instruction may contain a claim that the
                    # independent reviewer later corrects. Show only the routed action.
                    "detail": "After the initial worker reports."
                    if stage == "lead.decide.1"
                    else "After the follow-up search.",
                }
            )
    dispute = record.get("dispute") or {}
    if dispute and dispute.get("status") != "control":
        status = dispute["status"]
        decisions.append(
            {
                "label": "Consult Astra"
                if status == "adjudicated"
                else "Dispute review",
                "detail": {
                    "adjudicated": "The reviewer identified a material disagreement. The specialist examined the dated originals.",
                    "none": "The reviewer found no material disagreement on the question.",
                    "reconciled": "The primary evidence reconciled the disagreement.",
                    "insufficient_budget": "The remaining budget was reserved for review.",
                }.get(status, status.replace("_", " ").capitalize()),
            }
        )
    visual = record.get("visual_reader")
    if visual:
        decisions.append(
            {
                "label": "Read the page image"
                if visual["requested"]
                else "Use the text table",
                "detail": visual.get("reason") or "",
            }
        )
    review = record.get("review") or {}
    if record["state"] == "complete" and review.get("status") == "approved":
        decisions.append(
            {
                "label": "Release reviewed memo",
                "detail": "Independent review approved the revision."
                if len(review["rounds"]) > 1
                else "Independent review approved the first draft.",
            }
        )
    return decisions


@dataclass
class Release:
    memo: bytes
    blocks: list
    passages: list

    def sources(self):
        return [
            {"number": n, "title": h.title, "locator": h.locator}
            for n, h in enumerate(self.passages, 1)
        ]


def released_report(directory, case, record, corpus):
    memo = replay(case, record, corpus=corpus)
    path = directory / "memo.md"
    if (path.exists() or path.is_symlink()) and read_saved_file(path) != memo:
        raise CaseError("The saved memo differs from the verified CLI report")
    if memo is None:
        return None
    header = f"# {case.title}\n\n**As of {case.cutoff}**\n\n{case.question}\n\n"
    raw = memo.decode()
    if not raw.startswith(header):
        raise CaseError("The report header differs from the saved intake")
    passages = []
    links = {}
    blocks = []
    with corpus.session(case.cutoff) as session:

        def tokens(line):
            result, start = [], 0
            for match in TOKENS.finditer(line):
                if match.start() > start:
                    result.append(line[start : match.start()])
                label, url, strong = match.groups()
                if strong is not None:
                    result.append({"strong": strong})
                else:
                    if url not in links:
                        hits = session.read(url, max_date=case.cutoff)
                        if not hits:
                            raise CaseError(
                                "A report citation could not be reopened at its cutoff"
                            )
                        if "#" in url:
                            if len(hits) != 1 or hits[0].citation_url != url:
                                raise CaseError(
                                    "The citation did not identify one original passage"
                                )
                            passages.append(hits[0])
                            links[url] = {"ref": len(passages)}
                        else:
                            links[url] = {"url": hits[0].url}
                    result.append({**links[url], "label": label})
                start = match.end()
            if start < len(line):
                result.append(line[start:])
            return result

        for line in raw[len(header) :].splitlines():
            if not line.strip():
                continue
            if line.startswith("|") and line.endswith("|"):
                cells = [s.strip() for s in line[1:-1].split("|")]
                if all(re.fullmatch(r":?-{3,}:?", c) for c in cells):
                    continue
                if not blocks or blocks[-1]["kind"] != "table":
                    blocks.append({"kind": "table", "rows": []})
                blocks[-1]["rows"].append([tokens(cell) for cell in cells])
                continue
            kind = (
                "heading"
                if line.startswith("## ")
                else "bullet"
                if line.startswith("- ")
                else "paragraph"
            )
            content = (
                line.removeprefix("## ")
                if kind == "heading"
                else line.removeprefix("- ")
                if kind == "bullet"
                else line
            )
            blocks.append({"kind": kind, "tokens": tokens(content)})
    return Release(memo, blocks, passages)


def inspect_record(directory, record):
    case = saved_case(record)
    corpus = case.corpus()
    config, _ = saved_workflow(case, record, corpus)
    check_configuration(record, config)
    terminal = record["state"] in ("complete", "withheld")
    report = released_report(directory, case, record, corpus) if terminal else None
    if not terminal:
        verify_ledger(record, partial=True)
    info = {
        "title": case.title,
        "question": case.question,
        "cutoff": case.cutoff,
        "state": record["state"],
        "route": ROUTE_LABELS[record["route"]],
        "budget": record["budget_usd"],
        "spent": record["estimated_model_cost_usd"],
        "reserved": record["unreconciled_reservation_usd"],
        "call_count": len(record["calls"]),
        "activity": activity(record),
        "decisions": routing(record),
        "coverage": record.get("coverage"),
        "review": (record.get("review") or {}).get("status"),
        "failure": record.get("failure"),
        "report": report.blocks if report else None,
        "sources": report.sources() if report else [],
    }
    return info, report
