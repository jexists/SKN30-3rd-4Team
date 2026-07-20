from __future__ import annotations

import unittest

from pipeline.common import ROOT


class SupabaseSchemaTests(unittest.TestCase):
    def test_v2_schema_is_isolated_and_idempotent(self) -> None:
        sql = (ROOT / "pipeline/supabase/schema.sql").read_text(encoding="utf-8").lower()
        self.assertIn("kb_chunks_v2", sql)
        self.assertIn("primary key (experiment_id, chunk_id)", sql)
        self.assertIn("match_kb_chunks_v2", sql)
        self.assertIn("where c.experiment_id = p_experiment_id", sql)
        self.assertNotIn("alter table public.kb_chunks ", sql)


if __name__ == "__main__":
    unittest.main()
