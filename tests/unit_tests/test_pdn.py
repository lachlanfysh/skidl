"""Synthetic tests for PDN impedance analysis (Step 7).

Tests cap impedance models, target impedance calculation,
parallel impedance combining, frequency sweep, and distance penalty.
"""
from __future__ import annotations

import math
import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


# ---------------------------------------------------------------------------
# Unit tests for CapModel
# ---------------------------------------------------------------------------
class TestCapModel:
    def test_impedance_at_resonance_equals_esr(self):
        from skidl.sim.pdn import CapModel

        cap = CapModel(ref="C1", capacitance=100e-9, esr=0.015, esl=0.5e-9)
        f_res = cap.resonant_freq()
        z_at_res = cap.impedance_at(f_res)
        assert abs(z_at_res - cap.esr) < 1e-6

    def test_low_freq_dominated_by_capacitance(self):
        from skidl.sim.pdn import CapModel

        cap = CapModel(ref="C1", capacitance=10e-6, esr=0.010, esl=1e-9)
        z_low = cap.impedance_at(100)  # 100Hz
        z_c = 1 / (2 * math.pi * 100 * 10e-6)
        assert z_low > z_c * 0.9

    def test_high_freq_dominated_by_inductance(self):
        from skidl.sim.pdn import CapModel

        cap = CapModel(ref="C1", capacitance=100e-9, esr=0.015, esl=0.5e-9)
        z_high = cap.impedance_at(500e6)  # 500MHz
        z_l = 2 * math.pi * 500e6 * 0.5e-9
        assert z_high > z_l * 0.5

    def test_distance_penalty_increases_esl(self):
        from skidl.sim.pdn import CapModel

        cap = CapModel(
            ref="C1", capacitance=100e-9, esr=0.015, esl=0.5e-9,
            distance_mm=10.0, distance_esl_penalty=10e-9,
        )
        assert cap.effective_esl == 0.5e-9 + 10e-9
        f_res_near = CapModel(
            ref="C2", capacitance=100e-9, esr=0.015, esl=0.5e-9,
        ).resonant_freq()
        f_res_far = cap.resonant_freq()
        assert f_res_far < f_res_near  # farther cap resonates lower


# ---------------------------------------------------------------------------
# Target impedance tiers
# ---------------------------------------------------------------------------
class TestTargetImpedance:
    def test_tier_3v3(self):
        from skidl.sim.pdn import _tier_z_target
        assert _tier_z_target(3.3) == 0.050

    def test_tier_1v8(self):
        from skidl.sim.pdn import _tier_z_target
        assert _tier_z_target(1.8) == 0.025

    def test_tier_5v(self):
        from skidl.sim.pdn import _tier_z_target
        assert _tier_z_target(5.0) == 0.100

    def test_tier_12v(self):
        from skidl.sim.pdn import _tier_z_target
        assert _tier_z_target(12.0) == 0.200

    def test_user_override(self):
        """With ripple_fraction and transient_current, user formula wins."""
        from skidl.sim.pdn import PDNConstraints
        c = PDNConstraints(ripple_fraction=0.05, transient_current=1.0)
        # Z = 3.3 * 0.05 / 1.0 = 0.165
        z = 3.3 * c.ripple_fraction / c.transient_current
        assert abs(z - 0.165) < 1e-6


# ---------------------------------------------------------------------------
# Parallel impedance
# ---------------------------------------------------------------------------
class TestParallelImpedance:
    def test_two_identical_caps_halves_impedance(self):
        from skidl.sim.pdn import CapModel, _parallel_impedance

        c1 = CapModel(ref="C1", capacitance=100e-9, esr=0.015, esl=0.5e-9)
        c2 = CapModel(ref="C2", capacitance=100e-9, esr=0.015, esl=0.5e-9)
        f = c1.resonant_freq()
        z_single = c1.impedance_at(f)
        z_parallel = _parallel_impedance([c1, c2], f)
        assert abs(z_parallel - z_single / 2) < 1e-6

    def test_empty_caps_gives_infinity(self):
        from skidl.sim.pdn import _parallel_impedance
        assert _parallel_impedance([], 1e6) == float("inf")


# ---------------------------------------------------------------------------
# Package detection
# ---------------------------------------------------------------------------
class TestPackageDetection:
    def test_detects_0603(self):
        from skidl.sim.pdn import _detect_package
        assert _detect_package("Capacitor_SMD:C_0603_1608Metric") == "0603"

    def test_detects_0402(self):
        from skidl.sim.pdn import _detect_package
        assert _detect_package("Capacitor_SMD:C_0402_1005Metric") == "0402"

    def test_unknown_returns_empty(self):
        from skidl.sim.pdn import _detect_package
        assert _detect_package("Custom:MyFootprint") == ""

    def test_parasitics_lookup_0603(self):
        from skidl.sim.pdn import _lookup_parasitics
        esr, esl = _lookup_parasitics("0603")
        assert esr == 0.015
        assert abs(esl - 0.5e-9) < 1e-12

    def test_parasitics_lookup_unknown(self):
        from skidl.sim.pdn import _lookup_parasitics, _DEFAULT_ESR, _DEFAULT_ESL
        esr, esl = _lookup_parasitics("")
        assert esr == _DEFAULT_ESR
        assert abs(esl - _DEFAULT_ESL * 1e-9) < 1e-12


# ---------------------------------------------------------------------------
# Integration: analyze_pdn with mock circuit
# ---------------------------------------------------------------------------
class MockPin:
    def __init__(self, net=None):
        self.net = net


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


def _cap(ref, value, pwr_net, gnd_net, footprint=""):
    return MockPart(
        ref=ref, name="C", value=value,
        pins=[MockPin(pwr_net), MockPin(gnd_net)],
        footprint=footprint,
    )


def _ic(ref, pin_nets):
    """Mock IC — needs 3+ pins for decoupling's IC detection."""
    class PwrPin:
        def __init__(self, net, name="VCC"):
            self.net = net
            self.name = name
            self.num = "1"
            self.func = None
    pins = []
    for i, n in enumerate(pin_nets):
        p = PwrPin(n, n.name if n else f"P{i}")
        p.num = str(i + 1)
        pins.append(p)
    # IC detection needs >2 pins; add a dummy signal pin
    if len(pins) <= 2:
        dummy_net = MockNet("SIG_DUMMY")
        pins.append(PwrPin(dummy_net, "SIG"))
    return MockPart(ref=ref, name="IC", value="MCU", pins=pins)


def _harness(sources=None, rail_assertions=None):
    from skidl.sim.declarations import SimHarness, DeclaredSource, RailAssertion
    h = SimHarness()
    for s in (sources or []):
        h.sources.append(DeclaredSource(**s))
    for ra in (rail_assertions or []):
        h.rail_assertions.append(RailAssertion(**ra))
    return h


class TestAnalyzePDN:
    def test_single_cap_on_rail(self):
        """One 100nF cap on VCC → PDN analysis produces rail result."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd,
                     footprint="Capacitor_SMD:C_0603_1608Metric"),
            ],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=10))

        vcc_rail = next(
            (r for r in report.rails if r.net_name == "VCC"), None
        )
        assert vcc_rail is not None
        assert vcc_rail.voltage == 3.3
        assert len(vcc_rail.caps) == 1
        assert vcc_rail.caps[0].ref == "C1"
        assert vcc_rail.z_target == 0.050  # tier default for 3.3V
        assert len(vcc_rail.frequencies) == 10
        assert len(vcc_rail.combined_impedance) == 10

    def test_no_caps_on_rail_warns(self):
        """Rail with source but no caps → warning finding."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=5))

        assert any(f.category == "no_decoupling" for f in report.findings)

    def test_user_z_target_overrides_tier(self):
        """User-specified ripple + transient → custom Z_target."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        constraints = PDNConstraints(
            freq_points=5,
            ripple_fraction=0.05,
            transient_current=0.5,
        )
        report = analyze_pdn(ckt, constraints)

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        expected_z = 3.3 * 0.05 / 0.5  # = 0.33Ω
        assert abs(vcc_rail.z_target - expected_z) < 1e-6
        assert vcc_rail.z_target_source == "user"

    def test_report_summary_format(self):
        """Summary includes rail name, voltage, Z_target, cap count."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=5))
        summary = report.summary()

        assert "VCC" in summary
        assert "3.3" in summary
        assert "PDN impedance report" in summary


class TestFrequencyRange:
    def test_custom_range(self):
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        constraints = PDNConstraints(
            freq_min=10e3, freq_max=1e9, freq_points=5,
        )
        report = analyze_pdn(ckt, constraints)

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        assert vcc_rail.frequencies[0] >= 9e3
        assert vcc_rail.frequencies[-1] <= 1.1e9


class TestCapDedup:
    def test_shared_cap_not_double_counted(self):
        """Cap associated with multiple ICs on the same rail is counted once."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _ic("U2", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=_harness(sources=[
                {"net_name": "VCC", "voltage": 3.3},
            ]),
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=5))

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        cap_refs = [c.ref for c in vcc_rail.caps]
        assert cap_refs.count("C1") == 1


class TestHarnessLoadDrivesPDN:
    def test_harness_load_sets_z_target(self):
        """sim_load() current declaration drives PDN target impedance."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints
        from skidl.sim.declarations import SimHarness, DeclaredSource, DeclaredLoad

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        h = SimHarness()
        h.sources.append(DeclaredSource(net_name="VCC", voltage=3.3))
        h.loads.append(DeclaredLoad(net_name="VCC", current=0.5))

        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=h,
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=5))

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        # Z = 3.3V * 0.05 / 0.5A = 0.33Ω
        assert abs(vcc_rail.z_target - 0.33) < 0.01
        assert vcc_rail.z_target_source == "harness_load"

    def test_multiple_loads_sum(self):
        """Two current loads on same rail sum for Z_target calculation."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints
        from skidl.sim.declarations import SimHarness, DeclaredSource, DeclaredLoad

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        h = SimHarness()
        h.sources.append(DeclaredSource(net_name="VCC", voltage=3.3))
        h.loads.append(DeclaredLoad(net_name="VCC", current=0.1))
        h.loads.append(DeclaredLoad(net_name="VCC", current=0.1))

        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=h,
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=5))

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        # Z = 3.3V * 0.05 / 0.2A = 0.825Ω
        assert abs(vcc_rail.z_target - 0.825) < 0.01
        assert vcc_rail.z_target_source == "harness_load"

    def test_resistance_load_derives_current(self):
        """Resistance load with known voltage derives I = V/R for Z_target."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints
        from skidl.sim.declarations import SimHarness, DeclaredSource, DeclaredLoad

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        h = SimHarness()
        h.sources.append(DeclaredSource(net_name="VCC", voltage=3.3))
        h.loads.append(DeclaredLoad(net_name="VCC", resistance=33.0))

        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=h,
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=5))

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        # I = 3.3V / 33Ω = 0.1A, Z = 3.3 * 0.05 / 0.1 = 1.65Ω
        assert abs(vcc_rail.z_target - 1.65) < 0.01
        assert vcc_rail.z_target_source == "harness_load"

    def test_explicit_constraints_override_harness_load(self):
        """User-provided constraints take priority over harness loads."""
        from skidl.sim.pdn import analyze_pdn, PDNConstraints
        from skidl.sim.declarations import SimHarness, DeclaredSource, DeclaredLoad

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        h = SimHarness()
        h.sources.append(DeclaredSource(net_name="VCC", voltage=3.3))
        h.loads.append(DeclaredLoad(net_name="VCC", current=0.5))

        ckt = MockCircuit(
            parts=[
                _ic("U1", [vcc, gnd]),
                _cap("C1", "100n", vcc, gnd),
            ],
            nets=[vcc, gnd],
            sim_harness=h,
        )

        constraints = PDNConstraints(
            freq_points=5,
            ripple_fraction=0.05,
            transient_current=1.0,
        )
        report = analyze_pdn(ckt, constraints)

        vcc_rail = next(r for r in report.rails if r.net_name == "VCC")
        # Z = 3.3V * 0.05 / 1.0A = 0.165Ω (user override)
        assert abs(vcc_rail.z_target - 0.165) < 0.01
        assert vcc_rail.z_target_source == "user"


class TestNoHarness:
    def test_no_harness_no_crash(self):
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        vcc = MockNet("VCC")
        gnd = MockNet("GND")
        ckt = MockCircuit(
            parts=[_cap("C1", "100n", vcc, gnd)],
            nets=[vcc, gnd],
        )

        report = analyze_pdn(ckt, PDNConstraints(freq_points=3))

        assert isinstance(report.rails, list)
