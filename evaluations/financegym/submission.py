"""Prepare a public methodology note and a local email draft from a fully verified FinanceGym export."""

import argparse
import json
import math
from collections import Counter
from datetime import date
from decimal import Decimal
from email.headerregistry import Address
from email.message import EmailMessage
from email.policy import SMTP
from pathlib import Path
from statistics import mean
from urllib.parse import urlsplit

from evaluations.financebench.corpus import FOLDER as FINANCEBENCH
from evaluations.paired.analysis import nearest
from evaluations.paired.confirmation import committed, write_once
from evaluations.paired.production import FOLDER as PRIMARY
from scripts.run_checkpoint import load
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import ASTRA, LUNA, MODEL

from . import (
    budget_publication,
    budget_routes,
    draft_review,
    editorial,
    recovery,
    reviewed_release,
    titled_publication,
)
from .runner import locked, tasks, validate_file

NAMES = {
    "mixed": "Sol-led team with Luna researchers and an Astra author",
    "single_astra": "Single Astra analyst",
    "single_sol": "Single Sol analyst",
    "evidence_team_v4": "Quantitative Sol and Astra team",
}
RECIPIENT = "rujunh@google.com"


def verified_selection(
    manifest,
    proof,
    extension,
    publication=None,
    funding=None,
    title_registration=None,
    budget_registration=None,
    selected_recovery=None,
):
    profile = manifest["profiles_by_task"][proof["task_id"]]
    if selected_recovery != proof.get("independent_budget_route") or (
        selected_recovery is not None and len(proof.get("attempts", [])) != 2
    ):
        return False
    derived = proof.get("reviewed_publication")
    edit = proof.get("editorial")
    lookup = proof.get("title_lookup")
    review = proof.get("primary_draft_review")
    if extension is None:
        return (
            proof["profile"] == profile
            and "attempts" not in proof
            and derived is None
            and edit is None
            and lookup is None
            and review is None
        )
    chain = proof["attempts"]
    if len(chain) not in (1, 2) or chain[0]["profile"] != profile:
        return False
    if review is not None and not (
        budget_registration
        and review["registration_sha256"] == budget_registration
        and review["profile"] == draft_review.PROFILE
        and chain[0]["state"] == "failed"
        and chain[0]["report_sha256"] is None
        and review["state"]
        in {"qualified", "withheld", "failed", "needs_reconciliation"}
        and ((review["state"] == "qualified") == bool(review["report_sha256"]))
        and review["transport_requests"] == 0
        and review["provider_generations"] in (0, 1)
        and Decimal(review["estimated_cost_usd"]) >= 0
        and Decimal(review["outstanding_usd"]) >= 0
        and Decimal(review["estimated_cost_usd"]) + Decimal(review["outstanding_usd"])
        <= draft_review.CAP
    ):
        return False
    if lookup is not None and not (
        title_registration
        and funding
        and edit is not None
        and len(chain) == 2
        and all(a["eligible_sources"] == 0 for a in chain)
        and lookup["registration_sha256"] == title_registration
        and lookup["model_generations"] == 0
        and Decimal(lookup["model_cost_usd"]) == 0
        and lookup["elapsed_s"] >= 0
        and lookup["searches"] > 0
        and lookup["transport_requests"] > 0
        and lookup["eligible_sources"] > 0
    ):
        return False
    if len(chain) == 2 and (
        chain[0]["report_sha256"] is not None
        or chain[0]["state"] in {"qualified", "complete"}
        or chain[-1]["profile"]
        != budget_routes.route(manifest, profile, selected_recovery)[1]
    ):
        return False
    costs = chain
    final_profile = chain[-1]["profile"]
    if review is not None and review["state"] == "qualified":
        if not (
            len(chain) == 1
            and edit is None
            and derived is None
            and lookup is None
            and review["provider_generations"] == 1
            and Decimal(review["outstanding_usd"]) == 0
            and proof["state"] == "qualified"
            and proof["report_sha256"] == review["report_sha256"]
        ):
            return False
        final_profile = draft_review.PROFILE
    elif edit is not None:
        if not (
            funding
            and publication
            and derived is None
            and len(chain) == 2
            and chain[-1]["report_sha256"] is None
            and chain[-1]["state"] not in {"qualified", "complete"}
            and edit["profile"] == editorial.engine.PROFILE
            and edit["state"] == proof["state"] == "qualified"
            and edit["report_sha256"] == proof["report_sha256"]
            and edit["transport_requests"] == 0
            and 0 < Decimal(edit["estimated_cost_usd"]) <= editorial.engine.CAP
            and Decimal(edit["outstanding_usd"]) == 0
            and edit["provider_generations"] >= 3
        ):
            return False
        costs = [*chain, edit]
        final_profile = edit["profile"]
    elif derived is not None:
        if not (
            publication
            and len(chain) == 2
            and chain[-1]["report_sha256"] is None
            and chain[-1]["state"] in {"failed", "needs_reconciliation"}
            and chain[-1]["profile"] == "evidence_team_v4"
            and derived["method"] == "independently_reviewed_first_draft"
            and derived["source_run_sha256"] == chain[-1]["original_run_sha256"]
            and derived["additional_model_generations"] == 0
            and Decimal(derived["additional_model_cost_usd"]) == 0
            and 0 < derived["published_paragraphs"] <= derived["approved_paragraphs"]
            and derived["report_sha256"] == proof["report_sha256"]
            and proof["state"] == "qualified"
        ):
            return False
    elif (
        chain[-1]["report_sha256"] != proof["report_sha256"]
        or chain[-1]["state"] != proof["state"]
    ):
        return False
    if review is not None:
        costs = [*costs, review]
    return (
        proof["primary_profile"] == profile
        and proof["profile"] == final_profile
        and Decimal(proof["estimated_cost_usd"])
        == sum(Decimal(a["estimated_cost_usd"]) for a in costs)
        and Decimal(proof["outstanding_usd"])
        == sum(Decimal(a["outstanding_usd"]) for a in costs)
    )


def verify_export(questions, directory):
    audits = load(directory / "audit.json")
    manifest = locked()
    raw = (directory / "answers.jsonl").read_bytes()
    summary = validate_file(questions, directory / "answers.jsonl")
    inputs = tasks(questions)
    audits_rows = audits["tasks_audited"]
    allocation = (
        budget_routes.validate(load(directory / budget_routes.FILENAME))
        if (directory / budget_routes.FILENAME).is_file()
        else None
    )
    if allocation:
        budget_routes.verify_accounts(allocation, manifest, audits_rows)
    extension = audits.get("recovery_registration_sha256")
    publication = audits.get("reviewed_release_registration_sha256")
    funding = audits.get("editorial_registration_sha256")
    title_registration = audits.get("title_registration_sha256")
    budget_registration = audits.get("budget_review_registration_sha256")
    if extension is not None:
        verified = recovery.register(manifest)
        if json.loads(
            committed(recovery.REGISTRATION)
        ) != verified or extension != fingerprint(json_bytes(verified)):
            raise ValueError(
                "The recovery research differs from its registered procedure"
            )
        recovered = sum(len(a["attempts"]) == 2 for a in audits_rows)
        retained = sum("reviewed_publication" in a for a in audits_rows)
        editors = [a["editorial"] for a in audits_rows if "editorial" in a]
        reviews = [
            a["primary_draft_review"]
            for a in audits_rows
            if "primary_draft_review" in a
        ]
        approved_reviews = sum(a["state"] == "qualified" for a in reviews)
        if budget_registration is not None and (
            not title_registration
            or fingerprint(json_bytes(budget_publication.locked(title_registration)))
            != budget_registration
            or len(reviews) != audits["primary_draft_review_attempts"]
            or approved_reviews != audits["primary_draft_review_releases"]
            or sum(Decimal(r["estimated_cost_usd"]) for r in reviews)
            != Decimal(audits["primary_draft_review_cost_usd"])
        ):
            raise ValueError(
                "Budget-limited review counts, registration or cost changed"
            )
        if funding is not None:
            funded = editorial.locked(manifest, verified)
            if title_registration is not None and (
                title_registration != titled_publication.locked(funded)
                or sum("title_lookup" in a for a in audits_rows)
                != audits["title_lookup_releases"]
            ):
                raise ValueError(
                    "Source-restored report count or specification differs"
                )
            if (
                publication != funded["reviewed_release_sha256"]
                or funding != fingerprint(json_bytes(funded))
                or len(editors) != audits["editorial_releases"]
                or sum(Decimal(e["estimated_cost_usd"]) for e in editors)
                != Decimal(audits["editorial_cost_usd"])
            ):
                raise ValueError(
                    "Editorial funding, count or registration differs from the original records"
                )
        if publication is not None:
            registered = reviewed_release.locked(manifest, verified)
            if (
                publication != fingerprint(json_bytes(registered))
                or retained != audits["reviewed_first_draft_releases"]
            ):
                raise ValueError(
                    "Reviewed first drafts differ from the documented publication rule"
                )
        if (audits["recovery_releases"], audits["primary_releases"]) != (
            recovered - retained - len(editors),
            400 - recovered - approved_reviews,
        ) or Decimal(audits["outstanding_usd"]) != sum(
            Decimal(a["outstanding_usd"]) for a in audits_rows
        ):
            raise ValueError(
                "Recovered release count or reserved exposure differs from the ledgers"
            )
    if (
        summary["answers_sha256"] != audits["answers_sha256"]
        or audits["registration_sha256"] != fingerprint(json_bytes(manifest))
        or len(audits_rows) != 400
        or audits["tasks"] != 400
        or audits["verified_provider_generations"] < 1
        or audits["qualified"]
        != sum(row["state"] == "qualified" for row in audits_rows)
    ):
        raise ValueError("Supply the matching, fully verified 400-task export")
    for row, line, proof in zip(inputs, raw.splitlines(), audits_rows, strict=True):
        answer = json.loads(line)
        if (
            row["task_id"] != proof["task_id"]
            or not verified_selection(
                manifest,
                proof,
                extension,
                publication,
                funding,
                title_registration,
                budget_registration,
                budget_routes.tag(allocation, row["task_id"]),
            )
            or proof["report_sha256"] != fingerprint(answer["report"].encode())
            or proof["state"] not in {"complete", "qualified"}
        ):
            raise ValueError("A submitted report differs from its signed audit")
    if sum(Decimal(r["estimated_cost_usd"]) for r in audits_rows) != Decimal(
        audits["total_research_cost_usd"]
    ):
        raise ValueError("Official cost total differs from the assignment receipts")
    return manifest, audits


def model_names(cfg, audit):
    models = set(cfg["models"])
    if any("primary_draft_review" in r for r in audit.get("tasks_audited", ())):
        models.add(MODEL)
    if any(
        attempt["profile"] == "evidence_team_v4"
        for row in audit.get("tasks_audited", ())
        for attempt in row.get("attempts", (row,))
    ):
        models.update((ASTRA, MODEL))
    if any(
        r.get("independent_budget_route", {}).get("profile") == "mixed"
        for r in audit.get("tasks_audited", ())
    ):
        models.update((LUNA, MODEL, ASTRA))
    return sorted(models)


def per_question_usage(directory, audit):
    answers = (directory / "answers.jsonl").read_bytes().splitlines()
    times, costs = [], []
    for line, proof in zip(answers, audit["tasks_audited"], strict=True):
        elapsed = json.loads(line)["elapsed_s"]
        if (
            type(elapsed) not in (int, float)
            or not math.isfinite(elapsed)
            or elapsed < 0
        ):
            raise ValueError("Invalid recorded question duration")
        times.append(elapsed)
        costs.append(Decimal(proof["estimated_cost_usd"]))
    return {
        "mean_cost_usd": mean(costs),
        "p95_cost_usd": nearest(costs, 0.95),
        "mean_seconds": mean(times),
        "p95_seconds": nearest(times, 0.95),
    }


def paper(questions, directory):
    cfg, audit = verify_export(questions, directory)
    usage = per_question_usage(directory, audit)
    primary = json.loads(committed(PRIMARY / "blind/decision.json"))
    finance = json.loads(committed(FINANCEBENCH / "results.json"))["summary"]
    counts = Counter(cfg["profiles_by_task"].values())
    methods = "\n".join(
        f"- {NAMES[p]} received {n} original tasks under a USD {cfg['case_limits_usd'][p]} per-task ceiling."
        for p, n in sorted(counts.items())
    )
    if audit.get("recovery_registration_sha256"):
        original_cost = sum(
            Decimal(a["attempts"][0]["estimated_cost_usd"])
            for a in audit["tasks_audited"]
        )
        supplemental_cost = (
            Decimal(audit["total_research_cost_usd"])
            - original_cost
            - Decimal(audit.get("editorial_cost_usd", "0"))
            - Decimal(audit.get("primary_draft_review_cost_usd", "0"))
        )
        methods += f"\n\nThe primary researcher released {audit['primary_releases']} reports using an estimated USD {original_cost:.2f}. Independent investigations on unanswered questions released {audit['recovery_releases']} reports. All independent investigations together used an additional USD {supplemental_cost:.2f}. Unresolved provider reservations across both attempts total USD {audit['outstanding_usd']}."
        if audit.get("reviewed_first_draft_releases"):
            methods += f"\n\nAfter the first case in which an optional rewrite failed following a completed independent review, we added an offline publication rule. It produced {audit['reviewed_first_draft_releases']} further qualified reports by retaining only previously approved paragraphs. The originally registered system left these questions unanswered. Both original attempts and expenses are included. The additional publications used zero new searches or generations."
        if audit.get("editorial_releases"):
            methods += f"\n\nA second addition addressed questions with neither a released memo nor independently approved paragraphs. Sol checked proposed calculations, an Astra author wrote a short answer and a fresh Sol context reviewed it. Editing generated {audit['editorial_releases']} qualified reports for an additional USD {Decimal(audit['editorial_cost_usd']):.2f}, under a USD 0.55 ceiling per eligible case. The editor worked from verified dated passages."
        if audit.get("title_lookup_releases"):
            methods += f"\n\nFor {audit['title_lookup_releases']} of those edited reports, both investigators had retrieved zero passages. A later retrieval rule retried their original queries with capitalization variants of entity titles present in the question. It kept the cutoff, original query terms and result limits. The editor received the resulting historical passages. All earlier failures and the time spent on the retry were retained."
        if audit.get("primary_draft_review_attempts"):
            methods += f"\n\nAfter the owner set a USD 140 total project budget, we prospectively allowed one independent Sol review of a primary draft whose optional revision exceeded its allowance. It required a source-checked answer and a separate supporting section. We retained the original wording only when both survived independent review. The reviewer made no searches and had a USD 0.075 ceiling. Across {audit['primary_draft_review_attempts']} such reviews, {audit['primary_draft_review_releases']} qualified reports were released for a total additional USD {Decimal(audit['primary_draft_review_cost_usd']):.2f}. Earlier independent investigations kept their original outcomes."
    alternatives = [
        r for r in audit["tasks_audited"] if "independent_budget_route" in r
    ]
    if alternatives:
        released = sum(
            r["attempts"][1]["report_sha256"] is not None for r in alternatives
        )
        cost = sum(
            Decimal(r["attempts"][1]["estimated_cost_usd"]) for r in alternatives
        )
        methods += f"\n\nAfter every primary question and existing-draft review had settled, the available project balance determined the remaining independent research assignments. The previously tested mixed team took {len(alternatives)} source-rich questions under its USD 0.30 case ceiling. It conducted fresh dated searches, used Luna researchers and Sol analysis, and gave the Astra draft to a fresh Sol reviewer. The team released {released} reports directly and used an estimated USD {cost:.2f}, included in the independent totals above. All earlier investigations retain their original routes. The allocation used source counts and measured costs before spending."
    damaged = [
        a
        for task in audit["tasks_audited"]
        for a in task.get("attempts", [])
        if "archive_failure" in a
    ]
    if damaged:
        label = "failure" if len(damaged) == 1 else "failures"
        methods += f"\n\nWe reproduced {len(damaged)} PDF parsing {label} from the original dated searches and HTTP bytes. Each affected question retains its original research cost and follows the same independent recovery procedure as other unanswered questions."
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

The API model identifiers used on the benchmark were {", ".join(f"`{name}`" for name in model_names(cfg, audit))}.

{methods}

The researcher plans dated searches and receives passages only after the archive verifies their availability. Web evidence comes from dated captures, immutable encyclopedia revisions or accession-specific SEC records. For an admitted archived page or filing, the gateway can reopen the original bytes, match their hash and retrieve a section relevant to a specific part of the question. Every search uses the exact cutoff of its assigned question. Discovery titles and live snippets stay outside the model context. Numeric and citation checks run before the text is released. The local analyst application exposes cited passages, checked calculations and the cost of each run.

We ran the first six questions with up to three concurrent investigations, then used up to eight. When the public archive began rate-limiting requests, we let the active investigations finish and reduced concurrency after 218 questions. Subsequent primary investigations ran in groups of up to three, with pauses when the archive returned HTTP 429. The original results and expenses of all completed investigations were retained.

## Independent comparisons

Before publication, ThesisTape checks whether a statement attributed to the analyst's question appears in the original wording. When the check removes a passage, it retains the original and published hashes and qualifies the report. The same check applies to all configurations and incurs no model charge.

Before the official run we locked reports on 24 historical financial events across six categories. Cases used matched documents, dates and spending limits. Graders saw anonymized reports and eligible evidence before the evaluator opened the later outcomes. Local quality weighted facts at 50%, reasoning at 25% and forecast direction at 25%. Reports with no usable answer counted in the denominator. These weights define the local experiment.

| Configuration | Cases | Facts | Reasoning | Forecast direction | Local quality | Reports released |
|---|---:|---:|---:|---:|---:|---:|
{rows}

The event-paired 90% bootstrap interval for mixed minus single local quality was {100 * ci[0]:+.1f} to {100 * ci[1]:+.1f} percentage points. A separate financial statement test sampled one published FinanceBench question per issuer for 24 issuers. The quantitative team answered {arms["evidence_team_v4"]["both"]}/24 correctly with support from cited pages. Single Astra with the same tools answered {arms["quantitative_astra"]["both"]}/24. The 90% paired interval for that difference was {100 * fci[0]:+.1f} to {100 * fci[1]:+.1f} percentage points. The grader was an independent model context checking responses against the authors' human references. The open questions may have appeared in model training. [FinanceBench](https://github.com/patronus-ai/financebench/tree/cc39aeb4afdf33909ee1412188bf89035950c2eb)

## Reproducibility and cost

The JSONL contains exactly 400 original question and cutoff pairs. Each report's dated searches, provider request fingerprints, recorded usage and final Markdown were checked offline. The submission contains {audit["qualified"]} qualified reports. Those reports restrict their conclusions where the research did not establish part of the answer. Model generation for these runs cost an estimated USD {Decimal(audit["total_research_cost_usd"]):.2f}, based on the recorded usage for {audit["verified_provider_generations"]} confirmed generations. The estimate excludes local computing, external judging and development.

Per question, model costs averaged USD {usage["mean_cost_usd"]:.4f} and the 95th percentile was USD {usage["p95_cost_usd"]:.4f}. Recorded research time averaged {usage["mean_seconds"]:.1f} seconds, with a 95th percentile of {usage["p95_seconds"]:.1f} seconds. Each duration sums the elapsed time of that question's investigations and any additional editing. Time waiting between those stages is excluded.

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
base_model: {", ".join(model_names(cfg, audit))}
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
