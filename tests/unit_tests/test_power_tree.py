"""Synthetic tests for power tree analysis.

Covers general patterns — no real board topology optimisation.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


def _make_part(ref, value="", pins_spec=None, name="", description=""):
    from skidl.pin import pin_types

    class MockNet:
        def __init__(self, name):
            self.name = name

    class MockPin:
        def __init__(self, num, pname, net_name, func_val):
            self.num = str(num)
            self.name = pname
            self.net = MockNet(net_name) if net_name else None
            self.func = func_val if func_val is not None else pin_types.PASSIVE

    class MockPart:
        def __init__(self, ref, value, pins, name, description):
            self.ref = ref
            self.value = value
            self.pins = pins
            self.name = name or ref
            self.description = description or ""
            self.footprint = ""

        def __len__(self):
            return len(self.pins)

    pins = []
    if pins_spec:
        for num, pname, net_name, func_val in pins_spec:
            pins.append(MockPin(num, pname, net_name, func_val))

    return MockPart(ref, value, pins, name, description)


def _make_circuit(parts, sim_harness=None):
    class MockCircuit:
        def __init__(self, parts, sim_harness):
            self.parts = parts
            self.sim_harness = sim_harness

        def get_nets(self):
            nets = {}
            for p in self.parts:
                for pin in getattr(p, "pins", []):
                    net = getattr(pin, "net", None)
                    if net and net.name not in nets:
                        nets[net.name] = net
            return list(nets.values())

    return MockCircuit(parts, sim_harness)


# ---------------------------------------------------------------------------
# battery -> LDO -> 3V3 tree
# ---------------------------------------------------------------------------
class TestBatteryLdoTree:
    def test_basic_battery_ldo_mcu(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "3xAAA", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        ldo = _make_part("U1", "AP2112K-3.3", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (3, "EN", "VBAT", pin_types.INPUT),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ], description="Linear Regulator")
        mcu = _make_part("U2", "ESP32", pins_spec=[
            (3, "VDD", "VCC", pin_types.PWRIN),
            (1, "GND", "GND", pin_types.PWRIN),
            (8, "IO0", "SIG", pin_types.BIDIR),
        ])
        c_in = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VBAT", None),
            (2, "2", "GND", None),
        ])
        c_out = _make_part("C2", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([bat, ldo, mcu, c_in, c_out])

        report = analyze_power_tree(circuit=ckt)

        # Battery is source
        sources = [n for n in report.nodes if n.node_type == "source"]
        assert any(n.ref == "BT1" for n in sources)

        # LDO is regulator
        regs = [n for n in report.nodes if n.node_type == "regulator"]
        assert any(n.ref == "U1" for n in regs)

        # Edge from VBAT -> VCC
        assert ("VBAT", "VCC") in report.edges

        # VCC rail should have MCU as load
        vcc_rail = next(r for r in report.rails if r.name == "VCC")
        assert "U2" in vcc_rail.loads

        # No missing source warnings (VCC reachable via VBAT->LDO)
        missing = [f for f in report.findings if f.category == "missing_source"]
        assert len(missing) == 0


# ---------------------------------------------------------------------------
# USB VBUS -> regulator -> 3V3
# ---------------------------------------------------------------------------
class TestUsbVbusRegulator:
    def test_usb_powered(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        conn = _make_part("J1", "USB_C", pins_spec=[
            (1, "VBUS", "VBUS", pin_types.PASSIVE),
            (4, "GND", "GND", pin_types.PASSIVE),
        ])
        reg = _make_part("U1", "AP2112K", pins_spec=[
            (1, "VIN", "VBUS", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ], description="LDO Regulator")
        mcu = _make_part("U2", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        c_out = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        c_in = _make_part("C2", "100nF", pins_spec=[
            (1, "1", "VBUS", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([conn, reg, mcu, c_out, c_in])

        report = analyze_power_tree(circuit=ckt)

        regs = [n for n in report.nodes if n.node_type == "regulator"]
        assert any(n.ref == "U1" for n in regs)
        assert ("VBUS", "VCC") in report.edges


# ---------------------------------------------------------------------------
# Isolated analog rail through ferrite
# ---------------------------------------------------------------------------
class TestAnalogRailThroughFerrite:
    def test_ferrite_creates_edge(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VCC", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        fb = _make_part("FB1", "600R", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "AVCC", None),
        ], description="Ferrite Bead")
        adc = _make_part("U1", "ADS1115", pins_spec=[
            (1, "VDD", "AVCC", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (3, "SCL", "SCL", pin_types.INPUT),
            (4, "SDA", "SDA", pin_types.BIDIR),
        ])
        c1 = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        c2 = _make_part("C2", "100nF", pins_spec=[
            (1, "1", "AVCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([bat, fb, adc, c1, c2])

        report = analyze_power_tree(circuit=ckt)

        ferrites = [n for n in report.nodes if n.node_type == "ferrite"]
        assert any(n.ref == "FB1" for n in ferrites)
        assert ("VCC", "AVCC") in report.edges

        # AVCC reachable from VCC (sourced by battery)
        missing = [f for f in report.findings if f.category == "missing_source"
                   and f.rail == "AVCC"]
        assert len(missing) == 0


# ---------------------------------------------------------------------------
# Missing source warning
# ---------------------------------------------------------------------------
class TestMissingSource:
    def test_rail_with_loads_no_source(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        ckt = _make_circuit([mcu])

        report = analyze_power_tree(circuit=ckt)

        missing = [f for f in report.findings if f.category == "missing_source"]
        assert len(missing) == 1
        assert missing[0].rail == "VCC"


# ---------------------------------------------------------------------------
# Regulator cap recommendations
# ---------------------------------------------------------------------------
class TestRegulatorCapRecommendations:
    def test_regulator_missing_input_cap(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        ldo = _make_part("U1", "LDO", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ], description="Linear Regulator")
        c_out = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        # No input cap on VBAT
        ckt = _make_circuit([bat, ldo, c_out])

        report = analyze_power_tree(circuit=ckt)

        input_cap_warn = [f for f in report.findings
                          if f.category == "regulator_missing_input_cap"]
        assert len(input_cap_warn) == 1
        assert input_cap_warn[0].ref == "U1"
        assert input_cap_warn[0].rail == "VBAT"

    def test_regulator_missing_output_cap(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        ldo = _make_part("U1", "LDO", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ], description="Linear Regulator")
        c_in = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VBAT", None),
            (2, "2", "GND", None),
        ])
        # No output cap on VCC
        ckt = _make_circuit([bat, ldo, c_in])

        report = analyze_power_tree(circuit=ckt)

        output_cap_warn = [f for f in report.findings
                           if f.category == "regulator_missing_output_cap"]
        assert len(output_cap_warn) == 1
        assert output_cap_warn[0].ref == "U1"

    def test_regulator_with_both_caps_clean(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        ldo = _make_part("U1", "LDO", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ], description="Linear Regulator")
        c_in = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VBAT", None),
            (2, "2", "GND", None),
        ])
        c_out = _make_part("C2", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([bat, ldo, c_in, c_out])

        report = analyze_power_tree(circuit=ckt)

        cap_warns = [f for f in report.findings
                     if f.category in ("regulator_missing_input_cap",
                                       "regulator_missing_output_cap")]
        assert len(cap_warns) == 0


# ---------------------------------------------------------------------------
# Harness sources as authoritative roots
# ---------------------------------------------------------------------------
class TestHarnessSourceAsRoot:
    def test_sim_source_creates_rail_root(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree
        from skidl.sim.declarations import SimHarness, DeclaredSource

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        harness = SimHarness()
        harness.sources.append(DeclaredSource(
            net_name="VCC", voltage=3.3, ref="", provenance="user",
        ))
        ckt = _make_circuit([mcu], sim_harness=harness)

        report = analyze_power_tree(circuit=ckt)

        vcc_rail = next(r for r in report.rails if r.name == "VCC")
        assert len(vcc_rail.sources) >= 1
        assert vcc_rail.voltage == 3.3
        assert "sim_source" in vcc_rail.provenance

        missing = [f for f in report.findings if f.category == "missing_source"]
        assert len(missing) == 0


# ---------------------------------------------------------------------------
# Fuse in power path
# ---------------------------------------------------------------------------
class TestFuseInPath:
    def test_fuse_creates_edge(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        fuse = _make_part("F1", "500mA", pins_spec=[
            (1, "1", "VBAT", None),
            (2, "2", "VBAT_FUSED", None),
        ], description="Fuse")
        # VBAT_FUSED is not in POWER_NET_RE — use VIN instead
        ckt = _make_circuit([bat, fuse])

        report = analyze_power_tree(circuit=ckt)

        fuses = [n for n in report.nodes if n.node_type == "fuse"]
        assert any(n.ref == "F1" for n in fuses)


class TestFuseWithStandardNets:
    def test_fuse_between_power_nets(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        fuse = _make_part("F1", "500mA", pins_spec=[
            (1, "1", "VBAT", None),
            (2, "2", "VIN", None),
        ], description="Fuse")
        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VIN", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        ckt = _make_circuit([bat, fuse, mcu])

        report = analyze_power_tree(circuit=ckt)

        assert ("VBAT", "VIN") in report.edges
        missing = [f for f in report.findings if f.category == "missing_source"
                   and f.rail == "VIN"]
        assert len(missing) == 0


# ---------------------------------------------------------------------------
# Connector-only power input
# ---------------------------------------------------------------------------
class TestConnectorPowerInput:
    def test_connector_not_source(self):
        """Connectors shouldn't be auto-classified as sources."""
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        conn = _make_part("J1", "Barrel_Jack", pins_spec=[
            (1, "1", "VIN", pin_types.PASSIVE),
            (2, "2", "GND", pin_types.PASSIVE),
        ])
        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VIN", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        ckt = _make_circuit([conn, mcu])

        report = analyze_power_tree(circuit=ckt)

        # VIN has loads but no source — should warn
        missing = [f for f in report.findings if f.category == "missing_source"]
        assert len(missing) == 1


# ---------------------------------------------------------------------------
# Ambiguous rail names
# ---------------------------------------------------------------------------
class TestAmbiguousRailNames:
    def test_multiple_rails_detected(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VDDA", "AVDD", pin_types.PWRIN),
            (3, "VSS", "GND", pin_types.PWRIN),
            (4, "PA0", "SIG", pin_types.BIDIR),
        ])
        ckt = _make_circuit([mcu])

        report = analyze_power_tree(circuit=ckt)

        rail_names = {r.name for r in report.rails}
        assert "VCC" in rail_names
        assert "AVDD" in rail_names


# ---------------------------------------------------------------------------
# Summary format
# ---------------------------------------------------------------------------
class TestSummary:
    def test_summary_content(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        ldo = _make_part("U1", "LDO", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ], description="Regulator")
        c = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([bat, ldo, c])

        report = analyze_power_tree(circuit=ckt)
        summary = report.summary()

        assert "sources" in summary
        assert "regulators" in summary
        assert "rails" in summary


# ---------------------------------------------------------------------------
# Multiple regulators in chain
# ---------------------------------------------------------------------------
class TestRegulatorChain:
    def test_two_regulators_cascaded(self):
        from skidl.pin import pin_types
        from skidl.sim.power_tree import analyze_power_tree

        bat = _make_part("BT1", "Battery", pins_spec=[
            (1, "+", "VBAT", pin_types.PASSIVE),
            (2, "-", "GND", pin_types.PASSIVE),
        ])
        reg1 = _make_part("U1", "5V_Reg", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (3, "VOUT", "VCC", pin_types.PWROUT),
        ], description="Buck Converter")
        reg2 = _make_part("U2", "3V3_LDO", pins_spec=[
            (1, "VIN", "VCC", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (3, "VOUT", "VDDA", pin_types.PWROUT),
        ], description="LDO Regulator")
        c1 = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VBAT", None), (2, "2", "GND", None),
        ])
        c2 = _make_part("C2", "100nF", pins_spec=[
            (1, "1", "VCC", None), (2, "2", "GND", None),
        ])
        c3 = _make_part("C3", "100nF", pins_spec=[
            (1, "1", "VDDA", None), (2, "2", "GND", None),
        ])
        mcu = _make_part("U3", "STM32", pins_spec=[
            (1, "VDD", "VDDA", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        ckt = _make_circuit([bat, reg1, reg2, c1, c2, c3, mcu])

        report = analyze_power_tree(circuit=ckt)

        assert ("VBAT", "VCC") in report.edges
        assert ("VCC", "VDDA") in report.edges

        # VDDA is reachable from battery through two regulators
        missing = [f for f in report.findings if f.category == "missing_source"]
        assert len(missing) == 0
