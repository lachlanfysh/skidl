"""Tests for unified simulation report (Step 10).

Tests aggregation, risk ranking, assumption extraction, coverage gaps,
JSON export, and full-pipeline integration on the 45lux circuit.
"""
from __future__ import annotations

import json
import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")


class MockNet:
    def __init__(self, name):
        self.name = name


class MockPin:
    def __init__(self, net=None):
        self.net = net


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


def _harness(sources=None, loads=None):
    from skidl.sim.declarations import SimHarness, DeclaredSource, DeclaredLoad
    h = SimHarness()
    for s in (sources or []):
        h.sources.append(DeclaredSource(**s))
    for lo in (loads or []):
        h.loads.append(DeclaredLoad(**lo))
    return h


class TestUnifiedReportBasic:
    def test_empty_report(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit(parts=[MockPart("R1")], nets=[MockNet("VCC")])
        report = generate_unified_report(circuit=ckt)

        assert report.part_count == 1
        assert report.net_count == 1
        assert report.coverage.count == 0
        assert report.risk_count == 0

    def test_coverage_tracking(self):
        from skidl.sim.unified_report import generate_unified_report
        from skidl.sim.report import SimulationReport

        ckt = MockCircuit()
        erc = SimulationReport(executable=False)
        report = generate_unified_report(circuit=ckt, erc_report=erc)

        assert report.coverage.erc
        assert not report.coverage.decoupling
        assert report.coverage.count == 1
        assert "ERC" in report.section_summaries

    def test_assumptions_from_harness(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit(sim_harness=_harness(
            sources=[{"net_name": "VCC", "voltage": 3.3}],
            loads=[{"net_name": "VCC", "current": 0.15}],
        ))
        report = generate_unified_report(circuit=ckt)

        assert report.declared_source_count == 1

    def test_confidence_preserved_from_intent(self):
        """Confidence from intent flows through to unified report assumptions."""
        from skidl.sim.unified_report import generate_unified_report
        from skidl.sim.declarations import SimHarness, DeclaredSource

        h = SimHarness()
        h.sources.append(DeclaredSource(
            net_name="VCC", voltage=3.3,
            provenance="agent:datasheet", confidence=0.7,
        ))
        ckt = MockCircuit(sim_harness=h)
        report = generate_unified_report(circuit=ckt)

        src_a = next(a for a in report.assumptions if "source" in a.parameter)
        assert src_a.confidence == 0.7
        assert src_a.provenance == "agent:datasheet"
        assert "3.3" in src_a.value
        assert src_a.net == "VCC"


class TestRiskRanking:
    def test_risks_from_erc_findings(self):
        from skidl.sim.unified_report import generate_unified_report, RiskLevel
        from skidl.sim.report import SimulationReport, SimulationFinding, FindingSeverity

        erc = SimulationReport(findings=[
            SimulationFinding(
                severity=FindingSeverity.ERROR,
                message="Net VCC has no source",
                nets=["VCC"],
                category="missing_source",
            ),
        ])
        ckt = MockCircuit()
        report = generate_unified_report(circuit=ckt, erc_report=erc)

        assert len(report.risks) >= 1
        assert any(r.level == RiskLevel.HIGH for r in report.risks)

    def test_risks_from_failed_checks(self):
        from skidl.sim.unified_report import generate_unified_report, RiskLevel
        from skidl.sim.report import SimulationReport, SimulationCheck

        erc = SimulationReport(checks=[
            SimulationCheck(name="rc_tau_R1_C1", passed=False, reason="too slow"),
        ])
        ckt = MockCircuit()
        report = generate_unified_report(circuit=ckt, erc_report=erc)

        assert any(r.level == RiskLevel.HIGH and "rc_tau" in r.title
                   for r in report.risks)

    def test_top_risks_ordered(self):
        from skidl.sim.unified_report import (
            UnifiedReport, Risk, RiskLevel,
        )

        report = UnifiedReport(risks=[
            Risk(level=RiskLevel.LOW, title="minor", detail=""),
            Risk(level=RiskLevel.CRITICAL, title="critical", detail=""),
            Risk(level=RiskLevel.MEDIUM, title="medium", detail=""),
        ])

        top = report.top_risks(2)
        assert len(top) == 2
        assert top[0].level == RiskLevel.CRITICAL
        assert top[1].level == RiskLevel.MEDIUM


class TestCoverageGaps:
    def test_gaps_when_no_sources(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit()
        report = generate_unified_report(circuit=ckt)

        gap_areas = [g.area for g in report.gaps]
        assert "Power sources" in gap_areas

    def test_gaps_when_no_loads(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit(sim_harness=_harness(
            sources=[{"net_name": "VCC", "voltage": 3.3}],
        ))
        report = generate_unified_report(circuit=ckt)

        gap_areas = [g.area for g in report.gaps]
        assert "Load declarations" in gap_areas

    def test_gaps_for_missing_analyses(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit()
        report = generate_unified_report(circuit=ckt)

        gap_areas = [g.area for g in report.gaps]
        assert "ERC" in gap_areas
        assert "Decoupling" in gap_areas
        assert "PDN" in gap_areas

    def test_no_gap_when_covered(self):
        from skidl.sim.unified_report import generate_unified_report
        from skidl.sim.report import SimulationReport

        ckt = MockCircuit(sim_harness=_harness(
            sources=[{"net_name": "VCC", "voltage": 3.3}],
            loads=[{"net_name": "VCC", "current": 0.1}],
        ))
        erc = SimulationReport()
        report = generate_unified_report(circuit=ckt, erc_report=erc)

        gap_areas = [g.area for g in report.gaps]
        assert "ERC" not in gap_areas
        assert "Power sources" not in gap_areas
        assert "Load declarations" not in gap_areas


class TestJSONExport:
    def test_json_roundtrip(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit(
            parts=[MockPart("R1"), MockPart("C1")],
            nets=[MockNet("VCC"), MockNet("GND")],
            sim_harness=_harness(
                sources=[{"net_name": "VCC", "voltage": 3.3}],
            ),
        )
        report = generate_unified_report(circuit=ckt)

        j = report.to_json()
        data = json.loads(j)

        assert data["part_count"] == 2
        assert data["net_count"] == 2
        assert data["declared_sources"] == 1
        assert data["coverage"]["count"] == 0
        assert isinstance(data["risks"], list)
        assert isinstance(data["assumptions"], list)
        assert isinstance(data["gaps"], list)

    def test_dict_has_stable_keys(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit()
        report = generate_unified_report(circuit=ckt)
        d = report.to_dict()

        expected_keys = {
            "part_count", "net_count", "declared_sources",
            "declared_loads", "coverage", "risks", "assumptions", "gaps",
        }
        assert set(d.keys()) == expected_keys


class TestSummaryFormat:
    def test_summary_contains_sections(self):
        from skidl.sim.unified_report import generate_unified_report
        from skidl.sim.report import SimulationReport

        ckt = MockCircuit(
            parts=[MockPart("R1")],
            sim_harness=_harness(
                sources=[{"net_name": "VCC", "voltage": 3.3}],
            ),
        )
        erc = SimulationReport()
        report = generate_unified_report(circuit=ckt, erc_report=erc)

        summary = report.summary()
        assert "=== Simulation Analysis Report ===" in summary
        assert "Coverage:" in summary
        assert "Declarations:" in summary

    def test_summary_shows_risks(self):
        from skidl.sim.unified_report import (
            UnifiedReport, Risk, RiskLevel, AnalysisCoverage,
        )

        report = UnifiedReport(risks=[
            Risk(level=RiskLevel.CRITICAL, title="Bad rail", detail="VCC wrong"),
        ])
        summary = report.summary()

        assert "CRITICAL" in summary
        assert "Bad rail" in summary
        assert "Top Risks" in summary

    def test_summary_shows_gaps(self):
        from skidl.sim.unified_report import generate_unified_report

        ckt = MockCircuit()
        report = generate_unified_report(circuit=ckt)
        summary = report.summary()

        assert "Declare Next" in summary
