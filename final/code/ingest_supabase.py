"""임베딩 결과 Supabase 적재: data/05_vectordb/final → kb_chunks 테이블

embed_chunks.py 가 저장한 임베딩 체크포인트를 읽어
kb_chunks(content, embedding, metadata) 에 그대로 INSERT 한다 (append — 기존 데이터 유지).
id·created_at 은 DB 기본값(BIGSERIAL, now())으로 채워진다. 임베딩 호출 없음.

입력 형식:
    *.json  : [{content, embedding, metadata}, ...] 리스트
    *.jsonl : 한 줄에 {content, embedding, metadata} 하나 (스트리밍 적재)

실행:
    uv run python src/pipe/ingest_supabase.py                      # 폴더 전체 적재
    uv run python src/pipe/ingest_supabase.py <파일명> [<파일명>...]  # 지정 파일만 적재 (재적재·부분 적재용)
"""

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from psycopg.types.json import Json

ROOT = Path(__file__).resolve().parents[2]
load_dotenv(ROOT / ".env")  # DB_URL + vs_method import 시 OPENAI_API_KEY 필요
sys.path.insert(0, str(ROOT))

from src.core.vs_method import EMBEDDING_DIM, ensure_schema, get_conn  # noqa: E402

VECTOR_DIR = ROOT / "data" / "05_vectordb" / "final"

INSERT_BATCH = 500  # executemany 배치 크기 (대용량 jsonl 메모리·왕복 절충)
INSERT_SQL = "INSERT INTO kb_chunks (content, embedding, metadata) VALUES (%s, %s::vector, %s)"

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")  # Windows 콘솔 한글 깨짐 방지


def to_row(rec: dict, src_name: str) -> tuple:
    emb = rec["embedding"]
    if len(emb) != EMBEDDING_DIM:
        raise ValueError(f"{src_name}: 임베딩 차원 {len(emb)} ≠ 테이블 VECTOR({EMBEDDING_DIM})")
    return (rec["content"], str(emb), Json(rec.get("metadata", {})))


def iter_records(path: Path):
    if path.suffix == ".jsonl":
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    yield json.loads(line)
    else:
        yield from json.loads(path.read_text(encoding="utf-8"))


def ingest_file(conn, path: Path) -> int:
    """임베딩 체크포인트 하나를 배치 단위로 kb_chunks 에 INSERT. 적재 건수 반환."""
    total = 0
    rows: list[tuple] = []
    with conn.cursor() as cur:
        for rec in iter_records(path):
            rows.append(to_row(rec, path.name))
            if len(rows) >= INSERT_BATCH:
                cur.executemany(INSERT_SQL, rows)
                total += len(rows)
                rows.clear()
                if total % 5000 < INSERT_BATCH:
                    print(f"  ... {total}건 진행 중")
        if rows:
            cur.executemany(INSERT_SQL, rows)
            total += len(rows)
    conn.commit()  # 파일 단위 커밋 — 중간 실패 시 해당 파일 전체 롤백
    return total


def main() -> None:
    names = sys.argv[1:]
    if names:
        files = [VECTOR_DIR / n for n in names]
        missing = [f.name for f in files if not f.exists()]
        if missing:
            print(f"파일 없음: {missing}")
            return
    else:
        files = sorted(VECTOR_DIR.glob("*.json")) + sorted(VECTOR_DIR.glob("*.jsonl"))
    if not files:
        print(f"입력 파일 없음: {VECTOR_DIR} — 먼저 embed_chunks.py 를 실행하세요.")
        return

    conn = get_conn()
    try:
        ensure_schema(conn)
        total = 0
        for f in files:
            print(f"[..] {f.name}")
            n = ingest_file(conn, f)
            total += n
            print(f"[OK] {f.name}: {n}건 적재")
        print(f"완료: 총 {total}건 kb_chunks 적재 (append)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
