from __future__ import annotations

import unittest

from pipeline.supabase.run_insert import allowed_rows_for_file


class SupabaseLimitTests(unittest.TestCase):
    def test_limit_is_global_dataset_cap(self) -> None:
        self.assertEqual(allowed_rows_for_file(0, 3_000, 5_000), 3_000)
        self.assertEqual(allowed_rows_for_file(3_000, 4_000, 5_000), 2_000)
        self.assertEqual(allowed_rows_for_file(7_000, 2_000, 5_000), 0)

    def test_no_limit_allows_whole_file(self) -> None:
        self.assertEqual(allowed_rows_for_file(20_000, 4_000, None), 4_000)


if __name__ == "__main__":
    unittest.main()
