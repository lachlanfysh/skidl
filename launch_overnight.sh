#!/usr/bin/env bash
set -euo pipefail

# Overnight corpus run: engine_only baseline then internal mode with LLM corrections
# Usage: OPENROUTER_API_KEY=sk-or-... nohup ./launch_overnight.sh > artifacts/logs/overnight.log 2>&1 &

export KICAD9_SYMBOL_DIR="${KICAD9_SYMBOL_DIR:-/usr/share/kicad/symbols}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p artifacts/logs

echo "=== Phase A: engine_only baseline (50 boards, $0) ==="
echo "Started: $(date)"
python3 -m corpus.run_corpus \
  --mode engine_only \
  --no-mcp \
  --max-runtime-hours 2 \
  --concurrency 2

echo ""
echo "=== Phase B: internal mode (LLM correction loop) ==="
echo "Started: $(date)"
if [[ -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY not set — skipping internal mode"
else
  python3 -m corpus.run_corpus \
    --mode internal \
    --no-mcp \
    --max-runtime-hours 4 \
    --max-total-spend-usd 8 \
    --max-iters 8 \
    --concurrency 1

  echo ""
  echo "=== Phase C: external mode (subset if budget remains) ==="
  echo "Started: $(date)"
  python3 -m corpus.run_corpus \
    --mode external \
    --no-mcp \
    --max-runtime-hours 1 \
    --max-total-spend-usd 10 \
    --max-iters 4 \
    --concurrency 1 \
    --limit 10
fi

echo ""
echo "=== Done ==="
echo "Finished: $(date)"
echo "Records: $(wc -l < telemetry/runs.jsonl) rows in telemetry/runs.jsonl"
