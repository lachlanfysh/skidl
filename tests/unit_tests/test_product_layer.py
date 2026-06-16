"""Tests for the product layer: enrichment, corrections, exceptions, design review, policy.

Covers:
- schemas/enrichment.py: passive rules, block enrichment, design review exceptions
- schemas/corrections.py: all action handlers (add_parts, set_layers, etc.)
- schemas/exceptions.py: exception construction, waiver keys
- mcp_server/policy.py: auto-correction logic, decision kinds
- mcp_server/engine_worker.py: routing/DRC graceful fallbacks
"""

from __future__ import annotations

import copy
import json

import pytest

from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.enrichment import (
    BULK_CAP_RE,
    DECAP_VALUE_RE,
    design_review_exceptions,
    enrich,
    enrich_blocks,
)
from schemas.exceptions import (
    ActionType,
    Candidate,
    DesignException,
    ExcCode,
    Severity,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def minimal_spec_dict():
    return {
        "board": {"name": "test"},
        "parts": [
            {"ref": "U1", "lib": "Device", "part": "R", "value": "10K",
             "footprint": "Resistor_SMD:R_0603_1608Metric"},
        ],
        "nets": [
            {"name": "VCC", "power": True, "pins": ["U1.1"]},
            {"name": "GND", "power": True, "pins": ["U1.2"]},
        ],
    }


@pytest.fixture
def ic_spec_dict():
    """Spec with an IC that has power pins — triggers enrichment rules."""
    return {
        "board": {"name": "test-ic"},
        "parts": [
            {"ref": "U1", "lib": "Sensor_Temperature", "part": "TMP117",
             "footprint": "Package_SON:WSON-6"},
            {"ref": "J1", "lib": "Connector_Generic", "part": "Conn_01x04",
             "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"},
        ],
        "nets": [
            {"name": "VCC", "power": True, "pins": ["U1.VDD", "J1.1"]},
            {"name": "GND", "power": True, "pins": ["U1.GND", "J1.4"]},
            {"name": "SDA", "pins": ["U1.SDA", "J1.2"]},
            {"name": "SCL", "pins": ["U1.SCL", "J1.3"]},
        ],
    }


@pytest.fixture
def minimal_circuit_spec(minimal_spec_dict):
    return CircuitSpec.model_validate(minimal_spec_dict)


@pytest.fixture
def ic_circuit_spec(ic_spec_dict):
    return CircuitSpec.model_validate(ic_spec_dict)


# ---------------------------------------------------------------------------
# Enrichment: passive rules
# ---------------------------------------------------------------------------

class TestEnrichmentPassiveRules:
    def test_a1_adds_decoupling_cap(self, ic_spec_dict):
        enriched, actions = enrich(ic_spec_dict)
        decap_actions = [a for a in actions if a["rule"] == "A1"]
        assert len(decap_actions) >= 1
        added_refs = [r for a in decap_actions for r in a["parts_added"]]
        assert all(ref.startswith("C") for ref in added_refs)

    def test_a1_no_duplicate_decap(self, ic_spec_dict):
        ic_spec_dict["parts"].append({
            "ref": "C1", "lib": "Device", "part": "C", "value": "100nF",
            "footprint": "Capacitor_SMD:C_0603_1608Metric",
        })
        ic_spec_dict["nets"][0]["pins"].append("C1.1")
        ic_spec_dict["nets"][1]["pins"].append("C1.2")
        enriched, actions = enrich(ic_spec_dict)
        decap_actions = [a for a in actions if a["rule"] == "A1"]
        assert len(decap_actions) == 0

    def test_b1_adds_i2c_pullups(self, ic_spec_dict):
        enriched, actions = enrich(ic_spec_dict)
        pullup_actions = [a for a in actions if a["rule"] == "B1"]
        assert len(pullup_actions) == 2
        for a in pullup_actions:
            assert "4.7K" in a["description"]

    def test_a4_usb_cc_pulldowns(self):
        spec = {
            "parts": [
                {"ref": "J1", "lib": "Connector_USB", "part": "USB_C_Receptacle",
                 "footprint": "Connector_USB:USB_C_Receptacle"},
            ],
            "nets": [
                {"name": "VBUS", "power": True, "pins": ["J1.VBUS"]},
                {"name": "GND", "power": True, "pins": ["J1.GND"]},
                {"name": "CC1", "pins": ["J1.CC1"]},
                {"name": "CC2", "pins": ["J1.CC2"]},
            ],
        }
        enriched, actions = enrich(spec)
        cc_actions = [a for a in actions if a["rule"] == "A4"]
        assert len(cc_actions) == 2

    def test_a4_usb_cc_pulldowns_detects_usb_c_hyphen_and_usb4105(self):
        spec = {
            "parts": [
                {
                    "ref": "J_USB",
                    "lib": "",
                    "part": "USB-C receptacle",
                    "footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
                },
            ],
            "nets": [
                {"name": "VBUS", "power": True, "pins": ["J_USB.VBUS"]},
                {"name": "GND", "power": True, "pins": ["J_USB.GND"]},
                {"name": "CC1", "pins": ["J_USB.CC1"]},
                {"name": "CC2", "pins": ["J_USB.CC2"]},
            ],
        }

        enriched, actions = enrich(spec)

        assert len([a for a in actions if a["rule"] == "A4"]) == 2

    def test_enrich_returns_deepcopy(self, ic_spec_dict):
        original_parts = len(ic_spec_dict["parts"])
        enriched, _ = enrich(ic_spec_dict)
        assert len(ic_spec_dict["parts"]) == original_parts


# ---------------------------------------------------------------------------
# Enrichment: block templates
# ---------------------------------------------------------------------------

class TestBlockEnrichment:
    def test_no_blocks_on_simple_board(self):
        spec = {
            "parts": [{"ref": "R1", "lib": "Device", "part": "R", "value": "10K",
                        "footprint": "Resistor_SMD:R_0603_1608Metric"}],
            "nets": [{"name": "GND", "power": True, "pins": ["R1.2"]}],
        }
        enriched, actions = enrich_blocks(spec, "Simple resistor test board")
        assert len(actions) == 0

    def test_lipo_block_triggered_by_keyword(self):
        spec = {
            "parts": [{"ref": "U1", "lib": "MCU_Nordic", "part": "nRF52840",
                        "footprint": "Package_DFN_QFN:QFN-73"}],
            "nets": [
                {"name": "VCC", "power": True, "pins": ["U1.VDD"]},
                {"name": "GND", "power": True, "pins": ["U1.GND"]},
            ],
        }
        enriched, actions = enrich_blocks(spec, "BLE board with LiPo battery charging")
        block_actions = [a for a in actions if "lipo_charger" in a.get("rule", "")]
        assert len(block_actions) == 1

    def test_block_not_duplicated(self):
        spec = {
            "parts": [
                {"ref": "U1", "lib": "Battery_Management", "part": "MCP73831",
                 "value": "MCP73831", "footprint": "Package_TO_SOT_SMD:SOT-23-5"},
            ],
            "nets": [{"name": "GND", "power": True, "pins": ["U1.VSS"]}],
        }
        enriched, actions = enrich_blocks(spec, "Board with LiPo charging")
        assert len(actions) == 0

    def test_stemma_qt_block_not_duplicated_by_existing_i2c_connector(self):
        spec = {
            "parts": [
                {"ref": "U1", "lib": "Sensor", "part": "BME280",
                 "value": "BME280", "footprint": "Package_LGA:BME280"},
                {"ref": "J1", "lib": "Connector_Generic", "part": "Conn_01x04",
                 "value": "I2C connector",
                 "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"},
            ],
            "nets": [
                {"name": "GND", "power": True, "pins": ["U1.GND", "J1.1"]},
                {"name": "+3V3", "power": True, "pins": ["U1.VDD", "J1.2"]},
                {"name": "SDA", "pins": ["U1.SDA", "J1.3"]},
                {"name": "SCL", "pins": ["U1.SCL", "J1.4"]},
            ],
        }

        enriched, actions = enrich_blocks(spec, "BME280 breakout with Qwiic")

        assert not any(
            action["rule"] == "block:stemma_qt"
            for action in actions
        )
        assert [part["ref"] for part in enriched["parts"]].count("J1") == 1
        assert not any(
            str(part.get("value", "") or "").upper() == "STEMMA_QT"
            for part in enriched["parts"]
        )

    def test_usb_c_block_not_duplicated_by_existing_usb4105_connector(self):
        spec = {
            "parts": [
                {
                    "ref": "J_USB",
                    "lib": "",
                    "part": "USB-C receptacle",
                    "value": "",
                    "footprint": "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
                },
            ],
            "nets": [
                {"name": "VBUS", "power": True, "pins": ["J_USB.VBUS"]},
                {"name": "GND", "power": True, "pins": ["J_USB.GND"]},
                {"name": "CC1", "pins": ["J_USB.CC1"]},
                {"name": "CC2", "pins": ["J_USB.CC2"]},
            ],
        }

        enriched, actions = enrich_blocks(spec, "USB-C MIDI adapter")

        assert not any(action["rule"] == "block:usb_c_input" for action in actions)
        assert [part["ref"] for part in enriched["parts"]].count("J_USB") == 1
        assert not any(part["ref"] == "J100" for part in enriched["parts"])


# ---------------------------------------------------------------------------
# Design review exceptions
# ---------------------------------------------------------------------------

class TestDesignReviewExceptions:
    def test_missing_bulk_cap(self, ic_spec_dict):
        exceptions = design_review_exceptions(ic_spec_dict)
        bulk = [e for e in exceptions if e.code == ExcCode.DESIGN_MISSING_BULK_CAP]
        assert len(bulk) >= 1
        assert bulk[0].candidates[0].action == ActionType.ADD_PARTS

    def test_no_bulk_cap_when_present(self, ic_spec_dict):
        ic_spec_dict["parts"].append({
            "ref": "C10", "lib": "Device", "part": "C", "value": "10uF",
            "footprint": "Capacitor_SMD:C_0805_2012Metric",
        })
        ic_spec_dict["nets"][0]["pins"].append("C10.1")
        ic_spec_dict["nets"][1]["pins"].append("C10.2")
        exceptions = design_review_exceptions(ic_spec_dict)
        bulk = [e for e in exceptions if e.code == ExcCode.DESIGN_MISSING_BULK_CAP]
        assert len(bulk) == 0

    def test_bulk_cap_review_ignores_signal_nets_marked_power(self):
        spec = {
            "board": {"name": "signal-power-flags"},
            "parts": [
                {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115",
                 "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm"},
                {"ref": "J1", "lib": "Connector_Generic", "part": "Conn_01x08",
                 "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical"},
                {"ref": "C1", "lib": "Device", "part": "C", "value": "10uF",
                 "footprint": "Capacitor_SMD:C_0805_2012Metric"},
            ],
            "nets": [
                {"name": "3V3", "power": True, "pins": ["U1.VDD", "J1.1", "C1.1"]},
                {"name": "GND", "power": True, "pins": ["U1.GND", "J1.2", "C1.2"]},
                {"name": "SDA", "power": True, "pins": ["U1.SDA", "J1.3"]},
                {"name": "SCL", "power": True, "pins": ["U1.SCL", "J1.4"]},
                {"name": "ADDR", "power": True, "pins": ["U1.ADDR", "J1.5"]},
                {"name": "ALERT", "power": True, "pins": ["U1.ALERT", "J1.6"]},
                {"name": "AIN0", "power": True, "pins": ["U1.AIN0", "J1.7"]},
            ],
        }

        exceptions = design_review_exceptions(spec)
        bulk_nets = {
            e.subject.get("net")
            for e in exceptions
            if e.code == ExcCode.DESIGN_MISSING_BULK_CAP
        }

        assert not (bulk_nets & {"SDA", "SCL", "ADDR", "ALERT", "AIN0"})
        assert "3V3" not in bulk_nets

    def test_named_supply_without_ic_power_pin_does_not_trigger_ic_bulk_review(self):
        spec = {
            "board": {"name": "pullup-only-supply"},
            "parts": [
                {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115",
                 "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm"},
                {"ref": "R1", "lib": "Device", "part": "R", "value": "4.7K",
                 "footprint": "Resistor_SMD:R_0603_1608Metric"},
                {"ref": "R2", "lib": "Device", "part": "R", "value": "4.7K",
                 "footprint": "Resistor_SMD:R_0603_1608Metric"},
            ],
            "nets": [
                {"name": "3V3", "power": True, "pins": ["R1.1", "R2.1"]},
                {"name": "GND", "power": True, "pins": ["U1.GND"]},
                {"name": "SDA", "pins": ["U1.SDA", "R1.2"]},
                {"name": "SCL", "pins": ["U1.SCL", "R2.2"]},
            ],
        }

        exceptions = design_review_exceptions(spec)
        bulk_nets = {
            e.subject.get("net")
            for e in exceptions
            if e.code == ExcCode.DESIGN_MISSING_BULK_CAP
        }

        assert "3V3" not in bulk_nets

    def test_enrichment_does_not_treat_signal_power_flags_as_supply_rails(self):
        spec = {
            "board": {"name": "signal-power-flags-enrich"},
            "parts": [
                {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115",
                 "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm"},
            ],
            "nets": [
                {"name": "3V3", "power": True, "pins": ["U1.VDD"]},
                {"name": "GND", "power": True, "pins": ["U1.GND"]},
                {"name": "SDA", "power": True, "pins": ["U1.SDA"]},
                {"name": "SCL", "power": True, "pins": ["U1.SCL"]},
                {"name": "ADDR", "power": True, "pins": ["U1.ADDR"]},
                {"name": "ALERT", "power": True, "pins": ["U1.ALERT"]},
                {"name": "AIN0", "power": True, "pins": ["U1.AIN0"]},
            ],
        }

        enriched, actions = enrich(spec)
        cap_refs = {p["ref"] for p in enriched["parts"] if p["ref"].startswith("C")}
        signal_cap_nets = {
            n["name"]
            for n in enriched["nets"]
            if n["name"] in {"SDA", "SCL", "ADDR", "ALERT", "AIN0"}
            and any(pin.split(".", 1)[0] in cap_refs for pin in n.get("pins", []))
        }

        assert signal_cap_nets == set()
        assert any(action.get("rule") == "A1" for action in actions)

    def test_enrichment_accepts_nonstandard_net_with_power_pin_evidence(self):
        spec = {
            "board": {"name": "power-pin-evidence"},
            "parts": [
                {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115",
                 "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm"},
            ],
            "nets": [
                {"name": "SENSOR_SUPPLY", "pins": ["U1.VDD"]},
                {"name": "RETURN", "pins": ["U1.GND"]},
            ],
        }

        enriched, actions = enrich(spec)
        cap_refs = {p["ref"] for p in enriched["parts"] if p["ref"].startswith("C")}
        supply = next(n for n in enriched["nets"] if n["name"] == "SENSOR_SUPPLY")
        ground = next(n for n in enriched["nets"] if n["name"] == "RETURN")

        assert any(pin.split(".", 1)[0] in cap_refs for pin in supply["pins"])
        assert any(pin.split(".", 1)[0] in cap_refs for pin in ground["pins"])
        assert any(action.get("rule") == "A1" for action in actions)

    def test_bulk_review_accepts_nonstandard_net_with_power_pin_evidence(self):
        spec = {
            "board": {"name": "bulk-power-pin-evidence"},
            "parts": [
                {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115",
                 "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm"},
            ],
            "nets": [
                {"name": "SENSOR_SUPPLY", "pins": ["U1.VDD"]},
                {"name": "RETURN", "pins": ["U1.GND"]},
            ],
        }

        exceptions = design_review_exceptions(spec)
        bulk_nets = {
            e.subject.get("net")
            for e in exceptions
            if e.code == ExcCode.DESIGN_MISSING_BULK_CAP
        }

        assert "SENSOR_SUPPLY" in bulk_nets

    def test_negative_voltage_rail_is_power_for_review(self):
        spec = {
            "board": {"name": "eurorack-negative-rail"},
            "parts": [
                {"ref": "U1", "lib": "Amplifier_Operational", "part": "TL072",
                 "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm"},
            ],
            "nets": [
                {"name": "+12V", "power": True, "pins": ["U1.V+"]},
                {"name": "-12V", "power": True, "pins": ["U1.V-"]},
                {"name": "GND", "power": True, "pins": ["U1.GND"]},
            ],
        }

        exceptions = design_review_exceptions(spec)
        bulk_nets = {
            e.subject.get("net")
            for e in exceptions
            if e.code == ExcCode.DESIGN_MISSING_BULK_CAP
        }

        assert "+12V" in bulk_nets
        assert "-12V" in bulk_nets

    def test_no_connector_error(self):
        spec = {
            "parts": [{"ref": "U1", "lib": "Device", "part": "R", "value": "10K",
                        "footprint": "Resistor_SMD:R_0603_1608Metric"}],
            "nets": [
                {"name": "VCC", "power": True, "pins": ["U1.1"]},
                {"name": "GND", "power": True, "pins": ["U1.2"]},
            ],
        }
        exceptions = design_review_exceptions(spec)
        no_conn = [e for e in exceptions if e.code == ExcCode.DESIGN_NO_CONNECTOR]
        assert len(no_conn) == 1
        assert no_conn[0].severity == Severity.ERROR

    def test_onboard_usb_dev_module_counts_as_user_connection(self):
        spec = {
            "parts": [
                {
                    "ref": "A1",
                    "lib": "",
                    "part": "Raspberry Pi Pico module",
                    "footprint": "Module:RaspberryPi_Pico_Common_THT",
                }
            ],
            "nets": [
                {"name": "VBUS", "power": True, "pins": ["A1.VBUS"]},
                {"name": "GND", "power": True, "pins": ["A1.GND"]},
            ],
        }

        exceptions = design_review_exceptions(spec)

        assert not any(e.code == ExcCode.DESIGN_NO_CONNECTOR for e in exceptions)

    def test_no_power_rail_error(self):
        spec = {
            "parts": [{"ref": "J1", "lib": "Connector_Generic", "part": "Conn_01x02",
                        "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"}],
            "nets": [{"name": "SIG", "pins": ["J1.1"]}],
        }
        exceptions = design_review_exceptions(spec)
        no_pwr = [e for e in exceptions if e.code == ExcCode.DESIGN_NO_POWER_RAIL]
        assert len(no_pwr) >= 1

    def test_power_flag_advisory(self):
        spec = {
            "parts": [{"ref": "J1", "lib": "Connector_Generic", "part": "Conn_01x02",
                        "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical"}],
            "nets": [
                {"name": "VCC", "pins": ["J1.1"]},
                {"name": "GND", "power": True, "pins": ["J1.2"]},
            ],
        }
        exceptions = design_review_exceptions(spec)
        flags = [e for e in exceptions if e.code == ExcCode.DESIGN_POWER_FLAG]
        assert len(flags) == 1
        assert flags[0].severity == Severity.ADVISORY

    def test_marketing_cross_ref(self, ic_spec_dict):
        marketing = "I2C sensor with STEMMA QT and USB-C"
        exceptions = design_review_exceptions(ic_spec_dict, marketing_text=marketing)
        features = [e for e in exceptions if e.code == ExcCode.DESIGN_MISSING_FEATURE]
        feature_names = [e.subject.get("feature") for e in features]
        assert "USB-C connector" in feature_names
        assert "STEMMA QT/Qwiic connector" in feature_names

    def test_marketing_no_false_positive(self, ic_spec_dict):
        exceptions = design_review_exceptions(ic_spec_dict, marketing_text="temperature sensor breakout")
        features = [e for e in exceptions if e.code == ExcCode.DESIGN_MISSING_FEATURE]
        assert len(features) == 0

    def test_clean_spec_no_errors(self, ic_spec_dict):
        ic_spec_dict["parts"].append({
            "ref": "C10", "lib": "Device", "part": "C", "value": "10uF",
            "footprint": "Capacitor_SMD:C_0805_2012Metric",
        })
        ic_spec_dict["nets"][0]["pins"].append("C10.1")
        ic_spec_dict["nets"][1]["pins"].append("C10.2")
        exceptions = design_review_exceptions(ic_spec_dict)
        errors = [e for e in exceptions if e.severity in (Severity.FATAL, Severity.ERROR)]
        assert len(errors) == 0


# ---------------------------------------------------------------------------
# Corrections: action handlers
# ---------------------------------------------------------------------------

class TestCorrectionHandlers:
    def _make_exc(self, code, action, params, severity=Severity.ERROR):
        return DesignException(
            id="e1", code=code, severity=severity,
            message="test",
            candidates=[Candidate(
                id="c1", action=action, params=params,
                human_summary="test fix",
            )],
        )

    def test_add_parts(self, ic_circuit_spec):
        exc = self._make_exc(
            ExcCode.DESIGN_MISSING_BULK_CAP,
            ActionType.ADD_PARTS,
            {
                "parts": [{"ref": "C99", "lib": "Device", "part": "C",
                           "value": "10uF", "footprint": "Capacitor_SMD:C_0805_2012Metric"}],
                "net_connections": [
                    {"net": "VCC", "pin": "C99.1"},
                    {"net": "GND", "pin": "C99.2"},
                ],
            },
        )
        new = apply_candidate(ic_circuit_spec, exc, exc.candidates[0])
        assert new.part_by_ref("C99") is not None
        vcc = next(n for n in new.nets if n.name == "VCC")
        assert "C99.1" in vcc.pins

    def test_add_parts_creates_new_net(self, ic_circuit_spec):
        exc = self._make_exc(
            ExcCode.DESIGN_MISSING_BULK_CAP,
            ActionType.ADD_PARTS,
            {
                "parts": [{"ref": "R99", "lib": "Device", "part": "R",
                           "value": "10K", "footprint": "Resistor_SMD:R_0603_1608Metric"}],
                "net_connections": [
                    {"net": "NEW_NET", "pin": "R99.1"},
                    {"net": "GND", "pin": "R99.2"},
                ],
            },
        )
        new = apply_candidate(ic_circuit_spec, exc, exc.candidates[0])
        new_net = next((n for n in new.nets if n.name == "NEW_NET"), None)
        assert new_net is not None
        assert "R99.1" in new_net.pins

    def test_add_parts_duplicate_ref_raises(self, ic_circuit_spec):
        exc = self._make_exc(
            ExcCode.DESIGN_MISSING_BULK_CAP,
            ActionType.ADD_PARTS,
            {
                "parts": [{"ref": "U1", "lib": "Device", "part": "R",
                           "value": "10K", "footprint": "Resistor_SMD:R_0603_1608Metric"}],
                "net_connections": [],
            },
        )
        with pytest.raises(CorrectionError, match="already exists"):
            apply_candidate(ic_circuit_spec, exc, exc.candidates[0])

    def test_set_layers(self, ic_circuit_spec):
        exc = self._make_exc(
            ExcCode.ROUTE_TIMEOUT,
            ActionType.SET_LAYERS,
            {"layers": 4},
        )
        new = apply_candidate(ic_circuit_spec, exc, exc.candidates[0])
        assert new.board.layers == 4

    def test_accept_advisory(self, ic_circuit_spec):
        exc = self._make_exc(
            ExcCode.DESIGN_MISSING_BULK_CAP,
            ActionType.ACCEPT_ADVISORY,
            {},
            severity=Severity.ADVISORY,
        )
        new = apply_candidate(ic_circuit_spec, exc, exc.candidates[0])
        assert exc.waiver_key() in new.waivers

    def test_scale_outline(self):
        spec = CircuitSpec.model_validate({
            "board": {"name": "test", "outline_hint_mm": [40, 30]},
            "parts": [{"ref": "R1", "lib": "Device", "part": "R",
                        "value": "10K", "footprint": "Resistor_SMD:R_0603_1608Metric"}],
            "nets": [{"name": "GND", "power": True, "pins": ["R1.1"]}],
        })
        exc = self._make_exc(
            ExcCode.LAYOUT_OVERLAP,
            ActionType.SCALE_OUTLINE,
            {"area_factor": 1.5},
        )
        new = apply_candidate(spec, exc, exc.candidates[0])
        assert new.board.outline_hint_mm is not None
        assert new.board.outline_hint_mm[0] > 40
        assert new.board.outline_hint_mm[1] > 30

    def test_regenerate_noop(self, ic_circuit_spec):
        exc = self._make_exc(
            ExcCode.ENGINE_CRASH,
            ActionType.REGENERATE,
            {},
        )
        new = apply_candidate(ic_circuit_spec, exc, exc.candidates[0])
        assert len(new.parts) == len(ic_circuit_spec.parts)


# ---------------------------------------------------------------------------
# Exceptions: model and waiver keys
# ---------------------------------------------------------------------------

class TestExceptionModel:
    def test_waiver_key_with_ref(self):
        exc = DesignException(
            id="e1", code=ExcCode.LAYOUT_OVERLAP, severity=Severity.ERROR,
            message="test", subject={"ref": "U1"},
        )
        assert exc.waiver_key() == "LAYOUT_OVERLAP:U1"

    def test_waiver_key_with_net(self):
        exc = DesignException(
            id="e1", code=ExcCode.DESIGN_MISSING_BULK_CAP, severity=Severity.ADVISORY,
            message="test", subject={"net": "VCC"},
        )
        assert exc.waiver_key() == "DESIGN_MISSING_BULK_CAP:VCC"

    def test_waiver_key_empty_subject(self):
        exc = DesignException(
            id="e1", code=ExcCode.DESIGN_NO_CONNECTOR, severity=Severity.ERROR,
            message="test",
        )
        assert exc.waiver_key() == "DESIGN_NO_CONNECTOR:"


# ---------------------------------------------------------------------------
# Policy: auto-correction
# ---------------------------------------------------------------------------

class TestPolicy:
    def test_safe_policy_accepts_advisory(self):
        from mcp_server.policy import auto_corrections, normalize_policy

        policy = normalize_policy({"auto_apply": "safe", "max_internal_corrections": 4})
        exceptions = [DesignException(
            id="e1", code=ExcCode.DESIGN_MISSING_BULK_CAP, severity=Severity.ADVISORY,
            message="test", candidates=[
                Candidate(id="c1", action=ActionType.ACCEPT_ADVISORY, params={},
                          human_summary="waive"),
            ],
        )]
        choices = auto_corrections(exceptions, policy)
        assert len(choices) == 1
        assert choices[0]["candidate_id"] == "c1"

    def test_none_policy_rejects_everything(self):
        from mcp_server.policy import auto_corrections, normalize_policy

        policy = normalize_policy({"auto_apply": "none"})
        exceptions = [DesignException(
            id="e1", code=ExcCode.HIGH_CONGESTION, severity=Severity.ADVISORY,
            message="test", candidates=[
                Candidate(id="c1", action=ActionType.ACCEPT_ADVISORY, params={},
                          human_summary="waive"),
            ],
        )]
        choices = auto_corrections(exceptions, policy)
        assert len(choices) == 0

    def test_safe_policy_auto_applies_high_confidence_add_parts(self):
        from mcp_server.policy import auto_corrections, normalize_policy

        policy = normalize_policy({"auto_apply": "safe", "max_internal_corrections": 4})
        exceptions = [DesignException(
            id="e1", code=ExcCode.DESIGN_MISSING_BULK_CAP, severity=Severity.ADVISORY,
            message="test", candidates=[
                Candidate(id="c1", action=ActionType.ADD_PARTS,
                          params={"parts": [], "net_connections": []},
                          human_summary="add bulk cap", confidence=0.9),
            ],
        )]
        choices = auto_corrections(exceptions, policy)
        assert len(choices) == 1

    def test_safe_policy_rejects_low_confidence_add_parts(self):
        from mcp_server.policy import auto_corrections, normalize_policy

        policy = normalize_policy({"auto_apply": "safe", "max_internal_corrections": 4})
        exceptions = [DesignException(
            id="e1", code=ExcCode.DESIGN_MISSING_BULK_CAP, severity=Severity.ADVISORY,
            message="test", candidates=[
                Candidate(id="c1", action=ActionType.ADD_PARTS,
                          params={"parts": [], "net_connections": []},
                          human_summary="add bulk cap", confidence=0.5),
            ],
        )]
        choices = auto_corrections(exceptions, policy)
        assert len(choices) == 0

    def test_decision_kind_mechanical(self):
        from mcp_server.policy import decision_kind

        exceptions = [DesignException(
            id="e1", code=ExcCode.ROUTE_TIMEOUT, severity=Severity.ERROR,
            message="test", candidates=[
                Candidate(id="c1", action=ActionType.SET_LAYERS,
                          params={"layers": 4}, human_summary="4 layers"),
            ],
        )]
        assert decision_kind(exceptions) == "mechanical_constraint"

    def test_decision_kind_quality_advisory(self):
        from mcp_server.policy import decision_kind

        exceptions = [DesignException(
            id="e1", code=ExcCode.DRC_COURTYARD, severity=Severity.ADVISORY,
            message="test", candidates=[
                Candidate(id="c1", action=ActionType.ACCEPT_ADVISORY,
                          params={}, human_summary="accept"),
            ],
        )]
        assert decision_kind(exceptions) == "quality_advisory"


# ---------------------------------------------------------------------------
# Engine worker: routing/DRC fallbacks
# ---------------------------------------------------------------------------

class TestEngineWorkerFallbacks:
    def test_route_pcb_no_jar(self):
        from mcp_server.engine_worker import _route_pcb
        exceptions = _route_pcb("/tmp/nonexistent.kicad_pcb")
        assert len(exceptions) == 1
        assert exceptions[0].code == ExcCode.ROUTE_UNAVAILABLE
        assert exceptions[0].severity == Severity.ERROR

    def test_run_drc_no_file(self):
        from mcp_server.engine_worker import _run_drc
        exceptions = _run_drc("/tmp/nonexistent.kicad_pcb")
        assert len(exceptions) == 1
        assert exceptions[0].code == ExcCode.DRC_TOOL_FAILURE

    def test_drc_to_exceptions_clean(self):
        from mcp_server.engine_worker import _drc_to_exceptions
        report = {"violations": [], "unconnected_items": []}
        assert _drc_to_exceptions(report) == []

    def test_drc_to_exceptions_with_violations(self):
        from mcp_server.engine_worker import _drc_to_exceptions
        report = {
            "violations": [
                {"type": "clearance_violation", "description": "test"},
                {"type": "courtyard_overlap", "description": "test"},
            ],
            "unconnected_items": [
                {"items": [{"description": "Pad [VCC] test"}]},
            ],
        }
        exceptions = _drc_to_exceptions(report)
        codes = {e.code for e in exceptions}
        assert ExcCode.DRC_CLEARANCE in codes
        assert ExcCode.DRC_COURTYARD in codes
        assert ExcCode.DRC_UNCONNECTED in codes


# ---------------------------------------------------------------------------
# Exception suppression
# ---------------------------------------------------------------------------

class TestExceptionSuppression:
    def test_waived_advisory_suppressed(self, ic_circuit_spec):
        from mcp_server.exception_mapper import suppress_waived

        exc = DesignException(
            id="e1", code=ExcCode.DESIGN_MISSING_BULK_CAP, severity=Severity.ADVISORY,
            message="test", subject={"net": "VCC"},
        )
        spec_with_waiver = ic_circuit_spec.model_copy(deep=True)
        spec_with_waiver.waivers.append(exc.waiver_key())
        result = suppress_waived([exc], spec_with_waiver)
        assert len(result) == 0

    def test_error_not_suppressed(self, ic_circuit_spec):
        from mcp_server.exception_mapper import suppress_waived

        exc = DesignException(
            id="e1", code=ExcCode.DESIGN_NO_CONNECTOR, severity=Severity.ERROR,
            message="test",
        )
        spec_with_waiver = ic_circuit_spec.model_copy(deep=True)
        spec_with_waiver.waivers.append(exc.waiver_key())
        result = suppress_waived([exc], spec_with_waiver)
        assert len(result) == 1
