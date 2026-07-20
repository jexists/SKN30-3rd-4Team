from __future__ import annotations

import unittest

from pipeline.embedding.preflight import capacity_estimate


class EmbeddingPreflightTests(unittest.TestCase):
    def test_new_run_uses_conservative_fallback_and_reserve(self) -> None:
        result = capacity_estimate(
            total_rows=100,
            processed_rows=0,
            existing_bytes=0,
            free_bytes=2_000_000,
            fallback_bytes_per_row=1_000,
            safety_factor=1.25,
            reserve_bytes=100_000,
        )
        self.assertEqual(result["estimated_remaining_bytes_with_safety"], 125_000)
        self.assertEqual(result["required_free_bytes"], 225_000)
        self.assertTrue(result["passed"])

    def test_resume_uses_larger_observed_size_and_blocks_shortage(self) -> None:
        result = capacity_estimate(
            total_rows=100,
            processed_rows=20,
            existing_bytes=40_000,
            free_bytes=100_000,
            fallback_bytes_per_row=1_000,
            safety_factor=1.25,
            reserve_bytes=10_000,
        )
        self.assertEqual(result["estimated_bytes_per_row"], 2_000)
        self.assertEqual(result["required_free_bytes"], 210_000)
        self.assertFalse(result["passed"])

    def test_completed_run_needs_no_additional_reserve(self) -> None:
        result = capacity_estimate(
            total_rows=10,
            processed_rows=10,
            existing_bytes=50_000,
            free_bytes=0,
        )
        self.assertEqual(result["required_free_bytes"], 0)
        self.assertTrue(result["passed"])


if __name__ == "__main__":
    unittest.main()
