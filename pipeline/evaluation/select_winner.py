"""leaderboard를 확정 기준으로 정렬하고 검수된 최종 우승 조합을 기록한다."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, utc_now_iso, write_json_atomic


def numeric(row: dict[str, Any], key: str, default: float) -> float:
    try:
        return float(row.get(key) or default)
    except (TypeError, ValueError):
        return default


def rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return sorted(
        rows,
        key=lambda row: (
            -numeric(row, "recall_at_5", 0),
            -numeric(row, "ndcg_at_10", 0),
            numeric(row, "chunks", float("inf")),
            numeric(row, "parquet_bytes", float("inf")),
            numeric(row, "latency_p95_ms", float("inf")),
        ),
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--leaderboard",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/leaderboard.csv",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/winner.json",
    )
    parser.add_argument("--allow-pending-review", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with args.leaderboard.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not rows:
        raise ValueError("leaderboard가 비어 있습니다.")
    pending = [
        row.get("experiment_id")
        for row in rows
        if "pending" in str(row.get("questions_review_status", "")).lower()
    ]
    if pending and not args.allow_pending_review:
        raise RuntimeError(
            "gold 질문이 pending_team_review 상태이므로 최종 우승을 확정할 수 없습니다: "
            + ", ".join(str(value) for value in pending)
        )
    ranked = rank_rows(rows)
    result = {
        "selected_at": utc_now_iso(),
        "provisional": bool(pending),
        "ranking_rule": [
            "recall_at_5 desc",
            "ndcg_at_10 desc",
            "chunks asc",
            "parquet_bytes asc",
            "latency_p95_ms asc",
        ],
        "winner": ranked[0],
        "ranked_experiment_ids": [row.get("experiment_id") for row in ranked],
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
