import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from evaluations.paired.analyst_review import assess, assignments, sheet
from scripts.sol_baseline import fingerprint


class AnalystStudyTests(unittest.TestCase):
    def test_balanced_order_within_six_strata_and_blank_reviews_stay_unscored(self):
        rows = [
            {"case_id": f"test-{s}-{i}", "stratum": str(s), "arm": a}
            for s in range(6)
            for i in range(4)
            for a in ("mixed", "single_sol")
        ]
        picked = assignments(rows, 123)
        self.assertEqual(len(picked), 24)
        self.assertEqual(len({r["case_id"] for r in picked}), 12)
        self.assertEqual([r["arm"] for r in picked[::2]].count("mixed"), 6)
        for i in range(0, 24, 4):
            self.assertNotEqual(picked[i]["arm"], picked[i + 2]["arm"])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            packet = root / "analyst-packet.zip"
            with zipfile.ZipFile(packet, "w") as z:
                z.writestr("observations.csv", sheet())
            (root / "operator-key.json").write_text(
                json.dumps(
                    {
                        "packet_sha256": fingerprint(packet.read_bytes()),
                        "assignments": [],
                    }
                )
            )
            (root / "reviewer.json").write_text("{}")
            with self.assertRaisesRegex(ValueError, "human analyst"):
                assess(root, root)
