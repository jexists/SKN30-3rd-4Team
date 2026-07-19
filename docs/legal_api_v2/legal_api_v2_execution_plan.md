# 법령 API v2 분석 및 실행 계획

> 이 문서는 지금까지 확정한 요구사항과 구현 결정을 보존하고, 이후 Codex Agent 또는 개발자가 이 문서만 보고 단계별 작업을 재개할 수 있도록 작성한 실행 명세다.
>
> 현재 상태: Repository 분석과 정책 협의만 완료했다. v2 Notebook 및 데이터 파이프라인은 아직 구현하지 않았다.
>
> 2026-07-18 개정: 검토 결과를 반영해 OC 노출 처리(§3.4), 재실행 정책(§3.3), 데이터 커밋 정책(§6.1), 관련성 평가 산식(§4.3), 확정 검색어 목록(부록 A·B)을 추가하고, 저장 형식을 개별 `.json`에서 법령별·카테고리별 JSONL로 변경했다(§6).
>
> 2026-07-18 2차 개정: 유사 프로젝트(SKNETWORKS-FAMILY-AICAMP/SKN24-3rd-6team) 수집 코드 비교 결과를 반영해 판례 상세 오류 응답 규모 인지와 `resultCode` 검증(§3.3), Gate 2 HTML 진단 항목, Gate 3 규모 추정 보정, HTML fallback 범위 제외(§11)를 추가했다.

## 1. 목표와 절대 제약

법제처 국가법령정보 공동활용 API에서 법령·판례·법령해석례를 다시 수집하고 다음 5단계 산출물을 새 v2 경로에 만든다.

1. OC만 제거한 Sanitized Raw JSON
2. 사람이 원문을 검수하는 Raw Markdown
3. 전처리 전 `page_content + metadata` Document JSON
4. 사람이 전처리 결과를 검수하는 Processed Markdown
5. 전처리된 Text/Image Document JSON

반드시 지킬 제약은 다음과 같다.

- 기존 Notebook, Python 파일, 데이터 파일의 내용은 수정하지 않는다.
- 모든 구현과 산출물은 새 `legal_api_v2` 경로에만 추가한다.
- Raw 파일은 한 번 저장한 뒤 덮어쓰지 않는다.
- 토큰 기반 Chunking, 임베딩, 벡터 DB 적재는 하지 않는다.
- `chunk_id`, `chunk_index`, `overlap` 등 Chunking metadata를 생성하지 않는다.
- 구현은 수집 샘플 → 전체 수집 → 전처리 샘플 → 전체 전처리 순서로 진행한다.
- 각 샘플 결과를 사용자에게 공유하고 승인받은 후 다음 단계로 진행한다.
- 작업 중간마다 진행 상황, 생성 파일, 레코드 수, 오류와 검증 결과를 공유한다.

## 2. 기존 Repository 분석 결과

분석 기준은 `origin/wip/backup-fix`의 다음 코드다.

- `notebooks/01_load_api.ipynb`
- `notebooks/02_preprocess_legal_api.ipynb`
- `notebooks/03_build_kb_chunks.ipynb`
- `src/core/vs_method.py`
- `src/core/graph.py`

### 2.1 기존 수집 범위와 입력

| target | 자료 | 검색 방식 | 중복 제거 기준 |
|---|---|---|---|
| `eflaw` | 현행 법령 16종 | 법령명 정확 검색, `nw=3` | `법령ID` |
| `prec` | 판례 | 쟁점 키워드 본문 검색 | `판례일련번호` |
| `expc` | 법령해석례 | 법령·쟁점 키워드 본문 검색 | `법령해석례일련번호` |

기존 입력은 법령별 JSON과 판례·해석례 카테고리별 JSONL이다. 판례의 `prec_checkpoint.jsonl`은 카테고리 파일과 중복되어 전처리에서 제외한다.

### 2.2 기존 Raw 데이터 문제

기존 `fetch_details()`는 검색 결과와 상세 응답을 합친 뒤 저장한다.

- `_category`, `_query`, `_categories`, `_queries`를 주입한다.
- OC가 포함될 수 있는 `상세링크` 필드를 통째로 제거한다.
- 상세 응답을 `본문` 필드 아래에 병합한다.
- 빈 본문이나 오류 응답은 제거한다.

따라서 기존 Raw JSON은 API 응답 원형이 아니며, 수집과 metadata 가공이 섞여 있다.

### 2.3 기존 전처리와 정보 손실 가능성

기존 전처리는 HTML entity·공백·줄바꿈·표 문자를 정리하고 조문/부칙/별표, 판례 섹션, 해석례 섹션 단위의 Document를 만든다. 다음 정보 손실 가능성이 확인됐다.

- `조문여부 == "전문"`인 편·장·절·관 제목을 버려 조문 계층이 사라진다.
- 동일 문자열을 중복 제거하여 유효한 반복 문구가 사라질 수 있다.
- `조문참고자료`의 `[전문개정 ...]`, `[본조신설 ...]` 등이 충분히 보존되지 않는다.
- 임의의 `<...>`를 HTML로 판단하면 `<General>` 같은 실제 텍스트도 삭제될 수 있다.
- 삭제 조문, 특정 영문 별첨, 저관련 판례가 Processed 결과에서 제외된다.
- 기존 Raw Markdown도 이미 강한 정리를 거쳐 엄밀한 원문 검수본이 아니다.

기존 전처리 결과는 다음과 같다.

| 출력 | 레코드 수 |
|---|---:|
| `processed_eflaw.jsonl` | 2,808 |
| `processed_expc.jsonl` | 962 |
| `processed_prec.jsonl` | 3,532 |
| `excluded_prec.jsonl` | 1,734 |

### 2.4 기존 metadata 사용

기존 공통 metadata는 다음과 같다.

- `source_type`, `source_id`, `parent_id`, `record_id`
- `doc_title`, `source_org`, `doc_year`
- `authority`, `stage`, `issue`
- `source_file`, `section`

이 필드들은 후속 Chunking 방식, 검색 필터, 답변 citation에 실제로 사용된다. v2에서도 유지하면서 계층, 이미지, 수집 provenance를 확장한다.

## 3. 확정된 수집 정책

### 3.1 수집 범위

- `eflaw`, `prec`, `expc`를 모두 수집한다.
- 기존 현행 법령 16종과 판례·해석례 검색어 구성을 유지한다. 기준은 `origin/wip/backup-fix`의 `notebooks/01_load_api.ipynb`이며, 확정 목록은 부록 A에 고정한다.
- 주의: develop의 `notebooks/01_load_api.ipynb`에는 광의 단일어 위주 판례 검색어 **36개**(예: "대항력", "하자", "원상회복")가 있으나, 이는 노이즈가 커서 임대차 문맥을 포함한 **34개**(예: "임차인 대항력", "임대차 하자")로 대체되기 전 버전이다. v2는 34개를 사용한다.
- 법령은 정확 명칭과 현행 여부를 검증한 뒤 상세 조회한다.
- 검색 응답과 상세 응답을 서로 분리해 저장한다.
- 검색 provenance는 Raw JSON에 주입하지 않고 Document metadata에 기록한다.

### 3.2 판례 최신 300건 정책

판례 API 요청에는 다음 값을 명시한다.

```python
search = 2
sort = "ddes"
PRECEDENT_MAX_PER_QUERY = 300
```

정확한 의미는 다음과 같다.

> 각 검색어가 본문에 일치하는 판례 중 선고일자가 최신인 최대 300건을 수집하고, 수집된 후보 안에서 자체 관련성 점수를 계산한다.

중요한 제한은 다음과 같다.

- `sort="ddes"`는 선고일자 내림차순이며 관련도 순이 아니다.
- API가 제공하는 판례 목록 정렬에는 관련도 정렬 옵션이 없다.
- 검색어가 관련 후보군을 만들지만, 300건 내부 순서는 최신순이다.
- 300건보다 오래된 판례 중 더 관련성 높은 판례가 누락될 수 있다.
- 이 제한을 숨기지 않고 manifest와 metadata에 명시한다.
- `PRECEDENT_MAX_PER_QUERY = None`으로 바꾸면 전체 페이지를 수집할 수 있다.

각 판례 검색 manifest에는 다음 정보를 남긴다.

```json
{
  "selection_policy": "latest_per_query",
  "search_scope": "body",
  "sort": "ddes",
  "max_per_query": 300,
  "total_count": 4286,
  "collected_count": 300,
  "truncated_count": 3986,
  "relevance_evaluated_after_collection": true
}
```

각 판례 Document metadata에도 최소한 다음 값을 전달한다.

```json
{
  "selection_policy": "latest_per_query",
  "sort": "ddes",
  "max_per_query": 300,
  "collection_queries": ["임대차보증금"],
  "relevance_level": "relevant",
  "relevance_score": 8
}
```

2026-07-17 확인 기준 기존 판례 검색어 34개(부록 A)의 규모는 다음과 같다. 검색어 간 중복이 포함된 값이므로 상세 판례 수와 같지는 않다.

- 전체 검색 결과 합계: 17,466건
- 검색어별 최신 300건 적용 합계: 7,465건
- 300건을 초과한 검색어: 17개
- 기존 최신 300건 데이터의 ID dedup 후 상세 판례: 2,128건

실제 실행 시에는 고정된 위 수치가 아니라 API가 반환한 최신 `totalCnt`를 manifest에 기록한다.

### 3.3 OC만 제거한 Sanitized Raw JSON

Git에 저장하는 Raw JSON은 API 응답에서 OC만 제거한 `Sanitized Raw JSON`이다.

처리 순서는 다음과 같다.

1. HTTP 응답을 메모리에서 수신한다.
2. 변형 전 응답 bytes의 SHA-256을 manifest에 기록한다.
3. JSON을 파싱한다.
4. 모든 문자열을 재귀 탐색한다.
5. URL query parameter 이름이 대소문자 구분 없이 `OC`이면 그 parameter만 제거한다.
6. 독립된 `OC` 필드가 있으면 구조는 유지하고 값을 `__REDACTED__`로 치환한다.
7. 다른 필드, 본문, 배열 순서와 값은 변경하지 않는다.
8. envelope(`source_id`, `query`, `page`, `fetched_at`, `sha256_original`, `response`)로 감싸 대상 JSONL 파일에 append 저장한다(§6 저장 형식).
9. run 종료 시 각 JSONL 파일의 SHA-256과 레코드 수를 manifest에 기록한다. 이미 기록된 레코드는 다시 쓰지 않는다.

Raw는 byte 단위 완전 원본은 아니지만 OC는 법률 내용이 아닌 API 접근 정보이므로 법령 데이터 Source of Truth로 사용한다.

재실행(resume) 정책은 다음과 같다.

- 같은 `run_id` 안에서 재실행할 때, JSONL에 이미 기록된 레코드(상세는 `source_id`, 검색은 `query`+`page` 기준)는 해당 API 호출 자체를 건너뛴다(append-only resume — v1 checkpoint 방식과 동일).
- 다시 받아서 비교하는 방식은 쓰지 않는다. 검색 응답은 시간이 지나면 `totalCnt`가 바뀌므로 재호출 비교는 항상 실패할 수 있다.
- 새로 수집하려면 새 `run_id`를 만든다. 기존 run 폴더는 절대 덮어쓰지 않는다.

오류·빈 본문 상세 응답 처리는 다음과 같다. (v1은 이런 응답을 저장 전에 버렸다)

- 상세 응답이 오류(예: "일치하는 …없습니다")거나 빈 본문이어도 Sanitized Raw JSON은 그대로 저장한다. Raw는 응답 원형 보존이 원칙이다.
- 대신 manifest에 해당 `source_id`를 `invalid_details` 목록으로 기록한다.
- `02_raw_md`와 `03_document_json` 생성 시에는 제외하고, 제외 건수를 검증 결과에 보고한다.
- 따라서 §9.1의 "Raw Markdown 문서 수 = 상세 원문 문서 수"는 유효 상세 응답 수 기준이다.

오류 응답의 규모를 미리 인지한다. 판례 상세 `type=JSON` 조회는 상당 비율이 "일치하는 판례가 없습니다" 오류를 반환한다(v1 develop 실행 기준 6,372건 중 2,869건 ≈ 45%, 유사 프로젝트 SKN24-3rd-6team도 약 30% 확인). 이는 코드 버그가 아니라 API가 일부 판례의 JSON 본문을 제공하지 않는 특성이므로, 대량 제거가 발생해도 중단하지 않고 manifest에 비율을 기록한다. 원인 진단(HTML 형식 재시도 시 본문 제공 여부)은 Gate 2에서 수행한다.

응답 유효성 검증: HTTP 200이어도 검색 응답의 `resultCode != "00"`이면 API 오류다(예: parameter 오류). 검색 응답은 `resultCode`·`resultMsg`를 확인하고, 실패 시 재시도 대상으로 처리하며 manifest에 기록한다. v1 코드는 docstring과 달리 이 검사를 실제로 하지 않았다.

필수 보안 검증은 다음과 같다.

- 모든 v2 산출물에서 `OC=` 잔존 여부 검사 — URL 파싱이 아니라 **문자열 substring 검사**로 수행해 본문 HTML 안의 `&amp;OC=` 같은 escape 형태까지 잡는다.
- Notebook과 로그에 OC 출력 금지
- manifest 요청 parameter에 OC 기록 금지
- `상세링크` 필드는 유지하고 OC parameter만 제거

### 3.4 이미 커밋된 OC 처리 (Gate 1 선행 조치)

v2 산출물에서 OC를 제거해도, 저장소에 이미 OC가 노출되어 있어 sanitization 목적이 완성되지 않는다. 2026-07-17 확인 기준 노출 위치는 다음과 같다.

- `.env.example` — `LAW_API_OC`에 실제 OC 값이 커밋되어 있다.
- `notebooks/01_load_api.ipynb` — 설정 셀의 저장된 출력에 `OC: <실제값>`이 남아 있다.

확정된 조치는 다음과 같다. (2026-07-18 사용자 결정)

1. Gate 1에서 `.env.example`의 OC 값을 `LAW_API_OC=your-oc-here` placeholder로 교체한다. 이는 "기존 파일 수정 금지" 제약의 **명시적 예외**로 승인됐다.
2. OC 재발급은 하지 않는다. OC는 비밀 API 키가 아닌 공동활용 신청 계정 ID이므로 위험이 제한적이라고 판단했다. git 히스토리에 기존 값이 남는다는 사실을 인지하고 수용한다.
3. `notebooks/01_load_api.ipynb`의 출력 셀은 기존 파일 수정 금지 제약에 따라 그대로 둔다. 대신 v2 Notebook에서는 OC를 어떤 형태로도 출력하지 않는다.

## 4. Document 및 전처리 정책

### 4.1 구조 레코드

토큰 Chunking이 아니라 원래 법률 구조를 기준으로 Document를 만든다.

- 법령: 편·장·절·관 제목, 조문, 부칙, 별표
- 판례: 판시사항, 판결요지, 판례본문
- 해석례: 질의요지, 회답, 이유

`조문여부 == "전문"` 레코드는 버리지 않는다. 순차 상태 머신으로 편·장·절·관을 추적하고 제목 자체를 `section="heading"` Document로 보존한다. 뒤따르는 조문에는 `structure_path`를 연결한다.

다음 자료도 삭제하지 않고 metadata로 상태를 표시한다.

- 삭제 조문: `is_deleted=true`
- 공식 영문 별첨: `language`, `duplicate_candidate`
- 저관련 판례: `relevance_level`
- 개정 정보: `revision_notes`

동일 문구를 자동 dedup하지 않는다.

### 4.2 의미 보존형 전처리

- HTML entity 해제
- `\r\n`, `\r`을 `\n`으로 통일
- NBSP를 일반 공백으로 변환
- Unicode NFC만 사용하고 NFKC는 사용하지 않음
- 조문·항·호·목을 논리 단위별 한 줄로 유지
- 줄 내부 탭과 반복 공백 축약
- 과도한 빈 줄 정리
- 법률 번호, 원문 기호, 동그라미 숫자, 한자, `ㆍ` 보존
- 실제 HTML allowlist 태그만 처리
- `<br>`와 블록 태그는 줄바꿈으로 변환
- `<신설 ...>`, `<개정 ...>`, `<삭제 ...>`, `<General>` 같은 텍스트 보존
- 별표의 셀 텍스트와 순서를 유지하고 표 구분선만 `|`로 정리

### 4.3 판례 관련성 평가 방식 (2026-07-18 확정)

`origin/wip/backup-fix`의 `notebooks/02_preprocess_legal_api.ipynb`에 있는 v1 로직을 그대로 재사용한다. 규칙 기반 키워드 매칭이며 LLM을 사용하지 않는다.

산식은 다음과 같다.

1. 판례를 핵심 섹션(사건명·판시사항·판결요지·참조조문)과 판례본문으로 나눈다.
2. 임대차 anchor 11개(부록 B)와 카테고리별 topic 용어(부록 B)의 매칭을 확인한다. "전세"는 `전세(?!계)` 정규식으로 오탐을 줄인다.
3. 점수 = anchor 매칭 개수 + 섹션 가중치 합(topic 용어가 있는 섹션마다: 사건명 3, 판시사항 5, 판결요지 4, 참조조문 4) + 판례본문 topic 매칭 시 1 + 대법원 판례면 1
4. 등급 판정:
   - `relevant`: 핵심 섹션에 anchor 매칭과 topic 매칭이 모두 있고 score ≥ 5
   - `candidate`: anchor 섹션 매칭이 있거나, anchor와 topic이 동시에 매칭됨
   - `excluded`: 그 외

v1과의 차이는 하나다. v1은 `excluded` 판례를 별도 파일로 분리해 Processed에서 제외했지만, v2는 §4.1 보존 원칙에 따라 **어떤 등급도 삭제하지 않는다**. 모든 판례 Document에 `relevance_level`, `relevance_score`, `matched_anchors`, `matched_terms`, `exclusion_reason`(해당 시)을 기록하고, 후속 Chunking·검색 단계가 등급으로 필터링하게 한다.

관련성 필드의 생성 시점을 명확히 한다. 평가는 전처리 Notebook(02)에서 수행하므로 `relevance_*` 필드는 `05_processed_document_json`에만 존재한다. 수집 Notebook(01)이 만드는 `03_document_json`에는 `collection_queries`, `collection_categories` 같은 수집 provenance만 있고 관련성 필드는 없다.

Gate 4에서 등급 분포를 v1 결과(processed 3,532건 / excluded 1,734건)와 비교해 로직 이식이 정확한지 검증한다.

## 5. 이미지 분리 정책

이미지는 전처리 단계에서 Text Document와 Image Document로 분리한다. 이번 작업에서는 이미지 다운로드, OCR, 이미지 임베딩을 하지 않는다.

### 5.1 단계별 처리

- Sanitized Raw JSON: 이미지 태그·ID·URL·alt 보존, URL의 OC만 제거
- Raw Markdown: 이미지가 있던 위치에 이미지 ID와 클릭 가능한 URL 표시
- Raw Document JSON: `metadata.images`에 이미지 참조 보존
- Processed Text Document: `page_content`에는 전처리된 텍스트만 저장
- Processed Image Document: 이미지 설명과 연결 정보를 별도 레코드로 저장
- Processed Markdown: `text/`와 `image_review/`로 분리

텍스트와 이미지가 섞인 조문·별표는 Text Document 1건과 이미지마다 Image Document 1건으로 분리한다.

- Image Document의 `parent_record_id`는 원래 Text Document를 가리킨다.
- `image_order`, `source_section`으로 원래 위치와 순서를 보존한다.
- API에 제목·설명·alt가 있으면 Image Document `page_content`에 그 원문만 저장한다.
- 설명이 없으면 빈 문자열을 허용하고 `text_extraction_status="image_only"`로 표시한다.
- 향후 Chunking은 `content_type="text"`만 사용한다.

## 6. 목표 파일 구조

```text
notebooks/legal_api_v2/
├── 01_collect_legal_api_v2.ipynb
└── 02_preprocess_legal_api_v2.ipynb

data/legal_api_v2/
├── 01_raw_json/
│   └── {run_id}/
│       ├── collection_manifest.json
│       ├── eflaw/  eflaw_search.jsonl, eflaw_detail.jsonl
│       ├── prec/   prec_search.jsonl, prec_detail_{카테고리}.jsonl (6개)
│       └── expc/   expc_search.jsonl, expc_detail.jsonl
├── 02_raw_md/{run_id}/
│   ├── eflaw/  법령별 .md (16개)
│   ├── prec/   카테고리별 .md (6개, 200건 초과 시 part 분할)
│   └── expc/   카테고리별 .md (3개)
├── 03_document_json/{run_id}/
│   └── eflaw.jsonl, prec_{카테고리}.jsonl (6개), expc.jsonl
├── 04_processed_md/
│   └── {run_id}/{text,image_review}/  02와 같은 법령별·카테고리별 구성
└── 05_processed_document_json/{run_id}/
    ├── text/   eflaw.jsonl, prec_{카테고리}.jsonl (6개), expc.jsonl
    └── image/  images.jsonl
```

저장 형식 정책 (2026-07-18 사용자 결정):

- Document는 개별 `.json` 파일이 아니라 **JSON Lines(줄당 Document 1건)**로 저장한다. 묶음 단위는 법령별(eflaw)·카테고리별(prec·expc)이다.
- Raw JSON도 JSONL로 저장한다. 검색은 줄당 페이지 응답 1건, 상세는 줄당 문서 응답 1건이다. 각 줄은 `{"source_id", "query", "page", "fetched_at", "sha256_original", "response"}` envelope로 감싸되, `response` 안은 OC만 제거한 응답 원형을 유지한다(§3.3 위반 아님 — 응답 내용 무변형 원칙은 `response` 필드에 적용).
- 목표 파일 수는 폴더당 20개 안팎이다. 단일 파일이 50MB를 넘으면 `part01` 방식으로 분할하고 manifest에 기록한다.
- 검수용 Markdown이 너무 커지지 않도록 파일당 최대 200건으로 나눈다.

run 이름 규칙:

- 샘플 run 예시: `sample_YYYYMMDD_HHMMSS`
- 전체 run 예시: `full_YYYYMMDD_HHMMSS`

### 6.1 데이터 커밋 정책 (2026-07-18 사용자 결정)

저장소는 이미 v1 판례 데이터로 GitHub 100MB push 제한에 걸린 이력이 있다(`.gitignore`의 `kb_chunks_prec` 분할 커밋 참고). v2는 다음 정책을 따른다.

- 최종 승인된 `full_*` run은 `01_raw_json`~`05_processed_document_json` **전체를 커밋**한다. Sanitized Raw JSON이 Source of Truth이므로 재현성을 저장소에 보존한다.
- `sample_*` run은 커밋하지 않는다. Gate 1에서 `.gitignore`에 `data/legal_api_v2/**/sample_*/` 규칙을 추가한다.
- 단일 파일이 50MB를 넘으면 분할한다(§6 저장 형식 정책과 동일 기준). GitHub 100MB 제한에 미리 여유를 둔다.
- 커밋 유지 대상 full run은 1개다. 새 full run으로 교체할 때 이전 run의 삭제 여부는 사용자와 협의한다.
- Gate 3 완료 시 전체 파일 수와 용량 합계를 보고하고, 예상을 크게 벗어나면(예: 총 1GB 초과) 커밋 전에 다시 협의한다.
- `data/`는 CodeRabbit 리뷰 제외 대상이므로(`.coderabbit.yaml`) 대량 파일이 PR 리뷰를 막지는 않는다.

## 7. JSON 계약

### 7.1 Processed Text Document

```json
{
  "page_content": "전처리된 법령 텍스트",
  "metadata": {
    "record_id": "001234:article:0001001",
    "parent_id": "001234",
    "content_type": "text",
    "source_type": "statute",
    "section": "article",
    "law_id": "001234",
    "law_name": "주택임대차보호법",
    "article": "제1조",
    "article_no": "1",
    "chapter": "제1장 통칙",
    "structure_path": ["제1편 총칙", "제1장 통칙"],
    "is_deleted": false,
    "has_image": true
  }
}
```

### 7.2 Processed Image Document

```json
{
  "page_content": "API에 이미지 설명이 있으면 원문 설명, 없으면 빈 문자열",
  "metadata": {
    "record_id": "001234:article:0001001:image:1",
    "parent_record_id": "001234:article:0001001",
    "parent_id": "001234",
    "content_type": "image",
    "source_type": "statute",
    "section": "article",
    "law_id": "001234",
    "article": "제1조",
    "image_id": "122454725",
    "image_url": "https://www.law.go.kr/...",
    "image_order": 1,
    "source_section": "별표내용",
    "ocr_status": "not_attempted",
    "text_extraction_status": "image_only"
  }
}
```

### 7.3 Metadata 유지 및 확장

기존 호환 필드:

- `source_type`, `source_id`, `parent_id`, `record_id`
- `doc_title`, `source_org`, `doc_year`
- `authority`, `stage`, `issue`
- `source_file`, `section`

`authority` 판정도 v1 규칙을 유지한다: 법령 `binding`, 판례는 대법원이면 `binding` 아니면 `persuasive`, 해석례 `persuasive`.

자료 유형별 고유 필드도 v1(`origin/wip/backup-fix`의 `02_preprocess_legal_api.ipynb`)과 동일하게 유지한다.

- 법령: `law_id`, `law_name`, `law_type`, `effective_date`, `promulgation_date`, `promulgation_no`, `revision_type`, `ministry`
- 판례: `precedent_id`, `court`, `case_no`, `decision_date`, `decision_type`, `case_type`, `judgment_type`, `reference_laws`, `reference_cases`
- 해석례: `interpretation_id`, `case_no`(안건번호), `interpreting_agency`, `requesting_agency`, `decision_date`, `registration_date`, `law_name`, `article`

확장 필드:

- 수집 provenance: `collection_run_id`, `collection_queries`, `collection_categories`, Raw 경로와 해시
- 판례 선택: `selection_policy`, `sort`, `max_per_query`
- 법령 계층: `part`, `chapter`, `division`, `subdivision`, `structure_path`
- 조문: `article`, `article_no`, `article_title`, `article_key`
- 보존 상태: `is_deleted`, `revision_notes`, `language`, `duplicate_candidate`
- 이미지: `has_image`, `image_count`, `images`, `content_type`
- 판례 관련성: `relevance_level`, `relevance_score`, 매칭 근거

금지 필드:

- `chunk_id`
- `chunk_index`
- `overlap`
- 기타 토큰 Chunking 관련 필드

## 8. Notebook 구현 명세

### 8.1 `01_collect_legal_api_v2.ipynb`

권장 셀 순서:

1. 목적과 산출물 설명
2. `RUN_ID`, `SAMPLE_MODE`, timeout, retry, rate 설정
3. `PRECEDENT_MAX_PER_QUERY = 300`, `PRECEDENT_SORT = "ddes"` 설정
4. `LAW_API_OC` 존재 여부만 검사하고 값은 출력하지 않음
5. HTTP 요청·재시도 함수 (검색 응답 `resultCode` 검증 포함, §3.3)
6. OC 제거·Sanitized Raw 불변 저장 함수
7. manifest·해시 검증 함수
8. pagination과 ID dedup 함수
9. 법령 16종 검색·상세 수집
10. 판례 최신 300건 검색·상세 수집과 query/category provenance 구성
11. 해석례 검색·상세 수집
12. 저장된 Raw JSON을 다시 읽어 원문 문서별 Raw Markdown 생성
13. 저장된 Raw JSON을 다시 읽어 구조별 Document JSON 생성
14. 건수·누락·중복·OC·해시·파일 목록 검증

판례 API 호출에는 `sort="ddes"`를 항상 명시한다. 기본값에 의존하지 않는다.

검색 응답 파일 (줄당 페이지 응답 1건):

```text
{target}/{target}_search.jsonl
```

상세 응답 파일 (줄당 문서 응답 1건, 판례는 카테고리별 분할):

```text
{target}/{target}_detail.jsonl
prec/prec_detail_{카테고리}.jsonl
```

### 8.2 `02_preprocess_legal_api_v2.ipynb`

권장 셀 순서:

1. 목적과 비-Chunking 원칙
2. `INPUT_RUN_ID`와 경로 설정
3. 입력 Document inventory·schema 검증
4. 의미 보존형 텍스트 전처리 함수
5. HTML allowlist와 이미지 추출 함수
6. 법령 전처리
7. 판례 관련성 평가 및 섹션 전처리
8. 해석례 전처리
9. Text/Image Document 분리
10. 원문 문서별 `text` Markdown 생성
11. 문서별 `image_review` Markdown 생성
12. metadata·ID·빈 본문 예외·OC·정보 손실 검증
13. 유형별 샘플 출력

전처리는 `03_document_json`만 입력으로 사용하고 Raw JSON은 변경하지 않는다.

## 9. 검증 및 인수 기준

### 9.1 수집

- 모든 Sanitized Raw 파일이 유효한 JSON이다.
- 기존 Raw 파일을 덮어쓰지 않는다.
- manifest와 실제 파일 수·JSONL 레코드 수가 일치한다.
- 변형 전·후 SHA-256이 기록된다.
- v2 파일과 로그에 URL query `OC=`가 없다.
- 판례 요청에 `sort="ddes"`가 명시되어 있다.
- 검색 응답의 `resultCode`가 검증되고, 실패 응답이 manifest에 기록된다.
- 판례 상세 오류 응답 비율이 manifest에 기록된다.
- 판례 manifest에 `selection_policy="latest_per_query"`가 있다.
- 각 검색어의 `totalCnt`, 최신 300건 수집 수, 잘린 수가 기록된다.
- 관련성 점수는 수집 후 계산됐음을 기록한다.
- Raw Markdown 문서 수가 상세 원문 문서 수와 일치한다.
- `record_id`가 target 내에서 유일하다.

### 9.2 구조와 전처리

- 편·장·절·관 제목이 보존된다.
- 조문 `structure_path`가 직전 제목 상태와 일치한다.
- 조문·항·호·목 순서가 유지된다.
- 삭제 조문과 개정 정보가 사라지지 않는다.
- 저관련 판례는 삭제하지 않고 등급으로 표시한다.
- 실제 HTML만 제거되고 법률상 꺾쇠 텍스트는 남는다.
- 반복 문구가 임의로 제거되지 않는다.
- Text/Image Document가 `parent_record_id`로 연결된다.
- `image_only` 레코드에만 빈 `page_content`를 허용한다.
- Chunking metadata가 존재하지 않는다.

### 9.3 실행 가능성

- 모든 코드 셀이 문법 오류 없이 컴파일된다.
- 프로젝트 루트와 Notebook 폴더 양쪽에서 경로를 찾는다.
- 위에서 아래로 순차 실행할 수 있다.
- 샘플 설정을 전체 설정으로 바꿔도 코드 변경 없이 실행된다.

## 10. 단계별 승인 Gate

### Gate 1: 구현 시작

1. `start` 스킬의 명확성 gate를 확인한다.
2. 최신 `origin/develop`에서 `feat/legal-api-v2` 브랜치를 생성한다.
3. 사용자 변경과 기존 파일을 수정하지 않는다. 단 승인된 예외 2건은 수행한다:
   - `.env.example`의 OC 값을 placeholder로 교체 (§3.4)
   - `.gitignore`에 `data/legal_api_v2/**/sample_*/` 규칙 추가 (§6.1)
4. v2 폴더와 수집 Notebook만 생성한다. 판례 검색어는 부록 A의 34개를 사용한다.
5. 생성·수정 파일 목록을 공유한다.

### Gate 2: 수집 샘플

1. `sample_*` run으로 법령 1건, 판례·해석례 소량을 수집한다.
2. `01_raw_json`, `02_raw_md`, `03_document_json` 샘플을 만든다.
3. OC 제거, 판례 최신순, 해시, 계층, metadata, 이미지 참조를 검증한다.
4. 판례 상세 오류 응답("일치하는 판례가 없습니다") 진단: 오류가 난 판례 ID 몇 건을 `type=HTML`로 재조회해 본문이 제공되는지 확인하고 결과만 보고한다. HTML 수집 반영 여부는 사용자 승인 사항이다(§11 참고).
5. Codex 보조 에이전트가 읽기 전용으로 교차 검토한다.
6. 결과와 파일 목록을 공유하고 승인받는다.

### Gate 3: 전체 수집

1. 승인 후 `full_*` run으로 전체 수집한다.
2. 판례는 검색어별 최신 300건을 기본으로 한다.
3. 전체 후보가 아니라 최신 300건 정책임을 결과에 명시한다.
4. API 오류, 누락, totalCnt, 수집 수, 잘린 수를 공유한다.
5. `01`~`03` 파일 수와 manifest를 공유하고 승인받는다.
6. 예상 규모(2026-07-17 v1 수치 기준): 검색 약 100~130 페이지 호출 + 상세 조회 약 4,000~5,000건(판례 ID dedup 후 후보 + 법령 16건 + 해석례 ~320건). 이 중 판례의 30~45%는 API가 JSON 본문을 제공하지 않아 오류 응답이 예상되며(§3.3), 유효 판례는 ~2,128건 수준이다. 초당 5요청 제한 기준 상세 수집만 약 15~30분 소요. 실제 수치가 이 범위를 크게 벗어나면 진행을 멈추고 보고한다.
7. 전체 파일 수·용량 합계를 보고하고 §6.1 커밋 정책 기준으로 확인받은 뒤 커밋한다.

### Gate 4: 전처리 샘플

1. 전처리 Notebook을 생성한다.
2. 샘플 run으로 의미 보존형 전처리와 Text/Image 분리를 실행한다.
3. Raw/Processed 비교와 정보 보존 결과를 공유한다.
4. Codex 보조 에이전트가 읽기 전용으로 검토한다.
5. 승인받는다.

### Gate 5: 전체 전처리

1. 승인 후 전체 run을 전처리한다.
2. `04_processed_md`, `05_processed_document_json`을 만든다.
3. 파일 수, 레코드 수, 이미지 전용 수, 삭제 조문 수, 판례 관련성 분포를 공유한다.
4. 전체 인수 기준을 검증하고 최종 보고한다.

## 11. 범위 제외

- 토큰 기반 Chunking
- overlap과 chunk ID
- 이미지 다운로드
- OCR 및 이미지 설명 생성
- 임베딩과 벡터 DB 적재
- 기존 후속 Notebook이나 런타임 코드 수정
- 기존 v1 데이터 삭제 또는 교체
- 판례 상세 `type=HTML` fallback 수집 — JSON 미제공 판례(약 30~45%)를 HTML로 받아 파싱하면 수집률을 높일 수 있으나, HTML 파싱이라는 새 전처리 경로가 필요하므로 이번 범위에서 제외한다. Gate 2 진단 결과를 보고 후속 작업으로 논의한다.

## 12. 나중에 작업 재개 방법

다음 요청으로 시작한다.

```text
docs/legal_api_v2/legal_api_v2_execution_plan.md를 기준으로 Gate 1부터 진행해줘.
기존 파일은 수정하지 말고 각 승인 Gate에서 멈춰 결과와 파일 목록을 공유해줘.
```

실행 전 확인:

1. `.env`에 `LAW_API_OC`가 설정되어 있는지 확인한다.
2. 작업트리에 사용자 변경이 있는지 확인한다.
3. 정책 변경이 필요하면 구현 전에 이 문서와 사용자 결정을 먼저 갱신한다.
4. 새로운 정보 손실 가능성이나 API 응답 변형을 발견하면 추측하지 말고 질문한다.

## 부록 A. 확정 수집 검색어 목록

기준: `origin/wip/backup-fix`의 `notebooks/01_load_api.ipynb` (2026-07-18 확인). v2 구현은 이 목록을 그대로 사용하고, Notebook에도 동일하게 하드코딩한다.

### A.1 현행 법령 16종 (`EFLAW_QUERIES`)

주택임대차보호법 / 주택임대차보호법 시행령 / 민법 / 부동산등기법 / 공인중개사법 / 민사집행법 / 전세사기피해자 지원 및 주거안정에 관한 특별법 / 부동산 거래신고 등에 관한 법률 / 주민등록법 / 상가건물 임대차보호법 / 부동산등기규칙 / 공인중개사법 시행규칙 / 민간임대주택에 관한 특별법 / 국세징수법 / 집합건물의 소유 및 관리에 관한 법률 / 주택도시기금법

### A.2 판례 검색어 34개 (`PREC_QUERY_CONFIG`)

| 카테고리 | 검색어 |
|---|---|
| 보증금권리 (8) | 임대차보증금, 임대차보증금 반환, 임차인 대항력, 임차인 우선변제권, 소액임차인 최우선변제, 임대차 확정일자, 임차권등기명령, 임차인 전입신고 |
| 갱신종료 (6) | 계약갱신청구권, 임대차 묵시적 갱신, 임대차 갱신거절, 임대차 해지, 임차인 차임 연체, 임대차 차임 증감 |
| 전세사기 (6) | 전세사기, 임대차 가장임대차, 임대차 무권대리, 임대차 이중계약, 임대차보증금 사해행위, 임대차보증금 명의신탁 |
| 경매배당 (6) | 임차인 임의경매, 임차인 강제경매, 임차인 배당요구, 임차인 배당이의, 임차인 인도명령, 임대차 건물명도 |
| 수선원상회복 (5) | 임대인 수선의무, 임대차 원상회복, 임대차 누수, 임대차 하자, 임대차 통상의 손모 |
| 중개 (3) | 공인중개사 책임, 중개대상물 확인설명, 부동산 중개보수 |

### A.3 법령해석례 검색어 11개 (`EXPC_QUERY_CATEGORY`)

| 카테고리 | 검색어 |
|---|---|
| 보증금권리 (7) | 주택임대차보호법, 임대차보증금 우선변제, 대항력, 확정일자, 임차권등기명령, 소액임차인 최우선변제, 전입신고 |
| 갱신종료 (3) | 계약갱신청구권, 묵시적 갱신, 차임 증액 |
| 상가임대차 (1) | 상가건물 임대차 |

## 부록 B. 판례 관련성 평가 용어 (v1 로직)

기준: `origin/wip/backup-fix`의 `notebooks/02_preprocess_legal_api.ipynb`. §4.3의 산식이 사용하는 용어 목록이다.

임대차 anchor 11개 (`RENTAL_ANCHORS`):

임대차, 임대인, 임차인, 임차권, 보증금, 차임, 전세(정규식 `전세(?!계)`), 월세, 중개, 대항력, 우선변제

카테고리별 topic 용어 (`TOPIC_TERMS`):

| 카테고리 | topic 용어 |
|---|---|
| 보증금권리 | 보증금, 대항력, 우선변제, 최우선변제, 확정일자, 임차권등기, 전입신고 |
| 갱신종료 | 갱신, 해지, 차임, 연체, 계약종료 |
| 전세사기 | 전세사기, 가장임대차, 무권대리, 이중계약, 사해행위, 명의신탁 |
| 경매배당 | 경매, 배당, 인도명령, 건물명도, 우선변제 |
| 수선원상회복 | 수선, 원상회복, 누수, 하자, 통상의 손모 |
| 중개 | 공인중개사, 중개대상물, 확인설명, 중개보수, 중개업자 |
