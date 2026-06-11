#!/bin/sh
set -e
PORT="${PORT:-8000}"
echo "Starting MCP server on port $PORT"
echo "PYTHONPATH=$PYTHONPATH"
echo "Testing imports..."
python3 -c "
import sys
print('Python:', sys.version)
print('sys.path:', sys.path)
try:
    import mcp_server.serve_http
    print('serve_http import OK')
except Exception as e:
    print(f'Import failed: {e}')
    import traceback
    traceback.print_exc()
    sys.exit(1)
"
echo "Imports OK, starting server..."
exec python3 -m mcp_server.serve_http
