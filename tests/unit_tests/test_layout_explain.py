"""Tests for Phase 8: per-part and per-net placement explainability."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from skidl.layout.constraints import BoardOutline, LayoutConstraints
from skidl.layout.engine import plan_layout
from skidl.layout.explain import (
    NetExplanation,
    PartExplanation,
    RiskItem,
    explain_net,
    explain_part,
    top_risks,
)

from layout_case_helpers import (
    COMMON_BBOXES,
    _Circuit,
    _Net,
    _Part,
    make_connector,
    make_decap,
    make_ic,
    make_passive,
    make_power_nets,
)
from test_layout_benchmark_cases import (
    _power_board,
    _usb_mcu_board,
)


@pytest.fixture(scope="module")
def usb_mcu_result():
    ckt = _usb_mcu_board()
    return plan_layout(
        ckt,
        fp_bboxes=COMMON_BBOXES,
        constraints=LayoutConstraints(outline=BoardOutline(50.0, 35.0)),
    ), ckt


@pytest.fixture(scope="module")
def power_result():
    ckt = _power_board()
    return plan_layout(
        ckt,
        fp_bboxes=COMMON_BBOXES,
        constraints=LayoutConstraints(outline=BoardOutline(45.0, 30.0)),
    ), ckt


# ====================================================================
# explain_part tests
# ====================================================================

class TestExplainPart:
    def test_explains_usb_connector(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_part("J1", result, circuit)
        assert isinstance(explanation, PartExplanation)
        assert explanation.ref == "J1"
        assert explanation.role == "connector"
        assert len(explanation.placement_reasons) > 0
        assert any("edge" in r.lower() or "anchor" in r.lower() or "mating" in r.lower()
                    for r in explanation.placement_reasons)

    def test_explains_decoupling_cap(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_part("C1", result, circuit)
        assert explanation.role == "decoupling_cap"
        assert len(explanation.placement_reasons) > 0

    def test_explains_mcu(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_part("U1", result, circuit)
        assert explanation.role == "ic"
        assert explanation.footprint == "Package_QFP:LQFP-48_7x7mm_P0.5mm"

    def test_explains_missing_part(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_part("NONEXISTENT", result, circuit)
        assert "not found" in explanation.placement_reasons[0].lower()

    def test_has_nearby_parts(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_part("U1", result, circuit)
        assert len(explanation.nearby_parts) > 0
        for ref, dist in explanation.nearby_parts:
            assert isinstance(ref, str)
            assert dist > 0

    def test_has_edge_distances(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_part("U1", result, circuit)
        assert "left" in explanation.edge_distances
        assert "right" in explanation.edge_distances
        assert "top" in explanation.edge_distances
        assert "bottom" in explanation.edge_distances

    def test_summary_is_readable(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_part("J1", result, circuit)
        summary = explanation.summary()
        assert "J1" in summary
        assert "connector" in summary
        assert len(summary) > 50

    def test_jst_has_suggestions_or_reasons(self, power_result):
        result, circuit = power_result
        explanation = explain_part("J1", result, circuit)
        assert explanation.role == "connector"
        assert len(explanation.placement_reasons) > 0


# ====================================================================
# explain_net tests
# ====================================================================

class TestExplainNet:
    def test_explains_gnd_net(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_net("GND", result, circuit)
        assert isinstance(explanation, NetExplanation)
        assert explanation.name == "GND"
        assert explanation.is_ground
        assert not explanation.is_power
        assert explanation.pin_count > 0
        assert len(explanation.part_refs) > 0

    def test_explains_vcc_net(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_net("VCC", result, circuit)
        assert explanation.is_power
        assert explanation.hpwl_mm > 0

    def test_explains_signal_net(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_net("USB_DP", result, circuit)
        assert not explanation.is_power
        assert not explanation.is_ground
        assert len(explanation.part_refs) >= 2

    def test_explains_unknown_net(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_net("NONEXISTENT", result, circuit)
        assert explanation.pin_count == 0
        assert explanation.part_refs == []

    def test_summary_is_readable(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_net("GND", result, circuit)
        summary = explanation.summary()
        assert "GND" in summary
        assert "ground" in summary
        assert "HPWL" in summary

    def test_power_net_has_span(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        explanation = explain_net("GND", result, circuit)
        assert explanation.span_x_mm > 0 or explanation.span_y_mm > 0


# ====================================================================
# top_risks tests
# ====================================================================

class TestTopRisks:
    def test_returns_risk_items(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        risks = top_risks(result, circuit)
        assert isinstance(risks, list)
        for risk in risks:
            assert isinstance(risk, RiskItem)
            assert risk.severity in ("HIGH", "MEDIUM", "LOW")
            assert len(risk.description) > 0
            assert len(risk.suggestion) > 0

    def test_risks_sorted_by_severity(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        risks = top_risks(result, circuit)
        severity_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        for i in range(len(risks) - 1):
            assert severity_order[risks[i].severity] <= severity_order[risks[i + 1].severity]

    def test_risks_have_summaries(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        risks = top_risks(result, circuit)
        for risk in risks:
            summary = risk.summary()
            assert "[" in summary
            assert "→" in summary

    def test_respects_limit(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        risks = top_risks(result, circuit, limit=3)
        assert len(risks) <= 3

    def test_identifies_decap_distance_risk(self, usb_mcu_result):
        result, circuit = usb_mcu_result
        risks = top_risks(result, circuit)
        categories = [r.category for r in risks]
        has_decap = "decap_distance" in categories
        has_wirelength = "wirelength" in categories
        assert has_decap or has_wirelength, (
            f"Expected decap or wirelength risk, got: {categories}"
        )
