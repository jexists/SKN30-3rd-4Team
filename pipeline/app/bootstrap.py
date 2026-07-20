"""기존 graph.py를 수정하지 않고 v2 검색 모듈과 임계값을 주입한다."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

from pipeline.app import vs_method_v2
from pipeline.app.v2_runtime import thresholds_from_env

ROOT = Path(__file__).resolve().parents[2]
CORE_DIR = ROOT / "src/core"


def bootstrap_graph():
    vs_method_v2.get_settings()  # 모델·revision·experiment·차원 선검증
    weak, strong = thresholds_from_env()
    loaded_graph = sys.modules.get("graph")
    if loaded_graph is not None and getattr(loaded_graph, "vs_method", None) is not vs_method_v2:
        raise RuntimeError("v1 graph가 이미 로드되었습니다. v2 진입점으로 앱을 재시작하세요.")
    core = str(CORE_DIR)
    if core not in sys.path:
        sys.path.insert(0, core)
    sys.modules["vs_method"] = vs_method_v2
    graph = importlib.import_module("graph")
    graph.GRADE_WEAK = weak
    graph.GRADE_STRONG = strong
    return graph
