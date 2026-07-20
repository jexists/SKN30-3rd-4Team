from __future__ import annotations

import unittest

from pipeline.chunking.run_chunking import make_chunk_id, split_text


class ChunkingTests(unittest.TestCase):
    def test_short_record_is_not_merged_or_split(self) -> None:
        self.assertEqual(split_text("짧은 조문", 500, 50), ["짧은 조문"])

    def test_long_record_respects_max_and_overlap(self) -> None:
        text = ("제1조 본문입니다. " * 100).strip()
        chunks = split_text(text, 100, 10)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(0 < len(chunk) <= 100 for chunk in chunks))

    def test_chunk_id_is_deterministic_and_index_sensitive(self) -> None:
        first = make_chunk_id("record:1", 0, "본문")
        self.assertEqual(first, make_chunk_id("record:1", 0, "본문"))
        self.assertNotEqual(first, make_chunk_id("record:1", 1, "본문"))

    def test_large_markdown_table_repeats_title_and_header(self) -> None:
        text = "별표 1\n| 항목 | 금액 |\n|---|---|\n" + "\n".join(
            f"| 임차인 {index} | {index * 1000}원 |" for index in range(30)
        )
        chunks = split_text(text, 140, 14)
        table_chunks = [chunk for chunk in chunks if "|---|---|" in chunk]
        self.assertGreater(len(table_chunks), 1)
        self.assertTrue(all("별표 1" in chunk and "| 항목 | 금액 |" in chunk for chunk in table_chunks))
        self.assertTrue(all(len(chunk) <= 140 for chunk in chunks))


if __name__ == "__main__":
    unittest.main()
