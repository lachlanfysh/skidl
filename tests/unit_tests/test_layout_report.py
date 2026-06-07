from __future__ import annotations

import pytest

from skidl.layout.constraints import BoardOutline, LayoutConstraints
from skidl.layout.engine import plan_layout
from skidl.layout.report import (
    NetExplanation,
    PartExplanation,
    PlacementReport,
    build_placement_report,
)


class _Net:
    def __init__(self, name):
        self.name = name
        self._pins = []

    def get_pins(self):
        return self._pins


class _Pin:
    def __init__(self, part, net):
        self.part = part
        self.net = net
        net._pins.append(self)


class _Part:
    def __init__(self, ref, value="", footprint="", name="", nets=None, pins=2):
        self.ref = ref
        self.value = value
        self.footprint = footprint
        self.name = name
        self.node = None
        self.pins = []
        for net in nets or []:
            self.pins.append(_Pin(self, net))
        while len(self.pins) < pins:
            self.pins.append(_Pin(self, _Net(f"{ref}_N{len(self.pins)}")))

    def __len__(self):
        return len(self.pins)


class _Circuit:
    def __init__(self, parts, nets):
        self.parts = parts
        self.nets = nets

    def get_nets(self):
        return self.nets


BBOXES = {
    "Package_QFP:MCU": (12.0, 12.0),
    "Capacitor:C_0805": (2.0, 1.25),
    "Connector:USB": (10.0, 5.0),
}


def _circuit():
    vcc = _Net("VCC")
    gnd = _Net("GND")
    sig = _Net("SIG")
    u1 = _Part("U1", name="MCU", footprint="Package_QFP:MCU", nets=[vcc, gnd], pins=2)
    c1 = _Part("C1", value="100nF", footprint="Capacitor:C_0805", nets=[vcc, gnd])
    j1 = _Part("J1", name="USB connector", footprint="Connector:USB", nets=[sig, gnd])
    return _Circuit([u1, c1, j1], [vcc, gnd, sig])


class TestReportAPI:
    def test_part_returns_explanation(self):
        report = PlacementReport(
            selected="test",
            part_reasons={"U1": ["placed by intent", "MCU role"]},
            warnings=["U1 near board edge"],
            hard_violations=["overlap: U1 <-> C1"],
        )
        explanation = report.part("U1")
        assert isinstance(explanation, PartExplanation)
        assert explanation.ref == "U1"
        assert len(explanation.reasons) == 2
        assert "placed by intent" in explanation.reasons
        assert len(explanation.warnings) == 1
        assert len(explanation.violations) == 1

    def test_part_unknown_ref(self):
        report = PlacementReport(selected="test")
        explanation = report.part("MISSING")
        assert explanation.ref == "MISSING"
        assert explanation.reasons == []
        assert explanation.warnings == []
        assert explanation.violations == []

    def test_net_returns_explanation(self):
        report = PlacementReport(
            selected="test",
            risky_nets=[("VCC", 45.2)],
        )
        explanation = report.net("VCC")
        assert isinstance(explanation, NetExplanation)
        assert explanation.name == "VCC"
        assert explanation.hpwl_mm == 45.2
        assert len(explanation.risks) >= 1

    def test_net_case_insensitive(self):
        report = PlacementReport(
            selected="test",
            net_explanations={"VCC": NetExplanation(name="VCC", hpwl_mm=10.0)},
        )
        explanation = report.net("vcc")
        assert explanation.hpwl_mm == 10.0

    def test_net_unknown_returns_empty(self):
        report = PlacementReport(selected="test")
        explanation = report.net("UNKNOWN")
        assert explanation.name == "UNKNOWN"
        assert explanation.hpwl_mm is None

    def test_top_risks_orders_by_severity(self):
        report = PlacementReport(
            selected="test",
            hard_violations=["overlap: U1 <-> C1"],
            warnings=["U1 near edge"],
            risky_nets=[("VCC", 50.0)],
        )
        risks = report.top_risks(limit=5)
        assert len(risks) >= 2
        assert risks[0].startswith("hard violation:")

    def test_top_risks_respects_limit(self):
        report = PlacementReport(
            selected="test",
            hard_violations=[f"overlap: U{i} <-> C{i}" for i in range(20)],
        )
        risks = report.top_risks(limit=3)
        assert len(risks) == 3


class TestReportIntegration:
    def test_plan_layout_populates_net_explanations(self):
        circuit = _circuit()
        result = plan_layout(
            circuit,
            fp_bboxes=BBOXES,
            constraints=LayoutConstraints(
                outline=BoardOutline(60.0, 40.0),
            ),
            anneal=False,
        )
        assert result.report is not None
        assert isinstance(result.report.net_explanations, dict)
        vcc_explanation = result.report.net("VCC")
        assert isinstance(vcc_explanation, NetExplanation)
