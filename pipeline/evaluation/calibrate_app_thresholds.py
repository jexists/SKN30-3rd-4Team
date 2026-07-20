"""최종 우승 실험의 평가 분포로 앱 검색 강/약 임계값을 보정한다."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, relative_to_root, utc_now_iso, write_json_atomic
from pipeline.evaluation.retrieval_eval import percentile


def number(row: dict[str, Any], key: str) -> float | None:
    value = row.get(key)
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} 숫자 변환 실패: {value!r}") from exc


def calibrate_thresholds(
    rows: list[dict[str, Any]], *, min_questions: int = 10
) -> dict[str, Any]:
    if len(rows) < min_questions:
        raise ValueError(
            f"임계값 보정 질문 부족: actual={len(rows)}, required={min_questions}"
        )
    top1_success = [
        score
        for row in rows
        if (score := number(row, "top1_similarity")) is not None
        and (number(row, "hit_at_1") or 0.0) >= 1.0
    ]
    first_gold = [
        score
        for row in rows
        if (score := number(row, "first_gold_similarity")) is not None
    ]
    if not top1_success:
        raise ValueError("Hit@1 성공 질문이 없어 GRADE_STRONG을 계산할 수 없습니다.")
    if not first_gold:
        raise ValueError("top-10 gold 검색 결과가 없어 GRADE_WEAK을 계산할 수 없습니다.")

    strong = percentile(top1_success, 0.25)
    weak = percentile(first_gold, 0.10)
    if strong is None or weak is None:  # pragma: no cover - 위 빈 목록 검사로 방어됨
        raise RuntimeError("임계값 분위수 계산 실패")
    if weak >= strong:
        raise ValueError(
            "보정된 GRADE_WEAK이 GRADE_STRONG보다 작지 않습니다. "
            f"weak={weak:.6f}, strong={strong:.6f}; top-k 결과를 수동 검토하세요."
        )
    return {
        "grade_weak": round(weak, 4),
        "grade_strong": round(strong, 4),
        "questions": len(rows),
        "hit_at_1_successes": len(top1_success),
        "first_gold_hits_at_10": len(first_gold),
        "method": {
            "grade_weak": "first_gold_similarity p10",
            "grade_strong": "top1_similarity among Hit@1 successes p25",
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--winner",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/winner.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/app_thresholds.json",
    )
    parser.add_argument("--min-questions", type=int, default=10)
    parser.add_argument("--allow-pending-review", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    winner = json.loads(args.winner.read_text(encoding="utf-8"))
    provisional = bool(winner.get("provisional"))
    if provisional and not args.allow_pending_review:
        raise RuntimeError(
            "gold 질문 검수가 끝나지 않아 운영 임계값을 확정할 수 없습니다. "
            "비교용으로만 만들려면 --allow-pending-review를 사용하세요."
        )
    experiment_id = str((winner.get("winner") or {}).get("experiment_id") or "")
    if not experiment_id:
        raise ValueError("winner.json에 winner.experiment_id가 없습니다.")
    per_query = ROOT / "reports/legal_api_v2/eval" / experiment_id / "per_query_results.csv"
    with per_query.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    calibrated = calibrate_thresholds(rows, min_questions=args.min_questions)
    result = {
        "calibrated_at": utc_now_iso(),
        "provisional": provisional,
        "experiment_id": experiment_id,
        "winner": relative_to_root(args.winner),
        "per_query_results": relative_to_root(per_query),
        **calibrated,
        "environment": {
            "LEGAL_RAG_V2_GRADE_WEAK": str(calibrated["grade_weak"]),
            "LEGAL_RAG_V2_GRADE_STRONG": str(calibrated["grade_strong"]),
        },
        "apply_to_app": not provisional,
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
