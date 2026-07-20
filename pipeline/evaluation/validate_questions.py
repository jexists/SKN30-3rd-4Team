"""평가 질문의 gold record_id 존재 여부와 원문 미리보기 보고서를 만든다."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pipeline.common import ROOT, iter_jsonl, relative_to_root, utc_now_iso, write_json_atomic


ALLOWED_REVIEW_STATUSES = {"pending_team_review", "approved"}


def render_review_markdown(
    questions: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> str:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(str(row["question_id"]), []).append(row)
    lines = [
        "# 평가 gold 레코드 팀 검수표",
        "",
        "> 이 문서는 검토 보조용입니다. 체크박스를 수정해도 평가 상태는 바뀌지 않습니다. "
        "최종 승인 상태의 원본은 `pipeline/evaluation/questions.jsonl`입니다.",
        "",
    ]
    for question in questions:
        question_id = str(question.get("question_id") or "")
        lines.extend(
            [
                f"## {question_id}",
                "",
                f"- 질문: {str(question.get('question') or '').strip()}",
                f"- 쟁점: {str(question.get('issue') or '').strip()}",
                "- 기대 자료형: " + ", ".join(map(str, question.get("source_types", []))),
                f"- 현재 상태: `{question.get('review_status')}`",
                "",
            ]
        )
        for index, row in enumerate(grouped.get(question_id, []), start=1):
            preview = str(row.get("content_preview") or "").replace("\r", " ").replace("\n", " ")
            lines.extend(
                [
                    f"### Gold {index}: `{row.get('gold_record_id')}`",
                    "",
                    f"- 문서: {row.get('doc_title') or '-'}",
                    f"- 유형/구간: {row.get('source_type') or '-'} / {row.get('section') or '-'}",
                    f"- corpus 일치: `{row.get('exists')}` (match_count={row.get('match_count')})",
                    f"- [ ] 이 문서가 질문의 정답 근거로 적절함",
                    "",
                    f"> {preview}",
                    "",
                ]
            )
    return "\n".join(lines).rstrip() + "\n"


def question_definition_errors(questions: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    ids = [str(item.get("question_id") or "") for item in questions]
    texts = [str(item.get("question") or "").strip() for item in questions]
    for value, count in Counter(ids).items():
        if not value:
            errors.append("empty_question_id")
        elif count > 1:
            errors.append(f"duplicate_question_id:{value}")
    for value, count in Counter(texts).items():
        if not value:
            errors.append("empty_question")
        elif count > 1:
            errors.append(f"duplicate_question:{value}")
    for item in questions:
        question_id = str(item.get("question_id") or "<empty>")
        gold_ids = [str(value) for value in item.get("gold_record_ids", []) if str(value)]
        if not gold_ids:
            errors.append(f"empty_gold_record_ids:{question_id}")
        if len(gold_ids) != len(set(gold_ids)):
            errors.append(f"duplicate_gold_record_ids:{question_id}")
        review_status = str(item.get("review_status") or "")
        if review_status not in ALLOWED_REVIEW_STATUSES:
            errors.append(f"invalid_review_status:{question_id}:{review_status}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus-dir", type=Path, default=ROOT / "data/legal_api_v2/06_final")
    parser.add_argument(
        "--questions",
        type=Path,
        default=ROOT / "pipeline/evaluation/questions.jsonl",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "reports/legal_api_v2/eval/question_audit",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records: dict[str, list[dict[str, Any]]] = {}
    for path in sorted(args.corpus_dir.glob("*.jsonl")):
        for value in iter_jsonl(path):
            metadata = value.get("metadata") or {}
            record_id = str(metadata.get("record_id") or "")
            records.setdefault(record_id, []).append(
                {
                    "file": relative_to_root(path),
                    "content": str(value.get("page_content") or ""),
                    "metadata": metadata,
                }
            )

    rows = []
    questions = list(iter_jsonl(args.questions))
    definition_errors = question_definition_errors(questions)
    for question in questions:
        for gold_id in question.get("gold_record_ids", []):
            matches = records.get(str(gold_id), [])
            match = matches[0] if matches else {"file": "", "content": "", "metadata": {}}
            metadata = match["metadata"]
            rows.append(
                {
                    "question_id": question.get("question_id"),
                    "question": question.get("question"),
                    "review_status": question.get("review_status"),
                    "gold_record_id": gold_id,
                    "exists": len(matches) == 1,
                    "match_count": len(matches),
                    "source_file": match["file"],
                    "source_type": metadata.get("source_type"),
                    "doc_title": metadata.get("doc_title"),
                    "section": metadata.get("section"),
                    "content_preview": match["content"].replace("\n", " ")[:500],
                }
            )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "gold_record_review.csv"
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    markdown_path = args.output_dir / "gold_record_review.md"
    markdown_path.write_text(
        render_review_markdown(questions, rows),
        encoding="utf-8",
    )
    summary = {
        "checked_at": utc_now_iso(),
        "questions": len(questions),
        "gold_references": len(rows),
        "distinct_gold_record_ids": len({str(row["gold_record_id"]) for row in rows}),
        "valid_gold_references": sum(row["exists"] for row in rows),
        "missing_or_duplicate": [row["gold_record_id"] for row in rows if not row["exists"]],
        "definition_errors": definition_errors,
        "issue_counts": dict(sorted(Counter(str(item.get("issue") or "") for item in questions).items())),
        "declared_source_type_counts": dict(
            sorted(
                Counter(
                    str(source_type)
                    for item in questions
                    for source_type in item.get("source_types", [])
                ).items()
            )
        ),
        "review_statuses": sorted({str(item.get("review_status")) for item in questions}),
        "report": relative_to_root(csv_path),
        "review_markdown": relative_to_root(markdown_path),
    }
    write_json_atomic(args.output_dir / "summary.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if summary["missing_or_duplicate"] or definition_errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
