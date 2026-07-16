"""Export deterministic legal chunk samples to a readable Markdown file."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CHUNK_DIR = ROOT / "data" / "04_chunks" / "final"
OUTPUT_PATH = ROOT / "data" / "04_chunks" / "review" / "legal_chunks_review.md"

CHUNK_FILES = {
    "statute": CHUNK_DIR / "kb_chunks_eflaw.jsonl",
    "interpretation": CHUNK_DIR / "kb_chunks_expc.jsonl",
    "precedent": CHUNK_DIR / "kb_chunks_prec.jsonl",
}


def read_jsonl(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as file:
        return [json.loads(line) for line in file if line.strip()]


def group_by_source(records: list[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        groups[str(record["metadata"]["source_id"])].append(record)
    for chunks in groups.values():
        chunks.sort(key=lambda item: item["metadata"]["chunk_index"])
    return dict(groups)


def stable_rank(label: str, source_id: str) -> str:
    return hashlib.sha256(f"legal-review:{label}:{source_id}".encode()).hexdigest()


def select_groups(
    groups: dict[str, list[dict]],
    *,
    label: str,
    count: int,
    predicate,
    min_chunks: int = 1,
    max_chunks: int = 15,
    max_chars: int = 25_000,
) -> list[list[dict]]:
    candidates = []
    for source_id, chunks in groups.items():
        total_chars = sum(len(item["page_content"]) for item in chunks)
        if (
            min_chunks <= len(chunks) <= max_chunks
            and total_chars <= max_chars
            and predicate(chunks)
        ):
            candidates.append((stable_rank(label, source_id), chunks))
    candidates.sort(key=lambda item: item[0])
    return [chunks for _, chunks in candidates[:count]]


def markdown_value(value) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value)) or "-"
    return str(value or "-").replace("\n", " ")


def append_group(lines: list[str], chunks: list[dict], number: int) -> None:
    metadata = chunks[0]["metadata"]
    lines.extend(
        [
            f"### {number}. {metadata['doc_title']}",
            "",
            f"- source_id: `{metadata['source_id']}`",
            f"- source_type: `{metadata['source_type']}`",
            f"- section: `{markdown_value(metadata.get('section'))}`",
            f"- relevance: `{markdown_value(metadata.get('relevance_level'))}`",
            f"- issue: {markdown_value(metadata.get('issue'))}",
            f"- chunks: {len(chunks)}",
            "",
        ]
    )

    for chunk in chunks:
        chunk_metadata = chunk["metadata"]
        lines.extend(
            [
                (
                    f"#### Chunk {chunk_metadata['chunk_index']} · "
                    f"{markdown_value(chunk_metadata.get('section_title'))}"
                ),
                "",
                (
                    f"`chunk_id={chunk_metadata['chunk_id']}` · "
                    f"`chars={chunk_metadata['char_len']}` · "
                    f"`tokens={chunk_metadata['token_len']}`"
                ),
                "",
                "````text",
                chunk["page_content"],
                "````",
                "",
            ]
        )


def main() -> None:
    records = {source_type: read_jsonl(path) for source_type, path in CHUNK_FILES.items()}
    groups = {source_type: group_by_source(items) for source_type, items in records.items()}

    sections = [
        (
            "법령 일반 조문",
            select_groups(
                groups["statute"],
                label="statute-article",
                count=5,
                predicate=lambda chunks: chunks[0]["metadata"].get("section") == "article",
                max_chunks=5,
            ),
        ),
        (
            "법령 별표·서식",
            select_groups(
                groups["statute"],
                label="statute-annex",
                count=3,
                predicate=lambda chunks: chunks[0]["metadata"].get("section") == "annex",
                min_chunks=2,
                max_chunks=12,
            ),
        ),
        (
            "법령해석례",
            select_groups(
                groups["interpretation"],
                label="interpretation",
                count=5,
                predicate=lambda chunks: True,
                min_chunks=3,
                max_chunks=12,
            ),
        ),
        (
            "판례 relevant",
            select_groups(
                groups["precedent"],
                label="precedent-relevant",
                count=5,
                predicate=lambda chunks: chunks[0]["metadata"].get("relevance_level") == "relevant",
                min_chunks=3,
                max_chunks=12,
            ),
        ),
        (
            "판례 candidate",
            select_groups(
                groups["precedent"],
                label="precedent-candidate",
                count=5,
                predicate=lambda chunks: chunks[0]["metadata"].get("relevance_level") == "candidate",
                min_chunks=3,
                max_chunks=12,
            ),
        ),
    ]

    lines = [
        "# 법률 API 청크 표본 검수",
        "",
        "JSONL 원본은 임베딩용으로 유지하고, 이 파일은 청크 경계와 문맥을 사람이 확인하기 위한 고정 표본이다.",
        "같은 문서의 청크는 `chunk_index` 순서로 배치했다.",
        "",
        "## 전체 현황",
        "",
        "| 유형 | 청크 수 | 문서·레코드 수 |",
        "|---|---:|---:|",
    ]
    for source_type in ["statute", "interpretation", "precedent"]:
        lines.append(
            f"| {source_type} | {len(records[source_type]):,} | {len(groups[source_type]):,} |"
        )
    lines.extend(["", "## 검수 표본", ""])

    for section_title, selected_groups in sections:
        lines.extend([f"## {section_title}", "", f"선택 문서: {len(selected_groups)}개", ""])
        for number, chunks in enumerate(selected_groups, start=1):
            append_group(lines, chunks, number)

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(f"검수용 MD 생성: {OUTPUT_PATH}")
    print(f"파일 크기: {OUTPUT_PATH.stat().st_size / 1024:.1f} KiB")


if __name__ == "__main__":
    main()
