from __future__ import annotations

import unittest

from pipeline.common import ROOT
from pipeline.supabase.apply_schema import validate_schema_sql


class ApplySchemaTests(unittest.TestCase):
    def test_repository_schema_passes_safety_validation(self) -> None:
        sql = (ROOT / "pipeline/supabase/schema.sql").read_text(encoding="utf-8")
        validate_schema_sql(sql)

    def test_destructive_sql_is_rejected(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "금지 SQL"):
            validate_schema_sql("drop table public.kb_chunks;")


if __name__ == "__main__":
    unittest.main()
