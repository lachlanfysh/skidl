"""Synthetic tests for layout feedback loop (Step 8).

Tests decap distance suggestions, missing decoupling, bulk-near-regulator
checks, high-power resistor warnings, and sim penalty computation.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


class MockPin:
    def __init__(self, net=None, name="", func=None):
        self.net = net
        self.name = name
        self.num = "1"
        self.func = func


class MockNet:
    def __init__(self, name):
        self.name = name


class MockPart:
    def __init__(self, ref, name="", value="", pins=None, footprint=""):
        self.ref = ref
        self.name = name
        self.value = value
        self.pins = pins or []
        self.footprint = footprint


class MockCircuit:
    def __init__(self, parts=None, nets=None, sim_harness=None):
        self.parts = parts or []
        self.sim_harness = sim_harness
        self._nets = nets or []

    def get_nets(self):
        return self._nets


def _cap(ref, value, pwr_net, gnd_net):
    return MockPart(
        ref=ref, name="C", value=value,
        pins=[MockPin(pwr_net), MockPin(gnd_net)],
    )


def _resistor(ref, value, net_a, net_b):
    return MockPart(
        ref=ref, name="R", value=value,
        pins=[MockPin(net_a), MockPin(net_b)],
    )


def _ic(ref, pin_nets):
    pins = [MockPin(n, n.name if n else "P") for n in pin_nets]
    if len(pins) <= 2:
        dummy = MockNet("SIG")
        pins.append(MockPin(dummy, "SIG"))
    return MockPart(ref=ref, name="IC", pins=pins)


def _regulator(ref, pin_nets):
    pins = [MockPin(n, n.name if n else "P") for n in pin_nets]
    if len(pins) <= 2:
        dummy = MockNet("EN")
        pins.append(MockPin(dummy, "EN"))
    return MockPart(ref=ref, name="LDO regulator", pins=pins)


def _harness(sources=None):
    from skidl.sim.declarations import SimHarness, DeclaredSource
    h = SimHarness()
    for s in (sources or []):
        h.sources.append(DeclaredSource(**s))
    return h


class TestDecapDistanceFeedback:
    def test_close_cap_no_suggestion(self):
        """Cap within 5mm of IC → no suggestion."""
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100nF", vcc, gnd),
            ],
            nets=[vcc, gnd],
        )
        placed = {"U1": (10.0, 10.0), "C1": (12.0, 10.0)}

        report = analyze_layout_feedback(ckt, placed)

        decap_suggestions = [
            s for s in report.suggestions
            if s.category in ("decap_too_far", "decap_far")
        ]
        assert len(decap_suggestions) == 0

    def test_far_cap_warns(self):
        """Cap 7mm from IC → warning."""
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100nF", vcc, gnd),
            ],
            nets=[vcc, gnd],
        )
        placed = {"U1": (10.0, 10.0), "C1": (17.0, 10.0)}

        report = analyze_layout_feedback(ckt, placed)

        warns = [s for s in report.suggestions if s.category == "decap_far"]
        assert len(warns) == 1
        assert warns[0].ref == "C1"
        assert report.sim_penalty > 0

    def test_very_far_cap_errors(self):
        """Cap >10mm from IC → error."""
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100nF", vcc, gnd),
            ],
            nets=[vcc, gnd],
        )
        placed = {"U1": (10.0, 10.0), "C1": (25.0, 10.0)}

        report = analyze_layout_feedback(ckt, placed)

        errors = [s for s in report.suggestions if s.category == "decap_too_far"]
        assert len(errors) == 1


class TestBulkNearRegulator:
    def test_bulk_near_regulator_ok(self):
        """Bulk cap within 15mm of regulator → no suggestion."""
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _regulator("U1", [vcc, gnd]),
                _cap("C1", "10u", vcc, gnd),
            ],
            nets=[vcc, gnd],
        )
        placed = {"U1": (10.0, 10.0), "C1": (15.0, 10.0)}

        report = analyze_layout_feedback(ckt, placed)

        bulk_warns = [
            s for s in report.suggestions
            if s.category == "bulk_far_from_regulator"
        ]
        assert len(bulk_warns) == 0

    def test_bulk_far_from_regulator_warns(self):
        """Bulk cap >15mm from regulator → warning."""
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _regulator("U1", [vcc, gnd]),
                _cap("C1", "10u", vcc, gnd),
            ],
            nets=[vcc, gnd],
        )
        placed = {"U1": (10.0, 10.0), "C1": (30.0, 10.0)}

        report = analyze_layout_feedback(ckt, placed)

        bulk_warns = [
            s for s in report.suggestions
            if s.category == "bulk_far_from_regulator"
        ]
        assert len(bulk_warns) == 1
        assert "U1" in bulk_warns[0].message


class TestNoPlacement:
    def test_no_placed_no_crash(self):
        """Without placement data, still runs decoupling/rail analysis."""
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_ic("U1", [vcc, gnd])],
            nets=[vcc, gnd],
        )

        report = analyze_layout_feedback(ckt)

        assert report.decoupling_analyzed
        assert report.rail_sanity_analyzed


class TestHighPowerResistor:
    def test_high_power_resistor_warns(self):
        """R dissipating >250mW → thermal warning."""
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        # 10Ω between 5V and GND → 500mA, 2.5W
        ckt = MockCircuit(
            parts=[_resistor("R1", "10", vcc, gnd)],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 5.0},
            ]),
        )

        report = analyze_layout_feedback(ckt)

        power_warns = [
            s for s in report.suggestions
            if s.category == "high_power_resistor"
        ]
        assert len(power_warns) == 1
        assert "R1" in power_warns[0].ref


class TestReportSummary:
    def test_summary_format(self):
        from skidl.sim.layout_feedback import analyze_layout_feedback

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_ic("U1", [vcc, gnd])],
            nets=[vcc, gnd],
        )

        report = analyze_layout_feedback(ckt)
        summary = report.summary()

        assert "Layout feedback" in summary
        assert "suggestions" in summary
