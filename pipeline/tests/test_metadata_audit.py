from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from pipeline.audit_metadata import audit_metadata
from pipeline.common import ROOT


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")


class MetadataAuditTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = ROOT / ".tmp_legal_pipeline_tests" / uuid.uuid4().hex
        self.final_dir = self.root / "06_final"
        self.chunk_dir = self.root / "07_chunking_500_ov50"
        self.addCleanup(shutil.rmtree, self.root, True)
        self.metadata = {
            "source_type": "statute",
            "source_id": "law-1",
            "record_id": "law-1:article:1",
            "doc_title": "주택임대차보호법",
            "section": "article",
            "article_no": "1",
        }
        write_jsonl(self.final_dir / "manifest.json", [{"kind": "final"}])
        write_jsonl(self.chunk_dir / "manifest.json", [{"kind": "chunk"}])

    def test_valid_minimal_metadata_and_unchanged_chunk_copy(self) -> None:
        write_jsonl(
            self.final_dir / "api_eflaw.jsonl",
            [{"page_content": "본문", "metadata": self.metadata}],
        )
        write_jsonl(
            self.chunk_dir / "api_eflaw.jsonl",
            [{"chunk_id": "chunk-1", "content": "본문", "metadata": self.metadata}],
        )
        report = audit_metadata(self.final_dir, [self.chunk_dir])
        self.assertTrue(report["passed"])
        self.assertEqual(report["errors"]["count"], 0)
        self.assertEqual(len(report["audited_manifests"]), 2)

    def test_rejects_extra_pipeline_metadata_and_changed_chunk_metadata(self) -> None:
        final_metadata = {**self.metadata, "dataset_version": "forbidden"}
        chunk_metadata = {**self.metadata, "article_no": "2"}
        write_jsonl(
            self.final_dir / "api_eflaw.jsonl",
            [{"page_content": "본문", "metadata": final_metadata}],
        )
        write_jsonl(
            self.chunk_dir / "api_eflaw.jsonl",
            [{"chunk_id": "chunk-1", "content": "본문", "metadata": chunk_metadata}],
        )
        report = audit_metadata(self.final_dir, [self.chunk_dir])
        self.assertFalse(report["passed"])
        self.assertEqual(report["errors"]["by_code"]["unexpected_metadata_keys"], 1)
        self.assertEqual(report["errors"]["by_code"]["metadata_changed_after_chunking"], 1)


if __name__ == "__main__":
    unittest.main()
