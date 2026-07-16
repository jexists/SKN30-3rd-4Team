"""Generate the legal API preprocessing and chunking notebooks.

The notebooks keep the transformation code visible cell by cell while this
script makes their JSON structure reproducible and easy to review.
"""

from __future__ import annotations

import json
from pathlib import Path
from textwrap import dedent


ROOT = Path(__file__).resolve().parents[1]
NOTEBOOK_DIR = ROOT / "notebooks"


def _source(text: str) -> list[str]:
    text = dedent(text).strip("\n") + "\n"
    return text.splitlines(keepends=True)


def _markdown(text: str) -> dict:
    return {"cell_type": "markdown", "metadata": {}, "source": _source(text)}


def _code(text: str) -> dict:
    return {
        "cell_type": "code",
        "execution_count": None,
        "metadata": {},
        "outputs": [],
        "source": _source(text),
    }


def _notebook(cells: list[dict]) -> dict:
    return {
        "cells": cells,
        "metadata": {
            "kernelspec": {
                "display_name": "jeonse-factchecker",
                "language": "python",
                "name": "python3",
            },
            "language_info": {"name": "python", "version": "3.13"},
        },
        "nbformat": 4,
        "nbformat_minor": 5,
    }


def _write_notebook(path: Path, cells: list[dict]) -> None:
    path.write_text(
        json.dumps(_notebook(cells), ensure_ascii=False, indent=1) + "\n",
        encoding="utf-8",
    )


def patch_load_api_notebook() -> None:
    path = NOTEBOOK_DIR / "01_load_api.ipynb"
    notebook = json.loads(path.read_text(encoding="utf-8"))

    provenance_helper = _source(
        r'''
        def dedup_with_provenance(items: list[dict], id_field: str) -> list[dict]:
            """ID 중복은 제거하되 매칭된 카테고리와 검색어는 모두 보존한다."""
            merged: dict[str, dict] = {}
            order: list[str] = []

            for item in items:
                item_id = str(item.get(id_field, "")).strip()
                if not item_id:
                    continue

                categories = list(item.get("_categories") or [])
                queries = list(item.get("_queries") or [])
                if item.get("_category"):
                    categories.append(item["_category"])
                if item.get("_query"):
                    queries.append(item["_query"])

                if item_id not in merged:
                    merged[item_id] = dict(item)
                    merged[item_id]["_categories"] = []
                    merged[item_id]["_queries"] = []
                    order.append(item_id)

                record = merged[item_id]
                for value in categories:
                    if value and value not in record["_categories"]:
                        record["_categories"].append(value)
                for value in queries:
                    if value and value not in record["_queries"]:
                        record["_queries"].append(value)

                record["_category"] = record["_categories"][0] if record["_categories"] else "기타"
                record["_query"] = record["_queries"][0] if record["_queries"] else ""

            return [merged[item_id] for item_id in order]
        '''
    )

    text_export_helper = _source(
        r'''
        # API 상세 응답을 JSON 구조 없이 읽기 좋은 본문 Markdown으로 함께 저장한다.
        import html


        _API_TEXT_FIELD_ORDER = {
            "eflaw": (
                "법령명한글", "법령명_한글", "개정문내용", "제개정이유내용",
                "조문내용", "항내용", "호내용", "목내용", "부칙내용", "별표내용",
            ),
            "prec": ("사건명", "판시사항", "판결요지", "참조조문", "참조판례", "판례내용"),
            "expc": ("안건명", "질의요지", "회답", "이유"),
        }
        _TABLE_LAYOUT_ONLY_RE = re.compile(
            r"^[\s│┃║─━═┄┅┈┉╌╍┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋╔╗╚╝╠╣╦╩╬|+_-]+$"
        )
        _KOREAN_GUIDE_RE = re.compile(r"작성\s*방법\s*\(")
        _OFFICIAL_ENGLISH_ANNEX_RE = re.compile(
            r"(?im)^[ \t]*■\s*Enforcement\s+Rules?\s+of\s+"
            r"Licensed\s+Real\s+Estate\s+Agents?\s+Act\b"
        )
        _HTML_TAG_RE = re.compile(
            r"</?(?:p|div|span|strong|b|i|u|table|thead|tbody|tr|td|th|ul|ol|li)\b[^>]*>",
            re.IGNORECASE,
        )


        def _flatten_api_value(value) -> str:
            if value is None:
                return ""
            if isinstance(value, (list, tuple)):
                return "\n".join(
                    text for item in value if (text := _flatten_api_value(item))
                )
            if isinstance(value, dict):
                return "\n".join(
                    text for item in value.values() if (text := _flatten_api_value(item))
                )
            return str(value)


        def _remove_duplicate_official_english(text: str) -> str:
            korean_guide = _KOREAN_GUIDE_RE.search(text)
            if not korean_guide:
                return text
            english_annex = _OFFICIAL_ENGLISH_ANNEX_RE.search(text, korean_guide.end())
            return text[:english_annex.start()] if english_annex else text


        def _clean_api_text(value) -> str:
            text = html.unescape(_remove_duplicate_official_english(_flatten_api_value(value)))
            text = re.sub(r"(?i)<br\s*/?>", "\n", text)
            text = _HTML_TAG_RE.sub(" ", text).replace("\u00a0", " ")
            lines = []
            for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
                line = re.sub(r"[ \t]+", " ", raw_line).strip()
                if not line or _TABLE_LAYOUT_ONLY_RE.fullmatch(line):
                    continue
                line = re.sub(r"[│┃║]", " | ", line)
                line = re.sub(r"[─━═┄┅┈┉╌╍]+", " ", line)
                line = re.sub(r"[┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋╔╗╚╝╠╣╦╩╬]", " ", line)
                line = re.sub(r"\s*\|\s*", " | ", line)
                line = re.sub(r"[ \t]+", " ", line).strip(" |")
                if line:
                    lines.append(line)
            return "\n".join(lines).strip()


        def api_response_to_text(target: str, response: dict) -> str:
            """타깃별 본문 필드만 API 응답 순서대로 모은다."""
            field_order = _API_TEXT_FIELD_ORDER[target]
            fields = set(field_order)
            parts: list[str] = []

            def append_text(value) -> None:
                text = _clean_api_text(value)
                if text and (not parts or parts[-1] != text):
                    parts.append(text)

            def collect_in_response_order(value) -> None:
                if isinstance(value, dict):
                    for key, nested in value.items():
                        if key in fields:
                            append_text(nested)
                        else:
                            collect_in_response_order(nested)
                elif isinstance(value, (list, tuple)):
                    for nested in value:
                        collect_in_response_order(nested)

            def collect_field(value, wanted: str) -> None:
                if isinstance(value, dict):
                    for key, nested in value.items():
                        if key == wanted:
                            append_text(nested)
                        else:
                            collect_field(nested, wanted)
                elif isinstance(value, (list, tuple)):
                    for nested in value:
                        collect_field(nested, wanted)

            if target == "eflaw":
                # 제목은 첫 줄에 두고, 조문-항-호-목은 API의 원래 중첩 순서를 보존한다.
                for field in ("법령명한글", "법령명_한글"):
                    collect_field(response, field)
                fields.difference_update({"법령명한글", "법령명_한글"})
                collect_in_response_order(response)
            else:
                # 판례·해석례는 제목부터 시작하도록 사람이 읽는 순서를 고정한다.
                for field in field_order:
                    collect_field(response, field)
            return "\n\n".join(parts).strip()


        def api_response_to_markdown(target: str, response: dict) -> str:
            """본문 첫 줄을 문서 제목으로 사용한 metadata 없는 Markdown을 만든다."""
            text = api_response_to_text(target, response)
            if not text:
                return ""
            title, separator, body = text.partition("\n")
            return f"# {title}\n" + (f"\n{body.lstrip()}" if separator else "")


        def write_api_markdown_files(
            target: str,
            records: list[dict],
            output_dir: Path,
            filename_field: str,
        ) -> int:
            """상세 API 응답의 본문만 문서별 UTF-8 Markdown으로 저장한다."""
            output_dir.mkdir(parents=True, exist_ok=True)
            written = 0
            for record in records:
                markdown = api_response_to_markdown(target, record.get("본문", {}))
                if not markdown:
                    continue
                stem = re.sub(r'[\\/:*?"<>|]+', "_", str(record.get(filename_field) or "unknown"))
                (output_dir / f"{stem}.md").write_text(markdown + "\n", encoding="utf-8")
                written += 1
            return written
        '''
    )

    precision_query_cell = _source(
        r'''
        # 넓은 단일어보다 임대차 문맥이 포함된 검색어를 사용한다.
        PREC_QUERY_CONFIG: list[dict] = [
            {"query": "임대차보증금", "category": "보증금권리"},
            {"query": "임대차보증금 반환", "category": "보증금권리"},
            {"query": "임차인 대항력", "category": "보증금권리"},
            {"query": "임차인 우선변제권", "category": "보증금권리"},
            {"query": "소액임차인 최우선변제", "category": "보증금권리"},
            {"query": "임대차 확정일자", "category": "보증금권리"},
            {"query": "임차권등기명령", "category": "보증금권리"},
            {"query": "임차인 전입신고", "category": "보증금권리"},
            {"query": "계약갱신청구권", "category": "갱신종료"},
            {"query": "임대차 묵시적 갱신", "category": "갱신종료"},
            {"query": "임대차 갱신거절", "category": "갱신종료"},
            {"query": "임대차 해지", "category": "갱신종료"},
            {"query": "임차인 차임 연체", "category": "갱신종료"},
            {"query": "임대차 차임 증감", "category": "갱신종료"},
            {"query": "전세사기", "category": "전세사기"},
            {"query": "임대차 가장임대차", "category": "전세사기"},
            {"query": "임대차 무권대리", "category": "전세사기"},
            {"query": "임대차 이중계약", "category": "전세사기"},
            {"query": "임대차보증금 사해행위", "category": "전세사기"},
            {"query": "임대차보증금 명의신탁", "category": "전세사기"},
            {"query": "임차인 임의경매", "category": "경매배당"},
            {"query": "임차인 강제경매", "category": "경매배당"},
            {"query": "임차인 배당요구", "category": "경매배당"},
            {"query": "임차인 배당이의", "category": "경매배당"},
            {"query": "임차인 인도명령", "category": "경매배당"},
            {"query": "임대차 건물명도", "category": "경매배당"},
            {"query": "임대인 수선의무", "category": "수선원상회복"},
            {"query": "임대차 원상회복", "category": "수선원상회복"},
            {"query": "임대차 누수", "category": "수선원상회복"},
            {"query": "임대차 하자", "category": "수선원상회복"},
            {"query": "임대차 통상의 손모", "category": "수선원상회복"},
            {"query": "공인중개사 책임", "category": "중개"},
            {"query": "중개대상물 확인설명", "category": "중개"},
            {"query": "부동산 중개보수", "category": "중개"},
        ]

        QUERY_CATEGORY = {item["query"]: item["category"] for item in PREC_QUERY_CONFIG}
        PREC_QUERIES = [item["query"] for item in PREC_QUERY_CONFIG]
        MAX_PER_QUERY = 300

        # 본문검색 결과는 후보군이며 상세 본문에서 관련성을 다시 판정한다.
        prec_items: list[dict] = []
        for spec in PREC_QUERY_CONFIG:
            query = spec["query"]
            hits = fetch_list("prec", query=query, search=2, max_items=MAX_PER_QUERY)
            for hit in hits:
                hit["_category"] = spec["category"]
                hit["_query"] = query
            print(f"[{query}] {len(hits)}건")
            prec_items.extend(hits)
            time.sleep(0.3)

        before = len(prec_items)
        prec_items = dedup_with_provenance(prec_items, "판례일련번호")
        print(f"\n수집 {before}건 → 판례일련번호 중복 제거 {len(prec_items)}건")
        '''
    )

    for cell in notebook["cells"]:
        if cell.get("cell_type") == "code":
            cell["execution_count"] = None
            cell["outputs"] = []

        text = "".join(cell.get("source", []))
        if 'print("OC:", OC)' in text:
            text = text.replace('print("OC:", OC)', 'print("LAW_API_OC 설정: 완료")')

        text = text.replace(
            "| `eflaw` | 현행법령    | 명칭검색(search=1) | `법령ID`             | 법령별 JSON `eflaw/{법령명}.json`          |",
            "| `eflaw` | 현행법령    | 명칭검색(search=1) | `법령ID`             | JSON + 본문 MD `api_eflaw/md/{법령명}.md` |",
        )
        text = text.replace(
            "| `prec`  | 판례        | 본문검색(search=2) | `판례일련번호`       | 카테고리별 `prec/prec_{카테고리}.jsonl`     |",
            "| `prec`  | 판례        | 본문검색(search=2) | `판례일련번호`       | JSONL + 본문 MD `api_prec/md/{ID}.md`     |",
        )
        text = text.replace(
            "| `expc`  | 법령해석례  | 본문검색(search=2) | `법령해석례일련번호` | 카테고리별 `expc/expc_{카테고리}.jsonl`     |",
            "| `expc`  | 법령해석례  | 본문검색(search=2) | `법령해석례일련번호` | JSONL + 본문 MD `api_expc/md/{ID}.md`     |",
        )
        text = text.replace(
            "상세 조회 → 2차 검증 → 카테고리별 저장**",
            "상세 조회 → 2차 검증 → JSON/JSONL 및 본문 MD 저장**",
        )
        if "def dedup_by" in text and "def dedup_with_provenance" not in text:
            cell["source"] = cell.get("source", []) + ["\n"] + provenance_helper
            text = "".join(cell["source"])

        if "def fetch_details(" in text:
            helper_markers = (
                "# API 상세 응답을 JSON 구조 없이 읽기 좋은 본문 Markdown으로 함께 저장한다.",
            )
            marker_indexes = [text.index(marker) for marker in helper_markers if marker in text]
            if marker_indexes:
                text = text[:min(marker_indexes)].rstrip() + "\n\n"
            text += "".join(text_export_helper)
            cell["source"] = text.splitlines(keepends=True)

        if (
            "PREC_QUERIES" in text
            and (
                "QUERY_CATEGORY: dict[str, str]" in text
                or "PREC_QUERY_CONFIG: list[dict]" in text
            )
        ):
            cell["source"] = precision_query_cell
            continue

        text = text.replace(
            'prec_details = dedup_by(prec_details, "판례일련번호")',
            'prec_details = dedup_with_provenance(prec_details, "판례일련번호")',
        )
        text = text.replace(
            'expc_items = dedup_by(expc_items, "법령해석례일련번호")',
            'expc_items = dedup_with_provenance(expc_items, "법령해석례일련번호")',
        )
        text = text.replace(
            'expc_details = dedup_by(expc_details, "법령해석례일련번호")',
            'expc_details = dedup_with_provenance(expc_details, "법령해석례일련번호")',
        )
        text = text.replace(
            "_rate_limiter = RateLimiter(max_rps=5.0)",
            "_rate_limiter = RateLimiter(max_rps=2.0)",
        )
        if "DETAIL_MAX_ATTEMPTS" not in text:
            text = text.replace(
                '        _rate_limiter.wait()\n'
                '        body = api(target, "detail", {"ID": doc_id})\n'
                '        if _is_invalid_body(body):\n'
                '            return None\n'
                '        _strip_link_fields(item)\n'
                '        return {**item, "본문": body}\n',
                '        DETAIL_MAX_ATTEMPTS = 4\n'
                '        for attempt in range(DETAIL_MAX_ATTEMPTS):\n'
                '            _rate_limiter.wait()\n'
                '            body = api(target, "detail", {"ID": doc_id})\n'
                '            if not _is_invalid_body(body):\n'
                '                _strip_link_fields(item)\n'
                '                return {**item, "본문": body}\n'
                '            if attempt + 1 < DETAIL_MAX_ATTEMPTS:\n'
                '                time.sleep(1.5 * (attempt + 1))\n'
                '        return None\n',
            )
        text = text.replace(
            'prec_files = sorted(PREC_DIR.glob("prec_*.jsonl"))',
            'prec_files = sorted(\n'
            '    path for path in PREC_DIR.glob("prec_*.jsonl")\n'
            '    if path.name != "prec_checkpoint.jsonl"\n'
            ')',
        )
        if "법령 저장 완료:" in text and "write_api_markdown_files(" not in text:
            text = text.replace(
                'print(f"법령 저장 완료: {len(eflaw_details)}개 파일 → {EFLAW_DIR}")',
                'eflaw_md_count = write_api_markdown_files(\n'
                '    "eflaw", eflaw_details, EFLAW_DIR / "md", "법령명한글"\n'
                ')\n'
                'print(f"법령 저장 완료: JSON {len(eflaw_details)}개 + MD {eflaw_md_count}개 → {EFLAW_DIR}")',
            )
        if "체크포인트 유지" in text and "write_api_markdown_files(" not in text:
            text = text.replace(
                'print(f"\\n체크포인트 유지 → {PREC_CHECKPOINT}  (재실행 시 resume 용)")',
                'prec_md_count = write_api_markdown_files(\n'
                '    "prec", prec_details, PREC_DIR / "md", "판례일련번호"\n'
                ')\n'
                'print(f"MD 저장 → {PREC_DIR / \'md\'}  ({prec_md_count}개)")\n'
                'print(f"\\n체크포인트 유지 → {PREC_CHECKPOINT}  (재실행 시 resume 용)")',
            )
        if "법령해석례 저장 완료:" in text and "write_api_markdown_files(" not in text:
            text = text.replace(
                'print(f"\\n법령해석례 저장 완료: 총 {len(expc_details)}건 → {EXPC_DIR}")',
                'expc_md_count = write_api_markdown_files(\n'
                '    "expc", expc_details, EXPC_DIR / "md", "법령해석례일련번호"\n'
                ')\n'
                'print(f"\\n법령해석례 저장 완료: JSONL {len(expc_details)}건 + "\n'
                '      f"MD {expc_md_count}개 → {EXPC_DIR}")',
            )
        if cell.get("source") is not None:
            cell["source"] = text.splitlines(keepends=True)

    note = "## 5. 다음 단계: 전처리 노트북"
    if not any(note in "".join(cell.get("source", [])) for cell in notebook["cells"]):
        notebook["cells"].append(
            _markdown(
                '''
                ## 5. 다음 단계: 전처리 노트북

                이 노트북은 원본 수집까지만 담당한다. 저장된 원본은
                `02_preprocess_legal_api.ipynb`에서 정제·metadata 생성 후
                `03_build_kb_chunks.ipynb`에서 구조 기반으로 청킹한다.
                '''
            )
        )

    path.write_text(json.dumps(notebook, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")


def build_preprocess_notebook() -> None:
    cells = [
        _markdown(
            '''
            # 02. 법률 API 전처리와 metadata

            `data/01_raw/api_eflaw`, `api_expc`, `api_prec` 원본을 읽어
            법률 구조를 보존한 `page_content + metadata` JSONL로 변환한다.
            원본 파일은 수정하지 않는다.
            '''
        ),
        _markdown(
            '''
            ## 0. 경로와 분류 설정

            노트북을 프로젝트 루트 또는 `notebooks/`에서 실행해도 같은 경로를 사용한다.
            '''
        ),
        _code(
            r'''
            # 경로와 분류 기준
            import html
            import json
            import re
            import unicodedata
            from collections import Counter
            from pathlib import Path
            from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

            import pandas as pd

            ROOT = Path.cwd().resolve()
            if ROOT.name == "notebooks":
                ROOT = ROOT.parent

            RAW_DIR = ROOT / "data" / "01_raw"
            EFLAW_DIR = RAW_DIR / "api_eflaw"
            EXPC_DIR = RAW_DIR / "api_expc"
            PREC_DIR = RAW_DIR / "api_prec"
            OUTPUT_DIR = ROOT / "data" / "03_processed" / "legal_api"
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            LAW_STAGE_MAP = {
                "주택임대차보호법": ("both", ["갱신종료", "보증금권리", "경매배당"]),
                "주택임대차보호법_시행령": ("both", ["갱신종료", "보증금권리"]),
                "민법": ("both", []),
                "부동산등기법": ("pre", ["등기"]),
                "부동산등기규칙": ("pre", ["등기"]),
                "공인중개사법": ("pre", ["중개"]),
                "공인중개사법_시행규칙": ("pre", ["중개"]),
                "민사집행법": ("post", ["경매배당"]),
                "국세징수법": ("post", ["경매배당"]),
                "전세사기피해자_지원_및_주거안정에_관한_특별법": ("pre", ["전세사기"]),
                "부동산_거래신고_등에_관한_법률": ("pre", ["신고"]),
                "상가건물_임대차보호법": ("both", ["상가임대차"]),
                "주민등록법": ("pre", ["대항력"]),
                "민간임대주택에_관한_특별법": ("both", []),
                "집합건물의_소유_및_관리에_관한_법률": ("post", []),
                "주택도시기금법": ("pre", []),
            }

            TOPIC_STAGE_MAP = {
                "전세사기": "pre", "중개": "pre", "갱신종료": "post",
                "경매배당": "post", "수선원상회복": "post",
                "보증금권리": "both", "상가임대차": "both",
            }

            print("원본:", RAW_DIR)
            print("출력:", OUTPUT_DIR)
            '''
        ),
        _markdown(
            '''
            ## 1. 원본 파일과 건수 확인

            판례 체크포인트는 카테고리 파일과 중복되므로 건수와 전처리 대상에서 제외한다.
            '''
        ),
        _code(
            r'''
            # 원본 파일 목록과 JSONL 행 수
            def count_jsonl(path: Path) -> int:
                with path.open(encoding="utf-8") as file:
                    return sum(1 for line in file if line.strip())


            law_files = sorted(EFLAW_DIR.glob("*.json"))
            expc_files = sorted(EXPC_DIR.glob("expc_*.jsonl"))
            prec_files = sorted(
                path for path in PREC_DIR.glob("prec_*.jsonl")
                if path.name != "prec_checkpoint.jsonl"
            )

            inventory = pd.DataFrame([
                {"type": "statute", "files": len(law_files), "records": len(law_files)},
                {"type": "interpretation", "files": len(expc_files), "records": sum(map(count_jsonl, expc_files))},
                {"type": "precedent", "files": len(prec_files), "records": sum(map(count_jsonl, prec_files))},
            ])
            display(inventory)

            assert law_files, "법령 원본이 없습니다."
            assert expc_files, "해석례 원본이 없습니다."
            assert prec_files, "판례 원본이 없습니다."
            assert all(path.name != "prec_checkpoint.jsonl" for path in prec_files)
            '''
        ),
        _markdown(
            '''
            ## 2. 공통 정제 함수

            법률 번호와 특수문자는 유지하고 HTML, 줄바꿈, 불필요한 공백만 정리한다.
            `NFKC`는 원문 번호를 바꿀 수 있어 사용하지 않는다.
            '''
        ),
        _code(
            r'''
            # 공통 정제와 metadata 보조 함수
            REVISION_RE = re.compile(r"<\s*(?:(?:일부|전문)?개정|신설|삭제)[^>]*>")
            LAW_NAME_RE = re.compile(r"「([^」]+)」")
            ARTICLE_RE = re.compile(r"제\s*\d+조(?:의\d+)?")
            DELETED_ARTICLE_RE = re.compile(
                r"^제\s*\d+조(?:의\d+)?(?:\([^\n]*\))?\s*삭제[.。]?$"
            )


            def as_list(value):
                if value is None:
                    return []
                return value if isinstance(value, list) else [value]


            def flatten_text(value) -> str:
                """중첩 list로 내려오는 부칙·별표 내용을 줄 단위 문자열로 만든다."""
                if value is None:
                    return ""
                if isinstance(value, (list, tuple)):
                    return "\n".join(part for item in value if (part := flatten_text(item)))
                if isinstance(value, dict):
                    if "content" in value:
                        return flatten_text(value["content"])
                    return "\n".join(part for item in value.values() if (part := flatten_text(item)))
                return str(value)


            def clean_text(value, *, preserve_spacing: bool = False) -> str:
                text = unicodedata.normalize("NFC", html.unescape(flatten_text(value)))
                text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
                text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
                text = re.sub(r"</(?:p|div|li|tr|h[1-6])>", "\n", text, flags=re.IGNORECASE)
                text = re.sub(r"</(?:td|th)>", " | ", text, flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", "", text)

                lines = []
                for line in text.split("\n"):
                    line = line.rstrip() if preserve_spacing else re.sub(r"[ \t]+", " ", line).strip()
                    lines.append(line)
                text = "\n".join(lines)
                return re.sub(r"\n{3,}", "\n\n", text).strip()


            TABLE_VERTICAL_RE = re.compile(r"[│┃║]")
            TABLE_HORIZONTAL_RE = re.compile(r"[─━═┄┅┈┉╌╍]+")
            TABLE_JUNCTION_RE = re.compile(r"[┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋╔╗╚╝╠╣╦╩╬]")
            TABLE_LAYOUT_ONLY_RE = re.compile(
                r"^[\s│┃║─━═┄┅┈┉╌╍┌┐└┘├┤┬┴┼┏┓┗┛┣┫┳┻╋╔╗╚╝╠╣╦╩╬|+_-]+$"
            )
            KOREAN_GUIDE_RE = re.compile(r"작성\s*방법\s*\(")
            OFFICIAL_ENGLISH_ANNEX_RE = re.compile(
                r"(?im)^[ \t]*■\s*Enforcement\s+Rules?\s+of\s+"
                r"Licensed\s+Real\s+Estate\s+Agents?\s+Act\b"
            )


            def remove_duplicate_official_english(value: str) -> str:
                """한국어 서식 뒤에 이어지는 공식 영문 중복본만 제외한다."""
                korean_guide = KOREAN_GUIDE_RE.search(value)
                if not korean_guide:
                    return value

                english_annex = OFFICIAL_ENGLISH_ANNEX_RE.search(value, korean_guide.end())
                if not english_annex:
                    return value
                return value[:english_annex.start()].rstrip()


            def clean_table_text(value) -> str:
                """별표·서식의 텍스트는 보존하고 정렬용 공백과 표 장식만 제거한다."""
                raw = flatten_text(value)
                # 한국어 확인ㆍ설명서와 동일한 공식 영문 별첨은 검색용 텍스트에서 제외한다.
                raw = remove_duplicate_official_english(raw)
                text = clean_text(raw, preserve_spacing=True)
                lines = []
                for raw_line in text.splitlines():
                    line = re.sub(r"[ \t]+", " ", raw_line).strip()
                    if not line or TABLE_LAYOUT_ONLY_RE.fullmatch(line):
                        continue

                    line = TABLE_VERTICAL_RE.sub(" | ", line)
                    line = TABLE_HORIZONTAL_RE.sub(" ", line)
                    line = TABLE_JUNCTION_RE.sub(" ", line)
                    line = re.sub(r"\s*\|\s*", " | ", line)
                    line = re.sub(r"(?:\s*\|\s*){2,}", " | ", line)
                    line = re.sub(r"[ \t]+", " ", line).strip(" |")
                    if line and not TABLE_LAYOUT_ONLY_RE.fullmatch(line):
                        lines.append(line)

                return "\n".join(lines).strip()


            def clean_with_revision_notes(value, *, preserve_spacing: bool = False):
                raw = flatten_text(value)
                notes = list(dict.fromkeys(match.group(0) for match in REVISION_RE.finditer(raw)))
                return clean_text(REVISION_RE.sub("", raw), preserve_spacing=preserve_spacing), notes


            def normalize_date(value):
                value = str(value or "").strip()
                digits = re.sub(r"\D", "", value)
                if len(digits) >= 8:
                    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
                return value or None


            def year_of(value):
                date = normalize_date(value)
                return int(date[:4]) if date and date[:4].isdigit() else None


            def compact_metadata(metadata: dict) -> dict:
                return {
                    key: value for key, value in metadata.items()
                    if value is not None and value != ""
                }


            def sanitize_url(value):
                value = str(value or "").strip()
                if not value:
                    return None
                parts = urlsplit(value)
                query = [(key, val) for key, val in parse_qsl(parts.query) if key.lower() != "oc"]
                return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


            def unique_values(*values):
                output = []
                for value in values:
                    for item in as_list(value):
                        if item and item not in output:
                            output.append(item)
                return output
            '''
        ),
        _markdown(
            '''
            ## 3. 법령 전처리

            본칙 조문은 항·호·목 순서를 유지한다. 부칙과 별표는 별도 레코드로 만들어
            나중에 서로 다른 섹션이 한 청크에 섞이지 않게 한다.
            '''
        ),
        _code(
            r'''
            # 법령 조문·부칙·별표 렌더링
            def article_label(article: dict) -> str:
                number = str(article.get("조문번호", "")).lstrip("0") or "0"
                branch = str(article.get("조문가지번호", "")).lstrip("0")
                suffix = f"의{branch}" if branch else ""
                return f"제{number}조{suffix}"


            def build_article_text(article: dict):
                parts = []
                revision_notes = []

                def append(value):
                    text, notes = clean_with_revision_notes(value)
                    if text and text not in parts:
                        parts.append(text)
                    revision_notes.extend(notes)

                append(article.get("조문내용"))
                for paragraph in as_list(article.get("항")):
                    append(paragraph.get("항내용"))
                    for item in as_list(paragraph.get("호")):
                        append(item.get("호내용"))
                        for subitem in as_list(item.get("목")):
                            append(subitem.get("목내용"))
                for item in as_list(article.get("호")):
                    append(item.get("호내용"))
                    for subitem in as_list(item.get("목")):
                        append(subitem.get("목내용"))
                return "\n".join(parts).strip(), list(dict.fromkeys(revision_notes))


            def build_statute_records(path: Path) -> list[dict]:
                raw = json.loads(path.read_text(encoding="utf-8"))
                law = raw.get("본문", {}).get("법령", {})
                basic = law.get("기본정보", {})

                law_name = raw.get("법령명한글") or basic.get("법령명_한글") or path.stem
                law_id = str(raw.get("법령ID") or basic.get("법령ID") or "")
                effective_date = normalize_date(raw.get("시행일자") or basic.get("시행일자"))
                stage, issue = LAW_STAGE_MAP.get(path.stem, ("both", []))

                common = {
                    "source_type": "statute",
                    "parent_id": law_id,
                    "doc_title": law_name,
                    "source_org": raw.get("소관부처명") or clean_text(basic.get("소관부처")),
                    "doc_year": year_of(effective_date),
                    "authority": "binding",
                    "stage": stage,
                    "issue": issue,
                    "source_file": path.name,
                    "law_id": law_id,
                    "law_name": law_name,
                    "law_type": raw.get("법령구분명") or clean_text(basic.get("법종구분")),
                    "effective_date": effective_date,
                    "promulgation_date": normalize_date(raw.get("공포일자") or basic.get("공포일자")),
                    "promulgation_no": raw.get("공포번호") or basic.get("공포번호"),
                    "revision_type": raw.get("제개정구분명") or basic.get("제개정구분"),
                    "ministry": raw.get("소관부처명") or clean_text(basic.get("소관부처")),
                }

                records = []
                articles = as_list(law.get("조문", {}).get("조문단위"))
                for article in articles:
                    if article.get("조문여부") != "조문":
                        continue
                    content, notes = build_article_text(article)
                    if not content or DELETED_ARTICLE_RE.fullmatch(content):
                        continue
                    label = article_label(article)
                    article_key = str(article.get("조문키") or label)
                    source_id = f"{law_id}:article:{article_key}"
                    metadata = compact_metadata({
                        **common,
                        "source_id": source_id,
                        "record_id": source_id,
                        "section": "article",
                        "article": label,
                        "article_title": article.get("조문제목"),
                        "article_key": article_key,
                        "article_effective_date": normalize_date(article.get("조문시행일자")),
                        "revision_notes": notes,
                    })
                    records.append({"page_content": content, "metadata": metadata})

                supplements = as_list(law.get("부칙", {}).get("부칙단위"))
                for index, supplement in enumerate(supplements):
                    content, notes = clean_with_revision_notes(supplement.get("부칙내용"))
                    if not content:
                        continue
                    key = str(supplement.get("부칙키") or index)
                    source_id = f"{law_id}:supplement:{key}"
                    metadata = compact_metadata({
                        **common,
                        "source_id": source_id,
                        "record_id": source_id,
                        "section": "supplement",
                        "section_title": "부칙",
                        "supplement_key": key,
                        "supplement_promulgation_date": normalize_date(supplement.get("부칙공포일자")),
                        "supplement_promulgation_no": supplement.get("부칙공포번호"),
                        "revision_notes": notes,
                    })
                    records.append({"page_content": content, "metadata": metadata})

                annexes = as_list(law.get("별표", {}).get("별표단위"))
                for index, annex in enumerate(annexes):
                    content = clean_table_text(annex.get("별표내용"))
                    if not content:
                        continue
                    key = str(annex.get("별표키") or index)
                    source_id = f"{law_id}:annex:{key}"
                    links = unique_values(
                        sanitize_url(annex.get("별표서식파일링크")),
                        sanitize_url(annex.get("별표서식PDF파일링크")),
                        sanitize_url(annex.get("별표서식이미지파일링크")),
                    )
                    metadata = compact_metadata({
                        **common,
                        "source_id": source_id,
                        "record_id": source_id,
                        "section": "annex",
                        "section_title": clean_text(annex.get("별표제목") or annex.get("별표제목문자열")),
                        "annex_key": key,
                        "annex_number": annex.get("별표번호"),
                        "annex_branch_number": annex.get("별표가지번호"),
                        "annex_type": annex.get("별표구분"),
                        "annex_effective_date": normalize_date(annex.get("별표시행일자")),
                        "attachment_urls": links,
                    })
                    records.append({"page_content": content, "metadata": metadata})
                return records


            statute_records = []
            for path in law_files:
                records = build_statute_records(path)
                statute_records.extend(records)
                counts = Counter(record["metadata"]["section"] for record in records)
                print(f"{path.stem}: {dict(counts)}")

            print("법령 전처리 레코드:", len(statute_records))
            '''
        ),
        _markdown(
            '''
            ## 4. 판례 전처리와 관련성

            판례 파일을 ID로 다시 합쳐 검색 provenance를 보존한다. 관련성은 자동 삭제
            기준이 아니라 `relevant`, `candidate`, `excluded` 검색 우선순위로 사용한다.
            '''
        ),
        _code(
            r'''
            # 판례 관련성 기준
            RENTAL_ANCHORS = [
                "임대차", "임대인", "임차인", "임차권", "보증금", "차임",
                "전세", "월세", "중개", "대항력", "우선변제",
            ]
            ANCHOR_PATTERNS = {
                "전세": re.compile(r"전세(?!계)"),
            }

            TOPIC_TERMS = {
                "보증금권리": ["보증금", "대항력", "우선변제", "최우선변제", "확정일자", "임차권등기", "전입신고"],
                "갱신종료": ["갱신", "해지", "차임", "연체", "계약종료"],
                "전세사기": ["전세사기", "가장임대차", "무권대리", "이중계약", "사해행위", "명의신탁"],
                "경매배당": ["경매", "배당", "인도명령", "건물명도", "우선변제"],
                "수선원상회복": ["수선", "원상회복", "누수", "하자", "통상의 손모"],
                "중개": ["공인중개사", "중개대상물", "확인설명", "중개보수", "중개업자"],
            }


            def contains_anchor(text: str, anchor: str) -> bool:
                pattern = ANCHOR_PATTERNS.get(anchor)
                return bool(pattern.search(text)) if pattern else anchor in text


            def iter_jsonl(path: Path):
                with path.open(encoding="utf-8") as file:
                    for line in file:
                        if line.strip():
                            yield json.loads(line)


            def merge_raw_records(paths: list[Path], id_fields: list[str]) -> list[dict]:
                merged = {}
                for path in paths:
                    file_category = path.stem.split("_", 1)[-1]
                    for raw in iter_jsonl(path):
                        detail = raw.get("본문", {})
                        if isinstance(detail, dict):
                            detail = detail.get("PrecService") or detail.get("ExpcService") or detail
                        record_id = next(
                            (str(raw.get(key) or detail.get(key) or "").strip() for key in id_fields
                             if raw.get(key) or detail.get(key)),
                            "",
                        )
                        if not record_id:
                            continue
                        if record_id not in merged:
                            merged[record_id] = dict(raw)
                            merged[record_id]["_source_files"] = []
                            merged[record_id]["_categories"] = []
                            merged[record_id]["_queries"] = []
                        item = merged[record_id]
                        item["_source_files"] = unique_values(item["_source_files"], path.name)
                        item["_categories"] = unique_values(
                            item["_categories"], raw.get("_categories"), raw.get("_category"), file_category
                        )
                        item["_queries"] = unique_values(
                            item["_queries"], raw.get("_queries"), raw.get("_query")
                        )
                return list(merged.values())


            def classify_precedent(title, holdings, summary, references, body, categories, court):
                high_sections = {
                    "사건명": title,
                    "판시사항": holdings,
                    "판결요지": summary,
                    "참조조문": references,
                }
                all_text = "\n".join([*high_sections.values(), body])
                topic_terms = list(dict.fromkeys(
                    term for category in categories for term in TOPIC_TERMS.get(category, [])
                ))
                matched_anchors = [
                    term for term in RENTAL_ANCHORS
                    if contains_anchor(all_text, term)
                ]
                matched_anchor_sections = [
                    section for section, text in high_sections.items()
                    if any(contains_anchor(text, term) for term in RENTAL_ANCHORS)
                ]
                matched_terms = [term for term in topic_terms if term in all_text]
                matched_sections = [
                    section for section, text in high_sections.items()
                    if any(term in text for term in topic_terms)
                ]

                score = len(matched_anchors)
                for section, weight in {"사건명": 3, "판시사항": 5, "판결요지": 4, "참조조문": 4}.items():
                    if any(term in high_sections[section] for term in topic_terms):
                        score += weight
                if any(term in body for term in topic_terms):
                    score += 1
                if "대법원" in court:
                    score += 1

                if matched_anchor_sections and matched_sections and score >= 5:
                    level, reason = "relevant", None
                elif matched_anchor_sections or (matched_anchors and matched_terms):
                    level, reason = "candidate", "관련 문맥은 있으나 핵심 쟁점 여부가 불명확함"
                else:
                    level, reason = "excluded", "임대차 핵심어와 카테고리 쟁점을 확인하지 못함"
                return (
                    level, score, matched_anchors, matched_anchor_sections,
                    matched_terms, matched_sections, reason,
                )
            '''
        ),
        _code(
            r'''
            # 판례 섹션과 metadata 생성
            def build_precedent_records(paths: list[Path]):
                processed, excluded = [], []
                raw_records = merge_raw_records(paths, ["판례일련번호", "판례정보일련번호"])

                for raw in raw_records:
                    detail = raw.get("본문", {})
                    detail = detail.get("PrecService", detail) if isinstance(detail, dict) else {}

                    source_id = str(raw.get("판례일련번호") or detail.get("판례정보일련번호") or "")
                    title = clean_text(detail.get("사건명") or raw.get("사건명"))
                    holdings = clean_text(detail.get("판시사항"))
                    summary = clean_text(detail.get("판결요지"))
                    body = clean_text(detail.get("판례내용"))
                    references = clean_text(detail.get("참조조문"))
                    reference_cases = clean_text(detail.get("참조판례"))
                    court = clean_text(detail.get("법원명") or raw.get("법원명"))
                    categories = unique_values(raw.get("_categories"), raw.get("_category"))
                    queries = unique_values(raw.get("_queries"), raw.get("_query"))
                    stage_values = {TOPIC_STAGE_MAP.get(category, "both") for category in categories}
                    stage = stage_values.pop() if len(stage_values) == 1 else "both"

                    (
                        level, score, anchors, anchor_sections,
                        terms, matched_sections, reason,
                    ) = classify_precedent(
                        title, holdings, summary, references, body, categories, court
                    )
                    metadata = compact_metadata({
                        "source_type": "precedent",
                        "source_id": source_id,
                        "parent_id": source_id,
                        "doc_title": title or raw.get("사건명") or source_id,
                        "source_org": court,
                        "doc_year": year_of(detail.get("선고일자") or raw.get("선고일자")),
                        "authority": "binding" if "대법원" in court else "persuasive",
                        "stage": stage,
                        "issue": categories,
                        "source_file": raw.get("_source_files", []),
                        "precedent_id": source_id,
                        "court": court,
                        "case_no": detail.get("사건번호") or raw.get("사건번호"),
                        "decision_date": normalize_date(detail.get("선고일자") or raw.get("선고일자")),
                        "decision_type": detail.get("선고") or raw.get("선고"),
                        "case_type": detail.get("사건종류명") or raw.get("사건종류명"),
                        "judgment_type": detail.get("판결유형") or raw.get("판결유형"),
                        "reference_laws": references,
                        "reference_cases": reference_cases,
                        "collection_categories": categories,
                        "collection_queries": queries,
                        "relevance_level": level,
                        "relevance_score": score,
                        "matched_anchors": anchors,
                        "matched_anchor_sections": anchor_sections,
                        "matched_terms": terms,
                        "matched_sections": matched_sections,
                        "exclusion_reason": reason,
                    })

                    sections = [
                        ("holding", "판시사항", holdings),
                        ("summary", "판결요지", summary),
                        ("body", "판례내용", body),
                    ]
                    if level == "excluded":
                        excluded.append({"metadata": metadata})
                        continue
                    for section, section_title, content in sections:
                        if not content:
                            continue
                        record_metadata = compact_metadata({
                            **metadata,
                            "section": section,
                            "section_title": section_title,
                            "record_id": f"precedent:{source_id}:{section}",
                        })
                        processed.append({"page_content": content, "metadata": record_metadata})
                return processed, excluded, raw_records


            precedent_records, excluded_precedents, raw_precedents = build_precedent_records(prec_files)
            status_by_id = {
                item["metadata"]["source_id"]: item["metadata"]["relevance_level"]
                for item in precedent_records
            }
            status_by_id.update({
                item["metadata"]["source_id"]: "excluded"
                for item in excluded_precedents
            })
            status_counts = Counter(status_by_id.values())
            print("판례 고유 ID:", len(raw_precedents))
            print("관련성 분류:", dict(status_counts))
            print("판례 전처리 섹션:", len(precedent_records))
            '''
        ),
        _markdown(
            '''
            ## 5. 법령해석례 전처리

            질의요지·회답·이유를 서로 다른 레코드로 저장한다. 상세 응답 필드를 우선하고
            목록 필드는 누락 시 fallback으로 사용한다.
            '''
        ),
        _code(
            r'''
            # 해석례 섹션과 metadata 생성
            def build_interpretation_records(paths: list[Path]):
                processed = []
                raw_records = merge_raw_records(paths, ["법령해석례일련번호"])
                for raw in raw_records:
                    detail = raw.get("본문", {})
                    detail = detail.get("ExpcService", detail) if isinstance(detail, dict) else {}

                    source_id = str(raw.get("법령해석례일련번호") or detail.get("법령해석례일련번호") or "")
                    title = clean_text(detail.get("안건명") or raw.get("안건명"))
                    categories = unique_values(raw.get("_categories"), raw.get("_category"))
                    queries = unique_values(raw.get("_queries"), raw.get("_query"))
                    stage_values = {TOPIC_STAGE_MAP.get(category, "both") for category in categories}
                    stage = stage_values.pop() if len(stage_values) == 1 else "both"
                    law_names = list(dict.fromkeys(LAW_NAME_RE.findall(title)))
                    article_names = list(dict.fromkeys(ARTICLE_RE.findall(title)))
                    decision_date = normalize_date(
                        detail.get("해석일자")
                        or raw.get("회신일자")
                        or detail.get("등록일시")
                    )

                    metadata = compact_metadata({
                        "source_type": "interpretation",
                        "source_id": source_id,
                        "parent_id": source_id,
                        "doc_title": title or source_id,
                        "source_org": detail.get("해석기관명") or raw.get("회신기관명"),
                        "doc_year": year_of(decision_date),
                        "authority": "persuasive",
                        "stage": stage,
                        "issue": categories,
                        "source_file": raw.get("_source_files", []),
                        "interpretation_id": source_id,
                        "case_no": detail.get("안건번호") or raw.get("안건번호"),
                        "interpreting_agency": detail.get("해석기관명") or raw.get("회신기관명"),
                        "requesting_agency": detail.get("질의기관명") or raw.get("질의기관명"),
                        "decision_date": decision_date,
                        "registration_date": normalize_date(detail.get("등록일시")),
                        "law_name": ", ".join(law_names) or None,
                        "article": ", ".join(article_names) or None,
                        "collection_categories": categories,
                        "collection_queries": queries,
                    })

                    for section, section_title, field in [
                        ("question", "질의요지", "질의요지"),
                        ("answer", "회답", "회답"),
                        ("reason", "이유", "이유"),
                    ]:
                        content = clean_text(detail.get(field))
                        if not content:
                            continue
                        record_metadata = compact_metadata({
                            **metadata,
                            "section": section,
                            "section_title": section_title,
                            "record_id": f"interpretation:{source_id}:{section}",
                        })
                        processed.append({"page_content": content, "metadata": record_metadata})
                return processed, raw_records


            interpretation_records, raw_interpretations = build_interpretation_records(expc_files)
            print("해석례 고유 ID:", len(raw_interpretations))
            print("해석례 전처리 섹션:", len(interpretation_records))
            '''
        ),
        _markdown(
            '''
            ## 6. 품질 검증

            저장 전에 빈 본문, 필수 metadata, record ID 중복과 원본 고유 ID 보존 여부를 검사한다.
            '''
        ),
        _code(
            r'''
            # 전처리 결과 검증
            REQUIRED_METADATA = {
                "source_type", "source_id", "parent_id", "doc_title",
                "source_org", "doc_year", "authority", "stage", "issue",
                "section", "source_file", "record_id",
            }


            def validate_records(records: list[dict], name: str):
                empty_content = [item for item in records if not item.get("page_content", "").strip()]
                missing_metadata = []
                for item in records:
                    metadata = item.get("metadata", {})
                    missing = sorted(key for key in REQUIRED_METADATA if key not in metadata)
                    if missing:
                        missing_metadata.append((metadata.get("record_id"), missing))
                record_ids = [item["metadata"].get("record_id") for item in records]
                duplicate_ids = len(record_ids) - len(set(record_ids))

                print(f"[{name}] 레코드={len(records)}, 빈 본문={len(empty_content)}, "
                      f"metadata 누락={len(missing_metadata)}, record_id 중복={duplicate_ids}")
                assert not empty_content
                assert not missing_metadata, missing_metadata[:5]
                assert duplicate_ids == 0


            validate_records(statute_records, "statute")
            validate_records(interpretation_records, "interpretation")
            validate_records(precedent_records, "precedent")

            deleted_only_articles = [
                item["metadata"]["record_id"]
                for item in statute_records
                if item["metadata"].get("section") == "article"
                and DELETED_ARTICLE_RE.fullmatch(item["page_content"].strip())
            ]
            assert not deleted_only_articles, deleted_only_articles[:5]

            precedent_ids = {
                item["metadata"]["source_id"] for item in precedent_records
            } | {
                item["metadata"]["source_id"] for item in excluded_precedents
            }
            assert len(precedent_ids) == len(raw_precedents)
            assert len({item["metadata"]["source_id"] for item in interpretation_records}) == len(raw_interpretations)

            quality_summary = pd.DataFrame([
                {"type": "statute", "documents": len(law_files), "sections": len(statute_records)},
                {"type": "interpretation", "documents": len(raw_interpretations), "sections": len(interpretation_records)},
                {"type": "precedent", "documents": len(raw_precedents), "sections": len(precedent_records),
                 "excluded": len(excluded_precedents)},
            ])
            display(quality_summary)
            '''
        ),
        _markdown(
            '''
            ## 7. 샘플 확인

            유형별 첫 레코드와 가장 긴 레코드를 확인한다. 판례는 관련성 등급별 표본도 함께 본다.
            '''
        ),
        _code(
            r'''
            # 유형별 첫 항목과 가장 긴 항목
            def preview_records(records: list[dict], name: str):
                longest = max(records, key=lambda item: len(item["page_content"]))
                first = records[0]
                display(pd.DataFrame([
                    {
                        "type": name,
                        "sample": "first",
                        "title": first["metadata"]["doc_title"],
                        "section": first["metadata"]["section"],
                        "chars": len(first["page_content"]),
                        "text": first["page_content"][:300],
                    },
                    {
                        "type": name,
                        "sample": "longest",
                        "title": longest["metadata"]["doc_title"],
                        "section": longest["metadata"]["section"],
                        "chars": len(longest["page_content"]),
                        "text": longest["page_content"][:300],
                    },
                ]))


            preview_records(statute_records, "statute")
            preview_records(interpretation_records, "interpretation")
            preview_records(precedent_records, "precedent")

            relevance_samples = []
            for level in ["relevant", "candidate"]:
                item = next(
                    (record for record in precedent_records
                     if record["metadata"]["relevance_level"] == level),
                    None,
                )
                if item:
                    relevance_samples.append({
                        "level": level,
                        "title": item["metadata"]["doc_title"],
                        "score": item["metadata"]["relevance_score"],
                        "terms": item["metadata"].get("matched_terms", []),
                    })
            display(pd.DataFrame(relevance_samples))
            '''
        ),
        _markdown(
            '''
            ## 8. 전처리 결과 저장

            세 유형은 임베딩 파이프가 읽을 수 있는 `page_content + metadata` JSONL로 분리 저장한다.
            제외 판례는 원본 ID와 제외 사유만 별도 보관한다.
            '''
        ),
        _code(
            r'''
            # JSONL 저장
            def write_jsonl(path: Path, records: list[dict]) -> None:
                with path.open("w", encoding="utf-8", newline="\n") as file:
                    for record in records:
                        file.write(json.dumps(record, ensure_ascii=False) + "\n")


            outputs = {
                "processed_eflaw.jsonl": statute_records,
                "processed_expc.jsonl": interpretation_records,
                "processed_prec.jsonl": precedent_records,
                "excluded_prec.jsonl": excluded_precedents,
            }
            for filename, records in outputs.items():
                path = OUTPUT_DIR / filename
                write_jsonl(path, records)
                print(f"{filename}: {len(records)}건 → {path}")
            '''
        ),
    ]
    _write_notebook(NOTEBOOK_DIR / "02_preprocess_legal_api.ipynb", cells)


def build_chunk_notebook() -> None:
    cells = [
        _markdown(
            '''
            # 03. 법률 API 구조 기반 청킹

            `02_preprocess_legal_api.ipynb` 결과를 읽어 법령·해석례·판례 구조에 맞게 청킹한다.
            섹션 경계를 우선하고 길이 제한은 긴 본문에만 적용한다.
            '''
        ),
        _markdown(
            '''
            ## 0. 경로와 청킹 설정

            숫자는 글자 수 기준이며 `token_len`도 함께 기록해 분포를 확인한다.
            '''
        ),
        _code(
            r'''
            # 경로와 유형별 청킹 기준
            import json
            from collections import Counter, defaultdict
            from pathlib import Path

            import pandas as pd
            import tiktoken
            from langchain_text_splitters import RecursiveCharacterTextSplitter

            ROOT = Path.cwd().resolve()
            if ROOT.name == "notebooks":
                ROOT = ROOT.parent

            INPUT_DIR = ROOT / "data" / "03_processed" / "legal_api"
            OUTPUT_DIR = ROOT / "data" / "04_chunks" / "final"
            OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

            CHUNK_CONFIG = {
                "statute": {"max_chars": 1000, "overlap": 80},
                "interpretation": {"max_chars": 1200, "overlap": 120},
                "precedent": {"max_chars": 1500, "overlap": 150},
            }
            MAX_EMBEDDING_TOKENS = 8192

            try:
                TOKENIZER = tiktoken.encoding_for_model("text-embedding-3-small")
            except KeyError:
                TOKENIZER = tiktoken.get_encoding("cl100k_base")

            print("입력:", INPUT_DIR)
            print("출력:", OUTPUT_DIR)
            print("설정:", CHUNK_CONFIG)
            '''
        ),
        _markdown(
            '''
            ## 1. 전처리 결과 로드

            `relevant`, `candidate` 판례만 전처리 파일에 들어 있으며 제외 판례는 별도 파일로 남는다.
            '''
        ),
        _code(
            r'''
            # 전처리 JSONL 로드
            def read_jsonl(path: Path) -> list[dict]:
                records = []
                with path.open(encoding="utf-8") as file:
                    for line in file:
                        if line.strip():
                            records.append(json.loads(line))
                return records


            inputs = {
                "statute": read_jsonl(INPUT_DIR / "processed_eflaw.jsonl"),
                "interpretation": read_jsonl(INPUT_DIR / "processed_expc.jsonl"),
                "precedent": read_jsonl(INPUT_DIR / "processed_prec.jsonl"),
            }
            display(pd.DataFrame([
                {"type": source_type, "records": len(records)}
                for source_type, records in inputs.items()
            ]))
            '''
        ),
        _markdown(
            '''
            ## 2. 공통 분할과 헤더 함수

            각 청크에 출처 헤더를 반복해 metadata를 보지 않아도 문맥을 알 수 있게 한다.
            '''
        ),
        _code(
            r'''
            # 구조 경계를 우선하는 공통 함수
            STATUTE_SEPARATORS = [
                "\n①", "\n②", "\n③", "\n④", "\n⑤", "\n⑥", "\n⑦", "\n⑧", "\n⑨", "\n⑩",
                "\n1.", "\n2.", "\n3.", "\n가.", "\n나.", "\n다.", "\n\n", "\n", ". ", " ", "",
            ]
            PARAGRAPH_SEPARATORS = ["\n\n", "\n", ". ", "。", " ", ""]


            def split_text(text: str, max_chars: int, overlap: int, separators: list[str]):
                if len(text) <= max_chars:
                    return [text]
                splitter = RecursiveCharacterTextSplitter(
                    chunk_size=max_chars,
                    chunk_overlap=overlap,
                    length_function=len,
                    separators=separators,
                    keep_separator=True,
                )
                return splitter.split_text(text)


            def split_table_rows(text: str, max_chars: int, overlap: int):
                """별표 행을 유지하되 제한보다 긴 단일 행만 재귀적으로 분할한다."""
                if len(text) <= max_chars:
                    return [text]
                lines = [line for line in text.splitlines() if line.strip()]
                header = lines[:2]
                rows = lines[2:] if len(lines) > 2 else lines
                chunks, current = [], list(header)
                header_text = "\n".join(header).strip()
                available = max(1, max_chars - len(header_text) - 1)
                for row in rows:
                    # 헤더를 반복하면서 남은 공간이 줄어도 행 자체가 제한 이하면 보존한다.
                    # 제한을 넘는 단일 행만 헤더를 포함할 수 있는 길이로 재분할한다.
                    if len(row) > max_chars:
                        if current != header:
                            chunks.append("\n".join(current).strip())
                        row_overlap = min(overlap, max(0, available // 4))
                        for part in split_text(
                            row, available, row_overlap, PARAGRAPH_SEPARATORS
                        ):
                            chunks.append("\n".join([*header, part]).strip())
                        current = list(header)
                        continue

                    candidate = "\n".join([*current, row])
                    if current != header and len(candidate) > max_chars:
                        chunks.append("\n".join(current).strip())
                        current = [*header, row]
                    else:
                        current.append(row)
                if current != header or not chunks:
                    chunks.append("\n".join(current).strip())
                return chunks


            def build_header(metadata: dict) -> str:
                source_type = metadata["source_type"]
                if source_type == "statute":
                    lines = [f"[법령] {metadata['doc_title']}"]
                    if metadata.get("article"):
                        title = f"({metadata['article_title']})" if metadata.get("article_title") else ""
                        lines.append(f"[조문] {metadata['article']}{title}")
                    else:
                        lines.append(f"[구분] {metadata.get('section_title', metadata['section'])}")
                    return "\n".join(lines)
                if source_type == "interpretation":
                    return "\n".join(filter(None, [
                        f"[법령해석례] {metadata['doc_title']}",
                        f"[안건번호] {metadata.get('case_no', '')}",
                        f"[구분] {metadata.get('section_title', metadata['section'])}",
                    ]))
                return "\n".join(filter(None, [
                    f"[판례] {metadata['doc_title']}",
                    f"[법원] {metadata.get('court', '')}",
                    f"[사건번호] {metadata.get('case_no', '')}",
                    f"[구분] {metadata.get('section_title', metadata['section'])}",
                ]))
            '''
        ),
        _markdown(
            '''
            ## 3. 유형별 청킹

            짧은 조문·질의요지·회답·판시사항·판결요지는 통째로 유지한다.
            이유와 판례내용 등 긴 본문만 항 또는 문단 경계로 나눈다.
            '''
        ),
        _code(
            r'''
            # 유형별 분할 규칙
            def split_record(record: dict) -> list[str]:
                text = record["page_content"].strip()
                metadata = record["metadata"]
                source_type = metadata["source_type"]
                section = metadata["section"]
                config = CHUNK_CONFIG[source_type]

                if source_type == "statute":
                    if section == "annex":
                        return split_table_rows(
                            text, config["max_chars"], config["overlap"]
                        )
                    return split_text(text, config["max_chars"], config["overlap"], STATUTE_SEPARATORS)

                if source_type == "interpretation":
                    if section in {"question", "answer"}:
                        return [text]
                    return split_text(text, config["max_chars"], config["overlap"], PARAGRAPH_SEPARATORS)

                if section in {"holding", "summary"}:
                    return [text]
                return split_text(text, config["max_chars"], config["overlap"], PARAGRAPH_SEPARATORS)


            def build_chunks(records: list[dict]) -> list[dict]:
                chunks = []
                source_indexes = defaultdict(int)
                for record in records:
                    metadata = record["metadata"]
                    header = build_header(metadata)
                    for section_index, body in enumerate(split_record(record)):
                        content = f"{header}\n\n{body}".strip()
                        source_id = metadata["source_id"]
                        chunk_index = source_indexes[source_id]
                        source_indexes[source_id] += 1
                        chunk_metadata = {
                            **metadata,
                            "chunk_id": f"{metadata['record_id']}:{section_index}",
                            "chunk_index": chunk_index,
                            "section_chunk_index": section_index,
                            "char_len": len(content),
                            "token_len": len(TOKENIZER.encode(content)),
                        }
                        chunks.append({"page_content": content, "metadata": chunk_metadata})
                return chunks


            chunk_sets = {
                source_type: build_chunks(records)
                for source_type, records in inputs.items()
            }
            display(pd.DataFrame([
                {"type": source_type, "input_records": len(inputs[source_type]), "chunks": len(chunks)}
                for source_type, chunks in chunk_sets.items()
            ]))
            '''
        ),
        _markdown(
            '''
            ## 4. 청크 품질 검증

            빈 청크, ID 중복, metadata 누락과 길이 분포를 확인한다. 통째로 유지한 요약 섹션은
            설정 길이를 넘을 수 있으므로 별도 경고로 확인한다.
            '''
        ),
        _code(
            r'''
            # 청크 ID와 길이 검증
            REQUIRED_METADATA = {
                "source_type", "source_id", "record_id", "doc_title", "section",
                "chunk_id", "chunk_index", "section_chunk_index", "char_len", "token_len",
            }

            quality_rows = []
            for source_type, chunks in chunk_sets.items():
                chunk_ids = [item["metadata"]["chunk_id"] for item in chunks]
                empty = sum(not item["page_content"].strip() for item in chunks)
                missing = sum(
                    bool(REQUIRED_METADATA - set(item["metadata"])) for item in chunks
                )
                duplicate = len(chunk_ids) - len(set(chunk_ids))
                lengths = [item["metadata"]["char_len"] for item in chunks]
                token_lengths = [item["metadata"]["token_len"] for item in chunks]
                quality_rows.append({
                    "type": source_type,
                    "chunks": len(chunks),
                    "empty": empty,
                    "missing_metadata": missing,
                    "duplicate_chunk_id": duplicate,
                    "char_p50": int(pd.Series(lengths).quantile(0.5)),
                    "char_p95": int(pd.Series(lengths).quantile(0.95)),
                    "char_max": max(lengths),
                    "token_p95": int(pd.Series(token_lengths).quantile(0.95)),
                    "token_max": max(token_lengths),
                })
                assert empty == 0
                assert missing == 0
                assert duplicate == 0
                assert max(token_lengths) <= MAX_EMBEDDING_TOKENS, (
                    f"{source_type} 청크가 임베딩 토큰 한도를 초과했습니다: {max(token_lengths)}"
                )

            quality = pd.DataFrame(quality_rows)
            display(quality)

            for source_type, chunks in chunk_sets.items():
                longest = max(chunks, key=lambda item: item["metadata"]["token_len"])
                print(
                    f"[{source_type}] 최대 토큰={longest['metadata']['token_len']}, "
                    f"제목={longest['metadata']['doc_title']}, 섹션={longest['metadata']['section']}"
                )
                print(longest["page_content"][:500], "\n")
            '''
        ),
        _markdown(
            '''
            ## 5. 최종 JSONL 저장

            기존 임베딩 파이프가 읽는 `page_content + metadata` 형식으로 유형별 저장한다.
            '''
        ),
        _code(
            r'''
            # 유형별 청크 JSONL 저장
            OUTPUT_NAMES = {
                "statute": "kb_chunks_eflaw.jsonl",
                "interpretation": "kb_chunks_expc.jsonl",
                "precedent": "kb_chunks_prec.jsonl",
            }


            def write_jsonl(path: Path, records: list[dict]) -> None:
                with path.open("w", encoding="utf-8", newline="\n") as file:
                    for record in records:
                        file.write(json.dumps(record, ensure_ascii=False) + "\n")


            for source_type, chunks in chunk_sets.items():
                path = OUTPUT_DIR / OUTPUT_NAMES[source_type]
                write_jsonl(path, chunks)
                print(f"{source_type}: {len(chunks)}개 → {path}")
            '''
        ),
    ]
    _write_notebook(NOTEBOOK_DIR / "03_build_kb_chunks.ipynb", cells)


def main() -> None:
    patch_load_api_notebook()
    build_preprocess_notebook()
    build_chunk_notebook()
    print("법률 API 노트북 생성 완료")


if __name__ == "__main__":
    main()
