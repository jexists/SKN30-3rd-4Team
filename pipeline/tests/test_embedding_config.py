from __future__ import annotations

import unittest
import shutil
import uuid
from pathlib import Path

from pipeline.common import ROOT
from pipeline.embedding.run_embedding import (
    acquire_output_lock,
    build_experiment_id,
    derive_output_dir,
    release_output_lock,
    resolve_model,
)


class EmbeddingConfigTests(unittest.TestCase):
    def test_known_model_registry(self) -> None:
        model = resolve_model("kakao1513/KURE-legal-ft-v1")
        self.assertEqual(model["slug"], "kure-legal-ft-v1")
        self.assertEqual(model["dimension"], 1024)

    def test_smoke_output_is_separate(self) -> None:
        output = derive_output_dir(
            __import__("pathlib").Path("data/legal_api_v2/07_chunking_500_ov50"),
            "kure-v1",
            100,
        )
        self.assertEqual(output.name, "08_embedding_500_ov50_kure-v1_smoke100")

    def test_smoke_experiment_id_is_separate(self) -> None:
        manifest = {
            "dataset_version": "legalv2_test",
            "settings": {"chunk_size": 500, "chunk_overlap": 50},
        }
        full = build_experiment_id(
            manifest,
            "kure-v1",
            Path("08_embedding_500_ov50_kure-v1"),
            None,
        )
        smoke = build_experiment_id(
            manifest,
            "kure-v1",
            Path("08_embedding_500_ov50_kure-v1_smoke100_bs16"),
            100,
        )
        self.assertEqual(full, "legalv2_test_cs500_ov50_kure-v1")
        self.assertEqual(smoke, "legalv2_test_cs500_ov50_kure-v1_smoke100_bs16")

    def test_output_lock_prevents_concurrent_writer(self) -> None:
        directory = ROOT / ".tmp_legal_pipeline_tests" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory, True)
        lock_path = Path(directory) / ".embedding.lock"
        acquire_output_lock(lock_path, "run-a")
        with self.assertRaisesRegex(RuntimeError, "이미 실행 중"):
            acquire_output_lock(lock_path, "run-b")
        release_output_lock(lock_path, "run-a")
        self.assertFalse(lock_path.exists())


if __name__ == "__main__":
    unittest.main()
