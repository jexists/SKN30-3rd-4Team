from __future__ import annotations

import unittest

from pipeline.evaluation.select_winner import rank_rows


class EvaluationSelectionTests(unittest.TestCase):
    def test_recall_then_ndcg_then_chunks_then_size_then_latency(self) -> None:
        rows = [
            {"experiment_id": "low-recall", "recall_at_5": "0.7", "ndcg_at_10": "1"},
            {
                "experiment_id": "larger",
                "recall_at_5": "0.8",
                "ndcg_at_10": "0.6",
                "chunks": "200",
                "parquet_bytes": "200",
                "latency_p95_ms": "2",
            },
            {
                "experiment_id": "winner",
                "recall_at_5": "0.8",
                "ndcg_at_10": "0.6",
                "chunks": "100",
                "parquet_bytes": "300",
                "latency_p95_ms": "5",
            },
        ]
        self.assertEqual(
            [row["experiment_id"] for row in rank_rows(rows)],
            ["winner", "larger", "low-recall"],
        )


if __name__ == "__main__":
    unittest.main()
