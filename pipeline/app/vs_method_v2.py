"""로컬 임베딩과 Supabase 격리 RPC를 사용하는 읽기 전용 검색 어댑터."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Mapping, Sequence

VECTOR_DIMENSION = 1024


@dataclass(frozen=True)
class SearchSettings:
    model_id: str
    model_revision: str
    experiment_id: str
    query_prefix: str = ""
    device: str | None = None
    dimension: int = VECTOR_DIMENSION


def settings_from_env(environ: Mapping[str, str] | None = None) -> SearchSettings:
    from pipeline.embedding.run_embedding import resolve_model

    values = os.environ if environ is None else environ
    model_id = str(values.get("LEGAL_RAG_V2_MODEL_ID") or "").strip()
    experiment_id = str(values.get("LEGAL_RAG_V2_EXPERIMENT_ID") or "").strip()
    if not model_id or not experiment_id:
        raise RuntimeError(
            "LEGAL_RAG_V2_MODEL_ID와 LEGAL_RAG_V2_EXPERIMENT_ID가 필요합니다. "
            "평가 우승 조합 확정 후 설정하세요."
        )
    model = resolve_model(model_id)
    revision = str(
        values.get("LEGAL_RAG_V2_MODEL_REVISION") or model.get("revision") or ""
    ).strip()
    if not revision:
        raise RuntimeError("재현 가능한 검색을 위해 LEGAL_RAG_V2_MODEL_REVISION이 필요합니다.")
    dimension = model.get("dimension")
    if dimension != VECTOR_DIMENSION:
        raise ValueError(
            f"v2 검색 차원 불일치: model={dimension}, database={VECTOR_DIMENSION}"
        )
    device = str(values.get("LEGAL_RAG_V2_DEVICE") or "").strip() or None
    if device not in (None, "cpu", "cuda", "mps"):
        raise ValueError(f"LEGAL_RAG_V2_DEVICE 지원 값이 아닙니다: {device}")
    return SearchSettings(
        model_id=model_id,
        model_revision=revision,
        experiment_id=experiment_id,
        query_prefix=str(model.get("query_prefix") or ""),
        device=device,
        dimension=int(dimension),
    )


@lru_cache(maxsize=1)
def get_settings() -> SearchSettings:
    return settings_from_env()


def get_conn():
    from dotenv import load_dotenv

    load_dotenv()
    url = (
        os.environ.get("DB_URL")
        or os.environ.get("SUPABASE_DB_URL")
        or os.environ.get("DATABASE_URL")
    )
    if not url:
        raise RuntimeError("DB_URL/SUPABASE_DB_URL/DATABASE_URL이 필요합니다.")
    import psycopg

    return psycopg.connect(url.replace("postgresql+psycopg://", "postgresql://"))


def ensure_schema(conn) -> None:
    """기존 backend 호환 이름의 읽기 전용 검사이며 DDL을 실행하지 않는다."""
    settings = get_settings()
    with conn.cursor() as cursor:
        cursor.execute(
            "select to_regclass('public.kb_chunks_v2'), "
            "to_regprocedure('public.match_kb_chunks_v2(vector,integer,text)')"
        )
        table, function = cursor.fetchone()
        if table is None or function is None:
            raise RuntimeError("kb_chunks_v2 또는 match_kb_chunks_v2가 준비되지 않았습니다.")
        cursor.execute(
            "select count(*) from public.kb_experiments_v2 where experiment_id=%s",
            (settings.experiment_id,),
        )
        if int(cursor.fetchone()[0]) != 1:
            raise RuntimeError(f"우승 experiment가 없습니다: {settings.experiment_id}")
        cursor.execute(
            "select count(*) from public.kb_chunks_v2 where experiment_id=%s",
            (settings.experiment_id,),
        )
        if int(cursor.fetchone()[0]) < 1:
            raise RuntimeError(f"우승 experiment에 검색 청크가 없습니다: {settings.experiment_id}")


@lru_cache(maxsize=3)
def _load_query_model(model_id: str, revision: str, device: str | None):
    from pipeline.embedding.run_embedding import load_model, model_revision

    model, selected_device = load_model(model_id, device, revision)
    actual_revision = model_revision(model)
    if actual_revision != revision:
        raise RuntimeError(
            f"query 모델 revision 불일치: expected={revision}, actual={actual_revision}"
        )
    return model, selected_device


@lru_cache(maxsize=256)
def _embed_query_cached(
    query: str,
    model_id: str,
    revision: str,
    device: str | None,
    query_prefix: str,
) -> tuple[float, ...]:
    if not query.strip():
        raise ValueError("검색 query가 비어 있습니다.")
    model, _ = _load_query_model(model_id, revision, device)
    vectors = model.encode(
        [query_prefix + query],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    vector = tuple(float(value) for value in vectors[0])
    if len(vector) != VECTOR_DIMENSION:
        raise RuntimeError(
            f"query embedding 차원 불일치: actual={len(vector)}, expected={VECTOR_DIMENSION}"
        )
    return vector


def vector_literal(values: Sequence[float]) -> str:
    if len(values) != VECTOR_DIMENSION:
        raise ValueError(
            f"vector 차원 불일치: actual={len(values)}, expected={VECTOR_DIMENSION}"
        )
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def search_similar(
    conn,
    query: str,
    *,
    k: int = 10,
    min_score: float = 0.0,
    config: SearchSettings | None = None,
) -> list[dict]:
    if k < 1:
        raise ValueError("k는 1 이상이어야 합니다.")
    if not -1.0 <= min_score <= 1.0:
        raise ValueError("min_score는 -1~1 범위여야 합니다.")
    settings = config or get_settings()
    vector = _embed_query_cached(
        query,
        settings.model_id,
        settings.model_revision,
        settings.device,
        settings.query_prefix,
    )
    sql = (
        "select chunk_id, content, metadata, similarity "
        "from public.match_kb_chunks_v2(%s::vector, %s, %s)"
    )
    with conn.cursor() as cursor:
        cursor.execute(sql, (vector_literal(vector), k, settings.experiment_id))
        rows = cursor.fetchall()

    hits = []
    for chunk_id, content, metadata, similarity in rows:
        score = float(similarity)
        if score < min_score:
            continue
        hits.append(
            {
                **(metadata or {}),
                "chunk_id": str(chunk_id),
                "content": str(content),
                "similarity": score,
            }
        )
    return hits


def clear_runtime_caches() -> None:
    get_settings.cache_clear()
    _embed_query_cached.cache_clear()
    _load_query_model.cache_clear()
