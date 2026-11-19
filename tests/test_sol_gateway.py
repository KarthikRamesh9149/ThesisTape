import json
import re
import unittest
from copy import deepcopy
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from scripts import sol_baseline, sol_gateway
from scripts.baseline_evidence import load_case
from scripts.disney_hulu_case import SAVED_CASE, CaseError
from scripts.retrieval_gateway import Corpus
from scripts.sol_provider import MODEL, Result, usage_cost
from scripts.sol_research import research, tool_decision
from tests.test_sol_baseline import draft


def search(query):
    return {"name": "search_archive", "arguments": {"query": query}}


def read(ids):
    return {"name": "read_passages", "arguments": {"passage_ids": ids}}


class RecordedSol:
    """Script only the provider boundary. Ranking, source selection and checks run locally."""

    def __init__(self, actions):
        self.actions = iter(actions)
        self.requests = []

    def count(self, prompt):
        return 200

    def generate(self, payload):
        self.requests.append(deepcopy(payload))
        context = json.loads(payload["input"])
        action = next(self.actions)
        if isinstance(action, Exception):
            raise action
        if callable(action):
            action = action(context)
        if "tools" in payload:
            output = {
                "type": "function_call",
                "name": action["name"],
                "arguments": json.dumps(action["arguments"]),
            }
        else:
            output = {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": json.dumps(action)}],
            }
        body = {
            "id": "resp_local",
            "model": MODEL,
            "status": "completed",
            "service_tier": "default",
            "usage": {
                "input_tokens": 200,
                "input_tokens_details": {"cached_tokens": 50},
                "output_tokens": 60,
            },
            "output": [{"type": "reasoning"}, output],
        }
        return Result(body, "req_local", 123)


def pick_disney(context):
    results = context["searches"][-1]["results"]
    return read(
        [r["id"] for r in results if r["title"] == "Disney Form 8-K" and r["locator"].endswith(("¶1", "¶2"))]
    )


def from_opened(context):
    ids = {p["locator"][-1]: p["id"] for p in context["passages"]}
    return json.loads(re.sub(r'"P([12])"', lambda m: json.dumps(ids[m[1]]), json.dumps(draft())))


class SolGatewayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.case = load_case(SAVED_CASE / "agent")
        cls.corpus = Corpus()

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.out = Path(temp.name) / "run"

    def run_with(self, model, cap="0.05"):
        return sol_gateway.run(self.case, self.out, Decimal(cap), model, corpus=self.corpus)

    def record(self):
        return json.loads((self.out / "run.json").read_bytes())

    def test_refinement_preserves_handles_and_only_selected_text_reaches_writer(self):
        model = RecordedSol(
            [search("Toyota"), search("Hulu appraisal NBCUniversal"), pick_disney, from_opened]
        )
        with patch("scripts.sol_baseline.retrieve", side_effect=AssertionError("supplied evidence used")):
            run = self.run_with(model)
            self.assertEqual(sol_baseline.replay(self.case, self.out), (self.out / "memo.md").read_bytes())
        opening, refinement, selection, final = (json.loads(p["input"]) for p in model.requests)
        self.assertEqual(opening, {"question": self.case.question, "cutoff": self.case.cutoff.isoformat()})
        self.assertEqual(
            [s["query"] for s in selection["searches"]], ["Toyota", "Hulu appraisal NBCUniversal"]
        )
        self.assertEqual(refinement["searches"], selection["searches"][:1])
        self.assertIn("Toyota", str(refinement))
        self.assertNotIn("Toyota", str(final))
        self.assertTrue(all(p["text"] == run["opened"][p["id"]]["text"] for p in final["passages"]))
        self.assertEqual({p["locator"][-1] for p in final["passages"]}, {"1", "2"})
        self.assertFalse({"P1", "P2"} & run["opened"].keys())
        self.assertEqual([e["operation"] for e in run["retrieval"]], ["search", "search", "read", "read"])
        self.assertTrue(all(e["max_date"] == self.case.cutoff.isoformat() for e in run["retrieval"]))
        self.assertEqual(
            sum(usage_cost(c["provider"]) for c in run["calls"]), Decimal(run["estimated_model_cost_usd"])
        )
        self.assertEqual(run["unreconciled_reservation_usd"], "0")
        self.assertNotIn("req_local", (self.out / "run.json").read_text())
        self.assertEqual(len(json.loads((self.out / "provider-ids.json").read_bytes())), 4)
        with self.assertRaisesRegex(CaseError, "existing run"):
            self.run_with(model)
        self.assertEqual(len(model.requests), 4)

    def test_unseen_number_cannot_seed_first_search(self):
        model = RecordedSol([search("Hulu $8.61 billion $27.5 billion")])
        with patch("scripts.retrieval_gateway.DatedSession.search") as lookup:
            with self.assertRaisesRegex(CaseError, "number absent"):
                self.run_with(model)
            lookup.assert_not_called()
        self.assertEqual(self.record()["state"], "failed")
        self.assertGreater(Decimal(self.record()["estimated_model_cost_usd"]), 0)

    def test_queries_can_use_form_names_and_figures_returned_in_an_earlier_preview(self):
        queries = iter((search("Hulu 8-K"), search("Disney 2023 November 1"), read([])))
        with self.assertRaisesRegex(CaseError, "insufficient evidence"):
            research(self.case, self.corpus, lambda *_: next(queries))

    def test_rejects_unknown_ids_direct_urls_and_cutoff_overrides(self):
        decisions = [
            read(["P999"]),
            read(["https://www.sec.gov/future"]),
            read(["P1", "P1"]),
            {"name": "search_archive", "arguments": {"query": "NBCU", "max_date": "2025-06-09"}},
            {"name": "other_tool", "arguments": {}},
        ]
        for decision in decisions:
            with self.subTest(decision=decision):
                actions = iter((search("Hulu"), decision))
                with self.assertRaises(CaseError):
                    research(self.case, self.corpus, lambda *_, actions=actions: next(actions))

    def test_no_eligible_evidence_stops_without_drafting(self):
        model = RecordedSol([search("xxyyzz"), search("zzqvwx")])
        with self.assertRaisesRegex(CaseError, "No eligible"):
            self.run_with(model)
        self.assertEqual(len(model.requests), 2)
        self.assertFalse((self.out / "memo.md").exists())

    def test_correct_figures_without_opened_support_cannot_pass(self):
        model = RecordedSol([search("Toyota"), read(["P1"]), draft()])
        with self.assertRaisesRegex(CaseError, "citation quote"):
            self.run_with(model)
        record = self.record()
        self.assertEqual(record["state"], "failed")
        self.assertEqual(len(record["calls"]), 3)
        self.assertEqual(record["unreconciled_reservation_usd"], "0")
        self.assertFalse((self.out / "memo.md").exists())

    def test_cumulative_budget_stops_before_next_paid_dispatch(self):
        model = RecordedSol([search("Hulu"), pick_disney, from_opened])
        # A single search fits, but the second reservation plus confirmed spend exceeds this cap.
        with self.assertRaisesRegex(CaseError, "exceeds budget"):
            self.run_with(model, "0.0060")
        record = self.record()
        self.assertEqual(len(model.requests), 1)
        self.assertEqual(record["unreconciled_reservation_usd"], "0")
        self.assertEqual(
            Decimal(record["estimated_model_cost_usd"]), usage_cost(record["calls"][0]["provider"])
        )

    def test_unconfirmed_request_keeps_reservation_and_never_retries(self):
        model = RecordedSol([search("NBCU Hulu"), CaseError("unconfirmed request")])
        with self.assertRaisesRegex(CaseError, "unconfirmed"):
            self.run_with(model)
        record = self.record()
        self.assertEqual(len(model.requests), 2)
        self.assertEqual(record["unreconciled_reservation_usd"], record["calls"][1]["reservation_usd"])
        self.assertEqual(record["estimated_model_cost_usd"], record["calls"][0]["estimated_model_cost_usd"])
        with self.assertRaisesRegex(CaseError, "incomplete"):
            sol_gateway.replay(self.case, record)

    def test_replay_detects_changed_calls_sources_totals_and_trace(self):
        record = self.run_with(RecordedSol([search("Hulu appraisal"), pick_disney, from_opened]))
        changes = (
            ("corpus_sha256",),
            ("estimated_model_cost_usd",),
            ("memo_sha256",),
            ("retrieval",),
            ("calls", 0, "output", "arguments", "query"),
            ("calls", 1, "request_sha256"),
            ("calls", 2, "provider", "usage", "output_tokens"),
            ("opened", next(iter(record["opened"])), "sha256"),
        )
        for path in changes:
            copy = deepcopy(record)
            node = copy
            for key in path[:-1]:
                node = node[key]
            node[path[-1]] = 7 if path[-1] == "output_tokens" else "0"
            with self.subTest(path=path), self.assertRaises(CaseError):
                sol_gateway.replay(self.case, copy, corpus=self.corpus)

    def test_requires_one_completed_model_tool_call(self):
        item = {"type": "function_call", "name": "search_archive", "arguments": '{"query":"Hulu"}'}
        for body in (
            {"status": "incomplete", "output": [item]},
            {"status": "completed", "output": [item, item]},
            {"status": "completed", "output": [{**item, "arguments": "null"}]},
        ):
            with self.subTest(body=body), self.assertRaises(CaseError):
                tool_decision(body)

    def test_saved_real_memos_replay_and_every_gateway_link_reopens(self):
        directory = SAVED_CASE.parents[1] / "evaluations/disney-sol-gateway"
        for path in (directory, directory / "supplied-control"):
            with self.subTest(path=path):
                self.assertEqual(sol_baseline.replay(self.case, path), (path / "memo.md").read_bytes())
        record = json.loads((directory / "run.json").read_bytes())
        memo = (directory / "memo.md").read_text()
        links = re.findall(r"\]\((https://[^)]+)\)", memo)
        passages = {h["citation_url"]: h for h in record["opened"].values()}
        source_urls = {h["url"] for h in passages.values()}
        locators = set()
        with self.corpus.session(self.case.cutoff.isoformat()) as session:
            for url in links:
                result = session.read(url, max_date=session.cutoff)
                self.assertTrue(result)
                if url in source_urls:
                    self.assertTrue(all(h.url == url for h in result))
                else:
                    self.assertEqual(len(result), 1)
                    self.assertEqual(result[0].text, passages[url]["text"])
                    locators.add(result[0].locator)
        self.assertEqual(len(links), 7)
        self.assertEqual(locators, {"Item 8.01 Other Events. ¶1", "Item 8.01 Other Events. ¶2"})
