from __future__ import annotations

import argparse
import json
import shutil
import unittest
import uuid

from pipeline.common import ROOT
from pipeline.chunking.run_chunking import make_chunk_id, split_text
from pipeline.embedding.run_embedding import InputRow, write_part
from pipeline.evaluation.retrieval_eval import load_corpus
from pipeline.supabase.run_insert import run


class ParquetDryRunTests(unittest.TestCase):
    def test_embedding_parquet_to_ingest_dry_run(self) -> None:
        try:
            import numpy as np
            import pyarrow  # noqa: F401
        except (ImportError, OSError) as exc:
            self.skipTest(f"numpy/pyarrow가 없거나 Windows 정책에 차단됨: {exc}")
        root = ROOT / ".tmp_legal_pipeline_tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        experiment_id = "testv2_cs500_ov50_fake"
        (root / "manifest.json").write_text(
            json.dumps({"experiment_id": experiment_id}), encoding="utf-8"
        )
        metadata = {"record_id": "record-1", "source_type": "statute"}
        chunks = split_text("제1조 첫 번째 본문입니다. " * 40, 120, 12)
        rows = [
            InputRow(
                index,
                make_chunk_id(metadata["record_id"], index, content),
                content,
                json.dumps(metadata, ensure_ascii=False),
            )
            for index, content in enumerate(chunks)
        ]
        write_part(
            root / "fixture.parquet",
            rows,
            np.zeros((len(rows), 1024), dtype=np.float32),
            1024,
        )
        result = run(
            argparse.Namespace(
                input_dir=root,
                experiment_id=experiment_id,
                batch_size=500,
                limit=None,
                resume=False,
                dry_run=True,
                database_url=None,
                skip_db_preflight=True,
                db_capacity_bytes=500 * 1024 * 1024,
                max_retries=0,
            )
        )
        self.assertEqual(result["rows"], len(chunks))
        self.assertEqual(result["dimension"], 1024)
        self.assertEqual(result["database_preflight"], "skipped_no_database_url")
        matrix, chunk_ids, contents, loaded_metadata = load_corpus(root)
        self.assertEqual(matrix.shape, (len(chunks), 1024))
        self.assertEqual(len(chunk_ids), len(contents))
        self.assertEqual(loaded_metadata[0]["record_id"], "record-1")


if __name__ == "__main__":
    unittest.main()
