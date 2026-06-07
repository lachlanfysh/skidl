"""Smoke tests: run simulation ERC readiness on real circuits (45lux, MR-1).

These tests build the actual circuits from project scripts, then run
simulation_erc(execute=False) to verify the sim module handles
real-world part counts, hierarchies, and net topologies without crashing.
"""
from __future__ import annotations

import builtins
import os
import sys

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


def _reset_circuit():
    """Reset global circuit state between tests."""
    from skidl import reset
    reset()


def _build_45lux():
    """Build the 45lux circuit without generating schematic/PCB."""
    from skidl import Part, Net, POWER, KICAD9, set_default_tool, subcircuit
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
    FP_TAG = "Connector:Tag-Connect_TC2030-IDC-FP_2x03_P1.27mm_Vertical"
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

    # Top level
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
    mcu_esp32c6(vcc, gnd, sda, scl, btn_up, btn_down, uart_tx, uart_rx, en_net, boot_net)
    sensor_array(vcc, gnd, sda, scl)
    imu_accel(vcc, gnd, sda, scl)
    color_temp_sensor(vcc, gnd, sda, scl)
    oled_connector(vcc, gnd, sda, scl)
    user_interface(vcc, gnd, btn_up, btn_down)
    debug_connector(vcc, gnd, uart_tx, uart_rx, en_net, boot_net)


class TestSimReal45lux:
    def setup_method(self):
        _reset_circuit()

    def test_readiness_report(self):
        from skidl.sim.erc import simulation_erc

        _build_45lux()
        ckt = builtins.default_circuit

        report = simulation_erc(circuit=ckt, execute=False)

        assert report is not None
        assert not report.executable
        assert ckt.last_simulation_report is report

        # Should identify R/C as eligible, skip ICs/sensors/connectors
        assert len(report.missing_models) > 0
        # Should have missing_model findings for unmapped ICs
        model_findings = [f for f in report.findings if f.category == "missing_model"]
        assert len(model_findings) > 0
        # Should NOT crash on ~80+ part circuit
        print(report.summary())

    def test_plan_stats(self):
        from skidl.sim.plan import plan_simulation

        _build_45lux()
        ckt = builtins.default_circuit

        plan = plan_simulation(circuit=ckt)

        assert len(plan.eligible_parts) > 0
        assert len(plan.skipped_parts) > 0
        # R and C parts should be eligible
        r_entries = [e for e in plan.eligible_parts if e.spice_element == "R"]
        c_entries = [e for e in plan.eligible_parts if e.spice_element == "C"]
        assert len(r_entries) > 0
        assert len(c_entries) > 0
        # ICs, sensors, connectors should be skipped
        assert any("U" in ref or "J" in ref or "BT" in ref
                    for ref in plan.skipped_parts)
        print(plan.summary())

    def test_harness_source_makes_executable(self):
        """Acceptance: sim_source('VBAT', 4.5) + sim_assert_rail('VBAT', 4.5)
        produces an executable plan with a rail_presence check."""
        from skidl.sim.declarations import sim_source, sim_assert_rail
        from skidl.sim.plan import plan_simulation

        _build_45lux()
        ckt = builtins.default_circuit

        sim_source("VBAT", voltage=4.5, circuit=ckt)
        sim_assert_rail("VBAT", 4.5, circuit=ckt)

        plan = plan_simulation(circuit=ckt)

        assert plan.executable
        # Harness source should appear
        harness_sources = [s for s in plan.sources if s.harness_declared]
        assert len(harness_sources) == 1
        assert harness_sources[0].net_name == "VBAT"
        assert harness_sources[0].value == 4.5
        # Rail check should exist (deduplicated — source creates one)
        rail_checks = [c for c in plan.checks if c.check_type == "rail_presence"
                       and "VBAT" in c.nets]
        assert len(rail_checks) == 1
        assert rail_checks[0].expected == 4.5
        # No missing_source warning
        assert not any(f.category == "missing_source" for f in plan.findings)

    def test_harness_execution(self):
        """Acceptance: executing with sim_source produces checks (static + sim)."""
        from skidl.sim.declarations import sim_source, sim_assert_rail
        from skidl.sim.erc import simulation_erc

        _build_45lux()
        ckt = builtins.default_circuit

        sim_source("VBAT", voltage=4.5, circuit=ckt)
        sim_assert_rail("VBAT", 4.5, circuit=ckt)

        report = simulation_erc(circuit=ckt, execute=True)

        assert report.executable
        # Static checks (RC tau) should always produce results
        rc_checks = [c for c in report.checks if "rc_" in c.name]
        assert len(rc_checks) > 0
        assert all(c.passed for c in rc_checks)

    def test_decoupling_report(self):
        """Smoke: decoupling analysis on 45lux produces useful counts."""
        from skidl.sim.decoupling import analyze_decoupling

        _build_45lux()
        ckt = builtins.default_circuit

        report = analyze_decoupling(circuit=ckt)

        # 45lux has ICs (ESP32, TCA9546, sensors, LDO) with power pins
        assert len(report.ic_power_pins) > 0
        ic_refs = {p.ic_ref for p in report.ic_power_pins}
        assert len(ic_refs) >= 3  # at least ESP32, mux, some sensors

        # Should find decoupling caps (many 100nF caps in 45lux)
        local_caps = [c for c in report.caps if c.classification == "local"]
        assert len(local_caps) >= 5

        # Should have associations
        assert len(report.associations) > 0

        # Should not crash
        summary = report.summary()
        assert "ICs" in summary
        print(summary)

    def test_power_tree_report(self):
        """Smoke: power tree analysis on 45lux produces useful structure."""
        from skidl.sim.power_tree import analyze_power_tree

        _build_45lux()
        ckt = builtins.default_circuit

        report = analyze_power_tree(circuit=ckt)

        # Should find rails
        rail_names = {r.name for r in report.rails}
        assert "VCC" in rail_names or "VBAT" in rail_names

        # Should find at least the LDO as a regulator
        regs = [n for n in report.nodes if n.node_type == "regulator"]
        assert len(regs) >= 1

        # Should have edges
        assert len(report.edges) >= 1

        # Should not crash
        summary = report.summary()
        assert "rails" in summary
        print(summary)

    # ------------------------------------------------------------------
    # Step 5: Intent contract on real circuit
    # ------------------------------------------------------------------
    def test_intent_applies_to_45lux(self):
        """Acceptance: apply_simulation_intent on 45lux wires up harness."""
        from skidl.sim.intent import apply_simulation_intent

        _build_45lux()
        ckt = builtins.default_circuit

        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "3xAAA battery", "confidence": 0.9},
            ],
            "rail_assertions": [
                {"net": "VCC", "nominal": 3.3, "tolerance": 0.05,
                 "provenance": "AP2112K-3.3 LDO output", "confidence": 0.95},
            ],
            "loads": [
                {"net": "VCC", "current": 0.15,
                 "provenance": "ESP32-C6 typical", "confidence": 0.5},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert report.sources_added >= 1
        assert report.rail_assertions_added >= 1
        assert report.loads_added >= 1
        assert not any(f.severity == "error" for f in report.findings)
        assert ckt.sim_harness is not None
        vbat_sources = [s for s in ckt.sim_harness.sources
                        if s.net_name == "VBAT"]
        assert len(vbat_sources) >= 1

    # ------------------------------------------------------------------
    # Step 6: Rail sanity on real circuit
    # ------------------------------------------------------------------
    def test_rail_sanity_45lux(self):
        """Acceptance: rail sanity with declared VBAT finds R power checks."""
        from skidl.sim.declarations import sim_source
        from skidl.sim.rail_sanity import analyze_rail_sanity

        _build_45lux()
        ckt = builtins.default_circuit

        sim_source("VBAT", voltage=4.5, circuit=ckt)
        sim_source("VCC", voltage=3.3, circuit=ckt)

        report = analyze_rail_sanity(circuit=ckt)

        rail_names = {r.net_name for r in report.rails}
        assert len(rail_names) > 0
        assert "VCC" in rail_names or "VBAT" in rail_names
        # 45lux has pull-ups (10K on VCC) — should produce resistor checks
        assert len(report.resistor_checks) > 0
        # Summary should not crash
        summary = report.summary()
        assert "Rail sanity" in summary
        print(summary)

    def test_rail_sanity_assertion_pass(self):
        """Acceptance: rail assertion on declared VBAT passes."""
        from skidl.sim.declarations import sim_source, sim_assert_rail
        from skidl.sim.rail_sanity import analyze_rail_sanity

        _build_45lux()
        ckt = builtins.default_circuit

        sim_source("VBAT", voltage=4.5, circuit=ckt)
        sim_assert_rail("VBAT", 4.5, circuit=ckt)

        report = analyze_rail_sanity(circuit=ckt)

        assert len(report.rail_assertions) >= 1
        assert any(a.passed for a in report.rail_assertions)

    # ------------------------------------------------------------------
    # Step 7: PDN on real circuit
    # ------------------------------------------------------------------
    def test_pdn_45lux(self):
        """Acceptance: PDN analysis on 45lux with declared VCC finds caps."""
        from skidl.sim.declarations import sim_source
        from skidl.sim.pdn import analyze_pdn, PDNConstraints

        _build_45lux()
        ckt = builtins.default_circuit

        sim_source("VCC", voltage=3.3, circuit=ckt)

        report = analyze_pdn(
            circuit=ckt,
            constraints=PDNConstraints(freq_points=20),
        )

        # Should find VCC rail
        vcc_rail = next(
            (r for r in report.rails if r.net_name == "VCC"), None
        )
        assert vcc_rail is not None
        assert vcc_rail.voltage == 3.3
        assert vcc_rail.z_target == 0.050  # tier default for 3.3V
        # 45lux has many 100nF caps on VCC
        assert len(vcc_rail.caps) >= 3
        assert len(vcc_rail.frequencies) == 20
        assert len(vcc_rail.combined_impedance) == 20
        # Summary should be non-empty
        summary = report.summary()
        assert "PDN impedance report" in summary
        print(summary)

    # ------------------------------------------------------------------
    # Step 8: Layout feedback on real circuit
    # ------------------------------------------------------------------
    def test_layout_feedback_45lux_no_placement(self):
        """Acceptance: layout feedback without placement data still analyzes."""
        from skidl.sim.declarations import sim_source
        from skidl.sim.layout_feedback import analyze_layout_feedback

        _build_45lux()
        ckt = builtins.default_circuit

        sim_source("VCC", voltage=3.3, circuit=ckt)

        report = analyze_layout_feedback(circuit=ckt)

        assert report.decoupling_analyzed
        assert report.rail_sanity_analyzed
        # Without placement, no distance-based suggestions
        dist_suggestions = [
            s for s in report.suggestions
            if s.category in ("decap_far", "decap_too_far")
        ]
        assert len(dist_suggestions) == 0
        summary = report.summary()
        assert "Layout feedback" in summary
        print(summary)

    def test_layout_feedback_45lux_with_placement(self):
        """Acceptance: layout feedback with mock placement produces suggestions."""
        from skidl.sim.declarations import sim_source
        from skidl.sim.layout_feedback import analyze_layout_feedback

        _build_45lux()
        ckt = builtins.default_circuit

        sim_source("VCC", voltage=3.3, circuit=ckt)

        # Build mock placement: ICs at origin, caps scattered far away
        placed = {}
        for part in ckt.parts:
            ref = part.ref
            if ref.startswith("U") or ref.startswith("BT"):
                placed[ref] = (50.0, 50.0)
            elif ref.startswith("C"):
                placed[ref] = (200.0, 200.0)  # far away
            elif ref.startswith("R"):
                placed[ref] = (60.0, 50.0)
            else:
                placed[ref] = (50.0, 60.0)

        report = analyze_layout_feedback(circuit=ckt, placed=placed)

        assert report.decoupling_analyzed
        assert report.rail_sanity_analyzed
        # Caps placed 200mm away should trigger suggestions
        far_suggestions = [
            s for s in report.suggestions
            if s.category in ("decap_far", "decap_too_far")
        ]
        assert len(far_suggestions) > 0
        assert report.sim_penalty > 0
        summary = report.summary()
        assert "Layout feedback" in summary
        print(summary)

    # ------------------------------------------------------------------
    # Full pipeline acceptance
    # ------------------------------------------------------------------
    def test_full_pipeline_45lux(self):
        """Acceptance: full sim analysis pipeline runs end-to-end on 45lux."""
        from skidl.sim.intent import apply_simulation_intent
        from skidl.sim.erc import simulation_erc
        from skidl.sim.decoupling import analyze_decoupling
        from skidl.sim.power_tree import analyze_power_tree
        from skidl.sim.rail_sanity import analyze_rail_sanity
        from skidl.sim.pdn import analyze_pdn, PDNConstraints
        from skidl.sim.layout_feedback import analyze_layout_feedback

        _build_45lux()
        ckt = builtins.default_circuit

        # Step 5: Apply intent
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "3xAAA", "confidence": 0.9},
                {"net": "VCC", "voltage": 3.3,
                 "provenance": "LDO output", "confidence": 0.95},
            ],
            "rail_assertions": [
                {"net": "VCC", "nominal": 3.3, "tolerance": 0.05,
                 "provenance": "AP2112K spec", "confidence": 0.95},
            ],
            "loads": [
                {"net": "VCC", "current": 0.15,
                 "provenance": "ESP32-C6 typical", "confidence": 0.5},
            ],
        }
        intent_report = apply_simulation_intent(intent, circuit=ckt)
        assert intent_report.applied

        # Steps 1-2: ERC with execution
        erc_report = simulation_erc(circuit=ckt, execute=True)
        assert erc_report is not None

        # Step 3: Decoupling
        decoupling = analyze_decoupling(circuit=ckt)
        assert len(decoupling.caps) > 0

        # Step 4: Power tree
        power_tree = analyze_power_tree(circuit=ckt)
        assert len(power_tree.rails) > 0

        # Step 6: Rail sanity
        rail_sanity = analyze_rail_sanity(circuit=ckt)
        assert len(rail_sanity.rails) > 0

        # Step 7: PDN
        pdn = analyze_pdn(circuit=ckt, constraints=PDNConstraints(freq_points=10))
        assert len(pdn.rails) > 0

        # Step 8: Layout feedback
        feedback = analyze_layout_feedback(circuit=ckt)
        assert feedback.decoupling_analyzed
        assert feedback.rail_sanity_analyzed

        # All summaries should produce non-empty strings
        for report_obj in [erc_report, decoupling, power_tree,
                           rail_sanity, pdn, feedback]:
            summary = report_obj.summary()
            assert len(summary) > 10, f"Empty summary from {type(report_obj)}"

        print("=== Full Pipeline Results ===")
        print(intent_report.summary())
        print(rail_sanity.summary())
        print(pdn.summary())
        print(feedback.summary())
