"""Tiny direct MCP JSON-RPC client for Codex sub-agent test rounds.

This module intentionally contains no model calls. It is only transport glue for
hosted MCP testing from shell scripts or sub-agents.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

import httpx


DEFAULT_SERVER = "https://mcp-server-production-5d58.up.railway.app/mcp"


def _load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


class DirectMCPClient:
    def __init__(self, url: str, token: str):
        self.url = url
        self._id = 0
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self.http = httpx.Client(timeout=120)

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    @staticmethod
    def _read_sse_result(response: httpx.Response) -> dict[str, Any]:
        response.raise_for_status()
        for line in response.text.strip().splitlines():
            if not line.startswith("data:"):
                continue
            msg = json.loads(line[5:])
            if "error" in msg:
                raise RuntimeError(json.dumps(msg["error"], indent=2))
            return msg.get("result", {})
        raise RuntimeError(f"No SSE data frame in response: {response.text[:200]}")

    def connect(self) -> dict[str, Any]:
        self.headers.pop("Mcp-Session-Id", None)
        response = self.http.post(
            self.url,
            headers=self.headers,
            json={
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "eda-mcp-direct", "version": "1.0"},
                },
            },
        )
        result = self._read_sse_result(response)
        session_id = response.headers.get("mcp-session-id")
        if not session_id:
            raise RuntimeError("MCP initialize response did not include Mcp-Session-Id")
        self.headers["Mcp-Session-Id"] = session_id
        self.http.post(
            self.url,
            headers=self.headers,
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
        ).raise_for_status()
        return result

    def rpc(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
        }
        if params is not None:
            payload["params"] = params
        return self._read_sse_result(
            self.http.post(self.url, headers=self.headers, json=payload)
        )

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return self.rpc("tools/call", {"name": name, "arguments": arguments})


def _json_arg(text: str | None) -> dict[str, Any]:
    if not text:
        return {}
    if text.startswith("@"):
        text = Path(text[1:]).read_text()
    data = json.loads(text)
    if not isinstance(data, dict):
        raise SystemExit("JSON argument must be an object")
    return data


def _content_text(result: dict[str, Any]) -> str:
    parts = result.get("content", [])
    if not isinstance(parts, list):
        return json.dumps(result)
    text = "\n".join(
        str(part.get("text", ""))
        for part in parts
        if isinstance(part, dict)
    )
    return text or json.dumps(result)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--server", default=DEFAULT_SERVER)
    parser.add_argument("--env-file", default=".env")
    parser.add_argument("--token-env", default="EDA_AUTH_TOKEN")
    parser.add_argument("--raw", action="store_true", help="Print raw MCP JSON")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tools", help="List hosted MCP tools")

    call = sub.add_parser("call", help="Call one hosted MCP tool")
    call.add_argument("tool")
    call.add_argument(
        "arguments_json",
        nargs="?",
        default="{}",
        help="JSON object, or @path/to/args.json",
    )

    args = parser.parse_args(argv)
    _load_env_file(Path(args.env_file))
    token = os.environ.get(args.token_env)
    if not token:
        raise SystemExit(f"{args.token_env} is not set")

    client = DirectMCPClient(args.server, token)
    client.connect()

    if args.cmd == "tools":
        result = client.rpc("tools/list")
        print(json.dumps(result, indent=2))
        return 0

    result = client.call_tool(args.tool, _json_arg(args.arguments_json))
    if args.raw:
        print(json.dumps(result, indent=2))
    else:
        print(_content_text(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
