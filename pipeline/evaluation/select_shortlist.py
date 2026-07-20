"""500/50 provisional leaderboard에서 전체 grid에 돌릴 모델 1~2개를 고른다."""

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
from pipeline.embedding.run_embedding import MODEL_REGISTRY
from pipeline.evaluation.select_winner import numeric, rank_rows


def choose_shortlist(
    rows: list[dict[str, Any]],
    *,
    recall_margin: float = 0.05,
    ndcg_margin: float = 0.05,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    ranked = rank_rows(rows)
    if not ranked:
        raise ValueError("500/50 leaderboard가 비어 있습니다.")
    selected = [ranked[0]]
    rationale: dict[str, Any] = {
        "rule": "always top1; include top2 when Recall@5 and nDCG@10 are both within margins",
        "recall_margin": recall_margin,
        "ndcg_margin": ndcg_margin,
    }
    if len(ranked) >= 2:
        top, second = ranked[:2]
        recall_gap = abs(numeric(top, "recall_at_5", 0) - numeric(second, "recall_at_5", 0))
        ndcg_gap = abs(numeric(top, "ndcg_at_10", 0) - numeric(second, "ndcg_at_10", 0))
        include_second = recall_gap <= recall_margin and ndcg_gap <= ndcg_margin
        rationale.update(
            {
                "top2_recall_gap": recall_gap,
                "top2_ndcg_gap": ndcg_gap,
                "second_model_included": include_second,
            }
        )
        if include_second:
            selected.append(second)
    return selected, rationale


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
        default=ROOT / "reports/legal_api_v2/eval/shortlist_500.json",
    )
    parser.add_argument("--recall-margin", type=float, default=0.05)
    parser.add_argument("--ndcg-margin", type=float, default=0.05)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    with args.leaderboard.open(encoding="utf-8-sig", newline="") as stream:
        all_rows = list(csv.DictReader(stream))
    rows = [
        row
        for row in all_rows
        if "08_embedding_500_ov50_" in str(row.get("input_dir") or "")
        and str(row.get("model_id") or "") in MODEL_REGISTRY
    ]
    expected_models = set(MODEL_REGISTRY)
    actual_models = {str(row.get("model_id")) for row in rows}
    if actual_models != expected_models or len(rows) != len(expected_models):
        raise RuntimeError(
            f"500/50 모델 평가가 모두 필요합니다: expected={sorted(expected_models)}, "
            f"actual={sorted(actual_models)}, rows={len(rows)}"
        )
    selected, rationale = choose_shortlist(
        rows,
        recall_margin=args.recall_margin,
        ndcg_margin=args.ndcg_margin,
    )
    result = {
        "selected_at": utc_now_iso(),
        "provisional": any(
            "pending" in str(row.get("questions_review_status") or "").lower() for row in rows
        ),
        "source_stage": "500_ov50_three_models",
        "rationale": rationale,
        "selected_models": [
            {
                "rank": rank,
                "model_id": row["model_id"],
                "slug": MODEL_REGISTRY[row["model_id"]]["slug"],
                "revision": MODEL_REGISTRY[row["model_id"]].get("revision"),
                "experiment_id": row.get("experiment_id"),
                "recall_at_5": numeric(row, "recall_at_5", 0),
                "ndcg_at_10": numeric(row, "ndcg_at_10", 0),
            }
            for rank, row in enumerate(selected, start=1)
        ],
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
