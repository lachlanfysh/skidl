"""Synthetic tests for structured simulation intent contract.

Tests the apply_simulation_intent() API — transactional validation,
strict mode, provenance/confidence tracking, and rejection of
malformed/ambiguous inputs.
"""
from __future__ import annotations

import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


def _make_circuit(parts=None, sim_harness=None):
    class MockNet:
        def __init__(self, name):
            self.name = name

    class MockCircuit:
        def __init__(self, parts, sim_harness):
            self.parts = parts or []
            self.sim_harness = sim_harness

        def get_nets(self):
            return []

    return MockCircuit(parts or [], sim_harness)


# ---------------------------------------------------------------------------
# Basic valid intent
# ---------------------------------------------------------------------------
class TestBasicIntent:
    def test_minimal_source(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "user:battery-spec", "confidence": 1.0},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert report.sources_added == 1
        assert ckt.sim_harness is not None
        assert len(ckt.sim_harness.sources) == 1
        assert ckt.sim_harness.sources[0].net_name == "VBAT"
        assert ckt.sim_harness.sources[0].voltage == 4.5
        assert "user:battery-spec" in ckt.sim_harness.sources[0].provenance

    def test_full_intent(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "datasheet", "confidence": 0.9},
            ],
            "loads": [
                {"net": "VCC", "resistance": 1000.0,
                 "provenance": "estimated", "confidence": 0.7},
            ],
            "probes": [
                {"net": "VCC", "provenance": "user", "confidence": 1.0},
            ],
            "rail_assertions": [
                {"net": "VBAT", "nominal": 4.5,
                 "provenance": "user", "confidence": 1.0},
            ],
            "ratio_assertions": [
                {"output_net": "VOUT", "input_net": "VBAT", "ratio": 0.73,
                 "provenance": "computed", "confidence": 0.8},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert report.sources_added == 1
        assert report.loads_added == 1
        assert report.probes_added == 1
        assert report.rail_assertions_added == 1
        assert report.ratio_assertions_added == 1


# ---------------------------------------------------------------------------
# Version validation
# ---------------------------------------------------------------------------
class TestVersionValidation:
    def test_missing_version(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {"sources": [{"net": "VCC", "voltage": 3.3}]}

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        errors = [f for f in report.findings if f.severity == "error"]
        assert any("version" in f.message for f in errors)

    def test_wrong_version(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {"version": 99, "sources": [{"net": "VCC", "voltage": 3.3}]}

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "version_mismatch" for f in report.findings)


# ---------------------------------------------------------------------------
# Strict mode — unknown keys
# ---------------------------------------------------------------------------
class TestStrictMode:
    def test_unknown_key_strict_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [{"net": "VCC", "voltage": 3.3,
                          "provenance": "test", "confidence": 1.0}],
            "spice_models": [{"ref": "U1", "model": "LM7805"}],  # unknown
        }

        report = apply_simulation_intent(intent, circuit=ckt, strict=True)

        assert not report.applied
        assert any(f.category == "unknown_key" for f in report.findings)

    def test_unknown_key_non_strict_warns(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [{"net": "VCC", "voltage": 3.3,
                          "provenance": "test", "confidence": 1.0}],
            "spice_models": [{"ref": "U1", "model": "LM7805"}],
        }

        report = apply_simulation_intent(intent, circuit=ckt, strict=False)

        assert report.applied
        warnings = [f for f in report.findings if f.category == "unknown_key"]
        assert len(warnings) >= 1

    def test_unknown_source_key_strict(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [{"net": "VCC", "voltage": 3.3,
                          "provenance": "test", "confidence": 1.0,
                          "spice_model": "ideal"}],
        }

        report = apply_simulation_intent(intent, circuit=ckt, strict=True)

        assert not report.applied


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------
class TestMissingFields:
    def test_source_missing_voltage(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [{"net": "VCC", "provenance": "test"}],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "missing_field" for f in report.findings)

    def test_source_missing_net(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [{"voltage": 3.3, "provenance": "test"}],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_load_missing_both_r_and_i(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "loads": [{"net": "VCC", "provenance": "test", "confidence": 1.0}],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_rail_assertion_missing_nominal(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "rail_assertions": [{"net": "VCC", "provenance": "test"}],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_ratio_assertion_missing_ratio(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "ratio_assertions": [
                {"output_net": "VOUT", "input_net": "VIN", "provenance": "test"},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied


# ---------------------------------------------------------------------------
# Provenance and confidence
# ---------------------------------------------------------------------------
class TestProvenanceAndConfidence:
    def test_missing_provenance_warns(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [{"net": "VCC", "voltage": 3.3}],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        prov_warns = [f for f in report.findings
                      if f.category == "missing_provenance"]
        assert len(prov_warns) >= 1

    def test_low_confidence_tracked(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "guessed-from-battery-value", "confidence": 0.3},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert len(report.low_confidence_items) == 1
        assert "0.3" in report.low_confidence_items[0]
        low_conf = [f for f in report.findings if f.category == "low_confidence"]
        assert len(low_conf) == 1

    def test_high_confidence_not_flagged(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "user:schematic-annotation", "confidence": 0.9},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert len(report.low_confidence_items) == 0

    def test_provenance_in_harness(self):
        """Provenance and confidence appear in the harness declaration."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VCC", "voltage": 3.3,
                 "provenance": "agent:datasheet-parse", "confidence": 0.8},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        src = ckt.sim_harness.sources[0]
        assert "agent:datasheet-parse" in src.provenance
        assert "confidence=0.8" in src.provenance


# ---------------------------------------------------------------------------
# Transactional — no partial apply
# ---------------------------------------------------------------------------
class TestTransactional:
    def test_error_in_second_item_rolls_back(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "user", "confidence": 1.0},
                {"net": "VCC", "voltage": "not_a_number",
                 "provenance": "user", "confidence": 1.0},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        # Harness should not have been modified
        assert not hasattr(ckt, "sim_harness") or ckt.sim_harness is None


# ---------------------------------------------------------------------------
# Type errors
# ---------------------------------------------------------------------------
class TestTypeErrors:
    def test_intent_not_dict(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        report = apply_simulation_intent("not a dict", circuit=ckt)

        assert not report.applied
        assert any(f.category == "type_error" for f in report.findings)

    def test_sources_not_list(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": {"net": "VCC", "voltage": 3.3},  # dict, not list
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_confidence_not_number(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VCC", "voltage": 3.3,
                 "provenance": "test", "confidence": "high"},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied


# ---------------------------------------------------------------------------
# "3xAAA and 3.3V MCU" — structured intent from agent
# ---------------------------------------------------------------------------
class TestAgentDerivedIntent:
    def test_battery_mcu_intent(self):
        """Agent produces intent for 3xAAA battery + 3.3V MCU with LDO.
        Low confidence on the load estimate."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5,
                 "provenance": "agent:battery-spec-3xAAA-nominal",
                 "confidence": 0.85},
            ],
            "loads": [
                {"net": "VCC", "resistance": 330.0,
                 "provenance": "agent:estimated-mcu-draw-10mA-at-3.3V",
                 "confidence": 0.3},
            ],
            "rail_assertions": [
                {"net": "VBAT", "nominal": 4.5,
                 "provenance": "agent:battery-spec", "confidence": 0.85},
                {"net": "VCC", "nominal": 3.3,
                 "provenance": "agent:ldo-output-typical",
                 "confidence": 0.6},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert report.sources_added == 1
        assert report.loads_added == 1
        assert report.rail_assertions_added == 2

        # Low-confidence load should be flagged
        assert len(report.low_confidence_items) == 1
        assert "loads[0]" in report.low_confidence_items[0]

        # Low confidence items appear in findings
        low = [f for f in report.findings if f.category == "low_confidence"]
        assert len(low) == 1

    def test_malformed_agent_intent_clear_error(self):
        """Malformed intent from agent gives clear errors, not crashes."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VBAT", "voltage": 4.5, "provenance": "agent"},
            ],
            "loads": [
                {"net": "VCC"},  # missing resistance AND current
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        errors = [f for f in report.findings if f.severity == "error"]
        assert len(errors) >= 1


# ---------------------------------------------------------------------------
# Report summary
# ---------------------------------------------------------------------------
class TestReportSummary:
    def test_summary_format(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "VCC", "voltage": 3.3,
                 "provenance": "user", "confidence": 1.0},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        summary = report.summary()
        assert "applied: True" in summary
        assert "1 sources" in summary


# ---------------------------------------------------------------------------
# Custom rail names only recognized through intent
# ---------------------------------------------------------------------------
class TestCustomRailNames:
    def test_nonstandard_rail_name(self):
        """A rail name not in POWER_NET_RE can still be used via intent."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [
                {"net": "CUSTOM_POWER_RAIL", "voltage": 12.0,
                 "provenance": "user:custom", "confidence": 1.0},
            ],
            "rail_assertions": [
                {"net": "CUSTOM_POWER_RAIL", "nominal": 12.0,
                 "provenance": "user", "confidence": 1.0},
            ],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert ckt.sim_harness.sources[0].net_name == "CUSTOM_POWER_RAIL"
        assert ckt.sim_harness.rail_assertions[0].net_name == "CUSTOM_POWER_RAIL"


# ---------------------------------------------------------------------------
# Empty intent is valid
# ---------------------------------------------------------------------------
class TestEmptyIntent:
    def test_empty_sections(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {"version": 1}

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert report.sources_added == 0

    def test_empty_lists(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = {
            "version": 1,
            "sources": [],
            "loads": [],
            "probes": [],
            "rail_assertions": [],
            "ratio_assertions": [],
        }

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
