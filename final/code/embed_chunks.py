"""청크 임베딩 파이프라인: data/04_chunks/final → data/05_vectordb/final

data/04_chunks/final 의 청킹 결과를 vs_method 와 동일한 임베딩
(text-embedding-3-small, 1024차원)으로 배치 임베딩하고,
원본과 같은 파일명으로 임베딩 체크포인트를 저장한다. 내부 재청킹 없음 —
이미 청크된 page_content 를 그대로 임베딩한다.
Supabase 적재는 src/pipe/ingest_supabase.py 가 담당한다 (임베딩/적재 분리).

입력 형식 (둘 다 지원):
    *.json  : [{page_content, metadata}, ...] 리스트
    *.jsonl : 한 줄에 레코드 하나. {page_content, metadata} 이거나
              메타 필드가 최상위에 펼쳐진 flat 형태({page_content, source_type, ...})
    본문 키는 page_content / content 둘 다 지원.

출력 형식:
    *.json  → *.json  : [{content, embedding, metadata}, ...]
    *.jsonl → *.jsonl : 한 줄에 {content, embedding, metadata} 하나 (스트리밍 저장)

실행:
    uv run python src/pipe/embed_chunks.py          # data/04_chunks/final 전체, 출력 있으면 스킵
    uv run python src/pipe/embed_chunks.py --force  # 전부 다시 임베딩
    # 파일 하나만 지정 → 그 파일을 임베딩해 data/05_vectordb/final 에 같은 이름으로 저장
    uv run python src/pipe/embed_chunks.py data/04_chunks/first/kb_chunks_eflaw.jsonl
"""

import json
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")  # vs_method import 시 OpenAIEmbeddings 초기화에 OPENAI_API_KEY 필요
sys.path.insert(0, str(ROOT))

from src.core.vs_method import EMBEDDING_DIM, EMBEDDING_MODEL, embeddings  # noqa: E402

CHUNKS_DIR = ROOT / "data" / "04_chunks" / "final"
VECTOR_DIR = ROOT / "data" / "05_vectordb" / "final"

EMBED_BATCH = 100  # 요청당 텍스트 수 — 요청당 토큰 한도(300k) 회피
EMBED_MAX_RETRY = 5

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 한글 깨짐 방지


def normalize_record(rec: dict) -> tuple[str, dict]:
    """레코드를 (content, metadata) 로 정규화.

    {page_content, metadata} 형태면 metadata 를 그대로 쓰고,
    flat 형태면 본문 키를 제외한 나머지 키 전부를 metadata 로 삼는다.
    본문 키는 page_content / content 둘 다 지원.
    """
    content = rec.get("page_content") or rec.get("content") or ""
    if "metadata" in rec:
        meta = rec.get("metadata") or {}
    else:
        meta = {k: v for k, v in rec.items() if k not in ("page_content", "content")}
    return content, meta


def embed_batch(texts: list[str]) -> list[list[float]]:
    """배치 임베딩 + 지수 백오프 재시도 (429·일시 오류 대비)."""
    for attempt in range(EMBED_MAX_RETRY):
        try:
            return embeddings.embed_documents(texts)
        except Exception as e:  # noqa: BLE001
            if attempt == EMBED_MAX_RETRY - 1:
                raise
            wait = 2**attempt
            print(f"  !! 임베딩 실패({e}) — {wait}초 후 재시도 {attempt + 1}/{EMBED_MAX_RETRY}")
            time.sleep(wait)
    raise RuntimeError("unreachable")


def embed_json(src_path: Path, out_path: Path) -> int:
    """*.json 파일 하나를 임베딩해 [{content, embedding, metadata}] JSON 으로 저장."""
    chunks = json.loads(src_path.read_text(encoding="utf-8"))
    norm = [normalize_record(c) for c in chunks]
    norm = [(c, m) for c, m in norm if c.strip()]
    if not norm:
        return 0

    records = []
    for i in range(0, len(norm), EMBED_BATCH):
        batch = norm[i : i + EMBED_BATCH]
        vectors = embed_batch([c for c, _ in batch])
        records += [
            {"content": c, "embedding": v, "metadata": m} for (c, m), v in zip(batch, vectors)
        ]
    out_path.write_text(json.dumps(records, ensure_ascii=False), encoding="utf-8")
    return len(records)


def embed_jsonl(src_path: Path, out_path: Path) -> int:
    """*.jsonl 파일 하나를 배치 단위로 임베딩하며 스트리밍 저장 (대용량 대비).

    중단 시 완성된 것처럼 보이는 파일이 남지 않도록 .tmp 에 쓰고 마지막에 rename 한다.
    """
    tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
    total = 0
    with src_path.open(encoding="utf-8") as fin, tmp_path.open("w", encoding="utf-8") as fout:
        batch: list[tuple[str, dict]] = []

        def flush():
            nonlocal total
            if not batch:
                return
            vectors = embed_batch([c for c, _ in batch])
            for (c, m), v in zip(batch, vectors):
                fout.write(
                    json.dumps({"content": c, "embedding": v, "metadata": m}, ensure_ascii=False)
                    + "\n"
                )
            total += len(batch)
            batch.clear()
            if total % 2000 < EMBED_BATCH:
                print(f"  ... {total}개 진행 중")

        for line in fin:
            line = line.strip()
            if not line:
                continue
            content, meta = normalize_record(json.loads(line))
            if content.strip():
                batch.append((content, meta))
            if len(batch) >= EMBED_BATCH:
                flush()
        flush()
    tmp_path.replace(out_path)
    return total


def main() -> None:
    args = sys.argv[1:]
    force = "--force" in args
    given = [a for a in args if not a.startswith("--")]  # 지정 파일 경로들
    VECTOR_DIR.mkdir(parents=True, exist_ok=True)

    if given:
        # 파일 하나(이상) 지정 → 그 파일만 임베딩. 출력은 항상 VECTOR_DIR/같은이름.
        files = [Path(p) if Path(p).is_absolute() else ROOT / p for p in given]
        missing = [f for f in files if not f.exists()]
        if missing:
            print("입력 파일 없음: " + ", ".join(str(m) for m in missing))
            return
    else:
        files = sorted(CHUNKS_DIR.glob("*.json")) + sorted(CHUNKS_DIR.glob("*.jsonl"))
        if not files:
            print(f"입력 파일 없음: {CHUNKS_DIR}")
            return

    print(f"임베딩 모델: {EMBEDDING_MODEL} ({EMBEDDING_DIM}차원) — 파일 {len(files)}개")
    total = 0
    for f in files:
        out = VECTOR_DIR / f.name
        if out.exists() and not force:
            print(f"[skip] {f.name} (이미 있음 — --force 로 재생성)")
            continue
        print(f"[..] {f.name}")
        n = embed_jsonl(f, out) if f.suffix == ".jsonl" else embed_json(f, out)
        total += n
        print(f"[OK] {f.name}: {n}개 청크 임베딩 → {out.relative_to(ROOT)}")
    print(f"완료: 총 {total}개 청크 임베딩")


if __name__ == "__main__":
    main()
