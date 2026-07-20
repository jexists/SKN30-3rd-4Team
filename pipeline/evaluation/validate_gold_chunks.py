"""평가 gold record가 모든 후보 청킹 폴더에 실제로 존재하는지 검증한다."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import (
    ROOT,
    iter_jsonl,
    load_pipeline_config,
    relative_to_root,
    utc_now_iso,
    write_json_atomic,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--questions",
        type=Path,
        default=ROOT / "pipeline/evaluation/questions.jsonl",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=ROOT / "data/legal_api_v2",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/question_audit",
    )
    return parser.parse_args()


def candidate_chunk_dirs(data_dir: Path) -> list[tuple[int, int, Path]]:
    config = load_pipeline_config()["chunking"]
    ratio = float(config["overlap_ratio"])
    return [
        (int(size), round(int(size) * ratio), data_dir / f"07_chunking_{size}_ov{round(int(size) * ratio)}")
        for size in config["candidates"]
    ]


def record_chunk_counts(chunk_dir: Path) -> tuple[Counter[str], int, int]:
    counts: Counter[str] = Counter()
    chunk_ids: set[str] = set()
    total = 0
    for path in sorted(chunk_dir.glob("*.jsonl")):
        for row in iter_jsonl(path):
            total += 1
            chunk_id = str(row.get("chunk_id") or "")
            record_id = str((row.get("metadata") or {}).get("record_id") or "")
            if chunk_id:
                chunk_ids.add(chunk_id)
            if record_id:
                counts[record_id] += 1
    return counts, total, len(chunk_ids)


def build_rows(
    questions: list[dict[str, Any]],
    candidates: list[tuple[int, int, Path]],
) -> tuple[list[dict[str, Any]], list[str], list[dict[str, Any]]]:
    gold_ids = sorted(
        {str(record_id) for item in questions for record_id in item.get("gold_record_ids", [])}
    )
    rows: list[dict[str, Any]] = []
    missing: list[str] = []
    candidate_summaries: list[dict[str, Any]] = []
    for chunk_size, overlap, chunk_dir in candidates:
        if not chunk_dir.is_dir():
            missing.append(f"missing_chunk_dir:{relative_to_root(chunk_dir)}")
            continue
        counts, total_chunks, unique_chunk_ids = record_chunk_counts(chunk_dir)
        candidate_missing = []
        for record_id in gold_ids:
            count = counts.get(record_id, 0)
            rows.append(
                {
                    "chunk_size": chunk_size,
                    "overlap": overlap,
                    "record_id": record_id,
                    "chunk_count": count,
                    "present": count > 0,
                }
            )
            if count == 0:
                value = f"cs{chunk_size}:{record_id}"
                candidate_missing.append(record_id)
                missing.append(value)
        candidate_summaries.append(
            {
                "chunk_size": chunk_size,
                "overlap": overlap,
                "directory": relative_to_root(chunk_dir),
                "total_chunks": total_chunks,
                "unique_chunk_ids": unique_chunk_ids,
                "gold_records": len(gold_ids),
                "gold_records_present": len(gold_ids) - len(candidate_missing),
                "missing_gold_records": candidate_missing,
            }
        )
    return rows, missing, candidate_summaries


def main() -> None:
    args = parse_args()
    questions = list(iter_jsonl(args.questions))
    rows, missing, candidate_summaries = build_rows(
        questions,
        candidate_chunk_dirs(args.data_dir),
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "gold_chunk_coverage.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        if rows:
            writer.writeheader()
            writer.writerows(rows)
    summary = {
        "checked_at": utc_now_iso(),
        "questions": len(questions),
        "distinct_gold_record_ids": len(
            {str(record_id) for item in questions for record_id in item.get("gold_record_ids", [])}
        ),
        "candidate_summaries": candidate_summaries,
        "missing": missing,
        "report": relative_to_root(csv_path),
        "passed": not missing,
    }
    write_json_atomic(args.output_dir / "gold_chunk_coverage_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if missing:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
