"""`06_final`의 모든 JSONL을 결정적으로 청킹한다."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import time
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import (
    ROOT,
    atomic_text_writer,
    git_commit,
    iter_jsonl,
    load_pipeline_config,
    relative_to_root,
    runtime_versions,
    resolve_repo_path,
    sha256_file,
    utc_now_iso,
    write_json_atomic,
    write_jsonl_record,
)

_CONFIG = load_pipeline_config()
_CHUNK_CONFIG = _CONFIG["chunking"]
CHUNK_SIZE = int(_CHUNK_CONFIG["default_size"])
CHUNK_OVERLAP = round(CHUNK_SIZE * float(_CHUNK_CONFIG["overlap_ratio"]))
INPUT_DIR = resolve_repo_path(_CONFIG["paths"]["final_dir"])
SEPARATORS = ("\n\n", "\n제", "\n", "다. ", "요. ", ". ", "。", " ")
METHOD = "korean_legal_structure_recursive_character_v2_table_aware"


def _preferred_end(text: str, start: int, maximum_end: int, chunk_size: int) -> int:
    """최대 크기 안에서 가능한 한 법률 구조 경계 뒤를 선택한다."""
    minimum_end = min(maximum_end, start + max(chunk_size // 2, 1))
    window = text[minimum_end:maximum_end]
    for separator in SEPARATORS:
        position = window.rfind(separator)
        if position >= 0:
            return minimum_end + position + len(separator)
    return maximum_end


def _split_plain(text: str, chunk_size: int, overlap: int) -> list[str]:
    if chunk_size <= 0:
        raise ValueError("chunk_size는 1 이상이어야 합니다.")
    if overlap < 0 or overlap >= chunk_size:
        raise ValueError("chunk_overlap은 0 이상 chunk_size 미만이어야 합니다.")
    text = text.strip()
    if not text:
        return []
    if len(text) <= chunk_size:
        return [text]

    chunks: list[str] = []
    start = 0
    while start < len(text):
        maximum_end = min(len(text), start + chunk_size)
        end = (
            maximum_end
            if maximum_end == len(text)
            else _preferred_end(text, start, maximum_end, chunk_size)
        )
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        next_start = max(start + 1, end - overlap)
        # overlap 시작점이 단어 중간이면 가까운 공백/줄바꿈 뒤로 이동한다.
        boundary = re.search(r"[\s]", text[next_start : min(end, next_start + 40)])
        if boundary:
            next_start += boundary.end()
        start = next_start
    return chunks


def _is_table_separator(line: str) -> bool:
    cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
    return len(cells) >= 2 and all(re.fullmatch(r":?-{3,}:?", cell or "") for cell in cells)


def _table_segments(text: str) -> list[tuple[str, str, str]]:
    """(kind, content, preceding_title)로 Markdown 표 블록을 분리한다."""
    lines = text.splitlines()
    result: list[tuple[str, str, str]] = []
    plain: list[str] = []
    index = 0
    while index < len(lines):
        if "|" not in lines[index]:
            plain.append(lines[index])
            index += 1
            continue
        end = index
        while end < len(lines) and "|" in lines[end]:
            end += 1
        block = lines[index:end]
        if len(block) >= 2 and any(_is_table_separator(line) for line in block):
            title = next((line.strip() for line in reversed(plain) if line.strip()), "")
            if plain:
                result.append(("plain", "\n".join(plain).strip(), ""))
                plain = []
            result.append(("table", "\n".join(block).strip(), title))
        else:
            plain.extend(block)
        index = end
    if plain:
        result.append(("plain", "\n".join(plain).strip(), ""))
    return [item for item in result if item[1]]


def _split_table(table: str, title: str, chunk_size: int, overlap: int) -> list[str]:
    if len(table) <= chunk_size:
        return [table]
    lines = table.splitlines()
    separator_index = next((i for i, line in enumerate(lines) if _is_table_separator(line)), None)
    if separator_index is None:
        return _split_plain(table, chunk_size, overlap)
    header = "\n".join(lines[: separator_index + 1]).strip()
    prefix = "\n".join(part for part in (title, header) if part).strip()
    if len(prefix) >= chunk_size:
        prefix = header
    chunks: list[str] = []
    current = prefix
    for row in lines[separator_index + 1 :]:
        candidate = f"{current}\n{row}" if current else row
        if len(candidate) <= chunk_size:
            current = candidate
            continue
        if current and current != prefix:
            chunks.append(current.strip())
            current = prefix
            candidate = f"{current}\n{row}" if current else row
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            # 한 행 자체가 너무 길 때만 문자 경계 fallback을 허용한다.
            chunks.extend(_split_plain(candidate, chunk_size, overlap))
            current = prefix
    if current and current != prefix:
        chunks.append(current.strip())
    return chunks or _split_plain(table, chunk_size, overlap)


def split_text(text: str, chunk_size: int, overlap: int) -> list[str]:
    text = text.strip()
    if not text:
        return []
    segments = _table_segments(text)
    if not any(kind == "table" for kind, _, _ in segments):
        return _split_plain(text, chunk_size, overlap)
    chunks: list[str] = []
    for kind, content, title in segments:
        if kind == "table":
            chunks.extend(_split_table(content, title, chunk_size, overlap))
        else:
            chunks.extend(_split_plain(content, chunk_size, overlap))
    return [chunk for chunk in chunks if chunk]


def make_chunk_id(record_id: str, index: int, content: str) -> str:
    content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
    value = f"{record_id}\0{index}\0{content_hash}".encode("utf-8")
    return "chk_" + hashlib.sha256(value).hexdigest()


def _load_final_manifest(input_dir: Path) -> dict[str, Any]:
    path = input_dir / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"06_final manifest 없음: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def chunk_file(
    input_path: Path,
    output_path: Path,
    *,
    chunk_size: int,
    overlap: int,
    seen_chunk_ids: set[str],
) -> dict[str, Any]:
    source_records = 0
    chunks_written = 0
    lengths: list[int] = []
    source_counts: Counter[str] = Counter()
    with atomic_text_writer(output_path) as stream:
        for record in iter_jsonl(input_path):
            source_records += 1
            content = str(record.get("page_content") or "").strip()
            metadata = record.get("metadata") or {}
            if not content:
                raise ValueError(f"빈 원본 본문: {input_path} #{source_records}")
            record_id = str(metadata.get("record_id") or "")
            if not record_id:
                raise ValueError(f"record_id 누락: {input_path} #{source_records}")
            chunks = split_text(content, chunk_size, overlap)
            if not chunks:
                raise ValueError(f"청크가 생성되지 않음: {record_id}")
            for index, chunk in enumerate(chunks):
                chunk_id = make_chunk_id(record_id, index, chunk)
                if chunk_id in seen_chunk_ids:
                    raise ValueError(f"중복 chunk_id: {chunk_id}")
                seen_chunk_ids.add(chunk_id)
                write_jsonl_record(
                    stream,
                    {"chunk_id": chunk_id, "content": chunk, "metadata": metadata},
                )
                chunks_written += 1
                lengths.append(len(chunk))
                source_counts[str(metadata.get("source_type") or "<missing>")] += 1
    return {
        "input": relative_to_root(input_path),
        "input_sha256": sha256_file(input_path),
        "output": relative_to_root(output_path),
        "output_sha256": sha256_file(output_path),
        "source_records": source_records,
        "chunks": chunks_written,
        "length_min": min(lengths),
        "length_max": max(lengths),
        "length_mean": sum(lengths) / len(lengths),
        "source_type_counts": dict(source_counts),
    }


def run_chunking(
    input_dir: Path,
    output_dir: Path,
    *,
    chunk_size: int,
    overlap: int,
    force: bool = False,
) -> dict[str, Any]:
    input_files = sorted(input_dir.glob("*.jsonl"))
    if not input_files:
        raise FileNotFoundError(f"JSONL 입력 없음: {input_dir}")
    source_manifest = _load_final_manifest(input_dir)
    manifest_path = output_dir / "manifest.json"
    expected_inputs = {path.name: sha256_file(path) for path in input_files}

    if manifest_path.exists():
        existing = json.loads(manifest_path.read_text(encoding="utf-8"))
        same = (
            existing.get("settings", {}).get("chunk_size") == chunk_size
            and existing.get("settings", {}).get("chunk_overlap") == overlap
            and existing.get("settings", {}).get("method") == METHOD
            and existing.get("input_files") == expected_inputs
        )
        if not same and not force:
            raise ValueError(f"기존 산출물의 설정 또는 입력 hash가 다릅니다: {output_dir}")
        if same:
            for item in existing.get("files", []):
                path = ROOT / item["output"]
                if not path.exists() or sha256_file(path) != item["output_sha256"]:
                    raise ValueError(f"완료 파일이 없거나 hash가 다릅니다: {path}")
            print(f"[skip] 같은 설정의 완성 산출물: {relative_to_root(output_dir)}")
            return existing
        expected_parent = (ROOT / "data" / "legal_api_v2").resolve()
        if output_dir.resolve().parent != expected_parent or not re.fullmatch(r"07_chunking_\d+_ov\d+", output_dir.name):
            raise ValueError(f"안전하지 않은 --force 대상: {output_dir}")
        shutil.rmtree(output_dir)

    if output_dir.exists() and any(output_dir.glob("*.jsonl")):
        raise FileExistsError(f"manifest 없는 부분 산출물이 있습니다: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    started_at = utc_now_iso()
    started_clock = time.perf_counter()
    seen_chunk_ids: set[str] = set()
    files = [
        chunk_file(
            input_path,
            output_dir / input_path.name,
            chunk_size=chunk_size,
            overlap=overlap,
            seen_chunk_ids=seen_chunk_ids,
        )
        for input_path in input_files
    ]
    total_source_records = sum(item["source_records"] for item in files)
    total_chunks = sum(item["chunks"] for item in files)
    expected_records = source_manifest.get("totals", {}).get("output_records")
    if expected_records is not None and total_source_records != expected_records:
        raise ValueError(
            f"원본 연결 검증 실패: manifest={expected_records}, processed={total_source_records}"
        )
    manifest = {
        "pipeline": "legal_rag_v2/run_chunking",
        "started_at": started_at,
        "finished_at": utc_now_iso(),
        "duration_seconds": time.perf_counter() - started_clock,
        "git_commit": git_commit(),
        "runtime_versions": runtime_versions(),
        "dataset_version": source_manifest.get("dataset_version"),
        "input_manifest_sha256": sha256_file(input_dir / "manifest.json"),
        "input_files": expected_inputs,
        "settings": {
            "chunk_size": chunk_size,
            "chunk_overlap": overlap,
            "method": METHOD,
            "separators": list(SEPARATORS),
            "record_schema": ["chunk_id", "content", "metadata"],
        },
        "totals": {
            "source_records": total_source_records,
            "chunks": total_chunks,
            "unique_chunk_ids": len(seen_chunk_ids),
            "empty_chunks": 0,
        },
        "files": files,
    }
    if total_chunks != len(seen_chunk_ids):
        raise ValueError("chunk_id 고유성 검증 실패")
    write_json_atomic(manifest_path, manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--chunk-size", type=int, default=CHUNK_SIZE)
    parser.add_argument("--chunk-overlap", type=int, default=CHUNK_OVERLAP)
    parser.add_argument("--force", action="store_true", help="알고리즘 버전이 바뀐 생성 산출물만 안전하게 재생성")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    output_dir = args.output_dir or (
        ROOT
        / "data"
        / "legal_api_v2"
        / f"07_chunking_{args.chunk_size}_ov{args.chunk_overlap}"
    )
    manifest = run_chunking(
        args.input_dir,
        output_dir,
        chunk_size=args.chunk_size,
        overlap=args.chunk_overlap,
        force=args.force,
    )
    print(json.dumps(manifest["totals"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
