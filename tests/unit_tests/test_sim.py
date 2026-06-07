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
        entry = reg.get("D1")
        assert entry.source == ModelSource.CONVERT_FOR_SPICE
        assert entry.spice_element == "D"
        assert entry.spice_ready

    def test_convert_for_spice_preserves_element_type(self):
        from skidl.sim.registry import ModelRegistry, ModelSource
        vcc = _Net("VCC")
        gnd = _Net("GND")
        mid = _Net("MID")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2,
                    pyspice={"name": "V", "add": None})
        v1.reordered_part_pins = [v1.pins[0], v1.pins[1]]
        r1 = _Part("R1", value="10K", nets=[vcc, mid],
                    pyspice={"name": "R", "add": None})
        r1.reordered_part_pins = [r1.pins[0], r1.pins[1]]
        r2 = _Part("R2", value="10K", nets=[mid, gnd],
                    pyspice={"name": "R", "add": None})
        r2.reordered_part_pins = [r2.pins[0], r2.pins[1]]
        circuit = _Circuit([v1, r1, r2], [vcc, gnd, mid])

        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.get("V1").spice_element == "V"
        assert reg.get("R1").spice_element == "R"
        assert reg.get("R2").spice_element == "R"
        assert all(reg.get(r).spice_ready for r in ["V1", "R1", "R2"])

    def test_convert_for_spice_divider_detected(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        mid = _Net("MID")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2,
                    pyspice={"name": "V", "add": None})
        v1.reordered_part_pins = [v1.pins[0], v1.pins[1]]
        r1 = _Part("R1", value="20K", nets=[vcc, mid],
                    pyspice={"name": "R", "add": None})
        r1.reordered_part_pins = [r1.pins[0], r1.pins[1]]
        r2 = _Part("R2", value="10K", nets=[mid, gnd],
                    pyspice={"name": "R", "add": None})
        r2.reordered_part_pins = [r2.pins[0], r2.pins[1]]
        circuit = _Circuit([v1, r1, r2], [vcc, gnd, mid])

        plan = plan_simulation(circuit=circuit)
        assert plan.executable
        assert len(plan.sources) == 1
        assert plan.sources[0].ref == "V1"
        div_checks = [c for c in plan.checks if c.check_type == "divider_ratio"]
        assert len(div_checks) == 1
        assert div_checks[0].expected == pytest.approx(10e3 / 30e3, rel=0.01)

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
        # Executable because divider_ratio is a static check and all refs have models
        assert plan.executable
        not_ready = [f for f in plan.findings if f.category == "not_spice_ready"]
        # R1 and R2 are auto-ready (2-pin, parseable value); only V1 lacks auto-ready
        assert len(not_ready) == 1
        assert not_ready[0].refs == ["V1"]
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
        from skidl.sim.erc import _log_findings, _is_error_severity
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

    def test_severity_integer_error_constant(self):
        from skidl.sim.erc import _is_error_severity
        from skidl.skidlbaseobj import ERROR, WARNING, OK

        assert _is_error_severity(ERROR)
        assert not _is_error_severity(WARNING)
        assert not _is_error_severity(OK)


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


# ---------------------------------------------------------------------------
# Auto-primitive tests
# ---------------------------------------------------------------------------

class TestAutoPrimitive:
    def test_2pin_resistor_auto_ready(self):
        from skidl.sim.registry import ModelRegistry, ModelSource
        gnd = _Net("GND")
        r1 = _Part("R1", value="10K", nets=[gnd, _Net("A")])
        circuit = _Circuit([r1], [gnd])
        reg = ModelRegistry()
        reg.build(circuit)
        entry = reg.get("R1")
        assert entry.spice_ready
        assert entry.source == ModelSource.BUILTIN_PRIMITIVE
        assert entry.value == pytest.approx(10e3)

    def test_2pin_capacitor_auto_ready(self):
        from skidl.sim.registry import ModelRegistry
        c1 = _Part("C1", value="100nF", nets=[_Net("A"), _Net("B")])
        circuit = _Circuit([c1], [])
        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.get("C1").spice_ready
        assert reg.get("C1").value == pytest.approx(100e-9)

    def test_2pin_inductor_auto_ready(self):
        from skidl.sim.registry import ModelRegistry
        l1 = _Part("L1", value="10uH", nets=[_Net("A"), _Net("B")])
        circuit = _Circuit([l1], [])
        reg = ModelRegistry()
        reg.build(circuit)
        assert reg.get("L1").spice_ready
        assert reg.get("L1").value == pytest.approx(10e-6)

    def test_3pin_potentiometer_not_auto_ready(self):
        from skidl.sim.registry import ModelRegistry
        rv1 = _Part("R1", value="10K", nets=[_Net("A"), _Net("B"), _Net("W")], pins=3)
        circuit = _Circuit([rv1], [])
        reg = ModelRegistry()
        reg.build(circuit)
        entry = reg.get("R1")
        assert not entry.spice_ready
        assert entry.value is None

    def test_unparseable_value_not_auto_ready(self):
        from skidl.sim.registry import ModelRegistry
        r1 = _Part("R1", value="DNP", nets=[_Net("A"), _Net("B")])
        circuit = _Circuit([r1], [])
        reg = ModelRegistry()
        reg.build(circuit)
        entry = reg.get("R1")
        assert not entry.spice_ready

    def test_voltage_source_not_auto_ready(self):
        from skidl.sim.registry import ModelRegistry
        v1 = _Part("V1", value="5V", nets=[_Net("VCC"), _Net("GND")], pins=2)
        circuit = _Circuit([v1], [])
        reg = ModelRegistry()
        reg.build(circuit)
        entry = reg.get("V1")
        assert not entry.spice_ready
        assert entry.spice_element == "V"

    def test_plan_auto_ready_primitives_reduce_not_ready(self):
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        mid = _Net("MID")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="20K", nets=[vcc, mid])
        r2 = _Part("R2", value="10K", nets=[mid, gnd])
        c1 = _Part("C1", value="100nF", nets=[vcc, gnd])
        circuit = _Circuit([v1, r1, r2, c1], [vcc, gnd, mid])

        plan = plan_simulation(circuit=circuit)
        not_ready = [f for f in plan.findings if f.category == "not_spice_ready"]
        # Only V1 is not ready; R1, R2, C1 are auto-ready
        assert len(not_ready) == 1
        assert not_ready[0].refs == ["V1"]
        ready = [e for e in plan.eligible_parts if e.spice_ready]
        assert len(ready) == 3


# ---------------------------------------------------------------------------
# Harness tests
# ---------------------------------------------------------------------------

class TestHarness:
    def test_safe_node_name(self):
        from skidl.sim.harness import _safe_node_name
        assert _safe_node_name("VCC") == "vcc"
        assert _safe_node_name("I2C_SDA") == "i2c_sda"
        assert _safe_node_name("3.3V") == "n_3_3v"
        assert _safe_node_name("+5V") == "_5v"

    def test_ref_suffix(self):
        from skidl.sim.harness import _ref_suffix
        assert _ref_suffix("R1", "R") == "1"
        assert _ref_suffix("R10", "R") == "10"
        assert _ref_suffix("C1", "C") == "1"
        assert _ref_suffix("X1", "R") == "X1"

    def test_map_nets_gnd_detection(self):
        from skidl.sim.harness import _map_nets
        gnd = _Net("GND")
        vcc = _Net("VCC")
        parts = [_Part("R1", nets=[vcc, gnd])]
        circuit = _Circuit(parts, [gnd, vcc])

        class FakeGnd:
            pass

        fake_gnd = FakeGnd()
        nodes = _map_nets(circuit, fake_gnd)
        assert nodes[id(gnd)] is fake_gnd
        assert nodes[id(vcc)] == "vcc"

    def test_runner_selects_harness_for_auto_primitives(self):
        from skidl.sim.runner import _has_auto_primitives
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        r1 = _Part("R1", value="10K", nets=[vcc, gnd])
        circuit = _Circuit([r1], [vcc, gnd])
        plan = plan_simulation(circuit=circuit)
        assert _has_auto_primitives(plan)

    def test_runner_skips_harness_for_pyspice_only(self):
        from skidl.sim.runner import _has_auto_primitives
        from skidl.sim.plan import plan_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        r1 = _Part("R1", value="10K", nets=[vcc, gnd], pyspice={"name": "R"})
        circuit = _Circuit([r1], [vcc, gnd])
        plan = plan_simulation(circuit=circuit)
        assert not _has_auto_primitives(plan)


# ---------------------------------------------------------------------------
# Static execution tests (no InSpice needed)
# ---------------------------------------------------------------------------

class TestStaticExecution:
    def test_rc_tau_runs_without_inspice(self):
        from skidl.sim.runner import run_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        sig = _Net("SIG")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="1K", nets=[vcc, sig])
        c1 = _Part("C1", value="100nF", nets=[sig, gnd])
        circuit = _Circuit([v1, r1, c1], [vcc, gnd, sig])

        report = run_simulation(circuit=circuit)
        assert report.executable
        rc_checks = [c for c in report.checks if "rc_" in c.name]
        assert len(rc_checks) >= 1
        assert rc_checks[0].passed
        assert rc_checks[0].expected == pytest.approx(1e3 * 100e-9, rel=0.01)

    def test_divider_runs_statically(self):
        from skidl.sim.runner import run_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        mid = _Net("MID")
        v1 = _Part("V1", value="5V", nets=[vcc, gnd], pins=2)
        r1 = _Part("R1", value="20K", nets=[vcc, mid])
        r2 = _Part("R2", value="10K", nets=[mid, gnd])
        circuit = _Circuit([v1, r1, r2], [vcc, gnd, mid])

        report = run_simulation(circuit=circuit)
        assert report.executable
        div_checks = [c for c in report.checks if "divider" in c.name]
        assert len(div_checks) == 1
        assert div_checks[0].passed
        assert div_checks[0].expected == pytest.approx(10e3 / 30e3, rel=0.01)
        assert "Static" in (div_checks[0].reason or "")

    def test_simulation_checks_skipped_without_source(self):
        from skidl.sim.runner import run_simulation
        vcc = _Net("VCC")
        gnd = _Net("GND")
        sig = _Net("SIG")
        r1 = _Part("R1", value="1K", nets=[vcc, sig])
        c1 = _Part("C1", value="100nF", nets=[sig, gnd])
        circuit = _Circuit([r1, c1], [vcc, gnd, sig])

        report = run_simulation(circuit=circuit)
        assert report.executable
        # RC tau is static — should run
        rc_checks = [c for c in report.checks if "rc_" in c.name]
        assert len(rc_checks) >= 1
        # No simulation measurements (no source, no simulation)
        assert len(report.measurements) == 0

    def test_45lux_gets_executable_checks(self):
        """45lux circuit should produce executable RC time constant checks."""
        from skidl.sim.runner import run_simulation
        from skidl.sim.plan import plan_simulation
        import os
        os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")
        from skidl import reset, Part, Net, POWER, KICAD9, set_default_tool, subcircuit
        reset()
        set_default_tool(KICAD9)

        FP_R = "Resistor_SMD:R_0603_1608Metric"
        FP_C = "Capacitor_SMD:C_0603_1608Metric"
        FP_C_BULK = "Capacitor_SMD:C_0805_2012Metric"
        FP_ESP32 = "RF_Module:ESP32-C6-MINI-1"
        FP_OPT = "Package_DFN_QFN:DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm"
        FP_MUX = "Package_SO:TSSOP-16_4.4x5mm_P0.65mm"
        FP_IMU = "Package_LGA:Bosch_LGA-14_3x2.5mm_P0.5mm"
        FP_LDO = "Package_TO_SOT_SMD:SOT-23-5"
        FP_SW = "Button_Switch_THT:SW_TH_Tactile_Omron_B3F-106x"
        FP_BAT = "Battery:BatteryHolder_Keystone_2479_3xAAA"
        FP_OLED = "Connector_FFC-FPC:Molex_200528-0040_1x04-1MP_P1.00mm_Horizontal"
        FP_TAG = "Connector_Generic:Conn_02x03_Odd_Even"
        FP_SPECTRAL = "Package_LGA:AMS_OLGA-8_2x3.1mm_P0.8mm"

        @subcircuit
        def power_supply(vbat, vcc, gnd):
            bat = Part("Device", "Battery_Cell", value="3xAAA", footprint=FP_BAT)
            bat[1] += vbat; bat[2] += gnd
            c_bat = Part("Device", "C", value="10uF", footprint=FP_C_BULK)
            c_bat[1] += vbat; c_bat[2] += gnd
            ldo = Part("Regulator_Linear", "AP2112K-3.3", footprint=FP_LDO)
            ldo[1] += vbat; ldo[2] += gnd; ldo[3] += vbat; ldo[5] += vcc
            c_in = Part("Device", "C", value="100nF", footprint=FP_C)
            c_in[1] += vbat; c_in[2] += gnd
            c_out = Part("Device", "C", value="100nF", footprint=FP_C)
            c_out[1] += vcc; c_out[2] += gnd

        @subcircuit
        def mcu_esp32c6(vcc, gnd, sda, scl, btn_up, btn_down, uart_tx, uart_rx,
                        en_net, boot_net):
            esp = Part("RF_Module", "ESP32-C6-MINI-1", footprint=FP_ESP32)
            esp[3] += vcc; esp[1] += gnd; esp[2] += gnd; esp[11] += gnd; esp[14] += gnd
            for p in range(36, 54): esp[p] += gnd
            esp[8] += en_net
            r_en = Part("Device", "R", value="10K", footprint=FP_R)
            r_en[1] += vcc; r_en[2] += en_net
            c_en = Part("Device", "C", value="100nF", footprint=FP_C)
            c_en[1] += en_net; c_en[2] += gnd
            esp[15] += sda; esp[16] += scl
            esp[9] += btn_up; esp[10] += btn_down
            esp[31] += uart_tx; esp[30] += uart_rx
            esp[23] += boot_net
            r_boot = Part("Device", "R", value="10K", footprint=FP_R)
            r_boot[1] += vcc; r_boot[2] += boot_net
            for _ in range(2):
                c = Part("Device", "C", value="100nF", footprint=FP_C)
                c[1] += vcc; c[2] += gnd
            c_bulk = Part("Device", "C", value="10uF", footprint=FP_C_BULK)
            c_bulk[1] += vcc; c_bulk[2] += gnd
            unused = [4,5,6,7,12,13,17,18,19,20,21,22,24,25,26,27,28,29,32,33,34,35]
            for p in unused: esp[p] += Net(f"ESP_P{p}_NC")

        @subcircuit
        def sensor_array(vcc, gnd, sda, scl):
            mux = Part("Interface_Expansion", "TCA9546APW", footprint=FP_MUX)
            mux[16] += vcc; mux[8] += gnd; mux[14] += scl; mux[15] += sda
            mux[1] += gnd; mux[2] += gnd; mux[13] += gnd
            r_rst = Part("Device", "R", value="10K", footprint=FP_R)
            r_rst[1] += vcc; r_rst[2] += mux[3]
            c_mux = Part("Device", "C", value="100nF", footprint=FP_C)
            c_mux[1] += vcc; c_mux[2] += gnd
            sd_pins = [4, 6, 9, 11]; sc_pins = [5, 7, 10, 12]
            for ch in range(4):
                ch_sda = Net(f"MUX_CH{ch}_SDA"); ch_scl = Net(f"MUX_CH{ch}_SCL")
                mux[sd_pins[ch]] += ch_sda; mux[sc_pins[ch]] += ch_scl
                addr_nets = [gnd, vcc, ch_sda, ch_scl]
                for addr_idx in range(4):
                    sensor = Part("Sensor_Optical", "TSL25911FN", footprint=FP_OPT,
                                  value="OPT3004")
                    sensor[1] += vcc; sensor[2] += addr_nets[addr_idx]
                    sensor[3] += gnd; sensor[4] += ch_scl
                    sensor[5] += Net(f"OPT_CH{ch}_{addr_idx}_INT"); sensor[6] += ch_sda
                    c_s = Part("Device", "C", value="100nF", footprint=FP_C)
                    c_s[1] += vcc; c_s[2] += gnd

        @subcircuit
        def imu_accel(vcc, gnd, sda, scl):
            imu = Part("Sensor_Motion", "LIS2DH", footprint=FP_IMU)
            imu[8] += vcc; imu[7] += vcc
            for p in [9,10,11,12,13,14]: imu[p] += gnd
            imu[1] += scl; imu[2] += sda; imu[4] += vcc; imu[3] += gnd
            imu[5] += Net("IMU_INT2_NC"); imu[6] += Net("IMU_INT1_NC")
            c_imu = Part("Device", "C", value="100nF", footprint=FP_C)
            c_imu[1] += vcc; c_imu[2] += gnd

        @subcircuit
        def color_temp_sensor(vcc, gnd, sda, scl):
            spec = Part("Sensor_Optical", "AS7343xDLG", footprint=FP_SPECTRAL)
            spec[1] += vcc; spec[2] += scl; spec[3] += gnd
            spec[4] += Net("AS7343_LDR_NC"); spec[5] += gnd
            spec[6] += Net("AS7343_GPIO_NC"); spec[7] += Net("AS7343_INT_NC")
            spec[8] += sda
            c_spec = Part("Device", "C", value="100nF", footprint=FP_C)
            c_spec[1] += vcc; c_spec[2] += gnd

        @subcircuit
        def oled_connector(vcc, gnd, sda, scl):
            conn = Part("Connector", "Conn_01x04_Pin", footprint=FP_OLED)
            conn[1] += gnd; conn[2] += vcc; conn[3] += scl; conn[4] += sda

        @subcircuit
        def user_interface(vcc, gnd, btn_up, btn_down):
            for btn_net in [btn_up, btn_down]:
                sw = Part("Switch", "SW_Push", footprint=FP_SW)
                sw[1] += btn_net; sw[2] += gnd
                r = Part("Device", "R", value="10K", footprint=FP_R)
                r[1] += vcc; r[2] += btn_net

        @subcircuit
        def debug_connector(vcc, gnd, uart_tx, uart_rx, en_net, boot_net):
            tag = Part("Connector_Generic", "Conn_02x03_Odd_Even", footprint=FP_TAG)
            tag[1] += vcc; tag[2] += uart_tx; tag[3] += uart_rx
            tag[4] += gnd; tag[5] += en_net; tag[6] += boot_net

        vbat = Net("VBAT")
        vcc = Net("VCC"); vcc.drive = POWER
        gnd = Net("GND"); gnd.drive = POWER
        sda = Net("I2C_SDA"); scl = Net("I2C_SCL")
        btn_up = Net("BTN_UP"); btn_down = Net("BTN_DOWN")
        uart_tx = Net("UART_TX"); uart_rx = Net("UART_RX")
        en_net = Net("ESP_EN"); boot_net = Net("ESP_BOOT")
        for net in [sda, scl]:
            r = Part("Device", "R", value="4.7K", footprint=FP_R)
            r[1] += vcc; r[2] += net
        power_supply(vbat, vcc, gnd)
        mcu_esp32c6(vcc, gnd, sda, scl, btn_up, btn_down, uart_tx, uart_rx,
                    en_net, boot_net)
        sensor_array(vcc, gnd, sda, scl)
        imu_accel(vcc, gnd, sda, scl)
        color_temp_sensor(vcc, gnd, sda, scl)
        oled_connector(vcc, gnd, sda, scl)
        user_interface(vcc, gnd, btn_up, btn_down)
        debug_connector(vcc, gnd, uart_tx, uart_rx, en_net, boot_net)

        import builtins
        ckt = builtins.default_circuit
        report = run_simulation(circuit=ckt)
        assert report.executable
        # Should have RC time constant checks that actually ran
        rc_checks = [c for c in report.checks if "rc_" in c.name]
        assert len(rc_checks) >= 1
        assert all(c.passed for c in rc_checks)
        # No simulation-requiring checks should have run (no source)
        assert len(report.measurements) == 0
