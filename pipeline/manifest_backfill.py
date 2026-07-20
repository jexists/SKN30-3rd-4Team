"""기존 v2 manifest에 실행 시간·런타임과 최신 hash chain을 비파괴 보강한다."""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.common import (
    ROOT,
    relative_to_root,
    runtime_versions,
    sha256_file,
    utc_now_iso,
    write_json_atomic,
)


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def add_runtime_and_duration(
    manifest: dict[str, Any], *, duration_hint: float | None = None
) -> None:
    if manifest.get("duration_seconds") is None:
        started = manifest.get("started_at")
        finished = manifest.get("finished_at")
        if started and finished:
            manifest["duration_seconds"] = max(
                0.0, (parse_time(str(finished)) - parse_time(str(started))).total_seconds()
            )
        elif duration_hint is not None and finished:
            duration = float(duration_hint)
            manifest["duration_seconds"] = duration
            manifest["started_at"] = (parse_time(str(finished)) - timedelta(seconds=duration)).isoformat()
    manifest.setdefault("runtime_versions", runtime_versions())
    manifest.setdefault(
        "runtime_capture",
        {
            "mode": "post_run_backfill",
            "captured_at": utc_now_iso(),
            "note": "실행 후 같은 작업 환경에서 보강; 원 실행 manifest의 git_commit은 유지",
        },
    )


def main() -> None:
    base = ROOT / "data/legal_api_v2"
    changed: list[dict[str, Any]] = []
    final_path = base / "06_final/manifest.json"
    final = json.loads(final_path.read_text(encoding="utf-8"))
    before = sha256_file(final_path)
    add_runtime_and_duration(final)
    write_json_atomic(final_path, final)
    changed.append(
        {"path": relative_to_root(final_path), "before": before, "after": sha256_file(final_path)}
    )

    chunk_hashes: dict[tuple[int, int], str] = {}
    for chunk_dir in sorted(base.glob("07_chunking_*_ov*")):
        match = re.fullmatch(r"07_chunking_(\d+)_ov(\d+)", chunk_dir.name)
        manifest_path = chunk_dir / "manifest.json"
        if not match or not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        before = sha256_file(manifest_path)
        manifest["input_manifest_sha256"] = sha256_file(final_path)
        add_runtime_and_duration(manifest)
        write_json_atomic(manifest_path, manifest)
        after = sha256_file(manifest_path)
        chunk_hashes[(int(match.group(1)), int(match.group(2)))] = after
        changed.append({"path": relative_to_root(manifest_path), "before": before, "after": after})

    smoke_report_path = ROOT / "reports/legal_api_v2/embedding_smoke/report.json"
    duration_by_model = {}
    if smoke_report_path.exists():
        smoke_report = json.loads(smoke_report_path.read_text(encoding="utf-8"))
        duration_by_model = {
            str(item["model_id"]): float(item["duration_seconds_including_load"])
            for item in smoke_report.get("models", [])
            if item.get("duration_seconds_including_load") is not None
        }
    for embedding_dir in sorted(base.glob("08_embedding_*_ov*_*")):
        match = re.match(r"08_embedding_(\d+)_ov(\d+)_", embedding_dir.name)
        manifest_path = embedding_dir / "manifest.json"
        if not match or not manifest_path.exists():
            continue
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        before = sha256_file(manifest_path)
        key = (int(match.group(1)), int(match.group(2)))
        if key not in chunk_hashes:
            raise RuntimeError(f"부모 chunk manifest가 없습니다: {embedding_dir}")
        manifest["input_manifest_sha256"] = chunk_hashes[key]
        model_id = str((manifest.get("model") or {}).get("id") or "")
        add_runtime_and_duration(manifest, duration_hint=duration_by_model.get(model_id))
        write_json_atomic(manifest_path, manifest)
        changed.append(
            {
                "path": relative_to_root(manifest_path),
                "before": before,
                "after": sha256_file(manifest_path),
            }
        )

    report = {
        "backfilled_at": utc_now_iso(),
        "capture_mode": "post_run_backfill",
        "jsonl_or_parquet_modified": False,
        "manifests": changed,
    }
    output = ROOT / "reports/legal_api_v2/manifest_audit/backfill.json"
    write_json_atomic(output, report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
