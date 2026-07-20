"""완료된 v2 핵심 manifest의 재현성 계약과 입력 hash 연결을 검사한다."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.common import ROOT, relative_to_root, sha256_file, utc_now_iso, write_json_atomic


COMMON_REQUIRED = (
    "pipeline",
    "started_at",
    "finished_at",
    "duration_seconds",
    "git_commit",
    "runtime_versions",
    "settings",
)


def core_manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors = [f"missing:{key}" for key in COMMON_REQUIRED if manifest.get(key) is None]
    duration = manifest.get("duration_seconds")
    if duration is not None and float(duration) < 0:
        errors.append("negative:duration_seconds")
    pipeline = str(manifest.get("pipeline") or "")
    if pipeline.endswith("build_final_corpus"):
        files = manifest.get("files") or []
        if not files or any(not item.get("input_sha256") for item in files):
            errors.append("missing:files.input_sha256")
    elif pipeline.endswith("run_chunking") or pipeline.endswith("run_embedding"):
        if not manifest.get("input_manifest_sha256"):
            errors.append("missing:input_manifest_sha256")
    if pipeline.endswith("run_embedding"):
        model = manifest.get("model") or {}
        if not model.get("id") or not model.get("revision"):
            errors.append("missing:model.id_or_revision")
        if not manifest.get("library_versions"):
            errors.append("missing:library_versions")
    return errors


def ocr_manifest_errors(manifest: dict[str, Any]) -> list[str]:
    errors = []
    engine = manifest.get("engine") or {}
    for key in ("pipeline", "run_id", "finished_at", "totals", "items"):
        if manifest.get(key) is None:
            errors.append(f"missing:{key}")
    if not engine.get("name") or not engine.get("version"):
        errors.append("missing:engine.name_or_version")
    if any(not item.get("image_sha256") for item in manifest.get("items", [])):
        errors.append("missing:items.image_sha256")
    return errors


def completed_manifest_paths(base: Path | None = None) -> list[Path]:
    base = base or ROOT / "data/legal_api_v2"
    paths = [base / "06_final/manifest.json"]
    paths.extend(
        base / f"07_chunking_{size}_ov{size // 10}/manifest.json"
        for size in (300, 500, 800, 1000)
    )
    paths.extend(sorted(base.glob("08_embedding_*_ov*_*/manifest.json")))
    ocr_path = base / "05_ocr_json/ocr_manifest.json"
    if ocr_path.exists():
        paths.append(ocr_path)
    return paths


def main() -> None:
    base = ROOT / "data/legal_api_v2"
    paths = completed_manifest_paths(base)
    rows = []
    for path in paths:
        if not path.exists():
            rows.append(
                {
                    "path": relative_to_root(path),
                    "sha256": None,
                    "errors": ["missing:file"],
                }
            )
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        is_ocr = path.name == "ocr_manifest.json"
        rows.append(
            {
                "path": relative_to_root(path),
                "sha256": sha256_file(path),
                **({"contract": "external_ocr_engine"} if is_ocr else {}),
                "errors": ocr_manifest_errors(manifest) if is_ocr else core_manifest_errors(manifest),
            }
        )
    errors = [f"{row['path']}:{error}" for row in rows for error in row["errors"]]
    report = {
        "checked_at": utc_now_iso(),
        "completed_manifests": len(rows),
        "errors": errors,
        "manifests": rows,
        "passed": not errors,
    }
    output = ROOT / "reports/legal_api_v2/manifest_audit/validation.json"
    write_json_atomic(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
