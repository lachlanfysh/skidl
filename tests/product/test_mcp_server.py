"""Tests for the MCP server pipeline layer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import mcp_server.engine_worker as engine_worker_mod
import mcp_server.pipeline as pipeline_mod
from mcp_server.exception_mapper import (
    classify_routing_failure,
    crash_exception,
    enrich_routing_failure_exceptions,
    layout_exceptions,
    order_exceptions_for_agent,
    suppress_waived,
    timeout_exception,
)
from mcp_server.engine_worker import (
    PREVIEW_BACKGROUND,
    _add_layout_mockup_preview,
    _brand_preview_png,
    _brand_preview_svg,
    _code_exception_from_exec,
    _code_exception_from_syntax,
    _circuit_to_spec_dict,
    _drc_to_exceptions,
    _drop_clean_manufacturing_advisories,
    _ensure_kicad_project_profile,
    _exec_skidl,
    _export_dsn_with_pcbnew,
    _find_kicad_python,
    _floorplan_intent_preflight_exception,
    _footprint_missing_exception,
    _freerouting_jar_path,
    _generate_board_previews,
    _generate_pipeline_previews,
    _import_ses_with_pcbnew,
    _manufacturing_output_exception,
    _metrics,
    _missing_manufacturing_outputs,
    _outline_for_spec,
    _route_pcb,
    _run_skidl_code,
    _run_pcbnew_child,
    _skidl_layout_intent_advisories,
    _write_inline_footprints,
    _write_layout_mockup_svg,
)
from mcp_server.pipeline import (
    _attach_layout_quality,
    _enrich_code_exceptions,
    _infer_crash_stage,
    _manufacturing_metrics,
    DesignResponse,
    run_pipeline,
    run_pipeline_code,
)
from mcp_server.layout_quality import build_layout_quality, write_layout_quality
from mcp_server.runs import RunStore
from mcp_server import worker as worker_mod
from mcp_server.worker import (
    _job_status_from_result,
    _lcsc_refs_in_spec,
    _restore_lcsc_asset,
)
from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity
from skidl.layout.constraints import BoardOutline
from skidl.layout.writer import PlacedPart


SYM_DIR = "/usr/share/kicad/symbols"
FP_DIR = "/usr/share/kicad/footprints"
needs_kicad = pytest.mark.skipif(
    not (os.path.isdir(SYM_DIR) and os.path.isdir(FP_DIR)),
    reason="KiCad 9 symbol/footprint libraries not installed",
)


def trivial_spec() -> dict:
    return {
        "board": {"name": "mcp-smoke", "outline_hint_mm": [30.0, 20.0]},
        "parts": [
            {
                "ref": "R1",
                "lib": None,
                "part": "R",
                "value": "10K",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
                "pins": [
                    {"num": "1", "name": "A"},
                    {"num": "2", "name": "B"},
                ],
            },
            {
                "ref": "R2",
                "lib": None,
                "part": "R",
                "value": "10K",
                "footprint": "Resistor_SMD:R_0603_1608Metric",
                "pins": [
                    {"num": "1", "name": "A"},
                    {"num": "2", "name": "B"},
                ],
            },
            {
                "ref": "J1",
                "lib": "Connector_Generic",
                "part": "Conn_01x03",
                "value": "IO",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
            },
        ],
        "nets": [
            {"name": "VCC", "power": True, "pins": ["J1.1", "R1.A"]},
            {"name": "SIG", "pins": ["J1.2", "R1.B", "R2.A"]},
            {"name": "GND", "power": True, "pins": ["J1.3", "R2.B"]},
        ],
    }


class TestRunStore:
    def test_round_trip(self, tmp_path):
        store = RunStore(tmp_path)
        spec = CircuitSpec.model_validate(trivial_spec())
        exc = DesignException(
            id="e1",
            code=ExcCode.HIGH_CONGESTION,
            severity=Severity.ADVISORY,
            message="high congestion",
            subject={"congestion_score": 42.0},
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.ACCEPT_ADVISORY,
                    human_summary="accept",
                )
            ],
        )
        response = {"run_id": "run-1", "ok": False}

        store.save("run-1", spec, [exc], response)

        assert store.load_spec("run-1") == spec
        assert store.load_exceptions("run-1")[0] == exc
        assert store.load_response("run-1") == response
        assert (tmp_path / "run-1" / "spec.json").exists()
        assert (tmp_path / "run-1" / "exceptions.json").exists()
        assert (tmp_path / "run-1" / "response.json").exists()


class TestWorkerStatus:
    def test_worker_preserves_failed_reviewable_status(self):
        assert _job_status_from_result(
            {"status": "failed_reviewable", "ok": False}
        ) == "failed_reviewable"


class TestLayoutQuality:
    def test_build_layout_quality_flags_clustered_oversized_board(self):
        layout = {
            "ok": True,
            "outline": {"width_mm": 100.0, "height_mm": 80.0},
            "placed_parts": [
                {"ref": "U1", "x_mm": 48.0, "y_mm": 38.0},
                {"ref": "U2", "x_mm": 50.0, "y_mm": 40.0},
                {"ref": "J1", "x_mm": 52.0, "y_mm": 42.0},
                {"ref": "R1", "x_mm": 49.0, "y_mm": 41.0},
                {"ref": "C1", "x_mm": 51.0, "y_mm": 39.0},
            ],
            "validation": {"ok": True},
        }
        quality = build_layout_quality(
            run_id="clustered",
            status="succeeded",
            stage="complete",
            ok=True,
            layout=layout,
            metrics={
                "manufacturable": True,
                "manufacturing_complete": True,
                "board_area_mm2": 8000.0,
            },
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}
        assert "LOW_PART_SPREAD" in issue_codes
        assert "UNUSED_OUTLINE_REGION" in issue_codes
        assert "OVERSIZED_BOARD_OUTLINE" in issue_codes
        assert quality["gates"]["manufacturable"] is True
        assert quality["gates"]["visual_review_ready"] is True
        assert quality["gates"]["product_layout_ok"] is False

    def test_build_layout_quality_recommends_shrink_for_sparse_auto_outline(self):
        quality = build_layout_quality(
            run_id="sparse-auto-outline",
            status="succeeded",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 120.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 20.0, "y_mm": 20.0},
                    {"ref": "J1", "x_mm": 26.0, "y_mm": 20.0},
                    {"ref": "R1", "x_mm": 22.0, "y_mm": 24.0},
                    {"ref": "R2", "x_mm": 24.0, "y_mm": 24.0},
                    {"ref": "C1", "x_mm": 23.0, "y_mm": 27.0},
                ],
                "score": {
                    "footprint_envelope_area_ratio": 0.035,
                    "compact_outline_mm": {
                        "width": 24.0,
                        "height": 18.0,
                        "area": 432.0,
                    },
                    "compact_outline_area_ratio": 0.045,
                    "empty_margin_ratios": {
                        "left": 0.10,
                        "right": 0.72,
                        "top": 0.12,
                        "bottom": 0.61,
                    },
                    "max_empty_margin_ratio": 0.72,
                },
                "validation": {"ok": True},
            },
            metrics={
                "manufacturable": True,
                "manufacturing_complete": True,
                "board_area_mm2": 9600.0,
            },
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue = next(
            issue for issue in quality["issues"]
            if issue["code"] == "OVERSIZED_BOARD_OUTLINE"
        )

        assert quality["placement"]["outline_intent"] == "auto_or_unknown"
        assert quality["placement"]["compact_outline_area_ratio"] == pytest.approx(0.045)
        assert "shrinking" in issue["recommendation"]
        assert "Do not treat routing" in issue["recommendation"]

    def test_build_layout_quality_recommends_redistribution_for_fixed_outline(self):
        quality = build_layout_quality(
            run_id="fixed-outline-underused",
            status="succeeded",
            stage="placement_review",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 120.0, "height_mm": 80.0},
                "floorplan": {"outline": "explicit", "fixed_positions": 4},
                "placed_parts": [
                    {"ref": "SW1", "x_mm": 55.0, "y_mm": 38.0},
                    {"ref": "SW2", "x_mm": 60.0, "y_mm": 38.0},
                    {"ref": "J1", "x_mm": 58.0, "y_mm": 44.0},
                    {"ref": "J2", "x_mm": 63.0, "y_mm": 44.0},
                    {"ref": "U1", "x_mm": 59.0, "y_mm": 48.0},
                ],
                "score": {
                    "footprint_envelope_area_ratio": 0.04,
                    "compact_outline_mm": {
                        "width": 28.0,
                        "height": 22.0,
                        "area": 616.0,
                    },
                    "compact_outline_area_ratio": 0.064,
                    "empty_margin_ratios": {
                        "left": 0.42,
                        "right": 0.36,
                        "top": 0.40,
                        "bottom": 0.30,
                    },
                    "max_empty_margin_ratio": 0.42,
                },
                "validation": {"ok": True},
            },
            metrics={
                "manufacturable": True,
                "manufacturing_complete": True,
                "pipeline_goal": "placement_review",
                "board_area_mm2": 9600.0,
            },
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}
        issue = next(
            issue for issue in quality["issues"]
            if issue["code"] == "FIXED_OUTLINE_UNDERUSED"
        )

        assert "OVERSIZED_BOARD_OUTLINE" not in issue_codes
        assert quality["placement"]["outline_intent"] == "mechanical"
        assert "Do not shrink or grow" in issue["recommendation"]
        assert "redistribute UI" in issue["recommendation"]

    def test_build_layout_quality_blocks_validation_failures(self):
        quality = build_layout_quality(
            run_id="validation-fail",
            status="failed",
            stage="complete",
            ok=False,
            layout={
                "ok": False,
                "outline": {"width_mm": 60.0, "height_mm": 40.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 10.0, "y_mm": 10.0},
                    {"ref": "J1", "x_mm": 62.0, "y_mm": 10.0},
                ],
                "validation": {
                    "ok": False,
                    "overlaps": [["U1", "R1"]],
                    "outline_violations": ["J1"],
                    "keepout_violations": ["C1"],
                    "missing_refs": ["D1"],
                },
            },
            metrics={"manufacturable": False, "manufacturing_complete": False},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}

        assert {
            "LAYOUT_OVERLAP",
            "LAYOUT_OUTLINE_VIOLATION",
            "LAYOUT_KEEPOUT",
            "LAYOUT_MISSING_REF",
        }.issubset(issue_codes)
        assert quality["gates"]["visual_review_ready"] is True
        assert quality["gates"]["product_layout_ok"] is False

    def test_build_layout_quality_dedupes_validation_and_exception_issues(self):
        quality = build_layout_quality(
            run_id="dedupe-quality",
            status="failed",
            stage="complete",
            ok=False,
            exceptions=[
                DesignException(
                    id="overlap",
                    code=ExcCode.LAYOUT_OVERLAP,
                    severity=Severity.ERROR,
                    message="1 placement overlap remains",
                    subject={"pairs": [["U1", "J1"]], "count": 1},
                ),
                DesignException(
                    id="congestion",
                    code=ExcCode.HIGH_CONGESTION,
                    severity=Severity.ADVISORY,
                    message="layout congestion is high",
                    subject={"congestion_score": 64.0},
                ),
            ],
            layout={
                "ok": False,
                "outline": {"width_mm": 60.0, "height_mm": 40.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 10.0, "y_mm": 10.0},
                    {"ref": "J1", "x_mm": 10.5, "y_mm": 10.0},
                ],
                "validation": {
                    "ok": False,
                    "overlaps": [["U1", "J1"]],
                },
            },
            metrics={
                "congestion_score": 64.0,
                "manufacturable": False,
                "manufacturing_complete": False,
            },
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = [issue["code"] for issue in quality["issues"]]

        assert issue_codes.count("LAYOUT_OVERLAP") == 1
        assert issue_codes.count("HIGH_CONGESTION") == 1
        assert quality["gates"]["product_layout_ok"] is False

    def test_build_layout_quality_does_not_block_clean_routed_board_on_congestion_only(self):
        quality = build_layout_quality(
            run_id="advisory-only",
            status="succeeded_with_warnings",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 66.3, "height_mm": 32.8},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 6.0, "y_mm": 5.0},
                    {"ref": "U2", "x_mm": 32.0, "y_mm": 5.5},
                    {"ref": "U3", "x_mm": 60.0, "y_mm": 6.0},
                    {"ref": "U4", "x_mm": 8.0, "y_mm": 26.0},
                    {"ref": "U5", "x_mm": 34.0, "y_mm": 27.0},
                    {"ref": "U6", "x_mm": 58.0, "y_mm": 26.5},
                    {"ref": "U7", "x_mm": 31.0, "y_mm": 16.0},
                ],
                "validation": {"ok": True},
            },
            metrics={
                "congestion_score": 85.0,
                "manufacturable": True,
                "manufacturing_complete": True,
                "board_area_mm2": 2175.0,
            },
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        assert quality["issues"] == []
        assert quality["gates"]["manufacturable"] is True
        assert quality["gates"]["visual_review_ready"] is True
        assert quality["gates"]["product_layout_ok"] is True

    def test_build_layout_quality_keeps_congestion_actionable_before_manufacturing(self):
        quality = build_layout_quality(
            run_id="congestion-unrouted",
            status="succeeded_with_warnings",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 66.3, "height_mm": 32.8},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 6.0, "y_mm": 5.0},
                    {"ref": "U2", "x_mm": 32.0, "y_mm": 5.5},
                    {"ref": "U3", "x_mm": 60.0, "y_mm": 6.0},
                    {"ref": "U4", "x_mm": 8.0, "y_mm": 26.0},
                    {"ref": "U5", "x_mm": 34.0, "y_mm": 27.0},
                    {"ref": "U6", "x_mm": 58.0, "y_mm": 26.5},
                    {"ref": "U7", "x_mm": 31.0, "y_mm": 16.0},
                ],
                "validation": {"ok": True},
            },
            metrics={
                "congestion_score": 85.0,
                "manufacturable": False,
                "manufacturing_complete": False,
                "board_area_mm2": 2175.0,
            },
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        assert [issue["code"] for issue in quality["issues"]] == ["HIGH_CONGESTION"]
        assert quality["gates"]["product_layout_ok"] is False

    def test_worker_drops_clean_manufacturing_congestion_advisory(self):
        high = DesignException(
            id="e-high",
            code=ExcCode.HIGH_CONGESTION,
            severity=Severity.ADVISORY,
            message="layout congestion is high",
        )
        other = DesignException(
            id="e-review",
            code=ExcCode.PLACEMENT_REVIEW_ONLY,
            severity=Severity.ADVISORY,
            message="placement review only",
        )

        assert _drop_clean_manufacturing_advisories(
            [high, other],
            manufacturable=True,
        ) == [other]
        assert _drop_clean_manufacturing_advisories(
            [high, other],
            manufacturable=False,
        ) == [high, other]

    def test_build_layout_quality_surfaces_front_panel_trace_span(self):
        quality = build_layout_quality(
            run_id="front-panel-traces",
            status="succeeded_with_warnings",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 80.0, "height_mm": 30.0},
                "placed_parts": [
                    {"ref": "J1", "x_mm": 8.0, "y_mm": 14.0, "side": "front"},
                    {"ref": "U1", "x_mm": 72.0, "y_mm": 14.0, "side": "front"},
                ],
                "score": {
                    "front_panel_trace_count": 1,
                    "front_panel_trace_mm": 64.0,
                },
                "validation": {"ok": True},
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue = next(
            issue for issue in quality["issues"]
            if issue["code"] == "FRONT_PANEL_TRACE_SPAN"
        )

        assert issue["severity"] == "warning"
        assert issue["evidence"]["front_panel_trace_mm"] == pytest.approx(64.0)
        assert "move service electronics to the back" in issue["recommendation"]

    @pytest.mark.parametrize(
        "code",
        [
            ExcCode.DRC_COURTYARD,
            ExcCode.FOOTPRINT_MISSING,
            ExcCode.ROUTE_UNCONNECTED,
            ExcCode.LONG_POWER_NET,
            ExcCode.ENGINE_CRASH,
        ],
    )
    def test_build_layout_quality_promotes_exception_codes(self, code):
        severity = (
            Severity.ERROR
            if code
            in {
                ExcCode.FOOTPRINT_MISSING,
                ExcCode.ROUTE_UNCONNECTED,
                ExcCode.ENGINE_CRASH,
            }
            else Severity.ADVISORY
        )
        quality = build_layout_quality(
            run_id=f"quality-{code.value.lower()}",
            status="succeeded_with_warnings",
            stage="complete",
            ok=True,
            exceptions=[
                DesignException(
                    id="e1",
                    code=code,
                    severity=severity,
                    message=f"{code.value} happened",
                )
            ],
            layout={
                "ok": True,
                "outline": {"width_mm": 30.0, "height_mm": 20.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 10.0, "y_mm": 10.0},
                    {"ref": "J1", "x_mm": 20.0, "y_mm": 10.0},
                ],
                "validation": {"ok": True},
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue = next(issue for issue in quality["issues"] if issue["code"] == code.value)

        assert issue["severity"] in {"advisory", "warning", "error"}
        assert quality["gates"]["visual_review_ready"] is True
        assert quality["gates"]["product_layout_ok"] is (
            code
            not in {
                ExcCode.DRC_COURTYARD,
                ExcCode.FOOTPRINT_MISSING,
                ExcCode.ROUTE_UNCONNECTED,
                ExcCode.LONG_POWER_NET,
                ExcCode.ENGINE_CRASH,
            }
        )

    def test_build_layout_quality_flags_edge_and_grid_drift(self):
        quality = build_layout_quality(
            run_id="edge-grid-drift",
            status="succeeded",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "J1", "x_mm": 50.0, "y_mm": 40.0},
                    {"ref": "RV1", "x_mm": 20.0, "y_mm": 20.0},
                    {"ref": "RV2", "x_mm": 40.0, "y_mm": 23.5},
                ],
                "validation": {"ok": True},
                "intent_plan": {
                    "edge_anchors": [{"ref": "J1", "edge": "right"}],
                    "align_constraints": [
                        {"refs": ["RV1", "RV2"], "axis": "y", "value_mm": 20.0}
                    ],
                },
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}

        assert "EDGE_ANCHOR_OFF_EDGE" in issue_codes
        assert "GRID_ALIGNMENT_DRIFT" in issue_codes
        assert quality["gates"]["visual_review_ready"] is True
        assert quality["gates"]["product_layout_ok"] is False

    def test_build_layout_quality_marks_failed_preview_as_reviewable(self):
        quality = build_layout_quality(
            run_id="failed-but-previewable",
            status="failed",
            stage="placement_review",
            ok=False,
            exceptions=[
                DesignException(
                    id="e-overlap",
                    code=ExcCode.LAYOUT_OVERLAP,
                    severity=Severity.ERROR,
                    message="footprint overlaps remain",
                    subject={"count": 2},
                )
            ],
            layout={
                "ok": False,
                "outline": {"width_mm": 65.0, "height_mm": 40.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 20.0, "y_mm": 20.0},
                    {"ref": "J1", "x_mm": 20.0, "y_mm": 20.0},
                ],
                "validation": {"ok": False, "overlaps": ["J1/U1"]},
            },
            metrics={"manufacturable": False, "manufacturing_complete": False},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        assert quality["gates"]["visual_review_ready"] is True
        assert quality["gates"]["product_layout_ok"] is False
        assert quality["gates"]["reviewable_failure"] is True
        assert quality["review"]["state"] == "failed_reviewable"
        assert quality["review"]["requires_human_feedback"] is True
        assert "preview artifacts are available" in quality["review"]["message"]

    def test_build_layout_quality_respects_explicit_fixed_floorplan_over_inferred_grid(self):
        quality = build_layout_quality(
            run_id="fixed-floorplan-grid-default",
            status="succeeded",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "floorplan": {
                    "fixed_positions": 2,
                    "fixed_refs": ["RV1", "RV2"],
                },
                "placed_parts": [
                    {"ref": "RV1", "x_mm": 20.0, "y_mm": 20.0},
                    {"ref": "RV2", "x_mm": 40.0, "y_mm": 28.0},
                    {"ref": "U1", "x_mm": 70.0, "y_mm": 50.0},
                ],
                "validation": {"ok": True},
                "intent_plan": {
                    "align_constraints": [
                        {"refs": ["RV1", "RV2"], "axis": "y", "value_mm": 20.0}
                    ],
                },
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}

        assert "GRID_ALIGNMENT_DRIFT" not in issue_codes

    def test_build_layout_quality_keeps_explicit_floorplan_grid_drift(self):
        quality = build_layout_quality(
            run_id="explicit-floorplan-grid-drift",
            status="succeeded",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "floorplan": {
                    "fixed_positions": 2,
                    "fixed_refs": ["RV1", "RV2"],
                    "align_constraints": 1,
                },
                "placed_parts": [
                    {"ref": "RV1", "x_mm": 20.0, "y_mm": 20.0},
                    {"ref": "RV2", "x_mm": 40.0, "y_mm": 28.0},
                    {"ref": "U1", "x_mm": 70.0, "y_mm": 50.0},
                ],
                "validation": {"ok": True},
                "intent_plan": {
                    "align_constraints": [
                        {"refs": ["RV1", "RV2"], "axis": "y", "value_mm": 20.0}
                    ],
                },
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}

        assert "GRID_ALIGNMENT_DRIFT" in issue_codes

    def test_build_layout_quality_avoids_origin_false_positive_for_edge_header(self):
        quality = build_layout_quality(
            run_id="edge-header-origin",
            status="succeeded",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {"width_mm": 60.0, "height_mm": 30.0},
                "placed_parts": [
                    {
                        "ref": "J1",
                        "x_mm": 30.0,
                        "y_mm": 20.11,
                        "rot_deg": 90.0,
                        "footprint": (
                            "Connector_PinHeader_2.54mm:"
                            "PinHeader_1x03_P2.54mm_Horizontal"
                        ),
                    },
                    {"ref": "U1", "x_mm": 30.0, "y_mm": 12.0},
                ],
                "score": {"warnings": []},
                "validation": {"ok": True},
                "intent_plan": {
                    "intents": {
                        "J1": [
                            {"kind": "edge_connector", "priority": 90, "reasons": []},
                            {"kind": "mechanical_mating", "priority": 88, "reasons": []},
                        ],
                    },
                    "edge_anchors": [
                        {
                            "ref": "J1",
                            "edge": "bottom",
                            "offset_mm": 30.0,
                            "inset_mm": 0.0,
                            "rot_deg": 90.0,
                        }
                    ],
                },
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}

        assert "EDGE_ANCHOR_OFF_EDGE" not in issue_codes

    def test_build_layout_quality_uses_geometry_warning_for_edge_anchor(self):
        quality = build_layout_quality(
            run_id="edge-header-geometry-warning",
            status="failed",
            stage="complete",
            ok=False,
            layout={
                "ok": False,
                "outline": {"width_mm": 60.0, "height_mm": 30.0},
                "placed_parts": [
                    {
                        "ref": "J1",
                        "x_mm": 30.0,
                        "y_mm": 20.11,
                        "rot_deg": 90.0,
                        "footprint": (
                            "Connector_PinHeader_2.54mm:"
                            "PinHeader_1x03_P2.54mm_Horizontal"
                        ),
                    }
                ],
                "score": {
                    "warnings": ["J1: violates bottom-edge mating intent by 4.2mm"]
                },
                "validation": {"ok": True},
                "intent_plan": {
                    "edge_anchors": [
                        {"ref": "J1", "edge": "bottom", "rot_deg": 90.0}
                    ],
                },
            },
            metrics={"manufacturable": False, "manufacturing_complete": False},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}

        assert "EDGE_ANCHOR_OFF_EDGE" in issue_codes

    def test_build_layout_quality_uses_offset_outline_bounds(self):
        quality = build_layout_quality(
            run_id="offset-outline",
            status="succeeded",
            stage="complete",
            ok=True,
            layout={
                "ok": True,
                "outline": {
                    "x_min_mm": -16.0,
                    "y_min_mm": -4.0,
                    "x_max_mm": 50.0,
                    "y_max_mm": 28.0,
                    "width_mm": 66.0,
                    "height_mm": 32.0,
                },
                "placed_parts": [
                    {"ref": "J1", "x_mm": 50.0, "y_mm": 12.0},
                    {"ref": "U1", "x_mm": 16.0, "y_mm": 12.0},
                ],
                "validation": {"ok": True},
                "intent_plan": {
                    "edge_anchors": [{"ref": "J1", "edge": "right"}],
                },
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue_codes = {issue["code"] for issue in quality["issues"]}

        assert "EDGE_ANCHOR_OFF_EDGE" not in issue_codes
        assert quality["placement"]["outline_bounds_mm"]["x_min"] == pytest.approx(-16.0)
        assert quality["placement"]["edge_margins_mm"]["right"] == pytest.approx(0.0)

    def test_build_layout_quality_classifies_routing_failure_as_placement_blocked(self):
        quality = build_layout_quality(
            run_id="route-placement-blocked",
            status="failed",
            stage="complete",
            ok=False,
            exceptions=[
                DesignException(
                    id="route",
                    code=ExcCode.ROUTE_UNCONNECTED,
                    severity=Severity.ERROR,
                    message="3 net(s) could not be routed",
                    subject={"unrouted_count": 3},
                    candidates=[
                        Candidate(
                            id="c1",
                            action=ActionType.REGENERATE,
                            params={},
                            human_summary="retry placement",
                        ),
                        Candidate(
                            id="c2",
                            action=ActionType.SCALE_OUTLINE,
                            params={"area_factor": 1.2},
                            human_summary="grow",
                        ),
                    ],
                )
            ],
            layout={
                "ok": False,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 10.0, "y_mm": 10.0},
                    {"ref": "J1", "x_mm": 10.5, "y_mm": 10.0},
                ],
                "validation": {"ok": False, "overlaps": [["U1", "J1"]]},
            },
            metrics={"manufacturable": False, "manufacturing_complete": False},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue = next(
            issue for issue in quality["issues"]
            if issue["code"] == "ROUTING_FAILURE_DIAGNOSIS"
        )

        assert issue["evidence"]["classification"] == "placement_blocked"
        assert "outline size" in issue["recommendation"]

    def test_build_layout_quality_classifies_cutout_blocked_route_as_placement_blocked(self):
        quality = build_layout_quality(
            run_id="route-cutout-blocked",
            status="failed",
            stage="complete",
            ok=False,
            exceptions=[
                DesignException(
                    id="route",
                    code=ExcCode.ROUTE_UNCONNECTED,
                    severity=Severity.ERROR,
                    message="1 net could not be routed",
                    candidates=[
                        Candidate(
                            id="c1",
                            action=ActionType.REGENERATE,
                            params={},
                            human_summary="retry placement",
                        ),
                        Candidate(
                            id="c2",
                            action=ActionType.SCALE_OUTLINE,
                            params={"area_factor": 1.2},
                            human_summary="grow",
                        ),
                    ],
                )
            ],
            layout={
                "ok": False,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 20.0, "y_mm": 20.0},
                    {"ref": "J1", "x_mm": 50.0, "y_mm": 20.0},
                ],
                "validation": {
                    "ok": False,
                    "cutout_violations": ["U1"],
                },
            },
            metrics={"manufacturable": False, "manufacturing_complete": False},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue = next(
            issue for issue in quality["issues"]
            if issue["code"] == "ROUTING_FAILURE_DIAGNOSIS"
        )

        assert issue["evidence"]["classification"] == "placement_blocked"
        assert issue["evidence"]["cutout_violations"] == ["U1"]
        assert "before changing outline size" in issue["recommendation"]

    def test_build_layout_quality_classifies_routing_failure_as_footprint_issue(self):
        quality = build_layout_quality(
            run_id="route-footprint-issue",
            status="failed",
            stage="complete",
            ok=False,
            exceptions=[
                DesignException(
                    id="clearance",
                    code=ExcCode.DRC_CLEARANCE,
                    severity=Severity.ERROR,
                    message="4 clearance violation(s)",
                    subject={"refs": ["U2"], "count": 4},
                )
            ],
            layout={
                "ok": True,
                "outline": {"width_mm": 80.0, "height_mm": 60.0},
                "placed_parts": [
                    {"ref": "U2", "x_mm": 40.0, "y_mm": 30.0},
                    {"ref": "J1", "x_mm": 42.0, "y_mm": 32.0},
                    {"ref": "R1", "x_mm": 43.0, "y_mm": 33.0},
                    {"ref": "C1", "x_mm": 41.0, "y_mm": 31.0},
                ],
                "validation": {"ok": True},
            },
            metrics={"manufacturable": False, "manufacturing_complete": False},
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue = next(
            issue for issue in quality["issues"]
            if issue["code"] == "ROUTING_FAILURE_DIAGNOSIS"
        )

        assert issue["evidence"]["classification"] == "footprint_issue"
        assert "footprint" in issue["recommendation"]

    def test_routing_diagnosis_treats_same_ref_drc_short_as_footprint_issue(self):
        exc = DesignException(
            id="short",
            code=ExcCode.DRC_SHORT,
            severity=Severity.ERROR,
            message="1 short circuit(s) detected",
            subject={
                "count": 1,
                "examples": [
                    {
                        "descriptions": [
                            "Pad 1 [VCC] of REF** on F.Cu",
                            "Pad 2 [GND] of REF** on F.Cu",
                        ],
                    }
                ],
                "refs": [],
                "nets": {"VCC": 1, "GND": 1},
            },
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.REGENERATE,
                    params={},
                    human_summary="retry routing",
                ),
                Candidate(
                    id="c2",
                    action=ActionType.SCALE_OUTLINE,
                    params={"area_factor": 1.2},
                    human_summary="grow board",
                ),
            ],
        )

        diagnosis = classify_routing_failure(
            [exc],
            layout={
                "ok": True,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "J1", "x_mm": 48.0, "y_mm": 39.0},
                    {"ref": "U1", "x_mm": 50.0, "y_mm": 40.0},
                    {"ref": "R1", "x_mm": 51.0, "y_mm": 40.5},
                    {"ref": "C1", "x_mm": 49.0, "y_mm": 40.5},
                ],
                "validation": {"ok": True},
                "score": {"congestion_score": 7.0},
            },
            metrics={"congestion_score": 7.0},
        )

        assert diagnosis["classification"] == "footprint_issue"
        assert diagnosis["evidence"]["hotspot"]["ref"] == "REF**"
        assert diagnosis["evidence"]["hotspot"]["placeholder_ref"] is True

        enriched = enrich_routing_failure_exceptions(
            [exc],
            layout={
                "ok": True,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "J1", "x_mm": 48.0, "y_mm": 39.0},
                    {"ref": "U1", "x_mm": 50.0, "y_mm": 40.0},
                    {"ref": "R1", "x_mm": 51.0, "y_mm": 40.5},
                    {"ref": "C1", "x_mm": 49.0, "y_mm": 40.5},
                ],
                "validation": {"ok": True},
                "score": {"congestion_score": 7.0},
            },
            metrics={"congestion_score": 7.0},
        )[0]

        assert enriched.subject["routing_diagnosis"] == "footprint_issue"
        assert [candidate.action for candidate in enriched.candidates] == [
            ActionType.REGENERATE,
            ActionType.SCALE_OUTLINE,
        ]
        assert enriched.candidates[-1].confidence <= 0.2
        assert "footprint" in enriched.candidates[0].human_summary

    def test_routing_diagnosis_treats_same_footprint_clearance_as_footprint_issue(self):
        exc = DesignException(
            id="clearance",
            code=ExcCode.DRC_CLEARANCE,
            severity=Severity.ERROR,
            message="1 clearance violation(s)",
            subject={
                "count": 1,
                "examples": [
                    {
                        "descriptions": [
                            "Pad 1 [VCC] of U7 on F.Cu",
                            "Pad 2 [GND] of U7 on F.Cu",
                        ],
                    }
                ],
                "refs": ["U7"],
                "nets": {"VCC": 1, "GND": 1},
            },
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.REGENERATE,
                    params={},
                    human_summary="retry routing",
                ),
                Candidate(
                    id="c2",
                    action=ActionType.SCALE_OUTLINE,
                    params={"area_factor": 1.15},
                    human_summary="grow board",
                    confidence=0.35,
                ),
            ],
        )
        layout = {
            "ok": True,
            "outline": {"width_mm": 100.0, "height_mm": 80.0},
            "placed_parts": [
                {"ref": "U7", "x_mm": 48.0, "y_mm": 38.0},
                {"ref": "J1", "x_mm": 50.0, "y_mm": 40.0},
                {"ref": "R1", "x_mm": 51.0, "y_mm": 39.0},
                {"ref": "C1", "x_mm": 49.0, "y_mm": 41.0},
            ],
            "validation": {"ok": True},
        }

        enriched = enrich_routing_failure_exceptions([exc], layout=layout)[0]

        assert enriched.subject["routing_diagnosis"] == "footprint_issue"
        assert enriched.subject["routing_diagnosis_evidence"]["hotspot"]["ref"] == "U7"
        assert [candidate.action for candidate in enriched.candidates] == [
            ActionType.REGENERATE,
            ActionType.SCALE_OUTLINE,
        ]
        assert enriched.candidates[-1].confidence <= 0.2
        assert "outline growth" not in enriched.candidates[0].human_summary.lower()

    def test_build_layout_quality_classifies_oversized_route_as_congestion(self):
        quality = build_layout_quality(
            run_id="route-oversized-congestion",
            status="failed",
            stage="complete",
            ok=False,
            exceptions=[
                DesignException(
                    id="unconnected",
                    code=ExcCode.DRC_UNCONNECTED,
                    severity=Severity.ERROR,
                    message="unconnected",
                    subject={"nets": {"SDA": 2}, "count": 2},
                )
            ],
            layout={
                "ok": True,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 48.0, "y_mm": 38.0},
                    {"ref": "J1", "x_mm": 50.0, "y_mm": 40.0},
                    {"ref": "R1", "x_mm": 51.0, "y_mm": 39.0},
                    {"ref": "C1", "x_mm": 49.0, "y_mm": 41.0},
                ],
                "validation": {"ok": True},
            },
            metrics={
                "congestion_score": 55.0,
                "manufacturable": False,
                "manufacturing_complete": False,
            },
            artifacts={"previews": {"files": ["preview_2d_top.png"]}},
        )

        issue = next(
            issue for issue in quality["issues"]
            if issue["code"] == "ROUTING_FAILURE_DIAGNOSIS"
        )

        assert issue["evidence"]["classification"] == "congestion_router_limitation"
        assert "defer outline growth" in issue["recommendation"]

    def test_attach_layout_quality_persists_report_and_updates_response(self, tmp_path):
        response = DesignResponse(
            run_id="run-quality",
            ok=True,
            status="succeeded",
            stage="complete",
            artifacts={
                "pcb": str(tmp_path / "board.kicad_pcb"),
                "previews": {"files": ["preview_2d_top.png"]},
            },
            layout={
                "ok": True,
                "outline": {"width_mm": 30.0, "height_mm": 20.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 10.0, "y_mm": 10.0},
                    {"ref": "J1", "x_mm": 20.0, "y_mm": 10.0},
                ],
                "validation": {"ok": True},
            },
            metrics={"manufacturable": True, "manufacturing_complete": True},
        )

        updated = _attach_layout_quality(response, tmp_path)

        report_path = tmp_path / "layout_quality.json"
        assert report_path.exists()
        assert updated.artifacts["layout_quality"] == str(report_path)
        assert updated.outputs["layout_quality"] == str(report_path)
        assert updated.layout_quality["gates"]["manufacturable"] is True
        assert updated.metrics["quality_gates"] == updated.layout_quality["gates"]

    def test_attach_layout_quality_promotes_reviewable_product_failure(self, tmp_path):
        response = DesignResponse(
            run_id="run-quality-fail-reviewable",
            ok=True,
            status="succeeded_with_warnings",
            stage="placement_review",
            exceptions=[
                DesignException(
                    id="e-review",
                    code=ExcCode.PLACEMENT_REVIEW_ONLY,
                    severity=Severity.ADVISORY,
                    message="placement preview generated without routing",
                )
            ],
            artifacts={
                "pcb": str(tmp_path / "board.kicad_pcb"),
                "previews": {"files": ["preview_2d_top.png"]},
            },
            layout={
                "ok": True,
                "outline": {"width_mm": 100.0, "height_mm": 80.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 48.0, "y_mm": 38.0},
                    {"ref": "U2", "x_mm": 50.0, "y_mm": 40.0},
                    {"ref": "J1", "x_mm": 52.0, "y_mm": 42.0},
                    {"ref": "R1", "x_mm": 49.0, "y_mm": 41.0},
                    {"ref": "C1", "x_mm": 51.0, "y_mm": 39.0},
                ],
                "validation": {"ok": True},
            },
            metrics={
                "manufacturable": False,
                "manufacturing_complete": False,
                "pipeline_goal": "placement_review",
                "board_area_mm2": 8000.0,
            },
        )

        updated = _attach_layout_quality(response, tmp_path)

        assert updated.ok is False
        assert updated.status == "failed_reviewable"
        assert updated.visual_review_ready is True
        assert updated.reviewable_failure is True
        assert updated.layout_quality["status"] == "failed_reviewable"
        assert updated.layout_quality["gates"]["visual_review_ready"] is True
        assert updated.layout_quality["gates"]["product_layout_ok"] is False
        assert updated.artifacts["previews"]["files"] == ["preview_2d_top.png"]
        assert updated.artifacts["layout_quality"].endswith("layout_quality.json")
        product_exc = next(
            exc for exc in updated.exceptions
            if exc.code == ExcCode.PRODUCT_LAYOUT_FAILED
        )
        assert product_exc.severity == Severity.ERROR
        assert product_exc.subject["visual_review_ready"] is True
        assert "LOW_PART_SPREAD" in product_exc.subject["issue_codes"]
        assert any(
            exc.code == ExcCode.PLACEMENT_REVIEW_ONLY
            for exc in updated.exceptions
        )

    def test_attach_layout_quality_promotes_review_only_product_gate(self, tmp_path):
        response = DesignResponse(
            run_id="run-quality-placement-review",
            ok=True,
            status="succeeded_with_warnings",
            stage="placement_review",
            exceptions=[
                DesignException(
                    id="e-review",
                    code=ExcCode.PLACEMENT_REVIEW_ONLY,
                    severity=Severity.ADVISORY,
                    message="placement preview generated without routing",
                )
            ],
            artifacts={
                "pcb": str(tmp_path / "board.kicad_pcb"),
                "previews": {"files": ["preview_2d_top.png"]},
            },
            layout={
                "ok": True,
                "outline": {"width_mm": 30.0, "height_mm": 20.0},
                "placed_parts": [
                    {"ref": "U1", "x_mm": 8.0, "y_mm": 8.0},
                    {"ref": "J1", "x_mm": 20.0, "y_mm": 8.0},
                ],
                "validation": {"ok": True},
            },
            metrics={
                "manufacturable": False,
                "manufacturing_complete": False,
                "pipeline_goal": "placement_review",
            },
        )

        updated = _attach_layout_quality(response, tmp_path)

        assert updated.ok is False
        assert updated.status == "failed_reviewable"
        assert updated.visual_review_ready is True
        assert updated.reviewable_failure is True
        assert updated.layout_quality["gates"]["visual_review_ready"] is True
        assert updated.layout_quality["gates"]["product_layout_ok"] is False
        product_exc = next(
            exc for exc in updated.exceptions
            if exc.code == ExcCode.PRODUCT_LAYOUT_FAILED
        )
        assert product_exc.subject["issue_codes"] == ["PLACEMENT_REVIEW_ONLY"]
        assert product_exc.subject["blocking_issue_codes"] == []

    def test_write_layout_quality_round_trip(self, tmp_path):
        quality = {"version": 1, "run_id": "r1", "gates": {"placement_ok": True}}

        path = write_layout_quality(tmp_path, quality)

        assert path == tmp_path / "layout_quality.json"
        assert json.loads(path.read_text(encoding="utf-8")) == quality


class TestBoardPreviews:
    def test_brand_preview_svg_uses_review_palette(self, tmp_path):
        svg_path = tmp_path / "preview_top.svg"
        svg_path.write_text(
            '<svg><g style="fill:#F2EDA1;stroke:#C83434"/>'
            '<path style="fill:#D864FF;stroke:#D0D2CD"/></svg>',
            encoding="utf-8",
        )

        warning = _brand_preview_svg(svg_path)

        assert warning is None
        branded = svg_path.read_text(encoding="utf-8")
        assert "#F2EDA1" not in branded
        assert "#C83434" not in branded
        assert "#D864FF" not in branded
        assert "#D0D2CD" not in branded
        assert "#15110F" in branded
        assert "#A66A53" in branded

    def test_brand_preview_png_flattens_transparency_to_light_surface(self, tmp_path):
        Image = pytest.importorskip("PIL.Image")

        png_path = tmp_path / "preview_2d_top.png"
        image = Image.new("RGBA", (2, 1), (0, 0, 0, 0))
        image.putpixel((1, 0), (185, 101, 76, 128))
        image.save(png_path)

        warning = _brand_preview_png(png_path, PREVIEW_BACKGROUND)

        assert warning is None
        flattened = Image.open(png_path)
        assert flattened.mode == "RGB"
        assert flattened.getpixel((0, 0)) == (231, 231, 227)
        assert flattened.getpixel((1, 0)) != (185, 101, 76)

    def test_write_layout_mockup_svg_styles_back_side_parts(self, tmp_path):
        layout_result = SimpleNamespace(
            outline=BoardOutline(40.0, 30.0, corner_radius_mm=1.2),
            placed_parts=[
                PlacedPart("J1", 10.0, 12.0, 0.0, "Connector_Audio:Jack"),
                PlacedPart("U1", 28.0, 16.0, 0.0, "Package:SOIC"),
            ],
            fp_bboxes={
                "Connector_Audio:Jack": (8.0, 8.0),
                "Package:SOIC": (6.0, 4.0),
            },
            intent_plan=SimpleNamespace(assembly_sides={"J1": "front", "U1": "back"}),
        )

        warning = _write_layout_mockup_svg(layout_result, tmp_path)

        assert warning is None
        svg = (tmp_path / "preview_assembly.svg").read_text(encoding="utf-8")
        assert 'data-ref="J1" data-side="front"' in svg
        assert 'data-ref="U1" data-side="back"' in svg
        assert 'fill="none" stroke="#D8CEC8"' in svg
        assert 'fill="#A66A53" opacity="0.78">U1</text>' in svg

    def test_write_layout_mockup_svg_uses_geometry_and_hides_hole_labels(
        self,
        tmp_path,
    ):
        class FakeGeometry:
            def transformed_bounds(self, placed):
                assert placed.ref == "H1"
                return (0.6, 0.8, 4.6, 4.8)

        intent_plan = SimpleNamespace(
            assembly_sides={},
            refs_with_kind=lambda kind: ["H1"] if kind == "mounting_hole" else [],
        )
        layout_result = SimpleNamespace(
            outline=BoardOutline(20.0, 12.0, corner_radius_mm=2.0),
            placed_parts=[
                PlacedPart("H1", 2.7, 2.7, 0.0, "MountingHole:M2"),
            ],
            fp_bboxes={"MountingHole:M2": (9.0, 9.0)},
            fp_geometries={"MountingHole:M2": FakeGeometry()},
            intent_plan=intent_plan,
        )

        warning = _write_layout_mockup_svg(layout_result, tmp_path)

        assert warning is None
        svg = (tmp_path / "preview_assembly.svg").read_text(encoding="utf-8")
        assert 'x="0.6000" y="0.8000" width="4.0000" height="4.0000"' in svg
        assert ">H1</text>" not in svg

    def test_generate_board_previews_writes_png_and_svg(self, monkeypatch, tmp_path):
        pcb_path = tmp_path / "board.kicad_pcb"
        pcb_path.write_text("(kicad_pcb)")

        monkeypatch.setattr(
            "mcp_server.engine_worker._find_kicad_cli",
            lambda: "kicad-cli",
        )

        def fake_rasterize(svg_path, png_path, width_px=1600):
            png_path.write_bytes(b"\x89PNG\r\n\x1a\nflat-preview")
            return None

        monkeypatch.setattr(
            "mcp_server.engine_worker._rasterize_svg_preview",
            fake_rasterize,
        )
        monkeypatch.setattr(
            "mcp_server.engine_worker._brand_preview_png",
            lambda png_path: None,
        )

        def fake_run(cmd, **kwargs):
            out_path = Path(cmd[cmd.index("--output") + 1])
            if cmd[2] == "render":
                out_path.write_bytes(b"\x89PNG\r\n\x1a\npreview")
            elif cmd[2:4] == ["export", "svg"]:
                out_path.write_text("<svg><text>pcb</text></svg>")
            else:
                raise AssertionError(f"unexpected command: {cmd}")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        monkeypatch.setattr(subprocess, "run", fake_run)

        previews = _generate_board_previews(str(pcb_path), tmp_path)

        assert previews["ok"] is True
        assert set(previews["files"]) == {
            "preview_2d_bottom.png",
            "preview_2d_bottom.svg",
            "preview_2d_combined.png",
            "preview_2d_combined.svg",
            "preview_2d_top.png",
            "preview_2d_top.svg",
            "preview_top.png",
            "preview_top.svg",
        }
        assert (tmp_path / "preview_2d_bottom.png").read_bytes().startswith(b"\x89PNG")
        assert (tmp_path / "preview_2d_combined.png").read_bytes().startswith(b"\x89PNG")
        assert (tmp_path / "preview_2d_top.png").read_bytes().startswith(b"\x89PNG")
        assert (tmp_path / "preview_top.png").read_bytes().startswith(b"\x89PNG")
        assert (tmp_path / "preview_2d_top.svg").read_text().startswith("<svg")
        assert (tmp_path / "preview_top.svg").read_text().startswith("<svg")

    def test_generate_board_previews_without_kicad_is_nonfatal(self, monkeypatch, tmp_path):
        pcb_path = tmp_path / "board.kicad_pcb"
        pcb_path.write_text("(kicad_pcb)")
        monkeypatch.setattr(
            "mcp_server.engine_worker._find_kicad_cli",
            lambda: None,
        )

        previews = _generate_board_previews(str(pcb_path), tmp_path)

        assert previews == {
            "files": [],
            "errors": ["kicad-cli not found"],
            "warnings": [],
            "ok": False,
        }

    def test_layout_mockup_preview_rasterizes_when_kicad_preview_missing(
        self, monkeypatch, tmp_path
    ):
        (tmp_path / "preview_assembly.svg").write_text(
            "<svg><rect width='10' height='10'/></svg>",
            encoding="utf-8",
        )

        def fake_rasterize(svg_path, png_path, width_px=1600):
            png_path.write_bytes(b"\x89PNG\r\n\x1a\nmockup")
            return None

        monkeypatch.setattr(
            "mcp_server.engine_worker._rasterize_svg_preview",
            fake_rasterize,
        )
        monkeypatch.setattr(
            "mcp_server.engine_worker._brand_preview_png",
            lambda png_path: None,
        )
        previews = {
            "files": [],
            "errors": ["pcb export svg exited 3: Failed to load board"],
            "warnings": [],
        }

        _add_layout_mockup_preview(previews, tmp_path, None)

        assert previews["files"] == ["preview_assembly.svg", "preview_2d_top.png"]
        assert (tmp_path / "preview_2d_top.png").read_bytes().startswith(b"\x89PNG")
        assert any("preview_assembly.svg" in warning for warning in previews["warnings"])

    def test_placement_review_fast_previews_skip_kicad_exports(
        self, monkeypatch, tmp_path
    ):
        def fail_full_preview(*args, **kwargs):
            raise AssertionError("fast placement review must not export full KiCad previews")

        def fake_mockup(layout_result, out_dir):
            (out_dir / "preview_assembly.svg").write_text(
                "<svg><rect width='10' height='10'/></svg>",
                encoding="utf-8",
            )
            return None

        def fake_rasterize(svg_path, png_path, width_px=1600):
            png_path.write_bytes(b"\x89PNG\r\n\x1a\nmockup")
            return None

        monkeypatch.setattr(engine_worker_mod, "_generate_board_previews", fail_full_preview)
        monkeypatch.setattr(engine_worker_mod, "_write_layout_mockup_svg", fake_mockup)
        monkeypatch.setattr(engine_worker_mod, "_rasterize_svg_preview", fake_rasterize)
        monkeypatch.setattr(engine_worker_mod, "_brand_preview_png", lambda png_path: None)

        previews = _generate_pipeline_previews(
            str(tmp_path / "board.kicad_pcb"),
            tmp_path,
            SimpleNamespace(),
            pipeline_goal="placement_review",
            preview_mode="fast",
        )

        assert previews["ok"] is True
        assert previews["mode"] == "fast"
        assert previews["files"] == ["preview_assembly.svg", "preview_2d_top.png"]
        assert any("return reviewable artifacts sooner" in w for w in previews["warnings"])
        assert any("fast preview mode skipped" in w for w in previews["warnings"])

    def test_placement_review_full_preview_mode_keeps_kicad_exports(
        self, monkeypatch, tmp_path
    ):
        called = {"full_preview": False}

        def fake_full_preview(pcb_path, out_dir):
            called["full_preview"] = True
            return {"files": ["preview_2d_top.png"], "errors": [], "warnings": [], "ok": True}

        def fake_mockup(layout_result, out_dir):
            (out_dir / "preview_assembly.svg").write_text(
                "<svg><rect width='10' height='10'/></svg>",
                encoding="utf-8",
            )
            return None

        monkeypatch.setattr(engine_worker_mod, "_generate_board_previews", fake_full_preview)
        monkeypatch.setattr(engine_worker_mod, "_write_layout_mockup_svg", fake_mockup)

        previews = _generate_pipeline_previews(
            str(tmp_path / "board.kicad_pcb"),
            tmp_path,
            SimpleNamespace(),
            pipeline_goal="placement_review",
            preview_mode="full",
        )

        assert called["full_preview"] is True
        assert previews["mode"] == "full"
        assert previews["files"] == ["preview_2d_top.png", "preview_assembly.svg"]


class TestKiCadEnv:
    def test_find_kicad_python_honors_configured_env(self, monkeypatch, tmp_path):
        python_bin = tmp_path / "python3"
        python_bin.write_text("#!/bin/sh\n", encoding="utf-8")
        python_bin.chmod(0o755)

        monkeypatch.setenv("KICAD_PYTHON", str(python_bin))

        assert _find_kicad_python() == str(python_bin)

    def test_freerouting_jar_path_honors_configured_env(self, monkeypatch, tmp_path):
        jar = tmp_path / "freerouting.jar"
        jar.write_bytes(b"jar")

        monkeypatch.setenv("FREEROUTING_JAR", str(jar))

        assert _freerouting_jar_path() == str(jar)

    def test_pipeline_env_falls_back_to_kicad_app_libraries(self, monkeypatch):
        mac_symbols = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
        mac_footprints = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
        existing = {mac_symbols, mac_footprints}

        monkeypatch.setenv("KICAD9_SYMBOL_DIR", "/missing/symbols")
        monkeypatch.setenv("KICAD9_FOOTPRINT_DIR", "/missing/footprints")
        monkeypatch.setattr(
            pipeline_mod.os.path,
            "isdir",
            lambda path: str(path) in existing,
        )

        env = pipeline_mod._env()

        assert env["KICAD9_SYMBOL_DIR"] == mac_symbols
        assert env["KICAD9_FOOTPRINT_DIR"] == mac_footprints

    def test_worker_configure_kicad_env_replaces_invalid_paths(self, monkeypatch):
        mac_symbols = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols"
        mac_footprints = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
        existing = {mac_symbols, mac_footprints}

        monkeypatch.setenv("KICAD9_SYMBOL_DIR", "/missing/symbols")
        monkeypatch.setenv("KICAD9_FOOTPRINT_DIR", "/missing/footprints")
        monkeypatch.setattr(
            engine_worker_mod.os.path,
            "isdir",
            lambda path: str(path) in existing,
        )

        engine_worker_mod._configure_kicad_env()

        assert os.environ["KICAD9_SYMBOL_DIR"] == mac_symbols
        assert os.environ["KICAD9_FOOTPRINT_DIR"] == mac_footprints
        assert os.environ["KICAD_SYMBOL_DIR"] == mac_symbols
        assert os.environ["KICAD_FOOTPRINT_DIR"] == mac_footprints


class TestExceptionMapper:
    def test_waived_advisory_suppressed(self):
        spec = CircuitSpec.model_validate(trivial_spec())
        exc = DesignException(
            id="e1",
            code=ExcCode.HIGH_CONGESTION,
            severity=Severity.ADVISORY,
            message="high congestion",
            subject={"net": "SIG"},
        )
        spec.waivers = [exc.waiver_key()]

        assert suppress_waived([exc], spec) == []

    def test_agent_order_puts_errors_before_advisories(self):
        advisory = DesignException(
            id="e-high-congestion",
            code=ExcCode.HIGH_CONGESTION,
            severity=Severity.ADVISORY,
            message="layout congestion is high",
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.ACCEPT_ADVISORY,
                    params={},
                    human_summary="accept advisory",
                )
            ],
        )
        error = DesignException(
            id="e-route-timeout",
            code=ExcCode.ROUTE_TIMEOUT,
            severity=Severity.ERROR,
            message="routing timed out",
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.REGENERATE,
                    params={"run_options": {"route_timeout_s": 240}},
                    human_summary="retry routing longer",
                )
            ],
        )

        ordered = order_exceptions_for_agent([advisory, error])

        assert [exc.code for exc in ordered] == [
            ExcCode.ROUTE_TIMEOUT,
            ExcCode.HIGH_CONGESTION,
        ]

    def test_layout_validation_overlap_maps_to_candidate(self):
        class Validation:
            overlaps = [("R1", "R2")]
            outline_violations = []
            keepout_violations = []
            missing_refs = []

        class Score:
            congestion_score = 0.0
            warnings = []

        class Result:
            validation = Validation()
            score = Score()
            outline = None

        exceptions = layout_exceptions(Result())

        assert exceptions[0].code == ExcCode.LAYOUT_OVERLAP
        assert exceptions[0].candidates[0].action == ActionType.SCALE_OUTLINE
        assert "@subcircuit" in exceptions[0].retry_hint
        assert "larger outline_mm" in exceptions[0].retry_hint
        assert "subject.pairs" in exceptions[0].retry_hint
        assert "fixed_positions" in exceptions[0].retry_hint

    def test_layout_overlap_on_spacious_board_does_not_scale_first(self):
        class Validation:
            overlaps = [("J1", "J2")]
            outline_violations = []
            keepout_violations = []
            missing_refs = []

        class Score:
            congestion_score = 0.0
            warnings = []

        class Result:
            validation = Validation()
            score = Score()
            outline = SimpleNamespace(width_mm=100.0, height_mm=55.0)

        exc = layout_exceptions(Result())[0]

        assert exc.code == ExcCode.LAYOUT_OVERLAP
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert exc.candidates[1].action == ActionType.SCALE_OUTLINE
        assert exc.candidates[1].confidence < 0.5
        assert "do not keep scaling" in exc.retry_hint

    def test_layout_outline_violation_warns_against_fixed_edge_connectors(self):
        class Validation:
            overlaps = []
            outline_violations = ["J1"]
            keepout_violations = []
            missing_refs = []

        class Score:
            congestion_score = 0.0
            warnings = []

        class Result:
            validation = Validation()
            score = Score()
            outline = SimpleNamespace(width_mm=40.0, height_mm=25.0)

        exc = layout_exceptions(Result())[0]

        assert exc.code == ExcCode.LAYOUT_OUTLINE_VIOLATION
        assert "edge_preference" in exc.retry_hint
        assert "edge_anchors" in exc.retry_hint
        assert "fixed_positions" in exc.retry_hint
        assert "footprint origins" in exc.retry_hint

    def test_routing_diagnosis_defers_outline_for_off_edge_origin_false_positive(self):
        exc = DesignException(
            id="route",
            code=ExcCode.ROUTE_UNCONNECTED,
            severity=Severity.ERROR,
            message="routing failed",
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.REGENERATE,
                    params={},
                    human_summary="retry",
                ),
                Candidate(
                    id="c2",
                    action=ActionType.SCALE_OUTLINE,
                    params={"area_factor": 1.2},
                    human_summary="grow",
                ),
            ],
        )
        layout = {
            "outline": {"width_mm": 60.0, "height_mm": 30.0},
            "placed_parts": [
                {
                    "ref": "J1",
                    "x_mm": 30.0,
                    "y_mm": 20.11,
                    "footprint": (
                        "Connector_PinHeader_2.54mm:"
                        "PinHeader_1x03_P2.54mm_Horizontal"
                    ),
                },
                {"ref": "U1", "x_mm": 30.0, "y_mm": 12.0},
                {"ref": "R1", "x_mm": 31.0, "y_mm": 12.5},
                {"ref": "C1", "x_mm": 29.0, "y_mm": 11.5},
            ],
            "score": {"warnings": []},
            "validation": {"ok": True},
            "intent_plan": {
                "intents": {
                    "J1": [
                        {"kind": "edge_connector", "priority": 90, "reasons": []},
                    ],
                },
                "edge_anchors": [
                    {"ref": "J1", "edge": "bottom", "rot_deg": 90.0},
                ],
            },
        }

        diagnosis = classify_routing_failure([exc], layout=layout)
        enriched = enrich_routing_failure_exceptions([exc], layout=layout)[0]

        assert diagnosis["classification"] != "placement_blocked"
        assert enriched.subject["routing_diagnosis"] == "congestion_router_limitation"
        assert enriched.candidates[-1].action == ActionType.SCALE_OUTLINE
        assert enriched.candidates[-1].confidence <= 0.2

    def test_routing_diagnosis_recommends_shrink_or_redistribute_for_sparse_board(self):
        exc = DesignException(
            id="route",
            code=ExcCode.DRC_UNCONNECTED,
            severity=Severity.ERROR,
            message="unconnected",
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.REGENERATE,
                    params={},
                    human_summary="retry",
                ),
                Candidate(
                    id="c2",
                    action=ActionType.SCALE_OUTLINE,
                    params={"area_factor": 1.2},
                    human_summary="grow",
                    confidence=0.4,
                ),
            ],
        )
        layout = {
            "ok": True,
            "outline": {"width_mm": 120.0, "height_mm": 80.0},
            "placed_parts": [
                {"ref": "U1", "x_mm": 20.0, "y_mm": 20.0},
                {"ref": "J1", "x_mm": 26.0, "y_mm": 20.0},
                {"ref": "R1", "x_mm": 22.0, "y_mm": 24.0},
                {"ref": "R2", "x_mm": 24.0, "y_mm": 24.0},
            ],
            "score": {
                "compact_outline_area_ratio": 0.08,
                "footprint_envelope_area_ratio": 0.05,
                "max_empty_margin_ratio": 0.70,
            },
            "validation": {"ok": True},
        }

        diagnosis = classify_routing_failure([exc], layout=layout)
        enriched = enrich_routing_failure_exceptions([exc], layout=layout)[0]

        assert diagnosis["classification"] == "sparse_or_underused_outline"
        assert "Shrink auto-sized boards" in diagnosis["recommendation"]
        assert "redistribute fixed-outline" in diagnosis["recommendation"]
        assert enriched.candidates[0].action == ActionType.REGENERATE
        assert "Shrink auto outline" in enriched.candidates[0].human_summary
        assert enriched.candidates[-1].action == ActionType.SCALE_OUTLINE
        assert enriched.candidates[-1].confidence <= 0.2
        assert "shrink or redistribute" in enriched.candidates[-1].human_summary

    def test_routing_diagnosis_promotes_outline_growth_only_when_tight(self):
        exc = DesignException(
            id="route",
            code=ExcCode.DRC_UNCONNECTED,
            severity=Severity.ERROR,
            message="unconnected",
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.REGENERATE,
                    params={},
                    human_summary="retry",
                ),
                Candidate(
                    id="c2",
                    action=ActionType.SET_LAYERS,
                    params={"layers": 4},
                    human_summary="layers",
                ),
                Candidate(
                    id="c3",
                    action=ActionType.SCALE_OUTLINE,
                    params={"area_factor": 1.15},
                    human_summary="grow",
                    confidence=0.25,
                ),
            ],
        )
        layout = {
            "outline": {"width_mm": 20.0, "height_mm": 15.0},
            "placed_parts": [
                {"ref": "U1", "x_mm": 1.0, "y_mm": 1.0},
                {"ref": "J1", "x_mm": 19.0, "y_mm": 1.0},
                {"ref": "R1", "x_mm": 1.0, "y_mm": 14.0},
                {"ref": "C1", "x_mm": 19.0, "y_mm": 14.0},
            ],
            "validation": {"ok": True},
        }

        enriched = enrich_routing_failure_exceptions([exc], layout=layout)[0]

        assert enriched.subject["routing_diagnosis"] == "outline_too_small"
        assert enriched.candidates[0].action == ActionType.SCALE_OUTLINE
        assert enriched.candidates[0].confidence >= 0.65

    def test_layout_oversized_warning_maps_to_outline_advisory(self):
        class Validation:
            overlaps = []
            outline_violations = []
            keepout_violations = []
            missing_refs = []

        class Score:
            congestion_score = 0.0
            warnings = [
                "board outline is 3.4x larger than placed footprint envelope "
                "(estimated compact outline 22.0x14.0mm); consider a smaller outline "
                "or explicit mechanical constraints"
            ]

        class Result:
            validation = Validation()
            score = Score()
            outline = None

        exceptions = layout_exceptions(Result())

        assert exceptions[0].code == ExcCode.LAYOUT_OVERSIZED
        assert exceptions[0].severity == Severity.ADVISORY
        assert exceptions[0].candidates[0].action == ActionType.SET_OUTLINE
        assert exceptions[0].candidates[0].params == {"w_mm": 22.0, "h_mm": 14.0}
        assert exceptions[0].candidates[1].action == ActionType.ACCEPT_ADVISORY
        assert "enclosure" in exceptions[0].retry_hint

    def test_layout_compact_oversized_warning_maps_to_outline_advisory(self):
        class Validation:
            overlaps = []
            outline_violations = []
            keepout_violations = []
            missing_refs = []

        class Score:
            congestion_score = 0.0
            warnings = [
                "board outline is 3.4x larger than compact footprint envelope "
                "(estimated compact outline 22.0x14.0mm); shrink auto-sized boards "
                "or redistribute parts if this outline is mechanically fixed"
            ]

        class Result:
            validation = Validation()
            score = Score()
            outline = None

        exceptions = layout_exceptions(Result())

        assert exceptions[0].code == ExcCode.LAYOUT_OVERSIZED
        assert exceptions[0].candidates[0].action == ActionType.SET_OUTLINE
        assert exceptions[0].candidates[0].params == {"w_mm": 22.0, "h_mm": 14.0}

    def test_outline_for_spec_defaults_to_rounded_product_corners(self):
        spec = CircuitSpec.model_validate(trivial_spec())

        outline = _outline_for_spec(spec)

        assert outline is not None
        assert outline.corner_radius_mm == 1.6

    def test_outline_for_spec_defaults_eurorack_to_square_corners(self):
        spec_dict = trivial_spec()
        spec_dict["board"]["name"] = "eurorack-vco"
        spec = CircuitSpec.model_validate(spec_dict)

        outline = _outline_for_spec(spec)

        assert outline is not None
        assert outline.corner_radius_mm == 0.0

    def test_outline_for_spec_respects_explicit_corner_radius(self):
        spec_dict = trivial_spec()
        spec_dict["board"]["name"] = "eurorack-vco"
        spec_dict["board"]["corner_radius_mm"] = 1.5
        spec = CircuitSpec.model_validate(spec_dict)

        outline = _outline_for_spec(spec)

        assert outline is not None
        assert outline.corner_radius_mm == 1.5


class TestHelpfulFailures:
    def test_lcsc_refs_are_detected_in_python_specs(self):
        spec = {
            "_mode": "skidl_python",
            "code": 'u1 = Part("C8734", "STM32F103C8T6"); u1.lcsc = "C8734"',
        }

        assert _lcsc_refs_in_spec(spec) == {"C8734"}

    def test_restore_lcsc_asset_writes_easyeda_cache(self, monkeypatch, tmp_path):
        cache = tmp_path / "easyeda_cache"
        monkeypatch.setattr(worker_mod, "EASYEDA_CACHE", cache)
        row = {
            "meta": {
                "lcsc": "C8734",
                "library": "C8734",
                "symbol": "STM32F103C8T6",
                "footprint": "C8734:LQFP-48_L7.0-W7.0-P0.50",
                "sym_file": str(cache / "C8734" / "C8734.kicad_sym"),
                "fp_dir": str(cache / "C8734" / "C8734.pretty"),
            },
            "sym_data": b"(kicad_symbol_lib)",
            "fp_data": b"(footprint)",
            "step_data": b"STEP",
        }

        assert _restore_lcsc_asset("C8734", row) is True
        assert (cache / "C8734" / "meta.json").exists()
        assert (cache / "C8734" / "C8734.kicad_sym").read_bytes() == b"(kicad_symbol_lib)"
        assert (
            cache / "C8734" / "C8734.pretty" / "LQFP-48_L7.0-W7.0-P0.50.kicad_mod"
        ).read_bytes() == b"(footprint)"
        assert (cache / "C8734" / "C8734.3dshapes" / "C8734.step").read_bytes() == b"STEP"

    def test_code_exec_error_gets_pin_and_line_context(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="TypeError: 'NoneType' object is not iterable",
        )
        code = "\n".join([
            "from skidl import *",
            "ads1115_part = Part('Analog_ADC', 'ADS1115IDGS')",
            "ain0 = Net('AIN0'); ain0 += ads1115_part['A0']",
        ])
        stderr = (
            "ERROR: No pins found using ADS1115IDGS:U1[('A0',)] "
            "@ [/app/mcp_server/engine_worker.py:627=>/tmp/run/<string>:3]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert "pin 'A0' not found on U1" in exc.message
        assert exc.subject["ref"] == "U1"
        assert exc.subject["pin"] == "A0"
        assert exc.subject["line"] == 3
        assert "ads1115_part['A0']" in exc.subject["line_text"]
        assert "search_kicad" in exc.retry_hint

    def test_code_exec_error_gets_multi_unit_pin_context_from_stderr(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="ValueError: No pins found using TL074:U1.uB[('3',)]",
        )
        code = "\n".join([
            "from skidl import *",
            "op = Part('Amplifier_Operational', 'TL074')",
            "feedback = Net('FB'); feedback += op.uB[3]",
        ])
        stderr = (
            "ERROR: No pins found using TL074:U1.uB[('3',)] "
            "@ [/app/mcp_server/engine_worker.py:627=>/tmp/run/<string>:3]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert exc.message == "pin '3' not found on U1.uB (TL074) while executing SKiDL code"
        assert exc.subject["ref"] == "U1"
        assert exc.subject["unit"] == "uB"
        assert exc.subject["pin"] == "3"
        assert exc.subject["multi_unit_pin_access"] is True
        assert "multi-unit symbol pin lookup" in exc.retry_hint
        assert "Do not reuse A-side package pin numbers" in exc.retry_hint

    def test_stderr_pin_context_adds_rgb_led_family_hint(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="ValueError: No pins found using LED_ARGB:D1[('R',)]",
        )
        code = "\n".join([
            "from skidl import *",
            "led = Part('Device', 'LED_ARGB')",
            "red = Net('RED'); red += led['R']",
        ])
        stderr = (
            "ERROR: No pins found using LED_ARGB:D1[('R',)] "
            "@ [/app/mcp_server/engine_worker.py:627=>/tmp/run/<string>:3]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert exc.subject["part"] == "LED_ARGB"
        assert exc.subject["pin"] == "R"
        assert exc.subject["pin_family_hint"]
        assert "RA/RK" in exc.retry_hint
        assert "RK/GK/BK" in exc.retry_hint

    def test_stderr_pin_context_overrides_wrong_line_inference(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="pin 'VCC' not found on U1 (ATtiny102-M)",
            subject={
                "ref": "U1",
                "pin": "VCC",
                "part": "ATtiny102-M",
                "variable": "mcu",
                "available_pins": ["VCC", "PA0"],
                "suggested_pins": ["VCC"],
            },
        )
        code = "\n".join([
            "from skidl import *",
            "vcc += mcu['VCC'], display['VCC'], battery['VOUT']",
        ])
        stderr = (
            "ERROR: No pins found using HY1602E:DS1[('VCC',)] "
            "@ [/app/mcp_server/engine_worker.py:906=>/tmp/run/<string>:2]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert exc.message == "pin 'VCC' not found on DS1 (HY1602E) while executing SKiDL code"
        assert exc.subject["ref"] == "DS1"
        assert exc.subject["part"] == "HY1602E"
        assert exc.subject["line"] == 2
        assert "available_pins" not in exc.subject
        assert "suggested_pins" not in exc.subject
        assert "variable" not in exc.subject

    def test_stderr_numeric_pin_context_overrides_wrong_line_inference(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="pin 'VCC' not found on DS1 (OLED-128O064D)",
            subject={
                "ref": "DS1",
                "pin": "VCC",
                "part": "OLED-128O064D",
                "variable": "display",
                "available_pins": ["GND", "VCC", "SCL", "SDA"],
                "suggested_pins": ["VCC"],
            },
        )
        code = "\n".join([
            "from skidl import *",
            "vcc += encoder[1], button[1], display['VCC'], buzzer[1], c1[1]",
        ])
        stderr = (
            "ERROR: No pins found using RotaryEncoder_Switch:SW1[(1,)] "
            "@ [/app/mcp_server/engine_worker.py:972=>/tmp/run/<string>:2]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert (
            exc.message
            == "pin '1' not found on SW1 (RotaryEncoder_Switch) while executing SKiDL code"
        )
        assert exc.subject["ref"] == "SW1"
        assert exc.subject["pin"] == "1"
        assert exc.subject["part"] == "RotaryEncoder_Switch"
        assert exc.subject["line"] == 2
        assert "encoder[1]" in exc.subject["line_text"]
        assert "available_pins" not in exc.subject
        assert "suggested_pins" not in exc.subject
        assert "variable" not in exc.subject

    def test_missing_symbol_library_error_is_specific(self):
        class FakeExecError:
            original = FileNotFoundError("Can't open file: Connector_USB.\n")
            line = 7
            line_text = 'usb = Part("Connector_USB", "USB_C_Receptacle")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == "symbol library 'Connector_USB' is not available to SKiDL"
        assert exc.subject["missing_library"] == "Connector_USB"
        assert "search_kicad" in exc.retry_hint
        assert "guessed symbol library names" in exc.retry_hint

    def test_missing_testpoint_library_suggests_connector_symbol(self):
        class FakeExecError:
            original = FileNotFoundError("Can't open file: TestPoint.\n")
            line = 12
            line_text = 'tp = Part("TestPoint", "TestPoint_Pad_D1.5mm")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == "symbol library 'TestPoint' is not available to SKiDL"
        assert exc.subject["missing_library"] == "TestPoint"
        assert exc.subject["suggested_usage"].startswith('Part("Connector", "TestPoint"')
        assert "TestPoint is a KiCad footprint library" in exc.retry_hint
        assert "Connector:TestPoint" in exc.retry_hint

    def test_missing_optodevice_library_suggests_isolator_search(self):
        class FakeExecError:
            original = FileNotFoundError("Can't open file: OptoDevice.\n")
            line = 10
            line_text = 'opto = Part("OptoDevice", "6N138")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == "symbol library 'OptoDevice' is not available to SKiDL"
        assert exc.subject["suggested_search"] == 'search_kicad("6N138 optocoupler", detail=true)'
        assert "Isolator" in exc.retry_hint

    def test_missing_symbol_part_error_is_specific(self):
        class FakeExecError:
            original = ValueError("Unable to find part LCD_16x2 in library Display_Character.")
            line = 10
            line_text = 'display = Part("Display_Character", "LCD_16x2")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == (
            "part 'LCD_16x2' was not found in symbol library 'Display_Character'"
        )
        assert exc.subject["missing_part"] == "LCD_16x2"
        assert exc.subject["library"] == "Display_Character"
        assert "exact returned library and part names" in exc.retry_hint

    def test_missing_symbol_part_error_suggests_close_search_matches(self, monkeypatch):
        from llm import kicad_index
        from llm.kicad_index import SymbolEntry

        monkeypatch.setattr(
            kicad_index,
            "search_symbols",
            lambda query, limit=5: [
                SymbolEntry(
                    lib="Device",
                    name="R_Potentiometer",
                    description="Potentiometer",
                    pin_count=3,
                )
            ]
            if "Potentiometer" in query
            else [],
        )

        class FakeExecError:
            original = ValueError("Unable to find part Potentiometer in library Device.")
            line = 29
            line_text = 'pot1 = Part("Device", "Potentiometer")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["suggested_parts"][0]["library"] == "Device"
        assert exc.subject["suggested_parts"][0]["part"] == "R_Potentiometer"
        assert 'Part("Device", "R_Potentiometer"' in exc.subject["suggested_parts"][0]["usage"]
        assert "subject.suggested_parts" in exc.retry_hint

    def test_missing_stm32_order_code_mentions_package_family_symbols(self):
        class FakeExecError:
            original = ValueError(
                "Unable to find part STM32F103C8T6 in library MCU_ST_STM32F1."
            )
            line = 4
            line_text = 'u1 = Part("MCU_ST_STM32F1", "STM32F103C8T6")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["part_number_style"].startswith("STM32 order codes")
        assert "package-family" in exc.retry_hint
        assert "convert_lcsc" in exc.retry_hint

    def test_switched_audio_jack_pin_error_explains_pin_family(self):
        jack = SimpleNamespace(
            ref="J1",
            name="AudioJack3_Dual_Ground_Switch",
            pins=[
                SimpleNamespace(name="~", num="G"),
                SimpleNamespace(name="~", num="S1"),
                SimpleNamespace(name="~", num="SN1"),
                SimpleNamespace(name="~", num="R1"),
                SimpleNamespace(name="~", num="RN1"),
                SimpleNamespace(name="~", num="T1"),
                SimpleNamespace(name="~", num="TN1"),
                SimpleNamespace(name="~", num="T2"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using J1['T']")
            line = 12
            line_text = "focus += jack['T']"
            namespace = {"jack": jack}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["pin"] == "T"
        assert "pin_family_hint" in exc.subject
        assert "Close valid pin names" in exc.retry_hint
        assert "plain 'T' is not a valid pin" in exc.subject["pin_family_hint"]
        assert "T1/T2" in exc.retry_hint
        assert "simpler AudioPlug/AudioJack" in exc.retry_hint

    def test_pin_reindex_error_explains_single_pin_lookup(self):
        jack = SimpleNamespace(
            ref="J1",
            name="AudioJack3",
            pins=[
                SimpleNamespace(name="~", num="T1"),
                SimpleNamespace(name="~", num="R1"),
                SimpleNamespace(name="~", num="S1"),
            ],
        )

        class FakeExecError:
            original = ValueError("Can't use a non-zero index for a pin.")
            line = 42
            line_text = 'gnd += jack["S1"][1]'
            namespace = {"jack": jack}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["pin"] == "S1"
        assert "lookup returned a single SKiDL Pin" in exc.retry_hint
        assert "not `jack['T1'][1]`" in exc.retry_hint
        assert "search_kicad" in exc.retry_hint

    def test_multi_unit_pin_error_explains_exact_unit_pins(self):
        unit_b = SimpleNamespace(
            pins=[
                SimpleNamespace(name="+", num="5"),
                SimpleNamespace(name="-", num="6"),
                SimpleNamespace(name="~", num="5"),
                SimpleNamespace(name="~", num="6"),
                SimpleNamespace(name="~", num="7"),
            ],
        )
        opamp = SimpleNamespace(
            ref="U1",
            name="TL072",
            value="TL072",
            uB=unit_b,
        )

        class FakeExecError:
            original = TypeError("'NoneType' object is not iterable")
            line = 51
            line_text = "feedback += opamp.uB[3]"
            namespace = {"opamp": opamp}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["unit"] == "uB"
        assert exc.subject["pin"] == "3"
        assert exc.subject["available_pins"] == ["+", "-", "5", "6", "7"]
        assert "guessed multi-unit symbol pin access" in exc.retry_hint
        assert "Do not reuse A-side package pin numbers" in exc.retry_hint
        assert "`op.uB['+']`" in exc.retry_hint

    def test_multi_unit_pin_not_found_error_explains_exact_unit_pins(self):
        unit_b = SimpleNamespace(
            pins=[
                SimpleNamespace(name="+", num="5"),
                SimpleNamespace(name="-", num="6"),
                SimpleNamespace(name="~", num="7"),
            ],
        )
        opamp = SimpleNamespace(
            ref="U1",
            name="TL074",
            value="TL074",
            uB=unit_b,
        )

        class FakeExecError:
            original = ValueError("No pins found using TL074:U1.uB[('3',)]")
            line = 51
            line_text = "feedback += opamp.uB[3]"
            namespace = {"opamp": opamp}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.message == "pin '3' not found on U1.uB (TL074)"
        assert exc.subject["available_pins"] == ["+", "-", "5", "6", "7"]
        assert "guessed multi-unit symbol pin access" in exc.retry_hint
        assert "`op.uB['+']`" in exc.retry_hint
        assert "`op.uB['-']`" in exc.retry_hint

    def test_rgb_led_pin_error_explains_channel_pins(self):
        led = SimpleNamespace(
            ref="D1",
            name="LED_ARGB",
            pins=[
                SimpleNamespace(name="~", num="A"),
                SimpleNamespace(name="~", num="RK"),
                SimpleNamespace(name="~", num="GK"),
                SimpleNamespace(name="~", num="BK"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using D1['R']")
            line = 8
            line_text = "red += led['R']"
            namespace = {"led": led}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "R"
        assert "pin_family_hint" in exc.subject
        assert "RK" in exc.retry_hint
        assert "A=anode, K=cathode" in exc.retry_hint

    def test_pico_usb_pin_error_explains_module_boundary(self):
        pico = SimpleNamespace(
            ref="A1",
            name="RaspberryPi_Pico",
            pins=[
                SimpleNamespace(name="GPIO0", num="1"),
                SimpleNamespace(name="VBUS", num="40"),
                SimpleNamespace(name="GND", num="38"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using A1['USB_DP']")
            line = 10
            line_text = "usb_dp += pico['USB_DP']"
            namespace = {"pico": pico}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "USB_DP"
        assert "pin_family_hint" in exc.subject
        assert "complete module with onboard USB" in exc.retry_hint
        assert "raw RP2040" in exc.retry_hint

    def test_bme280_i2c_pin_error_explains_spi_aliases(self):
        bme = SimpleNamespace(
            ref="U1",
            name="BME280",
            pins=[
                SimpleNamespace(name="SDI", num="6"),
                SimpleNamespace(name="SCK", num="4"),
                SimpleNamespace(name="SDO", num="5"),
                SimpleNamespace(name="CSB", num="2"),
                SimpleNamespace(name="GND", num="1"),
                SimpleNamespace(name="VDD", num="8"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using U1['SDA']")
            line = 31
            line_text = 'sda += bme["SDA"]'
            namespace = {"bme": bme}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "SDA"
        assert "pin_family_hint" in exc.subject
        assert "SDI for I2C SDA" in exc.retry_hint
        assert "CSB high" in exc.retry_hint

    def test_numeric_relay_pin_error_warns_against_semantic_aliases(self):
        relay = SimpleNamespace(
            ref="K1",
            name="EC2-3NU",
            pins=[
                SimpleNamespace(name="~", num="1"),
                SimpleNamespace(name="~", num="3"),
                SimpleNamespace(name="~", num="4"),
                SimpleNamespace(name="~", num="5"),
                SimpleNamespace(name="~", num="8"),
                SimpleNamespace(name="~", num="9"),
                SimpleNamespace(name="~", num="10"),
                SimpleNamespace(name="~", num="12"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using K1['Coil+']")
            line = 47
            line_text = 'jack_signal += relay["Coil+"]'
            namespace = {"relay": relay}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "Coil+"
        assert "pin_family_hint" in exc.subject
        assert "numeric package pins only" in exc.retry_hint
        assert "Coil+/Coil-/COM/NO/NC" in exc.retry_hint

    def test_missing_footprint_error_suggests_replacements(self, monkeypatch):
        from llm import kicad_index

        monkeypatch.setattr(
            kicad_index,
            "search_footprints",
            lambda query, limit=5: [
                "Connector_USB:USB_Micro-B_Molex-105017-0001",
                "Connector_USB:USB_Micro-B_Amphenol_10118193-0001LF_Horizontal",
            ]
            if "Micro" in query or "USB_B" in query or "USB" in query
            else [],
        )
        circuit = SimpleNamespace(
            parts=[
                SimpleNamespace(ref="J5", footprint="Connector_USB:USB_B_Micro"),
            ]
        )

        exc = _footprint_missing_exception(
            FileNotFoundError("INCOMPLETE PCB: 1/12 parts missing footprints: J5"),
            circuit,
        )

        assert exc.code == ExcCode.FOOTPRINT_MISSING
        assert exc.subject["suggested_footprints"]["J5"][0].startswith(
            "Connector_USB:USB_Micro-B"
        )
        assert exc.candidates[0].action == ActionType.REPLACE_FOOTPRINT
        assert exc.candidates[0].params == {
            "old": "Connector_USB:USB_B_Micro",
            "new": "Connector_USB:USB_Micro-B_Molex-105017-0001",
        }
        assert "subject.suggested_footprints" in exc.retry_hint
        assert "preserve the user's product intent" in exc.retry_hint
        assert "EDA_FOOTPRINTS" in exc.retry_hint

    def test_crash_stage_infers_partial_artifacts(self, tmp_path):
        (tmp_path / "amp.kicad_sch").write_text("(schematic)")
        (tmp_path / "amp.kicad_pcb").write_text("(pcb)")

        assert _infer_crash_stage({}, tmp_path) == "after_pcb_write"

    def test_manufacturing_gate_requires_all_fab_outputs(self):
        mfg = {"gerbers": True, "gerber_files": ["board-F_Cu.gbr", "board.drl"]}

        missing = _missing_manufacturing_outputs(mfg)
        exc = _manufacturing_output_exception(mfg)

        assert missing == ["bom.csv", "cpl.csv"]
        assert exc.code == ExcCode.MANUFACTURING_OUTPUT_FAILURE
        assert exc.severity == Severity.ERROR
        assert exc.subject["missing_outputs"] == missing

    def test_terminal_clash_crash_maps_to_schematic_routing_failure(self):
        stderr = "\n".join([
            "Traceback (most recent call last):",
            "  File \"/app/src/skidl/schematics/route.py\", line 495, in add_terminal",
            "    raise TerminalClashException",
            "skidl.schematics.route.TerminalClashException",
        ])

        exc = crash_exception("TerminalClashException: ", stderr, stage="engine_worker")

        assert exc.code == ExcCode.SCH_ROUTING_FAILURE
        assert exc.severity == Severity.FATAL
        assert exc.subject["stage"] == "schematic_routing"
        assert exc.subject["exception"] == "TerminalClashException"
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert "schematic renderer limitation" in exc.retry_hint

    def test_schematic_route_assertion_maps_to_schematic_routing_failure(self):
        stderr = "\n".join([
            "Traceback (most recent call last):",
            "  File \"/app/src/skidl/schematics/route.py\", line 703, in audit",
            "    assert len(self.switchboxes) <= 2",
            "AssertionError",
        ])

        exc = crash_exception("AssertionError: ", stderr, stage="schematic_generation")

        assert exc.code == ExcCode.SCH_ROUTING_FAILURE
        assert exc.severity == Severity.FATAL
        assert exc.subject["stage"] == "schematic_routing"
        assert exc.subject["exception"] == "AssertionError"
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert "schematic renderer limitation" in exc.retry_hint

    def test_after_pcb_crash_maps_to_post_artifact_failure(self):
        exc = crash_exception(
            "worker exited with status -11",
            "segmentation fault after writing PCB",
            stage="after_pcb_write",
            artifact_keys=["board.kicad_pcb", "board.kicad_sch"],
        )

        assert exc.code == ExcCode.POST_ARTIFACT_FAILURE
        assert exc.severity == Severity.ERROR
        assert exc.subject["stage"] == "after_pcb_write"
        assert exc.subject["partial_artifacts"] == [
            "board.kicad_pcb",
            "board.kicad_sch",
        ]
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert "Fetch the run artifacts" in exc.retry_hint

    def test_timeout_exception_keeps_partial_artifact_context(self):
        exc = timeout_exception(
            240.0,
            artifact_keys=["board.kicad_sch", "board.kicad_pcb"],
            stderr="worker log tail",
        )

        assert exc.code == ExcCode.ENGINE_TIMEOUT
        assert exc.subject["stage"] == "timeout"
        assert exc.subject["timeout_s"] == 240.0
        assert exc.subject["partial_artifacts"] == [
            "board.kicad_pcb",
            "board.kicad_sch",
        ]
        assert "worker log tail" in exc.subject["stderr_tail"]
        assert "backend/runtime feedback" in exc.candidates[0].human_summary
        assert "partial_artifacts" in exc.retry_hint

    def test_post_artifact_failure_metrics_are_definitive(self):
        exc = DesignException(
            id="e-post",
            code=ExcCode.POST_ARTIFACT_FAILURE,
            severity=Severity.ERROR,
            message="post artifact failure",
        )

        metrics = _manufacturing_metrics({}, [exc], ok=False)

        assert metrics["manufacturable"] is False
        assert metrics["manufacturing_complete"] is False

    def test_valid_pin_in_bad_net_expression_is_not_reported_missing(self):
        class FakePin:
            def __init__(self, name, num):
                self.name = name
                self.num = num
                self.aliases = []

        class FakePart:
            ref = "U2"
            name = "ATmega328P-A"
            value = "ATmega328P-A"
            pins = [FakePin("PB4", "18"), FakePin("GND", "8")]

        class FakeExecError:
            original = TypeError(
                "unsupported operand type(s) for +: 'Net' and 'Pin'"
            )
            line = 80
            line_text = 'prog[1] += Net("MISO") + mcu["PB4"]'
            namespace = {"mcu": FakePart()}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert "does not connect a Net and Pin" in exc.message
        assert "pin 'PB4' not found" not in exc.message
        assert exc.subject["pin"] == "PB4"
        assert "net += pin1, pin2" in exc.retry_hint

    def test_syntax_error_reports_line_and_skidl_connection_hint(self):
        err = SyntaxError(
            "'function call' is an illegal expression for augmented assignment",
            ("<string>", 7, 1, 'make_net()[0] += usb["CC1"]\n'),
        )

        exc = _code_exception_from_syntax(err)

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["line"] == 7
        assert exc.subject["line_text"] == 'make_net()[0] += usb["CC1"]'
        assert "net += pin1, pin2" in exc.retry_hint
        assert "no global `connect()` helper" in exc.retry_hint

    def test_connect_name_error_gets_skidl_specific_hint(self):
        class FakeExecError:
            original = NameError("name 'connect' is not defined")
            line = 11
            line_text = 'connect(usb["CC1"], r_cc1[1])'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["line"] == 11
        assert "does not provide a global connect() helper" in exc.retry_hint
        assert "vcc += u1['VCC'], c1[1]" in exc.retry_hint


class TestPowerAnalysisMetrics:
    @staticmethod
    def _part(ref, value="", pins=(), name="", description=""):
        from skidl.pin import pin_types

        class NetObj:
            def __init__(self, net_name):
                self.name = net_name

        class PinObj:
            def __init__(self, num, pin_name, net_name, func):
                self.num = str(num)
                self.name = pin_name
                self.net = NetObj(net_name) if net_name else None
                self.func = func if func is not None else pin_types.PASSIVE

        return SimpleNamespace(
            ref=ref,
            value=value,
            name=name or ref,
            description=description,
            footprint="",
            pins=[
                PinObj(num, pin_name, net_name, func)
                for num, pin_name, net_name, func in pins
            ],
        )

    @staticmethod
    def _circuit(parts):
        class CircuitObj:
            def __init__(self, parts):
                self.parts = parts

            def get_nets(self):
                nets = {}
                for part in self.parts:
                    for pin in getattr(part, "pins", []):
                        net = getattr(pin, "net", None)
                        if net is not None:
                            nets.setdefault(net.name, net)
                return list(nets.values())

        return CircuitObj(parts)

    def test_metrics_include_power_tree_and_rail_sanity(self):
        from skidl.pin import pin_types

        circuit = self._circuit([
            self._part("BT1", "3xAAA", pins=[
                (1, "+", "VBAT", pin_types.PASSIVE),
                (2, "-", "GND", pin_types.PASSIVE),
            ]),
            self._part("U1", "AP2112K", description="linear regulator", pins=[
                (1, "VIN", "VBAT", pin_types.PWRIN),
                (2, "GND", "GND", pin_types.PWRIN),
                (5, "VOUT", "3V3", pin_types.PWROUT),
            ]),
            self._part("U2", "MCU", pins=[
                (1, "VDD", "3V3", pin_types.PWRIN),
                (2, "GND", "GND", pin_types.PWRIN),
            ]),
            self._part("C1", "10uF", pins=[
                (1, "1", "VBAT", None),
                (2, "2", "GND", None),
            ]),
            self._part("C2", "10uF", pins=[
                (1, "1", "3V3", None),
                (2, "2", "GND", None),
            ]),
        ])

        metrics = _metrics(circuit=circuit)
        analysis = metrics["power_analysis"]

        assert analysis["available"] is True
        assert analysis["power_tree"]["source_count"] == 1
        assert analysis["power_tree"]["regulator_count"] == 1
        assert analysis["power_tree"]["rail_count"] >= 2
        assert analysis["rail_sanity"]["unknown_rail_count"] >= 1

    def test_pullup_only_named_supply_has_no_ic_load_in_power_tree(self):
        from skidl.pin import pin_types

        circuit = self._circuit([
            self._part("U1", "ADS1115", pins=[
                (1, "GND", "GND", pin_types.PWRIN),
                (2, "SDA", "SDA", pin_types.BIDIR),
                (3, "SCL", "SCL", pin_types.BIDIR),
            ]),
            self._part("R1", "4.7k", pins=[
                (1, "1", "3V3", None),
                (2, "2", "SDA", None),
            ]),
            self._part("R2", "4.7k", pins=[
                (1, "1", "3V3", None),
                (2, "2", "SCL", None),
            ]),
        ])

        metrics = _metrics(circuit=circuit)
        findings = metrics["power_analysis"]["power_tree"]["findings"]

        assert not any(
            finding["category"] == "missing_source" and finding["rail"] == "3V3"
            for finding in findings
        )


class TestRoutingExceptions:
    def test_pcbnew_child_can_use_kicad_python_env(self, monkeypatch):
        seen = {}

        def fake_run(cmd, **kwargs):
            seen["cmd"] = cmd
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setenv("KICAD_PYTHON", "/opt/kicad-python")
        monkeypatch.setattr("subprocess.run", fake_run)

        _run_pcbnew_child("print('ok')")

        assert seen["cmd"][:2] == ["/opt/kicad-python", "-c"]
        assert seen["cmd"][2] == "print('ok')"

    def test_drc_unconnected_includes_examples_refs_and_retry_hint(self):
        report = {
            "unconnected_items": [
                {
                    "items": [
                        {"description": "Pad 1 [GND] of J1"},
                        {"description": "Track end [GND] near U2"},
                    ],
                    "pos": {"x": 10.0, "y": 12.5},
                },
                {
                    "items": [
                        {"description": "Pad 2 [SDA] of U3"},
                    ],
                },
            ],
            "violations": [],
        }

        exc = _drc_to_exceptions(report)[0]

        assert exc.code == ExcCode.DRC_UNCONNECTED
        assert exc.subject["nets"] == {"GND": 2, "SDA": 1}
        assert exc.subject["refs"] == ["J1", "U2", "U3"]
        assert exc.subject["examples"][0]["pos"] == {"x": 10.0, "y": 12.5}
        assert "not manufacturable" in exc.retry_hint
        assert "subject.examples" in exc.retry_hint
        assert [c.action for c in exc.candidates] == [
            ActionType.REGENERATE,
            ActionType.SET_LAYERS,
            ActionType.SCALE_OUTLINE,
        ]
        assert exc.candidates[-1].confidence < 0.5
        assert "Do not blindly grow" in exc.retry_hint

    def test_drc_short_hotspot_points_to_package_or_footprint(self):
        report = {
            "unconnected_items": [],
            "violations": [
                {
                    "type": "short",
                    "items": [
                        {"description": "Pad 8 [GND] of U2 on F.Cu"},
                        {"description": "Pad 9 [VCC] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "short",
                    "items": [
                        {"description": "Pad 8 [GND] of U2 on F.Cu"},
                        {"description": "Pad 10 [CLOCK] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "short",
                    "items": [
                        {"description": "Pad 8 [GND] of U2 on F.Cu"},
                        {"description": "Pad 11 [RESET] of U2 on F.Cu"},
                    ],
                },
            ],
        }

        exc = next(e for e in _drc_to_exceptions(report) if e.code == ExcCode.DRC_SHORT)

        assert exc.subject["refs"] == ["U2"]
        assert exc.subject["nets"]["GND"] == 3
        assert "mismatched symbol/footprint package" in exc.retry_hint
        assert "unrelated circuitry" in exc.retry_hint

    def test_drc_clearance_hotspot_points_to_package_or_footprint(self):
        report = {
            "unconnected_items": [],
            "violations": [
                {
                    "type": "clearance",
                    "items": [
                        {"description": "Pad 1 [VCC] of U2 on F.Cu"},
                        {"description": "Pad 2 [<no net>] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "clearance",
                    "items": [
                        {"description": "Pad 1 [VCC] of U2 on F.Cu"},
                        {"description": "Pad 3 [GND] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "clearance",
                    "items": [
                        {"description": "Pad 1 [VCC] of U2 on F.Cu"},
                        {"description": "Pad 4 [GPIO] of U2 on F.Cu"},
                    ],
                },
            ],
        }

        exc = next(e for e in _drc_to_exceptions(report) if e.code == ExcCode.DRC_CLEARANCE)

        assert exc.subject["refs"] == ["U2"]
        assert exc.subject["nets"] == {"VCC": 3, "GND": 1, "GPIO": 1}
        assert "too-tight package" in exc.retry_hint
        assert [c.action for c in exc.candidates] == [
            ActionType.REGENERATE,
            ActionType.SCALE_OUTLINE,
        ]
        assert exc.candidates[-1].confidence < 0.5

    def test_kicad_project_profile_uses_deterministic_prototype_rules(self, tmp_path):
        pcb = tmp_path / "board.kicad_pcb"
        pcb.write_text("(kicad_pcb)\n")
        existing = {
            "meta": {"filename": "board.kicad_pro", "version": 3},
            "board": {
                "design_settings": {
                    "rules": {
                        "min_hole_clearance": 0.25,
                        "min_through_hole_diameter": 0.3,
                    },
                },
            },
        }
        pcb.with_suffix(".kicad_pro").write_text(json.dumps(existing))

        pro = _ensure_kicad_project_profile(str(pcb))
        data = json.loads(pro.read_text())
        rules = data["board"]["design_settings"]["rules"]

        assert rules["min_hole_clearance"] == 0.15
        assert rules["min_through_hole_diameter"] == 0.2

    def test_route_timeout_suggests_router_budget_retry(self, monkeypatch, tmp_path):
        original_exists = Path.exists
        jar_path = tmp_path / "freerouting.jar"

        def fake_exists(path: Path) -> bool:
            if str(path) == str(jar_path):
                return True
            return original_exists(path)

        def fake_run(cmd, **kwargs):
            if len(cmd) >= 3 and "ExportSpecctraDSN" in cmd[2]:
                (tmp_path / "timeout.dsn").write_text("(dsn)")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            assert kwargs["timeout"] == 120
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/java")
        monkeypatch.setenv("FREEROUTING_JAR", str(jar_path))
        monkeypatch.setattr(Path, "exists", fake_exists)
        monkeypatch.setattr("subprocess.run", fake_run)

        exceptions = _route_pcb(str(tmp_path / "timeout.kicad_pcb"), timeout_s=120)

        assert len(exceptions) == 1
        exc = exceptions[0]
        assert exc.code == ExcCode.ROUTE_TIMEOUT
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert exc.candidates[0].params == {
            "run_options": {"route_timeout_s": 240.0},
        }
        assert "route_timeout_s=240" in exc.retry_hint

    def test_route_unrouted_stdout_defers_to_drc_after_ses_import(self, monkeypatch, tmp_path):
        original_exists = Path.exists
        pcb_path = tmp_path / "unrouted.kicad_pcb"
        dsn_path = tmp_path / "unrouted.dsn"
        ses_path = tmp_path / "unrouted.ses"
        jar_path = tmp_path / "freerouting.jar"

        def fake_exists(path: Path) -> bool:
            if str(path) == str(jar_path):
                return True
            return original_exists(path)

        def fake_run(cmd, **kwargs):
            if len(cmd) >= 3 and "ExportSpecctraDSN" in cmd[2]:
                dsn_path.write_text("(dsn)")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "/usr/bin/java":
                ses_path.write_text("(ses)")
                return subprocess.CompletedProcess(cmd, 0, "3 unrouted", "")
            if len(cmd) >= 3 and "ImportSpecctraSES" in cmd[2]:
                return subprocess.CompletedProcess(cmd, 0, "", "")
            raise AssertionError(cmd)

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/java")
        monkeypatch.setenv("FREEROUTING_JAR", str(jar_path))
        monkeypatch.setattr(Path, "exists", fake_exists)
        monkeypatch.setattr("subprocess.run", fake_run)

        exceptions = _route_pcb(str(pcb_path), timeout_s=120)

        assert exceptions == []

    def test_dsn_export_segfault_is_route_unavailable(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _export_dsn_with_pcbnew(
            str(tmp_path / "segfault.kicad_pcb"),
            str(tmp_path / "segfault.dsn"),
        )

        assert exc is not None
        assert exc.code == ExcCode.ROUTE_UNAVAILABLE
        assert exc.subject["stage"] == "dsn_export"
        assert exc.subject["returncode"] == -11
        assert "signal 11" in exc.message
        assert "routing/export tool failure" in exc.retry_hint

    def test_dsn_export_signal_retries_without_footprint_zones(self, monkeypatch, tmp_path):
        pcb_path = tmp_path / "zone-crash.kicad_pcb"
        dsn_path = tmp_path / "zone-crash.dsn"
        pcb_path.write_text(
            '(kicad_pcb\n'
            '  (version 20241229)\n'
            '  (footprint "USB_Test"\n'
            '    (uuid "11111111-1111-1111-1111-111111111111")\n'
            '    (zone\n'
            '      (net 0)\n'
            '      (net_name "")\n'
            '      (layer "F.Cu")\n'
            '      (uuid "22222222-2222-2222-2222-222222222222")\n'
            '      (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1))))\n'
            '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))))\n'
        )
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")
            assert ".dsn_export_sanitized.kicad_pcb" in cmd[2]
            dsn_path.write_text("(dsn)")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _export_dsn_with_pcbnew(str(pcb_path), str(dsn_path))

        assert exc is None
        assert len(calls) == 2
        sanitized = tmp_path / "zone-crash.dsn_export_sanitized.kicad_pcb"
        assert sanitized.exists()
        assert "(zone" not in sanitized.read_text()

    def test_dsn_export_signal_retries_without_kicad9_pad_metadata(self, monkeypatch, tmp_path):
        pcb_path = tmp_path / "metadata-crash.kicad_pcb"
        dsn_path = tmp_path / "metadata-crash.dsn"
        pcb_path.write_text(
            '(kicad_pcb\n'
            '  (version 20241229)\n'
            '  (footprint "QFN_Test"\n'
            '    (uuid "11111111-1111-1111-1111-111111111111")\n'
            '    (pad "EP" smd rect\n'
            '      (at 0 0)\n'
            '      (size 2 2)\n'
            '      (layers "F.Cu" "F.Paste" "F.Mask")\n'
            '      (property "pad_prop_heatsink" (uuid "22222222-2222-2222-2222-222222222222"))\n'
            '      (zone_connect 2))\n'
            '    (pad "1" smd rect (at 1 0) (size 0.5 0.5) (layers "F.Cu"))))\n'
        )
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")
            assert ".dsn_export_sanitized.kicad_pcb" in cmd[2]
            dsn_path.write_text("(dsn)")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _export_dsn_with_pcbnew(str(pcb_path), str(dsn_path))

        assert exc is None
        assert len(calls) == 2
        sanitized = tmp_path / "metadata-crash.dsn_export_sanitized.kicad_pcb"
        assert sanitized.exists()
        sanitized_text = sanitized.read_text()
        assert "pad_prop_heatsink" not in sanitized_text
        assert "(zone_connect" not in sanitized_text
        assert '(pad "EP"' in sanitized_text

    def test_ses_import_segfault_is_route_unavailable(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _import_ses_with_pcbnew(
            str(tmp_path / "routed.kicad_pcb"),
            str(tmp_path / "routed.ses"),
        )

        assert exc is not None
        assert exc.code == ExcCode.ROUTE_UNAVAILABLE
        assert exc.subject["stage"] == "ses_import"
        assert exc.subject["returncode"] == -11
        assert "signal 11" in exc.message

    def test_ses_import_signal_retries_with_sanitized_board(self, monkeypatch, tmp_path):
        pcb_path = tmp_path / "metadata-import-crash.kicad_pcb"
        ses_path = tmp_path / "metadata-import-crash.ses"
        pcb_path.write_text(
            '(kicad_pcb\n'
            '  (version 20241229)\n'
            '  (footprint "QFN_Test"\n'
            '    (uuid "11111111-1111-1111-1111-111111111111")\n'
            '    (pad "EP" smd rect\n'
            '      (at 0 0)\n'
            '      (size 2 2)\n'
            '      (layers "F.Cu" "F.Paste" "F.Mask")\n'
            '      (property "pad_prop_heatsink" (uuid "22222222-2222-2222-2222-222222222222"))\n'
            '      (zone_connect 2))\n'
            '    (pad "1" smd rect (at 1 0) (size 0.5 0.5) (layers "F.Cu"))))\n'
        )
        ses_path.write_text("(ses)")
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")
            assert ".ses_import_sanitized.kicad_pcb" in cmd[2]
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _import_ses_with_pcbnew(str(pcb_path), str(ses_path))

        assert exc is None
        assert len(calls) == 2
        pcb_text = pcb_path.read_text()
        assert "pad_prop_heatsink" not in pcb_text
        assert "(zone_connect" not in pcb_text
        assert '(pad "EP"' in pcb_text

    def test_route_import_segfault_does_not_crash_worker(self, monkeypatch, tmp_path):
        original_exists = Path.exists
        pcb_path = tmp_path / "import-crash.kicad_pcb"
        dsn_path = tmp_path / "import-crash.dsn"
        ses_path = tmp_path / "import-crash.ses"
        jar_path = tmp_path / "freerouting.jar"

        def fake_exists(path: Path) -> bool:
            if str(path) == str(jar_path):
                return True
            return original_exists(path)

        def fake_run(cmd, **kwargs):
            if len(cmd) >= 3 and "ExportSpecctraDSN" in cmd[2]:
                dsn_path.write_text("(dsn)")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "/usr/bin/java":
                ses_path.write_text("(ses)")
                return subprocess.CompletedProcess(cmd, 0, "0 unrouted", "")
            if len(cmd) >= 3 and "ImportSpecctraSES" in cmd[2]:
                return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")
            raise AssertionError(cmd)

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/java")
        monkeypatch.setenv("FREEROUTING_JAR", str(jar_path))
        monkeypatch.setattr(Path, "exists", fake_exists)
        monkeypatch.setattr("subprocess.run", fake_run)

        exceptions = _route_pcb(str(pcb_path), timeout_s=120)

        assert len(exceptions) == 1
        exc = exceptions[0]
        assert exc.code == ExcCode.ROUTE_UNAVAILABLE
        assert exc.subject["stage"] == "ses_import"
        assert exc.subject["returncode"] == -11


class TestFloorplanParsing:
    def test_floorplan_docs_include_agent_visible_grid_example(self):
        from mcp_server import server_http

        doc_text = Path("docs/floorplan-intent.md").read_text()
        guide_text = server_http.SKIDL_GUIDE
        tool_text = server_http.submit_skidl_code.__doc__ or ""

        for text in (doc_text, guide_text, tool_text):
            for needle in (
                "EDA_FLOORPLAN",
                "grid",
                "align",
                "distribute",
                "assembly_sides",
                "keepouts",
                "cutouts",
                "outline",
            ):
                assert needle in text

    def test_floorplan_parses_agent_friendly_layout_intent(self):
        constraints, meta = engine_worker_mod._floorplan_constraints(
            {
                "outline": {
                    "x_min": -2.0,
                    "y_min": 1.0,
                    "x_max": 50.0,
                    "y_max": 35.0,
                    "corner_radius_mm": 1.5,
                },
                "fixed_positions": [
                    {
                        "ref": "U1",
                        "x_mm": 30.0,
                        "y_mm": 20.0,
                        "rotation_deg": 90.0,
                        "side": "back",
                    },
                ],
                "edge_anchors": [
                    {
                        "ref": "J1",
                        "edge": "bottom",
                        "offset_mm": 24.0,
                        "rotation_deg": 180.0,
                        "side": "front",
                    },
                ],
                "grid": {
                    "refs": ["SW1", "SW2", "SW3", "SW4"],
                    "rows": 2,
                    "cols": 2,
                    "x_mm": 10.0,
                    "y_mm": 12.0,
                    "dx_mm": 14.0,
                    "dy_mm": 9.0,
                    "side": "front",
                },
                "align": [{"refs": ["LED1", "LED2"], "axis": "y", "value_mm": 6.0}],
                "distribute": [
                    {
                        "refs": ["LED1", "LED2", "LED3"],
                        "axis": "x",
                        "start_mm": 8.0,
                        "end_mm": 36.0,
                    }
                ],
                "assembly_sides": {"U2": "back"},
                "keepouts": [
                    {
                        "x_min": 0.0,
                        "y_min": 0.0,
                        "x_max": 5.0,
                        "y_max": 35.0,
                        "allowed_refs": "H1,H2",
                    }
                ],
            }
        )

        fixed_by_ref = {item.ref: item for item in constraints.fixed}
        assert fixed_by_ref["U1"].rot_deg == pytest.approx(90.0)
        assert fixed_by_ref["SW1"].x_mm == pytest.approx(10.0)
        assert fixed_by_ref["SW1"].y_mm == pytest.approx(12.0)
        assert fixed_by_ref["SW4"].x_mm == pytest.approx(24.0)
        assert fixed_by_ref["SW4"].y_mm == pytest.approx(21.0)

        assert constraints.edge_anchors[0].ref == "J1"
        assert constraints.edge_anchors[0].edge == "bottom"
        assert constraints.edge_anchors[0].offset_mm == pytest.approx(24.0)
        assert constraints.keepouts[0].allowed_refs == ["H1", "H2"]
        assert constraints.outline.x_min == pytest.approx(-2.0)
        assert constraints.outline.y_min == pytest.approx(1.0)
        assert constraints.outline.x_max == pytest.approx(50.0)
        assert constraints.outline.y_max == pytest.approx(35.0)
        assert constraints.outline.corner_radius_mm == pytest.approx(1.5)
        assert len(constraints.align) == 5
        assert len(constraints.distribute) == 5

        assert meta["fixed_positions"] == 5
        assert meta["fixed_refs"] == ["SW1", "SW2", "SW3", "SW4", "U1"]
        assert meta["explicit_fixed_refs"] == ["U1"]
        assert meta["edge_anchors"] == 1
        assert meta["keepouts"] == 1
        assert meta["outline"] == "explicit"
        assert meta["grids"] == 1
        assert meta["grid_refs"] == ["SW1", "SW2", "SW3", "SW4"]
        assert meta["grid_fixed_positions"] == 4
        assert meta["align_constraints"] == 5
        assert meta["distribute_constraints"] == 5
        assert meta["assembly_sides"]["U1"] == "back"
        assert meta["assembly_sides"]["U2"] == "back"
        assert meta["assembly_sides"]["SW4"] == "front"
        assert meta["assembly_side_counts"] == {"back": 2, "front": 5}

    def test_floorplan_accepts_layout_constraint_objects(self):
        from skidl.layout import (
            AlignConstraint,
            BoardCutout,
            BoardOutline,
            DistributeConstraint,
            EdgeAnchor,
            FixedPosition,
            KeepOut,
            LayoutConstraints,
        )

        constraints, meta = engine_worker_mod._floorplan_constraints(
            LayoutConstraints(
                outline=BoardOutline(65.0, 40.0, corner_radius_mm=1.25),
                fixed=[
                    FixedPosition("U_DAISY", 32.5, 22.0, 180.0),
                    FixedPosition("OLED1", 48.0, 10.0, 0.0),
                ],
                edge_anchors=[
                    EdgeAnchor("J_USB", "bottom", offset_mm=32.0, inset_mm=0.75, rot_deg=180.0),
                ],
                keepouts=[
                    KeepOut(0.0, 0.0, 65.0, 4.0, allowed_refs=["H1", "H2"]),
                ],
                cutouts=[
                    BoardCutout(12.0, 7.0, 28.0, 15.0, name="display_window"),
                ],
                align=[
                    AlignConstraint(["LED1", "LED2"], "y", 5.0),
                ],
                distribute=[
                    DistributeConstraint(["LED1", "LED2", "LED3"], "x", 8.0, 34.0),
                ],
            )
        )

        assert constraints is not None
        assert constraints.outline.width_mm == pytest.approx(65.0)
        assert constraints.outline.height_mm == pytest.approx(40.0)
        assert constraints.outline.corner_radius_mm == pytest.approx(1.25)
        assert [item.ref for item in constraints.fixed] == ["U_DAISY", "OLED1"]
        assert constraints.edge_anchors[0].ref == "J_USB"
        assert constraints.edge_anchors[0].edge == "bottom"
        assert constraints.edge_anchors[0].offset_mm == pytest.approx(32.0)
        assert constraints.edge_anchors[0].inset_mm == pytest.approx(0.75)
        assert constraints.keepouts[0].allowed_refs == ["H1", "H2"]
        assert constraints.cutouts[0].name == "display_window"
        assert constraints.align[0].refs == ["LED1", "LED2"]
        assert constraints.distribute[0].refs == ["LED1", "LED2", "LED3"]
        assert meta["outline"] == "explicit"
        assert meta["fixed_refs"] == ["OLED1", "U_DAISY"]
        assert meta["explicit_fixed_refs"] == ["OLED1", "U_DAISY"]
        assert meta["edge_anchor_refs"] == [
            {
                "ref": "J_USB",
                "edge": "bottom",
                "offset_mm": 32.0,
                "rot_deg": 180.0,
            }
        ]
        assert meta["keepouts"] == 1
        assert meta["cutouts"] == 1
        assert meta["cutout_shapes"][0]["name"] == "display_window"
        assert meta["align_constraints"] == 1
        assert meta["distribute_constraints"] == 1

    def test_floorplan_parses_cutouts_apertures_and_slots_as_geometry(self):
        constraints, meta = engine_worker_mod._floorplan_constraints(
            {
                "cutouts": [
                    {
                        "name": "sensor_lattice_window",
                        "x_min": 10.0,
                        "y_min": 12.0,
                        "x_max": 18.0,
                        "y_max": 22.0,
                    },
                    {
                        "name": "polygon_window",
                        "vertices": [
                            [30.0, 10.0],
                            [40.0, 12.0],
                            [38.0, 22.0],
                            [30.0, 20.0],
                        ],
                    },
                ],
                "apertures": [
                    {
                        "shape": "circle",
                        "center_x_mm": 55.0,
                        "center_y_mm": 16.0,
                        "diameter_mm": 6.0,
                    }
                ],
                "slots": [
                    {
                        "name": "oled_ribbon_slot",
                        "start": [70.0, 12.0],
                        "end": [82.0, 12.0],
                        "width_mm": 3.0,
                    }
                ],
            }
        )

        assert constraints is not None
        assert len(constraints.cutouts) == 4
        assert meta["cutouts"] == 4
        assert [item["shape"] for item in meta["cutout_shapes"]] == [
            "rect",
            "polygon",
            "circle",
            "slot",
        ]
        circle = constraints.cutouts[2]
        assert circle.radius_mm == pytest.approx(3.0)
        slot = constraints.cutouts[3]
        assert slot.x_min == pytest.approx(68.5)
        assert slot.x_max == pytest.approx(83.5)

    def test_floorplan_keepout_bands_imply_coordinate_frame_outline(self):
        constraints, meta = engine_worker_mod._floorplan_constraints(
            {
                "fixed_positions": [
                    {"ref": "SW1", "x_mm": 24.0, "y_mm": 18.0},
                    {"ref": "H1", "x_mm": 6.5, "y_mm": 6.5},
                    {"ref": "H4", "x_mm": 108.5, "y_mm": 58.5},
                ],
                "keepouts": [
                    {"x_min": 0.0, "y_min": 0.0, "x_max": 115.0, "y_max": 4.0},
                    {"x_min": 0.0, "y_min": 61.0, "x_max": 115.0, "y_max": 65.0},
                ],
            }
        )

        assert constraints is not None
        assert constraints.outline is not None
        assert constraints.outline.x_min == pytest.approx(0.0)
        assert constraints.outline.y_min == pytest.approx(0.0)
        assert constraints.outline.x_max == pytest.approx(115.0)
        assert constraints.outline.y_max == pytest.approx(65.0)
        assert meta["outline"] == "keepout_bands"

    def test_floorplan_corner_mounting_holes_imply_board_outline(self):
        constraints, meta = engine_worker_mod._floorplan_constraints(
            {
                "fixed_positions": [
                    {"ref": "U1", "x_mm": 38.0, "y_mm": 30.0},
                    {"ref": "H1", "x_mm": 2.7, "y_mm": 2.7},
                    {"ref": "H2", "x_mm": 87.3, "y_mm": 2.7},
                    {"ref": "H3", "x_mm": 2.7, "y_mm": 67.3},
                    {"ref": "H4", "x_mm": 87.3, "y_mm": 67.3},
                ],
                "edge_anchors": [
                    {"ref": "J1", "edge": "right", "offset_mm": 18.0},
                ],
            }
        )

        assert constraints is not None
        assert constraints.outline is not None
        assert constraints.outline.x_min == pytest.approx(0.0)
        assert constraints.outline.y_min == pytest.approx(0.0)
        assert constraints.outline.x_max == pytest.approx(90.0)
        assert constraints.outline.y_max == pytest.approx(70.0)
        assert meta["outline"] == "mounting_holes"

    def test_floorplan_single_internal_keepout_does_not_imply_outline(self):
        constraints, meta = engine_worker_mod._floorplan_constraints(
            {
                "fixed_positions": [
                    {"ref": "U1", "x_mm": 20.0, "y_mm": 20.0},
                ],
                "keepouts": [
                    {"x_min": 8.0, "y_min": 8.0, "x_max": 12.0, "y_max": 12.0},
                ],
            }
        )

        assert constraints is not None
        assert constraints.outline is None
        assert "outline" not in meta

    def test_code_mode_floorplan_sides_and_outline_reach_layout(self, monkeypatch, tmp_path):
        import skidl.layout as layout_mod

        floorplan = {
            "outline": {"width_mm": 42.0, "height_mm": 24.0},
            "fixed_positions": [
                {"ref": "U1", "x_mm": 18.0, "y_mm": 12.0, "side": "back"},
            ],
            "edge_anchors": [
                {"ref": "J1", "edge": "right", "offset_mm": 12.0, "side": "front"},
            ],
            "grid": {
                "refs": ["SW1", "SW2", "SW3", "SW4"],
                "rows": 2,
                "cols": 2,
                "x_mm": 8.0,
                "y_mm": 6.0,
                "dx_mm": 12.0,
                "dy_mm": 10.0,
                "side": "front",
            },
            "align": [{"refs": ["D1", "D2"], "axis": "y"}],
            "distribute": [{"refs": ["D1", "D2", "D3"], "axis": "x"}],
            "keepouts": [{"x_min": 0.0, "y_min": 0.0, "x_max": 3.0, "y_max": 24.0}],
            "cutouts": [
                {"name": "sensor_window", "x_min": 10.0, "y_min": 8.0, "x_max": 16.0, "y_max": 14.0}
            ],
            "assembly_sides": {"U2": "back"},
        }
        refs = ["U1", "U2", "J1", "SW1", "SW2", "SW3", "SW4", "D1", "D2", "D3"]
        circuit = SimpleNamespace(parts=[SimpleNamespace(ref=ref, pins=[]) for ref in refs])
        circuit.generate_schematic = lambda *args, **kwargs: None
        captured = {}

        class FakeScore:
            ok = True
            score = 100.0
            total_hpwl_mm = 0.0
            congestion_score = 0.0

            def to_dict(self):
                return {"ok": True}

        class FakeLayoutResult:
            ok = True
            candidates = []
            score = FakeScore()
            validation = SimpleNamespace(
                ok=True,
                overlaps=[],
                outline_violations=[],
                keepout_violations=[],
                missing_refs=[],
                total_parts=len(refs),
                placed_parts=len(refs),
            )

            def __init__(self, outline):
                self.outline = outline
                self.cutouts = list(captured["constraints"].cutouts)
                self.placed_parts = [
                    SimpleNamespace(
                        ref=part.ref,
                        x_mm=0.0,
                        y_mm=0.0,
                        rot_deg=0.0,
                        footprint="Fake:Part",
                        side=getattr(part, "assembly_side", "front"),
                    )
                    for part in circuit.parts
                ]

            def to_dict(self):
                return {
                    "ok": True,
                    "outline": {
                        "width_mm": self.outline.width_mm,
                        "height_mm": self.outline.height_mm,
                    },
                    "placed_parts": [
                        {"ref": part.ref, "side": part.side}
                        for part in self.placed_parts
                    ],
                    "cutouts": [cutout.to_dict() for cutout in self.cutouts],
                    "intent_plan": {
                        "assembly_sides": {
                            part.ref: getattr(part, "assembly_side", "front")
                            for part in circuit.parts
                            if hasattr(part, "assembly_side")
                        },
                        "edge_anchors": [
                            {
                                "ref": part.ref,
                                "edge": getattr(part, "edge_preference"),
                            }
                            for part in circuit.parts
                            if hasattr(part, "edge_preference")
                        ],
                    },
                    "validation": {"ok": True},
                    "score": {"ok": True},
                }

            def summary(self):
                return "fake layout"

        def fake_plan_layout(circuit_arg, *, fp_lib_dirs, constraints, **kwargs):
            captured["constraints"] = constraints
            captured["part_sides"] = {
                part.ref: getattr(part, "assembly_side", None)
                for part in circuit_arg.parts
            }
            captured["part_edges"] = {
                part.ref: getattr(part, "edge_preference", None)
                for part in circuit_arg.parts
            }
            return FakeLayoutResult(constraints.outline)

        def fake_write_kicad_pcb(_placed, _circuit, _fp_dirs, _output_path, **kwargs):
            captured["write_cutouts"] = kwargs.get("cutouts")

        monkeypatch.setattr(
            engine_worker_mod,
            "_exec_skidl_with_namespace",
            lambda code: (circuit, {"EDA_FLOORPLAN": floorplan}),
        )
        monkeypatch.setattr(engine_worker_mod, "_circuit_to_spec_dict", lambda circuit: {})
        monkeypatch.setattr(engine_worker_mod, "enrich_blocks", lambda spec, marketing: (spec, []))
        monkeypatch.setattr(engine_worker_mod, "enrich_spec", lambda spec: (spec, []))
        monkeypatch.setattr(engine_worker_mod, "design_review_exceptions", lambda *a, **k: [])
        monkeypatch.setattr(engine_worker_mod, "_preflight_footprints", lambda *a, **k: None)
        monkeypatch.setattr(engine_worker_mod, "_write_inline_footprints", lambda *a, **k: (None, {"count": 0}))
        monkeypatch.setattr(engine_worker_mod, "_metrics", lambda *a, **k: {})
        monkeypatch.setattr(engine_worker_mod, "layout_exceptions", lambda *a, **k: [])
        monkeypatch.setattr(engine_worker_mod, "_skidl_layout_intent_advisories", lambda *a, **k: [])
        monkeypatch.setattr(layout_mod, "plan_layout", fake_plan_layout)
        monkeypatch.setattr(layout_mod, "write_kicad_pcb", fake_write_kicad_pcb)
        monkeypatch.setattr(engine_worker_mod, "_generate_board_previews", lambda *a, **k: {})
        monkeypatch.setattr(engine_worker_mod, "_write_layout_mockup_svg", lambda *a, **k: None)
        monkeypatch.setattr(engine_worker_mod, "_add_layout_mockup_preview", lambda *a, **k: None)

        cwd = os.getcwd()
        try:
            result = _run_skidl_code({
                "run_id": "floorplan-code-mode",
                "out_dir": str(tmp_path / "floorplan-code-mode"),
                "code": "from skidl import *",
                "board_name": "floorplan-code-mode",
                "outline_mm": [99.0, 88.0],
                "pipeline_goal": "placement_review",
            })
        finally:
            os.chdir(cwd)

        constraints = captured["constraints"]
        assert constraints.outline.width_mm == pytest.approx(42.0)
        assert constraints.outline.height_mm == pytest.approx(24.0)
        assert len(constraints.fixed) == 5
        assert len(constraints.edge_anchors) == 1
        assert len(constraints.keepouts) == 1
        assert len(constraints.cutouts) == 1
        assert len(constraints.align) == 5
        assert len(constraints.distribute) == 5
        assert captured["part_sides"]["U1"] == "back"
        assert captured["part_sides"]["U2"] == "back"
        assert captured["part_sides"]["SW4"] == "front"
        assert captured["part_edges"]["J1"] == "right"
        assert result["layout"]["floorplan"]["outline"] == "explicit"
        assert result["layout"]["floorplan"]["fixed_positions"] == 5
        assert result["layout"]["floorplan"]["fixed_refs"] == [
            "SW1",
            "SW2",
            "SW3",
            "SW4",
            "U1",
        ]
        assert result["layout"]["floorplan"]["explicit_fixed_refs"] == ["U1"]
        assert result["layout"]["floorplan"]["grid_refs"] == [
            "SW1",
            "SW2",
            "SW3",
            "SW4",
        ]
        assert result["layout"]["floorplan"]["edge_anchor_refs"] == [
            {
                "ref": "J1",
                "edge": "right",
                "offset_mm": 12.0,
                "rot_deg": None,
            }
        ]
        assert result["layout"]["floorplan"]["cutouts"] == 1
        assert result["layout"]["cutouts"][0]["name"] == "sensor_window"
        assert captured["write_cutouts"][0].name == "sensor_window"
        assert result["layout"]["floorplan"]["assembly_side_counts"] == {
            "back": 2,
            "front": 5,
        }
        assert result["layout"]["intent_plan"]["assembly_sides"]["U1"] == "back"
        assert result["layout"]["intent_plan"]["edge_anchors"] == [
            {"ref": "J1", "edge": "right"}
        ]


@needs_kicad
class TestWorkerAndPipeline:
    def test_engine_worker_echo_pipe_outputs_json(self, tmp_path):
        run_dir = tmp_path / "worker-run"
        envelope = {
            "run_id": "worker-run",
            "out_dir": str(run_dir),
            "spec": trivial_spec(),
        }
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_server.engine_worker"],
            input=json.dumps(envelope),
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["run_id"] == "worker-run"
        assert "metrics" in payload
        assert "outputs" in payload
        assert "layout" in payload
        assert payload["artifacts"].get("pcb", "").endswith(".kicad_pcb")

    def test_pipeline_smoke(self, tmp_path):
        response = run_pipeline(trivial_spec(), tmp_path, timeout_s=120)

        assert response.run_id
        assert response.stage in {"complete", "translate"}
        assert (tmp_path / response.run_id / "spec.json").exists()
        assert response.metrics["cpu_time_s"] >= 0.0
        if response.ok:
            assert os.path.exists(response.outputs["pcb"])
            assert os.path.exists(response.artifacts["pcb"])


class TestPlacementReviewRuntime:
    def test_circuit_spec_placement_review_skips_routing_and_full_previews(
        self, monkeypatch, tmp_path
    ):
        import skidl.layout as layout_mod

        run_dir = tmp_path / "placement-review-spec"

        class FakeCircuit:
            parts = [
                SimpleNamespace(
                    ref="R1",
                    footprint="Resistor_SMD:R_0603_1608Metric",
                    pins=[object(), object()],
                )
            ]

            def generate_schematic(self, filepath, top_name, **kwargs):
                Path(filepath, f"{top_name}.kicad_sch").write_text(
                    "(kicad_sch)",
                    encoding="utf-8",
                )

        class FakeLayoutResult:
            ok = True
            placed_parts = [
                PlacedPart(
                    "R1",
                    10.0,
                    10.0,
                    0.0,
                    "Resistor_SMD:R_0603_1608Metric",
                )
            ]
            outline = BoardOutline(30.0, 20.0)
            validation = SimpleNamespace(
                overlaps=[],
                outline_violations=[],
                keepout_violations=[],
                cutout_violations=[],
                missing_refs=[],
            )
            score = SimpleNamespace(
                score=95.0,
                total_hpwl_mm=0.0,
                congestion_score=0.0,
                warnings=[],
            )
            candidates = []

            def summary(self):
                return "fake placement review"

            def to_dict(self):
                return {
                    "ok": True,
                    "outline": {"width_mm": 30.0, "height_mm": 20.0},
                    "placed_parts": [
                        {"ref": "R1", "x_mm": 10.0, "y_mm": 10.0}
                    ],
                    "validation": {"ok": True},
                }

        def configure_env():
            os.environ["KICAD9_FOOTPRINT_DIR"] = str(tmp_path)

        def fake_write_pcb(placed, circuit, fp_dirs, output_path, **kwargs):
            Path(output_path).write_text("(kicad_pcb)", encoding="utf-8")

        def fail_route(*args, **kwargs):
            raise AssertionError("placement_review must not call routing")

        def fail_full_preview(*args, **kwargs):
            raise AssertionError("placement_review fast mode must not export full previews")

        def fake_mockup(layout_result, out_dir):
            (out_dir / "preview_assembly.svg").write_text(
                "<svg><rect width='10' height='10'/></svg>",
                encoding="utf-8",
            )
            return None

        def fake_rasterize(svg_path, png_path, width_px=1600):
            png_path.write_bytes(b"\x89PNG\r\n\x1a\nmockup")
            return None

        monkeypatch.setattr(engine_worker_mod, "_configure_kicad_env", configure_env)
        monkeypatch.setattr(engine_worker_mod, "_easyeda_fp_dirs", lambda: [])
        monkeypatch.setattr(
            engine_worker_mod,
            "translate",
            lambda spec, fp_dirs: SimpleNamespace(
                exceptions=[],
                circuit=FakeCircuit(),
            ),
        )
        monkeypatch.setattr(engine_worker_mod, "design_review_exceptions", lambda *a, **k: [])
        monkeypatch.setattr(engine_worker_mod, "_floorplan_intent_preflight_exception", lambda *a, **k: None)
        monkeypatch.setattr(engine_worker_mod, "layout_exceptions", lambda result: [])
        monkeypatch.setattr(layout_mod, "plan_layout", lambda *a, **k: FakeLayoutResult())
        monkeypatch.setattr(layout_mod, "write_kicad_pcb", fake_write_pcb)
        monkeypatch.setattr(engine_worker_mod, "_route_pcb", fail_route)
        monkeypatch.setattr(engine_worker_mod, "_generate_board_previews", fail_full_preview)
        monkeypatch.setattr(engine_worker_mod, "_write_layout_mockup_svg", fake_mockup)
        monkeypatch.setattr(engine_worker_mod, "_rasterize_svg_preview", fake_rasterize)
        monkeypatch.setattr(engine_worker_mod, "_brand_preview_png", lambda png_path: None)

        cwd = os.getcwd()
        try:
            result = engine_worker_mod.run({
                "run_id": "placement-review-spec",
                "out_dir": str(run_dir),
                "spec": trivial_spec(),
                "pipeline_goal": "placement_review",
            })
        finally:
            os.chdir(cwd)

        assert result["stage"] == "placement_review"
        assert result["metrics"]["pipeline_goal"] == "placement_review"
        assert result["metrics"]["preview_mode"] == "fast"
        assert result["artifacts"]["pcb"].endswith(".kicad_pcb")
        assert "manufacturing" not in result["artifacts"]
        assert result["artifacts"]["previews"]["mode"] == "fast"
        assert result["artifacts"]["previews"]["files"] == [
            "preview_assembly.svg",
            "preview_2d_top.png",
        ]
        assert any(
            exc["code"] == "PLACEMENT_REVIEW_ONLY"
            for exc in result["exceptions"]
        )


class TestInlineFootprintBundle:
    def test_inline_footprints_write_temporary_preflight_library(self, tmp_path):
        content = (
            '(footprint "R_Test" '
            '(pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu")))'
        )

        root, meta = _write_inline_footprints(
            {"TestLib:R_Test": content},
            tmp_path,
        )

        assert root == str(tmp_path / "_inline_footprints")
        assert meta == {"count": 1, "footprints": ["TestLib:R_Test"]}
        assert (
            tmp_path
            / "_inline_footprints"
            / "TestLib.pretty"
            / "R_Test.kicad_mod"
        ).read_text() == content

    def test_inline_footprints_merge_code_and_tool_parameter_bundles(self, tmp_path):
        code_content = '(footprint "From_Code" (layer "F.Cu"))'
        param_content = '(footprint "From_Param" (layer "F.Cu"))'

        root, meta = _write_inline_footprints(
            {"CodeLib:From_Code": code_content},
            tmp_path,
            extra_raw={"ParamLib:From_Param": param_content},
        )

        assert root == str(tmp_path / "_inline_footprints")
        assert meta == {
            "count": 2,
            "footprints": ["CodeLib:From_Code", "ParamLib:From_Param"],
        }
        assert (
            tmp_path
            / "_inline_footprints"
            / "CodeLib.pretty"
            / "From_Code.kicad_mod"
        ).read_text() == code_content
        assert (
            tmp_path
            / "_inline_footprints"
            / "ParamLib.pretty"
            / "From_Param.kicad_mod"
        ).read_text() == param_content

    def test_inline_footprints_reject_path_traversal(self, tmp_path):
        with pytest.raises(ValueError, match="invalid footprint"):
            _write_inline_footprints(
                {"../Bad:R_Test": '(footprint "R_Test")'},
                tmp_path,
            )

    def test_inline_footprints_reject_filesystem_path_payload(self, tmp_path):
        with pytest.raises(ValueError, match="does not accept filesystem paths"):
            _write_inline_footprints(
                [
                    {
                        "library": "TestLib",
                        "name": "R_Test",
                        "path": "/Users/example/project/TestLib.pretty/R_Test.kicad_mod",
                    }
                ],
                tmp_path,
            )

    def test_inline_footprints_reject_malformed_content_without_echoing_it(self, tmp_path):
        bad_content = 'not secret-token-123 (footprint "R_Test")'

        with pytest.raises(ValueError) as exc_info:
            _write_inline_footprints(
                {"TestLib:R_Test": bad_content},
                tmp_path,
            )

        message = str(exc_info.value)
        assert "must start with a KiCad (footprint ...)" in message
        assert "secret-token-123" not in message

    def test_inline_footprints_reject_name_mismatch(self, tmp_path):
        with pytest.raises(ValueError, match="footprint name mismatch"):
            _write_inline_footprints(
                {"TestLib:R_Test": '(footprint "Other_Name" (layer "F.Cu"))'},
                tmp_path,
            )

    def test_inline_footprints_reject_conflicting_duplicates(self, tmp_path):
        with pytest.raises(ValueError, match="duplicate custom footprint TestLib:R_Test"):
            _write_inline_footprints(
                {"TestLib:R_Test": '(footprint "R_Test" (layer "F.Cu"))'},
                tmp_path,
                extra_raw={"TestLib:R_Test": '(footprint "R_Test" (attr smd))'},
            )


def test_skidl_worker_schematic_terminal_clash_returns_stage_result(tmp_path, monkeypatch):
    from skidl.schematics.route import TerminalClashException

    class FakeCircuit:
        parts = [SimpleNamespace(ref="U1", footprint="", pins=[])]

        def generate_schematic(self, *args, **kwargs):
            raise TerminalClashException()

    monkeypatch.setattr(
        engine_worker_mod,
        "_exec_skidl_with_namespace",
        lambda code: (FakeCircuit(), {}),
    )
    monkeypatch.setattr(engine_worker_mod, "_circuit_to_spec_dict", lambda circuit: {})
    monkeypatch.setattr(
        engine_worker_mod,
        "enrich_blocks",
        lambda spec, marketing: (spec, []),
    )
    monkeypatch.setattr(engine_worker_mod, "enrich_spec", lambda spec: (spec, []))
    monkeypatch.setattr(engine_worker_mod, "design_review_exceptions", lambda *a, **k: [])
    monkeypatch.setattr(engine_worker_mod, "_preflight_footprints", lambda *a, **k: None)

    result = _run_skidl_code({
        "_mode": "skidl_python",
        "code": "# fake circuit from monkeypatch",
        "board_name": "schematic-clash",
        "outline_mm": [25.0, 20.0],
        "out_dir": str(tmp_path),
        "run_id": "schematic-clash",
    })

    assert result["run_id"] == "schematic-clash"
    assert result["stage"] == "schematic_generation"
    assert result["status"] == "failed"
    assert result["exceptions"][0]["code"] == ExcCode.SCH_ROUTING_FAILURE.value
    assert result["exceptions"][0]["subject"]["exception"] == "TerminalClashException"
    assert result["outputs"]["run_dir"] == str(tmp_path.resolve())


def test_kicad9_schematic_route_assertion_uses_labels_fallback(
    monkeypatch,
    tmp_path,
):
    import importlib

    import skidl
    from skidl import KICAD9
    from skidl.schematics.route import RoutingFailure

    gen_schematic_mod = importlib.import_module("skidl.tools.kicad9.gen_schematic")
    sexp_schematic_mod = importlib.import_module(
        "skidl.tools.kicad9.sexp_schematic"
    )
    sch_node_mod = importlib.import_module("skidl.schematics.sch_node")

    class FakeNet:
        name = "SIG"
        _stub = False
        _stub_explicit = False

        def get_pins(self):
            return []

    class FakeCircuit:
        nets = [FakeNet()]
        parts = []

    calls = {"route": 0}

    class FakeNode:
        def __init__(self, *_args, **_kwargs):
            pass

        def place(self, *_args, **_kwargs):
            pass

        def route(self, *_args, **_kwargs):
            calls["route"] += 1
            if calls["route"] <= 2:
                raise AssertionError("too many switchboxes on face")

    def fake_write_top_schematic(_circuit, _node, filepath, top_name, *_args, **_kwargs):
        path = Path(filepath) / f"{top_name}.kicad_sch"
        path.write_text("(kicad_sch)", encoding="utf-8")
        return str(path)

    monkeypatch.setattr(skidl, "get_default_tool", lambda: KICAD9)
    monkeypatch.setattr(gen_schematic_mod, "_setup_kicad_env", lambda: None)
    monkeypatch.setattr(gen_schematic_mod, "auto_stub_nets", lambda *a, **k: None)
    monkeypatch.setattr(gen_schematic_mod, "preprocess_circuit", lambda *a, **k: None)
    monkeypatch.setattr(
        gen_schematic_mod,
        "finalize_parts_and_nets",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        gen_schematic_mod,
        "_classify_and_stub_complex_nets",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(gen_schematic_mod, "_run_erc", lambda *_a, **_k: "")
    monkeypatch.setattr(
        gen_schematic_mod,
        "write_top_schematic",
        fake_write_top_schematic,
    )
    monkeypatch.setattr(
        sexp_schematic_mod,
        "write_top_schematic",
        fake_write_top_schematic,
    )
    monkeypatch.setattr(gen_schematic_mod.shutil, "which", lambda *_a, **_k: None)
    monkeypatch.setattr(sch_node_mod, "SchNode", FakeNode)

    gen_schematic_mod.gen_schematic(
        FakeCircuit(),
        filepath=str(tmp_path),
        top_name="assertion_fallback",
        auto_stub=True,
        retries=2,
    )

    assert calls["route"] == 3
    assert (tmp_path / "assertion_fallback.kicad_sch").exists()
    assert FakeCircuit.nets[0]._stub is True

    monkeypatch.setattr(
        FakeNode,
        "route",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("fallback failed")
        ),
    )
    with pytest.raises(RoutingFailure):
        gen_schematic_mod.gen_schematic(
            FakeCircuit(),
            filepath=str(tmp_path),
            top_name="assertion_fallback_failed",
            auto_stub=True,
            retries=1,
        )


def test_skidl_worker_rejects_malformed_custom_footprint_payload(
    tmp_path,
    monkeypatch,
):
    class FakeCircuit:
        parts = [
            SimpleNamespace(
                ref="R1",
                footprint="TestLib:R_Test",
                pins=[SimpleNamespace(num="1"), SimpleNamespace(num="2")],
            )
        ]

    monkeypatch.setattr(
        engine_worker_mod,
        "_exec_skidl_with_namespace",
        lambda code: (FakeCircuit(), {}),
    )

    result = _run_skidl_code({
        "_mode": "skidl_python",
        "code": "# fake circuit from monkeypatch",
        "board_name": "bad-footprint-bundle",
        "outline_mm": [25.0, 20.0],
        "out_dir": str(tmp_path),
        "run_id": "bad-footprint-bundle",
        "custom_footprints": {
            "TestLib:R_Test": 'leaked-token-456 (footprint "R_Test")',
        },
    })

    assert result["stage"] == "footprint_bundle"
    assert result["status"] == "failed"
    assert result["exceptions"][0]["code"] == ExcCode.CODE_EXEC_ERROR.value
    assert "custom_footprints entry TestLib:R_Test" in result["exceptions"][0]["message"]
    assert "leaked-token-456" not in result["exceptions"][0]["message"]


def test_skidl_worker_custom_footprints_reach_preflight_layout_and_writer(
    tmp_path,
    monkeypatch,
):
    import skidl.layout as layout_mod

    custom_footprint = """
(footprint "Tiny_2Pad"
  (layer "F.Cu")
  (pad "1" smd rect (at -0.6 0) (size 0.8 1.0)
    (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 0.6 0) (size 0.8 1.0)
    (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""
    seen = {}

    class FakeCircuit:
        parts = [
            SimpleNamespace(
                ref="R1",
                footprint="CustomLib:Tiny_2Pad",
                pins=[SimpleNamespace(num="1"), SimpleNamespace(num="2")],
            )
        ]

        def generate_schematic(self, filepath, top_name, **_kwargs):
            Path(filepath, f"{top_name}.kicad_sch").write_text(
                "(kicad_sch)", encoding="utf-8"
            )

    def assert_custom_library_available(fp_dirs):
        root = Path(fp_dirs[0])
        assert root.name == "_inline_footprints"
        assert (
            root
            / "CustomLib.pretty"
            / "Tiny_2Pad.kicad_mod"
        ).read_text(encoding="utf-8") == custom_footprint

    def fake_preflight(_circuit, fp_dirs):
        seen["preflight_dirs"] = list(fp_dirs)
        assert_custom_library_available(fp_dirs)
        return None

    def fake_plan_layout(_circuit, fp_lib_dirs, **_kwargs):
        seen["layout_dirs"] = list(fp_lib_dirs)
        assert_custom_library_available(fp_lib_dirs)
        return SimpleNamespace(
            ok=True,
            placed_parts=[
                SimpleNamespace(
                    ref="R1",
                    x_mm=5.0,
                    y_mm=5.0,
                    rot_deg=0.0,
                    footprint="CustomLib:Tiny_2Pad",
                    side="front",
                )
            ],
            outline=BoardOutline(20.0, 15.0),
            validation=SimpleNamespace(ok=True),
            score=SimpleNamespace(ok=True),
            power_plan=SimpleNamespace(),
            summary=lambda: "fake layout",
            to_dict=lambda: {
                "ok": True,
                "placed_parts": [
                    {
                        "ref": "R1",
                        "footprint": "CustomLib:Tiny_2Pad",
                    }
                ],
            },
        )

    def fake_write_kicad_pcb(_placed, _circuit, fp_dirs, output_path, **_kwargs):
        seen["writer_dirs"] = list(fp_dirs)
        assert_custom_library_available(fp_dirs)
        Path(output_path).write_text(
            '(kicad_pcb (footprint "CustomLib:Tiny_2Pad"))',
            encoding="utf-8",
        )

    monkeypatch.setattr(
        engine_worker_mod,
        "_exec_skidl_with_namespace",
        lambda code: (FakeCircuit(), {}),
    )
    monkeypatch.setattr(engine_worker_mod, "_circuit_to_spec_dict", lambda circuit: {})
    monkeypatch.setattr(
        engine_worker_mod,
        "enrich_blocks",
        lambda spec, marketing: (spec, []),
    )
    monkeypatch.setattr(engine_worker_mod, "enrich_spec", lambda spec: (spec, []))
    monkeypatch.setattr(engine_worker_mod, "design_review_exceptions", lambda *a, **k: [])
    monkeypatch.setattr(engine_worker_mod, "_preflight_footprints", fake_preflight)
    monkeypatch.setattr(engine_worker_mod, "layout_exceptions", lambda result: [])
    monkeypatch.setattr(
        engine_worker_mod,
        "_skidl_layout_intent_advisories",
        lambda **_kwargs: [],
    )
    monkeypatch.setattr(engine_worker_mod, "_generate_board_previews", lambda *a, **k: {})
    monkeypatch.setattr(engine_worker_mod, "_write_layout_mockup_svg", lambda *a, **k: None)
    monkeypatch.setattr(engine_worker_mod, "_add_layout_mockup_preview", lambda *a, **k: None)
    monkeypatch.setattr(layout_mod, "plan_layout", fake_plan_layout)
    monkeypatch.setattr(layout_mod, "write_kicad_pcb", fake_write_kicad_pcb)

    result = _run_skidl_code({
        "_mode": "skidl_python",
        "code": "# fake circuit from monkeypatch",
        "board_name": "custom-footprint-worker",
        "outline_mm": [20.0, 15.0],
        "out_dir": str(tmp_path),
        "run_id": "custom-footprint-worker",
        "pipeline_goal": "placement_review",
        "custom_footprints": {
            "CustomLib:Tiny_2Pad": custom_footprint,
        },
    })

    assert result["stage"] == "placement_review"
    assert result["layout"]["inline_footprints"] == {
        "count": 1,
        "footprints": ["CustomLib:Tiny_2Pad"],
    }
    assert seen["preflight_dirs"][0] == seen["layout_dirs"][0] == seen["writer_dirs"][0]


def test_floorplan_preflight_blocks_large_module_connector_board():
    circuit = SimpleNamespace(
        parts=[
            SimpleNamespace(
                ref="U1",
                name="ESP32-S3 WROOM logger module",
                value="ESP32-S3",
                footprint="RF_Module:ESP32-S3-WROOM-1",
                pins=[object()] * 44,
            ),
            SimpleNamespace(
                ref="J1",
                name="USB-C connector",
                footprint="Connector_USB:USB_C_Receptacle",
                pins=[object()] * 16,
            ),
            SimpleNamespace(
                ref="J2",
                name="microSD card socket",
                footprint="Connector_Card:microSD",
                pins=[object()] * 12,
            ),
            SimpleNamespace(
                ref="J3",
                name="debug pin header",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x04",
                pins=[object()] * 4,
            ),
        ]
    )

    exc = _floorplan_intent_preflight_exception(circuit, floorplan_meta={})

    assert exc is not None
    assert exc.code == ExcCode.DESIGN_MISSING_FEATURE
    assert exc.severity == Severity.ERROR
    assert exc.subject["feature"] == "placement_floorplan_intent"
    assert exc.subject["classification"] == "intent_insufficient_for_large_module_board"
    assert exc.subject["large_module_refs"] == ["U1"]
    assert {"J1", "J2", "J3"}.issubset(set(exc.subject["connector_refs"]))
    assert exc.candidates[0].action == ActionType.REGENERATE
    assert "EDA_FLOORPLAN['edge_anchors']" in exc.retry_hint


@needs_kicad
class TestSkidlPythonDesignReview:
    def test_python_mode_blocks_missing_connector(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
r1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
vcc += r1[1]
gnd += r1[2]
"""

        response = run_pipeline_code(
            code,
            board_name="missing-connector",
            outline_mm=[25.0, 20.0],
            out_dir=tmp_path,
            timeout_s=120,
        )

        assert response.stage == "design_review"
        assert not response.ok
        assert any(exc.code == ExcCode.DESIGN_NO_CONNECTOR for exc in response.exceptions)

    def test_python_mode_uses_design_intent_for_missing_feature(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
vcc += j1[1]
gnd += j1[2]
"""

        response = run_pipeline_code(
            code,
            board_name="missing-spi",
            outline_mm=[25.0, 20.0],
            out_dir=tmp_path,
            timeout_s=120,
            design_intent="SPI sensor breakout",
        )

        assert any(
            exc.code == ExcCode.DESIGN_MISSING_FEATURE
            and exc.subject.get("feature") == "SPI interface"
            for exc in response.exceptions
        )

    def test_python_mode_missing_pcb_footprint_is_preflight_error(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
r1 = Part("Device", "R", value="10K", footprint="NoSuchLib:NoSuchFootprint")
vcc += j1[1], r1[1]
gnd += j1[2], r1[2]
"""

        response = run_pipeline_code(
            code,
            board_name="missing-footprint",
            outline_mm=[25.0, 20.0],
            out_dir=tmp_path,
            timeout_s=120,
        )

        assert response.stage == "footprint_preflight"
        assert not response.ok
        assert any(exc.code == ExcCode.FOOTPRINT_MISSING for exc in response.exceptions)
        assert not any(exc.code == ExcCode.ENGINE_CRASH for exc in response.exceptions)

    def test_python_mode_custom_footprint_payload_reaches_preflight_and_layout(self, tmp_path):
        custom_footprint = """
(footprint "Tiny_2Pad"
  (version 20240108)
  (generator "eda-mcp-test")
  (layer "F.Cu")
  (attr smd)
  (fp_text reference "REF**" (at 0 -1.5 0) (layer "F.SilkS")
    (effects (font (size 1 1) (thickness 0.15))))
  (fp_text value "Tiny_2Pad" (at 0 1.5 0) (layer "F.Fab")
    (effects (font (size 1 1) (thickness 0.15))))
  (pad "1" smd rect (at -0.6 0) (size 0.8 1.0)
    (layers "F.Cu" "F.Paste" "F.Mask"))
  (pad "2" smd rect (at 0.6 0) (size 0.8 1.0)
    (layers "F.Cu" "F.Paste" "F.Mask"))
)
"""
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
r1 = Part("Device", "R", value="10K", footprint="CustomLib:Tiny_2Pad")
vcc += j1[1], r1[1]
gnd += j1[2], r1[2]
"""

        response = run_pipeline_code(
            code,
            board_name="custom-footprint",
            outline_mm=[25.0, 20.0],
            out_dir=tmp_path,
            timeout_s=120,
            pipeline_goal="placement_review",
            custom_footprints={"CustomLib:Tiny_2Pad": custom_footprint},
        )

        assert response.stage == "placement_review"
        assert not any(exc.code == ExcCode.FOOTPRINT_MISSING for exc in response.exceptions)
        assert response.layout["inline_footprints"] == {
            "count": 1,
            "footprints": ["CustomLib:Tiny_2Pad"],
        }
        assert any(
            placed["footprint"] == "CustomLib:Tiny_2Pad"
            for placed in response.layout["placed_parts"]
        )
        pcb_text = Path(response.outputs["pcb"]).read_text(encoding="utf-8")
        assert "CustomLib:Tiny_2Pad" in pcb_text
        assert "Tiny_2Pad" in pcb_text

    def test_python_mode_layout_intent_advisory_for_flat_complex_overlap(self):
        layout_result = SimpleNamespace(
            validation=SimpleNamespace(overlaps=[("U1", "R1")] * 10),
            score=SimpleNamespace(congestion_score=250.0),
        )
        circuit = SimpleNamespace(parts=[SimpleNamespace(ref=f"R{i}") for i in range(24)])

        advisories = _skidl_layout_intent_advisories(
            code="from skidl import *\n# flat generated board\n",
            layout_result=layout_result,
            floorplan_meta={},
            circuit=circuit,
        )

        assert len(advisories) == 1
        assert advisories[0].code == ExcCode.DESIGN_MISSING_FEATURE
        assert advisories[0].severity == Severity.ADVISORY
        assert advisories[0].subject["feature"] == "SKiDL layout intent"
        assert set(advisories[0].subject["missing"]) == {
            "subcircuits",
            "floorplan",
            "edge_preferences",
            "simulation_sources",
        }
        assert "@subcircuit" in advisories[0].retry_hint
        assert "sim_source()" in advisories[0].retry_hint

    def test_python_mode_layout_intent_advisory_respects_present_markers(self):
        layout_result = SimpleNamespace(
            validation=SimpleNamespace(overlaps=[("U1", "R1")] * 10),
            score=SimpleNamespace(congestion_score=250.0),
        )
        circuit = SimpleNamespace(parts=[SimpleNamespace(ref=f"R{i}") for i in range(24)])
        code = """
from skidl import *
from skidl.sim import sim_source
@subcircuit
def power_block():
    pass
j1.edge_preference = "left"
sim_source("VBUS", 5.0, provenance="USB input")
"""

        advisories = _skidl_layout_intent_advisories(
            code=code,
            layout_result=layout_result,
            floorplan_meta={"edge_anchors": 1},
            circuit=circuit,
        )

        assert advisories == []

    def test_python_mode_layout_intent_advisory_skips_valid_layout(self):
        layout_result = SimpleNamespace(
            validation=SimpleNamespace(
                overlaps=[],
                outline_violations=[],
                keepout_violations=[],
                missing_refs=[],
            ),
            score=SimpleNamespace(congestion_score=250.0),
        )
        circuit = SimpleNamespace(parts=[SimpleNamespace(ref=f"R{i}") for i in range(24)])

        advisories = _skidl_layout_intent_advisories(
            code="from skidl import *\n# flat generated board\n",
            layout_result=layout_result,
            floorplan_meta={},
            circuit=circuit,
        )

        assert advisories == []

    def test_python_mode_placement_review_skips_routing(self, tmp_path, monkeypatch):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
r1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
vcc += j1[1], r1[1]
gnd += j1[2], r1[2]
"""

        def fail_route(*args, **kwargs):
            raise AssertionError("placement_review must not call the router")

        monkeypatch.setattr(engine_worker_mod, "_route_pcb", fail_route)

        cwd = os.getcwd()
        try:
            result = _run_skidl_code({
                "run_id": "placement-review-test",
                "out_dir": str(tmp_path / "placement-review-test"),
                "code": code,
                "board_name": "placement-review",
                "outline_mm": [35.0, 20.0],
                "pipeline_goal": "placement_review",
            })
        finally:
            os.chdir(cwd)

        assert result["stage"] == "placement_review"
        assert result["metrics"]["pipeline_goal"] == "placement_review"
        assert result["artifacts"]["pcb"].endswith(".kicad_pcb")
        assert any(
            exc["code"] == "PLACEMENT_REVIEW_ONLY"
            for exc in result["exceptions"]
        )

    def test_python_mode_preserves_eda_floorplan_fixed_positions(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
r1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
vcc += j1[1], r1[1]
gnd += j1[2], r1[2]

EDA_FLOORPLAN = {
    "fixed_positions": [
        {"ref": "J1", "x_mm": 5.0, "y_mm": 10.0, "rotation_deg": 90.0},
        {"ref": "R1", "x_mm": 20.0, "y_mm": 10.0},
    ]
}
"""

        cwd = os.getcwd()
        try:
            result = _run_skidl_code({
                "run_id": "floorplan-test",
                "out_dir": str(tmp_path / "floorplan-test"),
                "code": code,
                "board_name": "floorplan",
                "outline_mm": [35.0, 20.0],
                "pipeline_goal": "placement_review",
            })
        finally:
            os.chdir(cwd)

        placed = {p["ref"]: p for p in result["layout"]["placed_parts"]}
        assert placed["J1"]["x_mm"] == pytest.approx(5.0)
        assert placed["J1"]["y_mm"] == pytest.approx(10.0)
        assert placed["J1"]["rot_deg"] == pytest.approx(90.0)
        assert placed["R1"]["x_mm"] == pytest.approx(20.0)
        assert result["layout"]["floorplan"]["fixed_positions"] == 2

    def test_python_mode_handles_mounting_holes_and_test_points(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
r1 = Part("Device", "R", value="1K", footprint="Resistor_SMD:R_0603_1608Metric")
tp_vcc = Part("Connector", "TestPoint",
              footprint="TestPoint:TestPoint_Pad_D1.5mm")
tp_gnd = Part("Connector", "TestPoint",
              footprint="TestPoint:TestPoint_Pad_D1.5mm")
mh1 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
mh2 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
vcc += j1[1], r1[1], tp_vcc[1]
gnd += j1[2], r1[2], tp_gnd[1]
"""

        response = run_pipeline_code(
            code,
            board_name="mechanical-parts",
            outline_mm=[40.0, 25.0],
            out_dir=tmp_path,
            timeout_s=120,
        )

        assert response.stage != "engine_worker"
        assert not any(exc.code == ExcCode.ENGINE_CRASH for exc in response.exceptions)

    def test_python_extraction_does_not_mark_signal_nets_as_power(self):
        code = """
from skidl import *
vcc = Net("3V3"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
sda = Net("SDA")
r1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
j1 = Part("Connector_Generic", "Conn_01x03",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
vcc += r1[1], j1[1]
sda += r1[2], j1[2]
gnd += j1[3]
"""

        spec = _circuit_to_spec_dict(_exec_skidl(code))
        power_by_name = {net["name"]: net["power"] for net in spec["nets"]}

        assert power_by_name["3V3"] is True
        assert power_by_name["GND"] is True
        assert power_by_name["SDA"] is False
