"""shortlist grid 산출물이 모두 존재하는지 검증하고 완료 marker를 기록한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, relative_to_root, utc_now_iso, write_json_atomic


def grid_is_provisional(winner: dict) -> bool:
    """최종 검수 상태는 초기 shortlist가 아니라 최신 winner가 결정한다."""
    return bool(winner.get("provisional", True))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--shortlist",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/shortlist_500.json",
    )
    parser.add_argument(
        "--winner",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/winner.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/grid_complete.json",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    shortlist = json.loads(args.shortlist.read_text(encoding="utf-8"))
    if not shortlist.get("selected_models"):
        raise ValueError("shortlist 모델이 없습니다.")
    if not args.winner.exists():
        raise FileNotFoundError(args.winner)
    winner = json.loads(args.winner.read_text(encoding="utf-8"))
    if not (winner.get("winner") or {}).get("experiment_id"):
        raise ValueError("winner.json에 winner.experiment_id가 없습니다.")
    verified = []
    missing = []
    for model in shortlist["selected_models"]:
        for size in (300, 500, 800, 1000):
            overlap = round(size * 0.10)
            embedding_dir = (
                ROOT / "data/legal_api_v2" / f"08_embedding_{size}_ov{overlap}_{model['slug']}"
            )
            manifest_path = embedding_dir / "manifest.json"
            if not manifest_path.exists():
                missing.append(relative_to_root(manifest_path))
                continue
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            summary_path = (
                ROOT
                / "reports/legal_api_v2/eval"
                / str(manifest["experiment_id"])
                / "experiment_summary.csv"
            )
            if not summary_path.exists():
                missing.append(relative_to_root(summary_path))
                continue
            verified.append(
                {
                    "model_id": model["model_id"],
                    "chunk_size": size,
                    "overlap": overlap,
                    "experiment_id": manifest["experiment_id"],
                    "manifest": relative_to_root(manifest_path),
                    "summary": relative_to_root(summary_path),
                }
            )
    if missing:
        raise RuntimeError(f"grid 산출물 누락: {missing}")
    result = {
        "completed_at": utc_now_iso(),
        "provisional": grid_is_provisional(winner),
        "shortlist_provisional": bool(shortlist.get("provisional", True)),
        "shortlist": relative_to_root(args.shortlist),
        "winner": relative_to_root(args.winner),
        "verified_experiments": verified,
        "missing": [],
        "passed": True,
    }
    write_json_atomic(args.output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
