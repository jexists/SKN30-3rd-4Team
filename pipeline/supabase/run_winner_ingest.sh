#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/mnt/d/project/SKN30-3rd-4Team-dev"
PYTHON_BIN="/home/playdata2/.venvs/legal-rag-v2/bin/python"
WINNER="$REPO_ROOT/reports/legal_api_v2/eval/winner.json"
GRID_COMPLETE="$REPO_ROOT/reports/legal_api_v2/eval/grid_complete.json"
LOG_DIR="$REPO_ROOT/pipeline/logs/insert"
RUN_LOG="$LOG_DIR/winner_ingest.log"
BATCH_SIZE="${LEGAL_RAG_V2_INSERT_BATCH_SIZE:-500}"
DB_CAPACITY_BYTES="${LEGAL_RAG_V2_DB_CAPACITY_BYTES:-}"

mkdir -p "$LOG_DIR"
cd "$REPO_ROOT"

if [[ ! "$BATCH_SIZE" =~ ^[1-9][0-9]*$ ]]; then
  printf 'LEGAL_RAG_V2_INSERT_BATCH_SIZE must be a positive integer\n' >&2
  exit 2
fi
if [[ ! "$DB_CAPACITY_BYTES" =~ ^[1-9][0-9]*$ ]]; then
  printf 'LEGAL_RAG_V2_DB_CAPACITY_BYTES must be the project total DB quota in bytes\n' >&2
  exit 2
fi

readarray -t winner_fields < <(
  "$PYTHON_BIN" - "$WINNER" "$GRID_COMPLETE" <<'PY'
import json
import sys
from pathlib import Path

winner_path, grid_path = map(Path, sys.argv[1:])
winner = json.loads(winner_path.read_text(encoding="utf-8"))
grid = json.loads(grid_path.read_text(encoding="utf-8"))
if winner.get("provisional") or grid.get("provisional"):
    raise SystemExit("approved non-provisional winner and grid_complete are required")
row = winner.get("winner") or {}
experiment_id = str(row.get("experiment_id") or "")
input_dir = str(row.get("input_dir") or "")
if not experiment_id or not input_dir:
    raise SystemExit("winner experiment_id/input_dir missing")
if not Path(input_dir).is_absolute():
    input_dir = str(Path("/mnt/d/project/SKN30-3rd-4Team-dev") / input_dir)
manifest = json.loads((Path(input_dir) / "manifest.json").read_text(encoding="utf-8"))
if manifest.get("experiment_id") != experiment_id:
    raise SystemExit("winner and manifest experiment_id mismatch")
print(experiment_id)
print(input_dir)
PY
)

if [[ ${#winner_fields[@]} -ne 2 ]]; then
  printf 'winner resolution failed\n' >&2
  exit 2
fi
EXPERIMENT_ID="${winner_fields[0]}"
INPUT_DIR="${winner_fields[1]}"
GATE_REPORT="$REPO_ROOT/reports/legal_api_v2/ingest/${EXPERIMENT_ID}_capacity_gate.json"

printf '%s START experiment=%s input=%s\n' "$(date --iso-8601=seconds)" "$EXPERIMENT_ID" "$INPUT_DIR" >> "$RUN_LOG"

"$PYTHON_BIN" pipeline/validate_manifests.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/audit_metadata.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/supabase/check_schema.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/supabase/run_insert.py \
  --input-dir "$INPUT_DIR" \
  --experiment-id "$EXPERIMENT_ID" \
  --batch-size "$BATCH_SIZE" \
  --dry-run >> "$RUN_LOG" 2>&1

"$PYTHON_BIN" pipeline/supabase/run_insert.py \
  --input-dir "$INPUT_DIR" \
  --experiment-id "$EXPERIMENT_ID" \
  --batch-size "$BATCH_SIZE" \
  --limit 5000 \
  --db-capacity-bytes "$DB_CAPACITY_BYTES" \
  --resume >> "$RUN_LOG" 2>&1

"$PYTHON_BIN" - "$GATE_REPORT" <<'PY' >> "$RUN_LOG" 2>&1
import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
gate = report.get("capacity_gate") or {}
if not gate.get("passed"):
    raise SystemExit("capacity gate failed; full ingest is stopped")
print(json.dumps(gate, ensure_ascii=False, indent=2))
PY

"$PYTHON_BIN" pipeline/supabase/run_insert.py \
  --input-dir "$INPUT_DIR" \
  --experiment-id "$EXPERIMENT_ID" \
  --batch-size "$BATCH_SIZE" \
  --resume >> "$RUN_LOG" 2>&1

"$PYTHON_BIN" pipeline/supabase/check_insert.py \
  --input-dir "$INPUT_DIR" \
  --experiment-id "$EXPERIMENT_ID" >> "$RUN_LOG" 2>&1

"$PYTHON_BIN" pipeline/supabase/search_smoke.py \
  --input-dir "$INPUT_DIR" \
  --experiment-id "$EXPERIMENT_ID" \
  --top-k 10 --device cpu >> "$RUN_LOG" 2>&1

"$PYTHON_BIN" pipeline/evaluation/generate_app_env.py --device cpu >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/supabase/check_schema.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/status_report.py >> "$RUN_LOG" 2>&1
printf '%s ALL_COMPLETE experiment=%s\n' "$(date --iso-8601=seconds)" "$EXPERIMENT_ID" >> "$RUN_LOG"
