#!/usr/bin/env bash
# Overnight stress test: run mcp_ux_probe against multiple OpenRouter models.
# Each model gets 3 runs with varied design requests.
# Results land in /tmp/eda-ux-overnight/<model-slug>/<run-N>/

set -euo pipefail
cd "$(dirname "$0")/.."

export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:?Set OPENROUTER_API_KEY env var}"
SERVER="https://mcp-server-production-5d58.up.railway.app/mcp"
TOKEN="${MCP_TOKEN:?Set MCP_TOKEN env var}"
OUT_BASE="/tmp/eda-ux-overnight"
LOG="$OUT_BASE/overnight.log"

mkdir -p "$OUT_BASE"

MODELS=(
  "meta-llama/llama-4-maverick"
  "meta-llama/llama-4-scout"
  "meta-llama/llama-3.3-70b-instruct"
  "google/gemini-2.5-flash"
)

REQUESTS=(
  "Design me a BME280 sensor breakout board: I2C interface with 10K pullups on SDA/SCL, proper decoupling, and a 1x06 2.54mm pin header breaking out 3V3, GND, SDA, SCL, SDO and CSB. 3.3V operation, compact board."
  "Build a TMP117 high-precision temperature sensor board. I2C address set to 0x48, 100nF decoupling cap, and a Qwiic/STEMMA QT JST-SH 4-pin connector for I2C+power. Include test points on SDA and SCL."
  "Create an RGB LED driver board using 3x IRLML6344 N-channel MOSFETs. Each MOSFET drives one color channel (common-anode LED strip). 10K gate pulldowns, 100R gate resistors. 1x04 2.54mm header for signal input (R, G, B, GND) and a 1x02 screw terminal for 12V LED strip power."
)

echo "=== EDA MCP Overnight Stress Test ===" | tee "$LOG"
echo "Started: $(date -Iseconds)" | tee -a "$LOG"
echo "Models: ${#MODELS[@]}, Requests: ${#REQUESTS[@]}, Total runs: $(( ${#MODELS[@]} * ${#REQUESTS[@]} ))" | tee -a "$LOG"
echo "" | tee -a "$LOG"

total=0
passed=0
failed=0

for model in "${MODELS[@]}"; do
  slug="${model//\//_}"
  for run_idx in "${!REQUESTS[@]}"; do
    request="${REQUESTS[$run_idx]}"
    run_dir="$OUT_BASE/$slug/run-$run_idx"
    total=$((total + 1))

    mkdir -p "$run_dir"
    echo "[$total] Model=$model Run=$run_idx" | tee -a "$LOG"
    echo "    Request: ${request:0:80}..." | tee -a "$LOG"

    if python3 -m corpus.mcp_ux_probe \
        --model "$model" \
        --server "$SERVER" \
        --token "$TOKEN" \
        --out "$run_dir" \
        --request "$request" \
        >> "$run_dir/stdout.log" 2>&1; then

      if [ -f "$run_dir/summary.json" ]; then
        finished=$(python3 -c "import json; d=json.load(open('$run_dir/summary.json')); print(d.get('finished_with_report', False))")
        turns=$(python3 -c "import json; d=json.load(open('$run_dir/summary.json')); print(d.get('turns_used', '?'))")
        wall=$(python3 -c "import json; d=json.load(open('$run_dir/summary.json')); print(d.get('wall_time_s', '?'))")
        arts=$(python3 -c "import json; d=json.load(open('$run_dir/summary.json')); print(len(d.get('artifacts_fetched', [])))")
        echo "    OK: finished=$finished turns=$turns wall=${wall}s artifacts=$arts" | tee -a "$LOG"
        passed=$((passed + 1))
      else
        echo "    WARN: completed but no summary.json" | tee -a "$LOG"
        failed=$((failed + 1))
      fi
    else
      echo "    FAIL: probe crashed (exit $?)" | tee -a "$LOG"
      failed=$((failed + 1))
    fi

    echo "" | tee -a "$LOG"
    sleep 5
  done
done

echo "=== Summary ===" | tee -a "$LOG"
echo "Total: $total  Passed: $passed  Failed: $failed" | tee -a "$LOG"
echo "Finished: $(date -Iseconds)" | tee -a "$LOG"

# Aggregate results into a single report
python3 -c "
import json, os, sys
base = '$OUT_BASE'
results = []
for model_dir in sorted(os.listdir(base)):
    model_path = os.path.join(base, model_dir)
    if not os.path.isdir(model_path) or model_dir == 'overnight.log':
        continue
    for run_dir in sorted(os.listdir(model_path)):
        summary_path = os.path.join(model_path, run_dir, 'summary.json')
        if os.path.exists(summary_path):
            with open(summary_path) as f:
                results.append(json.load(f))
with open(os.path.join(base, 'aggregate.json'), 'w') as f:
    json.dump(results, f, indent=1)
print(f'Wrote aggregate.json with {len(results)} results')
" | tee -a "$LOG"
