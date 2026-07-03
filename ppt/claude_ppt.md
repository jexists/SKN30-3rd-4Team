# 홈쉴드(HomeShield) 발표 슬라이드 덱 — 문서

SKN30 3차 프로젝트 · 4조 (김진남 · 정민규 · 정주애 · 천성배)
주제: **전·월세 분쟁 팩트체커** — 계약 전 예방 + 계약 후 분쟁 대응 투트랙 세입자 법률 도우미

순수 HTML/CSS/JS 슬라이드 덱. 외부 CDN 의존 없음 — `file://` 로 열어도 동작한다.
`ppt/index.html` 을 브라우저에서 열면 표지로 자동 이동한다.

슬라이드 전환은 매번 새 문서를 로드하는 방식(각 파일이 독립 실행 가능해야 해서 SPA 구조를 쓰지 않음)이라, `shared/nav.js`의 F키/전체화면 버튼(Fullscreen API)은 슬라이드를 넘기는 순간 브라우저가 보안상 자동 해제한다 — iframe으로 감싸도 동일하다(디스크립트에서 확인). 슬라이드를 넘겨도 안 풀리는 진짜 전체화면이 필요하면 발표 중엔 브라우저 자체 전체화면(F11, 창 단위 기능이라 페이지 이동과 무관하게 유지됨)을 쓴다.

---

## 1. 덱 구성 (18장)

| # | 파일 | 제목 | 핵심 내용 | 콘텐츠 출처 |
|---|------|------|-----------|-------------|
| 1 | `slides/01_cover.html` | 표지 | 로고 · 팀명 · 주제 · 팀원 4인 | docs/prd.pdf |
| 2 | `slides/02_toc.html` | 목차 | 8개 대목차 카드 (클릭 점프) | — |
| 3 | `slides/03_team.html` | 팀 소개 | 팀 정의 + 4인 역할 카드 | docs/prd.pdf |
| 4 | `slides/04_problem.html` | 문제 정의 · 선정 배경 | 히어로 인용 + 4대 문제 카드 | docs/prd.pdf |
| 5 | `slides/05_goal.html` | 목표 & 차별점 | **계약 전 예방 + 계약 후 대응 투트랙**(차별점) + 3단계 파이프라인 + 하이브리드 설계 원칙 | docs/prd.pdf, app/ (계약 전/후 2페이지 구성) |
| 6 | `slides/06_stack.html` | 기술 스택 | 카테고리별 스택 행 (Python 3.13 ~ PyMuPDF) | pyproject.toml, docs/prd.pdf |
| 7 | `slides/07_data.html` | 사용한 데이터 | 법령 API · 공공 PDF 16종 · 웹 자료 + 메타 4축 | data/01_raw, docs/prd.pdf |
| 8 | `slides/08_pipeline.html` | 데이터 파이프라인 | 01_raw→Supabase 6단계 플로우 + 포인트 3개 | src/pipe/, data/ 폴더 구조 |
| 9 | `slides/09_erd_v1.html` | ERD ① 물리+논리 | kb_chunks 물리 테이블 + JSONB 논리 엔티티 분기 | src/core/vs_method.py |
| 10 | `slides/10_langgraph.html` | LangGraph 전체 구조 | `assets/langgraph_main.png` + 핵심 포인트 4개 | src/core/graph.py (draw_mermaid 결과) |
| 11 | `slides/11_langgraph_detail.html` | pre 서브그래프 & 품질 루프 | `assets/langgraph_pre.png` + 결정론 노드 강조 + 품질 카드 4개 | src/core/graph.py |
| 12 | `slides/12_features.html` | 핵심 기능 | OCR 판독 5단계 히어로 + 보조 기능 4카드 | docs/prd.pdf, app/ |
| 13 | `slides/13_eval.html` | 성능 평가 | 0.868 / 0.68 / 1~2초 큰 숫자 + TC-01~04 + 21.8초 병목 | 팀 테스트 보고서 (RAGAS), eval/ |
| 14 | `slides/14_demo.html` | 프로젝트 시연 | **예외 레이아웃** — localhost:8501 풀스크린 iframe | app/main.py |
| 15 | `slides/15_issues.html` | 주요 이슈 및 해결 | 문제→해결 4행 (필터 버그 · PDF 손상 · 429 · grade 게이트) | 팀 트러블슈팅 기록 |
| 16 | `slides/16_future.html` | 확장 가능성 & 향후 과제 | 확장 4개 / 한계 5개 2열 | docs/prd.pdf |
| 17 | `slides/17_future2.html` | 확장 가능성 & 향후 과제 2 | 16번과 동일 구성(아쉬움/개선안 대비 카드) | docs/prd.pdf |
| 18 | `slides/18_closing.html` | 마무리 | 감사 인사 + 태그라인 + Q&A + 팀원 | — |

ERD는 물리+논리 스키마 한 버전만 담았다(9번) — 컬럼 상세를 별도로 보여주던 "단일 테이블" 버전은 중복이라 제거했다.

---

## 2. 디자인 토큰 & 규칙

reveal.js 계열 발표 사이트 스타일: **콘텐츠 정중앙 배치 · 큰 타이포 · 미니멀**.

### 색상 (Streamlit 앱 "홈쉴드"와 통일)

| 토큰 | Hex | 용도 |
|------|-----|------|
| `--c-primary` | `#2563eb` | 브랜드 블루 · 강조 · 프로그레스 바 |
| `--c-deep` | `#2f5fe0` | 진한 블루 텍스트 |
| `--c-light` | `#3b82f6` | 화살표 · 라이트 블루 |
| `--c-navy` | `#1a1a1a` | 기본 텍스트 · 다크 카드 |
| `--c-gray` | `#6b7280` | 보조 텍스트 |
| `--c-gray-bg` | `#f3f4f6` | 옅은 회색 카드 배경 |
| `--c-blue-bg` | `#eaf0ff` | 옅은 블루 카드 배경 |
| `--c-yellow` | `#f5df4e` | 포인트 옐로 (다크 배경 위 강조) |

### 타이포그래피

- 폰트 스택: `"Pretendard", "Noto Sans KR", "Malgun Gothic", sans-serif` (시스템 폰트, CDN 없음)
- 스케일: hero 60px / 제목 42px / 섹션 26px / 본문 22px / 캡션 20px
- **본문 텍스트 최소 20px** — 안 들어가면 글자를 줄이지 말고 요소를 줄인다

### 레이아웃 규칙

- 16:9 고정 캔버스 1280×720, 뷰포트에 맞춰 `transform: scale()` 중앙 축소/확대 (nav.js가 리사이즈 대응)
- **콘텐츠는 화면 정중앙**: `.content`(스크롤 컨테이너) 안 `.content-inner`에 `margin: auto` — 짧으면 중앙, 길면 상단부터 세로 스크롤
- 내용이 길면 슬라이드 안에서 **세로 스크롤** (`overflow-y: auto`, 6px 얇은 스크롤바). 휠·트랙패드·↑↓키로 스크롤
- 상단 바는 얇게: 로고 + "홈쉴드" + 페이지 번호만 (`.topbar`, nav.js가 번호 자동 기입)
- 최하단 **5px 브랜드 블루 프로그레스 바** — 현재 슬라이드/전체 비율 (nav.js 자동 렌더)
- 카드: 옅은 배경(`#f3f4f6`/`#eaf0ff`) + 16px 둥근 모서리, 그림자·테두리 최소화. **카드 크기는 내용에 맞추고 빈 공간을 남기지 않는다**
- 표지(01)·마무리(18)는 네이비→블루 그라데이션(`.bg-gradient`) + 흰 텍스트
- 다이어그램: LangGraph 는 PNG 이미지(`assets/langgraph_*.png`), 나머지 플로우는 순수 HTML/CSS 박스 + 텍스트 화살표
- 아이콘: 이모지만 사용 (외부 아이콘 폰트 금지)

---

## 3. 조작법

| 입력 | 동작 |
|------|------|
| `→` `Space` `PageDown` | 다음 슬라이드 |
| `←` `PageUp` | 이전 슬라이드 |
| `Home` / `End` | 처음 / 끝 |
| `↑` `↓` · 마우스 휠 | 슬라이드 **내부 스크롤** (내비에 안 씀) |
| `T` 또는 `Esc` | 목차 오버레이 토글 |
| 우하단 `‹` `›` 버튼 | 이전 / 다음 |
| 우하단 페이지 번호 클릭 | 목차 오버레이 |
| 목차 카드 클릭 | 해당 슬라이드로 점프 (현재 슬라이드 하이라이트) |

각 슬라이드는 독립 HTML — 아무 파일이나 브라우저로 직접 열어도 완전히 렌더된다.

---

## 4. 슬라이드 추가 / 삭제 / 순서 변경

**`shared/nav.js` 상단의 `SLIDES` 배열이 순서·제목의 단일 소스다.** 이 배열만 고치면 목차 오버레이 · 이전/다음 버튼 · 페이지 번호 · 프로그레스 바가 전부 자동 반영된다.

```js
var SLIDES = [
  { file: "01_cover.html", title: "표지" },
  // ... 순서 변경 = 배열 순서 변경, 삭제 = 항목 제거
  { file: "18_appendix.html", title: "부록" },  // 추가 예시
];
```

새 슬라이드 보일러플레이트 (`slides/18_appendix.html` 예시):

```html
<!DOCTYPE html>
<html lang="ko">
<head>
<meta charset="UTF-8" />
<title>홈쉴드 — 부록</title>
<link rel="stylesheet" href="../shared/style.css" />
<style>/* 슬라이드 전용 스타일 */</style>
</head>
<body>
  <div class="slide-viewport">
    <div class="slide-canvas">
      <div class="topbar">
        <div class="topbar-brand">
          <img src="../assets/logo.png" class="topbar-logo" alt="" />
          <span class="topbar-name">Home Shield</span>
        </div>
        <span class="topbar-page"></span><!-- nav.js가 자동 기입 -->
      </div>
      <div class="content">
        <div class="content-inner" style="gap: 28px;">
          <h1 class="title">부록</h1>
          <!-- 내용 -->
        </div>
      </div>
    </div>
  </div>
  <script src="../shared/nav.js"></script>
</body>
</html>
```

---

## 5. 시연 슬라이드 (14_demo) 사용법

1. 발표 전 터미널에서 앱을 먼저 실행:
   ```bash
   uv run streamlit run app/main.py
   ```
2. 슬라이드 14로 진입하면 `http://localhost:8501` 이 **100vw×100vh 풀스크린 iframe**으로 뜬다 — 슬라이드 안에서 실제 앱을 그대로 조작하며 시연.
3. 서버 미기동 시 iframe 뒤 배경의 실행 안내(`uv run streamlit run app/main.py`)가 보인다. 서버를 켠 뒤 F5 새로고침.
4. 이 슬라이드에서 키보드 포커스가 iframe 안에 있으면 ←/→ 가 안 먹는다 — **우하단 플로팅 ‹ › 버튼**으로 이동하라.

---

## 6. 성능 수치 출처

슬라이드 13의 수치는 팀 테스트 보고서 기반이다 (RAGAS 프레임워크 + LangSmith 추적):

- **Faithfulness 0.868**, **Factual Correctness 0.68 (0.50~0.89 분포)** — RAGAS 평가 결과
- **응답 시간 1~2초** (일반 질의), **최대 21.8초** (재작성 루프 중첩 케이스) — LangSmith 레이턴시 측정
- TC-01~04 는 팀 테스트 보고서(테스트 계획 및 결과 보고서, 2026-07-02)의 4대 핵심 테스트 영역: TC-01 의도 분류(잡담→chitchat 분기) / TC-02 서류 분석·위험 진단(PDF→보증금·선순위 파싱, 전세가율 등급) / TC-03 하이브리드 벡터 검색(법령·사례 청크 검색) / TC-04 답변 생성·환각 제어(verify가 faithful=false 감지)
- 그 외 평가 수단: RAGAS 4지표(Faithfulness·ResponseRelevancy·ContextPrecision·ContextRecall), LLM 심판·키워드 채점(eval/run_graph_test2_accuracy.py), LangSmith 노드별 지연·토큰 모니터링
- 임계값 수치(전세가율 0.8/0.7, grade 게이트 0.45/0.35, 청킹 500/50·1200/80, 임베딩 1024차원)는 src/ 코드 기준 — **임의로 수정 금지**

---

## 7. Claude 에게 수정 요청할 때

이 덱을 수정할 때는 다음처럼 요청하면 정확하다: 대상 슬라이드를 파일명으로 지정하고(예: "13_eval.html의 게이지 색을 바꿔줘"), 공통 변경은 `shared/style.css`(디자인 토큰)나 `shared/nav.js`(순서·제목)를 지목하라. 디자인 원칙 — 콘텐츠 정중앙, 본문 20px 이상, 요소 수 최소화, 카드 빈 공간 금지, 외부 CDN 금지, 수치·노드명·팀원 이름 임의 변경 금지 — 를 유지하라고 명시하면 스타일이 흐트러지지 않는다. 새 통계·수치는 반드시 출처와 함께 제공할 것.
