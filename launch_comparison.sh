#!/usr/bin/env bash
set -euo pipefail

# 2x comparison run: same Adafruit boards, internal mode, two runs
# Compare stochastic LLM variation vs code improvements
# Usage: OPENROUTER_API_KEY=sk-or-... nohup ./launch_comparison.sh > artifacts/logs/comparison.log 2>&1 &

export KICAD9_SYMBOL_DIR="${KICAD9_SYMBOL_DIR:-/usr/share/kicad/symbols}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"
mkdir -p artifacts/logs

echo "=== Run A: internal mode (with fixes) ==="
echo "Started: $(date)"
python3 -m corpus.run_corpus \
  --mode internal \
  --no-mcp \
  --telemetry telemetry/runs_a.jsonl \
  --max-runtime-hours 3 \
  --max-total-spend-usd 5 \
  --max-iters 8 \
  --concurrency 2 \
  --force

echo ""
echo "=== Run B: internal mode (second pass, same config) ==="
echo "Started: $(date)"
python3 -m corpus.run_corpus \
  --mode internal \
  --no-mcp \
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
echo "Compare with: python3 -c \"
import json
from collections import Counter
a = {json.loads(l)['board_id']: json.loads(l)['status'] for l in open('telemetry/runs_a.jsonl')}
b = {json.loads(l)['board_id']: json.loads(l)['status'] for l in open('telemetry/runs_b.jsonl')}
same = sum(1 for k in a if k in b and a[k] == b[k])
diff = sum(1 for k in a if k in b and a[k] != b[k])
print(f'Same outcome: {same}, Different: {diff}')
for k in sorted(a):
    if k in b and a[k] != b[k]:
        print(f'  {k}: {a[k]} -> {b[k]}')
\""
