#!/usr/bin/env bash
# Overnight Claude probe: runs sonnet and opus through all 5 design requests,
# 3 repetitions each. ~30 runs total, free on Max sub.
# Usage: nohup ~/Projects/skidl/corpus/claude_overnight.sh &

set -euo pipefail
cd "$(dirname "$0")/.."

OUT="/tmp/eda-ux-claude"
LOG="$OUT/overnight.log"
mkdir -p "$OUT"

MCP_CONFIG_FILE="$OUT/.mcp-eda.json"
cat > "$MCP_CONFIG_FILE" <<'EOF'
{
  "mcpServers": {
    "eda": {
      "type": "streamable-http",
      "url": "https://mcp-server-production-5d58.up.railway.app/mcp",
      "headers": {
        "Authorization": "Bearer ${MCP_TOKEN:?Set MCP_TOKEN env var}"
      }
    }
  }
}
EOF

MODELS=(sonnet opus)
REPS=3

REQUESTS=(
  "Design me a BME280 sensor breakout board: I2C interface with 10K pullups on SDA/SCL, proper decoupling, and a 1x06 2.54mm pin header breaking out 3V3, GND, SDA, SCL, SDO and CSB. 3.3V operation, compact board."
  "Build a TMP117 high-precision temperature sensor board. I2C address set to 0x48, 100nF decoupling cap, and a Qwiic/STEMMA QT JST-SH 4-pin connector for I2C+power. Include test points on SDA and SCL."
  "Create an RGB LED driver board using 3x IRLML6344 N-channel MOSFETs. Each MOSFET drives one color channel (common-anode LED strip). 10K gate pulldowns, 100R gate resistors. 1x04 2.54mm header for signal input (R, G, B, GND) and a 1x02 screw terminal for 12V LED strip power."
  "Design a USB-C power delivery board with a FUSB302 controller. Include a 5.1K CC pulldown for sink mode, 100nF and 10uF decoupling, and a screw terminal output for VBUS+GND. LED indicator on VBUS."
  "Build an I2S audio DAC breakout using a PCM5102A. Include 3.3V LDO (AP2112K-3.3), bulk and bypass caps, and a 3.5mm TRS audio jack output. 1x06 header for I2S signals (BCK, LRCK, DIN, SCK, 3V3, GND)."
)

SYSTEM="You are testing a PCB design MCP service. Use ONLY the eda MCP tools — read the guide resource first, build a CircuitSpec, submit it, poll until done, apply corrections if needed, fetch artifacts with get_run. When finished, summarize: what worked, what errors you hit, what confused you."

echo "=== Claude Overnight Probe ===" | tee "$LOG"
echo "Started: $(date -Iseconds)" | tee -a "$LOG"
echo "Models: ${MODELS[*]}, Requests: ${#REQUESTS[@]}, Reps: $REPS" | tee -a "$LOG"
echo "" | tee -a "$LOG"

total=0
for model in "${MODELS[@]}"; do
  for rep in $(seq 0 $((REPS - 1))); do
    for req_idx in "${!REQUESTS[@]}"; do
      request="${REQUESTS[$req_idx]}"
      run_dir="$OUT/$model/req${req_idx}-rep${rep}"
      mkdir -p "$run_dir"
      total=$((total + 1))

      echo "[$total] model=$model req=$req_idx rep=$rep" | tee -a "$LOG"
      start_s=$(date +%s)

      if claude -p "$SYSTEM

$request" \
          --model "$model" \
          --mcp-config "$MCP_CONFIG_FILE" \
          --output-format json \
          --max-turns 40 \
          --allowedTools "mcp__eda__*" \
          > "$run_dir/output.json" 2>"$run_dir/stderr.log"; then
        exit_code=0
      else
        exit_code=$?
      fi

      wall=$(($(date +%s) - start_s))

      # Parse result
      python3 -c "
import json
try:
    raw = open('$run_dir/output.json').read()
    data = json.loads(raw) if raw.strip() else {}
    text = ''
    if isinstance(data, dict):
        text = data.get('result', '')
    elif isinstance(data, list):
        text = '\n'.join(m.get('content','') or '' for m in data if m.get('role')=='assistant')
    open('$run_dir/result.txt', 'w').write(str(text)[:8000])
    # Count tool calls in output
    tool_count = raw.count('\"tool_use\"') + raw.count('mcp__eda__')
    summary = {'model': '$model', 'req': $req_idx, 'rep': $rep, 'wall_s': $wall, 'exit': $exit_code, 'tool_calls': tool_count, 'result_len': len(text)}
    json.dump(summary, open('$run_dir/summary.json', 'w'), indent=1)
    print(f'    exit={$exit_code} wall=${wall}s tools={tool_count} result={len(text)} chars')
except Exception as e:
    print(f'    parse error: {e}')
" | tee -a "$LOG"

      sleep 2
    done
  done
done

echo "" | tee -a "$LOG"
echo "=== Done: $total runs ===" | tee -a "$LOG"
echo "Finished: $(date -Iseconds)" | tee -a "$LOG"

# Aggregate
python3 -c "
import json, os
from collections import defaultdict
results = []
for model_dir in sorted(os.listdir('$OUT')):
    mp = os.path.join('$OUT', model_dir)
    if not os.path.isdir(mp) or model_dir.startswith('.'):
        continue
    for run in sorted(os.listdir(mp)):
        sp = os.path.join(mp, run, 'summary.json')
        if os.path.exists(sp):
            results.append(json.load(open(sp)))
json.dump(results, open('$OUT/aggregate.json', 'w'), indent=1)
by_model = defaultdict(list)
for r in results:
    by_model[r['model']].append(r)
for model, runs in sorted(by_model.items()):
    avg_wall = sum(r['wall_s'] for r in runs) / len(runs)
    avg_tools = sum(r['tool_calls'] for r in runs) / len(runs)
    print(f'{model:8s}  n={len(runs):2d}  avg_wall={avg_wall:.0f}s  avg_tools={avg_tools:.0f}')
" | tee -a "$LOG"
