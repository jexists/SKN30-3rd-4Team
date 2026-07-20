#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/mnt/d/project/SKN30-3rd-4Team-dev"
PYTHON_BIN="/home/playdata2/.venvs/legal-rag-v2/bin/python"
INPUT_DIR="$REPO_ROOT/data/legal_api_v2/07_chunking_500_ov50"
LOG_DIR="$REPO_ROOT/pipeline/logs/embedding"
RUN_LOG="$LOG_DIR/full_500_models.log"

mkdir -p "$LOG_DIR"

run_model() {
  local model_id="$1"
  local model_slug="$2"
  local revision="$3"
  local output_dir="$REPO_ROOT/data/legal_api_v2/08_embedding_500_ov50_${model_slug}"
  local revision_args=()
  if [[ -n "$revision" ]]; then
    revision_args=(--revision "$revision")
  fi

  if [[ -f "$output_dir/manifest.json" ]]; then
    printf '%s SKIP completed model=%s output=%s\n' "$(date --iso-8601=seconds)" "$model_id" "$output_dir" >> "$RUN_LOG"
    return
  fi

  printf '%s START model=%s output=%s\n' "$(date --iso-8601=seconds)" "$model_id" "$output_dir" >> "$RUN_LOG"
  PYTHONUNBUFFERED=1 "$PYTHON_BIN" -u "$REPO_ROOT/pipeline/embedding/run_embedding.py" \
    --input-dir "$INPUT_DIR" \
    --output-dir "$output_dir" \
    --model "$model_id" \
    "${revision_args[@]}" \
    --device cpu \
    --batch-size 8 \
    --part-size 512 >> "$RUN_LOG" 2>&1
  printf '%s COMPLETE model=%s\n' "$(date --iso-8601=seconds)" "$model_id" >> "$RUN_LOG"
}

while IFS=$'\t' read -r model_id model_slug revision; do
  run_model "$model_id" "$model_slug" "$revision"
done < <(
  cd "$REPO_ROOT"
  "$PYTHON_BIN" -c \
    'import json
from pathlib import Path
from pipeline.embedding.run_embedding import MODEL_REGISTRY

base = Path("data/legal_api_v2")
rows = []
for order, (model_id, item) in enumerate(MODEL_REGISTRY.items()):
    output = base / f"08_embedding_500_ov50_{item['"'"'slug'"'"']}"
    processed = 0
    for checkpoint in output.glob("_parts/*/checkpoint.json"):
        processed += int(json.loads(checkpoint.read_text(encoding="utf-8")).get("next_line", 0))
    rows.append((processed == 0, -processed, order, model_id, item))
for _, _, _, model_id, item in sorted(rows):
    print(model_id, item["slug"], item.get("revision") or "", sep="\t")'
)

printf '%s ALL_COMPLETE\n' "$(date --iso-8601=seconds)" >> "$RUN_LOG"
