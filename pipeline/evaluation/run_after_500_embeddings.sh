#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/mnt/d/project/SKN30-3rd-4Team-dev"
PYTHON_BIN="/home/playdata2/.venvs/legal-rag-v2/bin/python"
LOG_DIR="$REPO_ROOT/pipeline/logs/evaluation"
RUN_LOG="$LOG_DIR/after_500_embeddings.log"

mkdir -p "$LOG_DIR"

wait_and_evaluate() {
  local slug="$1"
  local model_id="$2"
  local input_dir="$REPO_ROOT/data/legal_api_v2/08_embedding_500_ov50_${slug}"
  local manifest="$input_dir/manifest.json"
  local state="$input_dir/run_state.json"

  while [[ ! -f "$manifest" ]]; do
    if [[ -f "$state" ]] && grep -q '"status": "failed"' "$state"; then
      printf '%s ABORT failed_embedding=%s\n' "$(date --iso-8601=seconds)" "$model_id" >> "$RUN_LOG"
      return 1
    fi
    sleep 60
  done

  local experiment_id
  experiment_id=$("$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["experiment_id"])' "$manifest")
  local summary="$REPO_ROOT/reports/legal_api_v2/eval/$experiment_id/experiment_summary.csv"

  if [[ -f "$summary" ]]; then
    printf '%s SKIP existing_eval=%s\n' "$(date --iso-8601=seconds)" "$model_id" >> "$RUN_LOG"
    return
  fi

  "$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/validate_questions.py" >> "$RUN_LOG" 2>&1
  "$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/validate_gold_chunks.py" >> "$RUN_LOG" 2>&1
  printf '%s START eval=%s\n' "$(date --iso-8601=seconds)" "$model_id" >> "$RUN_LOG"
  PYTHONUNBUFFERED=1 "$PYTHON_BIN" -u "$REPO_ROOT/pipeline/evaluation/retrieval_eval.py" \
    --input-dir "$input_dir" \
    --embedding-model "$model_id" \
    --top-k 10 \
    --device cpu >> "$RUN_LOG" 2>&1
  printf '%s COMPLETE eval=%s\n' "$(date --iso-8601=seconds)" "$model_id" >> "$RUN_LOG"
}

"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/validate_questions.py" >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/validate_gold_chunks.py" >> "$RUN_LOG" 2>&1
while IFS=$'\t' read -r model_id model_slug; do
  wait_and_evaluate "$model_slug" "$model_id"
done < <(
  cd "$REPO_ROOT"
  "$PYTHON_BIN" -c \
    'from pipeline.embedding.run_embedding import MODEL_REGISTRY
for model_id, item in MODEL_REGISTRY.items():
    print(model_id, item["slug"], sep="\t")'
)

printf '%s ALL_COMPLETE provisional_500_eval\n' "$(date --iso-8601=seconds)" >> "$RUN_LOG"
