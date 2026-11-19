"""Read a verified public-archive investment memo and its original cited passages for the analyst desk."""

from decimal import Decimal
from types import SimpleNamespace

from . import research_desk_agent as engine
from .desk_report import Release, activity
from .disney_hulu_case import CaseError, read_saved_file
from .research_team import check_configuration
from .team_meter import verify_ledger
from .temporal_web import ArchiveGateway, checked_doc, cutoff_day
from .verified_arithmetic import formula

COLLECTION = "historical-web"
LABEL = "Public historical sources"
TITLE = "Investment research memo"
FINISHED = {"complete", "qualified", "withheld", "insufficient_evidence"}


class SavedArchive:
    VERSION = ArchiveGateway.VERSION
    check_source = staticmethod(checked_doc)

    def __init__(self, cutoff):
        self.cutoff = cutoff

    def search(self, *args, **kwargs):
        raise CaseError("Viewing a saved memo requires its original search records")


def intake(question, cutoff):
    cutoff_day(cutoff)
    if (
        not isinstance(question, str)
        or not 10 <= len(question.strip()) <= 2000
        or any(ord(c) < 32 and c not in "\t\r\n" for c in question)
    ):
        raise CaseError("Enter a research question of 10 to 2,000 characters")
    return question.strip(), cutoff


def is_public(record):
    return (
        record.get("workflow") == engine.VERSION
        and record.get("archive_version") == ArchiveGateway.VERSION
    )


def release(directory, result):
    if not result["memo_sha256"]:
        return None
    memo = read_saved_file(directory / "memo.md")
    blocks, passages, refs = [], [], {}

    def citation(pid):
        if pid not in refs:
            p = result["evidence"][pid]
            proof = p["proof"]
            locator = {
                "wayback": "Archived capture " + str(proof.get("capture", "")),
                "wikimedia_revision": "Revision " + str(proof.get("revision", "")),
                "sec_filing": "EDGAR accession " + str(proof.get("accession", "")),
                "sec_xbrl": "EDGAR accession " + str(proof.get("accession", "")),
            }[p["kind"]]
            passages.append(
                SimpleNamespace(
                    title=p["title"],
                    locator=locator,
                    text=p["text"],
                    published=p["available"],
                    edition=p["available"],
                    citation_url=p["url"],
                    sha256=proof["original_sha256"],
                )
            )
            refs[pid] = len(passages)
        return {"ref": refs[pid], "label": str(refs[pid])}

    def paragraph(text, pids):
        tokens = [text]
        for pid in dict.fromkeys(pids):
            tokens.extend([" ", citation(pid)])
        blocks.append({"kind": "paragraph", "tokens": tokens})

    for section in engine.SECTIONS:
        group = [p for p in result["paragraphs"] if p["section"] == section]
        if group:
            blocks.append({"kind": "heading", "tokens": [section]})
            for p in group:
                paragraph(p["text"], p["pids"])
    required = {c for p in result["paragraphs"] for c in p["calculation_ids"]}
    if required:
        blocks.append({"kind": "heading", "tokens": ["Checked calculations"]})
        calculations = result["calculations"]
        for i, c in reversed(calculations.items()):
            if i in required:
                required.update(x for x in (c["left"], c["right"]) if x in calculations)
        for i, c in calculations.items():
            if i in required:
                paragraph(formula(i, c), c["evidence_ids"])
    return Release(memo, blocks, passages)


def inspect(directory, record):
    archive = SavedArchive(record["cutoff"])
    selected = engine
    if record["research_profile"] in {
        "evidence_team",
        "evidence_team_v2",
        "evidence_team_v3",
    }:
        from . import quantitative_engine as selected
    cfg = selected.configuration(
        record["question"],
        record["cutoff"],
        Decimal(record["budget_usd"]),
        archive,
        record["research_profile"],
        record["assignment"],
    )
    check_configuration(record, cfg)
    spent, held = verify_ledger(record, partial=True)
    status = record["state"]
    result = (
        selected.audit(directory, archive, record=record) if status in FINISHED else {}
    )
    report = release(directory, result) if result else None
    decisions = []
    if result:
        decisions.extend(
            {"label": "Dated source search", "detail": r["query"]}
            for r in result["searches"]
        )
        if status == "qualified":
            last = result.get("reviews", [])[-1]
            decisions.extend(
                {"label": "Coverage limitation", "detail": msg}
                for msg in last["missing"]
            )
    return {
        "title": TITLE,
        "question": record["question"],
        "cutoff": record["cutoff"],
        "state": status,
        "route": "Public archives · "
        + {
            "mixed": "research team",
            "evidence_team": "research team with quantitative review",
            "evidence_team_v2": "research team with quantitative review",
            "evidence_team_v3": "research team with quantitative review",
            "single_astra": "single Astra",
            "single_sol": "single Sol",
        }[record["research_profile"]],
        "budget": record["budget_usd"],
        "spent": str(spent),
        "reserved": str(sum(held.values(), Decimal(0))),
        "call_count": len(record["calls"]),
        "activity": activity(record),
        "decisions": decisions,
        "coverage": "qualify" if status == "qualified" else None,
        "review": None,
        "failure": record.get("failure"),
        "report": report.blocks if report else None,
        "sources": report.sources() if report else [],
    }, report
