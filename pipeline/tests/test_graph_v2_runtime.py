from __future__ import annotations

import unittest

from pipeline.app.v2_runtime import thresholds_from_env


class GraphV2RuntimeTests(unittest.TestCase):
    def test_thresholds_are_parsed_in_weak_strong_order(self) -> None:
        self.assertEqual(
            thresholds_from_env(
                {
                    "LEGAL_RAG_V2_GRADE_WEAK": "0.41",
                    "LEGAL_RAG_V2_GRADE_STRONG": "0.63",
                }
            ),
            (0.41, 0.63),
        )

    def test_thresholds_require_values_range_and_order(self) -> None:
        with self.assertRaises(RuntimeError):
            thresholds_from_env({})
        with self.assertRaises(ValueError):
            thresholds_from_env(
                {
                    "LEGAL_RAG_V2_GRADE_WEAK": "0.7",
                    "LEGAL_RAG_V2_GRADE_STRONG": "0.6",
                }
            )
        with self.assertRaises(ValueError):
            thresholds_from_env(
                {
                    "LEGAL_RAG_V2_GRADE_WEAK": "-2",
                    "LEGAL_RAG_V2_GRADE_STRONG": "0.6",
                }
            )


if __name__ == "__main__":
    unittest.main()
