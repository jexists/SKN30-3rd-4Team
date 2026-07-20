from __future__ import annotations

import json
import shutil
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pipeline.common import ROOT
from pipeline.status_report import (
    csv_result_passed,
    completion_states,
    embedding_progress,
    evaluation_artifact_status,
    hash_inventory_is_current,
    manifest_validation_is_current,
    render_markdown,
)


@contextmanager
def project_temp_dir() -> Iterator[str]:
    path = ROOT / ".tmp_legal_pipeline_tests" / uuid.uuid4().hex
    path.mkdir(parents=True)
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


class StatusReportTests(unittest.TestCase):
    def test_local_scope_excludes_supabase_from_active_completion(self) -> None:
        local = completion_states(
            local_requirements=[True, True, True],
            app_env_ready=False,
            capacity_ready=False,
            reconciliation_ready=False,
            search_smoke_ready=False,
            scope_mode="local_only",
        )
        self.assertTrue(local["local_complete"])
        self.assertFalse(local["full_pipeline_complete"])
        self.assertTrue(local["complete"])
        full = completion_states(
            local_requirements=[True, True, True],
            app_env_ready=False,
            capacity_ready=False,
            reconciliation_ready=False,
            search_smoke_ready=False,
            scope_mode="full",
        )
        self.assertFalse(full["complete"])

    def test_embedding_progress_sums_checkpoints_without_smoke_manifest(self) -> None:
        with project_temp_dir() as temp:
            output = Path(temp)
            for name, value in (("a", 40), ("b", 25)):
                path = output / "_parts" / name
                path.mkdir(parents=True)
                (path / "checkpoint.json").write_text(
                    json.dumps({"next_line": value}), encoding="utf-8"
                )
            result = embedding_progress(output, 100)
            self.assertEqual(result["state"], "running")
            self.assertEqual(result["processed_rows"], 65)
            self.assertEqual(result["remaining_rows"], 35)
            self.assertEqual(result["progress_percent"], 65.0)

    def test_markdown_contains_main_stage_tables(self) -> None:
        status = {
            "generated_at": "now",
            "dataset_version": "v2",
            "complete": False,
            "local_complete": False,
            "full_pipeline_complete": False,
            "execution_scope": {"mode": "local_only"},
            "final_corpus": {"records": 1},
            "chunking": [{"chunk_size": 500, "overlap": 50, "state": "complete", "chunks": 2}],
            "embedding_500": [
                {
                    "model_id": "model",
                    "state": "running",
                    "progress_percent": 50.0,
                    "remaining_rows": 1,
                    "evaluation": "pending",
                }
            ],
            "gold_review": {"statuses": ["pending_team_review"], "approved": False},
            "evaluation_artifacts": {"grid_complete": False, "app_v2_env": False},
            "supabase": {
                "schema_ready": True,
                "chunk_rows": 0,
                "capacity_gate_passed": False,
                "reconciliation_passed": False,
                "search_smoke_passed": False,
            },
            "manifest_contract": {"passed": True, "completed_manifests": 10},
            "metadata_contract": {
                "passed": True,
                "final_rows": 8259,
                "chunk_rows": 226778,
                "errors": 0,
            },
        }
        result = render_markdown(status)
        self.assertIn("## 청킹", result)
        self.assertIn("500/50", result)
        self.assertIn("Supabase", result)
        self.assertIn("최소 metadata 계약", result)
        self.assertIn("local_only", result)

    def test_csv_result_requires_explicit_passed_true(self) -> None:
        with project_temp_dir() as temp:
            path = Path(temp) / "result.csv"
            path.write_text("passed,count\nTrue,10\n", encoding="utf-8")
            self.assertTrue(csv_result_passed(path))
            path.write_text("passed,count\nFalse,10\n", encoding="utf-8")
            self.assertFalse(csv_result_passed(path))

    def test_provisional_files_do_not_count_as_approved_artifacts(self) -> None:
        with project_temp_dir() as temp:
            root = Path(temp)
            (root / "shortlist_500.json").write_text("{}", encoding="utf-8")
            (root / "winner.json").write_text(
                json.dumps(
                    {"provisional": True, "winner": {"experiment_id": "experiment-1"}}
                ),
                encoding="utf-8",
            )
            (root / "app_thresholds.json").write_text(
                json.dumps({"provisional": True, "experiment_id": "experiment-1"}),
                encoding="utf-8",
            )
            (root / "grid_complete.json").write_text(
                json.dumps({"passed": True, "provisional": True, "missing": []}),
                encoding="utf-8",
            )
            (root / "app_v2.env").write_text(
                "LEGAL_RAG_V2_EXPERIMENT_ID=experiment-1\n", encoding="utf-8"
            )
            result = evaluation_artifact_status(root)
            self.assertTrue(result["shortlist"])
            self.assertFalse(result["winner"])
            self.assertFalse(result["app_thresholds"])
            self.assertFalse(result["grid_complete"])
            self.assertFalse(result["app_v2_env"])

    def test_approved_artifacts_require_matching_experiment_env(self) -> None:
        with project_temp_dir() as temp:
            root = Path(temp)
            embedding = root / "embedding"
            embedding.mkdir()
            (embedding / "manifest.json").write_text(
                json.dumps(
                    {
                        "experiment_id": "experiment-1",
                        "model": {"id": "model", "revision": "revision"},
                    }
                ),
                encoding="utf-8",
            )
            verified = []
            for index in range(4):
                grid_experiment = f"grid-experiment-{index}"
                grid_manifest = root / f"grid_manifest_{index}.json"
                grid_manifest.write_text(
                    json.dumps({"experiment_id": grid_experiment}), encoding="utf-8"
                )
                grid_summary = root / f"grid_summary_{index}.csv"
                grid_summary.write_text("metric,value\nrecall_at_5,1\n", encoding="utf-8")
                verified.append(
                    {
                        "experiment_id": grid_experiment,
                        "manifest": str(grid_manifest),
                        "summary": str(grid_summary),
                    }
                )
            (root / "shortlist_500.json").write_text(
                json.dumps({"selected_models": [{"model_id": "model"}]}), encoding="utf-8"
            )
            (root / "winner.json").write_text(
                json.dumps(
                    {
                        "provisional": False,
                        "winner": {
                            "experiment_id": "experiment-1",
                            "input_dir": str(embedding),
                        },
                    }
                ),
                encoding="utf-8",
            )
            (root / "app_thresholds.json").write_text(
                json.dumps(
                    {
                        "provisional": False,
                        "experiment_id": "experiment-1",
                        "grade_weak": 0.2,
                        "grade_strong": 0.5,
                    }
                ),
                encoding="utf-8",
            )
            (root / "grid_complete.json").write_text(
                json.dumps(
                    {
                        "passed": True,
                        "provisional": False,
                        "missing": [],
                        "verified_experiments": verified,
                    }
                ),
                encoding="utf-8",
            )
            (root / "app_v2.env").write_text(
                "\n".join(
                    [
                        "LEGAL_RAG_SEARCH_BACKEND=v2",
                        "LEGAL_RAG_V2_MODEL_ID=model",
                        "LEGAL_RAG_V2_MODEL_REVISION=revision",
                        "LEGAL_RAG_V2_EXPERIMENT_ID=experiment-1",
                        "LEGAL_RAG_V2_DEVICE=cpu",
                        "LEGAL_RAG_V2_GRADE_WEAK=0.2",
                        "LEGAL_RAG_V2_GRADE_STRONG=0.5",
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            result = evaluation_artifact_status(root)
            self.assertTrue(result["winner"])
            self.assertTrue(result["app_thresholds"])
            self.assertTrue(result["grid_complete"])
            self.assertTrue(result["app_v2_env"])

    def test_manifest_validation_must_match_current_file_hashes(self) -> None:
        with project_temp_dir() as temp:
            path = Path(temp) / "manifest.json"
            path.write_text('{"version": 1}', encoding="utf-8")
            from pipeline.common import relative_to_root, sha256_file

            report = {
                "passed": True,
                "manifests": [
                    {"path": relative_to_root(path), "sha256": sha256_file(path)}
                ],
            }
            self.assertTrue(manifest_validation_is_current(report, [path]))
            path.write_text('{"version": 2}', encoding="utf-8")
            self.assertFalse(manifest_validation_is_current(report, [path]))

    def test_metadata_audit_must_match_current_lineage_hashes(self) -> None:
        with project_temp_dir() as temp:
            path = Path(temp) / "manifest.json"
            path.write_text('{"version": 1}', encoding="utf-8")
            from pipeline.common import relative_to_root, sha256_file

            report = {
                "passed": True,
                "audited_manifests": [
                    {"path": relative_to_root(path), "sha256": sha256_file(path)}
                ],
            }
            self.assertTrue(
                hash_inventory_is_current(
                    report, [path], inventory_key="audited_manifests"
                )
            )
            path.write_text('{"version": 2}', encoding="utf-8")
            self.assertFalse(
                hash_inventory_is_current(
                    report, [path], inventory_key="audited_manifests"
                )
            )

    def test_hash_inventory_rejects_missing_required_path(self) -> None:
        with project_temp_dir() as temp:
            missing = Path(temp) / "missing.json"
            report = {"passed": True, "audited_manifests": []}
            self.assertFalse(
                hash_inventory_is_current(
                    report, [missing], inventory_key="audited_manifests"
                )
            )


if __name__ == "__main__":
    unittest.main()
