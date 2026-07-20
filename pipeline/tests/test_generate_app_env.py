from __future__ import annotations

import unittest

from pipeline.evaluation.generate_app_env import build_environment, safe_env_line


class GenerateAppEnvTests(unittest.TestCase):
    def fixtures(self):
        experiment_id = "legalv2_test_cs500_ov50_kure-v1"
        winner = {
            "provisional": False,
            "winner": {"experiment_id": experiment_id},
        }
        thresholds = {
            "provisional": False,
            "experiment_id": experiment_id,
            "grade_weak": 0.41,
            "grade_strong": 0.63,
        }
        manifest = {
            "experiment_id": experiment_id,
            "model": {"id": "nlpai-lab/KURE-v1", "revision": "abc", "dimension": 1024},
        }
        return winner, thresholds, manifest

    def test_build_environment_requires_matching_approved_artifacts(self) -> None:
        winner, thresholds, manifest = self.fixtures()
        result = build_environment(winner, thresholds, manifest, device="cpu")
        self.assertEqual(result["LEGAL_RAG_SEARCH_BACKEND"], "v2")
        self.assertEqual(result["LEGAL_RAG_V2_EXPERIMENT_ID"], manifest["experiment_id"])
        self.assertEqual(result["LEGAL_RAG_V2_GRADE_WEAK"], "0.41")

        winner["provisional"] = True
        with self.assertRaises(RuntimeError):
            build_environment(winner, thresholds, manifest, device="cpu")

    def test_build_environment_rejects_mismatch_and_unsafe_lines(self) -> None:
        winner, thresholds, manifest = self.fixtures()
        thresholds["experiment_id"] = "different"
        with self.assertRaises(ValueError):
            build_environment(winner, thresholds, manifest, device="cpu")
        with self.assertRaises(ValueError):
            safe_env_line("KEY", "bad\nvalue")


if __name__ == "__main__":
    unittest.main()
