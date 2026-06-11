#!/usr/bin/env bash
# Probe the deployed eda-mcp server using claude -p (local CLI, no API cost).
# Usage: ./corpus/claude_probe.sh [model] [out_dir]
#   model: "sonnet" (default) or "opus"
#   out_dir: defaults to /tmp/eda-ux-claude/<model>-<timestamp>

set -euo pipefail

MODEL="${1:-sonnet}"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)
OUT_DIR="${2:-/tmp/eda-ux-claude/${MODEL}-${TIMESTAMP}}"
mkdir -p "$OUT_DIR"

MCP_CONFIG=$(cat <<'MCPEOF'
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
MCPEOF
)

REQUESTS=(
  "Design me a BME280 sensor breakout board: I2C interface with 10K pullups on SDA/SCL, proper decoupling, and a 1x06 2.54mm pin header breaking out 3V3, GND, SDA, SCL, SDO and CSB. 3.3V operation, compact board."
  "Build a TMP117 high-precision temperature sensor board. I2C address set to 0x48, 100nF decoupling cap, and a Qwiic/STEMMA QT JST-SH 4-pin connector for I2C+power. Include test points on SDA and SCL."
  "Create an RGB LED driver board using 3x IRLML6344 N-channel MOSFETs. Each MOSFET drives one color channel (common-anode LED strip). 10K gate pulldowns, 100R gate resistors. 1x04 2.54mm header for signal input (R, G, B, GND) and a 1x02 screw terminal for 12V LED strip power."
  "Design a USB-C power delivery board with a FUSB302 controller. Include a 5.1K CC pulldown for sink mode, 100nF and 10uF decoupling, and a screw terminal output for VBUS+GND. LED indicator on VBUS."
  "Build an I2S audio DAC breakout using a PCM5102A. Include 3.3V LDO (AP2112K-3.3), bulk and bypass caps, and a 3.5mm TRS audio jack output. 1x06 header for I2S signals (BCK, LRCK, DIN, SCK, 3V3, GND)."
)

SYSTEM_PROMPT="You are testing a PCB design MCP service. Use ONLY the eda MCP tools to complete the design — read the guide resource first, build a CircuitSpec, submit it, poll until done, apply corrections if needed, and fetch the final artifacts. When finished, summarize: what worked, what errors you hit, what confused you about the service."

echo "=== Claude Probe: $MODEL ===" | tee "$OUT_DIR/probe.log"
echo "Started: $(date -Iseconds)" | tee -a "$OUT_DIR/probe.log"

for i in "${!REQUESTS[@]}"; do
  request="${REQUESTS[$i]}"
  run_dir="$OUT_DIR/run-$i"
  mkdir -p "$run_dir"

  echo "" | tee -a "$OUT_DIR/probe.log"
  echo "[$i] Request: ${request:0:80}..." | tee -a "$OUT_DIR/probe.log"

  start_s=$(date +%s)

  if claude -p "$SYSTEM_PROMPT

$request" \
    --model "$MODEL" \
    --mcp-config <(echo "$MCP_CONFIG") \
    --output-format json \
    --max-turns 40 \
    --allowedTools "mcp__eda__*" \
    > "$run_dir/output.json" 2>"$run_dir/stderr.log"; then
    status="ok"
  else
    status="error (exit $?)"
  fi

  wall=$(($(date +%s) - start_s))
  echo "    $status wall=${wall}s" | tee -a "$OUT_DIR/probe.log"

  # Extract result text
  python3 -c "
import json, sys
try:
    data = json.load(open('$run_dir/output.json'))
    if isinstance(data, dict):
        text = data.get('result', data.get('content', str(data)))
    elif isinstance(data, list):
        text = '\n'.join(m.get('content','') or '' for m in data if m.get('role')=='assistant')
    else:
        text = str(data)
    open('$run_dir/result.txt', 'w').write(text[:5000])
    print('    Result: ' + text[:200].replace(chr(10), ' '))
except Exception as e:
    print(f'    Parse error: {e}')
" | tee -a "$OUT_DIR/probe.log"

done

echo "" | tee -a "$OUT_DIR/probe.log"
echo "Finished: $(date -Iseconds)" | tee -a "$OUT_DIR/probe.log"
