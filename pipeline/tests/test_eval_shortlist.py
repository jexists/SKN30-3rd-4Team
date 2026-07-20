from __future__ import annotations

import unittest

from pipeline.evaluation.select_shortlist import choose_shortlist


class EvaluationShortlistTests(unittest.TestCase):
    def test_close_second_model_is_included(self) -> None:
        rows = [
            {"experiment_id": "first", "recall_at_5": "0.80", "ndcg_at_10": "0.70", "chunks": "10"},
            {"experiment_id": "second", "recall_at_5": "0.77", "ndcg_at_10": "0.67", "chunks": "10"},
            {"experiment_id": "third", "recall_at_5": "0.60", "ndcg_at_10": "0.80", "chunks": "10"},
        ]
        selected, rationale = choose_shortlist(rows)
        self.assertEqual([row["experiment_id"] for row in selected], ["first", "second"])
        self.assertTrue(rationale["second_model_included"])

    def test_clear_winner_runs_grid_alone(self) -> None:
        rows = [
            {"experiment_id": "first", "recall_at_5": "0.90", "ndcg_at_10": "0.80", "chunks": "10"},
            {"experiment_id": "second", "recall_at_5": "0.70", "ndcg_at_10": "0.75", "chunks": "10"},
        ]
        selected, rationale = choose_shortlist(rows)
        self.assertEqual([row["experiment_id"] for row in selected], ["first"])
        self.assertFalse(rationale["second_model_included"])


if __name__ == "__main__":
    unittest.main()
