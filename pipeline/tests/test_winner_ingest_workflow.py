from __future__ import annotations

import unittest
from pathlib import Path

from pipeline.common import ROOT


class WinnerIngestWorkflowTests(unittest.TestCase):
    def test_workflow_enforces_capacity_before_full_resume_and_verifies_after(self) -> None:
        script = (ROOT / "pipeline/supabase/run_winner_ingest.sh").read_text(encoding="utf-8")
        self.assertIn("LEGAL_RAG_V2_DB_CAPACITY_BYTES", script)
        self.assertIn('winner.get("provisional")', script)
        sample = script.index("--limit 5000")
        gate = script.index("capacity gate failed")
        full = script.index("pipeline/supabase/check_insert.py")
        smoke = script.index("pipeline/supabase/search_smoke.py")
        app_env = script.index("pipeline/evaluation/generate_app_env.py")
        self.assertLess(sample, gate)
        self.assertLess(gate, full)
        self.assertLess(full, smoke)
        self.assertLess(smoke, app_env)
        self.assertIn("--resume", script)


if __name__ == "__main__":
    unittest.main()
