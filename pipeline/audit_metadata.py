"""06_final과 청킹 산출물의 최소 metadata 계약을 전수 검사한다."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.common import (
    ROOT,
    iter_jsonl,
    relative_to_root,
    resolve_repo_path,
    sha256_file,
    utc_now_iso,
    write_json_atomic,
)
from pipeline.preprocess.build_final_corpus import (
    COMMON_FIELDS,
    PDF_FIELDS,
    REQUIRED_FIELDS,
    TYPE_FIELDS,
)

DEFAULT_FINAL_DIR = ROOT / "data/legal_api_v2/06_final"
DEFAULT_CHUNK_DIRS = tuple(
    ROOT / f"data/legal_api_v2/07_chunking_{size}_ov{size // 10}"
    for size in (300, 500, 800, 1000)
)
DEFAULT_REPORT = ROOT / "reports/legal_api_v2/metadata_audit.json"
FINAL_SCHEMA = {"page_content", "metadata"}
CHUNK_SCHEMA = {"chunk_id", "content", "metadata"}
MAX_ERROR_EXAMPLES = 100


def _is_blank(value: Any) -> bool:
    return value is None or value == "" or value == []


def _allowed_metadata(source_type: str, *, is_pdf: bool) -> set[str]:
    allowed = set(COMMON_FIELDS)
    allowed.update(TYPE_FIELDS.get(source_type, ()))
    if is_pdf:
        allowed.update(PDF_FIELDS)
    return allowed


class AuditErrors:
    def __init__(self) -> None:
        self.count = 0
        self.examples: list[dict[str, Any]] = []
        self.by_code: Counter[str] = Counter()

    def add(self, code: str, path: Path, row_number: int, detail: str) -> None:
        self.count += 1
        self.by_code[code] += 1
        if len(self.examples) < MAX_ERROR_EXAMPLES:
            self.examples.append(
                {
                    "code": code,
                    "path": str(path),
                    "row_number": row_number,
                    "detail": detail,
                }
            )


def _check_metadata(
    metadata: Any,
    *,
    path: Path,
    row_number: int,
    is_pdf: bool,
    errors: AuditErrors,
) -> str:
    if not isinstance(metadata, dict):
        errors.add("metadata_not_object", path, row_number, type(metadata).__name__)
        return ""
    source_type = str(metadata.get("source_type") or "")
    unexpected = sorted(set(metadata) - _allowed_metadata(source_type, is_pdf=is_pdf))
    if unexpected:
        errors.add("unexpected_metadata_keys", path, row_number, ",".join(unexpected))
    missing = [key for key in REQUIRED_FIELDS if _is_blank(metadata.get(key))]
    if missing:
        errors.add("missing_required_metadata", path, row_number, ",".join(missing))
    return str(metadata.get("record_id") or "")


def audit_metadata(final_dir: Path, chunk_dirs: list[Path]) -> dict[str, Any]:
    errors = AuditErrors()
    final_metadata: dict[str, dict[str, Any]] = {}
    final_rows = 0
    final_files = sorted(final_dir.glob("*.jsonl"))
    if not final_files:
        raise FileNotFoundError(f"06_final JSONL 없음: {final_dir}")

    for path in final_files:
        is_pdf = path.name.startswith("pdf_")
        for row_number, row in enumerate(iter_jsonl(path), start=1):
            final_rows += 1
            if set(row) != FINAL_SCHEMA:
                errors.add("final_record_schema", path, row_number, repr(sorted(row)))
            if not str(row.get("page_content") or "").strip():
                errors.add("empty_final_content", path, row_number, "page_content is blank")
            metadata = row.get("metadata")
            record_id = _check_metadata(
                metadata,
                path=path,
                row_number=row_number,
                is_pdf=is_pdf,
                errors=errors,
            )
            if record_id:
                if record_id in final_metadata:
                    errors.add("duplicate_record_id", path, row_number, record_id)
                elif isinstance(metadata, dict):
                    final_metadata[record_id] = metadata

    chunk_summaries: list[dict[str, Any]] = []
    for chunk_dir in chunk_dirs:
        files = sorted(chunk_dir.glob("*.jsonl"))
        if not files:
            raise FileNotFoundError(f"청킹 JSONL 없음: {chunk_dir}")
        seen_chunk_ids: set[str] = set()
        rows = 0
        metadata_mismatches = 0
        for path in files:
            is_pdf = path.name.startswith("pdf_")
            for row_number, row in enumerate(iter_jsonl(path), start=1):
                rows += 1
                if set(row) != CHUNK_SCHEMA:
                    errors.add("chunk_record_schema", path, row_number, repr(sorted(row)))
                chunk_id = str(row.get("chunk_id") or "")
                if not chunk_id:
                    errors.add("missing_chunk_id", path, row_number, "chunk_id is blank")
                elif chunk_id in seen_chunk_ids:
                    errors.add("duplicate_chunk_id", path, row_number, chunk_id)
                else:
                    seen_chunk_ids.add(chunk_id)
                if not str(row.get("content") or "").strip():
                    errors.add("empty_chunk_content", path, row_number, "content is blank")
                metadata = row.get("metadata")
                record_id = _check_metadata(
                    metadata,
                    path=path,
                    row_number=row_number,
                    is_pdf=is_pdf,
                    errors=errors,
                )
                original = final_metadata.get(record_id)
                if original is None:
                    errors.add("unknown_record_id", path, row_number, record_id)
                elif metadata != original:
                    metadata_mismatches += 1
                    errors.add("metadata_changed_after_chunking", path, row_number, record_id)
        chunk_summaries.append(
            {
                "directory": relative_to_root(chunk_dir),
                "files": len(files),
                "rows": rows,
                "unique_chunk_ids": len(seen_chunk_ids),
                "metadata_mismatches": metadata_mismatches,
            }
        )

    manifest_paths = [final_dir / "manifest.json"] + [
        chunk_dir / "manifest.json" for chunk_dir in chunk_dirs
    ]
    missing_manifests = [path for path in manifest_paths if not path.exists()]
    for path in missing_manifests:
        errors.add("missing_manifest", path, 0, "metadata audit lineage manifest missing")
    audited_manifests = [
        {"path": relative_to_root(path), "sha256": sha256_file(path)}
        for path in manifest_paths
        if path.exists()
    ]
    return {
        "checked_at": utc_now_iso(),
        "contract": {
            "final_schema": sorted(FINAL_SCHEMA),
            "chunk_schema": sorted(CHUNK_SCHEMA),
            "common_metadata": list(COMMON_FIELDS),
            "type_metadata": {key: list(value) for key, value in TYPE_FIELDS.items()},
            "pdf_metadata": list(PDF_FIELDS),
            "required_metadata": list(REQUIRED_FIELDS),
        },
        "final": {
            "directory": relative_to_root(final_dir),
            "files": len(final_files),
            "rows": final_rows,
            "unique_record_ids": len(final_metadata),
        },
        "chunking": chunk_summaries,
        "audited_manifests": audited_manifests,
        "errors": {
            "count": errors.count,
            "by_code": dict(sorted(errors.by_code.items())),
            "examples": errors.examples,
            "examples_truncated": errors.count > len(errors.examples),
        },
        "passed": errors.count == 0,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--final-dir", type=Path, default=DEFAULT_FINAL_DIR)
    parser.add_argument("--chunk-dir", type=Path, action="append")
    parser.add_argument("--output", type=Path, default=DEFAULT_REPORT)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    final_dir = resolve_repo_path(args.final_dir)
    chunk_dirs = [resolve_repo_path(path) for path in (args.chunk_dir or DEFAULT_CHUNK_DIRS)]
    output = resolve_repo_path(args.output)
    report = audit_metadata(final_dir, chunk_dirs)
    write_json_atomic(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
