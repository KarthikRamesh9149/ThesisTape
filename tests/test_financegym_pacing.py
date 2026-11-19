import unittest
from collections import Counter

from evaluations.financegym.pacing import next_wave


class ArchivePacingTests(unittest.TestCase):
    def test_repeated_rate_limits_reduce_concurrency_and_clear_archives_recover_gradually(
        self,
    ):
        limited = Counter({"429": 12, "200": 50})
        self.assertEqual(next_wave(3, 3, False, limited), (3, 60, True))
        self.assertEqual(next_wave(3, 3, True, limited), (2, 60, True))
        self.assertEqual(next_wave(1, 3, True, limited), (1, 60, True))
        self.assertEqual(next_wave(1, 3, True, Counter({"200": 50})), (2, 0, False))
        self.assertEqual(
            next_wave(3, 3, False, Counter({"200": 50, "429": 1})), (3, 20, True)
        )
