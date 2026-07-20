#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/mnt/d/project/SKN30-3rd-4Team-dev"
PYTHON_BIN="/home/playdata2/.venvs/legal-rag-v2/bin/python"
LOG_DIR="$REPO_ROOT/pipeline/logs/embedding"
RUN_LOG="$LOG_DIR/grid_after_500.log"
SHORTLIST="$REPO_ROOT/reports/legal_api_v2/eval/shortlist_500.json"

mkdir -p "$LOG_DIR"

expected_500_evaluations() {
  cd "$REPO_ROOT"
  "$PYTHON_BIN" -c 'from pipeline.embedding.run_embedding import MODEL_REGISTRY; print(len(MODEL_REGISTRY))'
}

completed_500_evaluations() {
  find "$REPO_ROOT/reports/legal_api_v2/eval" -mindepth 2 -maxdepth 2 \
    -name experiment_summary.csv -path '*cs500_ov50*' -print 2>/dev/null | wc -l
}

expected=$(expected_500_evaluations)
while [[ $(completed_500_evaluations) -lt $expected ]]; do
  sleep 60
done

"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/validate_questions.py" >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/validate_gold_chunks.py" >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/select_shortlist.py" >> "$RUN_LOG" 2>&1

while IFS=$'\t' read -r model_id slug revision; do
  for size in 300 800 1000; do
    overlap=$((size / 10))
    input_dir="$REPO_ROOT/data/legal_api_v2/07_chunking_${size}_ov${overlap}"
    output_dir="$REPO_ROOT/data/legal_api_v2/08_embedding_${size}_ov${overlap}_${slug}"
    manifest="$output_dir/manifest.json"
    if [[ ! -f "$manifest" ]]; then
      printf '%s START model=%s size=%s overlap=%s\n' "$(date --iso-8601=seconds)" "$model_id" "$size" "$overlap" >> "$RUN_LOG"
      PYTHONUNBUFFERED=1 "$PYTHON_BIN" -u "$REPO_ROOT/pipeline/embedding/run_embedding.py" \
        --input-dir "$input_dir" \
        --output-dir "$output_dir" \
        --model "$model_id" \
        --revision "$revision" \
        --device cpu >> "$RUN_LOG" 2>&1
      printf '%s COMPLETE model=%s size=%s overlap=%s\n' "$(date --iso-8601=seconds)" "$model_id" "$size" "$overlap" >> "$RUN_LOG"
    else
      printf '%s SKIP manifest=%s\n' "$(date --iso-8601=seconds)" "$manifest" >> "$RUN_LOG"
    fi

    experiment_id=$(
      "$PYTHON_BIN" -c 'import json,sys; print(json.load(open(sys.argv[1], encoding="utf-8"))["experiment_id"])' "$manifest"
    )
    summary="$REPO_ROOT/reports/legal_api_v2/eval/$experiment_id/experiment_summary.csv"
    if [[ ! -f "$summary" ]]; then
      "$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/retrieval_eval.py" \
        --input-dir "$output_dir" --embedding-model "$model_id" --top-k 10 --device cpu >> "$RUN_LOG" 2>&1
    fi
  done
done < <(
  "$PYTHON_BIN" -c 'import json,sys
value=json.load(open(sys.argv[1], encoding="utf-8"))
for item in value["selected_models"]:
    print(item["model_id"], item["slug"], item["revision"], sep="\t")' "$SHORTLIST"
)

"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/select_winner.py" --allow-pending-review >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/calibrate_app_thresholds.py" --allow-pending-review >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" "$REPO_ROOT/pipeline/validate_manifests.py" >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" "$REPO_ROOT/pipeline/audit_metadata.py" >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" "$REPO_ROOT/pipeline/evaluation/finalize_grid.py" >> "$RUN_LOG" 2>&1
printf '%s ALL_COMPLETE provisional_grid\n' "$(date --iso-8601=seconds)" >> "$RUN_LOG"
