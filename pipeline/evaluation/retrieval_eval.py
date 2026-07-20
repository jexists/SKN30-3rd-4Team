"""정규화된 Parquet 벡터를 메모리에 한 번 읽어 brute-force cosine 평가한다."""

from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, iter_jsonl, relative_to_root, utc_now_iso
from pipeline.embedding.run_embedding import load_model, model_revision, resolve_model

QUESTIONS = ROOT / "pipeline" / "evaluation" / "questions.jsonl"
TOP_K_VALUES = (1, 3, 5, 10)
METRIC_KEYS = (
    *[f"hit_at_{k}" for k in TOP_K_VALUES],
    *[f"recall_at_{k}" for k in TOP_K_VALUES],
    "mrr_at_10",
    "ndcg_at_10",
)


def percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    if not 0.0 <= fraction <= 1.0:
        raise ValueError("fraction은 0~1 범위여야 합니다.")
    ordered = sorted(float(value) for value in values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def reciprocal_rank(retrieved_ids: list[str], gold_ids: set[str], k: int = 10) -> float:
    for rank, record_id in enumerate(retrieved_ids[:k], start=1):
        if record_id in gold_ids:
            return 1.0 / rank
    return 0.0


def ndcg(retrieved_ids: list[str], gold_ids: set[str], k: int = 10) -> float:
    seen_relevant: set[str] = set()
    gains = []
    for record_id in retrieved_ids[:k]:
        is_new_relevant = record_id in gold_ids and record_id not in seen_relevant
        gains.append(1.0 if is_new_relevant else 0.0)
        if is_new_relevant:
            seen_relevant.add(record_id)
    dcg = sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))
    ideal_hits = min(len(gold_ids), k)
    idcg = sum(1.0 / math.log2(index + 2) for index in range(ideal_hits))
    return dcg / idcg if idcg else 0.0


def load_corpus(input_dir: Path):
    try:
        import numpy as np
        import pyarrow.parquet as pq
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("평가에는 numpy와 pyarrow가 필요합니다.") from exc
    chunk_ids: list[str] = []
    contents: list[str] = []
    metadata: list[dict[str, Any]] = []
    vector_blocks = []
    for path in sorted(input_dir.glob("*.parquet")):
        table = pq.read_table(path, columns=["chunk_id", "content", "metadata_json", "embedding"])
        chunk_ids.extend(table["chunk_id"].to_pylist())
        contents.extend(table["content"].to_pylist())
        metadata.extend(json.loads(value) for value in table["metadata_json"].to_pylist())
        embedding_array = table["embedding"].combine_chunks()
        dimension = embedding_array.type.list_size
        flat = embedding_array.values.to_numpy(zero_copy_only=False)
        vector_blocks.append(np.asarray(flat, dtype=np.float32).reshape(len(embedding_array), dimension))
    if not vector_blocks:
        raise FileNotFoundError(f"Parquet 벡터 없음: {input_dir}")
    matrix = np.concatenate(vector_blocks, axis=0)
    if matrix.ndim != 2 or matrix.shape[1] != 1024:
        raise ValueError(f"평가 벡터 차원 불일치: {matrix.shape}")
    if not np.isfinite(matrix).all():
        raise ValueError("평가 벡터에 NaN/Inf가 있습니다.")
    if len(chunk_ids) != matrix.shape[0]:
        raise ValueError(f"평가 행 수 불일치: ids={len(chunk_ids)}, vectors={matrix.shape[0]}")
    return matrix, chunk_ids, contents, metadata


def metrics_for_query(retrieved_ids: list[str], gold_ids: set[str]) -> dict[str, float]:
    result: dict[str, float] = {}
    for k in TOP_K_VALUES:
        hits = gold_ids.intersection(retrieved_ids[:k])
        result[f"hit_at_{k}"] = float(bool(hits))
        result[f"recall_at_{k}"] = len(hits) / len(gold_ids) if gold_ids else 0.0
    result["mrr_at_10"] = reciprocal_rank(retrieved_ids, gold_ids)
    result["ndcg_at_10"] = ndcg(retrieved_ids, gold_ids)
    return result


def grouped_metric_rows(per_query: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in per_query:
        issue = str(row.get("issue") or "unknown")
        groups.setdefault(("issue", issue), []).append(row)
        for source_type in row.get("source_type_values", []) or ["unknown"]:
            groups.setdefault(("source_type", str(source_type)), []).append(row)
    output = []
    for (group_type, group), rows in sorted(groups.items()):
        output.append(
            {
                "group_type": group_type,
                "group": group,
                "questions": len(rows),
                **{
                    key: statistics.fmean(float(row[key]) for row in rows)
                    for key in METRIC_KEYS
                },
                "latency_p50_ms": statistics.median(float(row["latency_ms"]) for row in rows),
            }
        )
    return output


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def evaluate(
    input_dir: Path,
    questions_path: Path,
    output_dir: Path | None,
    *,
    model_name: str | None,
    device: str | None,
    top_k: int,
    dataset_version: str | None = None,
    chunk_size: int | None = None,
) -> dict[str, Any]:
    import numpy as np

    manifest = json.loads((input_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest_model = manifest["model"]
    manifest_model_id = manifest_model["id"]
    if model_name is not None and model_name != manifest_model_id:
        raise ValueError(
            f"문서/질문 모델 불일치: manifest={manifest_model_id}, cli={model_name}"
        )
    model_name = manifest_model_id
    model_info = {**resolve_model(model_name), **manifest_model}
    experiment_id = manifest["experiment_id"]
    if dataset_version is not None and dataset_version != manifest.get("dataset_version"):
        raise ValueError(
            f"dataset_version 불일치: manifest={manifest.get('dataset_version')}, cli={dataset_version}"
        )
    if chunk_size is not None:
        import re

        match = re.search(r"_cs(\d+)_", experiment_id)
        actual_chunk_size = int(match.group(1)) if match else None
        if chunk_size != actual_chunk_size:
            raise ValueError(
                f"chunk_size 불일치: experiment={actual_chunk_size}, cli={chunk_size}"
            )
    output_dir = output_dir or ROOT / "reports" / "legal_api_v2" / "eval" / experiment_id
    output_dir.mkdir(parents=True, exist_ok=True)
    questions = list(iter_jsonl(questions_path))
    matrix, chunk_ids, contents, metadata = load_corpus(input_dir)
    if top_k < 1 or top_k > len(chunk_ids):
        raise ValueError(f"top_k 범위 오류: top_k={top_k}, chunks={len(chunk_ids)}")
    expected_revision = manifest_model.get("revision")
    model, selected_device = load_model(model_name, device, expected_revision)
    loaded_revision = model_revision(model)
    if expected_revision and loaded_revision != expected_revision:
        raise RuntimeError(
            f"query 모델 revision 불일치: expected={expected_revision}, actual={loaded_revision}"
        )
    query_vectors = model.encode(
        [model_info["query_prefix"] + item["question"] for item in questions],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )
    per_query = []
    review_path = output_dir / "topk_review.jsonl"
    latencies = []
    with review_path.open("w", encoding="utf-8") as review:
        for question, query_vector in zip(questions, query_vectors):
            started = time.perf_counter()
            scores = matrix @ np.asarray(query_vector, dtype=np.float32)
            indices = np.argpartition(scores, -top_k)[-top_k:]
            indices = indices[np.argsort(scores[indices])[::-1]]
            latency_ms = (time.perf_counter() - started) * 1000
            latencies.append(latency_ms)
            retrieved_record_ids = [str(metadata[index].get("record_id") or "") for index in indices]
            gold_ids = set(question["gold_record_ids"])
            first_gold_position = next(
                (
                    position
                    for position, record_id in enumerate(retrieved_record_ids)
                    if record_id in gold_ids
                ),
                None,
            )
            row = {
                "experiment_id": experiment_id,
                "question_id": question["question_id"],
                "question": question["question"],
                "issue": question.get("issue"),
                "source_types": "|".join(str(value) for value in question.get("source_types", [])),
                "source_type_values": list(question.get("source_types", [])),
                "gold_record_count": len(set(question["gold_record_ids"])),
                "latency_ms": latency_ms,
                "top1_similarity": float(scores[indices[0]]),
                "first_gold_rank": first_gold_position + 1 if first_gold_position is not None else None,
                "first_gold_similarity": (
                    float(scores[indices[first_gold_position]])
                    if first_gold_position is not None
                    else None
                ),
                **metrics_for_query(retrieved_record_ids, gold_ids),
            }
            per_query.append(row)
            results = [
                {
                    "rank": rank,
                    "score": float(scores[index]),
                    "chunk_id": chunk_ids[index],
                    "record_id": metadata[index].get("record_id"),
                    "doc_title": metadata[index].get("doc_title"),
                    "section": metadata[index].get("section"),
                    "content": contents[index],
                }
                for rank, index in enumerate(indices, start=1)
            ]
            review.write(json.dumps({"question": question, "results": results}, ensure_ascii=False) + "\n")
    write_csv(output_dir / "per_query_results.csv", per_query)
    write_csv(output_dir / "metrics_by_group.csv", grouped_metric_rows(per_query))
    top1_similarities = [float(row["top1_similarity"]) for row in per_query]
    first_gold_similarities = [
        float(row["first_gold_similarity"])
        for row in per_query
        if row["first_gold_similarity"] is not None
    ]
    summary = {
        "experiment_id": experiment_id,
        "dataset_version": manifest.get("dataset_version"),
        "model_id": model_name,
        "device": selected_device,
        "chunks": len(chunk_ids),
        "embedding_duration_seconds": manifest.get("duration_seconds"),
        "parquet_bytes": sum(path.stat().st_size for path in input_dir.glob("*.parquet")),
        "questions": len(questions),
        **{
            key: statistics.fmean(row[key] for row in per_query)
            for key in METRIC_KEYS
        },
        "latency_p50_ms": statistics.median(latencies),
        "latency_p95_ms": sorted(latencies)[max(0, math.ceil(len(latencies) * 0.95) - 1)],
        "top1_similarity_p10": percentile(top1_similarities, 0.10),
        "top1_similarity_p50": percentile(top1_similarities, 0.50),
        "top1_similarity_p90": percentile(top1_similarities, 0.90),
        "first_gold_similarity_p10": percentile(first_gold_similarities, 0.10),
        "first_gold_similarity_p50": percentile(first_gold_similarities, 0.50),
        "first_gold_hits_at_10": len(first_gold_similarities),
        "evaluated_at": utc_now_iso(),
        "questions_review_status": sorted({q.get("review_status") for q in questions}),
        "input_dir": relative_to_root(input_dir),
    }
    write_csv(output_dir / "experiment_summary.csv", [summary])
    leaderboard_path = ROOT / "reports" / "legal_api_v2" / "eval" / "leaderboard.csv"
    existing = []
    if leaderboard_path.exists():
        with leaderboard_path.open(encoding="utf-8-sig", newline="") as stream:
            existing = list(csv.DictReader(stream))
    existing = [row for row in existing if row.get("experiment_id") != experiment_id]
    write_csv(leaderboard_path, [*existing, summary])
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--questions", type=Path, default=QUESTIONS)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--embedding-model")
    parser.add_argument("--dataset-version")
    parser.add_argument("--chunk-size", type=int)
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    summary = evaluate(
        args.input_dir,
        args.questions,
        args.output_dir,
        model_name=args.embedding_model,
        device=args.device,
        top_k=args.top_k,
        dataset_version=args.dataset_version,
        chunk_size=args.chunk_size,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
