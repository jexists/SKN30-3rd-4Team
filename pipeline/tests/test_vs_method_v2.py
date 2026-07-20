from __future__ import annotations

import unittest
from unittest.mock import patch

from pipeline.app.vs_method_v2 import (
    SearchSettings,
    VECTOR_DIMENSION,
    search_similar,
    settings_from_env,
    vector_literal,
)


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchall(self):
        return self.rows


class SequentialCursor(FakeCursor):
    def __init__(self, rows):
        super().__init__([])
        self.fetchone_rows = iter(rows)

    def fetchone(self):
        return next(self.fetchone_rows)


class SequentialConnection:
    def __init__(self, rows):
        self.cursor_instance = SequentialCursor(rows)

    def cursor(self):
        return self.cursor_instance


class FakeConnection:
    def __init__(self, rows):
        self.cursor_instance = FakeCursor(rows)

    def cursor(self):
        return self.cursor_instance


class VsMethodV2Tests(unittest.TestCase):
    def test_settings_use_registry_revision_and_require_experiment(self) -> None:
        settings = settings_from_env(
            {
                "LEGAL_RAG_V2_MODEL_ID": "nlpai-lab/KURE-v1",
                "LEGAL_RAG_V2_EXPERIMENT_ID": "legalv2_test_cs500_ov50_kure-v1",
                "LEGAL_RAG_V2_DEVICE": "cpu",
            }
        )
        self.assertEqual(settings.dimension, VECTOR_DIMENSION)
        self.assertEqual(
            settings.model_revision,
            "d14c8a9423946e268a0c9952fecf3a7aabd73bd9",
        )
        with self.assertRaises(RuntimeError):
            settings_from_env({"LEGAL_RAG_V2_MODEL_ID": "nlpai-lab/KURE-v1"})

    def test_vector_literal_checks_dimension(self) -> None:
        literal = vector_literal([0.0] * VECTOR_DIMENSION)
        self.assertTrue(literal.startswith("[0.0,0.0"))
        with self.assertRaises(ValueError):
            vector_literal([0.0])

    def test_search_is_experiment_isolated_and_flattens_metadata(self) -> None:
        config = SearchSettings(
            model_id="nlpai-lab/KURE-v1",
            model_revision="revision",
            experiment_id="exp-winner",
            device="cpu",
        )
        connection = FakeConnection(
            [
                ("chunk-a", "본문 A", {"record_id": "record-a"}, 0.72),
                ("chunk-b", "본문 B", {"record_id": "record-b"}, 0.10),
            ]
        )
        with patch(
            "pipeline.app.vs_method_v2._embed_query_cached",
            return_value=tuple([0.0] * VECTOR_DIMENSION),
        ):
            hits = search_similar(
                connection,
                "임차권등기명령",
                k=12,
                min_score=0.15,
                config=config,
            )
        self.assertEqual(hits[0]["record_id"], "record-a")
        self.assertEqual(hits[0]["chunk_id"], "chunk-a")
        self.assertEqual(len(hits), 1)
        sql, params = connection.cursor_instance.executed
        self.assertIn("match_kb_chunks_v2", sql)
        self.assertEqual(params[1:], (12, "exp-winner"))

    def test_ensure_schema_is_read_only_and_requires_winner_rows(self) -> None:
        from pipeline.app import vs_method_v2

        settings = SearchSettings(
            model_id="nlpai-lab/KURE-v1",
            model_revision="revision",
            experiment_id="exp-winner",
        )
        connection = SequentialConnection([("kb_chunks_v2", "match_kb_chunks_v2"), (1,), (10,)])
        with patch("pipeline.app.vs_method_v2.get_settings", return_value=settings):
            vs_method_v2.ensure_schema(connection)
        sql, _ = connection.cursor_instance.executed
        self.assertIn("kb_chunks_v2", sql)
        self.assertNotIn("create ", sql.lower())

        empty = SequentialConnection([("kb_chunks_v2", "match_kb_chunks_v2"), (1,), (0,)])
        with patch("pipeline.app.vs_method_v2.get_settings", return_value=settings):
            with self.assertRaises(RuntimeError):
                vs_method_v2.ensure_schema(empty)


if __name__ == "__main__":
    unittest.main()
