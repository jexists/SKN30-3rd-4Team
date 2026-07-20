"""기존 kb_chunks를 변경하지 않고 법률 RAG v2 schema를 안전하게 적용한다."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, utc_now_iso, write_json_atomic
from pipeline.supabase.run_insert import database_url

SCHEMA_PATH = ROOT / "pipeline/supabase/schema.sql"


def validate_schema_sql(sql: str) -> None:
    lowered = re.sub(r"\s+", " ", sql.lower())
    forbidden = ("drop table", "truncate ", "alter table public.kb_chunks ", "delete from")
    matches = [token for token in forbidden if token in lowered]
    if matches:
        raise RuntimeError(f"schema.sql 금지 SQL 발견: {matches}")
    for required in (
        "create table if not exists public.kb_chunks_v2",
        "primary key (experiment_id, chunk_id)",
        "create or replace function public.match_kb_chunks_v2",
    ):
        if required not in lowered:
            raise RuntimeError(f"schema.sql 필수 구문 누락: {required}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--schema", type=Path, default=SCHEMA_PATH)
    parser.add_argument("--database-url")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    sql = args.schema.read_text(encoding="utf-8")
    validate_schema_sql(sql)
    if args.dry_run:
        print(json.dumps({"schema_valid": True, "statements": sql.count(";")}, indent=2))
        return
    url = database_url(args.database_url)
    if not url:
        raise RuntimeError("DB_URL/SUPABASE_DB_URL/DATABASE_URL 또는 --database-url이 필요합니다.")
    import psycopg

    with psycopg.connect(url) as connection, connection.cursor() as cursor:
        cursor.execute(
            "select to_regclass('public.kb_chunks'), "
            "to_regclass('public.kb_chunks_v2'), "
            "to_regclass('public.kb_experiments_v2')"
        )
        old_table_before, chunks_before, experiments_before = cursor.fetchone()
        cursor.execute(sql)
        cursor.execute(
            "select to_regclass('public.kb_chunks'), "
            "to_regclass('public.kb_chunks_v2'), "
            "to_regclass('public.kb_experiments_v2'), "
            "to_regprocedure('public.match_kb_chunks_v2(vector,integer,text)')"
        )
        old_table_after, chunks_after, experiments_after, function_after = cursor.fetchone()
        if old_table_before != old_table_after:
            raise RuntimeError("기존 kb_chunks 객체가 변경되었습니다. transaction을 중단합니다.")
        if chunks_after is None or experiments_after is None or function_after is None:
            raise RuntimeError("v2 schema 적용 후 객체 검증에 실패했습니다.")
    result = {
        "applied_at": utc_now_iso(),
        "schema": args.schema.relative_to(ROOT).as_posix(),
        "legacy_kb_chunks_existed_before": old_table_before is not None,
        "legacy_kb_chunks_unchanged": old_table_before == old_table_after,
        "legacy_kb_chunks_exists_after": old_table_after is not None,
        "kb_chunks_v2_existed_before": chunks_before is not None,
        "kb_experiments_v2_existed_before": experiments_before is not None,
        "kb_chunks_v2_ready": chunks_after is not None,
        "kb_experiments_v2_ready": experiments_after is not None,
        "match_kb_chunks_v2_ready": function_after is not None,
    }
    report = ROOT / "reports/legal_api_v2/ingest/schema_apply.json"
    write_json_atomic(report, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
