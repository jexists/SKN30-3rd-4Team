from __future__ import annotations

import unittest

from pipeline.supabase.run_insert import capacity_gate_measurement, capacity_resume_recheck


class SupabaseCapacityTests(unittest.TestCase):
    def test_gate_uses_actual_insert_rows_and_safety_factor(self) -> None:
        result = capacity_gate_measurement(
            before={"total": 100, "database": 1_000},
            after={"total": 1_100, "database": 2_000},
            total_rows=100,
            sampled_rows=10,
            inserted_rows=10,
            db_capacity_bytes=20_000,
            safety_factor=1.25,
        )
        self.assertEqual(result["bytes_per_row"], 100.0)
        self.assertEqual(result["predicted_remaining_bytes"], 9_000.0)
        self.assertEqual(result["predicted_remaining_with_safety_bytes"], 11_250)
        self.assertTrue(result["passed"])

    def test_gate_fails_when_safety_adjusted_estimate_exceeds_capacity(self) -> None:
        result = capacity_gate_measurement(
            before={"total": 0, "database": 2_000},
            after={"total": 1_000, "database": 3_000},
            total_rows=100,
            sampled_rows=10,
            inserted_rows=10,
            db_capacity_bytes=10_000,
            safety_factor=1.25,
        )
        self.assertFalse(result["passed"])

    def test_gate_rejects_zero_actual_inserts(self) -> None:
        with self.assertRaises(ValueError):
            capacity_gate_measurement(
                before={"total": 0, "database": 0},
                after={"total": 0, "database": 0},
                total_rows=1,
                sampled_rows=1,
                inserted_rows=0,
                db_capacity_bytes=1,
                safety_factor=1.25,
            )

    def test_resume_recheck_detects_database_growth(self) -> None:
        gate = {
            "db_capacity_bytes": 10_000,
            "predicted_remaining_with_safety_bytes": 4_000,
            "sample_rows": 5_000,
        }
        self.assertTrue(
            capacity_resume_recheck(
                gate,
                current_database_bytes=5_000,
                current_experiment_rows=5_000,
            )["passed"]
        )
        self.assertFalse(
            capacity_resume_recheck(
                gate,
                current_database_bytes=7_000,
                current_experiment_rows=5_000,
            )["passed"]
        )

    def test_resume_recheck_requires_sample_rows_in_target_database(self) -> None:
        gate = {
            "db_capacity_bytes": 10_000,
            "predicted_remaining_with_safety_bytes": 1_000,
            "sample_rows": 5_000,
        }
        result = capacity_resume_recheck(
            gate,
            current_database_bytes=1_000,
            current_experiment_rows=0,
        )
        self.assertFalse(result["sample_rows_present"])
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
