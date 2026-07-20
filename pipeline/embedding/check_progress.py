"""전체 임베딩 출력 폴더의 checkpoint를 합산해 진행 상태를 표시한다."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT

DEFAULT_MODEL_SLUGS = ("kure-v1", "kure-legal-ft-v1", "bge-m3")


def checkpoint_progress(output_dir: Path, expected_rows: int) -> dict[str, Any]:
    processed = 0
    part_count = 0
    current_file = None
    checkpoints = sorted((output_dir / "_parts").glob("*/checkpoint.json"))
    for path in checkpoints:
        value = json.loads(path.read_text(encoding="utf-8"))
        current = int(value.get("next_line", 0))
        processed += current
        part_count += len(value.get("parts", []))
        if current < int(value.get("target_rows", 0)):
            current_file = path.parent.name

    manifest_path = output_dir / "manifest.json"
    state_path = output_dir / "run_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        processed = int(manifest.get("totals", {}).get("rows", processed))
        status = "complete" if processed == expected_rows else "partial"
    elif state:
        status = str(state.get("status") or "unknown")
    elif processed:
        status = "running_or_paused"
    elif output_dir.exists():
        status = "initializing_or_paused"
    else:
        status = "queued"

    remaining = max(expected_rows - processed, 0)
    speed = None
    eta_seconds = None
    started_at = state.get("started_at")
    resume_from_rows = state.get("resume_from_rows")
    newly_processed = processed - int(resume_from_rows) if resume_from_rows is not None else 0
    if newly_processed > 0 and status == "running" and started_at:
        started = datetime.fromisoformat(str(started_at))
        elapsed = max((datetime.now(timezone.utc) - started).total_seconds(), 1.0)
        speed = newly_processed / elapsed
        eta_seconds = remaining / speed if speed else None
    return {
        "output_dir": output_dir.as_posix(),
        "status": status,
        "processed": processed,
        "total": expected_rows,
        "remaining": remaining,
        "percent": round(processed / expected_rows * 100, 2) if expected_rows else 100.0,
        "parts": part_count,
        "current_file": current_file,
        "speed_chunks_per_second": round(speed, 3) if speed else None,
        "eta_seconds": round(eta_seconds) if eta_seconds else None,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=ROOT / "data/legal_api_v2/07_chunking_500_ov50",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        action="append",
        help="반복 지정 가능. 생략하면 500/50 전체 모델 폴더를 자동 탐색한다.",
    )
    parser.add_argument("--json", action="store_true", dest="as_json")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    input_manifest = json.loads((args.input_dir / "manifest.json").read_text(encoding="utf-8"))
    expected_rows = int(input_manifest["totals"]["chunks"])
    output_dirs = args.output_dir or [
        args.input_dir.parent / f"08_embedding_500_ov50_{slug}"
        for slug in DEFAULT_MODEL_SLUGS
    ]
    rows = [checkpoint_progress(path, expected_rows) for path in output_dirs]
    if args.as_json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return
    if not rows:
        print("전체 임베딩 출력 폴더가 없습니다.")
        return
    for row in rows:
        current = f", file={row['current_file']}" if row["current_file"] else ""
        speed = (
            f", speed={row['speed_chunks_per_second']:.3f}/s, ETA={row['eta_seconds'] / 3600:.1f}h"
            if row["speed_chunks_per_second"] and row["eta_seconds"]
            else ""
        )
        print(
            f"{Path(row['output_dir']).name}: {row['status']} "
            f"{row['processed']:,}/{row['total']:,} ({row['percent']:.2f}%), "
            f"remaining={row['remaining']:,}, parts={row['parts']}{current}{speed}"
        )


if __name__ == "__main__":
    main()
