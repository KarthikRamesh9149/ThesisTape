"""Prepare a public methodology note and a local email draft from a fully verified FinanceGym export."""

import argparse
import json
from collections import Counter
from datetime import date
from decimal import Decimal
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from urllib.parse import urlsplit

from evaluations.financebench.corpus import FOLDER as FINANCEBENCH
from evaluations.paired.confirmation import committed, write_once
from evaluations.paired.production import FOLDER as PRIMARY
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes

from .runner import locked, tasks, validate_file

NAMES = {
    "mixed": "Sol-led team with Luna researchers and an Astra author",
    "single_astra": "Single Astra analyst",
    "single_sol": "Single Sol analyst",
    "evidence_team_v4": "Quantitative Sol and Astra team",
}
RECIPIENT = "rujunh@google.com"


def verify_export(questions, directory):
    audits = load(directory / "audit.json")
    manifest = locked()
    raw = (directory / "answers.jsonl").read_bytes()
    summary = validate_file(questions, directory / "answers.jsonl")
    inputs = tasks(questions)
    audits_rows = audits["tasks_audited"]
    if (
        summary["answers_sha256"] != audits["answers_sha256"]
        or audits["registration_sha256"] != fingerprint(json_bytes(manifest))
        or len(audits_rows) != 400
        or audits["tasks"] != 400
        or audits["verified_provider_generations"] < 1
    ):
        raise ValueError("Supply the matching, fully verified 400-task export")
    for row, line, proof in zip(inputs, raw.splitlines(), audits_rows, strict=True):
        answer = json.loads(line)
        if (
            row["task_id"] != proof["task_id"]
            or manifest["profiles_by_task"][row["task_id"]] != proof["profile"]
            or proof["report_sha256"] != fingerprint(answer["report"].encode())
            or proof["state"] not in {"complete", "qualified"}
        ):
            raise ValueError("A submitted report differs from its signed audit")
    if sum(Decimal(r["estimated_cost_usd"]) for r in audits_rows) != Decimal(
        audits["total_research_cost_usd"]
    ):
        raise ValueError("Official cost total differs from the assignment receipts")
    return manifest, audits


def paper(questions, directory):
    cfg, audit = verify_export(questions, directory)
    primary = json.loads(committed(PRIMARY / "blind/decision.json"))
    finance = json.loads(committed(FINANCEBENCH / "results.json"))["summary"]
    counts = Counter(cfg["profiles_by_task"].values())
    methods = "\n".join(
        f"- {NAMES[p]} handled {n} tasks under a USD {cfg['case_limits_usd'][p]} per-task ceiling."
        for p, n in sorted(counts.items())
    )
    metrics = primary["metrics"]
    rows = "\n".join(
        f"| {NAMES[k]} | {v['cases']} | {v['F']:.1%} | {v['R']:.1%} | {v['O']:.1%} | {v['Q']:.1%} | {v['released']} |"
        for k, v in metrics.items()
    )
    ci = primary["quality_90pct_interval"]
    fci = finance["paired_90pct_interval"]
    arms = finance["arms"]
    note = f"""# ThesisTape

## Point-in-time financial research

ThesisTape produces a research memorandum for a question, an information cutoff and a spending limit. We evaluated it on the full 400-question public FinanceGym release. The maintainer-held rubric determines the official pre-cutoff, post-cutoff and Overall scores. [FinanceGym](https://financegym.github.io/)

## Research configuration

{methods}

The researcher plans dated searches and receives passages only after the archive verifies their availability. Web evidence comes from dated captures, immutable encyclopedia revisions or accession-specific SEC records. For an admitted archived page or filing, the gateway can reopen the original bytes, match their hash and retrieve a section relevant to a specific part of the question. Every search uses the exact cutoff of its assigned question. Discovery titles and live snippets stay outside the model context. Numeric and citation checks run before the text is released. The local analyst application exposes cited passages, checked calculations and the cost of each run.

## Independent comparisons

Before publication, ThesisTape checks whether a statement attributed to the analyst's question appears in the original wording. When the check removes a passage, it retains the original and published hashes and qualifies the report. The same check applies to all configurations and incurs no model charge.

Before the official run we locked reports on 24 historical financial events across six categories. Cases used matched documents, dates and spending limits. Graders saw anonymized reports and eligible evidence before the evaluator opened the later outcomes. Local quality weighted facts at 50%, reasoning at 25% and forecast direction at 25%. Reports with no usable answer counted in the denominator. These weights define the local experiment.

| Configuration | Cases | Facts | Reasoning | Forecast direction | Local quality | Reports released |
|---|---:|---:|---:|---:|---:|---:|
{rows}

The event-paired 90% bootstrap interval for mixed minus single local quality was {100 * ci[0]:+.1f} to {100 * ci[1]:+.1f} percentage points. A separate financial statement test sampled one published FinanceBench question per issuer for 24 issuers. The quantitative team answered {arms["evidence_team_v4"]["both"]}/24 correctly with support from cited pages. Single Astra with the same tools answered {arms["quantitative_astra"]["both"]}/24. The 90% paired interval for that difference was {100 * fci[0]:+.1f} to {100 * fci[1]:+.1f} percentage points. The grader was an independent model context checking responses against the authors' human references. The open questions may have appeared in model training. [FinanceBench](https://github.com/patronus-ai/financebench/tree/cc39aeb4afdf33909ee1412188bf89035950c2eb)

## Reproducibility and cost

The JSONL contains exactly 400 original question and cutoff pairs. Each report's dated searches, provider request fingerprints, recorded usage and final Markdown were checked offline. The submission contains {audit["qualified"]} qualified reports. Those reports restrict their conclusions where the research did not establish part of the answer. Model generation for these runs cost an estimated USD {Decimal(audit["total_research_cost_usd"]):.2f}, based on the recorded usage for {audit["verified_provider_generations"]} confirmed generations. The estimate excludes local computing, external judging and development.

| Artifact | SHA-256 |
|---|---|
| FinanceGym public question file | `{cfg["dataset"]["sha256"]}` |
| Registered candidate | `{audit["registration_sha256"]}` |
| 400 reports | `{audit["answers_sha256"]}` |

The source archive is independent of the retrieval corpus used by the benchmark authors, so coverage can differ. A later-trained model can also retain information in its parameters. We require citations for factual assertions and preserve the original retrieval records. FinanceGym is used here for noncommercial benchmark evaluation under CC BY-NC 4.0. [Public questions and license](https://huggingface.co/datasets/Rujun/FinanceGym)
"""
    # The note describes the file being offered for submission. Sending and grading are separate events.
    write_once(directory / "system-paper.md", note.encode())
    return cfg, audit


def draft(questions, directory, contact, org, published_paper, submission_date):
    cfg, audit = paper(questions, directory)
    address = Address(addr_spec=contact)
    link = urlsplit(published_paper)
    if (
        not address.username
        or not address.domain
        or "." not in address.domain
        or not org.strip()
        or len(org) > 120
        or link.scheme != "https"
        or not link.hostname
        or link.username is not None
        or any(c in (org + contact + published_paper) for c in "\r\n")
    ):
        raise ValueError(
            "Supply a contact email, organization and public HTTPS paper URL"
        )
    date.fromisoformat(submission_date)
    message = EmailMessage(policy=SMTP)
    message["From"] = str(address)
    message["To"] = RECIPIENT
    message["Subject"] = "FinanceGym submission: ThesisTape"
    message.set_content(f"""Hello FinanceGym team,

Please evaluate the attached 400 ThesisTape research reports. We used the original public questions and point-in-time cutoffs. The methodology link describes the tested system and its independent controls.

agent: ThesisTape
org: {org}
repo: {published_paper}
base_model: {", ".join(cfg["models"])}
date: {submission_date}
contact: {contact}
notes: Full 400-task run. Exact max_date on every search. Archived passage and provider usage audit completed. {audit["qualified"]} qualified reports included.

Thank you.
""")
    message.add_attachment(
        (directory / "answers.jsonl").read_bytes(),
        maintype="application",
        subtype="octet-stream",
        filename="answers.jsonl",
    )
    # Fix the MIME boundary so regenerating the same local draft is byte-for-byte reproducible.
    message.set_boundary("thesistape-" + audit["answers_sha256"][:32])
    write_once(directory / "submission.eml", message.as_bytes())


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    sp = p.add_subparsers(dest="action", required=True)
    for name in ("paper", "draft"):
        c = sp.add_parser(name)
        c.add_argument("questions", type=Path)
        c.add_argument("directory", type=Path)
        if name == "draft":
            for key in ("contact", "org", "published-paper", "submission-date"):
                c.add_argument("--" + key, required=True)
    a = p.parse_args()
    if a.action == "paper":
        paper(a.questions, a.directory)
    else:
        draft(
            a.questions,
            a.directory,
            a.contact,
            a.org,
            a.published_paper,
            a.submission_date,
        )
