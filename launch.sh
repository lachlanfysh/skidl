#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG_DIR="${LOG_DIR:-"$ROOT/artifacts/logs"}"
PID_FILE="${PID_FILE:-"$ROOT/artifacts/run_corpus.pid"}"
MODE="${MODE:-engine_only}"
PYTHON_BIN="${PYTHON_BIN:-"$ROOT/.venv/bin/python"}"
USE_MCP="${USE_MCP:-1}"
MAX_RUNTIME_HOURS="${MAX_RUNTIME_HOURS:-8}"
MAX_TOTAL_SPEND_USD="${MAX_TOTAL_SPEND_USD:-10}"

mkdir -p "$LOG_DIR" "$(dirname "$PID_FILE")"

if [[ ! -x "$PYTHON_BIN" ]]; then
  PYTHON_BIN="python3"
fi

if [[ "$MODE" != "engine_only" && -z "${OPENROUTER_API_KEY:-}" ]]; then
  echo "OPENROUTER_API_KEY is not set; launching would silently degrade to engine_only." >&2
  echo "Set OPENROUTER_API_KEY, or run MODE=engine_only ./launch.sh" >&2
  exit 2
fi

LOG_FILE="$LOG_DIR/run_corpus-$(date +%Y%m%d-%H%M%S).log"
CMD=(
  "$PYTHON_BIN"
  -m corpus.run_corpus
  --mode "$MODE"
  --pid-file "$PID_FILE"
  --max-runtime-hours "$MAX_RUNTIME_HOURS"
  --max-total-spend-usd "$MAX_TOTAL_SPEND_USD"
)

if [[ "$USE_MCP" != "1" ]]; then
  CMD+=(--no-mcp)
fi

nohup "${CMD[@]}" "$@" >"$LOG_FILE" 2>&1 &
PID="$!"
echo "$PID" >"$PID_FILE"

echo "Started corpus runner PID $PID"
echo "PID file: $PID_FILE"
echo "Log file: $LOG_FILE"
echo "Tail logs: tail -f \"$LOG_FILE\""
