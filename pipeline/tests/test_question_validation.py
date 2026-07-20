from __future__ import annotations

import unittest

from pipeline.evaluation.validate_questions import (
    question_definition_errors,
    render_review_markdown,
)


class QuestionValidationTests(unittest.TestCase):
    def test_valid_questions_have_no_definition_errors(self) -> None:
        questions = [
            {
                "question_id": "q001",
                "question": "질문",
                "gold_record_ids": ["record:1"],
                "review_status": "pending_team_review",
            }
        ]
        self.assertEqual(question_definition_errors(questions), [])

    def test_markdown_review_is_grouped_and_declares_source_of_truth(self) -> None:
        questions = [
            {
                "question_id": "q001",
                "question": "질문",
                "issue": "쟁점",
                "source_types": ["statute"],
                "review_status": "pending_team_review",
            }
        ]
        rows = [
            {
                "question_id": "q001",
                "gold_record_id": "record-1",
                "doc_title": "법률",
                "source_type": "statute",
                "section": "article",
                "exists": True,
                "match_count": 1,
                "content_preview": "근거 본문",
            }
        ]
        result = render_review_markdown(questions, rows)
        self.assertIn("## q001", result)
        self.assertIn("`record-1`", result)
        self.assertIn("questions.jsonl", result)
        self.assertIn("- [ ]", result)

    def test_duplicate_and_empty_fields_are_rejected(self) -> None:
        questions = [
            {
                "question_id": "q001",
                "question": "같은 질문",
                "gold_record_ids": ["record:1", "record:1"],
                "review_status": "unknown",
            },
            {
                "question_id": "q001",
                "question": "같은 질문",
                "gold_record_ids": [],
                "review_status": "approved",
            },
        ]
        errors = question_definition_errors(questions)
        self.assertIn("duplicate_question_id:q001", errors)
        self.assertIn("duplicate_question:같은 질문", errors)
        self.assertIn("duplicate_gold_record_ids:q001", errors)
        self.assertIn("empty_gold_record_ids:q001", errors)
        self.assertIn("invalid_review_status:q001:unknown", errors)


if __name__ == "__main__":
    unittest.main()
