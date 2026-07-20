from __future__ import annotations

import unittest

from pipeline.supabase.check_insert import vector_max_abs_diff


class SupabaseReconciliationTests(unittest.TestCase):
    def test_vector_difference_is_measured(self) -> None:
        self.assertAlmostEqual(vector_max_abs_diff([0.1, 0.2], [0.1, 0.2000005]), 5e-7)

    def test_dimension_mismatch_returns_none(self) -> None:
        self.assertIsNone(vector_max_abs_diff([0.1], [0.1, 0.2]))


if __name__ == "__main__":
    unittest.main()
