from __future__ import annotations

import argparse
import json
import shutil
import unittest
import uuid
from unittest.mock import patch

from pipeline.common import ROOT
from pipeline.supabase import run_insert


class FakeConnection:
    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class SupabaseResumeFlowTests(unittest.TestCase):
    def test_sample_limit_is_not_added_again_on_resume(self) -> None:
        directory = ROOT / ".tmp_legal_pipeline_tests" / uuid.uuid4().hex
        input_dir = directory / "embedding"
        log_dir = directory / "logs"
        input_dir.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory, True)
        experiment_id = f"test_{uuid.uuid4().hex}_cs500_ov50_fake"
        manifest = {
            "experiment_id": experiment_id,
            "dataset_version": "test",
            "input_manifest_sha256": "input-hash",
            "model": {"id": "fake/model", "revision": "rev", "dimension": 1024},
        }
        (input_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
        with (input_dir / "fixture.jsonl").open("w", encoding="utf-8") as stream:
            for index in range(5):
                stream.write(
                    json.dumps(
                        {
                            "chunk_id": f"chunk-{index}",
                            "content": f"본문 {index}",
                            "metadata": {"record_id": f"record-{index}"},
                            "embedding": [0.0] * 1024,
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        gate_path = (
            ROOT
            / "reports/legal_api_v2/ingest"
            / f"{experiment_id}_capacity_gate.json"
        )
        self.addCleanup(gate_path.unlink, missing_ok=True)
        ledger_path = directory / "ledger.sqlite3"
        inserted: list[str] = []

        def fake_insert(_connection, _experiment_id, rows) -> int:
            inserted.extend(str(row["chunk_id"]) for row in rows)
            return len(rows)

        sizes = [
            {"total": 0, "heap": 0, "toast": 0, "indexes": 0, "database": 100},
            {"total": 300, "heap": 100, "toast": 100, "indexes": 100, "database": 400},
            {"total": 300, "heap": 100, "toast": 100, "indexes": 100, "database": 400},
            {"total": 300, "heap": 100, "toast": 100, "indexes": 100, "database": 400},
        ]

        def args(resume: bool) -> argparse.Namespace:
            return argparse.Namespace(
                input_dir=input_dir,
                experiment_id=experiment_id,
                batch_size=2,
                limit=3,
                resume=resume,
                dry_run=False,
                database_url="postgresql://fake",
                skip_db_preflight=False,
                db_capacity_bytes=10_000,
                capacity_safety_factor=1.25,
                max_retries=0,
            )

        real_init_ledger = run_insert.init_ledger
        with (
            patch.object(run_insert, "connect_and_preflight", return_value=FakeConnection()),
            patch.object(run_insert, "relation_sizes", side_effect=sizes),
            patch.object(run_insert, "insert_batch", side_effect=fake_insert),
            patch.object(run_insert, "init_ledger", side_effect=lambda: real_init_ledger(ledger_path)),
            patch.object(run_insert, "LOG_DIR", log_dir),
        ):
            first = run_insert.run(args(resume=False))
            second = run_insert.run(args(resume=True))

        self.assertEqual(inserted, ["chunk-0", "chunk-1", "chunk-2"])
        self.assertEqual(first["inserted_or_seen"], 3)
        self.assertEqual(second["inserted_or_seen"], 0)
        self.assertTrue(second["capacity_gate_reused"])
        connection = real_init_ledger(ledger_path)
        try:
            offset = connection.execute("select next_offset from ingest_state").fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(offset, 3)


if __name__ == "__main__":
    unittest.main()
