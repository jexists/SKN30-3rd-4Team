# 법률 RAG 데이터 파이프라인 작업 계획

> 작성일: 2026-07-20
> 대상 데이터: `data/legal_api_v2`
> 목표: 전처리 완료 데이터 통합 → 사전 분석 → 청킹 → 로컬 임베딩 → Supabase 적재 → 조합별 검색 평가

## 0. 완료 기준

아래 흐름을 명령행 인자만 바꿔 반복 실행할 수 있으면 완료로 본다.

```text
05_processed_*_json의 기존 텍스트/PDF
                  ↓
        06_final 기준선 corpus
                  ↓
       Pandas 사전 분석 Notebook
                  ↓
   07_chunking_{chunk_size}_ov{overlap}
                  ↓
 08_embedding_{chunk_size}_ov{overlap}_{model_slug}
                  ↓
  로컬 Parquet 검색 평가 (청크 크기 × 모델 전 조합)
                  ↓
  우승 조합만 Supabase 배치 UPSERT + 재개 로그

이미지 메타데이터 119건
    └─ 부모 텍스트 존재 확인 완료 → 기준선 작업을 막지 않음
       └─ 비차단 표본 OCR에서 새로운 정보가 발견된 경우만
          06_final/ocr_*.jsonl로 증분 추가
          → 해당 파일만 청킹·임베딩·적재
          → corpus 변경 후 검색 평가 재실행
```

완료 조건은 다음과 같다.

- 모든 입력과 출력에 데이터 버전, 설정, 레코드 수, SHA-256을 담은 manifest가 있다.
- 같은 설정으로 재실행해도 결과와 `chunk_id`가 동일하다.
- 중단 후 파일 처음부터가 아니라 마지막 성공 배치 다음부터 재개할 수 있다.
- Supabase 적재를 다시 실행해도 중복 행이 생기지 않는다.
- 청크 크기와 모델 이름을 코드 수정 없이 CLI 옵션 또는 설정값으로 바꿀 수 있다.
- 평가 결과로 조합별 `Recall@k`, `MRR`, `nDCG`, 지연시간을 비교할 수 있다.

## 1. 현재 데이터와 코드 확인 결과

### 1.1 데이터 현황

| 구분 | 현재 확인 결과 |
|---|---:|
| API 텍스트 레코드 | 8,419 |
| PDF 텍스트 레코드 | 716 |
| 텍스트 합계 | 9,135 |
| 고유 `record_id` | 9,135, 중복 0 |
| 이미지 메타데이터 | 119 |
| 이미지 URL 있음 | 113 |
| 이미지 URL 없음 | 6 |
| 이미지 `page_content` | 119건 모두 빈 문자열 |
| 이미지의 부모 텍스트 | 119건 모두 존재 |
| 판례 텍스트 | 4,311 레코드 |
| 판례 관련성 | relevant 1,774 / candidate 1,999 / excluded 538 |
| 삭제·폐지 조문 (`is_deleted=true`) | 94 |
| 목차성 `heading` 레코드 | 244 |

API 텍스트의 글자 수는 최소 5, 중앙값 365, 평균 약 3,178, 최대 359,996이다. 500자를 초과하는 레코드는 3,681건, 1,000자를 초과하는 레코드는 2,826건이다. 판례 일부가 매우 길어서 청킹은 필요하다. 최단 레코드들은 `heading` 섹션("제1장 총칙" 등 6자 내외)이며 본문 검색 가치가 없다.

PDF 텍스트의 글자 수는 최소 35, 중앙값 723, 평균 약 757, 최대 2,839이다. 500자를 초과하는 레코드는 553건이다.

### 1.2 이미지 현황과 1차 결론

`05_processed_document_json/full_20260719_163719/image/images.jsonl`은 이미지 파일이 아니라 이미지 위치를 담은 메타데이터 파일이다.

| 이미지 종류 | 건수 | 우선순위 |
|---|---:|---|
| 판례 본문 `body` | 30 | 높음 — 본문 증거가 누락될 수 있음 |
| 법령 별표·서식 `annex` | 83 | 중간 — 표/서식 검색이 필요한 경우 포함 |
| 법령 부칙 `supplement` | 6 | 보류 — 현재 URL이 비어 있어 원본 확보부터 필요 |

119건 모두 대응하는 부모 텍스트가 이미 있다. 판례 본문 이미지 30건의 부모 텍스트는 평균 약 12,972자이며, 법령 별표 83건의 부모 텍스트는 평균 약 8,841자다. 주택임대차보호법 시행령의 관할구역·수수료 표와 부동산등기규칙 양식도 부모 레코드에 텍스트로 추출돼 있다.

**확정 결론:** OCR은 논리적으로 청킹 전 단계지만, 이 데이터에서는 청킹 전체를 막는 필수 선행조건이 아니다. 우선 부모 텍스트를 사용해 기준선 `06_final → 청킹 → 임베딩 → 평가`를 진행한다. 빈 이미지 레코드 119건은 기준선 corpus에서 제외하고, `has_image`, `image_count`, `images`를 포함한 이미지 상세와 제외 목록은 검색 metadata가 아닌 manifest에만 보존한다. 표본 OCR은 기준선 작업과 병렬 또는 이후에 비차단으로 수행하며, 부모 텍스트에 없는 검색 가치가 있는 정보가 발견된 경우에만 해당 OCR 파일을 증분 추가한다.

### 1.3 기존 코드에서 보완할 점

- `src/pipe/embed_chunks.py`는 OpenAI `text-embedding-3-small`에 고정돼 있어 로컬 모델 교체가 불가능하다.
- 임베딩은 `.tmp` 파일 단위라 중단되면 해당 파일을 처음부터 다시 처리한다.
- `src/pipe/ingest_supabase.py`는 append INSERT이므로 재실행 시 중복 가능성이 있다.
- `src/pipe/check_ingest.py`는 `source_id` 표본만 확인하므로 청크 단위 적재 완전성을 보장하지 못한다.
- 현재 `kb_chunks`에는 모델과 데이터 버전을 구분하는 고유키가 없다.
- `06_final`, `07_chunking_*`, `08_embedding_*` 단계가 아직 없다.

## 2. 먼저 확정할 운영 규칙

구현 시 다음을 기본값으로 사용한다.

1. `chunk_size` 단위는 기존 코드와 호환되는 **문자 수**로 한다. Notebook에서는 모델별 tokenizer 기준 토큰 수도 함께 계산한다.
2. 기본 `chunk_size=500`, 기본 `chunk_overlap=50`으로 시작한다.
3. 비교 후보는 우선 `300`, `500`, `800`, `1000`자로 한다.
4. overlap 변경 시 결과가 덮어써지지 않도록 항상 `07_chunking_500_ov50`, `08_embedding_500_ov50_kure-v1` 형식으로 저장한다.
5. `relevance_level=excluded` 판례 538건은 기본 검색 corpus에서 제외하고 manifest에 제외 수를 기록한다. 필요하면 `--include-excluded`로만 포함한다.
6. `candidate` 판례는 포함하되 평가에서 검색 노이즈를 별도로 확인한다.
7. 원본 `05_*`는 수정하지 않고 이후 단계는 모두 새 폴더에 생성한다.
8. `is_deleted=true` 법령 조문 94건은 기본 제외한다. 실효 조문을 근거로 답변할 위험을 막고, 제외 수와 `record_id` 목록을 manifest에 기록한다.
9. `section=heading` 244건은 기본 제외한다. 20자 미만 레코드는 자동 제외하지 않고 EDA 검토 목록에만 기록한다. 실제 확인 결과 heading·삭제 조문이 아닌 20자 미만 레코드 16건 중 유효 법령 조문과 `relevant` 판례 판결요지가 존재한다.
10. 청크 크기 × 모델 비교 평가는 `08_*` Parquet을 로컬에서 brute-force cosine으로 수행하고, 우승 조합만 Supabase에 적재한다. 전체 적재 전 우승 조합 5,000건을 표본 적재해 테이블과 HNSW 인덱스의 실제 크기를 측정하고, 예상 전체 크기가 DB 여유 용량을 넘으면 전체 적재를 중단한다.

### 2.1 구현 폴더 구조

기존 `src/`, `eval/`, `notebooks/` 코드는 절대 수정하지 않고, 최상위 `pipeline/` 폴더에 v2 파이프라인을 독립 구성한다(2026-07-20 요구 반영).

```text
pipeline/
├─ requirements.txt          # v2 전용 의존성; 기존 root pyproject.toml은 수정하지 않음
├─ config/
│  └─ config.yaml            # chunk size, overlap, 모델 registry, 경로, batch, Supabase 설정
├─ preprocess/
│  └─ build_final_corpus.py  # 06_final 생성
├─ analysis/
│  └─ prechunk_eda.ipynb     # 청킹 전 Pandas 분석
├─ chunking/
│  └─ run_chunking.py
├─ embedding/
│  └─ run_embedding.py
├─ supabase/
│  ├─ schema.sql             # kb_chunks_v2 생성 SQL
│  ├─ run_insert.py
│  └─ check_insert.py
├─ evaluation/
│  ├─ questions.jsonl        # gold record_id 포함 평가 질문
│  ├─ retrieval_eval.py      # 지표 계산 핵심 로직
│  └─ eval_retrieval.ipynb   # 조합 비교표·정성 확인
├─ logs/                     # 단계별 실행 로그
└─ README.md                 # 단계별 실행 방법·폴더 구조
```

각 단계는 독립 실행 가능해야 하며, 공통 설정은 `pipeline/config/config.yaml`에서 읽고 CLI 인자가 이를 덮어쓴다.

## 3. Phase A — 비차단 OCR 검증 및 선택적 증분 반영

### A-0. 순서 원칙

- 이미지가 유일한 원문이라면 반드시 `OCR → 청킹 → 임베딩` 순서로 처리한다.
- 현재 119건은 모두 부모 텍스트가 있으므로 OCR 검증 때문에 기준선 corpus 생성을 기다리지 않는다.
- 기준선 `06_final`에는 기존 부모 텍스트를 넣고 빈 이미지 레코드는 넣지 않는다.
- OCR이 새로운 내용을 만들었을 때만 해당 OCR 결과를 새 입력 파일로 추가한다.
- 증분 추가 후에는 전체 파일을 다시 처리하지 않고 새 OCR 파일만 청킹·임베딩·적재한다.
- 다만 corpus 내용이 달라졌으므로 데이터 버전과 manifest를 갱신하고 검색 평가는 다시 실행한다.

### A-1. 부모 텍스트와 제외 목록 보존

- [x] 이미지 119건 모두 대응하는 부모 텍스트 `record_id`가 있음을 확인했다.
- [x] 판례 본문, 법령 별표, 법령 부칙의 부모 텍스트 길이를 확인했다.
- [x] `images.jsonl` 119건의 URL, `image_id`, 부모 `record_id`, 문서명, section을 제외 목록으로 만든다.
- [x] `06_final/manifest.json`에 `image_records_excluded=119`와 `reason=parent_text_available`을 기록한다.
- [x] `has_image`, `image_count`, `images` 상세값은 검색 metadata에 넣지 않고 이미지 제외 manifest에서 관리한다.

### A-2. 비차단 표본 OCR과 품질 판정

- [x] `pipeline/analysis/ocr_assessment.ipynb`를 만들어 이미지 개수·유형별 분포·URL 유무·부모 텍스트 길이를 메타데이터에서 집계하고, 표본 다운로드분의 해상도와 표본 처리 속도 기반 전량 OCR 예상 소요시간을 출력한다.
- [x] 판례 본문 3~5건, 법령 별표 3~5건을 표본으로 고른다.
- [x] 전량 113건을 먼저 내려받지 않고 선택된 표본 이미지만 내려받는다.
- [x] 파일 확장자, MIME type, 크기, 해상도, SHA-256, 다운로드 상태를 기록한다.
- [x] 1차는 로컬 OCR(후보: PaddleOCR 한국어, Tesseract kor)로 실행하고, 표·서식이 깨지면 표 인식이 가능한 대안(예: Upstage Document AI 등 클라우드 API)을 표본으로만 비교한다. — tesseract.js 7.0.0 kor+eng WebAssembly 사용
- [x] OCR 결과 저장 형식은 기존 corpus와 동일한 JSONL(`{page_content, metadata}`)로 하고, OCR provenance는 별도 manifest에 둔다.
- [x] OCR 결과별 `ocr_engine`, `ocr_version`, `ocr_status`, `ocr_confidence`, `image_sha256`은 검색 metadata가 아니라 OCR manifest에 기록한다.
- [x] 수동으로 제목/조문 번호/금액/날짜/표 셀의 누락·오인식 여부를 확인한다.
- [x] 부모 텍스트와 정규화 중복률을 계산한다.
- [x] URL이 없는 6건은 표본 검증에서 필요해진 경우에만 원본 API JSON에서 실제 다운로드 필드를 찾는다. — URL 보유 10건 표본만으로 신규 검색 가치 0건이 확인되어 추가 조회 불필요

OCR 포함 기준은 다음과 같이 정한다.

- 부모 텍스트에 없는 법적 사실, 금액, 표, 도식이 있고 OCR 결과가 검색 가능한 수준이면 포함한다.
- 부모 텍스트와 사실상 중복이면 OCR 레코드는 넣지 않고 `duplicate_of`만 manifest에 남긴다.
- 인식 품질이 낮으면 임의 보정하지 않고 `ocr_status=needs_review`로 격리한다.
- 판례 본문 이미지는 검색 근거 누락 가능성이 있으므로 통과 기준을 만족하면 우선 포함한다.

### A-3. 증분 처리 규칙

새로운 정보가 확인된 OCR 결과만 다음과 같이 별도 파일로 추가한다.

```text
data/legal_api_v2/06_final/ocr_added.jsonl
    → data/legal_api_v2/07_chunking_500_ov50/ocr_added.jsonl
    → data/legal_api_v2/08_embedding_500_ov50_{model}/ocr_added.parquet
    → Supabase UPSERT
    → 검색 평가 재실행
```

- 기존 API/PDF 파일은 다시 청킹하거나 임베딩하지 않는다.
- 새 OCR 파일의 검색 metadata에는 원문과 연결할 `source_id`, `record_id`만 유지하고 OCR engine·파일 hash 등 provenance는 OCR manifest에 둔다.
- OCR 추가 전후 corpus가 다르므로 `dataset_version`을 구분한다.
- 운영용 최종 corpus를 동결하기 전에는 OCR 포함 여부를 최종 결정한다.

### A-4. 선택적 산출물

- `data/legal_api_v2/05_ocr_source/`
- `data/legal_api_v2/05_ocr_json/text/*.jsonl`
- `data/legal_api_v2/05_ocr_json/review/*.jsonl`
- `data/legal_api_v2/05_ocr_json/ocr_manifest.json`
- `pipeline/logs/ocr/*.jsonl`

## 4. Phase B — `06_final` 통합 및 메타데이터 정리

### B-1. 구현 파일

- [x] 기존 파이프라인 파일을 수정하지 않고 `pipeline/preprocess/build_final_corpus.py`를 새로 만든다.
- [x] 입력 폴더와 출력 폴더를 CLI로 받을 수 있게 한다.
- [x] 출력은 사람이 확인하기 쉬운 JSONL을 기본 형식으로 한다.
- [x] API와 PDF를 접두사 파일명으로 `06_final`에 모은다. — 채택 OCR 0건이므로 OCR 파일은 생성하지 않음

예상 구조:

```text
data/legal_api_v2/06_final/
├─ api_eflaw.jsonl
├─ api_expc.jsonl
├─ api_prec_갱신종료.jsonl
├─ api_prec_경매배당.jsonl
├─ ...
├─ pdf_counsel_casebook_2024.jsonl
├─ pdf_mediation_casebook_2023.jsonl
├─ ...
├─ ocr_precedent_body.jsonl          # OCR 채택 시
├─ ocr_statute_annex.jsonl           # OCR 채택 시
└─ manifest.json
```

### B-2. 레코드 형식

```json
{
  "page_content": "검색할 본문",
  "metadata": {
    "source_type": "statute",
    "source_id": "001248",
    "record_id": "001248:article:0001001",
    "doc_title": "주택임대차보호법",
    "source_org": "법무부",
    "doc_year": "2025",
    "authority": "binding",
    "issue": "법령",
    "section": "article",
    "article": "제1조",
    "article_no": "1",
    "article_title": "목적",
    "effective_date": "20260102"
  }
}
```

### B-3. 메타데이터 allowlist

검색용 metadata에는 검색 필터, 출처 표시, 원문 추적에 필요한 법률 정보만 넣는다. 청킹·임베딩·실행 관리 정보는 섞지 않는다.

공통 필수 필드는 다음과 같다.

- 식별: `source_type`, `source_id`, `record_id`
- 출처·인용: `doc_title`, `source_org`, `doc_year`, `authority`
- 검색 필터: `issue`, `section`

자료별 필드는 필요한 경우에만 유지한다.

- 법령: `article`, `article_no`, `article_title`, `effective_date`
- 판례: `court`, `case_no`, `decision_date`, `judgment_type`, `relevance_level`
- 법령해석례: `case_no`, `decision_date`, `interpreting_agency`
- PDF: `pdf_page`, `book_page`
- OCR 채택 시: 기존 자료형의 최소 metadata와 원문 연결용 `source_id`, `record_id`만 유지

다음 값은 검색 metadata에서 제외한다.

- 중복 식별값: `parent_id`, `law_id`, `law_name`, `precedent_id`, `interpretation_id`
- 원본·수집 정보: `source_file`, `raw_sha256_original`, `raw_sha256_sanitized`, `collection_queries`, `collection_categories`
- 분석·디버깅 정보: `matched_*`, `relevance_score`, `reference_laws`, `structure_path`
- 파이프라인 정보: `data_stage`, `dataset_version`, `content_sha256`
- 이미지 상세: `has_image`, `image_count`, `images`, OCR engine/version/confidence
- PDF 처리 정보: `total_pages`, `load_method`, `pdf_md5`

원본 파일 hash, 데이터 버전, 제외 목록, 이미지/OCR 상세, 청킹 설정은 폴더별 `manifest.json`에서 한 번만 관리한다.

### B-4. 검증

- [x] 빈 `page_content`가 0건인지 확인한다.
- [x] `is_deleted=true` 조문과 `heading` 섹션이 corpus에 0건인지 확인하고 제외 수를 manifest에 기록한다.
- [x] 20자 미만 레코드는 제외하지 않고 `short_records_review.csv`에 기록해 `record_id`, section, relevance, 본문을 검토할 수 있게 한다.
- [x] `record_id`가 비어 있거나 중복된 레코드가 0건인지 확인한다.
- [x] 필수 metadata 누락률을 파일·자료형별로 출력한다.
- [x] 제외 판례, OCR 미채택, 빈 본문 등 제외 사유와 수를 manifest에 기록한다.
- [x] 입력/출력 파일별 레코드 수와 SHA-256을 기록한다.
- [x] 실행 중 실패하면 임시 파일을 남기고 완성 파일로 rename하지 않는다.

## 5. Phase C — 청킹 전 Pandas 분석 Notebook

### C-1. 구현 파일

- [x] `pipeline/analysis/prechunk_eda.ipynb`를 만든다.
- [x] 설정 셀에는 `INPUT_DIR`, 길이 기준 목록, chunk 후보, overlap 비율만 둔다.
- [x] 경로는 Notebook 실행 위치가 달라도 repository root를 찾도록 작성한다.

### C-2. 필수 분석

Pandas DataFrame 한 행을 `06_final` 레코드 하나로 만든 뒤 다음을 계산한다.

- 전체 및 파일별 레코드 수
- `source_type`, `issue`, `section`, `relevance_level`별 레코드 수
- 글자 수의 `count/min/mean/median/p25/p75/p90/p95/p99/max`
- 300, 500, 800, 1,000, 1,500, 2,000자 초과 레코드 수와 비율
- 모델별 tokenizer 기준 토큰 수와 8,192 토큰 초과 여부
- 빈 본문, 지나치게 짧은 본문, 완전 중복 본문, 중복 `record_id`
- 20자 미만 레코드 검토표(자동 제외 금지)
- metadata 필드별 존재율
- 가장 긴 레코드 상위 10건, 가장 짧은 레코드 하위 10건과 해당 `record_id`, 문서명, section
- 자료형별 표본 레코드 원문 출력 (정성 확인용)
- 후보 chunk 크기별 예상 청크 수
- 후보별 예상 임베딩 저장 크기와 Supabase 행 수

그래프는 다음 정도만 만든다.

- 길이 histogram: 원본과 log scale
- `source_type`별 boxplot
- 후보 chunk 크기별 예상 청크 수 막대그래프

### C-3. Notebook 산출물

```text
reports/legal_api_v2/prechunk_eda/
├─ summary.csv
├─ length_by_file.csv
├─ length_by_source_type.csv
├─ records_over_threshold.csv
├─ metadata_coverage.csv
├─ longest_records.csv
├─ short_records_review.csv
└─ chunk_projection.csv
```

Notebook 마지막 셀에서 현재 데이터 기준 추천 chunk size를 분석 근거와 함께 명시적으로 출력한다. 확정 비교 후보는 문자 수 기준 `300/500/800/1000`, 기본 overlap은 각 chunk size의 10%로 한다.

## 6. Phase D — `06_final` 전체 청킹

### D-1. 구현 파일과 설정

- [x] 기존 청킹 코드를 수정하지 않고 `pipeline/chunking/run_chunking.py`를 새로 만든다.
- [x] 파일 상단에 `CHUNK_SIZE`, `CHUNK_OVERLAP` 변수를 둔다 — 변수 하나만 바꿔 재실행할 수 있어야 한다.
- [x] CLI의 `--chunk-size`, `--chunk-overlap`이 상단 기본값보다 우선한다.
- [x] 출력 폴더 `07_chunking_{chunk_size}_ov{overlap}`은 자동 생성한다.
- [x] 입력 폴더의 각 JSONL 파일마다 동일한 이름의 결과 파일을 만든다.
- [x] 출력은 청크마다 개별 JSON 파일이 아니라 **원본 파일별 JSONL**로 저장한다. cs300 기준 개별 파일 방식은 약 99,000개 파일이 생겨 파일시스템·Git 성능을 크게 해친다.

실행 예:

```powershell
uv run python pipeline/chunking/run_chunking.py --input-dir data/legal_api_v2/06_final --chunk-size 500 --chunk-overlap 50
```

### D-2. 청킹 규칙

- 문서의 `record_id` 경계를 넘어 서로 다른 레코드를 합치지 않는다.
- `RecursiveCharacterTextSplitter` 계열을 사용하되 한국어 법률 문서 구분자를 우선한다.
- 우선순위 구분자 예: 문단, 조·항·호 줄바꿈, 문장 종결, 공백, 문자.
- 기준보다 짧은 구조 레코드는 억지로 이웃 문서와 합치지 않는다.
- 너무 긴 단일 판례 본문은 설정된 크기와 overlap으로 재귀 분할한다.
- 청킹 숫자는 문자 수 기준 최대 크기로 사용하고 정확히 N번째 문자에서 무조건 자르지 않는다.
- 작은 테이블은 통째로 유지하고 큰 테이블은 행 단위로 나눈다.
- 분할된 테이블 청크에는 표 제목과 열 header를 반복하며, 한 행은 가능한 한 중간에서 자르지 않는다.

청킹 출력은 검색 metadata를 그대로 유지하고, 유일한 운영 필드 `chunk_id`만 metadata 밖 최상위에 둔다(2026-07-20 요구 반영).

```json
{
  "chunk_id": "결정적 고유값",
  "content": "청크 본문",
  "metadata": {
    "source_type": "statute",
    "source_id": "001248",
    "record_id": "001248:article:0001001",
    "doc_title": "주택임대차보호법",
    "section": "article"
  }
}
```

- `chunk_id`는 Supabase 중복 방지와 재실행을 위해 반드시 유지하며, 원본 `record_id` + 내부 순번 + 본문 hash로 결정적으로 생성한다. 내부 순번과 hash 자체는 출력 레코드에 별도 저장하지 않는다.
- 청크 레코드의 최상위 필드는 `chunk_id`, `content`, `metadata`만 허용한다.
- `chunk_index`, `chunk_total`, `chunk_size`, `char_count`, `token_count`, `chunk_overlap`, `chunking_method`, `chunking_version`, `dataset_version`, splitter 버전, 입력 hash는 검색 metadata나 청크 레코드에 넣지 않는다.
- 위 운영·통계 정보는 `07_chunking_*/manifest.json`과 EDA 보고서에서 한 번만 관리한다.

### D-3. 폴더와 검증

```text
data/legal_api_v2/07_chunking_500_ov50/
├─ api_eflaw.jsonl
├─ ...
└─ manifest.json
```

- [x] 모든 원본 레코드가 1개 이상의 청크로 연결되는지 확인한다.
- [x] 빈 청크와 중복 `chunk_id`가 0건인지 확인한다.
- [x] 설정별 청크 수, 길이 통계, source별 분포를 manifest에 기록한다.
- [x] 설정이 같은 완성 파일은 skip하고, 입력 hash가 달라지면 명시적으로 실패시킨다.
- [x] `.tmp`에 쓴 뒤 검증 통과 후에만 최종 파일로 rename한다.

## 7. Phase E — 로컬 모델 임베딩

### E-1. 비교 모델

모델 문자열은 아래 정확한 Hugging Face ID를 사용한다.

| 별칭 | Hugging Face ID | 차원 | 최대 길이 | 용도 |
|---|---|---:|---:|---|
| `kure-legal-ft-v1` | `kakao1513/KURE-legal-ft-v1` | 1024 | 8192 | 한국어 법률 특화 후보 |
| `kure-v1` | `nlpai-lab/KURE-v1` | 1024 | 8192 | 한국어 검색 기준 모델 |
| `bge-m3` | `BAAI/bge-m3` | 1024 | 8192 | 다국어 기준 모델, 우선 dense만 사용 |

참고 모델 카드:

- <https://huggingface.co/kakao1513/KURE-legal-ft-v1>
- <https://huggingface.co/nlpai-lab/KURE-v1>
- <https://huggingface.co/BAAI/bge-m3>

**주의(2026-07-20 확인):** 요구 프롬프트의 `dragonkue/KURE-legal-ft-v1`은 Hugging Face에 존재하지 않는다. 법률 파인튜닝 모델의 정확한 ID는 `kakao1513/KURE-legal-ft-v1`이다. 커뮤니티 파인튜닝 모델이므로 실제 corpus 평가로 검증한다. `dragonkue/BGE-m3-ko`는 기본 3개 모델 평가 후 대안이 필요할 때만 registry에 추가한다.

세 모델 모두 1024차원이므로 현재 `vector(1024)` 방향과 맞지만, 실제 적재 전 샘플 벡터 차원을 코드로 다시 검증한다. 법률 특화 모델의 공개 평가 결과는 자체 보고 수치이므로 이름만 보고 최종 모델로 확정하지 않고 이 프로젝트 질문으로 비교한다.

### E-2. 구현 방향

- [x] 기존 `src/pipe/embed_chunks.py`를 수정하지 않고 `pipeline/embedding/run_embedding.py`를 새로 만든다.
- [x] 파일 상단에 `MODEL_NAME` 변수를 두고, 어떤 Hugging Face 모델이든 이 변수(또는 `--model`)만 바꿔 교체 가능하게 한다.
- [x] `pipeline/config/config.yaml`을 단일 원본으로 모델 registry의 ID, slug, revision, 차원, normalize 여부, query/document prefix를 관리한다.
- [x] `--model`, `--input-dir`, `--output-dir`, `--batch-size`, `--part-size`, `--device`를 지원한다.
- [x] `--device` 미지정 시 CUDA 가용하면 GPU, 아니면 CPU를 자동 선택한다.
- [x] 입력 청킹 manifest에서 overlap을 읽어 출력 폴더 `08_embedding_{chunk_size}_ov{overlap}_{model_slug}`를 자동 생성한다.
- [x] 문서와 query 임베딩 모두 동일 모델의 권장 방식을 사용한다.
- [x] cosine 검색용 벡터는 `normalize_embeddings=True`로 통일한다.
- [x] 모델의 revision/commit hash와 라이브러리 버전을 manifest에 기록한다.
- [x] 검색 평가 시 문서 임베딩과 같은 모델 ID·revision을 강제한다.
- [x] 기존 root `pyproject.toml`은 수정하지 않고 `pipeline/requirements.txt`에 `sentence-transformers`, `torch`, `tqdm`, `pyarrow` 등 v2 전용 의존성을 명시한다.

실행 예:

```powershell
uv run python pipeline/embedding/run_embedding.py --input-dir data/legal_api_v2/07_chunking_500_ov50 --model kakao1513/KURE-legal-ft-v1
```

출력 예:

```text
data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1/
├─ api_eflaw.parquet
├─ ...
├─ _parts/                  # 실행 중 배치 체크포인트
└─ manifest.json
```

청크 JSONL은 검수가 쉽지만 벡터 배열 JSON은 크고 느리므로 임베딩 결과는 Parquet을 우선 사용한다. Supabase 적재기는 JSONL과 Parquet을 모두 읽을 수 있게 한다.

### E-3. 중단 재개와 진행 표시

**이 작업 머신에는 NVIDIA GPU가 없다(2026-07-20 확인).** 세 모델 모두 대형 임베딩 모델이므로 CPU 전체 실행 전 `--limit 100` smoke test에서 실측 처리량과 예상 완료시간을 계산한다. 아래 배치 체크포인트는 필수다.

**실행 환경 확인(2026-07-20):** Windows 호스트에서는 조직의 애플리케이션 제어 정책이 `torch_python.dll`과 `pyarrow` DLL을 WinError 4551로 차단한다. 같은 PC의 WSL2 Ubuntu 24.04 ext4 가상환경(`/home/playdata2/.venvs/legal-rag-v2`)에서는 PyTorch·PyArrow가 정상 동작하고 전체 테스트 70건과 세 모델 100건 smoke를 통과했다. 전체 임베딩도 WSL에서 checkpoint 방식으로 실행한다. 재현 명령은 `pipeline/README.md`에 둔다.

100건 smoke 결과는 세 모델 모두 1024차원, NaN 0, L2 norm 평균 1.0, 동일 `chunk_id` 100건으로 검증됐다. 모델 최초 로드 시간을 포함한 CPU 실측은 `KURE-v1` 162초, `KURE-legal-ft-v1` 173초, `BGE-M3` 219초다. 전체 시간은 캐시·파일별 길이에 따라 달라지므로 보수적으로 수십 시간을 예상한다.

파일 전체를 다시 임베딩하지 않도록 배치별 part 파일을 사용한다.

- 모델 내부 batch는 8, 디스크 part checkpoint는 512건으로 분리해 CPU 안정성과 part 파일 수를 함께 관리한다.
- 각 part에 입력 파일 hash, 시작/끝 line, chunk 수, 모델 ID, 벡터 차원을 기록한다.
- part 저장과 검증이 끝난 뒤 checkpoint의 `next_line`을 갱신한다.
- 재실행 시 같은 설정·입력 hash의 완료 part는 skip한다.
- 파일 완료 후 part를 파일별 최종 Parquet으로 병합하고 행 수를 검증한다.
- 각 checkpoint마다 전체/완료/남은 청크, 처리 속도, ETA를 표시한다.
- 오류와 재시도는 `pipeline/logs/embedding/{run_id}.jsonl`에 남긴다.
- [x] WSL VM 재시작을 Windows 감시기가 감지해 임베딩·평가 실행기를 checkpoint에서 다시 시작하고, 세 모델 평가 완료 후 자동 종료한다. Windows 자체 재부팅 후에는 사용자가 감시기를 다시 실행한다.
- CUDA OOM이면 batch size를 줄여 재시도하고, 반복 실패 시 마지막 성공 위치에서 종료한다.

## 8. Phase F — Supabase 배치 적재 (우승 조합만)

비교 평가는 Phase G에서 로컬 Parquet으로 수행하므로, Supabase 적재는 **평가에서 확정된 우승 조합 1개**에만 적용한다. 단일 모델만 적재하므로 한 테이블에 서로 다른 벡터 공간이 섞여 ANN 인덱스 recall이 떨어지는 문제가 없다. 이후 다른 실험 조합을 추가 적재하게 되면 `experiment_id`별 partial index를 사용한다.

### F-1. DB 고유키와 스키마

재실행 안전성을 위해 metadata 표본 확인이 아니라 DB 고유키가 필요하다.

권장 핵심 컬럼:

```sql
experiment_id text         not null,
chunk_id      text         not null,
content       text         not null,
embedding     vector(1024) not null,
metadata      jsonb        not null,
unique (experiment_id, chunk_id)
```

- [x] 기존 `kb_chunks`를 수정하지 않고 실험용 `kb_chunks_v2` 테이블 생성 SQL을 `pipeline/supabase/schema.sql`로 작성한다.
- [x] `kb_chunks_v2_embedding_hnsw_idx`를 `embedding vector_cosine_ops` 기준으로 생성하고, 기존 검색 RPC를 수정하지 않은 새 `match_kb_chunks_v2` 함수를 만든다.
- [x] `experiment_id`는 `legalv2_20260720_cs500_ov50_kure-v1`처럼 데이터 버전·청크 크기·overlap·모델 조합을 모두 포함한다.
- [x] 상세 데이터 버전, 모델 ID, chunk size, overlap은 experiment manifest 또는 별도 experiment 테이블에 한 번만 기록한다.
- [x] `experiment_id` 필터가 검색 함수에도 적용되게 한다.
- [x] 여러 실험 조합이 한 테이블에 있어도 검색 시 서로 섞이지 않게 한다.
- [x] 재실행 기본 정책은 `ON CONFLICT (experiment_id, chunk_id) DO NOTHING`으로 고정한다.

### F-2. 적재 CLI 구현 및 dry-run

```powershell
uv run python pipeline/supabase/run_insert.py --input-dir data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1 --experiment-id legalv2_20260720_cs500_ov50_kure-v1 --batch-size 500 --dry-run
```

필수 기능:

- 폴더 경로를 받으면 지원하는 모든 결과 파일을 정렬해 처리한다.
- 기존 `src/pipe/ingest_supabase.py`를 수정하지 않고 `pipeline/supabase/run_insert.py`를 새로 만든다.
- 실행 전 DB 연결, pgvector, 테이블 컬럼, vector 차원, 모델, 데이터 버전을 preflight한다.
- `--dry-run`으로 파일 수, 레코드 수, 차원, 예상 배치 수만 검증할 수 있다.
- 배치 commit이 성공한 뒤에만 checkpoint를 전진시킨다.
- 실패한 배치는 지수 backoff로 재시도하고 실패한 `chunk_id` 범위를 로그에 남긴다.
- 진행률에 완료/전체/남은 행, 현재 파일, 배치, 처리 속도, ETA를 표시한다.

### F-3. 5,000건 용량 gate

> **이번 실행 범위 제외(2026-07-20 사용자 결정):** Supabase 5,000건 표본 적재를
> 진행하지 않는다. 이에 의존하는 전체 적재, DB reconciliation, Supabase 검색 smoke,
> DB 기반 앱 전환도 함께 보류한다. 구현 코드는 보존하며 추후 `execution_scope.json`의
> 범위를 `full`로 되돌리고 총 DB 허용량을 확인한 뒤 재개할 수 있다.

전체 적재 전에 우승 조합의 앞 5,000건만 같은 `experiment_id`로 적재한다.

```powershell
uv run python pipeline/supabase/run_insert.py --input-dir data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1 --experiment-id legalv2_20260720_cs500_ov50_kure-v1 --batch-size 500 --limit 5000 --db-capacity-bytes <프로젝트_DB_총허용량_바이트> --resume
```

`--limit 5000`은 재실행마다 추가하는 수가 아니라 해당 experiment 전체의 상한으로 적용한다. `--db-capacity-bytes`에는 요금제 이름으로 추측한 값이 아니라 Supabase 프로젝트에서 확인한 총 DB 허용량을 넣는다.

- [ ] `pg_total_relation_size('kb_chunks_v2')`와 `pg_indexes_size('kb_chunks_v2')`로 heap·TOAST·HNSW index 실제 크기를 각각 측정한다.
- [ ] 적재 전후 크기 차이와 실제 신규 삽입 행 기준 평균 크기로 전체 예상치를 계산하고, 기본 1.25 안전계수를 적용해 현재 프로젝트의 남은 DB 용량과 비교한다.
- [ ] 전체 예상치가 남은 용량을 넘으면 자동으로 계속 적재하지 않는다.
- [ ] 초과 시 `halfvec(1024)`, 더 큰 chunk size로 행 수 축소, Supabase 유료 용량 중 하나를 별도 결정한다.
- [ ] 통과하면 전체 재개 직전 현재 DB 크기로 gate를 다시 검사하고, 같은 `experiment_id`와 ledger로 5,001번째 행부터 전체 적재를 재개한다.

용량 gate를 통과한 뒤에만 다음 전체 재개 명령을 실행한다.

```powershell
uv run python pipeline/supabase/run_insert.py --input-dir data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1 --experiment-id legalv2_20260720_cs500_ov50_kure-v1 --batch-size 500 --resume
```

### F-4. 재개 상태와 로그

사람이 읽는 로그와 기계가 읽는 SQLite ledger를 함께 둔다. 요구 프롬프트의 `insert.log / success.log / failed.log`는 아래처럼 대응한다.

```text
pipeline/logs/insert/
├─ ingest_state.sqlite3      # 재개 판단의 단일 기준 (성공 오프셋 기록)
├─ {run_id}.jsonl            # insert.log — 전체 진행 이벤트
├─ {run_id}_success.jsonl    # success.log — 커밋 성공 배치
└─ {run_id}_failed.jsonl     # failed.log — 실패 배치와 chunk_id 범위
```

재실행 시 ledger의 성공 오프셋 다음부터 이어서 처리하고, failed 로그에 남은 배치를 우선 재시도한다.

ledger 최소 필드:

- `run_id`, `input_dir`, `input_file`, `input_sha256`
- `experiment_id`
- `total_records`, `committed_records`, `next_offset`
- `status`: pending/running/completed/failed
- `last_error`, `updated_at`

### F-5. 적재 후 검증

- [x] 기존 `src/pipe/check_ingest.py`를 수정하지 않고 `pipeline/supabase/check_insert.py`를 새로 만든다.
- 파일별 로컬 청크 수와 DB의 해당 `experiment_id` 행 수를 정확히 비교한다.
- 전체 count뿐 아니라 고유 `chunk_id` count도 비교한다.
- 벡터 차원, NULL, 빈 content, 중복 key가 0인지 확인한다.
- 임의 표본의 content hash와 metadata를 로컬 파일과 DB에서 비교한다.
- 완료 결과를 `reports/legal_api_v2/ingest/{run_id}_reconciliation.csv`로 저장한다.

## 9. Phase G — 청크 크기 × 모델 검색 평가

### G-1. 평가 데이터

- [x] `pipeline/evaluation/questions.jsonl`을 만든다.
- [x] 각 질문에 정답 문자열만 두지 말고 관련 `record_id` 또는 허용되는 `chunk_id`를 사람이 표시한다.
- [x] 법령, 판례, 법령해석례, PDF가 골고루 검색되게 한다.

최소 smoke test 질문 5개:

1. 임차인이 주택의 인도와 주민등록을 마친 경우 대항력은 언제 생기는가?
2. 소액임차인의 최우선변제 요건과 범위는 무엇인가?
3. 임대인이 실제 거주를 이유로 계약갱신 요구를 거절할 수 있는 조건은 무엇인가?
4. 묵시적으로 갱신된 임대차를 임차인이 해지하면 언제 효력이 생기는가?
5. 공인중개사가 선순위 권리나 중개대상물 상태를 잘못 설명한 경우 책임은 어떻게 판단되는가?

5개는 파이프라인 smoke test에는 충분하지만 모델을 최종 선정하기에는 표본이 작다. 가능하면 1차 결과 후 자료형·쟁점별 20~30문항으로 확장한다.

질문 레코드 예:

```json
{
  "question_id": "q001",
  "question": "임차인의 대항력은 언제 생기나요?",
  "issue": "보증금권리",
  "gold_record_ids": ["검수 후 실제 record_id 입력"],
  "notes": "법령 조문 검색"
}
```

### G-2. 평가 코드

- [x] 기존 평가 코드를 수정하지 않고 지표 계산 로직은 `pipeline/evaluation/retrieval_eval.py`에, 조합 비교표·정성 확인은 `pipeline/evaluation/eval_retrieval.ipynb`에 만든다.
- [x] Notebook에서 질문별 Query, Top-K 검색 결과, similarity score를 출력하고 마지막 셀에 청크 크기 × 모델 비교표를 출력한다.
- [x] `--dataset-version`, `--embedding-model`, `--chunk-size`, `--top-k`를 받는다.
- [x] 문서와 같은 모델로 query를 임베딩한다.
- [x] 검색은 `08_*` Parquet의 정규화 벡터 행렬을 실험별로 메모리에 한 번만 읽고 brute-force cosine(numpy 내적)으로 수행한다. 질문마다 Parquet을 다시 읽지 않으며, 실제 지연시간은 평가 결과에 기록한다.
- [ ] Supabase 기반 검색 확인은 우승 조합 적재 후 smoke test로만 수행하며, 이때 검색 함수는 `experiment_id`를 반드시 필터한다.
- [x] 정성 확인용 top-k 본문, 점수, 문서명, section, `record_id`, `chunk_id`를 저장한다.

핵심 지표:

- `Hit@1/3/5/10`
- `Recall@1/3/5/10`
- `MRR@10`
- `nDCG@10`
- 질문당 검색 지연시간 p50/p95
- 임베딩 시간, index 크기, 총 청크 수

결과 구조:

```text
reports/legal_api_v2/eval/
├─ per_query_results.csv
├─ experiment_summary.csv
├─ leaderboard.csv
└─ topk_review.jsonl
```

기본 우승 기준은 `Recall@5`를 최우선, `nDCG@10`을 다음으로 하고, 비슷하면 청크 수·저장 용량·p95 지연시간이 작은 조합을 선택한다. 모델 카드의 공개 점수는 참고만 하고 실제 corpus 평가 결과를 기준으로 한다.

## 10. 추가로 필요한 작업

### 10.1 테스트

- [x] 작은 fixture로 `06_final → chunk → embed → ingest dry-run` 통합 테스트 — WSL PyArrow 환경에서 통과
- [x] 같은 입력 재실행 시 동일 `chunk_id`가 나오는 테스트
- [x] metadata 보존 테스트
- [x] 중단 지점에서 마지막 완료 part 다음부터 재개하는 임베딩 테스트
- [x] 동일 폴더·동일 limit 재개 시 추가 배치 0건, ledger offset 유지, 기존 capacity gate 재사용을 검증하는 DB 독립 통합 테스트 — 실제 Supabase RPC/행 수 확인은 우승 조합 smoke에서 수행
- [x] 다른 모델/데이터 버전 검색 결과가 섞이지 않도록 `experiment_id` 복합 PK와 검색 함수 필터를 검사하는 테스트

### 10.2 버전과 재현성

- [x] 모든 핵심 manifest에 git commit, 시작/종료 시각, 총 실행 시간, Python/라이브러리 버전, 설정, 입력 hash를 기록한다. — 외부 OCR은 엔진 전용 계약 적용
- [x] 완료된 핵심 manifest의 시간·버전·입력 lineage 계약을 자동 검증하고, 초기 생성분은 본문 파일을 바꾸지 않는 `post_run_backfill`로 보강한다. — 외부 OCR은 engine/version·이미지 hash 전용 계약
- [x] 모델은 이름뿐 아니라 smoke에서 검증한 Hugging Face revision까지 기본 고정하고 `--revision`으로 교체 가능하게 한다.
- 실험 설정은 `pipeline/config/config.yaml` 한 곳에서 관리한다 (chunk size, overlap, 모델, batch size, 경로, Supabase 설정).
- [x] 원본 `05_processed_*`와 소형 reports는 유지하고, 재생성 가능한 OCR 표본·`06_final`·`07_chunking_*`·`08_embedding_*`·실행 로그는 `.gitignore`로 제외한다.
- `pipeline/README.md`에 단계별 실행 방법, 폴더 구조, 재개 방법을 정리한다.

### 10.3 운영 안전성

- `.env`와 DB URL을 로그에 출력하지 않는다.
- 전체 실행 전 100개 레코드 `--limit` smoke test를 지원한다.
- [x] 디스크 여유 공간, GPU/CPU, 예상 청크 수와 저장 용량을 preflight에서 확인한다. — checkpoint 실측 또는 행당 10,000바이트, 1.25 안전계수, 1GiB reserve 적용
- 대량 적재 전 운영 테이블과 실험 테이블 또는 dataset version을 분리한다.
- 평가에서 최종 조합을 고른 뒤에만 앱 기본 검색 설정을 변경한다.
- [x] 기존 `vs_method.py`를 수정하지 않는 읽기 전용 `pipeline/app/vs_method_v2.py` 어댑터와 환경변수 템플릿을 준비한다. — 우승 모델·experiment 연결은 검색 smoke 이후 수행
- [x] 우승 실험의 승인된 평가 CSV에서 앱 강/약 임계값 후보를 재현 가능하게 계산하는 보정 스크립트를 준비한다. — provisional 결과는 앱 적용 금지
- [x] 승인 후 전체 재평가·최종 우승·임계값을 확정하고, Supabase 검색 smoke 통과 후에만 비밀 없는 앱 v2 환경설정을 생성하도록 실행 순서를 분리한다.
- [x] 기존 `app/`·`src/` 파일을 수정하지 않는 별도 `pipeline/app/main_v2.py` 진입점을 만들고 v2에서는 DB DDL 없이 준비 상태만 읽도록 분리한다. — 실제 전환은 우승 적재·검색 smoke 후
- [ ] 우승 로컬 모델의 top-1 및 첫 gold similarity 분위수로 앱의 기존 OpenAI 기준 `GRADE_STRONG`·`GRADE_WEAK` 임계값을 다시 정한 뒤 반영한다.

## 11. 권장 구현 순서

### P0 — 데이터 기준선

- [x] 부모 텍스트를 사용해 `pipeline/preprocess/build_final_corpus.py`와 기준선 `06_final` 생성
- [x] 빈 이미지 레코드 119건, `is_deleted` 조문 94건, `heading` 244건은 제외하고 상세는 manifest에만 유지
- [x] 20자 미만 레코드는 자동 제외하지 않고 EDA 검토 목록으로 출력
- [x] `06_final/manifest.json` 검증
- [x] Pandas 사전 분석 Notebook 작성 및 실행

### P0 병렬 — 비차단 OCR 검증

- [x] 판례 본문 이미지 3~5건과 법령 별표 이미지 3~5건 표본 OCR
- [x] 부모 텍스트에 없는 검색 가치가 있는 정보인지 확인
- [x] 중복이면 OCR 생략 결정과 근거만 manifest에 기록
- [x] 새로운 정보가 있으면 `ocr_added.jsonl`만 증분 청킹·임베딩·적재 — 새 검색 가치 0건으로 생성·증분 처리 불필요
- [x] OCR을 추가한 corpus의 데이터 버전 갱신 및 검색 평가 재실행 — OCR 미채택으로 corpus 버전 변경·재평가 불필요

### P1 — 반복 가능한 실험 파이프라인

- [x] `pipeline/chunking/run_chunking.py` 구현
- [x] 우선 `500/50` 청킹 및 검증
- [x] `06_final` 8,259건과 네 청킹 후보 226,778청크의 최소 metadata 계약 전수 검사 — 허용 외 필드·필수값 누락·ID 중복·청킹 전후 metadata 변경 모두 0건 (`reports/legal_api_v2/metadata_audit.json`)
- [x] `pipeline/embedding/run_embedding.py`와 로컬 임베딩 모델 registry 구현
- [x] 세 모델·각 100개 청크 smoke test 및 차원·NaN·정규화·ID 일치 검증 — smoke 출력 폴더와 `experiment_id` 모두 정식 전체 실행과 분리
- [ ] 3개 모델 전체 임베딩 — 2026-07-20 WSL에서 `500/50` 순차 실행 중이며 checkpoint로 재개 가능 (`check_progress.py`로 진행률 확인)

### P2 — 로컬 비교 평가

- [x] gold `record_id`가 포함된 질문을 20개로 확장하고 원문 검수 후 gold 참조 26건(고유 25건) corpus 존재 검증 — q004에서 답을 직접 포함하지 않은 표준계약서 페이지 1건 제외
- [x] 질문별 gold 문서·본문 미리보기를 묶은 팀 검수용 Markdown 보고서를 자동 생성한다. — 승인 원본은 `questions.jsonl` 유지
- [x] 고유 gold 레코드 25건이 `300/500/800/1000` 네 청킹 후보 모두에 25/25 존재함을 검증 — `reports/legal_api_v2/eval/question_audit/gold_chunk_coverage_summary.json`
- [ ] `500 × 3개 모델`을 로컬 Parquet에서 먼저 평가
- [ ] 모델 후보를 1~2개로 줄인 뒤 `300/500/800/1000` 청크 크기 비교 — `500×3`에서 Recall@5·nDCG@10 차이가 모두 0.05 이내면 상위 2개, 아니면 상위 1개를 자동 shortlist하고 `run_grid_after_500.sh`가 나머지 조합을 checkpoint 실행
- [x] 자료형·쟁점별 평가 질문을 20개로 확장
- [ ] 최종 모델·청크 크기·overlap 확정

### P3 — 우승 조합 Supabase 적재

- [x] Supabase에 `kb_chunks_v2`, experiment 테이블, HNSW, 격리 검색 RPC 적용 및 100건 Parquet 읽기 전용 preflight 통과 — 2026-07-20, 적용 전부터 `public.kb_chunks`는 존재하지 않았고 v2 테이블 0행 확인 결과는 `reports/legal_api_v2/ingest/schema_status.json`
- [x] 폴더 단위 resumable UPSERT 구현 (우승 조합만 적재)
- [x] 승인된 우승 조합에 대해 dry-run→5,000건 용량 gate→전체 재개→reconciliation→검색 smoke 순서를 강제하는 적재 실행기를 준비한다. — 총 DB 허용량은 반드시 명시
- [ ] 우승 조합 5,000건 표본 적재 후 테이블·HNSW index 용량 gate 통과 — 이번 실행 범위 제외
- [x] 진행률, JSONL 로그, SQLite ledger 구현
- [x] final·청킹·임베딩 checkpoint·평가·검수·Supabase·앱 설정 상태를 한 번에 보는 JSON/Markdown 상태 보고서를 구현한다.
- [x] Windows 감시기가 상태 보고서를 10분 간격으로 자동 갱신하도록 연결한다.
- [x] 적재 reconciliation 구현
- [ ] Supabase 검색 smoke test 후 앱 설정 반영 — 이번 실행 범위 제외

## 12. 확정된 결정과 후속 조건

- [x] 20자 미만 레코드는 자동 제외하지 않고 EDA 검토 목록에만 기록한다. — 2026-07-20 결정
- [x] 청크 레코드는 `chunk_id`, `content`, 검색용 `metadata`만 저장한다. — 2026-07-20 결정
- [x] `has_image`, `image_count`, `images` 등 이미지 상세는 검색 metadata에서 제외하고 manifest에서 관리한다. — 2026-07-20 결정
- [x] 폴더명에는 overlap을, `experiment_id`에는 데이터 버전·chunk size·overlap·모델을 모두 포함한다. — 2026-07-20 결정
- [x] 청킹 단위는 문자 수, 후보는 `300/500/800/1000`, 기본 overlap은 10%로 한다. — 2026-07-20 결정
- [x] 기존 root `pyproject.toml`은 수정하지 않고 `pipeline/requirements.txt`에서 v2 의존성을 관리한다. — 2026-07-20 결정
- [x] Supabase 전체 적재 전 우승 조합 5,000건을 표본 적재해 실제 테이블·HNSW index 크기를 검증한다. — 2026-07-20 결정
- [x] `relevance_level=excluded` 판례 538건은 기본 제외한다. — 2026-07-20 결정
- [x] 평가 질문 작성자가 gold `record_id` 후보를 만들고, 최종 평가는 사용자 또는 프로젝트 팀이 직접 검수한다. — 2026-07-20 결정
- [x] 기존 `kb_chunks`는 수정하지 않고 실험용 `kb_chunks_v2`를 새로 만든다.
- [x] embedding 산출물은 Parquet으로 저장한다 (로컬 평가가 Parquet을 직접 읽으므로). — 2026-07-20 결정
- [x] `is_deleted=true` 조문 94건은 기본 제외한다. — 2026-07-20 결정
- [x] `heading` 244건은 기본 제외한다. — 2026-07-20 결정
- [x] 비교 평가는 로컬 Parquet에서 수행하고 우승 조합만 Supabase에 적재한다. — 2026-07-20 결정
- [x] 법률 파인튜닝 모델 ID는 `kakao1513/KURE-legal-ft-v1`로 확정 — `dragonkue/KURE-legal-ft-v1`은 존재하지 않음 (2026-07-20 HF API 확인)

다음은 지금 결정할 필요가 없는 조건부 항목이다.

- [x] 비차단 표본 OCR에서 부모 텍스트에 없는 정보가 확인될 경우에만 법령 별표 83건의 OCR 포함 여부를 결정한다. — 표본 신규 검색 가치 0건이므로 전량 OCR 미실행·미포함
- [ ] 5,000건 용량 gate가 실패할 경우에만 `halfvec`, 청크 수 축소, Supabase 용량 확장 중 하나를 결정한다.

## 13. 요구 프롬프트(2026-07-20) 대비 조정 사항

요구 프롬프트를 이 계획에 반영하되, 아래 항목은 근거와 함께 조정했다.

| 프롬프트 요구 | 조정 | 근거 |
|---|---|---|
| `dragonkue/KURE-legal-ft-v1` | `kakao1513/KURE-legal-ft-v1` | dragonkue 버전은 HF에 존재하지 않음. 대안으로 `dragonkue/BGE-m3-ko` 추가 등록 |
| Chunk마다 JSON 파일 하나씩 | 원본 파일별 JSONL | cs300 기준 약 99,000개 개별 파일은 파일시스템·Git 성능 저하. 청크 단위 확인은 JSONL 한 줄 = 청크 하나로 동일하게 가능 |
| 06_final 평면 metadata 예시 (`id/title/text/...`) | `{page_content, metadata}` 구조 유지 | 기존 LangChain Document 호환 + 실제 필드명(B-3 allowlist)이 이미 원본 데이터에 존재. 프롬프트의 필드는 모두 대응됨: id→`record_id`, title→`doc_title`, law_name→`doc_title`(법령), chapter/article→`article*`, source→`source_org`, type→`source_type`, page→`pdf_page`, updated_at→`effective_date`/`decision_date`, text→`page_content` |
| `chunk_index` 등 추가 metadata | 저장하지 않고 `chunk_id`, `content`, 검색 metadata만 유지 | 청킹 설정·통계는 manifest와 EDA에서 관리해 레코드와 Supabase jsonb를 최소화 |
| 전 조합 Supabase Insert 후 평가 | 로컬 Parquet 평가 → 우승 조합만 Insert → 5,000건 용량 gate 후 전체 재개 | 불필요한 DB 사용량과 ANN 벡터 공간 혼합을 피하고 실제 heap·TOAST·HNSW 크기로 전체 적재 가능 여부 판단 |
| chunk 후보 300/500/700 | 문자 수 기준 `300/500/800/1000`, 기본 overlap 10% | 중앙값 365자·장문 판례 혼재 특성을 넓은 간격으로 비교하고 EDA·검색 평가로 최종 선정 |
| OCR 선행 | 비차단 병렬 (Phase A) | 이미지 119건 모두 부모 텍스트 존재, `ocr_status=not_attempted`이나 기존 OCR 결과 없음에도 본문 누락 아님. 표본 OCR로 신규 정보 확인 시에만 증분 반영 |
| 이미지 해상도 분석 Notebook | 표본 다운로드분만 해상도 분석 | `images.jsonl`은 URL 메타데이터뿐 로컬 이미지 파일이 없어 전량 해상도 분석은 전량 다운로드가 선행돼야 함. 개수·유형·URL 유무 분석은 메타데이터로 즉시 가능 |
