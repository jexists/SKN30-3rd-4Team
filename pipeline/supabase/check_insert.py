"""로컬 임베딩과 `kb_chunks_v2` 적재 결과를 대조한다."""

from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT
from pipeline.supabase.run_insert import (
    file_summary,
    database_url,
    iter_rows,
    manifest_identity,
    supported_files,
)


def vector_max_abs_diff(local: list[float], remote: list[float]) -> float | None:
    if len(local) != len(remote):
        return None
    return max((abs(float(left) - float(right)) for left, right in zip(local, remote)), default=0.0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--database-url")
    parser.add_argument("--sample-size", type=int, default=10)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    if args.sample_size < 1:
        raise ValueError("--sample-size는 1 이상이어야 합니다.")
    import psycopg

    url = database_url(args.database_url)
    if not url:
        raise RuntimeError("DB_URL/SUPABASE_DB_URL/DATABASE_URL 또는 --database-url이 필요합니다.")
    files = supported_files(args.input_dir)
    local_manifest = json.loads((args.input_dir / "manifest.json").read_text(encoding="utf-8"))
    local_count = sum(file_summary(path)["rows"] for path in files)
    randomizer = random.Random(args.seed)
    sample = []
    seen = 0
    for path in files:
        for row in iter_rows(path):
            seen += 1
            if len(sample) < args.sample_size:
                sample.append(row)
            else:
                replacement = randomizer.randrange(seen)
                if replacement < args.sample_size:
                    sample[replacement] = row
    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select manifest from public.kb_experiments_v2 where experiment_id=%s",
            (args.experiment_id,),
        )
        manifest_row = cursor.fetchone()
        db_manifest = manifest_row[0] if manifest_row else None
        if isinstance(db_manifest, str):
            db_manifest = json.loads(db_manifest)
        manifest_matches = bool(
            db_manifest and manifest_identity(db_manifest) == manifest_identity(local_manifest)
        )
        cursor.execute(
            "select count(*), count(distinct chunk_id), count(*) filter (where content=''), "
            "count(*) filter (where embedding is null or metadata is null), "
            "count(*) filter (where embedding is not null and vector_dims(embedding) <> 1024) "
            "from public.kb_chunks_v2 where experiment_id=%s",
            (args.experiment_id,),
        )
        db_count, unique_count, empty_count, null_count, dimension_mismatch = cursor.fetchone()
        mismatches = 0
        vector_mismatches = 0
        max_vector_abs_diff = 0.0
        for row in sample:
            cursor.execute(
                "select content, metadata, embedding::real[] from public.kb_chunks_v2 "
                "where experiment_id=%s and chunk_id=%s",
                (args.experiment_id, row["chunk_id"]),
            )
            db_row = cursor.fetchone()
            if not db_row:
                mismatches += 1
                vector_mismatches += 1
                continue
            if db_row[0] != row["content"] or db_row[1] != row.get("metadata"):
                mismatches += 1
            difference = vector_max_abs_diff(row["embedding"], list(db_row[2]))
            if difference is None or difference > 1e-6:
                vector_mismatches += 1
            if difference is not None:
                max_vector_abs_diff = max(max_vector_abs_diff, difference)
    result = {
        "experiment_id": args.experiment_id,
        "local_count": local_count,
        "db_count": db_count,
        "unique_chunk_ids": unique_count,
        "empty_content": empty_count,
        "null_fields": null_count,
        "vector_dimension_mismatch": dimension_mismatch,
        "sample_mismatches": mismatches,
        "vector_sample_mismatches": vector_mismatches,
        "max_vector_abs_diff": max_vector_abs_diff,
        "vector_abs_tolerance": 1e-6,
        "sample_size": len(sample),
        "sample_seed": args.seed,
        "manifest_matches": manifest_matches,
        "passed": (
            local_count == db_count == unique_count
            and not empty_count
            and not null_count
            and not dimension_mismatch
            and not mismatches
            and not vector_mismatches
            and manifest_matches
        ),
    }
    output = ROOT / "reports" / "legal_api_v2" / "ingest" / f"{args.experiment_id}_reconciliation.csv"
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(result))
        writer.writeheader()
        writer.writerow(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
