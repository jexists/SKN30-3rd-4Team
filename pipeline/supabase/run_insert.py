"""Parquet/JSONL 임베딩 폴더를 재개 가능한 배치로 `kb_chunks_v2`에 적재한다."""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import (
    ROOT,
    iter_jsonl,
    load_pipeline_config,
    relative_to_root,
    sha256_file,
    utc_now_iso,
    write_json_atomic,
)

_CONFIG = load_pipeline_config()
TABLE = str(_CONFIG["supabase"]["table"])
DEFAULT_BATCH_SIZE = int(_CONFIG["supabase"]["insert_batch_size"])
DEFAULT_CAPACITY_SAFETY_FACTOR = float(_CONFIG["supabase"].get("capacity_safety_factor", 1.25))
LOG_DIR = ROOT / "pipeline" / "logs" / "insert"
LEDGER_PATH = LOG_DIR / "ingest_state.sqlite3"


def supported_files(input_dir: Path) -> list[Path]:
    files = sorted(input_dir.glob("*.parquet")) + sorted(input_dir.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"Parquet/JSONL 입력 없음: {input_dir}")
    return files


def iter_rows(path: Path, offset: int = 0) -> Iterable[dict[str, Any]]:
    if path.suffix == ".parquet":
        import pyarrow.parquet as pq

        data = pq.read_table(path).to_pydict()
        for index in range(offset, len(data["chunk_id"])):
            metadata_value = data.get("metadata_json", [None] * len(data["chunk_id"]))[index]
            yield {
                "chunk_id": data["chunk_id"][index],
                "content": data["content"][index],
                "metadata": json.loads(metadata_value) if isinstance(metadata_value, str) else metadata_value,
                "embedding": data["embedding"][index],
            }
        return
    for index, row in enumerate(iter_jsonl(path)):
        if index >= offset:
            yield row


def file_summary(path: Path) -> dict[str, Any]:
    count = 0
    dimensions: set[int] = set()
    seen: set[str] = set()
    duplicates = 0
    for row in iter_rows(path):
        count += 1
        chunk_id = str(row.get("chunk_id") or "")
        content = str(row.get("content") or "")
        vector = row.get("embedding")
        if not chunk_id or not content or vector is None:
            raise ValueError(f"필수 필드 누락: {path} row={count}")
        if chunk_id in seen:
            duplicates += 1
        seen.add(chunk_id)
        dimensions.add(len(vector))
    if dimensions != {1024}:
        raise ValueError(f"벡터 차원 불일치: {path}: {sorted(dimensions)}")
    if duplicates:
        raise ValueError(f"파일 내부 중복 chunk_id: {path}: {duplicates}")
    return {"path": relative_to_root(path), "sha256": sha256_file(path), "rows": count, "dimension": 1024}


def init_ledger(path: Path = LEDGER_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        create table if not exists ingest_state (
            experiment_id text not null,
            input_file text not null,
            run_id text,
            input_dir text,
            input_sha256 text not null,
            total_records integer not null,
            committed_records integer not null default 0,
            next_offset integer not null default 0,
            status text not null,
            last_error text,
            updated_at text not null,
            primary key (experiment_id, input_file)
        )
        """
    )
    columns = {row[1] for row in connection.execute("pragma table_info(ingest_state)")}
    for name in ("run_id", "input_dir"):
        if name not in columns:
            connection.execute(f"alter table ingest_state add column {name} text")
    connection.commit()
    return connection


def append_log(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": utc_now_iso(), **event}, ensure_ascii=False) + "\n")


def database_url(cli_value: str | None) -> str | None:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env", override=False)
    except ImportError:  # pragma: no cover - requirements 설치 전 dry import
        pass
    value = (
        cli_value
        or os.getenv("SUPABASE_DB_URL")
        or os.getenv("DATABASE_URL")
        or os.getenv("DB_URL")
    )
    return value.replace("postgresql+psycopg://", "postgresql://", 1) if value else None


def manifest_identity(manifest: dict[str, Any]) -> dict[str, Any]:
    model = manifest.get("model") or {}
    return {
        "experiment_id": manifest.get("experiment_id"),
        "dataset_version": manifest.get("dataset_version"),
        "input_manifest_sha256": manifest.get("input_manifest_sha256"),
        "model_id": model.get("id"),
        "model_revision": model.get("revision"),
        "dimension": model.get("dimension"),
    }


def validate_table_columns(columns: dict[str, str]) -> None:
    required = {
        "experiment_id": "text",
        "chunk_id": "text",
        "content": "text",
        "embedding": "vector(1024)",
        "metadata": "jsonb",
    }
    mismatches = {
        name: {"expected": expected, "actual": columns.get(name)}
        for name, expected in required.items()
        if columns.get(name) != expected
    }
    if mismatches:
        raise RuntimeError(f"kb_chunks_v2 컬럼 불일치: {mismatches}")


def connect_and_preflight(
    url: str,
    experiment_id: str,
    manifest: dict[str, Any],
    *,
    register_experiment: bool = True,
):
    import psycopg

    connection = psycopg.connect(url)
    try:
        with connection.cursor() as cursor:
            cursor.execute("select extversion from pg_extension where extname='vector'")
            if cursor.fetchone() is None:
                raise RuntimeError("pgvector extension이 없습니다. schema.sql을 먼저 적용하세요.")
            cursor.execute(
                "select to_regclass('public.kb_chunks_v2'), "
                "to_regprocedure('public.match_kb_chunks_v2(vector,integer,text)')"
            )
            table, function = cursor.fetchone()
            if table is None or function is None:
                raise RuntimeError("kb_chunks_v2 또는 match_kb_chunks_v2가 없습니다. schema.sql을 적용하세요.")
            cursor.execute(
                "select a.attname, format_type(a.atttypid, a.atttypmod) "
                "from pg_attribute a "
                "where a.attrelid = 'public.kb_chunks_v2'::regclass "
                "and a.attnum > 0 and not a.attisdropped"
            )
            validate_table_columns(dict(cursor.fetchall()))
            cursor.execute(
                "select manifest from public.kb_experiments_v2 where experiment_id=%s",
                (experiment_id,),
            )
            existing_row = cursor.fetchone()
            if existing_row:
                existing = existing_row[0]
                if isinstance(existing, str):
                    existing = json.loads(existing)
                if manifest_identity(existing) != manifest_identity(manifest):
                    raise RuntimeError(
                        "같은 experiment_id에 다른 데이터/모델 manifest가 이미 등록되어 있습니다."
                    )
            elif register_experiment:
                cursor.execute(
                    "insert into public.kb_experiments_v2 (experiment_id, manifest) "
                    "values (%s, %s::jsonb)",
                    (experiment_id, json.dumps(manifest, ensure_ascii=False)),
                )
        if register_experiment:
            connection.commit()
        else:
            connection.rollback()
        return connection
    except Exception:
        connection.rollback()
        connection.close()
        raise


def relation_sizes(connection: Any) -> dict[str, int]:
    with connection.cursor() as cursor:
        cursor.execute(
            "select pg_total_relation_size(c.oid), pg_relation_size(c.oid), "
            "case when c.reltoastrelid = 0 then 0 else pg_total_relation_size(c.reltoastrelid) end, "
            "pg_indexes_size(c.oid), pg_database_size(current_database()) "
            "from pg_class c where c.oid = 'public.kb_chunks_v2'::regclass"
        )
        total, heap, toast, indexes, database = cursor.fetchone()
    return {
        "total": total,
        "heap": heap,
        "toast": toast,
        "indexes": indexes,
        "database": database,
    }


def insert_batch(connection: Any, experiment_id: str, rows: list[dict[str, Any]]) -> int:
    values = [
        (
            experiment_id,
            row["chunk_id"],
            row["content"],
            "[" + ",".join(str(float(value)) for value in row["embedding"]) + "]",
            json.dumps(row.get("metadata") or {}, ensure_ascii=False),
        )
        for row in rows
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            "insert into public.kb_chunks_v2 (experiment_id, chunk_id, content, embedding, metadata) "
            "values (%s, %s, %s, %s::vector, %s::jsonb) "
            "on conflict (experiment_id, chunk_id) do nothing",
            values,
        )
        inserted = int(cursor.rowcount)
        if inserted < 0:
            raise RuntimeError("DB driver가 실제 삽입 행 수를 반환하지 않았습니다.")
    connection.commit()
    return inserted


def capacity_gate_measurement(
    *,
    before: dict[str, int],
    after: dict[str, int],
    total_rows: int,
    sampled_rows: int,
    inserted_rows: int,
    db_capacity_bytes: int,
    safety_factor: float,
) -> dict[str, Any]:
    if inserted_rows <= 0:
        raise ValueError("용량 측정에는 실제 신규 삽입 행이 필요합니다.")
    delta = max(0, after["total"] - before["total"])
    bytes_per_row = delta / inserted_rows
    predicted_total = bytes_per_row * total_rows
    remaining_rows = max(0, total_rows - sampled_rows)
    predicted_remaining = bytes_per_row * remaining_rows
    predicted_remaining_with_safety = math.ceil(predicted_remaining * safety_factor)
    remaining_capacity = max(0, int(db_capacity_bytes) - after["database"])
    return {
        "sample_rows": sampled_rows,
        "measurement_rows_this_run": inserted_rows,
        "relation_delta_bytes": delta,
        "bytes_per_row": bytes_per_row,
        "predicted_total_bytes": predicted_total,
        "predicted_remaining_bytes": predicted_remaining,
        "safety_factor": safety_factor,
        "predicted_remaining_with_safety_bytes": predicted_remaining_with_safety,
        "db_capacity_bytes": int(db_capacity_bytes),
        "remaining_capacity_bytes": remaining_capacity,
        "passed": predicted_remaining_with_safety <= remaining_capacity,
    }


def capacity_resume_recheck(
    saved_gate: dict[str, Any],
    *,
    current_database_bytes: int,
    current_experiment_rows: int,
) -> dict[str, Any]:
    capacity = int(saved_gate["db_capacity_bytes"])
    required = int(saved_gate["predicted_remaining_with_safety_bytes"])
    required_sample_rows = int(saved_gate["sample_rows"])
    currently_remaining = max(0, capacity - current_database_bytes)
    return {
        "db_capacity_bytes": capacity,
        "database_size_before_resume_bytes": current_database_bytes,
        "remaining_capacity_before_resume_bytes": currently_remaining,
        "required_remaining_with_safety_bytes": required,
        "required_sample_rows": required_sample_rows,
        "current_experiment_rows": current_experiment_rows,
        "sample_rows_present": current_experiment_rows >= required_sample_rows,
        "passed": (
            required <= currently_remaining
            and current_experiment_rows >= required_sample_rows
        ),
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    safety_factor = float(
        getattr(args, "capacity_safety_factor", DEFAULT_CAPACITY_SAFETY_FACTOR)
    )
    if args.batch_size < 1:
        raise ValueError("--batch-size는 1 이상이어야 합니다.")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit은 1 이상이어야 합니다.")
    if args.db_capacity_bytes is not None and args.db_capacity_bytes < 1:
        raise ValueError("--db-capacity-bytes는 1 이상이어야 합니다.")
    if safety_factor < 1.0:
        raise ValueError("--capacity-safety-factor는 1.0 이상이어야 합니다.")
    input_dir = args.input_dir.resolve()
    manifest_path = input_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("experiment_id") != args.experiment_id:
        raise ValueError(
            f"experiment_id 불일치: manifest={manifest.get('experiment_id')}, cli={args.experiment_id}"
        )
    summaries = [file_summary(path) for path in supported_files(input_dir)]
    total_rows = sum(item["rows"] for item in summaries)
    dry_run_result = {
        "input_dir": relative_to_root(input_dir),
        "experiment_id": args.experiment_id,
        "files": len(summaries),
        "rows": total_rows,
        "dimension": 1024,
        "batches": math_ceil(min(total_rows, args.limit or total_rows), args.batch_size),
        "file_summaries": summaries,
    }
    url = None if getattr(args, "skip_db_preflight", False) else database_url(args.database_url)
    if args.dry_run:
        if url:
            connection = connect_and_preflight(
                url,
                args.experiment_id,
                manifest,
                register_experiment=False,
            )
            connection.close()
            dry_run_result["database_preflight"] = "passed"
        else:
            dry_run_result["database_preflight"] = "skipped_no_database_url"
        return dry_run_result
    if not url:
        raise RuntimeError("DB_URL/SUPABASE_DB_URL/DATABASE_URL 또는 --database-url이 필요합니다.")
    if args.limit is not None and args.db_capacity_bytes is None:
        raise RuntimeError(
            "capacity gate에는 Supabase 프로젝트의 총 DB 허용량을 "
            "--db-capacity-bytes로 명시해야 합니다."
        )
    gate_path = ROOT / "reports" / "legal_api_v2" / "ingest" / f"{args.experiment_id}_capacity_gate.json"
    saved_gate: dict[str, Any] | None = None
    if args.resume and args.limit is None:
        if not gate_path.exists():
            raise RuntimeError("전체 재개 전 5,000건 capacity gate 결과가 필요합니다.")
        gate = json.loads(gate_path.read_text(encoding="utf-8"))
        saved_gate = gate.get("capacity_gate", {})
        if not saved_gate.get("passed"):
            raise RuntimeError("capacity gate가 통과하지 않아 전체 적재를 중단합니다.")

    run_id = utc_now_iso().replace(":", "").replace("+00:00", "Z")
    general_log = LOG_DIR / f"{run_id}.jsonl"
    success_log = LOG_DIR / f"{run_id}_success.jsonl"
    failed_log = LOG_DIR / f"{run_id}_failed.jsonl"
    connection = connect_and_preflight(url, args.experiment_id, manifest)
    before = relation_sizes(connection)
    ledger = init_ledger()
    committed_this_run = 0
    inserted_this_run = 0
    dataset_offset = 0
    target_rows = min(total_rows, args.limit) if args.limit is not None else total_rows
    started = time.perf_counter()
    active_key: tuple[str, str] | None = None
    try:
        capacity_recheck = None
        if saved_gate is not None:
            with connection.cursor() as cursor:
                cursor.execute(
                    "select count(*) from public.kb_chunks_v2 where experiment_id=%s",
                    (args.experiment_id,),
                )
                current_experiment_rows = int(cursor.fetchone()[0])
            capacity_recheck = capacity_resume_recheck(
                saved_gate,
                current_database_bytes=before["database"],
                current_experiment_rows=current_experiment_rows,
            )
            if not capacity_recheck["passed"]:
                raise RuntimeError("전체 재개 직전 DB 용량 재검사에 실패했습니다.")
        for path, summary in zip(supported_files(input_dir), summaries):
            allowed_rows = allowed_rows_for_file(dataset_offset, summary["rows"], args.limit)
            if allowed_rows <= 0:
                break
            key = (args.experiment_id, relative_to_root(path))
            active_key = key
            current = ledger.execute(
                "select input_sha256, next_offset from ingest_state where experiment_id=? and input_file=?",
                key,
            ).fetchone()
            if current and current[0] != summary["sha256"]:
                raise ValueError(f"ledger 입력 hash 불일치: {path}")
            if current and not args.resume:
                raise RuntimeError(f"기존 ledger가 있습니다. 재개하려면 --resume을 사용하세요: {path}")
            offset = int(current[1]) if current and args.resume else 0
            if offset >= allowed_rows:
                dataset_offset += summary["rows"]
                active_key = None
                continue
            ledger.execute(
                "insert into ingest_state "
                "(experiment_id, input_file, run_id, input_dir, input_sha256, total_records, "
                "committed_records, next_offset, status, last_error, updated_at) "
                "values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "on conflict(experiment_id,input_file) do update set "
                "run_id=excluded.run_id, input_dir=excluded.input_dir, status='running', "
                "last_error=null, updated_at=excluded.updated_at",
                (
                    *key,
                    run_id,
                    relative_to_root(input_dir),
                    summary["sha256"],
                    summary["rows"],
                    offset,
                    offset,
                    "running",
                    None,
                    utc_now_iso(),
                ),
            )
            ledger.commit()
            batch = []
            for row in iter_rows(path, offset):
                if offset >= allowed_rows:
                    break
                batch.append(row)
                target_batch = min(args.batch_size, allowed_rows - offset)
                if len(batch) < target_batch:
                    continue
                inserted_this_run += _commit_with_retry(
                    connection, args.experiment_id, batch, args.max_retries, failed_log
                )
                offset += len(batch)
                committed_this_run += len(batch)
                ledger.execute(
                    "update ingest_state set committed_records=?, next_offset=?, status='running', updated_at=? "
                    "where experiment_id=? and input_file=?",
                    (offset, offset, utc_now_iso(), *key),
                )
                ledger.commit()
                append_log(success_log, {"file": key[1], "end_offset": offset, "rows": len(batch)})
                elapsed = max(time.perf_counter() - started, 1e-9)
                completed_total = min(dataset_offset + offset, target_rows)
                speed = committed_this_run / elapsed
                remaining = max(0, target_rows - completed_total)
                eta = remaining / speed if speed else 0
                print(
                    f"{completed_total}/{target_rows} rows, remaining={remaining}, "
                    f"file={path.name}, {speed:.1f}/s, ETA={eta:.0f}s"
                )
                batch = []
            if batch and offset < allowed_rows:
                allowed = min(len(batch), allowed_rows - offset)
                batch = batch[:allowed]
                inserted_this_run += _commit_with_retry(
                    connection, args.experiment_id, batch, args.max_retries, failed_log
                )
                offset += len(batch)
                committed_this_run += len(batch)
                append_log(success_log, {"file": key[1], "end_offset": offset, "rows": len(batch)})
            status = "completed" if offset >= summary["rows"] else "partial"
            ledger.execute(
                "update ingest_state set committed_records=?, next_offset=?, status=?, updated_at=? "
                "where experiment_id=? and input_file=?",
                (offset, offset, status, utc_now_iso(), *key),
            )
            ledger.commit()
            dataset_offset += summary["rows"]
            active_key = None
        after = relation_sizes(connection)
        result = {
            **dry_run_result,
            "inserted_or_seen": committed_this_run,
            "actually_inserted_this_run": inserted_this_run,
            "size_before": before,
            "size_after": after,
        }
        if capacity_recheck is not None:
            result["capacity_recheck"] = capacity_recheck
        if args.limit is not None:
            if inserted_this_run == 0:
                if gate_path.exists():
                    existing_gate = json.loads(gate_path.read_text(encoding="utf-8"))
                    result["capacity_gate"] = existing_gate["capacity_gate"]
                    result["capacity_gate_reused"] = True
                    append_log(general_log, {"event": "completed", "result": result})
                    return result
                raise RuntimeError(
                    "새로 측정된 표본 행이 0건이라 용량을 추정할 수 없습니다. "
                    "새 experiment_id로 표본 적재하거나 DB 크기를 수동 검증하세요."
                )
            sampled_rows = min(total_rows, args.limit)
            result["capacity_gate"] = capacity_gate_measurement(
                before=before,
                after=after,
                total_rows=total_rows,
                sampled_rows=sampled_rows,
                inserted_rows=inserted_this_run,
                db_capacity_bytes=int(args.db_capacity_bytes),
                safety_factor=safety_factor,
            )
            report_path = ROOT / "reports" / "legal_api_v2" / "ingest" / f"{args.experiment_id}_capacity_gate.json"
            write_json_atomic(report_path, result)
        append_log(general_log, {"event": "completed", "result": result})
        return result
    except Exception as exc:
        if active_key is not None:
            ledger.execute(
                "update ingest_state set status='failed', last_error=?, updated_at=? "
                "where experiment_id=? and input_file=?",
                (str(exc), utc_now_iso(), *active_key),
            )
            ledger.commit()
        append_log(general_log, {"event": "failed", "error": str(exc)})
        raise
    finally:
        ledger.close()
        connection.close()


def _commit_with_retry(
    connection: Any,
    experiment_id: str,
    batch: list[dict[str, Any]],
    retries: int,
    failed_log: Path,
) -> int:
    for attempt in range(retries + 1):
        try:
            return insert_batch(connection, experiment_id, batch)
        except Exception as exc:
            connection.rollback()
            if attempt >= retries:
                append_log(failed_log, {"first_chunk_id": batch[0]["chunk_id"], "last_chunk_id": batch[-1]["chunk_id"], "error": str(exc)})
                raise
            time.sleep(2**attempt)


def math_ceil(value: int, divisor: int) -> int:
    return (value + divisor - 1) // divisor


def allowed_rows_for_file(dataset_offset: int, file_rows: int, limit: int | None) -> int:
    if limit is None:
        return file_rows
    return min(file_rows, max(0, limit - dataset_offset))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--database-url")
    parser.add_argument(
        "--skip-db-preflight",
        action="store_true",
        help="dry-run에서 파일만 검사하고 DB 읽기를 생략",
    )
    parser.add_argument("--db-capacity-bytes", type=int)
    parser.add_argument(
        "--capacity-safety-factor",
        type=float,
        default=DEFAULT_CAPACITY_SAFETY_FACTOR,
    )
    parser.add_argument("--max-retries", type=int, default=4)
    return parser.parse_args()


def main() -> None:
    result = run(parse_args())
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
