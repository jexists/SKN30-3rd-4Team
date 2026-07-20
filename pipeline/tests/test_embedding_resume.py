from __future__ import annotations

import json
import shutil
import unittest
import uuid

from pipeline.common import ROOT
from pipeline.embedding.run_embedding import embed_file


class FakeModel:
    def __init__(self, fail_on_call: int | None = None) -> None:
        self.calls = 0
        self.fail_on_call = fail_on_call

    def encode(self, texts, **_kwargs):
        import numpy as np

        self.calls += 1
        if self.calls == self.fail_on_call:
            raise RuntimeError("simulated interruption")
        return np.ones((len(texts), 3), dtype=np.float32)


class EmbeddingResumeTests(unittest.TestCase):
    def test_resume_uses_last_completed_part(self) -> None:
        try:
            import pyarrow.parquet as pq
        except (ImportError, OSError) as exc:
            self.skipTest(f"pyarrow unavailable: {exc}")

        root = ROOT / ".tmp_legal_pipeline_tests" / uuid.uuid4().hex
        root.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, root, True)
        input_path = root / "chunks.jsonl"
        with input_path.open("w", encoding="utf-8") as stream:
            for index in range(5):
                stream.write(
                    json.dumps(
                        {
                            "chunk_id": f"chunk-{index}",
                            "content": f"본문 {index}",
                            "metadata": {"record_id": "record-1"},
                        },
                        ensure_ascii=False,
                    )
                    + "\n"
                )

        output_path = root / "embedded.parquet"
        parts_dir = root / "_parts"
        log_path = root / "events.jsonl"
        model_info = {
            "id": "fake/model",
            "dimension": 3,
            "normalize": True,
            "document_prefix": "",
        }
        with self.assertRaisesRegex(RuntimeError, "simulated interruption"):
            embed_file(
                input_path,
                output_path,
                parts_dir,
                model=FakeModel(fail_on_call=2),
                model_info=model_info.copy(),
                batch_size=2,
                part_size=2,
                limit=None,
                log_path=log_path,
            )

        checkpoint = json.loads((parts_dir / "checkpoint.json").read_text(encoding="utf-8"))
        self.assertEqual(checkpoint["next_line"], 2)
        self.assertEqual(checkpoint["parts"][0]["start_line"], 0)
        self.assertEqual(checkpoint["parts"][0]["end_line"], 2)
        self.assertEqual(checkpoint["parts"][0]["vector_dimension"], 3)

        result = embed_file(
            input_path,
            output_path,
            parts_dir,
            model=FakeModel(),
            model_info=model_info.copy(),
            batch_size=2,
            part_size=2,
            limit=None,
            log_path=log_path,
        )
        self.assertEqual(result["rows"], 5)
        self.assertEqual(pq.ParquetFile(output_path).metadata.num_rows, 5)
