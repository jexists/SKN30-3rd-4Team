from __future__ import annotations

import unittest

from pipeline.analysis.evaluate_ocr_results import ngram_coverage, normalized_numbers


class OcrComparisonTests(unittest.TestCase):
    def test_normalized_ngram_coverage_ignores_spacing_and_punctuation(self) -> None:
        score = ngram_coverage("보증금 50,000,000원", "보증금 50 000 000원")
        self.assertEqual(score, 1.0)

    def test_numbers_are_normalized(self) -> None:
        self.assertIn("50000000", normalized_numbers("50,000,000원"))


if __name__ == "__main__":
    unittest.main()
