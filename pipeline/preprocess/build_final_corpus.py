"""05 API/PDF 산출물을 읽어 최소 metadata의 `06_final` JSONL을 만든다.

원본 `05_*` 파일은 읽기 전용이며 절대 수정하지 않는다. 삭제 조문, heading,
excluded 판례, 빈 본문은 제외하지만 20자 미만 레코드는 보존한다.
"""

from __future__ import annotations

import argparse
import json
import shutil
import time
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

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
_PATHS = _CONFIG["paths"]
DEFAULT_API_DIR = resolve_repo_path(_PATHS["api_text_dir"])
DEFAULT_IMAGE_JSONL = resolve_repo_path(_PATHS["api_image_jsonl"])
DEFAULT_PDF_DIR = resolve_repo_path(_PATHS["pdf_text_dir"])
DEFAULT_OUTPUT_DIR = resolve_repo_path(_PATHS["final_dir"])
DEFAULT_DATASET_VERSION = str(_CONFIG["dataset_version"])

COMMON_FIELDS = (
    "source_type",
    "source_id",
    "record_id",
    "doc_title",
    "source_org",
    "doc_year",
    "authority",
    "issue",
    "section",
)
TYPE_FIELDS: dict[str, tuple[str, ...]] = {
    "statute": ("article", "article_no", "article_title", "effective_date"),
    "precedent": ("court", "case_no", "decision_date", "judgment_type", "relevance_level"),
    "interpretation": ("case_no", "decision_date", "interpreting_agency"),
}
PDF_FIELDS = ("pdf_page", "book_page")
REQUIRED_FIELDS = ("source_type", "source_id", "record_id", "doc_title", "section")


@dataclass
class BuildState:
    seen_record_ids: set[str] = field(default_factory=set)
    all_input_record_ids: set[str] = field(default_factory=set)
    input_records: int = 0
    output_records: int = 0
    short_records_kept: list[dict[str, Any]] = field(default_factory=list)
    exclusions: dict[str, list[str]] = field(
        default_factory=lambda: {
            "empty_page_content": [],
            "is_deleted": [],
            "heading": [],
            "relevance_excluded": [],
        }
    )
    missing_required: Counter[str] = field(default_factory=Counter)


def minimal_metadata(metadata: dict[str, Any], *, is_pdf: bool) -> dict[str, Any]:
    source_type = str(metadata.get("source_type") or "")
    fields = list(COMMON_FIELDS)
    fields.extend(TYPE_FIELDS.get(source_type, ()))
    if is_pdf:
        fields.extend(PDF_FIELDS)

    result: dict[str, Any] = {}
    for key in fields:
        value = metadata.get(key)
        if value is None or value == "" or value == []:
            continue
        result[key] = value
    return result


def exclusion_reason(content: str, metadata: dict[str, Any]) -> str | None:
    if not content.strip():
        return "empty_page_content"
    if metadata.get("is_deleted") is True:
        return "is_deleted"
    if metadata.get("section") == "heading":
        return "heading"
    if metadata.get("relevance_level") == "excluded":
        return "relevance_excluded"
    return None


def source_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        raise FileNotFoundError(f"입력 폴더 없음: {directory}")
    files = sorted(directory.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"JSONL 입력 없음: {directory}")
    return files


def _scan_input_record_ids(files: Iterable[Path], state: BuildState) -> None:
    for path in files:
        for record in iter_jsonl(path):
            metadata = record.get("metadata") or {}
            record_id = str(metadata.get("record_id") or "")
            if record_id:
                state.all_input_record_ids.add(record_id)


def process_file(
    input_path: Path,
    output_path: Path,
    *,
    is_pdf: bool,
    state: BuildState,
) -> dict[str, Any]:
    input_count = 0
    output_count = 0
    with atomic_text_writer(output_path) as output_stream:
        for record in iter_jsonl(input_path):
            input_count += 1
            state.input_records += 1
            content = str(record.get("page_content") or "").strip()
            raw_metadata = record.get("metadata") or {}
            if not isinstance(raw_metadata, dict):
                raise ValueError(f"metadata가 object가 아닙니다: {input_path}")
            record_id = str(raw_metadata.get("record_id") or "")
            reason = exclusion_reason(content, raw_metadata)
            if reason:
                state.exclusions[reason].append(record_id)
                continue

            metadata = minimal_metadata(raw_metadata, is_pdf=is_pdf)
            for key in REQUIRED_FIELDS:
                if key not in metadata:
                    state.missing_required[key] += 1
            if not record_id:
                raise ValueError(f"record_id 누락: {input_path} 입력 #{input_count}")
            if record_id in state.seen_record_ids:
                raise ValueError(f"중복 record_id: {record_id} ({input_path})")
            state.seen_record_ids.add(record_id)

            if len(content) < 20:
                state.short_records_kept.append(
                    {
                        "record_id": record_id,
                        "source_type": metadata.get("source_type"),
                        "section": metadata.get("section"),
                        "relevance_level": metadata.get("relevance_level"),
                        "char_count": len(content),
                        "page_content": content,
                    }
                )

            write_jsonl_record(output_stream, {"page_content": content, "metadata": metadata})
            output_count += 1
            state.output_records += 1

    return {
        "input": relative_to_root(input_path),
        "input_sha256": sha256_file(input_path),
        "input_records": input_count,
        "output": relative_to_root(output_path),
        "output_sha256": sha256_file(output_path),
        "output_records": output_count,
    }


def image_exclusion_summary(path: Path, all_record_ids: set[str]) -> dict[str, Any]:
    if not path.exists():
        return {"input": relative_to_root(path), "exists": False, "records": 0, "items": []}

    items: list[dict[str, Any]] = []
    missing_parents: list[str] = []
    for record in iter_jsonl(path):
        metadata = record.get("metadata") or {}
        parent_record_id = str(metadata.get("parent_record_id") or "")
        parent_available = parent_record_id in all_record_ids
        if not parent_available:
            missing_parents.append(str(metadata.get("record_id") or ""))
        items.append(
            {
                "record_id": metadata.get("record_id"),
                "parent_record_id": parent_record_id,
                "source_type": metadata.get("source_type"),
                "doc_title": metadata.get("doc_title"),
                "section": metadata.get("section"),
                "image_id": metadata.get("image_id"),
                "image_url": metadata.get("image_url"),
                "parent_text_available": parent_available,
                "reason": "parent_text_available" if parent_available else "parent_text_missing",
            }
        )
    if missing_parents:
        raise ValueError(f"부모 텍스트가 없는 이미지 레코드: {missing_parents[:10]}")
    return {
        "input": relative_to_root(path),
        "input_sha256": sha256_file(path),
        "exists": True,
        "records": len(items),
        "reason": "parent_text_available",
        "items": items,
    }


def build_corpus(
    *,
    api_dir: Path,
    pdf_dir: Path,
    image_jsonl: Path,
    output_dir: Path,
    dataset_version: str,
    force: bool = False,
) -> dict[str, Any]:
    started_at = utc_now_iso()
    started_clock = time.perf_counter()
    api_files = source_files(api_dir)
    pdf_files = source_files(pdf_dir)
    if output_dir.exists() and any(output_dir.iterdir()):
        if not force:
            raise FileExistsError(f"출력 폴더가 비어 있지 않습니다(--force 필요): {output_dir}")
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    state = BuildState()
    _scan_input_record_ids([*api_files, *pdf_files], state)

    files_manifest: list[dict[str, Any]] = []
    for input_path in api_files:
        output_path = output_dir / f"api_{input_path.name}"
        files_manifest.append(
            process_file(input_path, output_path, is_pdf=False, state=state)
        )
    for input_path in pdf_files:
        output_path = output_dir / f"pdf_{input_path.name}"
        files_manifest.append(process_file(input_path, output_path, is_pdf=True, state=state))

    if state.missing_required:
        raise ValueError(f"필수 metadata 누락: {dict(state.missing_required)}")

    image_summary = image_exclusion_summary(image_jsonl, state.all_input_record_ids)
    exclusion_counts = {key: len(value) for key, value in state.exclusions.items()}
    finished_at = utc_now_iso()
    manifest: dict[str, Any] = {
        "pipeline": "legal_rag_v2/build_final_corpus",
        "dataset_version": dataset_version,
        "started_at": started_at,
        "finished_at": finished_at,
        "duration_seconds": time.perf_counter() - started_clock,
        "git_commit": git_commit(),
        "runtime_versions": runtime_versions(),
        "settings": {
            "excluded_relevance_levels": ["excluded"],
            "exclude_deleted": True,
            "exclude_sections": ["heading"],
            "short_records_are_kept": True,
            "metadata_policy": "allowlist",
        },
        "totals": {
            "input_records": state.input_records,
            "output_records": state.output_records,
            "excluded_records": sum(exclusion_counts.values()),
            "unique_record_ids": len(state.seen_record_ids),
            "short_records_kept": len(state.short_records_kept),
            "image_records_excluded": image_summary["records"],
        },
        "exclusions": {
            "counts": exclusion_counts,
            "record_ids": state.exclusions,
            "images": image_summary,
        },
        "short_records_kept": state.short_records_kept,
        "metadata": {
            "common_fields": list(COMMON_FIELDS),
            "type_fields": {key: list(value) for key, value in TYPE_FIELDS.items()},
            "pdf_fields": list(PDF_FIELDS),
        },
        "files": files_manifest,
    }
    write_json_atomic(output_dir / "manifest.json", manifest)
    return manifest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-dir", type=Path, default=DEFAULT_API_DIR)
    parser.add_argument("--pdf-dir", type=Path, default=DEFAULT_PDF_DIR)
    parser.add_argument("--image-jsonl", type=Path, default=DEFAULT_IMAGE_JSONL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--dataset-version", default=DEFAULT_DATASET_VERSION)
    parser.add_argument("--force", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    manifest = build_corpus(
        api_dir=args.api_dir,
        pdf_dir=args.pdf_dir,
        image_jsonl=args.image_jsonl,
        output_dir=args.output_dir,
        dataset_version=args.dataset_version,
        force=args.force,
    )
    print(json.dumps(manifest["totals"], ensure_ascii=False, indent=2))
    print(f"완료: {relative_to_root(args.output_dir)}")


if __name__ == "__main__":
    main()
