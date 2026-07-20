# Legal RAG v2 pipeline

기존 `src/`, `eval/`, `notebooks/`와 원본 `05_*`는 수정하지 않는 독립 파이프라인이다. 모든 실행은 repository root에서 한다.

`06_final`, `07_chunking_*`, `08_embedding_*`, OCR 표본과 실행 로그는 원본 `05_processed_*`와 코드로 재생성 가능한 대용량 산출물이므로 Git에서 제외한다. 집계·검수용 `reports/legal_api_v2`는 포함할 수 있다.

## 현재 생성·검증 결과

| 단계 | 결과 |
|---|---:|
| `06_final` | 입력 9,135 → 최종 8,259, 제외 876, 중복 0 |
| 이미지 | 119건 모두 원본 부모 텍스트 있음, 기준선 OCR 제외 |
| `300/30` | 95,350 chunks |
| `500/50` | 59,987 chunks |
| `800/80` | 39,226 chunks |
| `1000/100` | 32,215 chunks |

모든 청킹 후보는 원본 8,259건 연결, 빈 청크 0, 중복 `chunk_id` 0을 manifest로 검증했다.

## 환경 준비

루트 가상환경과 분리한다.

```powershell
uv venv pipeline/.venv
uv pip install --python pipeline/.venv/Scripts/python.exe -r pipeline/requirements.txt
```

Windows 호스트에서는 조직의 애플리케이션 제어 정책이 `torch_python.dll`과 `pyarrow` DLL을 차단한다(WinError 4551). WSL2 Ubuntu 24.04에서는 정상 실행되며, 아래 ext4 가상환경으로 전체 테스트 70건과 세 모델 smoke를 완료했다.

```bash
sudo apt-get update
sudo apt-get install -y python3.12-venv
python3 -m venv /home/playdata2/.venvs/legal-rag-v2
/home/playdata2/.venvs/legal-rag-v2/bin/pip install \
  --index-url https://download.pytorch.org/whl/cpu torch
/home/playdata2/.venvs/legal-rag-v2/bin/pip install \
  -r /mnt/d/project/SKN30-3rd-4Team-dev/pipeline/requirements.txt
```

가상환경은 NTFS의 `pipeline/.venv-wsl`이 아니라 WSL ext4에 둔다. 패키지 설치와 import가 훨씬 빠르며, 산출물만 repository의 `data/`에 저장한다.

tokenizer truncation을 끄고 계산한 결과 세 모델 모두 원문 8,259건 중 266건이 8,192토큰을 초과하며 최대 199,491토큰이다. 원문을 그대로 임베딩할 수 없으므로 청킹은 필수다.

OCR 표본은 전량 다운로드 대신 판례 5건·법령 별표 5건만 확보했다. `tesseract.js 7.0.0`의 한국어+영어 WebAssembly OCR은 10/10 성공했고 평균 confidence 81.4, 평균 1.36초, 전량 113건 예상 약 154초였다. 정규화 비교와 육안 검토 결과 부모 텍스트에 같은 핵심 쟁점·금액·표 내용이 있거나 빈 양식이어서 새 검색 가치는 0건이었다. 따라서 기준선에는 OCR 레코드를 추가하지 않으며, 상세 근거는 `data/legal_api_v2/05_ocr_json/ocr_manifest.json`과 `reports/legal_api_v2/ocr_assessment`에 있다.

## 1. 최종 corpus와 EDA

```powershell
pipeline/.venv/Scripts/python.exe pipeline/preprocess/build_final_corpus.py
pipeline/.venv/Scripts/python.exe pipeline/analysis/prechunk_eda.py
pipeline/.venv/Scripts/python.exe pipeline/analysis/ocr_assessment.py
```

OCR 표본 재현 명령:

```powershell
pipeline/.venv/Scripts/python.exe pipeline/analysis/ocr_assessment.py --download-samples
Set-Location pipeline/ocr
npm install
npm run sample
Set-Location ../..
python -m pipeline.analysis.evaluate_ocr_results
```

`06_final`이 이미 있으면 빌더는 덮어쓰지 않는다. 재생성은 산출물을 별도로 보관한 뒤 명시적으로 `--force`를 사용한다.

## 2. 청킹

```powershell
pipeline/.venv/Scripts/python.exe pipeline/chunking/run_chunking.py --chunk-size 500 --chunk-overlap 50
```

나머지 후보도 `300/30`, `800/80`, `1000/100`으로 실행한다. 같은 입력 hash와 설정의 완성 산출물은 skip하며, hash가 바뀌면 실패한다.

## 3. 임베딩

먼저 후보별·모델별 100건 smoke test를 실행한다.

```powershell
pipeline/.venv/Scripts/python.exe pipeline/embedding/run_embedding.py `
  --input-dir data/legal_api_v2/07_chunking_500_ov50 `
  --model kakao1513/KURE-legal-ft-v1 --device cpu --batch-size 8 --limit 100
```

모델 ID는 다음 세 개다.

- `kakao1513/KURE-legal-ft-v1`
- `nlpai-lab/KURE-v1`
- `BAAI/bge-m3`

smoke 폴더는 `_smoke100` 접미사로 본 실행과 분리된다. 세 모델 모두 100행·1024차원·NaN 0·평균 norm 1.0·동일 chunk ID로 검증됐다. 최초 모델 로드를 포함한 CPU 시간은 KURE-v1 162초, KURE-legal-ft-v1 173초, BGE-M3 219초였다.

전체 `500/50 × 3모델` 순차 실행:

```bash
cd /mnt/d/project/SKN30-3rd-4Team-dev
bash pipeline/embedding/run_full_500_models.sh
```

모델 내부 batch는 8, 디스크 checkpoint는 512건 단위다. 출력은 Parquet, part와 `next_line`은 각 출력 폴더의 `_parts`, 진행·오류는 `pipeline/logs/embedding`에 남는다. 중단 후 같은 명령을 실행하면 입력 hash와 모델이 같은 완료 part 다음부터 재개한다.
`--limit` smoke는 출력 폴더뿐 아니라 experiment ID에도 `_smoke100` 같은 suffix를
붙여 정식 평가·Supabase experiment와 충돌하지 않게 한다.

각 실행은 모델 로드 전에 `preflight.json`을 만든다. 장치 가용성과 CPU 수를 기록하고,
디스크 예상치는 기존 checkpoint가 있으면 실측 바이트/행, 없으면 현재 전체 실행
실측치(약 9,744바이트/행)를 올림한 행당 10,000바이트를
사용한다. 남은 산출물에 1.25 안전계수와 1GiB 운영 여유를 더한 값보다 실제 여유
공간이 작으면 lock을 해제하고 실행을 중단한다.

다른 터미널에서 모델별 완료·남은 건수를 확인한다.

```bash
/home/playdata2/.venvs/legal-rag-v2/bin/python pipeline/embedding/check_progress.py
tail -f pipeline/logs/embedding/full_500_models.log
```

## 4. 로컬 평가

`pipeline/evaluation/questions.jsonl`에는 자료형·쟁점별 질문 20개와 원문에서 확인한 gold 참조 26건(고유 25건)이 있다. `pending_team_review` 상태를 팀이 최종 확인한 뒤 확정 평가를 실행한다.

```bash
$PY pipeline/evaluation/validate_questions.py
$PY pipeline/evaluation/validate_gold_chunks.py
```

두 검증기는 gold 원문 존재·문항 정의 오류뿐 아니라 300/500/800/1000 네 후보 청킹 결과에 각 gold 레코드의 청크가 실제로 존재하는지도 확인한다.

```powershell
pipeline/.venv/Scripts/python.exe pipeline/evaluation/validate_questions.py
pipeline/.venv/Scripts/python.exe pipeline/evaluation/retrieval_eval.py `
  --input-dir data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1 `
  --top-k 10 --device cpu
```

모든 조합을 평가한 뒤 `reports/legal_api_v2/eval/leaderboard.csv`에서 `Recall@5`, `nDCG@10`, p95 지연시간 순으로 우승 조합을 고른다.
각 실험의 `metrics_by_group.csv`에는 전체 순위와 별도로 쟁점별·자료형별 Recall/MRR/nDCG와 중앙 지연시간을 기록한다. `per_query_results.csv`와 experiment summary에는 top-1 similarity 및 첫 gold의 score/rank·분위수를 남겨 우승 로컬 모델에 맞는 앱의 강/약 관련성 임계값을 정할 수 있게 한다.

완료된 모든 비-smoke 조합을 일괄 평가하고, 팀이 `review_status=approved`로 확정한 뒤 우승 조합을 기록한다.

```powershell
pipeline/.venv/Scripts/python.exe pipeline/evaluation/evaluate_all.py --require-reviewed
pipeline/.venv/Scripts/python.exe pipeline/evaluation/select_winner.py
```

검수 전 비교가 필요하면 `select_winner.py --allow-pending-review`로 provisional 결과만 만들 수 있다.

팀 검수 후 `questions.jsonl`의 모든 `review_status`가 `approved`가 되면 다음 한
명령이 완료된 모든 조합을 다시 평가하고 비-provisional 우승·앱 임계값을 확정한다.
앱 환경설정은 아직 만들지 않으며, 우승 조합의 Supabase 적재와 검색 smoke까지
통과해야 다음 단계에서 생성된다.

```bash
bash pipeline/evaluation/finalize_after_review.sh
```

팀 검수에는 `validate_questions.py`가 생성하는
`reports/legal_api_v2/eval/question_audit/gold_record_review.md`를 사용한다. 질문별
gold 원문 미리보기와 체크박스가 들어 있지만, 이 Markdown은 보조 문서이므로 실제
승인은 검토 후 `questions.jsonl`의 `review_status`를 변경해 기록한다.

전체 임베딩과 함께 `pipeline/evaluation/run_after_500_embeddings.sh`를 실행하면 각 모델 manifest 생성을 기다렸다가 `500/50` 평가를 순차 실행한다. 질문의 `pending_team_review` 상태가 확정되기 전 결과는 provisional이다.

`pipeline/embedding/run_grid_after_500.sh`는 세 모델의 500/50 평가가 모두 생길 때까지 기다린 뒤 `select_shortlist.py`로 모델 1~2개를 고르고, shortlist의 300/800/1000 임베딩과 평가를 순차 실행한다. 완료 여부는 `finalize_grid.py`가 모든 manifest·평가 summary를 확인한 후 `reports/legal_api_v2/eval/grid_complete.json`으로 증명한다.

같은 실행기는 provisional winner의 `per_query_results.csv`에서 앱 임계값 후보도
`reports/legal_api_v2/eval/app_thresholds.json`에 만든다. `GRADE_WEAK`은 첫 gold
similarity p10, `GRADE_STRONG`은 Hit@1 성공 질문의 top-1 similarity p25이다.
검수 전 산출물은 `apply_to_app=false`이며, 질문 승인 후
`calibrate_app_thresholds.py`를 플래그 없이 다시 실행한 결과만 앱에 반영한다.

Windows 재부팅은 감시기까지 종료하므로 자동 재개되지 않지만, 실행 중 WSL VM만 재시작되는 경우에는 Windows 감시기가 checkpoint에서 임베딩·평가 실행기를 다시 띄운다. `500×3` 평가 후에는 Recall@5와 nDCG@10 차이가 각각 0.05 이내면 상위 2개, 아니면 1개 모델을 shortlist로 삼아 300/800/1000 grid를 자동 실행한다. provisional grid 검증까지 끝나면 감시기는 자동 종료한다.

```powershell
Start-Process -WindowStyle Hidden powershell.exe -ArgumentList @(
  '-NoProfile', '-ExecutionPolicy', 'Bypass',
  '-File', (Resolve-Path 'pipeline/embedding/watch_full_500.ps1')
)
```

감시 이벤트는 `pipeline/logs/embedding/windows_watch_full_500.log`, 현재 PID는 같은 폴더의 `.pid` 파일에서 확인한다. 중복 임베딩 쓰기는 출력 폴더 lock이 차단한다.
감시기는 기본 60초마다 runner 생존을 확인하고, I/O를 줄이기 위해 상태 보고서는
기본 600초마다 manifest 계약을 재검사한 뒤 갱신한다. `-StatusIntervalSeconds`로
보고서 주기만 따로 바꿀 수 있다.

전체 단계의 로컬 증거는 다음 명령으로
`reports/legal_api_v2/status/pipeline_status.{json,md}`에 갱신한다. smoke manifest는
정식 완료로 세지 않고, 진행 중 임베딩은 파일별 checkpoint의 `next_line` 합계로
계산한다.

현재 `pipeline/config/execution_scope.json`은 사용자 결정에 따라 `local_only`다.
따라서 상태의 `complete`는 로컬 임베딩·평가·최종 조합 확정을 기준으로 하며,
Supabase 포함 전체 상태는 `full_pipeline_complete`에 별도로 남는다. Supabase 실행
코드와 미완료 gate는 삭제하지 않아 추후 범위를 `full`로 되돌려 재개할 수 있다.

```bash
$PY pipeline/status_report.py
```

핵심 manifest는 시작·종료·duration·git commit·Python/platform/package 버전과 입력
hash 계약을 검사한다. 초기 생성분은 JSONL/Parquet을 건드리지 않고 hash chain과
실행 메타데이터만 `post_run_backfill`로 명시해 보강했다. OCR은 Python pipeline이
아닌 tesseract.js 실행이므로 engine/version과 원본 이미지 hash 전용 계약을 쓴다.

```bash
$PY pipeline/validate_manifests.py
$PY pipeline/audit_metadata.py
```

`audit_metadata.py`는 `06_final`과 네 청킹 폴더를 전수 읽어 최상위 스키마,
metadata allowlist·필수값, ID 중복, 청킹 전후 metadata 동일성을 검사한다. 결과는
`reports/legal_api_v2/metadata_audit.json`에 저장되며 이 검증도 최종 완료 gate에 포함된다.

## 5. Supabase 우승 조합 적재

`pipeline/supabase/schema.sql`은 2026-07-20에 적용했다. 현재 연결된 DB에는 적용 전부터 `public.kb_chunks`가 없었고, 스크립트는 그 이름의 객체를 생성·변경하지 않았다. v2 테이블은 0행이며 HNSW와 격리 검색 함수가 준비된 상태다. 읽기 전용 상태 보고서는 `reports/legal_api_v2/ingest/schema_status.json`에 저장된다. 새 환경에서는 다음 명령으로 같은 스키마를 적용·확인한다.

```bash
cd /mnt/d/project/SKN30-3rd-4Team-dev
PY=/home/playdata2/.venvs/legal-rag-v2/bin/python
$PY pipeline/supabase/apply_schema.py --dry-run
$PY pipeline/supabase/apply_schema.py
$PY pipeline/supabase/check_schema.py
```

```bash
# 기존 repository .env의 DB_URL을 자동 로드한다.
$PY pipeline/supabase/run_insert.py \
  --input-dir data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1 \
  --experiment-id legalv2_20260720_cs500_ov50_kure-legal-ft-v1 \
  --batch-size 500 --dry-run

$PY pipeline/supabase/run_insert.py \
  --input-dir data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1 \
  --experiment-id legalv2_20260720_cs500_ov50_kure-legal-ft-v1 \
  --batch-size 500 --limit 5000 --db-capacity-bytes <프로젝트_DB_총허용량_바이트> --resume
```

로컬 Parquet만 검사하고 DB 읽기 preflight도 생략하려면 dry-run에 `--skip-db-preflight`를 함께 사용한다.

`--limit 5000`은 재실행마다 5,000건을 더 넣는 값이 아니라 해당 experiment 전체의 상한이다. 총 DB 허용량은 Supabase 프로젝트에서 확인한 값을 명시한다. gate는 `ON CONFLICT` 이후 실제 신규 삽입 행으로 행당 크기를 계산하고 예상 잔여 크기에 기본 1.25 안전계수를 적용한다. 전체 재개 직전에는 현재 DB 크기와 대상 experiment의 표본 5,000행 존재 여부로 같은 gate를 다시 확인한다. 따라서 다른 DB에 로컬 ledger만 잘못 재사용해 표본 행을 건너뛰지 않는다. 통과한 경우에만 `--limit`을 빼고 `--resume`한다. 재개 기준은 `pipeline/logs/insert/ingest_state.sqlite3`이며 성공/실패/전체 이벤트 로그가 분리된다. 적재 후 `check_insert.py`로 로컬 행 수·고유 ID·본문·metadata와 표본 벡터 값을 대조한다.

`--dry-run`의 DB preflight는 읽기 전용이며 experiment 행을 만들지 않는다. 실제 적재 전에는 테이블 필수 컬럼, `vector(1024)`, 검색 함수, 기존 experiment의 데이터 hash·모델 ID·revision 일치 여부를 확인한다.

적재와 reconciliation이 통과한 뒤 같은 모델 revision으로 Supabase 검색 RPC를 확인한다.

```bash
$PY pipeline/supabase/search_smoke.py \
  --input-dir data/legal_api_v2/08_embedding_500_ov50_kure-legal-ft-v1 \
  --experiment-id legalv2_20260720_cs500_ov50_kure-legal-ft-v1 --top-k 10
```

승인된 winner와 grid가 준비된 뒤에는 아래 실행기로 dry-run부터 검색 smoke와
`reports/legal_api_v2/eval/app_v2.env` 생성까지
순서를 강제할 수 있다. `LEGAL_RAG_V2_DB_CAPACITY_BYTES`에는 Supabase 프로젝트에서
확인한 **전체 DB 허용량(바이트)**을 반드시 직접 넣어야 한다. 값이 없거나 숫자가
아니거나 5,000건 gate가 실패하면 전체 적재를 시작하지 않는다. 재실행 시 같은
experiment와 SQLite ledger에서 이어진다.

```bash
export LEGAL_RAG_V2_DB_CAPACITY_BYTES=<프로젝트_DB_총허용량_바이트>
bash pipeline/supabase/run_winner_ingest.sh
```

앱용 읽기 전용 어댑터는 기존 `src/core/vs_method.py`를 수정하지 않고
`pipeline/app/vs_method_v2.py`로 분리했다. 최종 우승 조합이 적재되고 검색 smoke를
통과한 뒤 실행기가 생성한 `app_v2.env` 값을 실제 `.env`에 반영한다.
어댑터는 `kb_chunks_v2`를 직접 변경하지 않고 `match_kb_chunks_v2` RPC만 호출하며,
모델 revision과 1024차원 계약을 시작 전에 검증한다. 우승 전에는 기존 graph에
연결하거나 예시 값을 운영 기본값으로 사용하지 않는다.

승인된 `app_v2.env` 값을 실제 환경에 반영한 뒤 별도 진입점
`uv run streamlit run pipeline/app/main_v2.py`로 앱을 재시작한다. 이 진입점은
원본 graph·backend·UI 파일을 수정하지 않고 v2 검색 모듈을 먼저 주입한다.
v2의 `ensure_schema` 호환 함수는 DDL을 실행하지 않고 테이블·RPC·우승 experiment·
청크 존재만 읽기 전용으로 확인한다. 실행 중 backend를 변경하지 말고 반드시
Streamlit 프로세스를 재시작한다. 로컬 모델 의존성은 기존 root `pyproject.toml`을
바꾸지 않고 같은 실행 환경에 `pipeline/requirements.txt`를 추가 설치한다.

## 테스트

```bash
cd /mnt/d/project/SKN30-3rd-4Team-dev
PY=/home/playdata2/.venvs/legal-rag-v2/bin/python
$PY -m unittest discover -s pipeline/tests -v
$PY -m compileall -q pipeline
```
