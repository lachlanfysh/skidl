"""Tests for agent UX probe guardrails."""

from __future__ import annotations

import json

from corpus.mcp_ux_probe import (
    _extract_mcp_result,
    _final_report_block_reason,
    _result_summary,
)


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
