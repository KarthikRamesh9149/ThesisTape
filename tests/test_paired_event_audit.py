import tempfile
import unittest
from functools import partial
from pathlib import Path

from evaluations.paired.corpus import export_development
from evaluations.paired.event_audit import (
    RESULTS,
    audit_event,
    checked_count,
    replay_failure,
)
from scripts.paired_packet import PacketCorpus
from scripts.run_checkpoint import load

CASE = "nintendo-fy2021-guidance"


class PaidEventAuditTests(unittest.TestCase):
    def test_archived_event_replays_all_settled_requests_offline(self):
        result = audit_event(CASE)
        self.assertEqual(result, load(RESULTS / CASE / "results.json"))
        self.assertEqual((result["generation_calls"], result["released_reports"]), (74, 0))

    def test_budget_refusal_depends_on_the_verified_token_count(self):
        folder = RESULTS / CASE / "single_astra"
        record = load(folder / "run.json")
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp) / "input"
            export_development(CASE, packet)
            corpus = PacketCorpus(packet, record["packet_sha256"])
            count = partial(checked_count, folder / "budget-stop.json")

            def changed_count(stage, prompt):
                return count(stage, prompt) + 1

            with self.assertRaisesRegex(ValueError, "Budget refusal differs"):
                replay_failure(record, corpus, "single_astra", count_rejected=changed_count)

    def test_replay_detects_changed_failure_reason(self):
        record = load(RESULTS / CASE / "single_sol/run.json")
        record["failure"] = "A different terminal reason"
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp) / "input"
            export_development(CASE, packet)
            corpus = PacketCorpus(packet, record["packet_sha256"])
            with self.assertRaisesRegex(ValueError, "Terminal failure changed"):
                replay_failure(record, corpus, "single_sol")
