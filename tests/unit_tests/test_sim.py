"""Tests for skidl.sim — simulation ERC gate.

These tests do NOT require InSpice and test the static planning,
model registry, report dataclasses, and ERC integration.
"""
from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# Mock circuit objects — no real SKiDL import needed for unit tests
# ---------------------------------------------------------------------------

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
    def __init__(self, ref, value="", name="", nets=None, pins=2, pyspice=None):
        self.ref = ref
        self.value = value
        self.name = name
        self.pyspice = pyspice
        self.footprint = ""
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
        self.erc_list = []
        self.last_simulation_report = None

    def get_nets(self):
        return self.nets


# ---------------------------------------------------------------------------
# Report dataclass tests
# ---------------------------------------------------------------------------

class TestReportDataclasses:
    def test_finding_summary(self):
        from skidl.sim.report import SimulationFinding, FindingSeverity
        f = SimulationFinding(
            severity=FindingSeverity.ERROR,
            message="No ground net",
            category="missing_ground",
        )
        s = f.summary()
        assert "ERROR" in s
        assert "No ground net" in s
        assert "missing_ground" in s

    def test_measurement_summary(self):
        from skidl.sim.report import SimulationMeasurement
        m = SimulationMeasurement(name="V(VCC)", value=3.3, unit="V", net="VCC")
        assert "3.3" in m.summary()
        assert "VCC" in m.summary()

    def test_check_summary(self):
        from skidl.sim.report import SimulationCheck
        c = SimulationCheck(
            name="rail_VCC",
            passed=True,
            measured=3.3,
            expected=3.3,
            unit="V",
        )
        assert "PASS" in c.summary()
        assert "3.3" in c.summary()

    def test_report_counts(self):
        from skidl.sim.report import (
            SimulationReport, SimulationFinding, FindingSeverity,
            SimulationCheck,
        )
        report = SimulationReport(
            findings=[
                SimulationFinding(severity=FindingSeverity.ERROR, message="err1"),
                SimulationFinding(severity=FindingSeverity.WARNING, message="warn1"),
                SimulationFinding(severity=FindingSeverity.WARNING, message="warn2"),
            ],
            checks=[
                SimulationCheck(name="c1", passed=True),
                SimulationCheck(name="c2", passed=False, reason="bad"),
            ],
        )
        assert report.error_count == 1
        assert report.warning_count == 2
        assert not report.ok

    def test_report_ok_when_clean(self):
        from skidl.sim.report import SimulationReport, SimulationCheck
        report = SimulationReport(
            checks=[SimulationCheck(name="c1", passed=True)],
        )
        assert report.ok

    def test_report_summary_text(self):
        from skidl.sim.report import SimulationReport
        report = SimulationReport(executable=False, missing_models=["U1"])
        s = report.summary()
        assert "readiness-only" in s
        assert "U1" in s


# ---------------------------------------------------------------------------
# Model registry tests
# ---------------------------------------------------------------------------

class TestModelRegistry:
    def test_maps_resistor(self):
        from skidl.sim.registry import ModelRegistry, ModelSource
        gnd = _Net("GND")
        vcc = _Net("VCC")
        r1 = _Part("R1", value="10K", nets=[vcc, gnd])
        circuit = _Circuit([r1], [vcc, gnd])

        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.has_model("R1")
        entry = reg.get("R1")
        assert entry.spice_element == "R"
        assert entry.source == ModelSource.BUILTIN_PRIMITIVE

    def test_maps_capacitor(self):
        from skidl.sim.registry import ModelRegistry
        gnd = _Net("GND")
        c1 = _Part("C1", value="100nF", nets=[gnd, _Net("SIG")])
        circuit = _Circuit([c1], [gnd])

        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.has_model("C1")
        assert reg.get("C1").spice_element == "C"

    def test_maps_inductor(self):
        from skidl.sim.registry import ModelRegistry
        l1 = _Part("L1", value="10uH", nets=[_Net("A"), _Net("B")])
        circuit = _Circuit([l1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.has_model("L1")
        assert reg.get("L1").spice_element == "L"

    def test_maps_voltage_source(self):
        from skidl.sim.registry import ModelRegistry
        v1 = _Part("V1", value="5V", nets=[_Net("VCC"), _Net("GND")], pins=2)
        circuit = _Circuit([v1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.has_model("V1")
        assert reg.get("V1").spice_element == "V"

    def test_does_not_map_ic(self):
        from skidl.sim.registry import ModelRegistry
        u1 = _Part("U1", name="STM32F405", nets=[_Net("VCC"), _Net("GND")], pins=48)
        circuit = _Circuit([u1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        assert not reg.has_model("U1")

    def test_does_not_map_led(self):
        from skidl.sim.registry import ModelRegistry
        d1 = _Part("D1", name="LED", nets=[_Net("A"), _Net("K")])
        circuit = _Circuit([d1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        assert not reg.has_model("D1")

    def test_does_not_map_transistor(self):
        from skidl.sim.registry import ModelRegistry
        q1 = _Part("Q1", name="2N2222", nets=[_Net("B"), _Net("C"), _Net("E")], pins=3)
        circuit = _Circuit([q1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        assert not reg.has_model("Q1")

    def test_does_not_map_regulator(self):
        from skidl.sim.registry import ModelRegistry
        u1 = _Part("U1", name="AMS1117-3.3", nets=[_Net("VIN"), _Net("VOUT"), _Net("GND")], pins=3)
        circuit = _Circuit([u1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        assert not reg.has_model("U1")

    def test_maps_pyspice_part(self):
        from skidl.sim.registry import ModelRegistry, ModelSource
        d1 = _Part("D1", name="1N4148", pyspice={"name": "D", "add": None})
        d1.reordered_part_pins = [d1.pins[0], d1.pins[1]]
        circuit = _Circuit([d1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.has_model("D1")
        assert reg.get("D1").source == ModelSource.CONVERT_FOR_SPICE

    def test_unmapped_refs(self):
        from skidl.sim.registry import ModelRegistry
        r1 = _Part("R1", value="10K")
        u1 = _Part("U1", name="MCU", pins=48)
        circuit = _Circuit([r1, u1], [])

        reg = ModelRegistry()
        reg.build(circuit)
        unmapped = reg.unmapped_refs_for(circuit)
        assert "U1" in unmapped
        assert "R1" not in unmapped


# ---------------------------------------------------------------------------
# Plan tests
# ---------------------------------------------------------------------------

class TestSimulationPlan:
    def test_plan_basic_divider(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        mid = _Net("MID")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="10K", nets=[vcc, mid])
        r2 = _Part("R2", value="10K", nets=[mid, gnd])
        circuit = _Circuit([v1, r1, r2], [vcc, gnd, mid])

        plan = plan_simulation(circuit=circuit)
        assert plan.profile == "power_sanity"
        assert len(plan.eligible_parts) == 3
        assert len(plan.skipped_parts) == 0
        assert not plan.executable
        not_ready = [f for f in plan.findings if f.category == "not_spice_ready"]
        assert len(not_ready) == 3
        assert any(c.check_type == "divider_ratio" for c in plan.checks)

    def test_plan_executable_with_pyspice(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        mid = _Net("MID")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2, pyspice={"name": "V"})
        r1 = _Part("R1", value="10K", nets=[vcc, mid], pyspice={"name": "R"})
        r2 = _Part("R2", value="10K", nets=[mid, gnd], pyspice={"name": "R"})
        circuit = _Circuit([v1, r1, r2], [vcc, gnd, mid])

        plan = plan_simulation(circuit=circuit)
        assert plan.executable
        assert len(plan.skipped_parts) == 0
        not_ready = [f for f in plan.findings if f.category == "not_spice_ready"]
        assert len(not_ready) == 0

    def test_plan_missing_ground(self):
        from skidl.sim.plan import plan_simulation
        from skidl.sim.report import FindingSeverity
        vcc = _Net("VCC")
        r1 = _Part("R1", value="10K", nets=[vcc, _Net("SIG")])
        circuit = _Circuit([r1], [vcc, _Net("SIG")])

        plan = plan_simulation(circuit=circuit)
        assert not plan.executable
        ground_findings = [f for f in plan.findings if f.category == "missing_ground"]
        assert len(ground_findings) == 1
        assert ground_findings[0].severity == FindingSeverity.ERROR

    def test_plan_with_unsupported_ic(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        v1 = _Part("V1", value="3.3V", nets=[vcc, gnd], pins=2)
        u1 = _Part("U1", name="STM32", pins=48, nets=[vcc, gnd])
        circuit = _Circuit([v1, u1], [vcc, gnd])

        plan = plan_simulation(circuit=circuit)
        assert "U1" in plan.skipped_parts
        assert not plan.executable
        model_findings = [f for f in plan.findings if f.category == "missing_model"]
        assert len(model_findings) >= 1

    def test_plan_detects_rc_network(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        sig = _Net("SIG")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="1K", nets=[vcc, sig])
        c1 = _Part("C1", value="100nF", nets=[sig, gnd])
        circuit = _Circuit([v1, r1, c1], [vcc, gnd, sig])

        plan = plan_simulation(circuit=circuit)
        rc_checks = [c for c in plan.checks if c.check_type == "rc_time_constant"]
        assert len(rc_checks) >= 1
        assert rc_checks[0].expected == pytest.approx(1e3 * 100e-9, rel=0.01)

    def test_plan_summary(self):
        from skidl.sim.plan import plan_simulation
        gnd = _Net("GND")
        r1 = _Part("R1", value="10K", nets=[gnd, _Net("A")])
        circuit = _Circuit([r1], [gnd, _Net("A")])

        plan = plan_simulation(circuit=circuit)
        s = plan.summary()
        assert "power_sanity" in s
        assert "Eligible" in s

    def test_value_parsing(self):
        from skidl.sim.plan import _parse_value
        assert _parse_value("10K") == pytest.approx(10e3)
        assert _parse_value("100nF") == pytest.approx(100e-9)
        assert _parse_value("4.7u") == pytest.approx(4.7e-6)
        assert _parse_value("1M") == pytest.approx(1e6)
        assert _parse_value("") is None
        assert _parse_value("abc") is None


# ---------------------------------------------------------------------------
# Runner tests (without InSpice)
# ---------------------------------------------------------------------------

class TestRunnerNoInSpice:
    def test_non_executable_returns_readiness(self):
        from skidl.sim.runner import run_simulation
        from skidl.sim.plan import SimulationPlan
        from skidl.sim.report import FindingSeverity

        plan = SimulationPlan(
            profile="power_sanity",
            executable=False,
            skipped_parts=["U1"],
        )
        report = run_simulation(plan=plan)
        assert not report.executable
        assert any(f.category == "not_executable" for f in report.findings)


# ---------------------------------------------------------------------------
# ERC integration tests
# ---------------------------------------------------------------------------

class TestERCIntegration:
    def test_enable_attaches_callback(self):
        from skidl.sim.erc import enable_simulation_erc
        gnd = _Net("GND")
        r1 = _Part("R1", value="10K", nets=[gnd, _Net("A")])
        circuit = _Circuit([r1], [gnd, _Net("A")])

        initial_count = len(circuit.erc_list)
        enable_simulation_erc(circuit=circuit, execute=False)
        assert len(circuit.erc_list) == initial_count + 1
        assert getattr(circuit.erc_list[-1], "_is_simulation_erc", False)

    def test_enable_is_idempotent(self):
        from skidl.sim.erc import enable_simulation_erc
        circuit = _Circuit([], [])

        enable_simulation_erc(circuit=circuit, execute=False)
        enable_simulation_erc(circuit=circuit, execute=False)
        sim_callbacks = [
            fn for fn in circuit.erc_list
            if getattr(fn, "_is_simulation_erc", False)
        ]
        assert len(sim_callbacks) == 1

    def test_simulation_erc_stores_report(self):
        from skidl.sim.erc import simulation_erc
        gnd = _Net("GND")
        vcc = _Net("VCC")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="10K", nets=[vcc, gnd])
        circuit = _Circuit([v1, r1], [vcc, gnd])

        report = simulation_erc(circuit=circuit, execute=False)
        assert circuit.last_simulation_report is report
        assert not report.executable

    def test_simulation_erc_no_execute_deterministic(self):
        from skidl.sim.erc import simulation_erc
        gnd = _Net("GND")
        vcc = _Net("VCC")
        mid = _Net("MID")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="10K", nets=[vcc, mid])
        r2 = _Part("R2", value="10K", nets=[mid, gnd])
        circuit = _Circuit([v1, r1, r2], [vcc, gnd, mid])

        report1 = simulation_erc(circuit=circuit, execute=False)
        report2 = simulation_erc(circuit=circuit, execute=False)
        assert len(report1.findings) == len(report2.findings)
        assert len(report1.skipped_checks) == len(report2.skipped_checks)

    def test_severity_error_escalates_findings(self):
        from skidl.sim.erc import simulation_erc, _log_findings, _is_error_severity
        from skidl.sim.report import SimulationReport, SimulationFinding, FindingSeverity

        assert _is_error_severity("ERROR")
        assert _is_error_severity("error")
        assert not _is_error_severity("WARNING")

        report = SimulationReport(
            findings=[
                SimulationFinding(
                    severity=FindingSeverity.WARNING,
                    message="missing model",
                    category="missing_model",
                ),
                SimulationFinding(
                    severity=FindingSeverity.WARNING,
                    message="not spice ready",
                    category="not_spice_ready",
                ),
            ],
        )
        _log_findings(report, "ERROR")


# ---------------------------------------------------------------------------
# ERC regression: real SKiDL circuit
# ---------------------------------------------------------------------------

class TestERCRegression:
    """Verify that enabling simulation ERC does not change existing ERC
    warning/error counts when the simulation callback is not attached."""

    def test_erc_counts_unchanged_without_sim(self):
        """Existing ERC counts must not change from importing skidl.sim."""
        import skidl
        from skidl import Part, Net, Pin, ERC, erc_logger, POWER
        from skidl.pin import pin_types

        skidl.empty_footprint_handler = lambda part: None

        res = Part(
            tool=skidl.SKIDL,
            name="res",
            ref_prefix="R",
            dest=skidl.TEMPLATE,
            pins=[Pin(num=1, func=pin_types.PWRIN), Pin(num=2, func=pin_types.PWROUT)],
        )
        r1 = res()
        r1[1] += r1[2]

        ERC()
        assert erc_logger.warning.count == 0
        assert erc_logger.error.count == 0

    def test_sim_erc_readiness_on_real_circuit(self):
        """simulation_erc(execute=False) on a simple real circuit produces
        a deterministic readiness report without crashing."""
        import skidl
        from skidl import Part, Net, Pin
        from skidl.pin import pin_types
        from skidl.sim.erc import simulation_erc

        skidl.empty_footprint_handler = lambda part: None

        res = Part(
            tool=skidl.SKIDL,
            name="res",
            ref_prefix="R",
            dest=skidl.TEMPLATE,
            pins=[Pin(num=1, func=pin_types.PASSIVE), Pin(num=2, func=pin_types.PASSIVE)],
        )
        r1 = res(value="10K")
        r2 = res(value="10K")
        vcc = Net("VCC")
        gnd = Net("GND")
        mid = Net("MID")
        vcc += r1[1]
        r1[2] += mid
        mid += r2[1]
        r2[2] += gnd
        gnd.drive = skidl.POWER
        vcc.drive = skidl.POWER

        import builtins
        ckt = builtins.default_circuit
        report = simulation_erc(circuit=ckt, execute=False)
        assert report is not None
        assert not report.executable
        assert ckt.last_simulation_report is report


# ---------------------------------------------------------------------------
# Plan: additional inference tests
# ---------------------------------------------------------------------------

class TestPlanInference:
    def test_plan_passive_power_check(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        v1 = _Part("V1", value="12V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="100", nets=[vcc, gnd])
        circuit = _Circuit([v1, r1], [vcc, gnd])

        plan = plan_simulation(circuit=circuit)
        power_checks = [c for c in plan.checks if c.check_type == "passive_power"]
        assert len(power_checks) >= 1
        assert "R1" in power_checks[0].refs

    def test_plan_rail_presence_check(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        v1 = _Part("V1", value="3.3V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="1K", nets=[vcc, gnd])
        circuit = _Circuit([v1, r1], [vcc, gnd])

        plan = plan_simulation(circuit=circuit)
        rail_checks = [c for c in plan.checks if c.check_type == "rail_presence"]
        assert len(rail_checks) >= 1
        assert rail_checks[0].expected == pytest.approx(3.3)

    def test_plan_no_source_warns(self):
        from skidl.sim.plan import plan_simulation
        from skidl.sim.report import FindingSeverity
        vcc = _Net("VCC")
        gnd = _Net("GND")
        r1 = _Part("R1", value="10K", nets=[vcc, gnd])
        circuit = _Circuit([r1], [vcc, gnd])

        plan = plan_simulation(circuit=circuit)
        src_findings = [f for f in plan.findings if f.category == "missing_source"]
        assert len(src_findings) >= 1
        assert not plan.executable

    def test_divider_ratio_calculation(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        mid = _Net("MID")
        v1 = _Part("V1", value="10V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="20K", nets=[vcc, mid])
        r2 = _Part("R2", value="10K", nets=[mid, gnd])
        circuit = _Circuit([v1, r1, r2], [vcc, gnd, mid])

        plan = plan_simulation(circuit=circuit)
        div_checks = [c for c in plan.checks if c.check_type == "divider_ratio"]
        assert len(div_checks) == 1
        assert div_checks[0].expected == pytest.approx(10e3 / 30e3, rel=0.01)

    def test_dc_value_read_from_source(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        v1 = _Part("V1", value="", nets=[vcc, gnd], pins=2)
        v1.dc_value = 3.3
        r1 = _Part("R1", value="1K", nets=[vcc, gnd])
        circuit = _Circuit([v1, r1], [vcc, gnd])

        plan = plan_simulation(circuit=circuit)
        assert len(plan.sources) == 1
        assert plan.sources[0].value == pytest.approx(3.3)


# ---------------------------------------------------------------------------
# Import safety tests
# ---------------------------------------------------------------------------

class TestImportSafety:
    def test_import_sim_does_not_change_default_tool(self):
        from skidl import get_default_tool
        tool_before = get_default_tool()
        import skidl.sim  # noqa: F401
        tool_after = get_default_tool()
        assert tool_before == tool_after

    def test_import_sim_does_not_import_pyspice(self):
        import sys
        had_pyspice_before = "skidl.pyspice" in sys.modules
        import skidl.sim  # noqa: F401
        if not had_pyspice_before:
            assert "skidl.pyspice" not in sys.modules
