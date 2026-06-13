"""Tests for agent UX probe guardrails."""

from __future__ import annotations

import json
from io import StringIO

from corpus import mcp_ux_probe
from corpus.mcp_ux_probe import (
    MCPClient,
    _extract_mcp_result,
    _final_report_block_reason,
    _harvest_outstanding_jobs,
    _is_final_report_text,
    _result_summary,
)


def test_mcp_client_reconnects_once_after_lost_session():
    class FakeClient(MCPClient):
        def __init__(self):
            self.headers = {"Mcp-Session-Id": "stale"}
            self.rpc_calls = []
            self.connect_calls = 0

        def _rpc_once(self, method, params=None):
            self.rpc_calls.append((method, params, self.headers.get("Mcp-Session-Id")))
            if len(self.rpc_calls) == 1:
                raise RuntimeError("404 Not Found: session not found")
            return {"ok": True, "session": self.headers.get("Mcp-Session-Id")}

        def connect(self):
            self.connect_calls += 1
            assert "Mcp-Session-Id" not in self.headers
            self.headers["Mcp-Session-Id"] = "fresh"
            return {"instructions": "ok"}

    client = FakeClient()

    assert client._rpc("tools/list", {"x": 1}) == {"ok": True, "session": "fresh"}
    assert client.connect_calls == 1
    assert client.rpc_calls == [
        ("tools/list", {"x": 1}, "stale"),
        ("tools/list", {"x": 1}, "fresh"),
    ]


def test_extract_mcp_result_from_get_job_payload():
    payload = {
        "result": {
            "job_id": "job-1",
            "run_id": "run-1",
            "status": "running",
            "ok": False,
        }
    }

    assert _extract_mcp_result("get_job", json.dumps(payload)) == payload["result"]
    assert _extract_mcp_result("search_kicad", json.dumps(payload)) is None


def test_final_report_blocked_while_job_running():
    reason = _final_report_block_reason(
        {"job-1": {"status": "running"}},
        {"status": "running"},
    )

    assert "job job-1 is still running" in reason


def test_final_report_detection_accepts_markdown_prefixes():
    assert _is_final_report_text("FINAL REPORT: done")
    assert _is_final_report_text("**FINAL REPORT:** done")
    assert _is_final_report_text("## FINAL REPORT")
    assert not _is_final_report_text("I am still working")


def test_result_summary_keeps_manufacturing_and_exception_signal():
    summary = _result_summary({
        "run_id": "run-1",
        "status": "failed",
        "ok": False,
        "metrics": {"manufacturable": False, "manufacturing_complete": False},
        "exceptions": [{"code": "LAYOUT_OVERLAP"}, {"code": "DRC_CLEARANCE"}],
    })

    assert summary == {
        "run_id": "run-1",
        "status": "failed",
        "ok": False,
        "manufacturable": False,
        "manufacturing_complete": False,
        "exception_codes": ["LAYOUT_OVERLAP", "DRC_CLEARANCE"],
    }


def test_harvest_outstanding_jobs_polls_terminal_and_fetches_artifacts(tmp_path):
    class FakeMCP:
        def __init__(self):
            self.calls = []

        def call_tool(self, name, arguments):
            self.calls.append((name, arguments))
            if name == "get_job":
                return json.dumps({
                    "result": {
                        "job_id": arguments["job_id"],
                        "run_id": "run-1",
                        "status": "failed",
                        "ok": False,
                        "metrics": {"manufacturable": False},
                        "exceptions": [{"code": "LAYOUT_OVERLAP"}],
                    }
                })
            if name == "get_run":
                return json.dumps({
                    "response": {"run_id": "run-1", "status": "failed"},
                    "artifacts": {"board.kicad_pcb": "x" * 600},
                })
            raise AssertionError(name)

    mcp = FakeMCP()
    job_results = {"job-1": {"status": "running"}}
    call_log = StringIO()

    last, harvested = _harvest_outstanding_jobs(
        mcp,
        job_results,
        tmp_path,
        call_log,
        max_wait_s=0.1,
    )

    assert last["status"] == "failed"
    assert job_results["job-1"]["run_id"] == "run-1"
    assert harvested["job-1"]["exception_codes"] == ["LAYOUT_OVERLAP"]
    assert (tmp_path / "board.kicad_pcb").exists()
    assert ("get_run", {"run_id": "run-1"}) in mcp.calls
    assert "post_cap get_job" in call_log.getvalue()


def test_main_summarizes_openrouter_malformed_json(tmp_path, monkeypatch):
    class FakeMCP:
        def __init__(self, url, token):
            pass

        def connect(self):
            return {"instructions": "test instructions"}

        def list_tools(self):
            return []

        def list_resources(self):
            return []

    class BadJSONResponse:
        text = "<html>not json</html>"
        content = text.encode()

        def raise_for_status(self):
            return None

        def json(self):
            raise json.JSONDecodeError("Expecting value", self.text, 0)

    class FakeHTTP:
        def __init__(self, timeout):
            self.timeout = timeout

        def post(self, *args, **kwargs):
            return BadJSONResponse()

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(mcp_ux_probe, "MCPClient", FakeMCP)
    monkeypatch.setattr(mcp_ux_probe.httpx, "Client", FakeHTTP)
    monkeypatch.setattr(mcp_ux_probe.time, "sleep", lambda _: None)
    monkeypatch.setattr(
        mcp_ux_probe.sys,
        "argv",
        [
            "mcp_ux_probe",
            "--server",
            "https://example.invalid/mcp",
            "--token",
            "test-token",
            "--out",
            str(tmp_path),
        ],
    )

    assert mcp_ux_probe.main() == 0
    summary = json.loads((tmp_path / "summary.json").read_text())

    assert summary["finished_with_report"] is False
    assert "OpenRouter unreachable after 3 retries" in summary["final_report"]
