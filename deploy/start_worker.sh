#!/bin/sh
# If KICAD_VOLUME_PATH is set, use volume-mounted libs instead of apt-installed ones
KICAD_VOL="${KICAD_VOLUME_PATH:-}"
if [ -n "$KICAD_VOL" ] && [ -d "$KICAD_VOL/symbols" ]; then
    export KICAD9_SYMBOL_DIR="$KICAD_VOL/symbols"
    export KICAD9_FOOTPRINT_DIR="$KICAD_VOL/footprints"
    echo "Using volume-mounted KiCad libs from $KICAD_VOL"
fi

echo "Starting worker (concurrency=${WORKER_CONCURRENCY:-2})"
exec python3 -m mcp_server.worker
