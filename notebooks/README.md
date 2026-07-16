# notebooks/

실험·탐색용 Jupyter 노트북 공간.
검증 후 `src/`로 이동한다.

## 법률 API 파이프라인

아래 순서로 실행한다. 원본이 이미 있으면 1번은 다시 실행하지 않아도 된다.

1. `01_load_api.ipynb` - 법령·판례·해석례 원본 수집과 검증
2. `02_preprocess_legal_api.ipynb` - 텍스트 정제와 metadata 생성
3. `03_build_kb_chunks.ipynb` - 법률 구조 기반 청킹
4. `src/pipe/embed_chunks.py` - 임베딩 생성
5. `src/pipe/ingest_supabase.py` - Supabase 적재

`04_chunking.ipynb`는 과거 청크 복구 실험용이며 위 정식 흐름에는 사용하지 않는다.

- 01_load — 원본 문서 로드 (PDF, HTML 등)
- 02_preprocess — 텍스트 정제·정규화
- 03_chunk — 청킹 전략 실험
- 04_embed — 임베딩 및 벡터스토어 구축
- ... 등
