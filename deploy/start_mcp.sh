#!/bin/sh
PORT="${PORT:-8000}"
echo "Starting MCP server on port $PORT"
exec python3 -m mcp_server.serve_http
