from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from pipeline.supabase.run_insert import (
    connect_and_preflight,
    manifest_identity,
    validate_table_columns,
)


class FakeCursor:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self.last_query = ""

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, query, _params=None) -> None:
        self.last_query = str(query)
        self.queries.append(self.last_query)

    def fetchone(self):
        if "pg_extension" in self.last_query:
            return ("0.8.0",)
        if "to_regclass" in self.last_query:
            return ("kb_chunks_v2", "match_kb_chunks_v2")
        if "select manifest" in self.last_query:
            return None
        raise AssertionError(self.last_query)

    def fetchall(self):
        return [
            ("experiment_id", "text"),
            ("chunk_id", "text"),
            ("content", "text"),
            ("embedding", "vector(1024)"),
            ("metadata", "jsonb"),
        ]


class FakeConnection:
    def __init__(self) -> None:
        self.cursor_value = FakeCursor()
        self.commits = 0
        self.rollbacks = 0
        self.closed = False

    def cursor(self):
        return self.cursor_value

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1

    def close(self) -> None:
        self.closed = True


class SupabasePreflightTests(unittest.TestCase):
    def test_manifest_identity_ignores_runtime_timestamps(self) -> None:
        base = {
            "experiment_id": "legalv2_cs500_ov50_kure-v1",
            "dataset_version": "legalv2",
            "input_manifest_sha256": "abc",
            "model": {"id": "model/a", "revision": "rev1", "dimension": 1024},
        }
        other = {**base, "finished_at": "later", "duration_seconds": 123}
        self.assertEqual(manifest_identity(base), manifest_identity(other))

    def test_vector_dimension_is_checked(self) -> None:
        valid = {
            "experiment_id": "text",
            "chunk_id": "text",
            "content": "text",
            "embedding": "vector(1024)",
            "metadata": "jsonb",
        }
        validate_table_columns(valid)
        with self.assertRaisesRegex(RuntimeError, "컬럼 불일치"):
            validate_table_columns({**valid, "embedding": "vector(768)"})

    def test_dry_preflight_does_not_register_experiment(self) -> None:
        connection = FakeConnection()
        fake_psycopg = SimpleNamespace(connect=lambda _url: connection)
        manifest = {
            "experiment_id": "exp",
            "model": {"id": "model", "revision": "rev", "dimension": 1024},
        }
        with patch.dict("sys.modules", {"psycopg": fake_psycopg}):
            result = connect_and_preflight(
                "postgresql://example",
                "exp",
                manifest,
                register_experiment=False,
            )
        self.assertIs(result, connection)
        self.assertEqual(connection.commits, 0)
        self.assertEqual(connection.rollbacks, 1)
        self.assertFalse(any("insert into" in query.lower() for query in connection.cursor_value.queries))


if __name__ == "__main__":
    unittest.main()
