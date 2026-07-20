"""청킹 JSONL을 로컬 모델로 임베딩해 재개 가능한 Parquet으로 저장한다."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import (
    ROOT,
    git_commit,
    iter_jsonl,
    load_pipeline_config,
    relative_to_root,
    runtime_versions,
    sha256_file,
    utc_now_iso,
    write_json_atomic,
)
from pipeline.embedding.preflight import run_embedding_preflight

_PIPELINE_CONFIG = load_pipeline_config()
_EMBEDDING_CONFIG = _PIPELINE_CONFIG["embedding"]
MODEL_NAME = str(_EMBEDDING_CONFIG["default_model"])
BATCH_SIZE = int(_EMBEDDING_CONFIG["batch_size"])
PART_SIZE = int(_EMBEDDING_CONFIG["part_size"])

MODEL_REGISTRY: dict[str, dict[str, Any]] = {}
for _slug, _settings in _EMBEDDING_CONFIG["models"].items():
    _model_id = str(_settings["id"])
    MODEL_REGISTRY[_model_id] = {
        "slug": _slug,
        "dimension": int(_settings["dimension"]),
        "revision": _settings.get("revision"),
        "normalize": bool(_settings.get("normalize", _EMBEDDING_CONFIG.get("normalize", True))),
        "query_prefix": str(_settings.get("query_prefix", "")),
        "document_prefix": str(_settings.get("document_prefix", "")),
    }


@dataclass
class InputRow:
    line_number: int
    chunk_id: str
    content: str
    metadata_json: str


def resolve_model(model_name: str) -> dict[str, Any]:
    if model_name in MODEL_REGISTRY:
        return {"id": model_name, **MODEL_REGISTRY[model_name]}
    slug = model_name.rsplit("/", 1)[-1].lower().replace("_", "-")
    return {
        "id": model_name,
        "slug": slug,
        "dimension": None,
        "normalize": True,
        "query_prefix": "",
        "document_prefix": "",
    }


def load_model(
    model_name: str,
    device: str | None = None,
    revision: str | None = None,
):
    try:
        import torch
        from sentence_transformers import SentenceTransformer
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError(
            "임베딩 의존성이 없습니다. pipeline/requirements.txt를 설치하세요."
        ) from exc
    selected_device = device or ("cuda" if torch.cuda.is_available() else "cpu")
    return (
        SentenceTransformer(model_name, device=selected_device, revision=revision),
        selected_device,
    )


def read_rows(path: Path, start_line: int = 0, limit: int | None = None) -> Iterable[InputRow]:
    emitted = 0
    for line_number, record in enumerate(iter_jsonl(path)):
        if line_number < start_line:
            continue
        if limit is not None and emitted >= limit:
            break
        content = str(record.get("content") or "").strip()
        chunk_id = str(record.get("chunk_id") or "")
        metadata = record.get("metadata") or {}
        if not content or not chunk_id:
            raise ValueError(f"필수 청크 필드 누락: {path} line={line_number + 1}")
        yield InputRow(
            line_number=line_number,
            chunk_id=chunk_id,
            content=content,
            metadata_json=json.dumps(metadata, ensure_ascii=False, separators=(",", ":")),
        )
        emitted += 1


def count_rows(path: Path) -> int:
    return sum(1 for _ in iter_jsonl(path))


def append_log(path: Path, event: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"at": utc_now_iso(), **event}, ensure_ascii=False) + "\n")


def process_is_alive(pid: int) -> bool:
    if os.name == "nt":  # Windows의 os.kill(pid, 0)은 생존 확인 용도가 아니다.
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        open_process.restype = wintypes.HANDLE
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [wintypes.HANDLE]
        close_handle.restype = wintypes.BOOL
        process_query_limited_information = 0x1000
        handle = open_process(process_query_limited_information, False, pid)
        if not handle:
            return False
        close_handle(handle)
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def acquire_output_lock(path: Path, run_id: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            with path.open("x", encoding="utf-8") as stream:
                json.dump({"pid": os.getpid(), "run_id": run_id, "created_at": utc_now_iso()}, stream)
            return
        except FileExistsError:
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                pid = int(existing["pid"])
            except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
                raise RuntimeError(f"손상된 임베딩 lock을 수동 확인하세요: {path}") from exc
            if process_is_alive(pid):
                raise RuntimeError(f"같은 출력 폴더 임베딩이 이미 실행 중입니다: pid={pid}, lock={path}")
            path.unlink()
    raise RuntimeError(f"임베딩 lock 획득 실패: {path}")


def release_output_lock(path: Path, run_id: str) -> None:
    if not path.exists():
        return
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if existing.get("run_id") == run_id:
        path.unlink()


def write_part(path: Path, rows: list[InputRow], vectors: Any, dimension: int) -> None:
    try:
        import pyarrow as pa
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover - 환경 의존
        raise RuntimeError("Parquet 저장에는 pyarrow가 필요합니다.") from exc
    vector_values = vectors.tolist() if hasattr(vectors, "tolist") else vectors
    if len(vector_values) != len(rows):
        raise ValueError("임베딩 개수가 입력 배치와 다릅니다.")
    if vector_values and len(vector_values[0]) != dimension:
        raise ValueError(f"벡터 차원 불일치: expected={dimension}, actual={len(vector_values[0])}")
    table = pa.table(
        {
            "chunk_id": [row.chunk_id for row in rows],
            "content": [row.content for row in rows],
            "metadata_json": [row.metadata_json for row in rows],
            "embedding": pa.array(vector_values, type=pa.list_(pa.float32(), dimension)),
        }
    )
    temporary = path.with_name(path.name + ".tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, temporary, compression="zstd")
    temporary.replace(path)


def merge_parts(parts: list[Path], output_path: Path) -> int:
    import pyarrow.parquet as pq

    temporary = output_path.with_name(output_path.name + ".tmp")
    writer = None
    total = 0
    try:
        for part in parts:
            table = pq.read_table(part)
            if writer is None:
                writer = pq.ParquetWriter(temporary, table.schema, compression="zstd")
            writer.write_table(table)
            total += table.num_rows
    finally:
        if writer is not None:
            writer.close()
    if writer is None:
        raise ValueError("병합할 part가 없습니다.")
    if pq.ParquetFile(temporary).metadata.num_rows != total:
        raise ValueError("최종 Parquet 행 수 검증 실패")
    temporary.replace(output_path)
    return total


def checkpoint_part_path(item: str | dict[str, Any]) -> Path:
    """구형 문자열 checkpoint와 v2 메타데이터 checkpoint를 모두 읽는다."""
    relative_path = item["path"] if isinstance(item, dict) else item
    return ROOT / relative_path


def encode_with_oom_retry(model: Any, texts: list[str], batch_size: int, normalize: bool):
    current = batch_size
    while True:
        try:
            return model.encode(
                texts,
                batch_size=current,
                normalize_embeddings=normalize,
                show_progress_bar=False,
                convert_to_numpy=True,
            )
        except RuntimeError as exc:
            if "out of memory" not in str(exc).lower() or current <= 1:
                raise
            current = max(1, current // 2)


def model_revision(model: Any) -> str | None:
    try:
        return model._first_module().auto_model.config._commit_hash
    except (AttributeError, IndexError):
        return None


def library_versions() -> dict[str, str]:
    import pyarrow
    import sentence_transformers
    import torch

    return {
        "sentence_transformers": sentence_transformers.__version__,
        "torch": torch.__version__,
        "pyarrow": pyarrow.__version__,
    }


def embed_file(
    input_path: Path,
    output_path: Path,
    parts_dir: Path,
    *,
    model: Any,
    model_info: dict[str, Any],
    batch_size: int,
    part_size: int,
    limit: int | None,
    log_path: Path,
) -> dict[str, Any]:
    input_hash = sha256_file(input_path)
    total_input = count_rows(input_path)
    target_rows = min(total_input, limit) if limit is not None else total_input
    checkpoint_path = parts_dir / "checkpoint.json"
    checkpoint = {
        "input_sha256": input_hash,
        "model_id": model_info["id"],
        "model_revision": model_info.get("revision"),
        "target_rows": target_rows,
        "next_line": 0,
        "parts": [],
    }
    if checkpoint_path.exists():
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        expected = (input_hash, model_info["id"], target_rows)
        actual = (
            checkpoint.get("input_sha256"),
            checkpoint.get("model_id"),
            checkpoint.get("target_rows"),
        )
        if actual != expected:
            raise ValueError(f"checkpoint 설정/입력 불일치: {checkpoint_path}")
        checkpoint_revision = checkpoint.get("model_revision")
        if checkpoint_revision and checkpoint_revision != model_info.get("revision"):
            raise ValueError(f"checkpoint 모델 revision 불일치: {checkpoint_path}")
        checkpoint["model_revision"] = model_info.get("revision")

    processed = int(checkpoint["next_line"])
    started = time.perf_counter()
    batch: list[InputRow] = []
    remaining = target_rows - processed
    for row in read_rows(input_path, start_line=processed, limit=remaining):
        batch.append(row)
        if len(batch) < part_size:
            continue
        vectors = encode_with_oom_retry(
            model,
            [model_info["document_prefix"] + item.content for item in batch],
            batch_size,
            model_info["normalize"],
        )
        dimension = len(vectors[0])
        expected_dimension = model_info.get("dimension")
        if expected_dimension is not None and dimension != expected_dimension:
            raise ValueError(f"모델 차원 불일치: registry={expected_dimension}, actual={dimension}")
        model_info["dimension"] = dimension
        part = parts_dir / f"part_{processed:09d}_{processed + len(batch):09d}.parquet"
        write_part(part, batch, vectors, dimension)
        checkpoint["parts"].append(
            {
                "path": relative_to_root(part),
                "input_sha256": input_hash,
                "start_line": batch[0].line_number,
                "end_line": batch[-1].line_number + 1,
                "chunk_count": len(batch),
                "model_id": model_info["id"],
                "model_revision": model_info.get("revision"),
                "vector_dimension": dimension,
            }
        )
        processed += len(batch)
        checkpoint["next_line"] = processed
        write_json_atomic(checkpoint_path, checkpoint)
        elapsed = max(time.perf_counter() - started, 1e-9)
        speed = (processed - (target_rows - remaining)) / elapsed
        eta = (target_rows - processed) / speed if speed > 0 else None
        print(f"  {input_path.name}: {processed}/{target_rows} ({speed:.2f} chunks/s, ETA {eta:.0f}s)")
        batch = []

    if batch:
        vectors = encode_with_oom_retry(
            model,
            [model_info["document_prefix"] + item.content for item in batch],
            batch_size,
            model_info["normalize"],
        )
        dimension = len(vectors[0])
        expected_dimension = model_info.get("dimension")
        if expected_dimension is not None and dimension != expected_dimension:
            raise ValueError(f"모델 차원 불일치: registry={expected_dimension}, actual={dimension}")
        model_info["dimension"] = dimension
        part = parts_dir / f"part_{processed:09d}_{processed + len(batch):09d}.parquet"
        write_part(part, batch, vectors, dimension)
        checkpoint["parts"].append(
            {
                "path": relative_to_root(part),
                "input_sha256": input_hash,
                "start_line": batch[0].line_number,
                "end_line": batch[-1].line_number + 1,
                "chunk_count": len(batch),
                "model_id": model_info["id"],
                "model_revision": model_info.get("revision"),
                "vector_dimension": dimension,
            }
        )
        processed += len(batch)
        checkpoint["next_line"] = processed
        write_json_atomic(checkpoint_path, checkpoint)

    parts = [checkpoint_part_path(item) for item in checkpoint["parts"]]
    merged = merge_parts(parts, output_path)
    if merged != target_rows:
        raise ValueError(f"행 수 불일치: expected={target_rows}, actual={merged}")
    append_log(log_path, {"event": "file_completed", "file": input_path.name, "rows": merged})
    return {
        "input": relative_to_root(input_path),
        "input_sha256": input_hash,
        "output": relative_to_root(output_path),
        "output_sha256": sha256_file(output_path),
        "rows": merged,
    }


def derive_output_dir(input_dir: Path, model_slug: str, limit: int | None) -> Path:
    match = __import__("re").search(r"07_chunking_(\d+)_ov(\d+)$", input_dir.name)
    if not match:
        raise ValueError(f"입력 폴더명에서 chunk 설정을 읽을 수 없습니다: {input_dir.name}")
    suffix = f"_smoke{limit}" if limit is not None else ""
    return input_dir.parent / f"08_embedding_{match.group(1)}_ov{match.group(2)}_{model_slug}{suffix}"


def build_experiment_id(
    input_manifest: dict[str, Any],
    model_slug: str,
    output_dir: Path,
    limit: int | None,
) -> str:
    chunk_size = int(input_manifest["settings"]["chunk_size"])
    overlap = int(input_manifest["settings"]["chunk_overlap"])
    base = f"{input_manifest.get('dataset_version')}_cs{chunk_size}_ov{overlap}_{model_slug}"
    if limit is None:
        return base
    canonical_name = f"08_embedding_{chunk_size}_ov{overlap}_{model_slug}"
    suffix = ""
    if output_dir.name.startswith(canonical_name):
        suffix = output_dir.name[len(canonical_name) :].strip("_")
    suffix = suffix or f"smoke{limit}"
    suffix = __import__("re").sub(r"[^0-9A-Za-z_-]+", "-", suffix).strip("-")
    if not suffix:
        raise ValueError("smoke experiment suffix를 만들 수 없습니다.")
    return f"{base}_{suffix}"


def run_embedding(
    input_dir: Path,
    *,
    model_name: str,
    output_dir: Path | None,
    batch_size: int,
    part_size: int,
    device: str | None,
    revision: str | None,
    limit: int | None,
) -> dict[str, Any]:
    run_started_at = utc_now_iso()
    run_started_clock = time.perf_counter()
    model_info = resolve_model(model_name)
    output_dir = output_dir or derive_output_dir(input_dir, model_info["slug"], limit)
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = utc_now_iso().replace(":", "").replace("+00:00", "Z")
    lock_path = output_dir / ".embedding.lock"
    log_path = ROOT / "pipeline" / "logs" / "embedding" / f"{run_id}.jsonl"
    state_path = output_dir / "run_state.json"
    input_manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    input_files = sorted(input_dir.glob("*.jsonl"))
    if not input_files:
        raise FileNotFoundError(f"청킹 JSONL 없음: {input_dir}")
    acquire_output_lock(lock_path, run_id)
    resume_from_rows = 0
    for checkpoint_path in (output_dir / "_parts").glob("*/checkpoint.json"):
        checkpoint_value = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        resume_from_rows += int(checkpoint_value.get("next_line", 0))
    try:
        total_rows = int((input_manifest.get("totals") or {}).get("chunks") or 0)
        if limit is not None:
            total_rows = min(total_rows, limit)
        preflight = run_embedding_preflight(
            output_dir=output_dir,
            total_rows=total_rows,
            processed_rows=min(resume_from_rows, total_rows),
            requested_device=device,
        )
        append_log(log_path, {"event": "run_started", "input_dir": relative_to_root(input_dir), "model": model_name})
        write_json_atomic(
            state_path,
            {
                "status": "running",
                "run_id": run_id,
                "started_at": run_started_at,
                "model_id": model_name,
                "resume_from_rows": resume_from_rows,
                "input_dir": relative_to_root(input_dir),
                "log": relative_to_root(log_path),
                "preflight": preflight,
            },
        )
    except Exception:
        release_output_lock(lock_path, run_id)
        raise
    try:
        requested_revision = revision or model_info.get("revision")
        model, selected_device = load_model(model_name, device, requested_revision)
        model_info["revision"] = model_revision(model)
        files = []
        remaining = limit
        for input_path in input_files:
            if remaining is not None and remaining <= 0:
                break
            file_limit = remaining
            item = embed_file(
                input_path,
                output_dir / f"{input_path.stem}.parquet",
                output_dir / "_parts" / input_path.stem,
                model=model,
                model_info=model_info,
                batch_size=batch_size,
                part_size=part_size,
                limit=file_limit,
                log_path=log_path,
            )
            files.append(item)
            if remaining is not None:
                remaining -= item["rows"]
        manifest = {
            "pipeline": "legal_rag_v2/run_embedding",
            "started_at": run_started_at,
            "finished_at": utc_now_iso(),
            "duration_seconds": time.perf_counter() - run_started_clock,
            "run_id": run_id,
            "log": relative_to_root(log_path),
            "git_commit": git_commit(),
            "dataset_version": input_manifest.get("dataset_version"),
            "input_manifest_sha256": sha256_file(input_dir / "manifest.json"),
            "model": {**model_info, "device": selected_device},
            "library_versions": library_versions(),
            "runtime_versions": runtime_versions(
                ("sentence-transformers", "torch", "pyarrow")
            ),
            "settings": {"batch_size": batch_size, "part_size": part_size, "limit": limit},
            "preflight": preflight,
            "experiment_id": build_experiment_id(
                input_manifest, model_info["slug"], output_dir, limit
            ),
            "totals": {"rows": sum(item["rows"] for item in files), "files": len(files)},
            "files": files,
        }
        write_json_atomic(output_dir / "manifest.json", manifest)
        write_json_atomic(
            state_path,
            {
                "status": "complete",
                "run_id": run_id,
                "started_at": run_started_at,
                "finished_at": manifest["finished_at"],
                "model_id": model_name,
                "rows": manifest["totals"]["rows"],
                "resume_from_rows": resume_from_rows,
                "log": relative_to_root(log_path),
            },
        )
        append_log(log_path, {"event": "run_completed", "rows": manifest["totals"]["rows"]})
        return manifest
    except Exception as exc:
        write_json_atomic(
            state_path,
            {
                "status": "failed",
                "run_id": run_id,
                "started_at": run_started_at,
                "failed_at": utc_now_iso(),
                "model_id": model_name,
                "error": str(exc),
                "log": relative_to_root(log_path),
            },
        )
        append_log(
            log_path,
            {"event": "run_failed", "error": str(exc), "traceback": traceback.format_exc()},
        )
        raise
    finally:
        release_output_lock(lock_path, run_id)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--model", default=MODEL_NAME)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE)
    parser.add_argument("--part-size", type=int, default=PART_SIZE)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"))
    parser.add_argument("--revision", help="Hugging Face commit hash/tag. 생략 시 실제 commit을 manifest에 기록")
    parser.add_argument("--limit", type=int)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.batch_size < 1 or args.part_size < 1:
        raise ValueError("--batch-size와 --part-size는 1 이상이어야 합니다.")
    manifest = run_embedding(
        args.input_dir,
        model_name=args.model,
        output_dir=args.output_dir,
        batch_size=args.batch_size,
        part_size=args.part_size,
        device=args.device,
        revision=args.revision,
        limit=args.limit,
    )
    print(json.dumps(manifest["totals"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
