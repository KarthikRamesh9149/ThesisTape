import json
import re
import unittest
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import replace
from datetime import date
from decimal import Decimal
from itertools import pairwise
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event

from scripts import research_team as runner
from scripts import team_research as team
from scripts.baseline_evidence import Citation, Paragraph, check_paragraph
from scripts.disney_hulu_case import SAVED_CASE, CaseError
from scripts.retrieval_gateway import Corpus, Evidence
from scripts.sol_baseline import fingerprint, generation_request, json_bytes
from scripts.sol_provider import LUNA, MODEL, Result, usage_cost
from scripts.team_meter import Meter, verify_ledger

PROOF = SAVED_CASE.parents[1] / "evaluations/first-team"


def saved(name):
    return json.loads((PROOF / name / "run.json").read_bytes())


class TeamResearchTests(unittest.TestCase):
    def test_cutoff_language_is_supported_but_transaction_dates_still_require_evidence(self):
        text = "The appraisal is still pending."

        def check(claim):
            paragraph = Paragraph(text=claim, citations=[Citation(passage_id="P1", quote=text)])
            return check_paragraph(paragraph, {"P1": text}, date(2023, 11, 2))

        self.assertEqual(
            check("The price was not known on November 2."), "The price was not known on November 2."
        )
        for claim in (
            "The price was not known on November 2, 2025.",
            "Disney paid on November 2.",
            "The price remained unknown on November 3.",
        ):
            with self.subTest(claim=claim), self.assertRaisesRegex(CaseError, "numerical claim"):
                check(claim)

    def test_writer_can_use_remaining_budget_without_overspending(self):
        class Provider:
            def count(self, _):
                return 1000

            def generate(self, payload):
                self.limit = payload["max_output_tokens"]
                body = {
                    "status": "completed",
                    "service_tier": "default",
                    "model": MODEL,
                    "usage": {"input_tokens": 1000, "input_tokens_details": {}, "output_tokens": 150},
                    "output": [
                        {
                            "type": "message",
                            "role": "assistant",
                            "content": [{"type": "output_text", "text": "{}"}],
                        }
                    ],
                }
                return Result(body, "local", 1)

        with TemporaryDirectory() as tmp:
            record = {"budget_usd": "0.0145"}
            provider = Provider()
            meter = Meter(Path(tmp), record, provider)
            meter("lead.memo_repair", {"model": MODEL}, 2560)
            self.assertEqual(provider.limit, 1200)
            self.assertEqual(record["calls"]["lead.memo_repair"]["requested_output_tokens"], 2560)
            verify_ledger(record)

    def test_luna_and_sol_have_separate_metered_rates(self):
        receipt = {
            "model": LUNA,
            "service_tier": "default",
            "usage": {
                "input_tokens": 1000,
                "input_tokens_details": {"cached_tokens": 100, "cache_write_tokens": 200},
                "output_tokens": 100,
            },
        }
        self.assertEqual(usage_cost(receipt, expected_model=LUNA), Decimal("0.000146"))
        with self.assertRaisesRegex(CaseError, "requested model"):
            usage_cost(receipt, expected_model=MODEL)

    def test_parallel_reservations_cannot_spend_the_same_remaining_budget(self):
        started, release = Event(), Event()

        class Provider:
            generated = 0

            def count(self, _):
                return 100

            def generate(self, _):
                self.generated += 1
                started.set()
                if not release.wait(3):
                    raise AssertionError("Missing parallel reservation check")
                raise CaseError("Unconfirmed provider response")

        with TemporaryDirectory() as tmp, ThreadPoolExecutor(max_workers=2) as pool:
            provider = Provider()
            record = {"budget_usd": "0.00040"}
            meter = Meter(Path(tmp), record, provider)
            prompt = {"model": LUNA}
            first = pool.submit(meter, "filings.search.1", prompt, 512)
            try:
                self.assertTrue(started.wait(3))
                with self.assertRaisesRegex(CaseError, "exceeds budget"):
                    meter("contrary.search.1", prompt, 512)
            finally:
                release.set()
            with self.assertRaisesRegex(CaseError, "Unconfirmed"):
                first.result()
            with self.assertRaisesRegex(CaseError, "stopped"):
                meter("lead.memo", prompt, 256)
            self.assertEqual(record["unreconciled_reservation_usd"], "0.0002685")
            self.assertEqual(record["estimated_model_cost_usd"], "0")
            self.assertEqual(provider.generated, 1)

    def test_confirmed_incomplete_response_counts_as_spend(self):
        class Provider:
            def count(self, _):
                return 100

            def generate(self, _):
                return Result(
                    {
                        "status": "incomplete",
                        "model": LUNA,
                        "service_tier": "default",
                        "usage": {"input_tokens": 100, "input_tokens_details": {}, "output_tokens": 100},
                    },
                    "local-receipt",
                    1,
                )

        with TemporaryDirectory() as tmp:
            record = {"budget_usd": "0.05"}
            meter = Meter(Path(tmp), record, Provider())
            with self.assertRaisesRegex(CaseError, "did not complete"):
                meter("filings.extract", {"model": LUNA}, 512)
            self.assertEqual(Decimal(record["estimated_model_cost_usd"]), Decimal("0.00006"))
            self.assertEqual(record["unreconciled_reservation_usd"], "0")
            self.assertNotIn("output", record["calls"]["filings.extract"])

    def test_span_offsets_reopen_exact_text_even_on_long_rows(self):
        sample = "A forecast with a qualification, " * 20 + "x" * 480 + " USD 659 million"
        positions = list(team.source_spans(sample))
        self.assertTrue(all(12 <= b - a <= 220 for a, b in positions))
        self.assertTrue(all(a1 < a2 for (a1, _), (a2, _) in pairwise(positions)))
        self.assertTrue(any("USD 659 million" in sample[a:b] for a, b in positions))
        self.assertTrue(
            all(not sample[i].strip() or any(a <= i < b for a, b in positions) for i in range(len(sample)))
        )

    def test_live_artifacts_replay_and_every_memo_link_opens(self):
        for folder, key in (
            ("disney-team", "disney"),
            ("tradeweb-team", "tradeweb"),
            ("tradeweb-single", "tradeweb"),
        ):
            with self.subTest(folder=folder):
                record = saved(folder)
                case = team.case_for(key)
                memo = runner.replay(case, record)
                self.assertEqual(memo, (PROOF / folder / "memo.md").read_bytes())
                self.assertLessEqual(Decimal(record["estimated_model_cost_usd"]), Decimal("0.05"))
                with Corpus().session(case.cutoff) as gateway:
                    for url in re.findall(r"\]\((https://[^)]+)\)", memo.decode()):
                        result = gateway.read(url, max_date=case.cutoff)
                        self.assertTrue(result)
                        self.assertTrue(any(h.url == url or h.citation_url == url for h in result))
                for result in record["workers"]:
                    self.assertTrue(all(e["max_date"] == case.cutoff for e in result["trace"]))
                    for finding in result["findings"]:
                        text = result["opened"][finding["passage_id"]]["text"]
                        self.assertEqual(finding["quote"], text[finding["start"] : finding["end"]])

    def test_workers_start_concurrently_with_independent_context_and_requested_models(self):
        record = saved("disney-team")
        case = team.case_for("disney")
        barrier = Barrier(2, timeout=5)
        seen = set()

        def ask(stage, prompt, limit):
            actual = record["calls"][stage]
            self.assertEqual(
                actual["request_sha256"],
                fingerprint(
                    json_bytes(generation_request(prompt, max_output_tokens=actual["max_output_tokens"]))
                ),
            )
            self.assertEqual(limit, actual.get("requested_output_tokens", actual["max_output_tokens"]))
            data = json.loads(prompt["input"])
            if stage in ("filings.search.1", "contrary.search.1"):
                who = stage.split(".")[0]
                self.assertEqual(data, {**case.intake(), "assignment": record["assignments"][who]})
                self.assertEqual(prompt["model"], LUNA)
                barrier.wait()
            if stage.startswith("lead."):
                self.assertEqual(prompt["model"], MODEL)
            seen.add(stage)
            return actual["output"]

        memo, _ = team.research(case, Corpus(), ask, route="team")
        self.assertEqual(set(record["calls"]), seen)
        self.assertEqual(fingerprint(memo), record["memo_sha256"])

    def test_live_pre_amendment_check_follows_up_and_qualifies_without_hindsight(self):
        record = saved("tradeweb-before")
        case = team.case_for("tradeweb-before")
        self.assertEqual([d["action"] for d in record["decisions"]], ["check_filings", "qualify"])
        self.assertNotIn("lead.memo", record["calls"])

        def ask(stage, prompt, limit):
            actual = record["calls"][stage]
            payload = json_bytes(generation_request(prompt, max_output_tokens=limit))
            self.assertEqual(fingerprint(payload), actual["request_sha256"])
            for future in (
                "$695 million",
                "$659 million",
                "0001758730-23-000171",
                "Tradeweb Markets Form 8-K/A",
            ):
                self.assertNotIn(future, payload.decode())
            if stage == "lead.decide.2":
                self.assertEqual(
                    prompt["text"]["format"]["schema"]["properties"]["action"]["enum"], ["write", "qualify"]
                )
            return actual["output"]

        memo, trace = team.research(case, Corpus(), ask, route="team")
        self.assertEqual(memo, runner.replay(case, record))
        self.assertEqual(len(trace["workers"]), 3)
        self.assertIn(b"insufficient evidence", memo)
        self.assertEqual(
            {h["source_id"] for w in trace["workers"] for h in w["opened"].values()}, {"tradeweb-8k"}
        )

    def test_empty_evidence_qualifies_without_a_paid_draft(self):
        def ask(stage, _prompt, _limit):
            if stage == "lead.plan":
                return json.dumps({"filings": "Check the terms", "contrary": "Look for exceptions"})
            if stage == "lead.decide.1":
                return json.dumps({"action": "qualify", "instruction": "No eligible source located."})
            self.assertIn(".search.", stage)
            return {"name": "search_archive", "arguments": {"query": "xxyyzz"}}

        memo, trace = team.research(team.case_for("tradeweb"), Corpus(), ask, route="team")
        self.assertEqual(trace["coverage"], "qualify")
        self.assertEqual(trace["evidence"], {})
        self.assertIn(b"insufficient evidence", memo)

    def test_guessed_figure_never_reaches_search_and_gets_one_neutral_repair(self):
        case = team.case_for("disney")

        def ask(stage, prompt, _):
            data = json.loads(prompt["input"])
            self.assertNotIn("8.61", prompt["input"])
            if stage == "filings.search.1":
                return {"name": "search_archive", "arguments": {"query": "Hulu 8.61 billion"}}
            if stage == "filings.query_repair":
                self.assertIn("rejected", data["validation_feedback"])
                return {"name": "search_archive", "arguments": {"query": "Hulu NBCUniversal"}}
            return {"name": "read_passages", "arguments": {"passage_ids": []}}

        result = team.lookup(case, Corpus(), ask, "filings", "Find transaction terms", "filings", LUNA)
        self.assertEqual([search["query"] for search in result.searches], ["Hulu NBCUniversal"])
        self.assertEqual([e["operation"] for e in result.trace], ["search"])

        def repeat(_stage, _prompt, _limit):
            return {"name": "search_archive", "arguments": {"query": "Hulu 8.61 billion"}}

        with self.assertRaisesRegex(CaseError, "number absent"):
            team.lookup(case, Corpus(), repeat, "filings", "Look for terms", "filings", LUNA)

    def test_worker_rejects_a_span_outside_its_own_opened_passages(self):
        record = saved("tradeweb-team")

        def ask(stage, _prompt, _limit):
            if stage == "filings.extract":
                return json.dumps({"findings": [{"span_id": "foreign.S1", "kind": "fact"}]})
            return record["calls"][stage]["output"]

        with self.assertRaisesRegex(CaseError, "outside its assignment"):
            team.worker(
                team.case_for("tradeweb"),
                Corpus(),
                ask,
                "filings",
                record["assignments"]["filings"],
                "filings",
            )

    def test_real_tradeweb_searches_do_not_reveal_amendment_one_second_early(self):
        record = saved("tradeweb-team")
        late = team.case_for("tradeweb")
        early = replace(late, cutoff="2023-10-26T13:16:38Z")
        with Corpus().session(early.cutoff) as gateway:
            for w in record["workers"]:
                for search in w["searches"]:
                    hits = gateway.search(search["query"], max_date=early.cutoff)
                    self.assertNotIn("tradeweb-8ka", [h.source_id for h in hits])
                    self.assertNotIn("$695 million", str(hits))
            url = next(h["url"] for h in record["evidence"].values() if h["source_id"] == "tradeweb-8ka")
            self.assertEqual(gateway.read(url, max_date=early.cutoff), ())

    def test_replay_rejects_changed_model_order_cost_and_evidence(self):
        original = saved("disney-team")
        for path, value in [
            (("calls", "filings.extract", "model"), MODEL),
            (("calls", "lead.memo", "estimated_model_cost_usd"), "0"),
            (("estimated_model_cost_usd",), "0"),
            (("events", 0, 0), "settle"),
            (("calls", "lead.memo", "confirmed_ms"), 0),
            (("workers", 0, "findings", 0, "quote"), "Incorrect source text"),
            (("calls", "contrary.search.1", "request_sha256"), "0" * 64),
        ]:
            record = deepcopy(original)
            node = record
            for k in path[:-1]:
                node = node[k]
            node[path[-1]] = value
            with self.subTest(path=path), self.assertRaises(CaseError):
                runner.replay(team.case_for("disney"), record)
        verify_ledger(original)

    def test_filing_history_requires_source_wording_for_the_replacement(self):
        record = saved("tradeweb-single")
        draft = json.loads(record["calls"]["lead.memo"]["output"])
        history = draft["filing_history"]
        history["citations"] = [c for c in history["citations"] if "unchanged" in c["quote"]]
        self.assertTrue(history["citations"])
        opened = {pid: Evidence(**h) for pid, h in record["evidence"].items()}
        with self.assertRaisesRegex(CaseError, "replacement"):
            team.author(team.case_for("tradeweb"), lambda *_: json.dumps(draft), opened, [], "write")

    def test_development_spend_includes_failures_and_matches_the_portable_receipts(self):
        data = json.loads((PROOF / "development-usage.json").read_bytes())
        runs = data["attempts"]
        self.assertEqual(len(runs), len({r["run_id"] for r in runs}))
        self.assertTrue(
            any(r["state"] == "failed" and Decimal(r["estimated_model_cost_usd"]) > 0 for r in runs)
        )
        total = Decimal(0)
        for entry in runs:
            cost = Decimal(entry["estimated_model_cost_usd"])
            self.assertEqual(cost, sum(Decimal(v["usd"]) for v in entry["usage_by_model"].values()))
            total += cost
            self.assertLessEqual(cost, Decimal(entry["budget_usd"]))
            artifact = entry.get("artifact", "")
            if artifact.endswith("run.json"):
                verified = json.loads((PROOF / artifact).read_bytes())
                self.assertEqual(verified["run_id"], entry["run_id"])
                self.assertEqual(verified["estimated_model_cost_usd"], entry["estimated_model_cost_usd"])
            elif artifact:
                self.assertEqual(fingerprint((PROOF / artifact).read_bytes()), entry["memo_sha256"])
        self.assertEqual(total, Decimal(data["checkpoint_usd"]))
        self.assertEqual(total + Decimal(data["prior_recorded_usd"]), Decimal(data["project_recorded_usd"]))
