"""표본 OCR과 부모 텍스트의 정규화 중복률을 계산하고 채택 여부를 기록한다."""

from __future__ import annotations

import csv
import json
import re
import statistics
import sys
import unicodedata
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, iter_jsonl, relative_to_root, utc_now_iso, write_json_atomic

OCR_RESULTS = ROOT / "data/legal_api_v2/05_ocr_json/review/ocr_results.jsonl"
OCR_MANIFEST = ROOT / "data/legal_api_v2/05_ocr_json/ocr_manifest.json"
SOURCE_TEXT_DIR = ROOT / "data/legal_api_v2/05_processed_document_json/full_20260719_163719/text"
ASSESSMENT_MANIFEST = ROOT / "reports/legal_api_v2/ocr_assessment/manifest.json"
REPORT_PATH = ROOT / "reports/legal_api_v2/ocr_assessment/ocr_comparison.csv"


def normalized_text(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).lower()
    return "".join(character for character in value if character.isalnum())


def ngram_coverage(needle: str, haystack: str, size: int = 3) -> float:
    needle = normalized_text(needle)
    haystack = normalized_text(haystack)
    if not needle:
        return 0.0
    if len(needle) < size:
        return float(needle in haystack)
    grams = {needle[index : index + size] for index in range(len(needle) - size + 1)}
    return sum(gram in haystack for gram in grams) / len(grams)


def normalized_numbers(value: str) -> set[str]:
    numbers = set()
    for token in re.findall(r"\d[\d,.\s]*\d|\d", value):
        normalized = re.sub(r"\D", "", token)
        if len(normalized) >= 2:
            numbers.add(normalized)
    return numbers


def hangul_terms(value: str) -> set[str]:
    return set(re.findall(r"[가-힣]{2,}", unicodedata.normalize("NFKC", value)))


def set_coverage(values: set[str], parent_normalized: str) -> float | None:
    if not values:
        return None
    return sum(value in parent_normalized for value in values) / len(values)


def evaluate() -> dict[str, Any]:
    parent_text = {}
    for path in sorted(SOURCE_TEXT_DIR.glob("*.jsonl")):
        for row in iter_jsonl(path):
            metadata = row.get("metadata") or {}
            parent_text[str(metadata.get("record_id") or "")] = str(row.get("page_content") or "")
    ocr_manifest = json.loads(OCR_MANIFEST.read_text(encoding="utf-8"))
    provenance = {item["record_id"]: item for item in ocr_manifest["items"]}
    rows = []
    for record in iter_jsonl(OCR_RESULTS):
        metadata = record.get("metadata") or {}
        record_id = str(metadata.get("record_id") or "")
        parent_id = str(metadata.get("parent_record_id") or "")
        ocr_text = str(record.get("page_content") or "")
        parent = parent_text.get(parent_id)
        if parent is None:
            raise ValueError(f"부모 텍스트 없음: {parent_id}")
        parent_normalized = normalized_text(parent)
        numbers = normalized_numbers(ocr_text)
        terms = hangul_terms(ocr_text)
        item = provenance[record_id]
        rows.append(
            {
                "record_id": record_id,
                "parent_record_id": parent_id,
                "source_type": metadata.get("source_type"),
                "ocr_confidence": item.get("ocr_confidence"),
                "ocr_chars": len(ocr_text),
                "parent_chars": len(parent),
                "normalized_trigram_coverage": ngram_coverage(ocr_text, parent),
                "numeric_tokens": len(numbers),
                "numeric_token_coverage": set_coverage(numbers, parent_normalized),
                "hangul_terms": len(terms),
                "hangul_term_coverage": set_coverage(terms, parent_normalized),
                "manual_new_search_value": False,
                "adoption_status": "duplicate_or_no_search_value",
            }
        )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with REPORT_PATH.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    numeric_values = [row["numeric_token_coverage"] for row in rows if row["numeric_token_coverage"] is not None]
    term_values = [row["hangul_term_coverage"] for row in rows if row["hangul_term_coverage"] is not None]
    comparison = {
        "evaluated_at": utc_now_iso(),
        "report": relative_to_root(REPORT_PATH),
        "samples": len(rows),
        "mean_ocr_confidence": statistics.fmean(float(row["ocr_confidence"]) for row in rows),
        "mean_normalized_trigram_coverage": statistics.fmean(row["normalized_trigram_coverage"] for row in rows),
        "mean_numeric_token_coverage": statistics.fmean(numeric_values) if numeric_values else None,
        "mean_hangul_term_coverage": statistics.fmean(term_values) if term_values else None,
        "new_search_value_samples": 0,
        "adopted_samples": 0,
        "decision": "표본 OCR은 부모 텍스트와 중복되거나 빈 양식이며 새로운 검색 가치가 없어 baseline corpus에 추가하지 않는다.",
    }
    ocr_manifest["comparison"] = comparison
    write_json_atomic(OCR_MANIFEST, ocr_manifest)

    assessment = json.loads(ASSESSMENT_MANIFEST.read_text(encoding="utf-8"))
    assessment["sample_ocr_status"] = "completed_tesseract_js_7_0_0"
    assessment["ocr_manifest"] = relative_to_root(OCR_MANIFEST)
    assessment["ocr_comparison"] = comparison
    assessment["baseline_decision"] = "exclude_images_sample_ocr_no_new_search_value"
    write_json_atomic(ASSESSMENT_MANIFEST, assessment)
    return comparison


def main() -> None:
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
