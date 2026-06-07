"""Synthetic tests for rail sanity execution (Step 6).

Tests static operating-point analysis: rail voltage identification,
resistor current/power computation, rail and ratio assertions,
and skipped-rail reasons.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


# ---------------------------------------------------------------------------
# Mock helpers — minimal circuit/part/net/pin for rail sanity
# ---------------------------------------------------------------------------
class MockPin:
    def __init__(self, net=None):
        self.net = net


class MockNet:
    def __init__(self, name):
        self.name = name


class MockPart:
    def __init__(self, ref, name="", value="", pins=None):
        self.ref = ref
        self.name = name
        self.value = value
        self.pins = pins or []


class MockCircuit:
    def __init__(self, parts=None, nets=None, sim_harness=None):
        self.parts = parts or []
        self.sim_harness = sim_harness
        self._nets = nets or []

    def get_nets(self):
        return self._nets


def _resistor(ref, value, net_a, net_b):
    return MockPart(
        ref=ref, name="R", value=value,
        pins=[MockPin(net_a), MockPin(net_b)],
    )


def _capacitor(ref, value, net_a, net_b):
    return MockPart(
        ref=ref, name="C", value=value,
        pins=[MockPin(net_a), MockPin(net_b)],
    )


def _ic(ref, pin_nets):
    pins = [MockPin(n) for n in pin_nets]
    return MockPart(ref=ref, name="IC", pins=pins)


def _harness(sources=None, loads=None, rail_assertions=None,
             ratio_assertions=None, probes=None):
    from skidl.sim.declarations import (
        SimHarness, DeclaredSource, DeclaredLoad,
        RailAssertion, RatioAssertion, DeclaredProbe,
    )
    h = SimHarness()
    for s in (sources or []):
        h.sources.append(DeclaredSource(**s))
    for lo in (loads or []):
        h.loads.append(DeclaredLoad(**lo))
    for ra in (rail_assertions or []):
        h.rail_assertions.append(RailAssertion(**ra))
    for ra in (ratio_assertions or []):
        h.ratio_assertions.append(RatioAssertion(**ra))
    for p in (probes or []):
        h.probes.append(DeclaredProbe(**p))
    return h


# ---------------------------------------------------------------------------
# Basic: resistor between known source and GND
# ---------------------------------------------------------------------------
class TestResistorCurrentPower:
    def test_resistor_vcc_to_gnd(self):
        """R1=1k between VCC(5V) and GND → 5mA, 25mW."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_resistor("R1", "1k", vcc, gnd)],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 5.0},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        assert report.resistors_checked == 1
        rc = report.resistor_checks[0]
        assert rc.ref == "R1"
        assert rc.resistance == 1000.0
        assert abs(rc.voltage_across - 5.0) < 1e-9
        assert abs(rc.current_a - 0.005) < 1e-9
        assert abs(rc.power_w - 0.025) < 1e-9

    def test_resistor_between_two_rails(self):
        """R1=10k between VCC(5V) and VOUT(3.3V) → 1.7V, 0.17mA, 0.029mW."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        vout = MockNet("VOUT")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_resistor("R1", "10k", vcc, vout)],
            nets=[vcc, vout, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 5.0},
                {"net_name": "VOUT", "voltage": 3.3},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        assert report.resistors_checked == 1
        rc = report.resistor_checks[0]
        assert abs(rc.voltage_across - 1.7) < 1e-9
        assert abs(rc.current_a - 0.00017) < 1e-9
        assert abs(rc.power_w - 1.7 * 0.00017) < 1e-9

    def test_resistor_unknown_voltage_skipped(self):
        """R between VCC and unknown signal net → skipped with reason."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        sig = MockNet("SIG_OUT")
        ckt = MockCircuit(
            parts=[_resistor("R1", "4.7k", vcc, sig)],
            nets=[vcc, sig],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        assert report.resistors_skipped == 1
        rc = report.resistor_checks[0]
        assert "SIG_OUT" in rc.skipped_reason
        assert rc.power_w is None

    def test_zero_voltage_across_gives_zero_power(self):
        """Both pins on same rail → 0V, 0A, 0W."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        ckt = MockCircuit(
            parts=[_resistor("R1", "100", vcc, vcc)],
            nets=[vcc],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        rc = report.resistor_checks[0]
        assert rc.voltage_across == 0.0
        assert rc.current_a == 0.0
        assert rc.power_w == 0.0

    def test_capacitor_not_checked(self):
        """Capacitors are not included in resistor checks."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_capacitor("C1", "100n", vcc, gnd)],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        assert len(report.resistor_checks) == 0


# ---------------------------------------------------------------------------
# Rail assertion pass/fail
# ---------------------------------------------------------------------------
class TestRailAssertions:
    def test_assertion_passes(self):
        """Source voltage matches assertion nominal."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, gnd],
            sim_harness=_harness(
                sources=[{"net_name": "VCC", "voltage": 3.3}],
                rail_assertions=[
                    {"net_name": "VCC", "nominal": 3.3, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.assertions_passed == 1
        assert report.assertions_failed == 0
        assert report.rail_assertions[0].passed is True
        assert report.rail_assertions[0].actual == 3.3

    def test_assertion_fails_outside_tolerance(self):
        """Source 5V but assertion expects 3.3V → FAIL."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, gnd],
            sim_harness=_harness(
                sources=[{"net_name": "VCC", "voltage": 5.0}],
                rail_assertions=[
                    {"net_name": "VCC", "nominal": 3.3, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.assertions_failed == 1
        assert report.rail_assertions[0].passed is False
        assert any(f.category == "rail_assertion_failed" for f in report.findings)

    def test_assertion_within_tolerance(self):
        """Source 3.4V, assertion expects 3.3V ±5% (±0.165V) → PASS."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, gnd],
            sim_harness=_harness(
                sources=[{"net_name": "VCC", "voltage": 3.4}],
                rail_assertions=[
                    {"net_name": "VCC", "nominal": 3.3, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.rail_assertions[0].passed is True

    def test_assertion_skipped_no_source(self):
        """Assertion on rail without declared source → skipped."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, gnd],
            sim_harness=_harness(
                rail_assertions=[
                    {"net_name": "VCC", "nominal": 3.3, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.assertions_skipped == 1
        assert report.rail_assertions[0].passed is None
        assert "sim_source()" in report.rail_assertions[0].skipped_reason
        assert any(f.category == "assertion_skipped" for f in report.findings)


# ---------------------------------------------------------------------------
# Ratio assertions
# ---------------------------------------------------------------------------
class TestRatioAssertions:
    def test_ratio_passes(self):
        """VCC=5V, VOUT=3.3V → ratio=0.66, expected 0.66 → PASS."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        vout = MockNet("VOUT")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, vout, gnd],
            sim_harness=_harness(
                sources=[
                    {"net_name": "VCC", "voltage": 5.0},
                    {"net_name": "VOUT", "voltage": 3.3},
                ],
                ratio_assertions=[
                    {"output_net": "VOUT", "input_net": "VCC",
                     "ratio": 0.66, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.assertions_passed == 1
        assert report.ratio_assertions[0].passed is True
        assert abs(report.ratio_assertions[0].actual_ratio - 0.66) < 0.01

    def test_ratio_fails(self):
        """VCC=5V, VOUT=1.0V → ratio=0.2, expected 0.66 → FAIL."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        vout = MockNet("VOUT")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, vout, gnd],
            sim_harness=_harness(
                sources=[
                    {"net_name": "VCC", "voltage": 5.0},
                    {"net_name": "VOUT", "voltage": 1.0},
                ],
                ratio_assertions=[
                    {"output_net": "VOUT", "input_net": "VCC",
                     "ratio": 0.66, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.assertions_failed == 1
        assert any(f.category == "ratio_assertion_failed" for f in report.findings)

    def test_ratio_skipped_missing_input(self):
        """Ratio assertion with unknown input voltage → skipped."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vout = MockNet("VOUT")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vout, gnd],
            sim_harness=_harness(
                sources=[{"net_name": "VOUT", "voltage": 3.3}],
                ratio_assertions=[
                    {"output_net": "VOUT", "input_net": "VIN",
                     "ratio": 0.66, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.assertions_skipped == 1
        assert "VIN" in report.ratio_assertions[0].skipped_reason

    def test_ratio_zero_input_skipped(self):
        """Input voltage = 0 → cannot compute ratio, skipped."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vout = MockNet("VOUT")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vout, gnd],
            sim_harness=_harness(
                sources=[
                    {"net_name": "VOUT", "voltage": 3.3},
                    {"net_name": "GND", "voltage": 0.0},
                ],
                ratio_assertions=[
                    {"output_net": "VOUT", "input_net": "GND",
                     "ratio": 1.0, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.ratio_assertions[0].passed is None
        assert "0" in report.ratio_assertions[0].skipped_reason


# ---------------------------------------------------------------------------
# Rail status / skipped rail reasons
# ---------------------------------------------------------------------------
class TestRailStatus:
    def test_sourced_rail_has_voltage(self):
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        assert vcc_rail.voltage == 3.3
        assert vcc_rail.checked is True

    def test_gnd_always_zero(self):
        from skidl.sim.rail_sanity import analyze_rail_sanity

        gnd = MockNet("GND")
        ckt = MockCircuit(parts=[], nets=[gnd])

        report = analyze_rail_sanity(ckt)

        gnd_rail = next(r for r in report.rails if r.net_name == "GND")
        assert gnd_rail.voltage == 0.0

    def test_unsourced_power_rail_gives_reason(self):
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(parts=[], nets=[vcc, gnd])

        report = analyze_rail_sanity(ckt)

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        assert vcc_rail.voltage is None
        assert "sim_source()" in vcc_rail.skipped_reason


# ---------------------------------------------------------------------------
# Voltage divider (classic use case)
# ---------------------------------------------------------------------------
class TestDividerPattern:
    def test_divider_current_and_power(self):
        """5V → R1(10k) → mid → R2(10k) → GND. Each: 0.25mA, 0.625mW."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        mid = MockNet("VMID")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _resistor("R1", "10k", vcc, mid),
                _resistor("R2", "10k", mid, gnd),
            ],
            nets=[vcc, mid, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 5.0},
                {"net_name": "VMID", "voltage": 2.5},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        assert report.resistors_checked == 2
        r1 = next(r for r in report.resistor_checks if r.ref == "R1")
        r2 = next(r for r in report.resistor_checks if r.ref == "R2")
        assert abs(r1.voltage_across - 2.5) < 1e-9
        assert abs(r2.voltage_across - 2.5) < 1e-9
        assert abs(r1.current_a - 0.00025) < 1e-9
        assert abs(r1.power_w - 0.000625) < 1e-9


# ---------------------------------------------------------------------------
# No harness at all — graceful handling
# ---------------------------------------------------------------------------
class TestNoHarness:
    def test_no_harness_no_crash(self):
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_resistor("R1", "1k", vcc, gnd)],
            nets=[vcc, gnd],
        )

        report = analyze_rail_sanity(ckt)

        assert report.resistors_skipped == 1
        assert len(report.rail_assertions) == 0
        assert len(report.ratio_assertions) == 0

    def test_gnd_only_gives_zero_power(self):
        """R between GND and GND → 0V across, 0W."""
        from skidl.sim.rail_sanity import analyze_rail_sanity

        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_resistor("R1", "1k", gnd, gnd)],
            nets=[gnd],
        )

        report = analyze_rail_sanity(ckt)

        assert report.resistors_checked == 1
        rc = report.resistor_checks[0]
        assert rc.power_w == 0.0


# ---------------------------------------------------------------------------
# Report summary
# ---------------------------------------------------------------------------
class TestReportSummary:
    def test_summary_format(self):
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_resistor("R1", "1k", vcc, gnd)],
            nets=[vcc, gnd],
            sim_harness=_harness(
                sources=[{"net_name": "VCC", "voltage": 5.0}],
                rail_assertions=[
                    {"net_name": "VCC", "nominal": 5.0, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)
        summary = report.summary()

        assert "Rail sanity report" in summary
        assert "VCC" in summary
        assert "5" in summary
        assert "R1" in summary
        assert "PASS" in summary


# ---------------------------------------------------------------------------
# Multiple resistors, mixed known/unknown
# ---------------------------------------------------------------------------
class TestMixedResistors:
    def test_some_checked_some_skipped(self):
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        sig = MockNet("SIG")
        ckt = MockCircuit(
            parts=[
                _resistor("R1", "1k", vcc, gnd),
                _resistor("R2", "4.7k", vcc, sig),
            ],
            nets=[vcc, gnd, sig],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_rail_sanity(ckt)

        assert report.resistors_checked == 1
        assert report.resistors_skipped == 1


# ---------------------------------------------------------------------------
# Multiple rail assertions on different rails
# ---------------------------------------------------------------------------
class TestMultipleRails:
    def test_two_rails_two_assertions(self):
        from skidl.sim.rail_sanity import analyze_rail_sanity

        vcc = MockNet("VCC")
        vdd = MockNet("VDD")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, vdd, gnd],
            sim_harness=_harness(
                sources=[
                    {"net_name": "VCC", "voltage": 5.0},
                    {"net_name": "VDD", "voltage": 3.3},
                ],
                rail_assertions=[
                    {"net_name": "VCC", "nominal": 5.0, "tolerance": 0.05},
                    {"net_name": "VDD", "nominal": 3.3, "tolerance": 0.05},
                ],
            ),
        )

        report = analyze_rail_sanity(ckt)

        assert report.assertions_passed == 2
        assert report.assertions_failed == 0
