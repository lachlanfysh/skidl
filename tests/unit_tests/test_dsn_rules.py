"""Tests for mcp_server/dsn_rules.py — semantic DSN net class generation."""

import textwrap
from pathlib import Path

import pytest

from mcp_server.dsn_rules import (
    ANALOG_CLASS,
    BUS_CLASS,
    CLOCK_CLASS,
    GROUND_CLASS,
    POWER_CLASS,
    SIGNAL_CLASS,
    USB_CLASS,
    NetClassDef,
    _extract_net_names,
    _find_matching_paren,
    _generate_class_section,
    _generate_via_padstack,
    _group_by_class,
    classify_nets,
    inject_net_classes,
    summarize_classes,
)


class TestClassifyNets:
    def test_power_nets(self):
        names = [
            "VCC", "VDD", "VDDA", "+3.3V", "-3.3V", "+5V", "-5V",
            "+12V", "-12V", "VIN", "VOUT", "VBAT", "VREF",
        ]
        result = classify_nets(names)
        for n in names:
            assert result[n] is POWER_CLASS, f"{n} should be Power"

    def test_ground_nets(self):
        names = ["GND", "AGND", "DGND", "VSS", "AVSS", "DVSS", "GNDA", "GNDD"]
        result = classify_nets(names)
        for n in names:
            assert result[n] is GROUND_CLASS, f"{n} should be Ground"

    def test_analog_nets(self):
        names = ["AIN0", "AIN1", "ADC", "ADC0", "SENSE", "VREF", "AOUT0", "AN3"]
        result = classify_nets(names)
        # VREF matches POWER_NET_RE first (it's checked before ANALOG_RE)
        for n in names:
            if n == "VREF":
                assert result[n] is POWER_CLASS
            else:
                assert result[n] is ANALOG_CLASS, f"{n} should be Analog"

    def test_usb_nets(self):
        names = ["D+", "D-", "DP", "DM", "USB_DP", "USB_DM"]
        result = classify_nets(names)
        for n in names:
            assert result[n] is USB_CLASS, f"{n} should be USB"

    def test_clock_nets(self):
        names = ["CLK", "XTAL", "OSC", "XI", "XO", "CLK0"]
        result = classify_nets(names)
        for n in names:
            assert result[n] is CLOCK_CLASS, f"{n} should be Clock"

    def test_i2c_nets(self):
        result = classify_nets(["SDA", "SCL", "SDA0", "SCL1"])
        for n in result:
            assert result[n] is BUS_CLASS, f"{n} should be Bus"

    def test_spi_nets(self):
        names = ["MOSI", "MISO", "SCK", "SCLK", "SDI", "SDO", "COPI", "CIPO"]
        result = classify_nets(names)
        for n in names:
            assert result[n] is BUS_CLASS, f"{n} should be Bus"

    def test_default_signal(self):
        result = classify_nets(["NEOPIXEL_DATA", "LED1", "RESET", "MY_NET"])
        for n in result:
            assert result[n] is SIGNAL_CLASS, f"{n} should be Signal"

    def test_empty_and_quoted_empty(self):
        result = classify_nets(["", '""'])
        assert result[""] is SIGNAL_CLASS
        assert result['""'] is SIGNAL_CLASS

    def test_case_insensitive(self):
        result = classify_nets(["vcc", "gnd", "sda", "ain0", "clk", "d+"])
        assert result["vcc"] is POWER_CLASS
        assert result["gnd"] is GROUND_CLASS
        assert result["sda"] is BUS_CLASS
        assert result["ain0"] is ANALOG_CLASS
        assert result["clk"] is CLOCK_CLASS
        assert result["d+"] is USB_CLASS

    def test_ground_before_power(self):
        """GND should match Ground, not fall through to Power regex."""
        result = classify_nets(["GND", "VSS"])
        assert result["GND"] is GROUND_CLASS
        assert result["VSS"] is GROUND_CLASS


class TestGroupByClass:
    def test_groups_sorted(self):
        classified = {
            "VCC": POWER_CLASS,
            "GND": GROUND_CLASS,
            "SDA": BUS_CLASS,
            "LED": SIGNAL_CLASS,
        }
        groups = _group_by_class(classified)
        assert sorted(groups.keys()) == ["Bus", "Ground", "Power", "Signal"]
        assert groups["Power"] == ["VCC"]
        assert groups["Bus"] == ["SDA"]

    def test_nets_sorted_within_group(self):
        classified = {"Z_NET": SIGNAL_CLASS, "A_NET": SIGNAL_CLASS, "M_NET": SIGNAL_CLASS}
        groups = _group_by_class(classified)
        assert groups["Signal"] == ["A_NET", "M_NET", "Z_NET"]


class TestFindMatchingParen:
    def test_simple(self):
        assert _find_matching_paren("(abc)", 0) == 4

    def test_nested(self):
        text = "(a (b (c) d) e)"
        assert _find_matching_paren(text, 0) == len(text) - 1

    def test_quoted_paren(self):
        text = '(a ")" b)'
        assert _find_matching_paren(text, 0) == len(text) - 1

    def test_no_match(self):
        assert _find_matching_paren("(abc", 0) == -1


class TestExtractNetNames:
    def test_extracts_names(self):
        dsn = textwrap.dedent("""\
            (network
              (net GND (pins U1-4 C1-2))
              (net VCC (pins U1-1 C1-1))
              (net SDA (pins U1-5 R1-1))
            )
        """)
        names = _extract_net_names(dsn)
        assert "GND" in names
        assert "VCC" in names
        assert "SDA" in names


class TestGenerateClassSection:
    def test_format(self):
        section = _generate_class_section(POWER_CLASS, ["VCC", "+3.3V"])
        assert "(class Power VCC +3.3V" in section
        assert "(width 250)" in section
        assert "(clearance 200)" in section
        assert "Via[0-1]_600:300_um" in section

    def test_signal_defaults(self):
        section = _generate_class_section(SIGNAL_CLASS, ["NET1"])
        assert "(width 250)" in section
        assert "(clearance 200)" in section


class TestGenerateViaPadstack:
    def test_two_layer(self):
        ps = _generate_via_padstack(POWER_CLASS, layer_count=2)
        assert "F.Cu" in ps
        assert "B.Cu" in ps
        assert "In1.Cu" not in ps

    def test_four_layer(self):
        ps = _generate_via_padstack(POWER_CLASS, layer_count=4)
        assert "F.Cu" in ps
        assert "In1.Cu" in ps
        assert "In2.Cu" in ps
        assert "B.Cu" in ps


class TestInjectNetClasses:
    MINIMAL_DSN = textwrap.dedent("""\
        (pcb board.dsn
          (structure
            (layer F.Cu (type signal))
            (layer B.Cu (type signal))
          )
          (library
            (padstack "Via[0-1]_600:300_um"
              (shape (circle F.Cu 600))
              (shape (circle B.Cu 600))
              (attach off)
            )
          )
          (network
            (net GND (pins U1-4 C1-2))
            (net VCC (pins U1-1 C1-1))
            (net SDA (pins U1-5 R1-1))
            (net AIN0 (pins U1-6 R2-1))
            (net LED_DATA (pins U1-7 D1-1))
            (class kicad_default GND VCC SDA AIN0 LED_DATA
              (circuit
                (use_via "Via[0-1]_600:300_um")
              )
              (rule
                (width 250)
                (clearance 200)
              )
            )
          )
          (wiring)
        )
    """)

    def test_replaces_kicad_default(self, tmp_path):
        dsn_file = tmp_path / "board.dsn"
        dsn_file.write_text(self.MINIMAL_DSN)

        result = inject_net_classes(str(dsn_file))

        content = dsn_file.read_text()
        assert "kicad_default" not in content
        assert "(class Analog" in content
        assert "(class Bus" in content
        assert "(class Ground" in content
        assert "(class Power" in content
        assert "(class Signal" in content

    def test_returns_grouped_nets(self, tmp_path):
        dsn_file = tmp_path / "board.dsn"
        dsn_file.write_text(self.MINIMAL_DSN)

        groups = inject_net_classes(str(dsn_file))

        assert "Ground" in groups
        assert "GND" in groups["Ground"]
        assert "Power" in groups
        assert "VCC" in groups["Power"]
        assert "Bus" in groups
        assert "SDA" in groups["Bus"]
        assert "Analog" in groups
        assert "AIN0" in groups["Analog"]

    def test_adds_missing_via_padstacks(self, tmp_path):
        dsn_file = tmp_path / "board.dsn"
        dsn_file.write_text(self.MINIMAL_DSN)

        inject_net_classes(str(dsn_file))
        content = dsn_file.read_text()

        # Power class uses fine default vias unless explicitly widened.
        assert "Via[0-1]_600:300_um" in content

    def test_extracts_nets_when_not_provided(self, tmp_path):
        dsn_file = tmp_path / "board.dsn"
        dsn_file.write_text(self.MINIMAL_DSN)

        groups = inject_net_classes(str(dsn_file), net_names=None)
        all_nets = [n for nets in groups.values() for n in nets]
        assert "GND" in all_nets
        assert "VCC" in all_nets

    def test_explicit_net_names(self, tmp_path):
        dsn_file = tmp_path / "board.dsn"
        dsn_file.write_text(self.MINIMAL_DSN)

        groups = inject_net_classes(str(dsn_file), net_names=["VCC", "GND"])
        all_nets = [n for nets in groups.values() for n in nets]
        assert "VCC" in all_nets
        assert "GND" in all_nets
        assert "SDA" not in all_nets

    def test_idempotent(self, tmp_path):
        """Running inject twice should produce the same result."""
        dsn_file = tmp_path / "board.dsn"
        dsn_file.write_text(self.MINIMAL_DSN)

        inject_net_classes(str(dsn_file))
        first_content = dsn_file.read_text()

        inject_net_classes(str(dsn_file))
        second_content = dsn_file.read_text()

        assert first_content == second_content


class TestSummarizeClasses:
    def test_output(self):
        groups = {"Power": ["VCC", "+3.3V"], "Signal": ["LED1"]}
        text = summarize_classes(groups)
        assert "Power:" in text
        assert "VCC" in text
        assert "Signal:" in text

    def test_truncation(self):
        groups = {"Signal": [f"NET{i}" for i in range(20)]}
        text = summarize_classes(groups)
        assert "+12 more" in text
