"""Tests for the direct hosted MCP client helper."""

from __future__ import annotations

import json

import httpx

from corpus.mcp_direct import DirectMCPClient, _content_text, _json_arg


def _sse(payload):
    return "event: message\n" f"data: {json.dumps(payload)}\n\n"


def test_direct_client_initializes_and_calls_tool():
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        requests.append((payload, dict(request.headers)))
        if payload["method"] == "initialize":
            return httpx.Response(
                200,
                headers={"mcp-session-id": "session-1"},
                text=_sse({"jsonrpc": "2.0", "id": payload["id"], "result": {}}),
            )
        if payload["method"] == "notifications/initialized":
            return httpx.Response(202, text="")
        assert request.headers["mcp-session-id"] == "session-1"
        return httpx.Response(
            200,
            text=_sse({
                "jsonrpc": "2.0",
                "id": payload["id"],
                "result": {"content": [{"text": "{\"ok\": true}"}]},
            }),
        )

    client = DirectMCPClient("https://example.test/mcp", "token")
    client.http = httpx.Client(transport=httpx.MockTransport(handler))

    client.connect()
    result = client.call_tool("get_job", {"job_id": "job-1"})

    assert json.loads(_content_text(result)) == {"ok": True}
    assert requests[0][0]["method"] == "initialize"
    assert requests[-1][0] == {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "get_job", "arguments": {"job_id": "job-1"}},
    }


def test_json_arg_requires_object():
    assert _json_arg('{"query": "MCP9808"}') == {"query": "MCP9808"}

    try:
        _json_arg("[1, 2]")
    except SystemExit as exc:
        assert "object" in str(exc)
    else:
        raise AssertionError("expected SystemExit")


def test_json_arg_reads_at_file(tmp_path):
    args = tmp_path / "args.json"
    args.write_text('{"job_id": "job-1"}')

    assert _json_arg(f"@{args}") == {"job_id": "job-1"}
