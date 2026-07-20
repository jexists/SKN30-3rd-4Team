from __future__ import annotations

import unittest

from pipeline.validate_manifests import core_manifest_errors, ocr_manifest_errors


class ManifestContractTests(unittest.TestCase):
    def test_embedding_contract_requires_lineage_model_and_versions(self) -> None:
        manifest = {
            "pipeline": "legal_rag_v2/run_embedding",
            "started_at": "start",
            "finished_at": "finish",
            "duration_seconds": 1,
            "git_commit": "abc",
            "runtime_versions": {"python": "3.12"},
            "settings": {},
            "input_manifest_sha256": "hash",
            "model": {"id": "model", "revision": "rev"},
            "library_versions": {"torch": "1"},
        }
        self.assertEqual(core_manifest_errors(manifest), [])
        del manifest["input_manifest_sha256"]
        self.assertIn("missing:input_manifest_sha256", core_manifest_errors(manifest))

    def test_ocr_contract_uses_engine_and_image_hashes(self) -> None:
        manifest = {
            "pipeline": "legal_rag_v2/sample_ocr",
            "run_id": "run",
            "finished_at": "finish",
            "engine": {"name": "tesseract.js", "version": "7"},
            "totals": {},
            "items": [{"image_sha256": "hash"}],
        }
        self.assertEqual(ocr_manifest_errors(manifest), [])


if __name__ == "__main__":
    unittest.main()
