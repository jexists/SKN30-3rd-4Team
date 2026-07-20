"""임베딩 전에 장치와 예상 출력 용량 대비 디스크 여유를 검증한다."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Any

from pipeline.common import relative_to_root, utc_now_iso, write_json_atomic

DEFAULT_BYTES_PER_ROW = 10_000
DEFAULT_SAFETY_FACTOR = 1.25
DEFAULT_RESERVE_BYTES = 1 << 30


def capacity_estimate(
    *,
    total_rows: int,
    processed_rows: int,
    existing_bytes: int,
    free_bytes: int,
    fallback_bytes_per_row: int = DEFAULT_BYTES_PER_ROW,
    safety_factor: float = DEFAULT_SAFETY_FACTOR,
    reserve_bytes: int = DEFAULT_RESERVE_BYTES,
) -> dict[str, Any]:
    if total_rows < 0 or processed_rows < 0 or processed_rows > total_rows:
        raise ValueError("임베딩 preflight 행 수가 올바르지 않습니다.")
    if fallback_bytes_per_row < 1 or safety_factor < 1.0 or reserve_bytes < 0:
        raise ValueError("임베딩 preflight 용량 설정이 올바르지 않습니다.")
    observed = existing_bytes / processed_rows if processed_rows else 0.0
    bytes_per_row = max(float(fallback_bytes_per_row), observed)
    remaining_rows = total_rows - processed_rows
    estimated_remaining = int(remaining_rows * bytes_per_row * safety_factor)
    required_free = estimated_remaining + (reserve_bytes if remaining_rows else 0)
    return {
        "total_rows": total_rows,
        "processed_rows": processed_rows,
        "remaining_rows": remaining_rows,
        "existing_output_bytes": existing_bytes,
        "observed_bytes_per_row": observed or None,
        "estimated_bytes_per_row": bytes_per_row,
        "safety_factor": safety_factor,
        "estimated_remaining_bytes_with_safety": estimated_remaining,
        "reserve_bytes": reserve_bytes if remaining_rows else 0,
        "required_free_bytes": required_free,
        "disk_free_bytes": free_bytes,
        "passed": free_bytes >= required_free,
    }


def device_status(requested_device: str | None) -> dict[str, Any]:
    try:
        import torch

        cuda_available = bool(torch.cuda.is_available())
        mps_available = bool(
            hasattr(torch.backends, "mps") and torch.backends.mps.is_available()
        )
    except Exception as exc:  # pragma: no cover - 설치/정책 환경 의존
        raise RuntimeError("PyTorch 장치 preflight에 실패했습니다.") from exc
    if requested_device == "cuda" and not cuda_available:
        raise RuntimeError("--device cuda를 요청했지만 CUDA를 사용할 수 없습니다.")
    if requested_device == "mps" and not mps_available:
        raise RuntimeError("--device mps를 요청했지만 MPS를 사용할 수 없습니다.")
    selected = requested_device or ("cuda" if cuda_available else "cpu")
    return {
        "requested": requested_device,
        "selected": selected,
        "cuda_available": cuda_available,
        "mps_available": mps_available,
        "cpu_count": os.cpu_count(),
        "platform": platform.platform(),
    }


def output_bytes(output_dir: Path) -> int:
    return sum(path.stat().st_size for path in output_dir.rglob("*.parquet"))


def run_embedding_preflight(
    *,
    output_dir: Path,
    total_rows: int,
    processed_rows: int,
    requested_device: str | None,
) -> dict[str, Any]:
    disk = shutil.disk_usage(output_dir)
    estimate = capacity_estimate(
        total_rows=total_rows,
        processed_rows=processed_rows,
        existing_bytes=output_bytes(output_dir),
        free_bytes=disk.free,
    )
    result = {
        "checked_at": utc_now_iso(),
        "output_dir": relative_to_root(output_dir),
        "device": device_status(requested_device),
        "disk": estimate,
        "passed": bool(estimate["passed"]),
    }
    write_json_atomic(output_dir / "preflight.json", result)
    if not result["passed"]:
        raise RuntimeError(
            "임베딩 예상 출력 용량 대비 디스크 여유가 부족합니다: "
            f"required={estimate['required_free_bytes']}, free={estimate['disk_free_bytes']}"
        )
    return result
