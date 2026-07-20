"""`06_final`을 Pandas로 분석하고 재현 가능한 CSV/그래프를 만든다."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from pipeline.common import (
    ROOT,
    iter_jsonl,
    load_pipeline_config,
    relative_to_root,
    resolve_repo_path,
    utc_now_iso,
    write_json_atomic,
)

_CONFIG = load_pipeline_config()
_CHUNK_CONFIG = _CONFIG["chunking"]
INPUT_DIR = resolve_repo_path(_CONFIG["paths"]["final_dir"])
OUTPUT_DIR = resolve_repo_path(_CONFIG["paths"]["reports_dir"]) / "prechunk_eda"
LENGTH_THRESHOLDS = (20, 300, 500, 800, 1000, 1500, 2000)
CHUNK_CANDIDATES = tuple(int(value) for value in _CHUNK_CONFIG["candidates"])
OVERLAP_RATIO = float(_CHUNK_CONFIG["overlap_ratio"])
SHORT_RECORD_CHARS = int(_CONFIG["filters"]["short_record_review_chars"])
EMBEDDING_DIM = 1024
MODEL_IDS = tuple(str(item["id"]) for item in _CONFIG["embedding"]["models"].values())
MODEL_REVISIONS = {
    str(item["id"]): item.get("revision")
    for item in _CONFIG["embedding"]["models"].values()
}


def load_records(input_dir: Path) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    files = sorted(input_dir.glob("*.jsonl"))
    if not files:
        raise FileNotFoundError(f"JSONL 입력 없음: {input_dir}")
    for path in files:
        for record in iter_jsonl(path):
            content = str(record.get("page_content") or "")
            metadata = record.get("metadata") or {}
            rows.append(
                {
                    "file": path.name,
                    "page_content": content,
                    "char_count": len(content),
                    **{f"meta.{key}": value for key, value in metadata.items()},
                }
            )
    return pd.DataFrame(rows)


def projected_chunks(length: int, size: int, overlap: int) -> int:
    if length <= size:
        return 1
    return 1 + math.ceil((length - size) / (size - overlap))


def add_token_counts(
    df: pd.DataFrame,
    model_ids: tuple[str, ...],
    model_revisions: dict[str, str | None] | None = None,
) -> pd.DataFrame:
    """모델별 tokenizer의 토큰 수를 추가한다. 모델 다운로드가 필요할 수 있다."""
    try:
        from huggingface_hub import hf_hub_download
        from tokenizers import Tokenizer
    except ImportError as exc:  # pragma: no cover - 선택 실행 의존성
        raise RuntimeError("tokenizer 분석에는 huggingface_hub와 tokenizers가 필요합니다.") from exc

    texts = df["page_content"].tolist()
    for model_id in model_ids:
        slug = model_id.rsplit("/", 1)[-1].lower()
        tokenizer_path = hf_hub_download(
            model_id,
            "tokenizer.json",
            revision=(model_revisions or {}).get(model_id),
        )
        tokenizer = Tokenizer.from_file(tokenizer_path)
        tokenizer.no_truncation()
        counts = [len(tokenizer.encode(text, add_special_tokens=True).ids) for text in texts]
        df[f"tokens.{slug}"] = counts
        df[f"over_8192.{slug}"] = [count > 8192 for count in counts]
    return df


def _length_summary(series: pd.Series) -> dict[str, float | int]:
    desc = series.describe(percentiles=[0.25, 0.5, 0.75, 0.9, 0.95, 0.99])
    return {
        "count": int(desc["count"]),
        "min": int(desc["min"]),
        "mean": float(desc["mean"]),
        "median": float(desc["50%"]),
        "p25": float(desc["25%"]),
        "p75": float(desc["75%"]),
        "p90": float(desc["90%"]),
        "p95": float(desc["95%"]),
        "p99": float(desc["99%"]),
        "max": int(desc["max"]),
    }


def _review_columns(df: pd.DataFrame) -> list[str]:
    return [
        column
        for column in (
            "file",
            "meta.record_id",
            "meta.doc_title",
            "meta.source_type",
            "meta.section",
            "meta.relevance_level",
            "char_count",
            "page_content",
        )
        if column in df.columns
    ]


def write_plots(df: pd.DataFrame, output_dir: Path, projections: pd.DataFrame) -> None:
    plt.figure(figsize=(9, 5))
    df["char_count"].plot.hist(bins=80)
    plt.xlabel("characters")
    plt.tight_layout()
    plt.savefig(output_dir / "length_histogram.png", dpi=150)
    plt.close()

    plt.figure(figsize=(9, 5))
    df["char_count"].clip(lower=1).plot.hist(bins=80, log=True)
    plt.xscale("log")
    plt.xlabel("characters (log scale)")
    plt.tight_layout()
    plt.savefig(output_dir / "length_histogram_log.png", dpi=150)
    plt.close()

    if "meta.source_type" in df:
        plt.figure(figsize=(10, 5))
        df.boxplot(column="char_count", by="meta.source_type", showfliers=False)
        plt.suptitle("")
        plt.title("Length by source type (outliers hidden)")
        plt.tight_layout()
        plt.savefig(output_dir / "length_by_source_type.png", dpi=150)
        plt.close()

    plt.figure(figsize=(8, 5))
    projections.plot.bar(x="chunk_size", y="estimated_chunks", legend=False)
    plt.ylabel("estimated chunks")
    plt.tight_layout()
    plt.savefig(output_dir / "chunk_projection.png", dpi=150)
    plt.close()


def analyze(
    input_dir: Path = INPUT_DIR,
    output_dir: Path = OUTPUT_DIR,
    *,
    with_tokenizers: bool = False,
    length_thresholds: tuple[int, ...] = LENGTH_THRESHOLDS,
    chunk_candidates: tuple[int, ...] = CHUNK_CANDIDATES,
    overlap_ratio: float = OVERLAP_RATIO,
    short_record_chars: int = SHORT_RECORD_CHARS,
    model_ids: tuple[str, ...] = MODEL_IDS,
    model_revisions: dict[str, str | None] | None = MODEL_REVISIONS,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    df = load_records(input_dir)
    if with_tokenizers:
        df = add_token_counts(df, model_ids, model_revisions)

    summary = _length_summary(df["char_count"])
    summary.update(
        {
            "empty_content": int((df["char_count"] == 0).sum()),
            "duplicate_record_id": int(df["meta.record_id"].duplicated().sum()),
            "duplicate_content": int(df["page_content"].duplicated().sum()),
        }
    )
    pd.DataFrame([summary]).to_csv(output_dir / "summary.csv", index=False, encoding="utf-8-sig")

    df.groupby("file")["char_count"].agg(["count", "min", "mean", "median", "max"]).reset_index().to_csv(
        output_dir / "length_by_file.csv", index=False, encoding="utf-8-sig"
    )
    if "meta.source_type" in df:
        df.groupby("meta.source_type")["char_count"].agg(
            ["count", "min", "mean", "median", "max"]
        ).reset_index().to_csv(
            output_dir / "length_by_source_type.csv", index=False, encoding="utf-8-sig"
        )

    threshold_rows = []
    for threshold in length_thresholds:
        count = int((df["char_count"] > threshold).sum())
        threshold_rows.append(
            {"threshold": threshold, "records_over": count, "ratio": count / len(df)}
        )
    pd.DataFrame(threshold_rows).to_csv(
        output_dir / "records_over_threshold.csv", index=False, encoding="utf-8-sig"
    )

    meta_columns = sorted(column for column in df if column.startswith("meta."))
    pd.DataFrame(
        [
            {
                "field": column.removeprefix("meta."),
                "present": int(df[column].notna().sum()),
                "coverage": float(df[column].notna().mean()),
            }
            for column in meta_columns
        ]
    ).to_csv(output_dir / "metadata_coverage.csv", index=False, encoding="utf-8-sig")

    review_columns = _review_columns(df)
    df.nlargest(10, "char_count")[review_columns].to_csv(
        output_dir / "longest_records.csv", index=False, encoding="utf-8-sig"
    )
    df.nsmallest(10, "char_count")[review_columns].to_csv(
        output_dir / "shortest_records.csv", index=False, encoding="utf-8-sig"
    )
    df[df["char_count"] < short_record_chars][review_columns].sort_values("char_count").to_csv(
        output_dir / "short_records_review.csv", index=False, encoding="utf-8-sig"
    )

    projections = pd.DataFrame(
        [
            {
                "chunk_size": size,
                "overlap": int(size * overlap_ratio),
                "estimated_chunks": int(
                    df["char_count"].map(
                        lambda length: projected_chunks(length, size, int(size * overlap_ratio))
                    ).sum()
                ),
            }
            for size in chunk_candidates
        ]
    )
    projections["embedding_raw_gib"] = (
        projections["estimated_chunks"] * EMBEDDING_DIM * 4 / 1024**3
    )
    projections["supabase_rows"] = projections["estimated_chunks"]
    actual_chunks = []
    for item in projections.to_dict(orient="records"):
        manifest_path = input_dir.parent / f"07_chunking_{item['chunk_size']}_ov{item['overlap']}" / "manifest.json"
        actual_chunks.append(
            json.loads(manifest_path.read_text(encoding="utf-8"))["totals"]["chunks"]
            if manifest_path.exists()
            else None
        )
    projections["actual_chunks"] = actual_chunks
    projections.to_csv(output_dir / "chunk_projection.csv", index=False, encoding="utf-8-sig")

    count_tables = []
    for field in ("source_type", "issue", "section", "relevance_level"):
        column = f"meta.{field}"
        if column in df:
            counts = df[column].fillna("<missing>").value_counts(dropna=False)
            count_tables.extend(
                {"field": field, "value": value, "count": int(count)}
                for value, count in counts.items()
            )
    pd.DataFrame(count_tables).to_csv(
        output_dir / "category_counts.csv", index=False, encoding="utf-8-sig"
    )

    token_stats: dict[str, dict[str, int]] = {}
    if with_tokenizers:
        token_columns = [column for column in df if column.startswith(("tokens.", "over_8192."))]
        df[["meta.record_id", *token_columns]].to_csv(
            output_dir / "token_counts.csv", index=False, encoding="utf-8-sig"
        )
        for model_id in model_ids:
            slug = model_id.rsplit("/", 1)[-1].lower()
            token_stats[model_id] = {
                "over_8192": int(df[f"over_8192.{slug}"].sum()),
                "max_tokens": int(df[f"tokens.{slug}"].max()),
            }
        pd.DataFrame(
            [{"model_id": model_id, **stats} for model_id, stats in token_stats.items()]
        ).to_csv(output_dir / "tokenizer_summary.csv", index=False, encoding="utf-8-sig")
    write_plots(df, output_dir, projections)

    recommended = int(_CHUNK_CONFIG["default_size"])
    report = {
        "generated_at": utc_now_iso(),
        "input_dir": relative_to_root(input_dir),
        "records": len(df),
        "length": summary,
        "chunk_candidates": projections.to_dict(orient="records"),
        "recommended_chunk_size": recommended,
        "recommended_overlap": int(recommended * overlap_ratio),
        "recommendation_reason": "최종 corpus 중앙값 431자 부근을 한 청크로 보존하는 기준선이며 300/800/1000과 검색 평가로 비교한다.",
        "tokenizer_analysis_completed": with_tokenizers,
        "tokenizer_stats": token_stats,
    }
    write_json_atomic(output_dir / "report.json", report)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, default=INPUT_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    parser.add_argument("--with-tokenizers", action="store_true")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    report = analyze(args.input_dir, args.output_dir, with_tokenizers=args.with_tokenizers)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
