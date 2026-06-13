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
import re
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
- If a run returns exceptions, edit the SKiDL source using the service's \
structured feedback and resubmit. Stop after at most 5 correction rounds; \
the harness enforces this cap.
- When you finish (or are stuck), reply with plain text starting with \
"FINAL REPORT:" summarizing the outcome, every point of confusion you hit, \
anything you had to guess, and what the service could explain better.
"""

USER_ONLY_SUFFIX = ""

MAX_MODEL_TURNS = 60
MAX_TOOL_RESULT_CHARS = 15000
DEFAULT_LLM_TIMEOUT_S = 90.0
DEFAULT_MAX_WALL_S = 900.0
DEFAULT_MAX_SUBMISSIONS = 5
POLL_SPACING_S = 5.0
FINAL_REPORT_RE = re.compile(r"^[\s#>*_`-]*FINAL\s+REPORT\b", re.IGNORECASE)


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

    def _rpc_once(self, method: str, params: dict | None = None) -> dict:
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

    @staticmethod
    def _looks_like_lost_session(exc: Exception) -> bool:
        text = str(exc).lower()
        return (
            "mcp-session-id" in text
            or "session not found" in text
            or "session id" in text
            or "404 not found" in text
        )

    def _rpc(self, method: str, params: dict | None = None) -> dict:
        try:
            return self._rpc_once(method, params)
        except Exception as exc:
            if not self._looks_like_lost_session(exc):
                raise
            self.headers.pop("Mcp-Session-Id", None)
            self.connect()
            return self._rpc_once(method, params)

    def connect(self) -> dict:
        self.headers.pop("Mcp-Session-Id", None)
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


def _trim_json_value(value, *, max_str: int, max_items: int, depth: int = 0):
    if isinstance(value, str):
        if len(value) <= max_str:
            return value
        return value[:max_str] + f"\n... ({len(value) - max_str} chars omitted)"
    if depth >= 5:
        return str(value)[:max_str]
    if isinstance(value, list):
        out = [
            _trim_json_value(item, max_str=max_str, max_items=max_items, depth=depth + 1)
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            out.append(f"... ({len(value) - max_items} more items)")
        return out
    if isinstance(value, dict):
        out = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_items:
                out["_truncated_keys"] = len(value) - max_items
                break
            out[key] = _trim_json_value(
                item,
                max_str=max_str,
                max_items=max_items,
                depth=depth + 1,
            )
        return out
    return value


def _compact_run_spec_for_probe(data: dict) -> None:
    """Drop echoed source/spec bulk from get_run/get_job while preserving signal."""

    specs: list[dict] = []
    if isinstance(data.get("spec"), dict):
        specs.append(data["spec"])
    result = data.get("result")
    if isinstance(result, dict) and isinstance(result.get("spec"), dict):
        specs.append(result["spec"])
    response = data.get("response")
    if isinstance(response, dict) and isinstance(response.get("spec"), dict):
        specs.append(response["spec"])

    for spec in specs:
        code = spec.get("code")
        if isinstance(code, str) and len(code) > 900:
            spec["code_excerpt"] = code[:900] + f"\n... ({len(code) - 900} chars omitted)"
            spec["code"] = "<omitted from probe transcript; see prior submit_skidl_code call>"


def _json_dump_for_model(data: dict, *, original_text_len: int) -> str:
    """Return valid JSON no larger than MAX_TOOL_RESULT_CHARS where possible."""

    text = json.dumps(data, indent=1)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    trimmed = _trim_json_value(data, max_str=700, max_items=14)
    if isinstance(trimmed, dict):
        trimmed["_probe_compacted_from_chars"] = original_text_len
    text = json.dumps(trimmed, indent=1)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    trimmed = _trim_json_value(data, max_str=260, max_items=8)
    if isinstance(trimmed, dict):
        trimmed["_probe_compacted_from_chars"] = original_text_len
        trimmed["_probe_compaction_note"] = (
            "Large get_run/get_job response compacted by the probe; artifacts were "
            "spooled to disk when present."
        )
    text = json.dumps(trimmed, indent=1)
    if len(text) <= MAX_TOOL_RESULT_CHARS:
        return text
    summary = {
        "_probe_compacted_from_chars": original_text_len,
        "_probe_compaction_note": "Response too large after recursive trimming.",
        "keys": sorted(data.keys()),
    }
    for key in ("run_id", "job_id", "id", "status", "hint"):
        if key in data:
            summary[key] = data[key]
    return json.dumps(summary, indent=1)


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
            # Strip file content blobs that bloat responses
            for arts_key in ("artifacts", "_artifact_paths"):
                arts = container.get(arts_key)
                if isinstance(arts, dict) and arts:
                    artifacts_dir.mkdir(parents=True, exist_ok=True)
                    spooled = {}
                    for fname, content in arts.items():
                        if isinstance(content, str) and len(content) > 500:
                            (artifacts_dir / fname).write_text(content)
                            spooled[fname] = f"<file saved to disk, {len(content)} bytes>"
                        else:
                            spooled[fname] = content
                    container[arts_key] = spooled
            container.pop("stderr", None)
            # The job result echoes the full spec twice; drop one copy.
            if container is not data and isinstance(container.get("spec"), dict) \
                    and container.get("spec") == data.get("spec"):
                container["spec"] = "<same as top-level spec, omitted>"
            _compact_run_spec_for_probe(data)
            text = _json_dump_for_model(data, original_text_len=len(text))
    if len(text) > MAX_TOOL_RESULT_CHARS and name not in ("get_run", "get_job"):
        half = MAX_TOOL_RESULT_CHARS // 2
        text = text[:half] + f"\n...[{len(text)-MAX_TOOL_RESULT_CHARS} chars truncated]...\n" + text[-half:]
    return text


def _extract_mcp_result(tool_name: str, text: str) -> dict | None:
    if tool_name not in ("get_job", "get_run"):
        return None
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return None
    if tool_name == "get_job" and isinstance(data.get("result"), dict):
        return data["result"]
    if tool_name == "get_run" and isinstance(data.get("response"), dict):
        return data["response"]
    if any(key in data for key in ("status", "ok", "exceptions", "metrics")):
        return data
    return None


def _result_summary(result: dict | None) -> dict:
    if not isinstance(result, dict):
        return {}
    exceptions = result.get("exceptions") or []
    metrics = result.get("metrics") or {}
    exception_codes = result.get("exception_codes")
    if not isinstance(exception_codes, list):
        exception_codes = [
            exc.get("code")
            for exc in exceptions
            if isinstance(exc, dict) and exc.get("code")
        ]
    return {
        "run_id": result.get("run_id"),
        "status": result.get("status"),
        "ok": result.get("ok"),
        "stage": result.get("stage"),
        "manufacturable": metrics.get("manufacturable"),
        "manufacturing_complete": metrics.get("manufacturing_complete"),
        "exception_codes": exception_codes,
    }


def _terminal_status(status: str | None) -> bool:
    return status in {"succeeded", "succeeded_with_warnings", "failed", "timeout", "crashed"}


def _needs_resubmission(result: dict | None) -> bool:
    if not isinstance(result, dict):
        return False
    status = result.get("status")
    nested = result.get("result")
    if isinstance(nested, dict):
        if nested.get("ok") is False:
            return True
        if nested.get("status") in {"failed", "timeout", "crashed"}:
            return True
        metrics = nested.get("metrics")
        if isinstance(metrics, dict) and metrics.get("manufacturable") is False:
            return True
    return status in {"failed", "timeout", "crashed"} and result.get("ok") is False


def _final_report_block_reason(
    job_results: dict[str, dict],
    last_result: dict | None,
    *,
    submissions: int = 0,
    max_submissions: int = DEFAULT_MAX_SUBMISSIONS,
) -> str:
    for job_id, result in sorted(job_results.items()):
        status = result.get("status")
        if status in {"queued", "running"}:
            return f"job {job_id} is still {status}; poll get_job() until terminal"
    if isinstance(last_result, dict):
        status = last_result.get("status")
        if status and not _terminal_status(status):
            return f"last job status is {status}; poll get_job() until terminal"
    if submissions < max_submissions:
        for job_id, result in sorted(job_results.items()):
            if _needs_resubmission(result):
                return (
                    f"job {job_id} failed and {max_submissions - submissions} "
                    "submit_skidl_code attempt(s) remain; edit the SKiDL code "
                    "using the latest exceptions and resubmit"
                )
        if _needs_resubmission(last_result):
            return (
                "latest MCP result failed and "
                f"{max_submissions - submissions} submit_skidl_code attempt(s) "
                "remain; edit the SKiDL code using the latest exceptions and resubmit"
            )
    return ""


def _is_final_report_text(content: str | None) -> bool:
    return bool(FINAL_REPORT_RE.match((content or "").strip()))


def _harvest_outstanding_jobs(
    mcp: MCPClient,
    job_results: dict[str, dict],
    artifacts_dir: Path,
    call_log,
    *,
    max_wait_s: float = 180.0,
) -> tuple[dict | None, dict[str, dict]]:
    """Poll queued/running jobs after the model stops so summaries are truthful."""

    pending = {
        job_id
        for job_id, result in job_results.items()
        if result.get("status") in {"queued", "running"}
    }
    if not pending:
        return None, {}

    harvested: dict[str, dict] = {}
    last_result: dict | None = None
    deadline = time.time() + max_wait_s
    while pending and time.time() <= deadline:
        for job_id in list(sorted(pending)):
            try:
                text = mcp.call_tool("get_job", {"job_id": job_id})
            except Exception as exc:
                harvested[job_id] = {"error": str(exc)}
                pending.remove(job_id)
                continue
            result = _extract_mcp_result("get_job", text)
            if isinstance(result, dict):
                job_results[job_id] = result
                last_result = result
                harvested[job_id] = _result_summary(result)
                if _terminal_status(result.get("status")):
                    pending.remove(job_id)
                    run_id = result.get("run_id")
                    if run_id:
                        try:
                            run_text = mcp.call_tool("get_run", {"run_id": run_id})
                            shrink_result("get_run", run_text, artifacts_dir)
                        except Exception as exc:
                            harvested[job_id]["get_run_error"] = str(exc)
            else:
                harvested[job_id] = {"status": "unparseable"}
            call_log.write(
                "post_cap get_job "
                f"args={{\"job_id\": \"{job_id}\"}} -> ok ({len(text)} chars)\n"
            )
            call_log.flush()
        if pending:
            time.sleep(min(POLL_SPACING_S, max(0.0, deadline - time.time())))

    for job_id in pending:
        harvested.setdefault(job_id, {"status": "still_running_after_harvest"})
    return last_result, harvested


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="meta-llama/llama-4-maverick")
    ap.add_argument("--server", required=True)
    ap.add_argument("--token", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--request", default=USER_REQUEST)
    ap.add_argument("--no-system-prompt", action="store_true",
                    help="User prompt only — no system message. Tests raw MCP discoverability.")
    ap.add_argument("--llm-timeout-s", type=float, default=DEFAULT_LLM_TIMEOUT_S,
                    help="Per OpenRouter request timeout in seconds.")
    ap.add_argument("--max-wall-s", type=float, default=DEFAULT_MAX_WALL_S,
                    help="Hard wall-clock budget for one probe; <=0 disables.")
    ap.add_argument("--max-submissions", type=int, default=DEFAULT_MAX_SUBMISSIONS,
                    help="Maximum submit_skidl_code calls before forcing final report.")
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
    for mcp_attempt in range(3):
        try:
            init = mcp.connect()
            break
        except Exception as exc:
            print(f"MCP connect attempt {mcp_attempt+1} failed: {exc}", flush=True)
            if mcp_attempt < 2:
                time.sleep(5)
            else:
                print("Cannot connect to MCP server after 3 attempts", file=sys.stderr)
                return 1
    instructions = init.get("instructions", "(none)")
    tools = openai_tools(mcp.list_tools(), mcp.list_resources())

    if args.no_system_prompt:
        messages = [
            {"role": "system", "content": f"Connected service: {instructions}"},
            {"role": "user", "content": args.request},
        ]
    else:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(instructions=instructions)},
            {"role": "user", "content": args.request},
        ]
    initial_tool_prompt = (
        "Before coding, read eda://guide/skidl. If the board uses mechanical "
        "connectors, jacks, switches, pots, USB, or panel-facing parts, also "
        "read eda://guide/parts so you choose footprint style deliberately."
    )
    messages.append({"role": "user", "content": initial_tool_prompt})

    or_http = httpx.Client(
        timeout=httpx.Timeout(float(args.llm_timeout_s), connect=30.0)
    )
    usage_totals = {"prompt_tokens": 0, "completion_tokens": 0}
    last_poll_at = 0.0
    started = time.time()
    final_report = None
    final_report_valid = False
    premature_final_reason = ""
    job_results: dict[str, dict] = {}
    last_mcp_result: dict | None = None
    nudges = 0
    submissions = 0

    for turn in range(MAX_MODEL_TURNS):
        if args.max_wall_s > 0 and time.time() - started >= args.max_wall_s:
            final_report = f"(probe wall-clock limit reached after {args.max_wall_s:.0f}s)"
            break
        for attempt in range(3):
            try:
                request_timeout = float(args.llm_timeout_s)
                if args.max_wall_s > 0:
                    remaining = args.max_wall_s - (time.time() - started)
                    if remaining <= 0:
                        final_report = (
                            f"(probe wall-clock limit reached after "
                            f"{args.max_wall_s:.0f}s)"
                        )
                        break
                    request_timeout = max(1.0, min(request_timeout, remaining))
                resp = or_http.post(
                    "https://openrouter.ai/api/v1/chat/completions",
                    headers={"Authorization": f"Bearer {api_key}"},
                    json={
                        "model": args.model,
                        "messages": messages,
                        "tools": tools,
                        "temperature": 0.2,
                    },
                    timeout=httpx.Timeout(request_timeout, connect=min(30.0, request_timeout)),
                )
                resp.raise_for_status()
                body = resp.json()
                break
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                detail = exc.response.text[:300]
                print(f"[{turn:02d}] OpenRouter HTTP {code} (attempt {attempt+1}): {detail}", flush=True)
                if code == 429 or code >= 500:
                    time.sleep(10 * (attempt + 1))
                    continue
                if code == 400 and attempt < 2:
                    # 400 often means our message history is malformed —
                    # drop the last assistant+tool messages and retry
                    while messages and messages[-1]["role"] in ("assistant", "tool"):
                        messages.pop()
                    messages.append({"role": "user", "content": "Continue with the design task. Use tool calls."})
                    continue
                final_report = f"(OpenRouter HTTP {code}: {detail})"
                break
            except json.JSONDecodeError as exc:
                detail = resp.text[:300].replace("\n", "\\n") if "resp" in locals() else ""
                print(
                    (
                        f"[{turn:02d}] OpenRouter JSON decode error "
                        f"(attempt {attempt+1}): {exc}; body={detail}"
                    ),
                    flush=True,
                )
                time.sleep(5 * (attempt + 1))
                continue
            except httpx.HTTPError as exc:
                print(f"[{turn:02d}] HTTP error (attempt {attempt+1}): {exc}", flush=True)
                time.sleep(5 * (attempt + 1))
                continue
        else:
            final_report = "(OpenRouter unreachable after 3 retries)"
            break
        if final_report is not None:
            break
        if "choices" not in body:
            print(f"OpenRouter error: {json.dumps(body)[:500]}", file=sys.stderr)
            final_report = f"(OpenRouter returned no choices: {json.dumps(body)[:200]})"
            break
        for k in usage_totals:
            usage_totals[k] += body.get("usage", {}).get(k, 0)
        msg = body["choices"][0]["message"]
        messages.append(msg)

        tool_calls = msg.get("tool_calls") or []
        content = (msg.get("content") or "").strip()

        if not tool_calls:
            if _is_final_report_text(content):
                premature_final_reason = _final_report_block_reason(
                    job_results,
                    last_mcp_result,
                    submissions=submissions,
                    max_submissions=args.max_submissions,
                )
                if premature_final_reason:
                    messages.append({
                        "role": "user",
                        "content": (
                            "You cannot give the final report yet: "
                            f"{premature_final_reason}. Continue with tool "
                            "calls. Read the latest exception details, edit "
                            "the SKiDL code, and call submit_skidl_code() "
                            "again until the design is manufacturable or the "
                            "probe submission cap is reached."
                        ),
                    })
                    nudges += 1
                    continue
                final_report = content
                final_report_valid = True
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
                if name == "submit_skidl_code" and submissions >= args.max_submissions:
                    result = (
                        "TOOL ERROR: probe max submit_skidl_code calls reached "
                        f"({args.max_submissions}). Do not submit again; write "
                        "FINAL REPORT from the latest terminal MCP result."
                    )
                else:
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
                        if name == "submit_skidl_code":
                            submissions += 1
                    except Exception as exc:
                        result = f"TOOL ERROR: {exc}"
            parsed_result = _extract_mcp_result(name, result)
            if parsed_result is not None:
                last_mcp_result = parsed_result
                job_id = None
                if isinstance(arguments, dict):
                    job_id = arguments.get("job_id")
                job_id = job_id or parsed_result.get("job_id") or parsed_result.get("id")
                if job_id:
                    job_results[str(job_id)] = parsed_result
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
            if (
                name == "get_job"
                and submissions >= args.max_submissions
                and isinstance(parsed_result, dict)
                and _terminal_status(parsed_result.get("status"))
            ):
                messages.append({
                    "role": "user",
                    "content": (
                        "You have reached the probe submission cap. Do not "
                        "submit another design. Finish now with a FINAL REPORT "
                        "that states whether the latest result is manufacturable "
                        "and summarizes the blocking exceptions."
                    ),
                })

    harvest_last_result, post_cap_results = _harvest_outstanding_jobs(
        mcp,
        job_results,
        artifacts_dir,
        call_log,
    )
    if harvest_last_result is not None:
        last_mcp_result = harvest_last_result

    wall = time.time() - started
    call_log.close()
    (out / "transcript.json").write_text(json.dumps(messages, indent=1))
    summary = {
        "model": args.model,
        "turns_used": turn + 1,
        "wall_time_s": round(wall, 1),
        "usage": usage_totals,
        "submissions": submissions,
        "max_submissions": args.max_submissions,
        "finished_with_report": _is_final_report_text(final_report),
        "final_report_valid": final_report_valid,
        "premature_final_reason": premature_final_reason,
        "last_mcp_result": _result_summary(last_mcp_result),
        "post_cap_results": post_cap_results,
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
