from __future__ import annotations

import unittest

from pipeline.evaluation.retrieval_eval import (
    grouped_metric_rows,
    metrics_for_query,
    ndcg,
    percentile,
)


class RetrievalMetricsTests(unittest.TestCase):
    def test_binary_retrieval_metrics(self) -> None:
        result = metrics_for_query(["x", "gold", "y"], {"gold"})
        self.assertEqual(result["hit_at_1"], 0.0)
        self.assertEqual(result["hit_at_3"], 1.0)
        self.assertEqual(result["recall_at_3"], 1.0)
        self.assertEqual(result["mrr_at_10"], 0.5)
        self.assertGreater(result["ndcg_at_10"], 0.0)

    def test_duplicate_chunks_do_not_double_count_ndcg(self) -> None:
        score = ndcg(["gold-a", "gold-a", "other"], {"gold-a"}, k=3)
        self.assertEqual(score, 1.0)

    def test_group_metrics_cover_issue_and_each_source_type(self) -> None:
        base_metrics = metrics_for_query(["gold"], {"gold"})
        rows = [
            {
                "issue": "계약갱신",
                "source_type_values": ["statute", "standard_contract"],
                "latency_ms": 2.0,
                **base_metrics,
            }
        ]
        groups = {(row["group_type"], row["group"]): row for row in grouped_metric_rows(rows)}
        self.assertEqual(groups[("issue", "계약갱신")]["questions"], 1)
        self.assertEqual(groups[("source_type", "statute")]["hit_at_5"], 1.0)
        self.assertEqual(groups[("source_type", "standard_contract")]["recall_at_5"], 1.0)

    def test_percentile_uses_linear_interpolation(self) -> None:
        self.assertEqual(percentile([], 0.5), None)
        self.assertEqual(percentile([1.0, 3.0], 0.5), 2.0)
        self.assertAlmostEqual(percentile([1.0, 2.0, 3.0], 0.9), 2.8)
        with self.assertRaises(ValueError):
            percentile([1.0], 1.1)


if __name__ == "__main__":
    unittest.main()
