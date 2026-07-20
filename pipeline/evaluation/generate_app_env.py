"""승인된 우승 manifest와 보정 임계값에서 비밀 없는 앱 v2 환경설정을 만든다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT


def build_environment(
    winner: dict[str, Any],
    thresholds: dict[str, Any],
    manifest: dict[str, Any],
    *,
    device: str,
) -> dict[str, str]:
    if winner.get("provisional") or thresholds.get("provisional"):
        raise RuntimeError("provisional 우승 결과나 임계값은 앱 설정으로 만들 수 없습니다.")
    winner_row = winner.get("winner") or {}
    experiment_id = str(winner_row.get("experiment_id") or "")
    if not experiment_id:
        raise ValueError("winner.experiment_id가 없습니다.")
    if str(thresholds.get("experiment_id") or "") != experiment_id:
        raise ValueError("winner와 app_thresholds의 experiment_id가 다릅니다.")
    if str(manifest.get("experiment_id") or "") != experiment_id:
        raise ValueError("winner와 embedding manifest의 experiment_id가 다릅니다.")
    model = manifest.get("model") or {}
    model_id = str(model.get("id") or "")
    revision = str(model.get("revision") or "")
    if not model_id or not revision:
        raise ValueError("embedding manifest에 모델 ID 또는 revision이 없습니다.")
    if int(model.get("dimension") or 0) != 1024:
        raise ValueError("앱 v2 검색은 vector(1024) 우승 모델만 지원합니다.")
    weak = float(thresholds["grade_weak"])
    strong = float(thresholds["grade_strong"])
    if not -1.0 <= weak < strong <= 1.0:
        raise ValueError(f"앱 임계값 순서 오류: weak={weak}, strong={strong}")
    return {
        "LEGAL_RAG_SEARCH_BACKEND": "v2",
        "LEGAL_RAG_V2_MODEL_ID": model_id,
        "LEGAL_RAG_V2_MODEL_REVISION": revision,
        "LEGAL_RAG_V2_EXPERIMENT_ID": experiment_id,
        "LEGAL_RAG_V2_DEVICE": device,
        "LEGAL_RAG_V2_GRADE_WEAK": str(weak),
        "LEGAL_RAG_V2_GRADE_STRONG": str(strong),
    }


def safe_env_line(key: str, value: str) -> str:
    if any(character in value for character in ("\n", "\r", "\x00")):
        raise ValueError(f"환경변수 값에 금지 문자가 있습니다: {key}")
    return f"{key}={value}"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--winner",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/winner.json",
    )
    parser.add_argument(
        "--thresholds",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/app_thresholds.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/app_v2.env",
    )
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"), default="cpu")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    winner = json.loads(args.winner.read_text(encoding="utf-8"))
    thresholds = json.loads(args.thresholds.read_text(encoding="utf-8"))
    winner_row = winner.get("winner") or {}
    input_dir_value = str(winner_row.get("input_dir") or "")
    if not input_dir_value:
        raise ValueError("winner에 input_dir가 없습니다.")
    input_dir = Path(input_dir_value)
    if not input_dir.is_absolute():
        input_dir = ROOT / input_dir
    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    environment = build_environment(
        winner,
        thresholds,
        manifest,
        device=args.device,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "# 승인된 legal-rag v2 우승 조합. DB_URL 등 비밀값은 포함하지 않는다.\n"
        + "\n".join(safe_env_line(key, value) for key, value in environment.items())
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(args.output), "environment": environment}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
