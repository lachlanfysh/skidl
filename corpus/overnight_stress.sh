#!/usr/bin/env bash
# Overnight stress test: run mcp_ux_probe in a continuous loop across
# multiple models and design requests. Runs until killed or MAX_HOURS.
# Results: /tmp/eda-ux-overnight/<model-slug>/run-<N>/

set -euo pipefail
cd "$(dirname "$0")/.."

export OPENROUTER_API_KEY="${OPENROUTER_API_KEY:?Set OPENROUTER_API_KEY env var}"
SERVER="https://mcp-server-production-5d58.up.railway.app/mcp"
TOKEN="${MCP_TOKEN:?Set MCP_TOKEN env var}"
OUT_BASE="/tmp/eda-ux-overnight"
LOG="$OUT_BASE/overnight.log"
MAX_HOURS=8

mkdir -p "$OUT_BASE"

MODELS=(
  "meta-llama/llama-4-maverick"
  "meta-llama/llama-4-scout"
  "meta-llama/llama-3.3-70b-instruct"
  "google/gemini-2.5-flash"
  "nvidia/llama-3.3-nemotron-super-49b-v1.5"
  "qwen/qwen3-235b-a22b"
  "mistralai/mistral-medium-3"
  "deepseek/deepseek-chat-v3-0324"
)

REQUESTS=(
  "Design me a BME280 sensor breakout board: I2C interface with 10K pullups on SDA/SCL, proper decoupling, and a 1x06 2.54mm pin header breaking out 3V3, GND, SDA, SCL, SDO and CSB. 3.3V operation, compact board."
  "Build a TMP117 high-precision temperature sensor board. I2C address set to 0x48, 100nF decoupling cap, and a Qwiic/STEMMA QT JST-SH 4-pin connector for I2C+power. Include test points on SDA and SCL."
  "Create an RGB LED driver board using 3x IRLML6344 N-channel MOSFETs. Each MOSFET drives one color channel (common-anode LED strip). 10K gate pulldowns, 100R gate resistors. 1x04 2.54mm header for signal input (R, G, B, GND) and a 1x02 screw terminal for 12V LED strip power."
  "Design a USB-C power delivery board with a FUSB302 controller. Include a 5.1K CC pulldown for sink mode, 100nF and 10uF decoupling, and a screw terminal output for VBUS+GND. LED indicator on VBUS."
  "Build an I2S audio DAC breakout using a PCM5102A. Include 3.3V LDO (AP2112K-3.3), bulk and bypass caps, and a 3.5mm TRS audio jack output. 1x06 header for I2S signals (BCK, LRCK, DIN, SCK, 3V3, GND)."
)

START_EPOCH=$(date +%s)
END_EPOCH=$((START_EPOCH + MAX_HOURS * 3600))

echo "=== EDA MCP Overnight Stress Test ===" | tee "$LOG"
echo "Started: $(date -Iseconds)" | tee -a "$LOG"
echo "Models: ${#MODELS[@]}, Requests: ${#REQUESTS[@]}" | tee -a "$LOG"
echo "Will run for up to ${MAX_HOURS}h (until $(date -d @$END_EPOCH -Iseconds 2>/dev/null || date -r $END_EPOCH -Iseconds 2>/dev/null || echo 'unknown'))" | tee -a "$LOG"
echo "" | tee -a "$LOG"

total=0
passed=0
failed=0
round=0

while [ "$(date +%s)" -lt "$END_EPOCH" ]; do
  round=$((round + 1))
  echo "=== Round $round ($(date -Iseconds)) ===" | tee -a "$LOG"

  for model in "${MODELS[@]}"; do
    slug="${model//\//_}"

    # Pick a random request for this round
    req_idx=$(( (round + RANDOM) % ${#REQUESTS[@]} ))
    request="${REQUESTS[$req_idx]}"

    # Find next available run number for this model
    run_num=0
    while [ -d "$OUT_BASE/$slug/run-$run_num" ]; do
      run_num=$((run_num + 1))
    done
    run_dir="$OUT_BASE/$slug/run-$run_num"
    mkdir -p "$run_dir"

    total=$((total + 1))
    echo "[$total] round=$round model=$model req=$req_idx" | tee -a "$LOG"

    # Check time budget
    if [ "$(date +%s)" -ge "$END_EPOCH" ]; then
      echo "Time limit reached, stopping." | tee -a "$LOG"
      break 2
    fi

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

    # Brief pause between runs to avoid hammering OpenRouter
    sleep 3
  done

  echo "Round $round done. Total=$total Passed=$passed Failed=$failed" | tee -a "$LOG"
  echo "" | tee -a "$LOG"
done

echo "=== Final Summary ===" | tee -a "$LOG"
echo "Total: $total  Passed: $passed  Failed: $failed" | tee -a "$LOG"
echo "Finished: $(date -Iseconds)" | tee -a "$LOG"

# Aggregate results
python3 -c "
import json, os
base = '$OUT_BASE'
results = []
for model_dir in sorted(os.listdir(base)):
    model_path = os.path.join(base, model_dir)
    if not os.path.isdir(model_path):
        continue
    for run_dir in sorted(os.listdir(model_path)):
        summary_path = os.path.join(model_path, run_dir, 'summary.json')
        if os.path.exists(summary_path):
            with open(summary_path) as f:
                results.append(json.load(f))
with open(os.path.join(base, 'aggregate.json'), 'w') as f:
    json.dump(results, f, indent=1)
print(f'Wrote aggregate.json with {len(results)} results')

# Per-model summary
from collections import defaultdict
by_model = defaultdict(list)
for r in results:
    by_model[r['model'].split('/')[-1]].append(r)
for model, runs in sorted(by_model.items()):
    reports = sum(1 for r in runs if r['finished_with_report'])
    avg_turns = sum(r['turns_used'] for r in runs) / len(runs)
    avg_wall = sum(r['wall_time_s'] for r in runs) / len(runs)
    print(f'  {model:40s}  n={len(runs):2d}  reports={reports}/{len(runs)}  avg_turns={avg_turns:.0f}  avg_wall={avg_wall:.0f}s')
" | tee -a "$LOG"
