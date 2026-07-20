"""legal-rag v2 전체 단계의 로컬 증거를 JSON과 Markdown으로 요약한다."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.common import ROOT, relative_to_root, sha256_file, utc_now_iso, write_json_atomic
from pipeline.embedding.run_embedding import MODEL_REGISTRY
from pipeline.validate_manifests import completed_manifest_paths


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def csv_result_passed(path: Path) -> bool:
    if not path.exists():
        return False
    with path.open(encoding="utf-8-sig", newline="") as stream:
        row = next(csv.DictReader(stream), None)
    return str((row or {}).get("passed") or "").strip().lower() == "true"


def read_app_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            return {}
        key, value = line.split("=", 1)
        if not key or not value or key in values:
            return {}
        values[key] = value
    return values


def evaluation_artifact_status(eval_dir: Path) -> dict[str, bool]:
    shortlist_path = eval_dir / "shortlist_500.json"
    winner_path = eval_dir / "winner.json"
    thresholds_path = eval_dir / "app_thresholds.json"
    grid_path = eval_dir / "grid_complete.json"
    env_path = eval_dir / "app_v2.env"
    shortlist = read_json(shortlist_path) or {}
    winner = read_json(winner_path) or {}
    thresholds = read_json(thresholds_path) or {}
    grid = read_json(grid_path) or {}
    experiment_id = str((winner.get("winner") or {}).get("experiment_id") or "")
    approved_winner = bool(experiment_id and winner.get("provisional") is False)
    try:
        weak = float(thresholds["grade_weak"])
        strong = float(thresholds["grade_strong"])
        threshold_values_valid = -1.0 <= weak < strong <= 1.0
    except (KeyError, TypeError, ValueError):
        weak = strong = 0.0
        threshold_values_valid = False
    approved_thresholds = bool(
        approved_winner
        and thresholds.get("provisional") is False
        and str(thresholds.get("experiment_id") or "") == experiment_id
        and threshold_values_valid
    )
    selected_models = shortlist.get("selected_models") or []
    verified = grid.get("verified_experiments") or []
    expected_grid_rows = len(selected_models) * 4
    grid_rows_valid = bool(expected_grid_rows and len(verified) == expected_grid_rows)
    seen_grid_experiments: set[str] = set()
    for item in verified:
        manifest_value = str(item.get("manifest") or "")
        summary_value = str(item.get("summary") or "")
        manifest_path = Path(manifest_value)
        summary_path = Path(summary_value)
        if not manifest_path.is_absolute():
            manifest_path = ROOT / manifest_path
        if not summary_path.is_absolute():
            summary_path = ROOT / summary_path
        manifest = read_json(manifest_path) or {}
        row_experiment = str(item.get("experiment_id") or "")
        if (
            not row_experiment
            or row_experiment in seen_grid_experiments
            or str(manifest.get("experiment_id") or "") != row_experiment
            or not summary_path.exists()
        ):
            grid_rows_valid = False
        seen_grid_experiments.add(row_experiment)
    approved_grid = bool(
        approved_winner
        and grid.get("passed") is True
        and grid.get("provisional") is False
        and not grid.get("missing")
        and grid_rows_valid
    )
    env = read_app_env(env_path)
    required_env = {
        "LEGAL_RAG_SEARCH_BACKEND",
        "LEGAL_RAG_V2_MODEL_ID",
        "LEGAL_RAG_V2_MODEL_REVISION",
        "LEGAL_RAG_V2_EXPERIMENT_ID",
        "LEGAL_RAG_V2_DEVICE",
        "LEGAL_RAG_V2_GRADE_WEAK",
        "LEGAL_RAG_V2_GRADE_STRONG",
    }
    winner_input = Path(str((winner.get("winner") or {}).get("input_dir") or ""))
    if not winner_input.is_absolute():
        winner_input = ROOT / winner_input
    winner_manifest = read_json(winner_input / "manifest.json") or {}
    winner_model = winner_manifest.get("model") or {}
    try:
        env_weak = float(env.get("LEGAL_RAG_V2_GRADE_WEAK", ""))
        env_strong = float(env.get("LEGAL_RAG_V2_GRADE_STRONG", ""))
        env_thresholds_match = env_weak == weak and env_strong == strong
    except ValueError:
        env_thresholds_match = False
    approved_env = bool(
        approved_winner
        and approved_thresholds
        and required_env <= set(env)
        and env.get("LEGAL_RAG_SEARCH_BACKEND") == "v2"
        and env.get("LEGAL_RAG_V2_EXPERIMENT_ID") == experiment_id
        and env.get("LEGAL_RAG_V2_DEVICE") in {"cpu", "cuda", "mps"}
        and str(winner_manifest.get("experiment_id") or "") == experiment_id
        and env.get("LEGAL_RAG_V2_MODEL_ID") == str(winner_model.get("id") or "")
        and env.get("LEGAL_RAG_V2_MODEL_REVISION")
        == str(winner_model.get("revision") or "")
        and env_thresholds_match
    )
    return {
        "shortlist": shortlist_path.exists(),
        "winner": approved_winner,
        "app_thresholds": approved_thresholds,
        "grid_complete": approved_grid,
        "app_v2_env": approved_env,
    }


def hash_inventory_is_current(
    report: dict[str, Any], paths: list[Path], *, inventory_key: str
) -> bool:
    if report.get("passed") is not True or not paths or any(not path.exists() for path in paths):
        return False
    validated = {
        str(item.get("path") or ""): str(item.get("sha256") or "")
        for item in report.get(inventory_key) or []
    }
    current = {relative_to_root(path): sha256_file(path) for path in paths}
    return validated == current


def manifest_validation_is_current(report: dict[str, Any], paths: list[Path]) -> bool:
    return hash_inventory_is_current(report, paths, inventory_key="manifests")


def completion_states(
    *,
    local_requirements: list[bool],
    app_env_ready: bool,
    capacity_ready: bool,
    reconciliation_ready: bool,
    search_smoke_ready: bool,
    scope_mode: str,
) -> dict[str, bool]:
    if scope_mode not in {"local_only", "full"}:
        raise ValueError(f"알 수 없는 execution scope mode: {scope_mode}")
    local_complete = all(local_requirements)
    full_complete = bool(
        local_complete
        and app_env_ready
        and capacity_ready
        and reconciliation_ready
        and search_smoke_ready
    )
    return {
        "local_complete": local_complete,
        "full_pipeline_complete": full_complete,
        "complete": local_complete if scope_mode == "local_only" else full_complete,
    }


def embedding_progress(output_dir: Path, total_rows: int) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    manifest = read_json(manifest_path)
    if manifest is not None:
        processed = int((manifest.get("totals") or {}).get("rows") or 0)
        state = "complete" if processed == total_rows else "invalid_manifest"
    else:
        processed = 0
        for checkpoint_path in output_dir.glob("_parts/*/checkpoint.json"):
            checkpoint = read_json(checkpoint_path) or {}
            processed += int(checkpoint.get("next_line") or 0)
        state = "running" if processed else "not_started"
    remaining = max(0, total_rows - processed)
    return {
        "state": state,
        "processed_rows": processed,
        "total_rows": total_rows,
        "remaining_rows": remaining,
        "progress_percent": round(processed / total_rows * 100, 2) if total_rows else 0.0,
        "manifest": relative_to_root(manifest_path) if manifest_path.exists() else None,
    }


def collect_status() -> dict[str, Any]:
    base = ROOT / "data/legal_api_v2"
    execution_scope = read_json(ROOT / "pipeline/config/execution_scope.json") or {
        "mode": "full"
    }
    final_manifest_path = base / "06_final/manifest.json"
    final_manifest = read_json(final_manifest_path)
    dataset_version = str((final_manifest or {}).get("dataset_version") or "unknown")
    chunking = []
    chunk_manifests: dict[int, dict[str, Any]] = {}
    for size in (300, 500, 800, 1000):
        overlap = size // 10
        path = base / f"07_chunking_{size}_ov{overlap}/manifest.json"
        manifest = read_json(path)
        if manifest:
            chunk_manifests[size] = manifest
        chunking.append(
            {
                "chunk_size": size,
                "overlap": overlap,
                "state": "complete" if manifest else "missing",
                "chunks": int(((manifest or {}).get("totals") or {}).get("chunks") or 0),
                "manifest": relative_to_root(path) if manifest else None,
            }
        )

    total_500 = int((chunk_manifests.get(500, {}).get("totals") or {}).get("chunks") or 0)
    embeddings = []
    for model_id, settings in MODEL_REGISTRY.items():
        slug = str(settings["slug"])
        output_dir = base / f"08_embedding_500_ov50_{slug}"
        progress = embedding_progress(output_dir, total_500)
        experiment_id = f"{dataset_version}_cs500_ov50_{slug}"
        manifest = read_json(output_dir / "manifest.json")
        if manifest:
            experiment_id = str(manifest.get("experiment_id") or experiment_id)
        summary = ROOT / f"reports/legal_api_v2/eval/{experiment_id}/experiment_summary.csv"
        embeddings.append(
            {
                "model_id": model_id,
                "slug": slug,
                **progress,
                "evaluation": "complete" if summary.exists() else "pending",
                "evaluation_summary": relative_to_root(summary) if summary.exists() else None,
            }
        )

    audit = read_json(ROOT / "reports/legal_api_v2/eval/question_audit/summary.json") or {}
    schema = read_json(ROOT / "reports/legal_api_v2/ingest/schema_status.json") or {}
    manifest_validation = read_json(
        ROOT / "reports/legal_api_v2/manifest_audit/validation.json"
    ) or {}
    metadata_audit = read_json(ROOT / "reports/legal_api_v2/metadata_audit.json") or {}
    current_manifest_paths = completed_manifest_paths(base)
    manifest_current = manifest_validation_is_current(
        manifest_validation, current_manifest_paths
    )
    metadata_manifest_paths = [base / "06_final/manifest.json"] + [
        base / f"07_chunking_{size}_ov{size // 10}/manifest.json"
        for size in (300, 500, 800, 1000)
    ]
    metadata_current = hash_inventory_is_current(
        metadata_audit,
        metadata_manifest_paths,
        inventory_key="audited_manifests",
    )
    winner_data = read_json(ROOT / "reports/legal_api_v2/eval/winner.json") or {}
    winner_experiment_id = str((winner_data.get("winner") or {}).get("experiment_id") or "")
    ingest_dir = ROOT / "reports/legal_api_v2/ingest"
    capacity_path = ingest_dir / f"{winner_experiment_id}_capacity_gate.json"
    reconciliation_path = ingest_dir / f"{winner_experiment_id}_reconciliation.csv"
    search_smoke_path = (
        ingest_dir / winner_experiment_id / "supabase_search_smoke_summary.json"
    )
    capacity = read_json(capacity_path) or {}
    search_smoke = read_json(search_smoke_path) or {}
    capacity_passed = bool((capacity.get("capacity_gate") or {}).get("passed"))
    reconciliation_passed = csv_result_passed(reconciliation_path)
    search_smoke_passed = bool(search_smoke.get("passed"))
    artifacts = evaluation_artifact_status(ROOT / "reports/legal_api_v2/eval")
    review_statuses = list(audit.get("review_statuses") or [])
    scope_mode = str(execution_scope.get("mode") or "full")
    completion = completion_states(
        local_requirements=[
            bool(final_manifest),
            all(item["state"] == "complete" for item in chunking),
            all(
                item["state"] == "complete" and item["evaluation"] == "complete"
                for item in embeddings
            ),
            bool(review_statuses) and set(review_statuses) == {"approved"},
            artifacts["winner"],
            artifacts["app_thresholds"],
            artifacts["grid_complete"],
            manifest_current,
            metadata_current,
        ],
        app_env_ready=artifacts["app_v2_env"],
        capacity_ready=capacity_passed,
        reconciliation_ready=reconciliation_passed,
        search_smoke_ready=search_smoke_passed,
        scope_mode=scope_mode,
    )
    return {
        "generated_at": utc_now_iso(),
        "dataset_version": (final_manifest or {}).get("dataset_version"),
        "execution_scope": {
            "mode": scope_mode,
            "decision": execution_scope.get("decision"),
            "excluded_stages": list(execution_scope.get("excluded_stages") or []),
        },
        "final_corpus": {
            "state": "complete" if final_manifest else "missing",
            "records": int(((final_manifest or {}).get("totals") or {}).get("output_records") or 0),
            "manifest": relative_to_root(final_manifest_path) if final_manifest else None,
        },
        "chunking": chunking,
        "embedding_500": embeddings,
        "gold_review": {
            "questions": int(audit.get("questions") or 0),
            "gold_references": int(audit.get("gold_references") or 0),
            "statuses": review_statuses,
            "approved": bool(review_statuses) and set(review_statuses) == {"approved"},
            "markdown": audit.get("review_markdown"),
        },
        "evaluation_artifacts": artifacts,
        "supabase": {
            "schema_ready": bool(schema.get("passed")),
            "chunk_rows": int(schema.get("chunk_rows") or 0),
            "experiment_rows": int(schema.get("experiment_rows") or 0),
            "last_checked_at": schema.get("checked_at"),
            "winner_experiment_id": winner_experiment_id or None,
            "capacity_gate_passed": capacity_passed,
            "reconciliation_passed": reconciliation_passed,
            "search_smoke_passed": search_smoke_passed,
        },
        "manifest_contract": {
            "passed": manifest_current,
            "validation_passed": bool(manifest_validation.get("passed")),
            "current": manifest_current,
            "completed_manifests": int(manifest_validation.get("completed_manifests") or 0),
            "current_manifests": len(current_manifest_paths),
            "checked_at": manifest_validation.get("checked_at"),
        },
        "metadata_contract": {
            "passed": metadata_current,
            "validation_passed": bool(metadata_audit.get("passed")),
            "current": metadata_current,
            "final_rows": int((metadata_audit.get("final") or {}).get("rows") or 0),
            "chunk_rows": sum(
                int(item.get("rows") or 0) for item in metadata_audit.get("chunking") or []
            ),
            "errors": int((metadata_audit.get("errors") or {}).get("count") or 0),
            "checked_at": metadata_audit.get("checked_at"),
        },
        **completion,
    }


def render_markdown(status: dict[str, Any]) -> str:
    lines = [
        "# Legal RAG v2 파이프라인 상태",
        "",
        f"- 생성 시각: `{status['generated_at']}`",
        f"- 데이터 버전: `{status.get('dataset_version')}`",
        f"- 실행 범위: `{(status.get('execution_scope') or {}).get('mode', 'full')}`",
        f"- 현재 범위 완료: `{status['complete']}`",
        f"- 로컬 파이프라인 완료: `{status.get('local_complete', False)}`",
        f"- Supabase 포함 전체 완료: `{status.get('full_pipeline_complete', False)}`",
        f"- 06_final: {status['final_corpus']['records']:,} records",
        "",
        "## 청킹",
        "",
        "| 크기/overlap | 상태 | 청크 수 |",
        "|---:|---|---:|",
    ]
    for row in status["chunking"]:
        lines.append(
            f"| {row['chunk_size']}/{row['overlap']} | {row['state']} | {row['chunks']:,} |"
        )
    lines.extend(
        [
            "",
            "## 500/50 모델별 임베딩·평가",
            "",
            "| 모델 | 임베딩 | 진행 | 남은 행 | 평가 |",
            "|---|---|---:|---:|---|",
        ]
    )
    for row in status["embedding_500"]:
        lines.append(
            f"| {row['model_id']} | {row['state']} | {row['progress_percent']:.2f}% | "
            f"{row['remaining_rows']:,} | {row['evaluation']} |"
        )
    gold = status["gold_review"]
    supabase = status["supabase"]
    manifest_contract = status["manifest_contract"]
    metadata_contract = status["metadata_contract"]
    lines.extend(
        [
            "",
            "## 후속 gate",
            "",
            f"- Gold 검수: `{gold['statuses']}` (approved={gold['approved']})",
            f"- Grid 완료 marker: `{status['evaluation_artifacts']['grid_complete']}`",
            f"- 앱 v2 환경설정: `{status['evaluation_artifacts']['app_v2_env']}`",
            f"- Supabase schema: `{supabase['schema_ready']}`, 현재 v2 rows: {supabase['chunk_rows']:,}",
            f"- 5,000건 용량 gate: `{supabase['capacity_gate_passed']}`",
            f"- 전체 적재 reconciliation: `{supabase['reconciliation_passed']}`",
            f"- Supabase 검색 smoke: `{supabase['search_smoke_passed']}`",
            f"- Manifest 계약: `{manifest_contract['passed']}` "
            f"({manifest_contract['completed_manifests']} completed manifests)",
            f"- 최소 metadata 계약: `{metadata_contract['passed']}` "
            f"(final {metadata_contract['final_rows']:,}, chunks {metadata_contract['chunk_rows']:,}, "
            f"errors {metadata_contract['errors']:,})",
            "",
        ]
    )
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports/legal_api_v2/status",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    status = collect_status()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    json_path = args.output_dir / "pipeline_status.json"
    markdown_path = args.output_dir / "pipeline_status.md"
    write_json_atomic(json_path, status)
    markdown_path.write_text(render_markdown(status), encoding="utf-8")
    print(json.dumps(status, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
