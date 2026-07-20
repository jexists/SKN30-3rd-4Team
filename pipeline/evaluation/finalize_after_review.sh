#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="/mnt/d/project/SKN30-3rd-4Team-dev"
PYTHON_BIN="/home/playdata2/.venvs/legal-rag-v2/bin/python"
LOG_DIR="$REPO_ROOT/pipeline/logs/evaluation"
RUN_LOG="$LOG_DIR/finalize_after_review.log"

mkdir -p "$LOG_DIR"
cd "$REPO_ROOT"

"$PYTHON_BIN" pipeline/evaluation/validate_questions.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/evaluation/validate_gold_chunks.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/evaluation/evaluate_all.py --require-reviewed --top-k 10 --device cpu >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/evaluation/select_winner.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/evaluation/calibrate_app_thresholds.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/validate_manifests.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/audit_metadata.py >> "$RUN_LOG" 2>&1
"$PYTHON_BIN" pipeline/evaluation/finalize_grid.py >> "$RUN_LOG" 2>&1

printf '%s ALL_COMPLETE approved_winner_and_thresholds\n' "$(date --iso-8601=seconds)" >> "$RUN_LOG"
