"""우승 조합의 Supabase RPC 검색과 experiment 격리를 smoke 검증한다."""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, iter_jsonl, relative_to_root, utc_now_iso, write_json_atomic
from pipeline.embedding.run_embedding import load_model, model_revision, resolve_model
from pipeline.evaluation.retrieval_eval import metrics_for_query
from pipeline.supabase.run_insert import database_url


def vector_literal(values: Any) -> str:
    return "[" + ",".join(str(float(value)) for value in values) + "]"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--experiment-id", required=True)
    parser.add_argument(
        "--questions",
        type=Path,
        default=ROOT / "pipeline/evaluation/questions.jsonl",
    )
    parser.add_argument("--database-url")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--device", choices=("cpu", "cuda", "mps"))
    parser.add_argument("--output-dir", type=Path)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.top_k < 1:
        raise ValueError("--top-k는 1 이상이어야 합니다.")
    url = database_url(args.database_url)
    if not url:
        raise RuntimeError("DB_URL/SUPABASE_DB_URL/DATABASE_URL 또는 --database-url이 필요합니다.")
    manifest = json.loads((args.input_dir / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("experiment_id") != args.experiment_id:
        raise ValueError(
            f"experiment_id 불일치: manifest={manifest.get('experiment_id')}, cli={args.experiment_id}"
        )
    model_info = {**resolve_model(manifest["model"]["id"]), **manifest["model"]}
    expected_revision = model_info.get("revision")
    model, selected_device = load_model(model_info["id"], args.device, expected_revision)
    if expected_revision and model_revision(model) != expected_revision:
        raise RuntimeError("query 모델 revision이 문서 임베딩 manifest와 다릅니다.")
    questions = list(iter_jsonl(args.questions))
    query_vectors = model.encode(
        [model_info.get("query_prefix", "") + item["question"] for item in questions],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )

    import psycopg

    output_dir = args.output_dir or (
        ROOT / "reports/legal_api_v2/ingest" / args.experiment_id
    )
    output_dir.mkdir(parents=True, exist_ok=True)
    result_path = output_dir / "supabase_search_smoke.jsonl"
    latencies = []
    metric_rows = []
    failures: list[str] = []
    with (
        psycopg.connect(url) as connection,
        connection.cursor() as cursor,
        result_path.open("w", encoding="utf-8") as output,
    ):
        for question, vector in zip(questions, query_vectors):
            started = time.perf_counter()
            cursor.execute(
                "select chunk_id, content, metadata, similarity "
                "from public.match_kb_chunks_v2(%s::vector, %s, %s)",
                (vector_literal(vector), args.top_k, args.experiment_id),
            )
            rows = cursor.fetchall()
            latency_ms = (time.perf_counter() - started) * 1000
            latencies.append(latency_ms)
            if not rows:
                failures.append(f"{question['question_id']}: empty_results")
            chunk_ids = [str(row[0]) for row in rows]
            if len(chunk_ids) != len(set(chunk_ids)):
                failures.append(f"{question['question_id']}: duplicate_chunk_ids")
            similarities = [float(row[3]) for row in rows]
            if similarities != sorted(similarities, reverse=True):
                failures.append(f"{question['question_id']}: scores_not_descending")
            results = []
            for rank, (chunk_id, content, metadata, similarity) in enumerate(rows, start=1):
                cursor.execute(
                    "select count(*) from public.kb_chunks_v2 "
                    "where experiment_id=%s and chunk_id=%s and content=%s",
                    (args.experiment_id, chunk_id, content),
                )
                if cursor.fetchone()[0] != 1:
                    failures.append(f"{question['question_id']}: isolation_mismatch:{chunk_id}")
                results.append(
                    {
                        "rank": rank,
                        "chunk_id": chunk_id,
                        "record_id": (metadata or {}).get("record_id"),
                        "similarity": float(similarity),
                        "content": content,
                        "metadata": metadata,
                    }
                )
            retrieved_ids = [str(item["record_id"] or "") for item in results]
            metrics = metrics_for_query(retrieved_ids, set(question.get("gold_record_ids", [])))
            metric_rows.append(metrics)
            output.write(
                json.dumps(
                    {
                        "question": question,
                        "latency_ms": latency_ms,
                        "metrics": metrics,
                        "results": results,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

    summary = {
        "checked_at": utc_now_iso(),
        "experiment_id": args.experiment_id,
        "model_id": model_info["id"],
        "model_revision": expected_revision,
        "device": selected_device,
        "questions": len(questions),
        "top_k": args.top_k,
        "hit_at_5": statistics.fmean(row["hit_at_5"] for row in metric_rows),
        "latency_p50_ms": statistics.median(latencies),
        "latency_max_ms": max(latencies),
        "question_review_statuses": sorted(
            {str(item.get("review_status")) for item in questions}
        ),
        "results": relative_to_root(result_path),
        "failures": failures,
        "passed": not failures,
    }
    write_json_atomic(output_dir / "supabase_search_smoke_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
