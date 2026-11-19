"""Bind development generation to the approved code, prompts, tools, data and spend limits."""

import hashlib
import inspect
import json
import platform
import sqlite3
import subprocess
from collections import Counter
from dataclasses import asdict
from decimal import Decimal
from importlib.metadata import version

from scripts import (
    claim_review,
    forecast_specialist,
    material_dispute,
    paired_research,
    paired_tools,
    sol_research,
    team_report,
    team_research,
)
from scripts.paired_packet import CHUNK_CHARS, IMAGE_SIZE, packet_digest
from scripts.paired_research import ARMS
from scripts.sol_provider import MODEL_RATES

from . import corpus

ROOT = corpus.ROOT
REPO = ROOT.parents[1]
MANIFEST = ROOT / "run-manifest.json"
FREEZE_REVISION = "819cb0bd21a9279fa267e882e44460536adcd72f"
RUNTIME_FILES = (
    "requirements-baseline.txt",
    "requirements-retrieval.txt",
    "evaluations/paired/corpus.py",
    "evaluations/paired/runner.py",
    "evaluations/paired/registration.py",
    "evaluations/paired/readiness.py",
    *(
        "scripts/" + name + ".py"
        for name in (
            "paired_packet",
            "paired_tools",
            "paired_research",
            "research_team",
            "team_research",
            "team_report",
            "team_meter",
            "claim_review",
            "forecast_specialist",
            "material_dispute",
            "visual_reader",
            "run_checkpoint",
            "sol_provider",
            "sol_baseline",
            "sol_gateway",
            "sol_research",
            "retrieval_gateway",
            "retrieval_sources",
            "baseline_evidence",
            "edgar_exhibits",
            "disney_hulu_case",
        )
    ),
)
PROMPT_BUILDERS = (
    team_research.team_evidence,
    team_research.lookup,
    team_research.worker,
    team_research.lead_decision,
    sol_research.decision_prompt,
    sol_research.discover,
    forecast_specialist.analyze,
    paired_tools.inspect_evidence,
    paired_research.workflow,
    claim_review.review_once,
    material_dispute.inspect,
    material_dispute.adjudicate,
    team_report.typed_prompt,
)


def hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def seed_order(seed, ids, prefix=""):
    return sorted(ids, key=lambda name: hash_bytes(f"{seed}/development/{prefix}{name}".encode()))


def schedule():
    freeze = corpus.read_json(ROOT / "freeze.json")
    proto = json.loads(corpus.checked_file(ROOT, freeze, "protocol.json"))
    indexed = {c["case_id"]: c for c in json.loads(corpus.checked_file(ROOT, freeze, "development.json"))}
    rows = []
    for cid in seed_order(proto["order_seed"], indexed):
        case = indexed[cid]
        q, sources = corpus.agent_fields(case)
        arms = [a for a in ARMS if a != "mixed_no_forecast" or q["forecast_rule"]]
        rows.append(
            {
                "case_id": cid,
                "packet_sha256": packet_digest(corpus.json_bytes(q), corpus.json_bytes(sources)),
                "arms": seed_order(proto["order_seed"], arms, cid + "/"),
            }
        )
    if Counter(arm for r in rows for arm in r["arms"]) != proto["development_runs"]:
        raise ValueError("Development schedule differs from the frozen allocation")
    return proto, rows


def git(*args):
    return subprocess.check_output(["git", "-C", str(REPO), *args])


def manifest(code_revision):
    proto, rows = schedule()
    development_ceiling = Decimal(proto["budget_usd"]["per_case_arm"]) * sum(len(r["arms"]) for r in rows)
    sources = {p: hash_bytes((REPO / p).read_bytes()) for p in RUNTIME_FILES}
    templates = {f"{f.__module__}.{f.__name__}": inspect.getsource(f) for f in PROMPT_BUILDERS}
    templates.update(
        search=sol_research.FIND,
        filings=team_research.ROLE["filings"],
        contrary=team_research.ROLE["contrary"],
        forecast=forecast_specialist.INSTRUCTIONS,
        writer=paired_research.WRITER,
        tools=paired_tools.TOOLS,
        image=paired_tools.VISUAL,
        review=claim_review.INSTRUCTIONS,
        conflict=material_dispute.SCREEN,
        adjudication=material_dispute.ADJUDICATE,
    )
    renderer = subprocess.run(
        ["pdftoppm", "-v"], capture_output=True, text=True, check=True
    ).stderr.splitlines()[0]
    return {
        "version": 1,
        "code_revision": code_revision,
        "corpus_revision": FREEZE_REVISION,
        "freeze_sha256": hash_bytes((ROOT / "freeze.json").read_bytes()),
        "runtime_files_sha256": sources,
        "prompt_sha256": {k: hash_bytes(v.encode()) for k, v in templates.items()},
        "runtime": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "pdf_renderer": renderer,
            "packages": {
                p: version(p) for p in ("pydantic", "pydantic-core", "pypdf", "fonttools", "cryptography")
            },
        },
        "models": {
            "discovery": "Project /v1/models listed the four exact aliases on 2026-11-20 Sydney. No dated snapshots were listed.",
            "rate_card": MODEL_RATES,
            "sources": [f"https://developers.openai.com/api/docs/models/{m}" for m in MODEL_RATES],
            "service_tier": "default",
            "store": False,
            "cache_ttl": "30m",
            "receipt_model": "retain the returned model ID",
        },
        "arms": {k: {**asdict(a), "bindings": a.bindings()} for k, a in ARMS.items()},
        "budgets_usd": {**proto["budget_usd"], "development_ceiling": str(development_ceiling), "paid_tool_fees": "0"},
        "tools": {
            "searches_per_lookup": sol_research.MAX_SEARCHES,
            "results_per_search": sol_research.RESULT_LIMIT,
            "passage_chars": CHUNK_CHARS,
            "image_long_side_pixels": IMAGE_SIZE,
            "visual_pages_per_report": 1,
            "max_calculations": 8,
            "tools": ["search_archive", "read_passages", "page_image", "decimal_calculator"],
        },
        "policy": {
            "workers": 2,
            "extra_targeted_assignments": 1,
            "transport_retries": 0,
            "ambiguous_charge": "retain reservation until reconciled",
            "failures": "one attempt; retain terminal failures",
            "structured_repairs": {
                "search": 1,
                "forecast": 1,
                "tool_plan": 1,
                "draft": 1,
                "review_revision": 1,
            },
            "output_token_limits": {
                "plan": 768,
                "search": 512,
                "extract": 960,
                "coverage": 1280,
                "tools": 1100,
                "visual": 800,
                "forecast": 2048,
                "write": 2560,
                "review": 2048,
                "adjudicate": 1536,
            },
            "protected_usd": {
                "adjudication": str(material_dispute.REVIEW_ALLOWANCE),
                "revision": str(material_dispute.RECHECK_ALLOWANCE),
            },
            "reasoning": "low for discovery, planning, extraction, tools and visual; medium for forecast, writing and review",
            "factual_forecast_ablation": "reuse mixed report and cost; zero additional dispatches",
        },
        "schedule": rows,
    }


def registered(*, for_generation=False):
    saved = corpus.read_json(MANIFEST)
    if manifest(saved["code_revision"]) != saved:
        raise ValueError("Runtime, frozen settings, corpus, or prompts differ from the registered manifest")
    if for_generation:
        path = str(MANIFEST.relative_to(REPO))
        if git("show", "HEAD:" + path) != MANIFEST.read_bytes():
            raise ValueError("Commit the run manifest before development generation")
        for filename, expected in saved["runtime_files_sha256"].items():
            if hash_bytes(git("show", saved["code_revision"] + ":" + filename)) != expected:
                raise ValueError("Source hash differs from its registered Git revision")
        if (
            hash_bytes(git("show", FREEZE_REVISION + ":evaluations/paired/freeze.json"))
            != saved["freeze_sha256"]
        ):
            raise ValueError("The case freeze differs from its registered revision")
    return saved
