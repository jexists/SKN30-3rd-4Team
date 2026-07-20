from __future__ import annotations

import unittest

from pipeline.evaluation.calibrate_app_thresholds import calibrate_thresholds


class AppThresholdTests(unittest.TestCase):
    def test_calibration_uses_success_and_first_gold_distributions(self) -> None:
        rows = [
            {"top1_similarity": 0.90, "hit_at_1": 1, "first_gold_similarity": 0.90},
            {"top1_similarity": 0.80, "hit_at_1": 1, "first_gold_similarity": 0.80},
            {"top1_similarity": 0.70, "hit_at_1": 1, "first_gold_similarity": 0.70},
            {"top1_similarity": 0.60, "hit_at_1": 0, "first_gold_similarity": 0.55},
            {"top1_similarity": 0.50, "hit_at_1": 0, "first_gold_similarity": 0.45},
        ]
        result = calibrate_thresholds(rows, min_questions=5)
        self.assertEqual(result["grade_strong"], 0.75)
        self.assertEqual(result["grade_weak"], 0.49)
        self.assertEqual(result["hit_at_1_successes"], 3)
        self.assertEqual(result["first_gold_hits_at_10"], 5)

    def test_calibration_rejects_insufficient_questions(self) -> None:
        with self.assertRaises(ValueError):
            calibrate_thresholds([], min_questions=1)


if __name__ == "__main__":
    unittest.main()
