"""Synthetic tests for structured simulation intent contract.

Tests the apply_simulation_intent() API — transactional validation,
strict mode, provenance/confidence tracking, and rejection of
malformed/ambiguous inputs.
"""
from __future__ import annotations

import math
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


def _src(net="VCC", voltage=3.3, prov="user:test", conf=1.0, **kw):
    return {"net": net, "voltage": voltage,
            "provenance": prov, "confidence": conf, **kw}


def _load(net="VCC", resistance=1000.0, prov="user:test", conf=1.0, **kw):
    return {"net": net, "resistance": resistance,
            "provenance": prov, "confidence": conf, **kw}


def _probe(net="VCC", prov="user:test", conf=1.0, **kw):
    return {"net": net, "provenance": prov, "confidence": conf, **kw}


def _rail(net="VCC", nominal=3.3, prov="user:test", conf=1.0, **kw):
    return {"net": net, "nominal": nominal,
            "provenance": prov, "confidence": conf, **kw}


def _ratio(out="VOUT", inp="VIN", ratio=0.5, prov="user:test", conf=1.0, **kw):
    return {"output_net": out, "input_net": inp, "ratio": ratio,
            "provenance": prov, "confidence": conf, **kw}


def _intent(**sections):
    d = {"version": 1}
    d.update(sections)
    return d


# ---------------------------------------------------------------------------
# Basic valid intent
# ---------------------------------------------------------------------------
class TestBasicIntent:
    def test_minimal_source(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        report = apply_simulation_intent(
            _intent(sources=[_src("VBAT", 4.5, "user:battery-spec")]),
            circuit=ckt,
        )

        assert report.applied
        assert report.sources_added == 1
        assert ckt.sim_harness.sources[0].net_name == "VBAT"
        assert ckt.sim_harness.sources[0].voltage == 4.5
        assert "user:battery-spec" in ckt.sim_harness.sources[0].provenance

    def test_full_intent(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(
            sources=[_src("VBAT", 4.5, "datasheet", 0.9)],
            loads=[_load("VCC", 1000.0, "estimated", 0.7)],
            probes=[_probe("VCC")],
            rail_assertions=[_rail("VBAT", 4.5)],
            ratio_assertions=[_ratio("VOUT", "VBAT", 0.73, "computed", 0.8)],
        )

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
        report = apply_simulation_intent(
            {"sources": [_src()]}, circuit=ckt,
        )

        assert not report.applied
        assert any("version" in f.message for f in report.findings
                    if f.severity == "error")

    def test_wrong_version(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        report = apply_simulation_intent(
            {"version": 99, "sources": [_src()]}, circuit=ckt,
        )

        assert not report.applied
        assert any(f.category == "version_mismatch" for f in report.findings)


# ---------------------------------------------------------------------------
# Strict mode — unknown keys
# ---------------------------------------------------------------------------
class TestStrictMode:
    def test_unknown_top_key_strict_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(
            sources=[_src()],
            spice_models=[{"ref": "U1", "model": "LM7805"}],
        )

        report = apply_simulation_intent(intent, circuit=ckt, strict=True)

        assert not report.applied
        assert any(f.category == "unknown_key" for f in report.findings)

    def test_unknown_top_key_non_strict_warns(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(
            sources=[_src()],
            spice_models=[{"ref": "U1", "model": "LM7805"}],
        )

        report = apply_simulation_intent(intent, circuit=ckt, strict=False)

        assert report.applied
        warnings = [f for f in report.findings if f.category == "unknown_key"]
        assert len(warnings) >= 1

    def test_unknown_source_key_strict(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        src = _src()
        src["spice_model"] = "ideal"
        intent = _intent(sources=[src])

        report = apply_simulation_intent(intent, circuit=ckt, strict=True)

        assert not report.applied


# ---------------------------------------------------------------------------
# Strict provenance/confidence enforcement
# ---------------------------------------------------------------------------
class TestStrictProvenanceConfidence:
    def test_missing_provenance_strict_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3, "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt, strict=True)

        assert not report.applied
        assert any(f.category == "missing_provenance" for f in report.findings)

    def test_empty_provenance_strict_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3, "provenance": "", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt, strict=True)

        assert not report.applied

    def test_missing_confidence_strict_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3, "provenance": "test"},
        ])

        report = apply_simulation_intent(intent, circuit=ckt, strict=True)

        assert not report.applied
        assert any(f.category == "missing_confidence" for f in report.findings)

    def test_missing_provenance_non_strict_warns(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3},
        ])

        report = apply_simulation_intent(intent, circuit=ckt, strict=False)

        assert report.applied
        prov_warns = [f for f in report.findings
                      if f.category == "missing_provenance"]
        assert len(prov_warns) >= 1

    def test_missing_confidence_non_strict_defaults(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3, "provenance": "test"},
        ])

        report = apply_simulation_intent(intent, circuit=ckt, strict=False)

        assert report.applied
        assert ckt.sim_harness.sources[0].confidence == 1.0


# ---------------------------------------------------------------------------
# Missing required fields
# ---------------------------------------------------------------------------
class TestMissingFields:
    def test_source_missing_voltage(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "missing_field" for f in report.findings)

    def test_source_missing_net(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"voltage": 3.3, "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_load_missing_both_r_and_i(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "VCC", "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_rail_assertion_missing_nominal(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(rail_assertions=[
            {"net": "VCC", "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_ratio_assertion_missing_ratio(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(ratio_assertions=[
            {"output_net": "VOUT", "input_net": "VIN",
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied


# ---------------------------------------------------------------------------
# Confidence tracking
# ---------------------------------------------------------------------------
class TestConfidenceTracking:
    def test_low_confidence_tracked(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            _src("VBAT", 4.5, "guessed-from-battery-value", 0.3),
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert len(report.low_confidence_items) == 1
        assert "0.3" in report.low_confidence_items[0]

    def test_high_confidence_not_flagged(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            _src("VBAT", 4.5, "user:annotation", 0.9),
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert len(report.low_confidence_items) == 0

    def test_provenance_in_harness(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            _src("VCC", 3.3, "agent:datasheet-parse", 0.8),
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        src = ckt.sim_harness.sources[0]
        assert src.provenance == "agent:datasheet-parse"
        assert src.confidence == 0.8


# ---------------------------------------------------------------------------
# Transactional — no partial apply
# ---------------------------------------------------------------------------
class TestTransactional:
    def test_error_in_second_item_rolls_back(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            _src("VBAT", 4.5),
            {"net": "VCC", "voltage": "not_a_number",
             "provenance": "user", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
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
        intent = {"version": 1, "sources": {"net": "VCC", "voltage": 3.3}}

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_confidence_string_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": "high"},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_confidence_bool_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": True},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_confidence_nan_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": float("nan")},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_confidence_above_one_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": 2.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "invalid_value" for f in report.findings)

    def test_confidence_negative_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": -0.5},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "invalid_value" for f in report.findings)

    def test_confidence_above_one_non_strict_also_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": 1.5},
        ])

        report = apply_simulation_intent(intent, circuit=ckt, strict=False)

        assert not report.applied

    def test_confidence_zero_passes(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": 0.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied

    def test_confidence_one_passes(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": 3.3,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied


# ---------------------------------------------------------------------------
# Empty net names
# ---------------------------------------------------------------------------
class TestEmptyNetNames:
    def test_empty_source_net_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "", "voltage": 3.3,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "invalid_value" for f in report.findings)

    def test_whitespace_net_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "   ", "voltage": 3.3,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_empty_load_net_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "", "resistance": 100.0,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_empty_ratio_output_net_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(ratio_assertions=[
            {"output_net": "", "input_net": "VIN", "ratio": 0.5,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied


# ---------------------------------------------------------------------------
# ADVERSARIAL: numeric field validation
# ---------------------------------------------------------------------------
class TestNumericValidation:
    def test_string_voltage_rejects(self):
        """voltage: "4.5V" should fail, not pass as truthy."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VBAT", "voltage": "4.5V",
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "type_error" for f in report.findings)

    def test_string_resistance_rejects(self):
        """resistance: "100 ohms" should fail."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "VCC", "resistance": "100 ohms",
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_string_current_rejects(self):
        """current: "10mA" should fail."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "VCC", "current": "10mA",
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_negative_resistance_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "VCC", "resistance": -100.0,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "invalid_value" for f in report.findings)

    def test_zero_resistance_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "VCC", "resistance": 0.0,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_inf_voltage_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": float("inf"),
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_nan_voltage_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": float("nan"),
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_bool_voltage_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(sources=[
            {"net": "VCC", "voltage": True,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied


# ---------------------------------------------------------------------------
# ADVERSARIAL: tolerance validation
# ---------------------------------------------------------------------------
class TestToleranceValidation:
    def test_string_tolerance_rejects(self):
        """tolerance: "5%" should fail cleanly."""
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(rail_assertions=[
            {"net": "VCC", "nominal": 3.3, "tolerance": "5%",
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "type_error" for f in report.findings)

    def test_negative_tolerance_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(rail_assertions=[
            {"net": "VCC", "nominal": 3.3, "tolerance": -0.05,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied

    def test_valid_tolerance_passes(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(rail_assertions=[
            {"net": "VCC", "nominal": 3.3, "tolerance": 0.1,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert ckt.sim_harness.rail_assertions[0].tolerance == 0.1


# ---------------------------------------------------------------------------
# ADVERSARIAL: load must have exactly one of resistance/current
# ---------------------------------------------------------------------------
class TestLoadExclusivity:
    def test_both_resistance_and_current_rejects(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "VCC", "resistance": 100.0, "current": 0.01,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert not report.applied
        assert any(f.category == "invalid_value" for f in report.findings)


# ---------------------------------------------------------------------------
# "3xAAA and 3.3V MCU" — structured intent from agent
# ---------------------------------------------------------------------------
class TestAgentDerivedIntent:
    def test_battery_mcu_intent(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(
            sources=[_src("VBAT", 4.5, "agent:battery-spec-3xAAA-nominal", 0.85)],
            loads=[_load("VCC", 330.0, "agent:estimated-mcu-draw-10mA-at-3.3V", 0.3)],
            rail_assertions=[
                _rail("VBAT", 4.5, "agent:battery-spec", 0.85),
                _rail("VCC", 3.3, "agent:ldo-output-typical", 0.6),
            ],
        )

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert report.sources_added == 1
        assert report.loads_added == 1
        assert report.rail_assertions_added == 2

        # Low-confidence load should be flagged
        assert len(report.low_confidence_items) == 1
        assert "loads[0]" in report.low_confidence_items[0]

    def test_malformed_agent_intent_clear_error(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(
            sources=[_src("VBAT", 4.5)],
            loads=[{"net": "VCC", "provenance": "agent", "confidence": 1.0}],
        )

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
        intent = _intent(sources=[_src()])

        report = apply_simulation_intent(intent, circuit=ckt)

        summary = report.summary()
        assert "applied: True" in summary
        assert "1 sources" in summary


# ---------------------------------------------------------------------------
# Custom rail names
# ---------------------------------------------------------------------------
class TestCustomRailNames:
    def test_nonstandard_rail_name(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(
            sources=[_src("CUSTOM_POWER_RAIL", 12.0, "user:custom")],
            rail_assertions=[_rail("CUSTOM_POWER_RAIL", 12.0)],
        )

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert ckt.sim_harness.sources[0].net_name == "CUSTOM_POWER_RAIL"


# ---------------------------------------------------------------------------
# Empty intent
# ---------------------------------------------------------------------------
class TestEmptyIntent:
    def test_empty_sections(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        report = apply_simulation_intent(_intent(), circuit=ckt)

        assert report.applied
        assert report.sources_added == 0

    def test_empty_lists(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(
            sources=[], loads=[], probes=[],
            rail_assertions=[], ratio_assertions=[],
        )

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied


# ---------------------------------------------------------------------------
# ADVERSARIAL: load with current (not resistance)
# ---------------------------------------------------------------------------
class TestLoadWithCurrent:
    def test_valid_current_load(self):
        from skidl.sim.intent import apply_simulation_intent

        ckt = _make_circuit()
        intent = _intent(loads=[
            {"net": "VCC", "current": 0.01,
             "provenance": "test", "confidence": 1.0},
        ])

        report = apply_simulation_intent(intent, circuit=ckt)

        assert report.applied
        assert ckt.sim_harness.loads[0].current == 0.01
        assert ckt.sim_harness.loads[0].resistance is None
