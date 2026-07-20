from __future__ import annotations

import unittest

from pipeline.evaluation.finalize_grid import grid_is_provisional


class FinalizeGridTests(unittest.TestCase):
    def test_latest_winner_controls_provisional_status(self) -> None:
        self.assertFalse(grid_is_provisional({"provisional": False}))
        self.assertTrue(grid_is_provisional({"provisional": True}))
        self.assertTrue(grid_is_provisional({}))


if __name__ == "__main__":
    unittest.main()
