"""Synthetic tests for decoupling confidence report.

These cover general patterns — no real board topology optimisation.
"""
from __future__ import annotations

import builtins
import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


def _reset():
    from skidl import reset
    reset()


def _make_part(ref, value="", pins_spec=None, footprint=""):
    """Create a minimal mock Part with controllable pins.

    pins_spec: list of (pin_num, pin_name, net_name, func) tuples.
    func should be a pin_types enum value or None for default (PASSIVE).
    """
    from skidl.pin import pin_types

    class MockNet:
        def __init__(self, name):
            self.name = name

    class MockPin:
        def __init__(self, num, name, net_name, func_val):
            self.num = str(num)
            self.name = name
            self.net = MockNet(net_name) if net_name else None
            self.func = func_val if func_val is not None else pin_types.PASSIVE

    class MockPart:
        def __init__(self, ref, value, pins, footprint):
            self.ref = ref
            self.value = value
            self.pins = pins
            self.footprint = footprint
            self.name = ref

        def __len__(self):
            return len(self.pins)

    pins = []
    if pins_spec:
        for num, name, net_name, func_val in pins_spec:
            pins.append(MockPin(num, name, net_name, func_val))

    return MockPart(ref, value, pins, footprint)


def _make_circuit(parts):
    """Create a minimal mock Circuit."""
    class MockCircuit:
        def __init__(self, parts):
            self.parts = parts

        def get_nets(self):
            nets = {}
            for p in self.parts:
                for pin in getattr(p, "pins", []):
                    net = getattr(pin, "net", None)
                    if net and net.name not in nets:
                        nets[net.name] = net
            return list(nets.values())

    return MockCircuit(parts)


# ---------------------------------------------------------------------------
# Test: IC with local decoupling cap found
# ---------------------------------------------------------------------------
class TestLocalCapFound:
    def test_mcu_with_100nf(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
            (4, "PA1", "SIG2", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        assert len(report.ic_power_pins) >= 1
        vcc_pins = [p for p in report.ic_power_pins if p.net_name == "VCC"]
        assert len(vcc_pins) >= 1
        assert vcc_pins[0].ic_ref == "U1"

        assert len(report.caps) == 1
        assert report.caps[0].classification == "local"

        assert len(report.associations) == 1
        assert report.associations[0].ic_ref == "U1"
        assert report.associations[0].rail == "VCC"

        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0


class TestWrongRailCapIgnored:
    def test_cap_on_different_rail(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VBAT", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        vcc_assocs = [a for a in report.associations if a.rail == "VCC"]
        assert len(vcc_assocs) == 0

        missing = [f for f in report.findings
                   if f.category == "missing_decap" and f.rail == "VCC"]
        assert len(missing) == 1


class TestBulkCapReportedSeparately:
    def test_10uf_classified_as_bulk(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        local = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        bulk = _make_part("C2", "10uF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, local, bulk])

        report = analyze_decoupling(circuit=ckt)

        local_caps = [c for c in report.caps if c.classification == "local"]
        bulk_caps = [c for c in report.caps if c.classification == "bulk"]
        assert len(local_caps) == 1
        assert local_caps[0].ref == "C1"
        assert len(bulk_caps) == 1
        assert bulk_caps[0].ref == "C2"


class TestFarCapWarnsWithPlacement:
    def test_distance_warning(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling, DecouplingThresholds

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        placed = {"U1": (50.0, 50.0), "C1": (60.0, 50.0)}  # 10mm apart
        thresholds = DecouplingThresholds(local_distance_warn_mm=5.0)

        report = analyze_decoupling(
            circuit=ckt, placed=placed, thresholds=thresholds,
        )

        assert report.layout_available
        far = [f for f in report.findings if f.category == "far_local"]
        assert len(far) == 1
        assert "10.0mm" in far[0].message

    def test_close_cap_no_warning(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling, DecouplingThresholds

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        placed = {"U1": (50.0, 50.0), "C1": (52.0, 50.0)}  # 2mm apart
        thresholds = DecouplingThresholds(local_distance_warn_mm=5.0)

        report = analyze_decoupling(
            circuit=ckt, placed=placed, thresholds=thresholds,
        )

        far = [f for f in report.findings if f.category == "far_local"]
        assert len(far) == 0


class TestMissingDecap:
    def test_ic_with_no_cap(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        ckt = _make_circuit([mcu])

        report = analyze_decoupling(circuit=ckt)

        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 1
        assert missing[0].ic_ref == "U1"
        assert missing[0].rail == "VCC"


class TestMultipleVDDPins:
    def test_mcu_with_two_vdd_pins_same_rail(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (11, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        # Two power pins but same rail — one association is enough
        vcc_assocs = [a for a in report.associations if a.rail == "VCC"]
        assert len(vcc_assocs) == 1
        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0

    def test_mcu_with_separate_avdd_and_dvdd(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "DVDD", "DVDD", pin_types.PWRIN),
            (11, "AVDD", "AVDD", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap_d = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "DVDD", None),
            (2, "2", "GND", None),
        ])
        # No cap on AVDD
        ckt = _make_circuit([mcu, cap_d])

        report = analyze_decoupling(circuit=ckt)

        missing = [f for f in report.findings
                   if f.category == "missing_decap" and f.rail == "AVDD"]
        assert len(missing) == 1

        dvdd_missing = [f for f in report.findings
                        if f.category == "missing_decap" and f.rail == "DVDD"]
        assert len(dvdd_missing) == 0


class TestSensorArraySharedRail:
    def test_four_sensors_one_rail(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        parts = []
        for i in range(4):
            sensor = _make_part(f"U{i+1}", "OPT3004", pins_spec=[
                (1, "VDD", "VCC", pin_types.PWRIN),
                (3, "GND", "GND", pin_types.PWRIN),
                (4, "SCL", f"SCL_{i}", pin_types.INPUT),
                (5, "SDA", f"SDA_{i}", pin_types.BIDIR),
            ])
            parts.append(sensor)
            cap = _make_part(f"C{i+1}", "100nF", pins_spec=[
                (1, "1", "VCC", None),
                (2, "2", "GND", None),
            ])
            parts.append(cap)

        ckt = _make_circuit(parts)
        report = analyze_decoupling(circuit=ckt)

        # Each sensor should be associated with caps on VCC
        for i in range(4):
            ic_assocs = [a for a in report.associations if a.ic_ref == f"U{i+1}"]
            assert len(ic_assocs) >= 1

        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0


class TestNetNameFallback:
    def test_pin_without_pwrin_type_but_vcc_net(self):
        """IC pin not typed PWRIN but connected to VCC net and named VCC."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "ATTINY85", pins_spec=[
            (8, "VCC", "VCC", pin_types.PASSIVE),
            (4, "GND", "GND", pin_types.PASSIVE),
            (1, "PB0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        vcc_pins = [p for p in report.ic_power_pins if p.net_name == "VCC"]
        assert len(vcc_pins) >= 1
        assert vcc_pins[0].detection == "net_name"


class TestPassivePartExcluded:
    def test_resistors_not_treated_as_ic(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        r1 = _make_part("R1", "10K", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "SIG", None),
        ])
        ckt = _make_circuit([r1])

        report = analyze_decoupling(circuit=ckt)

        assert len(report.ic_power_pins) == 0


class TestSchematicOnlyMode:
    def test_no_placement_no_distance_warnings(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        assert not report.layout_available
        far = [f for f in report.findings
               if f.category in ("far_local", "far_bulk")]
        assert len(far) == 0


class TestBatteryLdoMcuRail:
    def test_battery_ldo_mcu_pattern(self):
        """battery -> LDO -> 3V3 MCU rail with decap."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        ldo = _make_part("U1", "AP2112K-3.3", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (3, "EN", "VBAT", pin_types.INPUT),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ])
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
        ckt = _make_circuit([ldo, mcu, c_in, c_out])

        report = analyze_decoupling(circuit=ckt)

        # MCU should have VCC decap
        mcu_assocs = [a for a in report.associations if a.ic_ref == "U2"]
        assert any(a.rail == "VCC" for a in mcu_assocs)

        # LDO input pin on VBAT should have decap
        ldo_assocs = [a for a in report.associations if a.ic_ref == "U1"]
        assert any(a.rail == "VBAT" for a in ldo_assocs)

        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0


class TestConnectorOnlyPowerInput:
    def test_connector_not_treated_as_ic_needing_decap(self):
        """Connector providing power should not trigger missing decap."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        conn = _make_part("J1", "USB_C", pins_spec=[
            (1, "VBUS", "VBUS", pin_types.PASSIVE),
            (4, "GND", "GND", pin_types.PASSIVE),
        ])
        ckt = _make_circuit([conn])

        report = analyze_decoupling(circuit=ckt)

        # Connector has only 2 pins — should be excluded from IC detection
        assert len(report.ic_power_pins) == 0
        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0


class TestUnknownCapValue:
    def test_unparseable_value(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "DNP", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        unknown = [c for c in report.caps if c.classification == "unknown"]
        assert len(unknown) == 1
        info = [f for f in report.findings if f.category == "unknown_value"]
        assert len(info) == 1


class TestReportSummary:
    def test_summary_format(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        summary = report.summary()
        assert "1 ICs" in summary
        assert "1 power rails" in summary
        assert "Local caps: 1" in summary


class TestOpAmpBiasDivider:
    def test_opamp_with_power_pins(self):
        """Op-amp has VCC+/VCC- power pins; decap should be detected."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        opamp = _make_part("U1", "OPA2340", pins_spec=[
            (8, "VDD", "VCC", pin_types.PWRIN),
            (4, "VSS", "GND", pin_types.PWRIN),
            (1, "OUT", "VOUT", pin_types.OUTPUT),
            (2, "IN-", "FB", pin_types.INPUT),
            (3, "IN+", "REF", pin_types.INPUT),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        # Bias divider resistors (not ICs, should be ignored)
        r1 = _make_part("R1", "10K", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "REF", None),
        ])
        r2 = _make_part("R2", "10K", pins_spec=[
            (1, "1", "REF", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([opamp, cap, r1, r2])

        report = analyze_decoupling(circuit=ckt)

        assert len(report.ic_power_pins) >= 1
        assert report.ic_power_pins[0].ic_ref == "U1"
        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0


class TestAnalogRailThroughFerrite:
    def test_ferrite_bead_excluded_from_ic(self):
        """Ferrite bead (FB prefix, 2-pin) should not be treated as IC."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        fb = _make_part("FB1", "600R", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "AVCC", None),
        ])
        adc = _make_part("U1", "ADS1115", pins_spec=[
            (1, "VDD", "AVCC", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (3, "SCL", "SCL", pin_types.INPUT),
            (4, "SDA", "SDA", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "AVCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([fb, adc, cap])

        report = analyze_decoupling(circuit=ckt)

        # FB1 should not appear as IC
        ic_refs = {p.ic_ref for p in report.ic_power_pins}
        assert "FB1" not in ic_refs
        # ADC should have decap on AVCC
        assert "U1" in ic_refs
        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0


class TestMissingLocalWarning:
    def test_only_bulk_no_local(self):
        """IC with only a bulk cap should warn about missing local."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        bulk = _make_part("C1", "10uF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, bulk])

        report = analyze_decoupling(circuit=ckt)

        missing_local = [f for f in report.findings
                         if f.category == "missing_local"]
        assert len(missing_local) == 1
        assert missing_local[0].ic_ref == "U1"


class TestBulkCapNearRegulator:
    def test_bulk_near_regulator_output(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        ldo = _make_part("U1", "AP2112K", pins_spec=[
            (1, "VIN", "VBAT", pin_types.PWRIN),
            (2, "GND", "GND", pin_types.PWRIN),
            (5, "VOUT", "VCC", pin_types.PWROUT),
        ])
        c_bulk = _make_part("C1", "10uF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        c_in = _make_part("C2", "100nF", pins_spec=[
            (1, "1", "VBAT", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([ldo, c_bulk, c_in])

        placed = {"U1": (50.0, 50.0), "C1": (52.0, 50.0), "C2": (48.0, 50.0)}

        report = analyze_decoupling(circuit=ckt, placed=placed)

        assert report.layout_available
        # LDO input should have decap association on VBAT
        vbat_assocs = [a for a in report.associations
                       if a.ic_ref == "U1" and a.rail == "VBAT"]
        assert len(vbat_assocs) >= 1


# ---------------------------------------------------------------------------
# ADVERSARIAL: tiny cap not counted as decap
# ---------------------------------------------------------------------------
class TestTinyCapNotLocal:
    def test_10pf_classified_as_filter(self):
        """A 10pF rail-to-ground cap should not satisfy local decoupling."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        tiny_cap = _make_part("C1", "10pF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, tiny_cap])

        report = analyze_decoupling(circuit=ckt)

        filters = [c for c in report.caps if c.classification == "filter"]
        assert len(filters) == 1
        assert filters[0].ref == "C1"

        # Should NOT satisfy local decoupling requirement
        missing_local = [f for f in report.findings
                         if f.category == "missing_local"]
        assert len(missing_local) == 1

    def test_1nf_classified_as_filter(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "1nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        filters = [c for c in report.caps if c.classification == "filter"]
        assert len(filters) == 1

    def test_22nf_counts_as_local(self):
        """22nF is above the 10nF threshold — should be classified as local."""
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        mcu = _make_part("U1", "STM32", pins_spec=[
            (1, "VDD", "VCC", pin_types.PWRIN),
            (2, "VSS", "GND", pin_types.PWRIN),
            (3, "PA0", "SIG", pin_types.BIDIR),
        ])
        cap = _make_part("C1", "22nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        ckt = _make_circuit([mcu, cap])

        report = analyze_decoupling(circuit=ckt)

        local_caps = [c for c in report.caps if c.classification == "local"]
        assert len(local_caps) == 1


# ---------------------------------------------------------------------------
# ADVERSARIAL: one cap shared across many ICs
# ---------------------------------------------------------------------------
class TestOneCapManyICs:
    def test_single_cap_associates_with_all_ics_on_rail(self):
        from skidl.pin import pin_types
        from skidl.sim.decoupling import analyze_decoupling

        parts = []
        for i in range(5):
            ic = _make_part(f"U{i+1}", f"IC{i}", pins_spec=[
                (1, "VDD", "VCC", pin_types.PWRIN),
                (2, "GND", "GND", pin_types.PWRIN),
                (3, "IO", f"SIG{i}", pin_types.BIDIR),
            ])
            parts.append(ic)

        # Only ONE decoupling cap for all 5 ICs
        cap = _make_part("C1", "100nF", pins_spec=[
            (1, "1", "VCC", None),
            (2, "2", "GND", None),
        ])
        parts.append(cap)
        ckt = _make_circuit(parts)

        report = analyze_decoupling(circuit=ckt)

        # The single cap should associate with all 5 ICs
        assocs = [a for a in report.associations if a.cap_ref == "C1"]
        assert len(assocs) == 5

        # No missing_decap — technically there IS a cap on VCC
        missing = [f for f in report.findings if f.category == "missing_decap"]
        assert len(missing) == 0
