"""Tests for MCP-facing generate policy behavior."""

from __future__ import annotations

from mcp_server import server
from mcp_server.policy import decision_kind
from mcp_server.pipeline import DesignResponse
from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity


def trivial_spec() -> dict:
    return {
        "board": {"name": "policy-smoke", "outline_hint_mm": [25.0, 20.0]},
        "parts": [
            {
                "ref": "U1",
                "lib": None,
                "part": None,
                "value": "CUSTOM",
                "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                "pins": [
                    {"num": "1", "name": "VCC", "func": "power_in"},
                    {"num": "2", "name": "GND", "func": "power_in"},
                ],
            },
            {
                "ref": "C1",
                "lib": None,
                "part": None,
                "value": "100nF",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
                "pins": [
                    {"num": "1", "name": "1", "func": "passive"},
                    {"num": "2", "name": "2", "func": "passive"},
                ],
            },
        ],
        "nets": [
            {"name": "VCC", "power": True, "pins": ["U1.VCC", "C1.1"]},
            {"name": "GND", "power": True, "pins": ["U1.GND", "C1.2"]},
        ],
    }


def advisory_exception() -> DesignException:
    return DesignException(
        id="e-high-congestion",
        code=ExcCode.HIGH_CONGESTION,
        severity=Severity.ADVISORY,
        message="layout congestion is high",
        subject={"net": "VCC"},
        candidates=[
            Candidate(
                id="c1",
                action=ActionType.ACCEPT_ADVISORY,
                params={},
                human_summary="Accept advisory.",
            )
        ],
    )


def overlap_exception() -> DesignException:
    return DesignException(
        id="e-layout-overlap-1",
        code=ExcCode.LAYOUT_OVERLAP,
        severity=Severity.ERROR,
        message="parts overlap",
        subject={"pair": ["U1", "C1"]},
        candidates=[
            Candidate(
                id="c1",
                action=ActionType.SCALE_OUTLINE,
                params={"area_factor": 1.25},
                human_summary="Grow the board.",
            )
        ],
    )


def test_decision_kind_classifies_code_errors_before_no_candidate():
    exc = DesignException(
        id="e-code",
        code=ExcCode.CODE_EXEC_ERROR,
        severity=Severity.FATAL,
        message="pin not found",
        candidates=[],
    )

    assert decision_kind([exc]) == "code_authoring_error"


def test_decision_kind_classifies_engine_failures_before_no_candidate():
    exc = DesignException(
        id="e-crash",
        code=ExcCode.ENGINE_CRASH,
        severity=Severity.FATAL,
        message="worker exited with status -11",
        candidates=[],
    )

    assert decision_kind([exc]) == "engine_failure"


def test_decision_kind_classifies_tool_failures_before_no_candidate():
    exc = DesignException(
        id="e-route-unavailable",
        code=ExcCode.ROUTE_UNAVAILABLE,
        severity=Severity.ADVISORY,
        message="router unavailable",
        candidates=[],
    )

    assert decision_kind([exc]) == "tool_unavailable"


def test_decision_kind_classifies_post_artifact_failure_as_tool_failure():
    exc = DesignException(
        id="e-post-artifact",
        code=ExcCode.POST_ARTIFACT_FAILURE,
        severity=Severity.ERROR,
        message="PCB artifacts exist but finalization failed",
        candidates=[],
    )

    assert decision_kind([exc]) == "tool_unavailable"


def test_decision_kind_classifies_floorplan_intent_as_mechanical_constraint():
    exc = DesignException(
        id="e-floorplan-intent",
        code=ExcCode.DESIGN_MISSING_FEATURE,
        severity=Severity.ERROR,
        message="placement needs explicit floorplan intent",
        subject={"feature": "placement_floorplan_intent"},
        candidates=[
            Candidate(
                id="c1",
                action=ActionType.REGENERATE,
                params={"required_intent": ["EDA_FLOORPLAN.edge_anchors"]},
                human_summary="Add floorplan intent and regenerate.",
            )
        ],
    )

    assert decision_kind([exc]) == "mechanical_constraint"


def test_generate_policy_auto_applies_advisory(tmp_path, monkeypatch):
    calls = []

    def fake_run_pipeline(spec, out_dir, **kwargs):
        calls.append(CircuitSpec.model_validate(spec))
        if len(calls) == 1:
            return DesignResponse(
                run_id="run-1",
                ok=True,
                status="succeeded_with_warnings",
                exceptions=[advisory_exception()],
            )
        return DesignResponse(run_id="run-2", ok=True, status="succeeded")

    monkeypatch.setattr(server, "run_pipeline", fake_run_pipeline)

    out = server.generate_design(
        trivial_spec(),
        run_options={"out_dir": str(tmp_path), "record_telemetry": False},
        policy={"auto_apply": "advisory_only", "max_internal_corrections": 1},
    )

    assert out["status"] == "succeeded"
    assert out["decision_required"] is False
    assert out["corrections_applied"][0]["action"] == "accept_advisory"
    assert advisory_exception().waiver_key() in calls[1].waivers


def test_generate_policy_returns_decision_required_for_mechanical_choice(tmp_path, monkeypatch):
    def fake_run_pipeline(spec, out_dir, **kwargs):
        return DesignResponse(
            run_id="run-1",
            ok=False,
            status="failed",
            exceptions=[overlap_exception()],
        )

    monkeypatch.setattr(server, "run_pipeline", fake_run_pipeline)

    out = server.generate_design(
        trivial_spec(),
        run_options={"out_dir": str(tmp_path), "record_telemetry": False},
        policy={"auto_apply": "safe", "max_internal_corrections": 2},
    )

    assert out["status"] == "failed"
    assert out["decision_required"] is True
    assert out["decision_kind"] == "mechanical_constraint"
    assert out["recommended_next_tool"] == "apply_correction"
