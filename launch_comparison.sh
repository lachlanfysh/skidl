#!/usr/bin/env bash
set -euo pipefail

# 2x comparison run:
#   Phase 1: engine-only on all reference boards ($0 LLM cost, tests engine determinism)
#   Phase 2+3: internal mode on Adafruit boards, twice (tests LLM stochastic variation)
# Usage: OPENROUTER_API_KEY=sk-or-... nohup ./launch_comparison.sh > artifacts/logs/comparison.log 2>&1 &

export KICAD9_SYMBOL_DIR="${KICAD9_SYMBOL_DIR:-/usr/share/kicad/symbols}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p artifacts/logs telemetry

echo "=== Phase 1: Engine-only (reference boards, $0 LLM) ==="
echo "Started: $(date)"
python3 -m corpus.run_corpus \
  --mode engine_only \
  --no-mcp \
  --validation-mode reference \
  --telemetry telemetry/runs_a.jsonl \
  --max-runtime-hours 4 \
  --max-iters 8 \
  --concurrency 2 \
  --force

echo ""
echo "=== Phase 2: Run A — internal mode (Adafruit, LLM-driven) ==="
echo "Started: $(date)"
python3 -m corpus.run_corpus \
  --mode internal \
  --no-mcp \
  --validation-mode internal \
  --telemetry telemetry/runs_a.jsonl \
  --max-runtime-hours 3 \
  --max-total-spend-usd 5 \
  --max-iters 8 \
  --concurrency 2 \
  --force

echo ""
echo "=== Phase 3: Run B — internal mode (Adafruit, second pass) ==="
echo "Started: $(date)"
python3 -m corpus.run_corpus \
  --mode internal \
  --no-mcp \
  --validation-mode internal \
  --telemetry telemetry/runs_b.jsonl \
  --max-runtime-hours 3 \
  --max-total-spend-usd 5 \
  --max-iters 8 \
  --concurrency 2 \
  --force

echo ""
echo "=== Done ==="
echo "Finished: $(date)"
echo "Run A: $(wc -l < telemetry/runs_a.jsonl) records"
echo "Run B: $(wc -l < telemetry/runs_b.jsonl) records"
echo ""
echo "Compare Adafruit runs: python3 -c \"
import json
from collections import Counter
a = {json.loads(l)['board_id']: json.loads(l)['status'] for l in open('telemetry/runs_a.jsonl') if json.loads(l).get('mode')=='internal'}
b = {json.loads(l)['board_id']: json.loads(l)['status'] for l in open('telemetry/runs_b.jsonl')}
same = sum(1 for k in a if k in b and a[k] == b[k])
diff = sum(1 for k in a if k in b and a[k] != b[k])
print(f'Same outcome: {same}, Different: {diff}')
for k in sorted(a):
    if k in b and a[k] != b[k]:
        print(f'  {k}: {a[k]} -> {b[k]}')
\""
