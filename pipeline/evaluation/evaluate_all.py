"""완료된 비-smoke 임베딩 폴더를 찾아 로컬 검색 평가를 일괄 실행한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, iter_jsonl
from pipeline.evaluation.retrieval_eval import QUESTIONS, evaluate


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", type=Path, default=ROOT / "data/legal_api_v2")
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--pattern", default="08_embedding_*")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"))
    parser.add_argument("--require-reviewed", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    questions = list(iter_jsonl(args.questions))
    statuses = {str(item.get("review_status")) for item in questions}
    if args.require_reviewed and any(status != "approved" for status in statuses):
        raise RuntimeError(f"gold 질문 검수가 완료되지 않았습니다: {sorted(statuses)}")
    input_dirs = [
        path
        for path in sorted(args.base_dir.glob(args.pattern))
        if path.is_dir() and "_smoke" not in path.name and (path / "manifest.json").exists()
    ]
    if not input_dirs:
        raise FileNotFoundError("완료된 비-smoke 임베딩 manifest가 없습니다.")
    summaries = []
    for input_dir in input_dirs:
        print(f"평가 시작: {input_dir.name}")
        summaries.append(
            evaluate(
                input_dir,
                args.questions,
                None,
                model_name=None,
                device=args.device,
                top_k=args.top_k,
            )
        )
    print(json.dumps(summaries, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
