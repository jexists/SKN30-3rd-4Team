# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## 프로젝트 개요

전·월세 세입자(사회초년생)를 위한 챗봇. **계약 전** 등기부·특약 분석으로 전세사기 위험을 진단하고, **계약 후** 수리 분쟁·보증금 갈등을 돕는다.

## 환경 설정

```bash
uv sync                          # 의존성 설치 (.venv 자동 생성)
cp .env.example .env             # 환경 변수 파일 생성 후 키 입력
```

`.env` 필수 키: `OPENAI_API_KEY`, `LANGSMITH_API_KEY`

## 주요 명령어

```bash
uv run streamlit run app/main.py  # 앱 실행
uv run pytest                     # 전체 테스트
uv run pytest tests/path/test_x.py::test_func  # 단일 테스트
uv run ruff check .               # 린트
uv run ruff format .              # 포맷
```

## 아키텍처

### 데이터 파이프라인 (오프라인, 일회성)

```text
data/01_raw/        HWP 원본 표준계약서
      ↓
data/02_loaded/     문서 로드 결과 (텍스트 추출 체크포인트)
      ↓
data/03_processed/  전처리 결과 (정제·정규화)
      ↓
data/04_chunks/     청킹 결과 체크포인트
      ↓
data/05_vectordb/   Chroma 벡터스토어 (커밋 — 재임베딩 비용 절약)
```

`src/pipe/` 안의 오프라인 빌드 파이프라인이 위 변환을 수행한다.

### 런타임 흐름

```text
사용자 입력 (Streamlit app/)
      ↓
src/pipe/  런타임 review 파이프라인 (LangGraph 그래프)
      ↓
src/core/  이탈 탐지 순수 함수 (조항 vs 표준 비교)
      ↓
src/adapter/  외부 I/O — ChromaDB 검색, SQLite 조회, 법령 MCP
      ↓
응답 반환
```

### src/ 레이어 규칙

| 레이어 | 역할 | 의존 방향 |
|--------|------|-----------|
| `contracts/` | enums, Pydantic 모델, Port 인터페이스 | 아무것도 import 안 함 |
| `core/` | 순수 함수 (TDD 대상) | `contracts/`만 |
| `adapter/` | DB·벡터·임베더·MCP 구현체 | `contracts/`만 |
| `pipe/` | 파이프라인 조합 | `core/` + `adapter/` |

`core/`는 외부 I/O 없음 — 순수 함수만. `app/`은 비즈니스 로직을 `src/`에 위임하고 UI만 담당.

### 평가

`eval/`에 metrics, run_eval, ablation 하네스. `data/03_normalized/`의 정규화 JSON이 정답지.

## 협업 규칙

- **브랜치**: 기능 브랜치 → `develop` PR → CodeRabbit 자동 리뷰 → 머지 → `main` 자동 미러
- **커밋 형식**: `<type>: <한글 설명>` — type은 `feat fix refactor chore docs test style perf` 중 하나
- **PR 본문**: 📌 배경 / ✅ 수정 내역 / 📸 스크린샷(UI 변경 시)
- `data/`는 CodeRabbit 리뷰 제외 (`.coderabbit.yaml`)
