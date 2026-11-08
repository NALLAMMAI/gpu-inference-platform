#!/usr/bin/env bash
# Run from the repository root on a host with a vLLM bench CLI installed.
# Ensure the inference endpoint is available (e.g. kubectl port-forward).
set -euo pipefail
BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
MODEL="${MODEL:-Qwen/Qwen3-0.6B}"
RESULT_DIR="${RESULT_DIR:-benchmarks/final-benchmark/results}"
mkdir -p "$RESULT_DIR"

CONCURRENCY_LEVELS=(1 2 4 8 16 32)
COOLDOWN_SECONDS=180
for i in "${!CONCURRENCY_LEVELS[@]}"; do
  c="${CONCURRENCY_LEVELS[$i]}"
  echo "Starting synthetic sweep concurrency=$c"
  vllm bench serve \
    --backend openai-chat \
    --base-url "$BASE_URL" \
    --model "$MODEL" \
    --dataset-name random \
    --random-input-len 512 \
    --random-output-len 256 \
    --num-prompts 128 \
    --request-rate inf \
    --max-concurrency "$c" \
    --percentile-metrics ttft,tpot \
    --metric-percentiles 95 \
    --save-result \
    --result-dir "$RESULT_DIR" \
    --result-filename "concurrency-$c.json"

  # Rest for three minutes between runs, but not after the final run.
  if (( i < ${#CONCURRENCY_LEVELS[@]} - 1 )); then
    echo "Cooldown: waiting ${COOLDOWN_SECONDS}s before next concurrency test..."
    sleep "$COOLDOWN_SECONDS"
  fi
done
echo "Complete: $RESULT_DIR"
