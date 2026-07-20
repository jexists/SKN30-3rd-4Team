"""세 모델 smoke Parquet의 행·차원·정규화·ID 일치와 처리시간을 검증한다."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, relative_to_root, utc_now_iso, write_json_atomic

INPUT_ROOT = ROOT / "data/legal_api_v2"
OUTPUT_DIR = ROOT / "reports/legal_api_v2/embedding_smoke"


def log_duration_seconds(model_id: str) -> float | None:
    candidates = []
    for path in (ROOT / "pipeline/logs/embedding").glob("*.jsonl"):
        events = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        started = next(
            (
                event
                for event in events
                if event.get("event") == "run_started" and event.get("model") == model_id
            ),
            None,
        )
        completed = next(
            (event for event in reversed(events) if event.get("event") == "run_completed"),
            None,
        )
        if started and completed:
            duration = datetime.fromisoformat(completed["at"]) - datetime.fromisoformat(started["at"])
            candidates.append((path.stat().st_mtime, duration.total_seconds()))
    return max(candidates)[1] if candidates else None


def validate(input_root: Path = INPUT_ROOT, output_dir: Path = OUTPUT_DIR) -> dict[str, Any]:
    import numpy as np
    import pyarrow.parquet as pq

    directories = sorted(input_root.glob("08_embedding_500_ov50_*_smoke100"))
    if len(directories) < 3:
        raise FileNotFoundError(f"세 모델 smoke 폴더가 필요합니다: {directories}")
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    reference_ids = None
    for directory in directories:
        manifest = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        ids = []
        vectors = []
        parquet_bytes = 0
        for path in sorted(directory.glob("*.parquet")):
            data = pq.read_table(path, columns=["chunk_id", "embedding"]).to_pydict()
            ids.extend(data["chunk_id"])
            vectors.extend(data["embedding"])
            parquet_bytes += path.stat().st_size
        matrix = np.asarray(vectors, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1)
        same_ids = reference_ids is None or ids == reference_ids
        if reference_ids is None:
            reference_ids = ids
        duration = manifest.get("duration_seconds") or log_duration_seconds(manifest["model"]["id"])
        throughput = len(ids) / duration if duration else None
        full_chunks = 59987
        row = {
            "model_id": manifest["model"]["id"],
            "model_revision": manifest["model"].get("revision"),
            "rows": len(ids),
            "dimension": int(matrix.shape[1]),
            "nan_values": int(np.isnan(matrix).sum()),
            "norm_min": float(norms.min()),
            "norm_mean": float(norms.mean()),
            "norm_max": float(norms.max()),
            "same_chunk_ids": same_ids,
            "parquet_bytes": parquet_bytes,
            "duration_seconds_including_load": duration,
            "throughput_chunks_per_second": throughput,
            "projected_hours_500_ov50": full_chunks / throughput / 3600 if throughput else None,
            "passed": (
                len(ids) == 100
                and matrix.shape[1] == 1024
                and not np.isnan(matrix).any()
                and np.allclose(norms, 1.0, atol=1e-4)
                and same_ids
            ),
        }
        rows.append(row)
    with (output_dir / "summary.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    result = {
        "generated_at": utc_now_iso(),
        "input_root": relative_to_root(input_root),
        "all_passed": all(row["passed"] for row in rows),
        "models": rows,
    }
    write_json_atomic(output_dir / "report.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, default=INPUT_ROOT)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    print(json.dumps(validate(args.input_root, args.output_dir), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
