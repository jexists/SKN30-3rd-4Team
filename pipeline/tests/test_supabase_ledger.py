from __future__ import annotations

import shutil
import unittest
import uuid

from pipeline.common import ROOT
from pipeline.supabase.run_insert import init_ledger


class SupabaseLedgerTests(unittest.TestCase):
    def test_ledger_contains_resume_and_audit_columns(self) -> None:
        directory = ROOT / ".tmp_legal_pipeline_tests" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        self.addCleanup(shutil.rmtree, directory, True)
        connection = init_ledger(directory / "ledger.sqlite3")
        try:
            columns = {row[1] for row in connection.execute("pragma table_info(ingest_state)")}
        finally:
            connection.close()
        self.assertTrue(
            {
                "run_id",
                "input_dir",
                "input_sha256",
                "committed_records",
                "next_offset",
                "status",
                "last_error",
            }.issubset(columns)
        )


if __name__ == "__main__":
    unittest.main()
