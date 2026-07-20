"""Supabase v2 schema 객체, 행 수, relation 크기를 읽기 전용으로 점검한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, utc_now_iso, write_json_atomic
from pipeline.supabase.run_insert import database_url, relation_sizes


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/legal_api_v2/ingest/schema_status.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    url = database_url(args.database_url)
    if not url:
        raise RuntimeError("DB_URL/SUPABASE_DB_URL/DATABASE_URL 또는 --database-url이 필요합니다.")
    import psycopg

    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select extversion from pg_extension where extname='vector'"
        )
        vector_row = cursor.fetchone()
        cursor.execute("select count(*) from public.kb_chunks_v2")
        chunk_rows = cursor.fetchone()[0]
        cursor.execute("select count(*) from public.kb_experiments_v2")
        experiment_rows = cursor.fetchone()[0]
        cursor.execute(
            "select indexdef from pg_indexes "
            "where schemaname='public' and indexname='kb_chunks_v2_embedding_hnsw_idx'"
        )
        index_row = cursor.fetchone()
        cursor.execute(
            "select to_regprocedure('public.match_kb_chunks_v2(vector,integer,text)')"
        )
        function_ready = cursor.fetchone()[0] is not None
        cursor.execute("select to_regclass('public.kb_chunks')")
        legacy_ready = cursor.fetchone()[0] is not None
        legacy = None
        if legacy_ready:
            cursor.execute("select count(*) from public.kb_chunks")
            legacy_rows = cursor.fetchone()[0]
            cursor.execute(
                "select pg_total_relation_size(c.oid), pg_relation_size(c.oid), "
                "case when c.reltoastrelid = 0 then 0 else pg_total_relation_size(c.reltoastrelid) end, "
                "pg_indexes_size(c.oid) "
                "from pg_class c where c.oid = 'public.kb_chunks'::regclass"
            )
            total, heap, toast, indexes = cursor.fetchone()
            legacy = {
                "rows": legacy_rows,
                "total": total,
                "heap": heap,
                "toast": toast,
                "indexes": indexes,
            }
        sizes = relation_sizes(connection)
    result = {
        "checked_at": utc_now_iso(),
        "pgvector_version": vector_row[0] if vector_row else None,
        "chunk_rows": chunk_rows,
        "experiment_rows": experiment_rows,
        "hnsw_index_ready": bool(index_row and "using hnsw" in index_row[0].lower()),
        "hnsw_index_definition": index_row[0] if index_row else None,
        "match_function_ready": function_ready,
        "legacy_kb_chunks_exists": legacy_ready,
        "legacy_kb_chunks": legacy,
        "sizes_bytes": sizes,
        "passed": bool(vector_row and index_row and function_ready),
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
