from __future__ import annotations

import json
import shutil
import unittest
import uuid
from pathlib import Path

from pipeline.common import ROOT
from pipeline.preprocess.build_final_corpus import build_corpus


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def record(content: str, **metadata) -> dict:
    return {"page_content": content, "metadata": metadata}


class BuildFinalCorpusTests(unittest.TestCase):
    def test_filters_and_minimal_metadata(self) -> None:
        test_tmp_root = ROOT / ".tmp" / "pipeline-tests" / uuid.uuid4().hex
        test_tmp_root.mkdir(parents=True, exist_ok=False)
        self.addCleanup(shutil.rmtree, test_tmp_root, True)
        root = test_tmp_root
        api_dir = root / "api"
        pdf_dir = root / "pdf"
        output_dir = root / "out"
        image_path = root / "images.jsonl"

        with self.subTest("workspace fixture"):
            write_jsonl(
                api_dir / "eflaw.jsonl",
                [
                    record(
                        "유효한 법령 본문입니다.",
                        source_type="statute",
                        source_id="law-1",
                        record_id="law-1:article:1",
                        doc_title="테스트법",
                        source_org="법무부",
                        doc_year="2026",
                        authority="binding",
                        issue="법령",
                        section="article",
                        article="제1조",
                        article_no="1",
                        article_title="목적",
                        effective_date="20260101",
                        raw_sha256_original="drop-me",
                        images=[{"id": 1}],
                    ),
                    record(
                        "제2조의2",
                        source_type="statute",
                        source_id="law-1",
                        record_id="law-1:article:2-2",
                        doc_title="테스트법",
                        source_org="법무부",
                        doc_year="2026",
                        authority="binding",
                        issue="법령",
                        section="article",
                        article="제2조의2",
                    ),
                    record(
                        "삭제 조문",
                        source_type="statute",
                        source_id="law-1",
                        record_id="law-1:deleted",
                        doc_title="테스트법",
                        section="article",
                        is_deleted=True,
                    ),
                    record(
                        "제1장 총칙",
                        source_type="statute",
                        source_id="law-1",
                        record_id="law-1:heading",
                        doc_title="테스트법",
                        section="heading",
                    ),
                ],
            )
            write_jsonl(
                api_dir / "prec_중개.jsonl",
                [
                    record(
                        "검색에 포함할 판례 요지",
                        source_type="precedent",
                        source_id="case-1",
                        record_id="case-1:holding",
                        doc_title="손해배상",
                        source_org="대법원",
                        doc_year="2025",
                        authority="binding",
                        issue="중개",
                        section="holding",
                        court="대법원",
                        case_no="2025다1",
                        decision_date="20250101",
                        judgment_type="판결",
                        relevance_level="candidate",
                        matched_terms=["drop-me"],
                    ),
                    record(
                        "제외할 판례",
                        source_type="precedent",
                        source_id="case-2",
                        record_id="case-2:holding",
                        doc_title="무관 판례",
                        section="holding",
                        relevance_level="excluded",
                    ),
                ],
            )
            write_jsonl(
                pdf_dir / "guide.jsonl",
                [
                    record(
                        "PDF 안내서 본문",
                        source_type="guide",
                        source_id="guide-1",
                        record_id="guide-1:page:1",
                        doc_title="안내서",
                        source_org="기관",
                        doc_year="2026",
                        authority="persuasive",
                        issue="안내",
                        section="page",
                        pdf_page=1,
                        book_page=1,
                        pdf_md5="drop-me",
                    )
                ],
            )
            write_jsonl(
                image_path,
                [
                    record(
                        "",
                        source_type="statute",
                        source_id="law-1",
                        record_id="law-1:article:1:image:1",
                        parent_record_id="law-1:article:1",
                        doc_title="테스트법",
                        section="annex",
                        image_id="img-1",
                        image_url="https://example.test/image",
                    )
                ],
            )

            manifest = build_corpus(
                api_dir=api_dir,
                pdf_dir=pdf_dir,
                image_jsonl=image_path,
                output_dir=output_dir,
                dataset_version="test-v2",
            )

            self.assertEqual(manifest["totals"]["input_records"], 7)
            self.assertEqual(manifest["totals"]["output_records"], 4)
            self.assertEqual(manifest["totals"]["short_records_kept"], 4)
            self.assertEqual(manifest["totals"]["image_records_excluded"], 1)
            self.assertEqual(manifest["exclusions"]["counts"]["is_deleted"], 1)
            self.assertEqual(manifest["exclusions"]["counts"]["heading"], 1)
            self.assertEqual(manifest["exclusions"]["counts"]["relevance_excluded"], 1)

            rows = []
            for path in output_dir.glob("*.jsonl"):
                rows.extend(json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
            short = next(row for row in rows if row["metadata"]["record_id"] == "law-1:article:2-2")
            self.assertEqual(short["page_content"], "제2조의2")
            statute = next(row for row in rows if row["metadata"]["record_id"] == "law-1:article:1")
            self.assertNotIn("raw_sha256_original", statute["metadata"])
            self.assertNotIn("images", statute["metadata"])
            pdf = next(row for row in rows if row["metadata"]["source_type"] == "guide")
            self.assertEqual(pdf["metadata"]["pdf_page"], 1)
            self.assertNotIn("pdf_md5", pdf["metadata"])


if __name__ == "__main__":
    unittest.main()
