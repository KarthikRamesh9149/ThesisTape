import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from evaluations.paired import reconciliation as r
from evaluations.paired.corpus import export_development
from scripts.paired_packet import PacketCorpus
from scripts.run_checkpoint import load


class SettlementTests(unittest.TestCase):
    def setUp(self):
        self.cid = "nintendo-fy2021-guidance"
        self.arm = "single_astra"
        path = r.sidecar("development", self.cid, self.arm)
        self.proof = load(path)
        self.record = load(path.with_suffix(".interrupted.json"))

    def test_real_export_cost_and_lost_dispatch_replay(self):
        proof = r.verify_resolution(self.record, self.proof)
        self.assertEqual(
            (proof["usage_usd"], proof["settled_total_usd"], proof["outstanding_usd"]),
            ("0.01004", "0.140664", "0"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp) / "input"
            export_development(self.cid, packet)
            corpus = PacketCorpus(packet, self.record["packet_sha256"])
            r.replay_lost(self.record, self.proof, corpus, self.arm)

    def test_reject_ambiguous_misattributed_and_overbudget_usage(self):
        for target, key, value in (
            ("target_minute", "num_model_requests", 2),
            ("target_minute", "input_tokens", 714),
            ("target_minute", "output_tokens", 769),
            ("target_minute", "account_scope_sha256", "other"),
            ("previous_minute", "output_tokens", 1064),
            ("target_minute", "start_time", 1795099500),
            ("target_minute", "service_tier", "priority"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                copy = deepcopy(self.proof)
                copy[target][key] = value
                r.verify_resolution(self.record, copy)
