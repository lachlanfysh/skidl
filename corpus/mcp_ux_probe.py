"""Blind agent-UX probe: drive the deployed eda-mcp server with an OpenRouter model.

Measures whether the MCP surface (server instructions, tool descriptions,
resources) is sufficient for a non-frontier model to complete a board design
unaided. The model sees exactly what an MCP client would expose: the server's
instructions, its tools as native tool-calls, and a read_resource tool.
It gets NO prior knowledge of the spec format.

Usage:
    OPENROUTER_API_KEY=... python3 -m corpus.mcp_ux_probe \
        --model meta-llama/llama-4-maverick \
        --out /tmp/eda-ux-llama \
        --server https://mcp-server-production-5d58.up.railway.app/mcp \
        --token $EDA_AUTH_TOKEN

Outputs in --out: transcript.json (full message log), call_log.txt,
artifacts/ (any fetched board files), summary.json (machine-readable outcome).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

import httpx

USER_REQUEST = (
    "Design me a BME280 sensor breakout board: I2C interface with 10K pullups "
    "on SDA/SCL, proper decoupling, and a 1x06 2.54mm pin header breaking out "
    "3V3, GND, SDA, SCL, SDO and CSB. 3.3V operation, compact board."
)

SYSTEM_PROMPT = """\
You are an autonomous hardware design agent connected to the "eda-mcp" PCB \
design service via tool calls. Complete the user's request using ONLY the \
tools provided and what the service itself tells you. You have no prior \
documentation for this service — its tool descriptions and readable resources \
are your only source of truth for input formats and workflow. General \
electronics and KiCad knowledge is fine.

Service instructions: {instructions}

Practical notes:
- Poll asynchronous jobs with the polling tool until they reach a terminal \
status; the harness inserts real delays between polls for you.
- If a run returns exceptions, resolve them through the service's correction \
mechanism. Stop after at most 5 correction rounds.
- When you finish (or are stuck), reply with plain text starting with \
"FINAL REPORT:" summarizing the outcome, every point of confusion you hit, \
anything you had to guess, and what the service could explain better.
"""

MAX_MODEL_TURNS = 60
MAX_TOOL_RESULT_CHARS = 15000
POLL_SPACING_S = 5.0


class MCPClient:
    """Minimal streamable-HTTP MCP client."""

    def __init__(self, url: str, token: str):
        self.url = url
        self.headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        self._id = 0
        self.http = httpx.Client(timeout=120)

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        r = self.http.post(self.url, headers=self.headers, json=payload)
        r.raise_for_status()
        for line in r.text.strip().split("\n"):
            if line.startswith("data:"):
                msg = json.loads(line[5:])
                if "error" in msg:
                    raise RuntimeError(f"MCP error: {msg['error']}")
                return msg.get("result", {})
        raise RuntimeError(f"no data frame in response: {r.text[:200]}")

    def connect(self) -> dict:
        self._id += 1
        r = self.http.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0", "id": self._id, "method": "initialize",
            "params": {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "mcp-ux-probe", "version": "1.0"},
            },
        })
        r.raise_for_status()
        self.headers["Mcp-Session-Id"] = r.headers["mcp-session-id"]
        init = None
        for line in r.text.strip().split("\n"):
            if line.startswith("data:"):
                init = json.loads(line[5:])["result"]
        self.http.post(self.url, headers=self.headers, json={
            "jsonrpc": "2.0", "method": "notifications/initialized",
        })
        return init

    def list_tools(self) -> list[dict]:
        return self._rpc("tools/list")["tools"]

    def list_resources(self) -> list[dict]:
        return self._rpc("resources/list")["resources"]

    def call_tool(self, name: str, arguments: dict) -> str:
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        parts = [c.get("text", "") for c in result.get("content", [])]
        text = "\n".join(parts)
        if result.get("isError"):
            return f"TOOL ERROR: {text}"
        return text

    def read_resource(self, uri: str) -> str:
        result = self._rpc("resources/read", {"uri": uri})
        return "\n".join(c.get("text", "") for c in result.get("contents", []))


def openai_tools(mcp_tools: list[dict], resources: list[dict]) -> list[dict]:
    tools = [
        {
            "type": "function",
            "function": {
                "name": t["name"],
                "description": t.get("description", ""),
                "parameters": t.get("inputSchema", {"type": "object"}),
            },
        }
        for t in mcp_tools
    ]
    listing = "; ".join(f"{r['uri']} ({r.get('description','')})" for r in resources)
    tools.append({
        "type": "function",
        "function": {
            "name": "read_resource",
            "description": f"Read one of the service's documentation resources. Available: {listing}",
            "parameters": {
                "type": "object",
                "properties": {"uri": {"type": "string"}},
                "required": ["uri"],
            },
        },
    })
    return tools


def shrink_result(name: str, text: str, artifacts_dir: Path) -> str:
    """Keep tool results model-sized; spool artifact file bodies to disk."""
    if name in ("get_run", "get_job"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            data = None
        if isinstance(data, dict):
            container = data
            if isinstance(data.get("result"), dict):
                container = data["result"]
            arts = container.get("artifacts")
            if isinstance(arts, dict) and arts:
                artifacts_dir.mkdir(parents=True, exist_ok=True)
                spooled = {}
                for fname, content in arts.items():
                    if isinstance(content, str) and len(content) > 500:
                        (artifacts_dir / fname).write_text(content)
                        spooled[fname] = f"<file saved to disk, {len(content)} bytes>"
                    else:
                        spooled[fname] = content
                container["artifacts"] = spooled
            # The job result echoes the full spec twice; drop one copy.
            if container is not data and isinstance(container.get("spec"), dict) \
                    and container.get("spec") == data.get("spec"):
                container["spec"] = "<same as top-level spec, omitted>"
            text = json.dumps(data, indent=1)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        half = MAX_TOOL_RESULT_CHARS // 2
        text = text[:half] + f"\n...[{len(text)-MAX_TOOL_RESULT_CHARS} chars truncated]...\n" + text[-half:]
    return text


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="meta-llama/llama-4-maverick")
    ap.add_argument("--server", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--request", default=USER_REQUEST)
    args = ap.parse_args()

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        print("OPENROUTER_API_KEY not set", file=sys.stderr)
        return 1

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    artifacts_dir = out / "artifacts"
    call_log = (out / "call_log.txt").open("w")

    mcp = MCPClient(args.server, args.token)
    init = mcp.connect()
    instructions = init.get("instructions", "(none)")
    tools = openai_tools(mcp.list_tools(), mcp.list_resources())

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT.format(instructions=instructions)},
        {"role": "user", "content": args.request},
    ]

    or_http = httpx.Client(timeout=300)
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0}
    last_poll_at = 0.0
    started = time.time()
    final_report = None
    nudges = 0

    for turn in range(MAX_MODEL_TURNS):
        resp = or_http.post(
            "https://openrouter.ai/api/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "model": args.model,
                "messages": messages,
                "tools": tools,
                "temperature": 0.2,
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if "choices" not in body:
            print(f"OpenRouter error: {json.dumps(body)[:500]}", file=sys.stderr)
            return 1
        for k in usage_totals:
            usage_totals[k] += body.get("usage", {}).get(k, 0)
        msg = body["choices"][0]["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        content = (msg.get("content") or "").strip()

        if not tool_calls:
            if content.startswith("FINAL REPORT"):
                final_report = content
                break
            if nudges >= 3:
                final_report = content or "(model stopped without a report)"
                break
            messages.append({
                "role": "user",
                "content": (
                    "You wrote text instead of acting. You MUST use the native "
                    "tool-calling mechanism (function calls), not JSON in prose. "
                    "Invoke one of your available tools now, or finish with a "
                    "message starting 'FINAL REPORT:'."
                ),
            })
            nudges += 1
            continue
        nudges = 0

        for tc in tool_calls:
            name = tc["function"]["name"]
            try:
                arguments = json.loads(tc["function"]["arguments"] or "{}")
            except json.JSONDecodeError as exc:
                result = f"TOOL ERROR: your arguments were not valid JSON: {exc}"
                arguments = None
            if arguments is not None:
                if name == "get_job":
                    wait = POLL_SPACING_S - (time.time() - last_poll_at)
                    if wait > 0:
                        time.sleep(wait)
                    last_poll_at = time.time()
                try:
                    if name == "read_resource":
                        result = mcp.read_resource(arguments["uri"])
                    else:
                        result = mcp.call_tool(name, arguments)
                except Exception as exc:
                    result = f"TOOL ERROR: {exc}"
            result = shrink_result(name, result, artifacts_dir)
            arg_short = json.dumps(arguments)[:160] if arguments is not None else "<bad json>"
            outcome = "error" if result.startswith("TOOL ERROR") else "ok"
            call_log.write(f"turn={turn} {name} args={arg_short} -> {outcome} ({len(result)} chars)\n")
            call_log.flush()
            print(f"[{turn:02d}] {name} {arg_short[:100]} -> {outcome}", flush=True)
            messages.append({
                "role": "tool",
                "tool_call_id": tc["id"],
                "content": result,
            })

    wall = time.time() - started
    call_log.close()
    (out / "transcript.json").write_text(json.dumps(messages, indent=1))
    summary = {
        "model": args.model,
        "turns_used": turn + 1,
        "wall_time_s": round(wall, 1),
        "usage": usage_totals,
        "finished_with_report": final_report is not None and final_report.startswith("FINAL REPORT"),
        "artifacts_fetched": sorted(p.name for p in artifacts_dir.glob("*")) if artifacts_dir.exists() else [],
        "final_report": final_report,
    }
    (out / "summary.json").write_text(json.dumps(summary, indent=1))
    print(json.dumps({k: v for k, v in summary.items() if k != "final_report"}, indent=1))
    if final_report:
        print("\n" + final_report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
