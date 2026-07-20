"""Pandas DLL을 실행할 수 없는 제한 환경용 EDA 보고서 생성기.

정식 분석 인터페이스는 `prechunk_eda.ipynb`와 `prechunk_eda.py`이다. 이 파일은
Windows 실행 정책이 Pandas 바이너리를 차단할 때 동일 핵심 CSV를 표준 라이브러리로
생성해 파이프라인 진행을 막지 않기 위한 fallback이다.
"""

from __future__ import annotations

import csv
import json
import math
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, iter_jsonl, relative_to_root, utc_now_iso, write_json_atomic

INPUT_DIR = ROOT / "data" / "legal_api_v2" / "06_final"
OUTPUT_DIR = ROOT / "reports" / "legal_api_v2" / "prechunk_eda"
THRESHOLDS = (300, 500, 800, 1000, 1500, 2000)
CANDIDATES = (300, 500, 800, 1000)


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    fields = fields or list(rows[0])
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def percentile(sorted_values: list[int], quantile: float) -> float:
    position = (len(sorted_values) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(sorted_values[lower])
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    rows = []
    metadata_fields: Counter[str] = Counter()
    for path in sorted(INPUT_DIR.glob("*.jsonl")):
        for record in iter_jsonl(path):
            metadata = record.get("metadata") or {}
            content = str(record.get("page_content") or "")
            metadata_fields.update(metadata.keys())
            rows.append(
                {
                    "file": path.name,
                    "record_id": metadata.get("record_id"),
                    "doc_title": metadata.get("doc_title"),
                    "source_type": metadata.get("source_type"),
                    "issue": metadata.get("issue"),
                    "section": metadata.get("section"),
                    "relevance_level": metadata.get("relevance_level"),
                    "char_count": len(content),
                    "page_content": content,
                }
            )
    if not rows:
        raise FileNotFoundError(f"JSONL 입력 없음: {INPUT_DIR}")
    lengths = sorted(row["char_count"] for row in rows)
    summary = {
        "count": len(rows),
        "min": min(lengths),
        "mean": statistics.fmean(lengths),
        "median": statistics.median(lengths),
        "p25": percentile(lengths, 0.25),
        "p75": percentile(lengths, 0.75),
        "p90": percentile(lengths, 0.90),
        "p95": percentile(lengths, 0.95),
        "p99": percentile(lengths, 0.99),
        "max": max(lengths),
        "empty_content": sum(length == 0 for length in lengths),
        "duplicate_record_id": len(rows) - len({row["record_id"] for row in rows}),
        "duplicate_content": len(rows) - len({row["page_content"] for row in rows}),
    }
    write_csv(OUTPUT_DIR / "summary.csv", [summary])

    for group_field, filename in (
        ("file", "length_by_file.csv"),
        ("source_type", "length_by_source_type.csv"),
    ):
        groups: dict[str, list[int]] = defaultdict(list)
        for row in rows:
            groups[str(row[group_field] or "<missing>")].append(row["char_count"])
        grouped_rows = [
            {
                group_field: key,
                "count": len(values),
                "min": min(values),
                "mean": statistics.fmean(values),
                "median": statistics.median(values),
                "max": max(values),
            }
            for key, values in sorted(groups.items())
        ]
        write_csv(OUTPUT_DIR / filename, grouped_rows)

    write_csv(
        OUTPUT_DIR / "records_over_threshold.csv",
        [
            {
                "threshold": threshold,
                "records_over": sum(length > threshold for length in lengths),
                "ratio": sum(length > threshold for length in lengths) / len(lengths),
            }
            for threshold in THRESHOLDS
        ],
    )
    write_csv(
        OUTPUT_DIR / "metadata_coverage.csv",
        [
            {"field": field, "present": count, "coverage": count / len(rows)}
            for field, count in sorted(metadata_fields.items())
        ],
    )
    review_fields = [
        "file", "record_id", "doc_title", "source_type", "section",
        "relevance_level", "char_count", "page_content",
    ]
    write_csv(OUTPUT_DIR / "longest_records.csv", sorted(rows, key=lambda x: x["char_count"], reverse=True)[:10], review_fields)
    write_csv(OUTPUT_DIR / "shortest_records.csv", sorted(rows, key=lambda x: x["char_count"])[:10], review_fields)
    write_csv(OUTPUT_DIR / "short_records_review.csv", [row for row in rows if row["char_count"] < 20], review_fields)

    category_rows = []
    for field in ("source_type", "issue", "section", "relevance_level"):
        counts = Counter(str(row[field] or "<missing>") for row in rows)
        category_rows.extend(
            {"field": field, "value": value, "count": count}
            for value, count in counts.most_common()
        )
    write_csv(OUTPUT_DIR / "category_counts.csv", category_rows)

    projection_rows = []
    for size in CANDIDATES:
        overlap = size // 10
        manifest_path = INPUT_DIR.parent / f"07_chunking_{size}_ov{overlap}" / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        chunks = manifest["totals"]["chunks"]
        projection_rows.append(
            {
                "chunk_size": size,
                "overlap": overlap,
                "estimated_chunks": chunks,
                "actual_chunks": chunks,
                "embedding_raw_gib": chunks * 1024 * 4 / 1024**3,
                "supabase_rows": chunks,
            }
        )
    write_csv(OUTPUT_DIR / "chunk_projection.csv", projection_rows)
    report = {
        "generated_at": utc_now_iso(),
        "input_dir": relative_to_root(INPUT_DIR),
        "records": len(rows),
        "length": summary,
        "chunk_candidates": projection_rows,
        "recommended_chunk_size": 500,
        "recommended_overlap": 50,
        "recommendation_reason": "최종 corpus 중앙값 431자 부근을 보존하는 기준선이며 네 후보의 실제 검색 평가로 최종 결정한다.",
        "tokenizer_analysis_completed": False,
        "runtime_note": "Pandas DLL이 Windows 애플리케이션 제어 정책에 차단되어 stdlib fallback으로 생성; tokenizer 분석은 모델 환경에서 재실행 필요",
    }
    write_json_atomic(OUTPUT_DIR / "report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
