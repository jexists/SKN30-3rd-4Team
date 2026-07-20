"""v2 graph bootstrap 전에 검증하는 순수 런타임 설정 함수."""

from __future__ import annotations

import os
from collections.abc import Mapping


def thresholds_from_env(environ: Mapping[str, str] | None = None) -> tuple[float, float]:
    values = os.environ if environ is None else environ

    def parse(name: str) -> float:
        raw = str(values.get(name) or "").strip()
        if not raw:
            raise RuntimeError(f"승인된 앱 임계값 환경변수가 필요합니다: {name}")
        try:
            value = float(raw)
        except ValueError as exc:
            raise ValueError(f"{name} 숫자 변환 실패: {raw!r}") from exc
        if not -1.0 <= value <= 1.0:
            raise ValueError(f"{name}은 -1~1 범위여야 합니다: {value}")
        return value

    weak = parse("LEGAL_RAG_V2_GRADE_WEAK")
    strong = parse("LEGAL_RAG_V2_GRADE_STRONG")
    if weak >= strong:
        raise ValueError(f"v2 임계값 순서 오류: weak={weak}, strong={strong}")
    return weak, strong
