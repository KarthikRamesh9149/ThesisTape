"""One-event capability and funding check. The only live endpoint counts input tokens."""

import json
import tempfile
from dataclasses import asdict
from decimal import Decimal
from pathlib import Path
from threading import Lock

from scripts.disney_hulu_case import CaseError
from scripts.paired_research import ARMS, workflow
from scripts.paired_tools import NUMBER, VISUAL, Operation, PageReading, calculate, numeric_text
from scripts.research_team import new_record
from scripts.sol_baseline import fingerprint, json_bytes
from scripts.sol_provider import OpenAIResponses, project_key, reservation_cost
from scripts.team_meter import Meter, verify_ledger
from scripts.team_report import typed_prompt
from scripts.visual_reader import with_page

from .registration import REPO, ROOT
from .runner import admission, config, isolated

PROOF = ROOT / "runner-readiness.json"


class TokenCounter:
    def __init__(self, saved):
        self.saved = saved
        self.client = OpenAIResponses(project_key(REPO / ".env.local")) if saved is None else None
        self.requests = {}
        self.dispatches = 0

    def count(self, prompt):
        key = fingerprint(json_bytes(prompt))
        tokens = self.client.count(prompt) if self.client else self.saved[key]["input_tokens"]
        self.requests[key] = {"model": prompt["model"], "input_tokens": tokens}
        return tokens

    def generate(self, payload):
        self.dispatches += 1
        raise AssertionError("Readiness check attempted a generation")


def probe(corpus, ticket, saved_counts):
    case = corpus.case
    with corpus.session(case.cutoff) as session:
        full = [h for s in corpus.sources for h in session.read(s["url"], max_date=case.cutoff)]
        hits = session.search(case.question, max_date=case.cutoff, limit=5)
        reopened = [session.read(h.citation_url, locator=h.locator, max_date=case.cutoff) for h in hits]
        if not hits or reopened != [(h,) for h in hits]:
            raise CaseError("Search could not reopen the same dated passages")
        page = session.page_image(hits[0], max_date=case.cutoff)
        unknown = session.read("https://unregistered.invalid/filing.pdf", max_date=case.cutoff)
        try:
            session.search(case.question, max_date="2099-01-01", limit=1)
        except ValueError:
            pass
        else:
            raise CaseError("Search accepted an alternate cutoff")
        if page is None or unknown:
            raise CaseError("Page access or packet boundary failed")
        # Exercise the calculator with a neutral identity, without writing an answer or using the key.
        operand = next(t[0] for t in NUMBER.finditer(numeric_text(hits[0].text)) if len(t[0]) <= 30)
        operation = Operation(
            label="Operand identity",
            unit="source units",
            operator="subtract",
            left="P1:" + operand,
            right="P1:" + operand,
        )
        calc = calculate([operation], {"P1": hits[0]})
        if Decimal(calc["C1"]["value"]) != 0:
            raise CaseError("Source-backed calculator check failed")
        shared = {
            "packet_sha256": corpus.digest,
            "source_hashes": sorted(s["sha256"] for s in corpus.sources),
            "cutoff": case.cutoff,
            "passages": len(full),
            "pages_with_text": len({(h.source_id, h.locator.split(" §")[0]) for h in full}),
            "search_hits_reopened": len(reopened),
            "unknown_source_blocked": True,
            "alternate_cutoff_blocked": True,
            "index_sha256": fingerprint(json_bytes([asdict(h) for h in full])),
            "search_read_trace_sha256": fingerprint(json_bytes(session.trace)),
            "pixel_sha256": fingerprint(page),
            "calculator_sha256": fingerprint(json_bytes(calc)),
        }
    starts = []
    lock = Lock()

    class ReachedDispatch(Exception):
        pass

    def capture(stage, prompt, limit, **kwargs):
        with lock:
            starts.append((stage, prompt, limit))
        raise ReachedDispatch

    try:
        workflow(corpus, ticket["arm"], capture)
    except ReachedDispatch:
        pass
    if not starts:
        raise CaseError("Workflow did not reach its registered opening dispatch")
    starts.sort(key=lambda item: item[0])
    first_stages = [s for s, _, _ in starts]
    binding = ARMS[ticket["arm"]].bindings()
    visual_prompt = with_page(
        typed_prompt(
            binding["visual"],
            VISUAL,
            {
                "locator": hits[0].locator,
                "question_about_page": "Identify the headings visible on the original page.",
            },
            PageReading,
        ),
        page,
    )
    starts.append(("visual.read", visual_prompt, 800))
    counter = TokenCounter(saved_counts)
    budget = Decimal(ticket["per_case_arm_usd"])
    checks = []
    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp) / "funding"
        record = new_record(config(corpus, ticket), directory, budget)
        meter = Meter(directory, record, counter)
        for stage, prompt, limit in starts:
            # Protect the entire allowance during the probe. This exercises the real pre-dispatch stop.
            result = meter(stage, prompt, limit, keep_usd=budget)
            key = fingerprint(json_bytes(prompt))
            reservation = reservation_cost(counter.requests[key]["input_tokens"], limit, prompt["model"])
            if result is not None or stage not in record["skipped_calls"] or reservation > budget:
                raise CaseError("Opening or image request failed the funding check")
            checks.append(
                {"stage": stage, "prompt_sha256": key, "maximum_charge_usd": format(reservation, "f")}
            )
        spent, pending = verify_ledger(record)
        if spent or pending or counter.dispatches or record["calls"]:
            raise CaseError("Preflight produced a billable dispatch or left a reservation")
    return {
        "shared_evidence": shared,
        "arm": ticket["arm"],
        "first_stages": first_stages,
        "funding_checks": checks,
        "budget_usd": str(budget),
        "generation_requests": counter.dispatches,
        "settled_generation_usd": str(spent),
        "token_counts": counter.requests,
    }


def preflight(manifest, *, live):
    reference = None if live else json.loads(PROOF.read_bytes())
    row = manifest["schedule"][0]
    results = []
    with tempfile.TemporaryDirectory(prefix="thesistape-preflight-") as tmp:
        for i, arm in enumerate(row["arms"]):
            expected = None if live else reference["arms"][i]["token_counts"]
            results.append(isolated(admission(manifest, row, arm), Path(tmp), "probe", counts=expected))
    if len(results) != 6 or any(r["shared_evidence"] != results[0]["shared_evidence"] for r in results):
        raise CaseError("Development arms do not have identical access")
    if any(r["budget_usd"] != manifest["budgets_usd"]["per_case_arm"] for r in results):
        raise CaseError("Arm budget drift")
    shared = results[0]["shared_evidence"]
    for r in results:
        del r["shared_evidence"]
    result = {
        "event": row["case_id"],
        "run_manifest_sha256": fingerprint(json_bytes(manifest)),
        "scope": "input-token count and pre-dispatch controls only",
        "shared_evidence": shared,
        "arms": results,
        "generation_requests": sum(r["generation_requests"] for r in results),
        "input_token_requests": sum(len(r["funding_checks"]) for r in results),
    }
    if not live and result != reference:
        raise CaseError("Preflight differs from its saved live token-count evidence")
    return result
