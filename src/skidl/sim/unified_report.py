"""Unified simulation report — aggregates all analysis outputs.

Combines ERC, decoupling, power tree, rail sanity, PDN, and layout
feedback into a single report with ranked risks, assumption table,
coverage summary, and JSON export.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from enum import Enum

from .report import SimulationReport, FindingSeverity, SimulationFinding


class RiskLevel(Enum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    INFO = "info"


@dataclass
class Risk:
    level: RiskLevel
    title: str
    detail: str
    category: str = ""
    refs: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)


@dataclass
class Assumption:
    parameter: str
    value: str
    provenance: str
    confidence: float | None = None
    net: str = ""


@dataclass
class CoverageGap:
    area: str
    suggestion: str


@dataclass
class AnalysisCoverage:
    erc: bool = False
    decoupling: bool = False
    power_tree: bool = False
    rail_sanity: bool = False
    pdn: bool = False
    layout_feedback: bool = False

    @property
    def count(self) -> int:
        return sum([self.erc, self.decoupling, self.power_tree,
                    self.rail_sanity, self.pdn, self.layout_feedback])

    @property
    def total(self) -> int:
        return 6


@dataclass
class UnifiedReport:
    risks: list[Risk] = field(default_factory=list)
    assumptions: list[Assumption] = field(default_factory=list)
    coverage: AnalysisCoverage = field(default_factory=AnalysisCoverage)
    gaps: list[CoverageGap] = field(default_factory=list)
    section_summaries: dict[str, str] = field(default_factory=dict)
    part_count: int = 0
    net_count: int = 0
    declared_source_count: int = 0
    declared_load_count: int = 0

    @property
    def risk_count(self) -> int:
        return len(self.risks)

    @property
    def critical_count(self) -> int:
        return sum(1 for r in self.risks if r.level == RiskLevel.CRITICAL)

    @property
    def high_count(self) -> int:
        return sum(1 for r in self.risks if r.level == RiskLevel.HIGH)

    def top_risks(self, n: int = 5) -> list[Risk]:
        order = {RiskLevel.CRITICAL: 0, RiskLevel.HIGH: 1,
                 RiskLevel.MEDIUM: 2, RiskLevel.LOW: 3, RiskLevel.INFO: 4}
        return sorted(self.risks, key=lambda r: order.get(r.level, 5))[:n]

    def summary(self) -> str:
        lines = ["=== Simulation Analysis Report ==="]
        lines.append(
            f"Circuit: {self.part_count} parts, {self.net_count} nets"
        )
        lines.append(
            f"Coverage: {self.coverage.count}/{self.coverage.total} analyses"
        )
        lines.append(
            f"Declarations: {self.declared_source_count} sources, "
            f"{self.declared_load_count} loads"
        )
        lines.append("")

        # Top risks
        top = self.top_risks()
        if top:
            lines.append("--- Top Risks ---")
            for r in top:
                prefix = r.level.value.upper()
                lines.append(f"  [{prefix}] {r.title}")
                if r.detail:
                    lines.append(f"    {r.detail}")
            lines.append("")

        # Assumptions
        if self.assumptions:
            lines.append("--- Assumptions ---")
            for a in self.assumptions:
                conf = f" (confidence: {a.confidence:.0%})" if a.confidence is not None else ""
                lines.append(f"  {a.parameter}: {a.value}{conf}")
                lines.append(f"    Source: {a.provenance}")
            lines.append("")

        # Coverage gaps
        if self.gaps:
            lines.append("--- Declare Next ---")
            for g in self.gaps:
                lines.append(f"  {g.area}: {g.suggestion}")
            lines.append("")

        # Section summaries
        if self.section_summaries:
            lines.append("--- Section Details ---")
            for name, summary in self.section_summaries.items():
                lines.append(f"\n[{name}]")
                lines.append(summary)

        return "\n".join(lines)

    def to_dict(self) -> dict:
        def _risk(r: Risk) -> dict:
            d = {"level": r.level.value, "title": r.title, "detail": r.detail}
            if r.category:
                d["category"] = r.category
            if r.refs:
                d["refs"] = r.refs
            if r.nets:
                d["nets"] = r.nets
            return d

        def _assumption(a: Assumption) -> dict:
            d = {"parameter": a.parameter, "value": a.value,
                 "provenance": a.provenance}
            if a.confidence is not None:
                d["confidence"] = a.confidence
            if a.net:
                d["net"] = a.net
            return d

        return {
            "part_count": self.part_count,
            "net_count": self.net_count,
            "declared_sources": self.declared_source_count,
            "declared_loads": self.declared_load_count,
            "coverage": {
                "count": self.coverage.count,
                "total": self.coverage.total,
                "erc": self.coverage.erc,
                "decoupling": self.coverage.decoupling,
                "power_tree": self.coverage.power_tree,
                "rail_sanity": self.coverage.rail_sanity,
                "pdn": self.coverage.pdn,
                "layout_feedback": self.coverage.layout_feedback,
            },
            "risks": [_risk(r) for r in self.risks],
            "assumptions": [_assumption(a) for a in self.assumptions],
            "gaps": [{"area": g.area, "suggestion": g.suggestion}
                     for g in self.gaps],
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)


def generate_unified_report(
    circuit=None,
    erc_report: SimulationReport | None = None,
    decoupling_report=None,
    power_tree_report=None,
    rail_sanity_report=None,
    pdn_report=None,
    layout_feedback_report=None,
) -> UnifiedReport:
    """Aggregate all analysis outputs into a unified report.

    Pass individual reports from prior analysis calls. Any omitted
    reports are simply marked as not covered — no analysis is re-run.
    """
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    report = UnifiedReport()
    report.part_count = len(circuit.parts) if hasattr(circuit, "parts") else 0
    report.net_count = (len(circuit.get_nets())
                        if hasattr(circuit, "get_nets") else 0)

    harness = getattr(circuit, "sim_harness", None)
    if harness:
        report.declared_source_count = len(harness.sources)
        report.declared_load_count = len(harness.loads)
        for src in harness.sources:
            report.assumptions.append(Assumption(
                parameter=f"source voltage ({src.net_name})",
                value=f"{src.voltage}V",
                provenance=getattr(src, "provenance", "user"),
                confidence=getattr(src, "confidence", None),
                net=src.net_name,
            ))
        for load in harness.loads:
            val = (f"{load.current}A" if getattr(load, "current", None)
                   else f"{load.resistance}Ω")
            report.assumptions.append(Assumption(
                parameter=f"load ({load.net_name})",
                value=val,
                provenance=getattr(load, "provenance", "user"),
                confidence=getattr(load, "confidence", None),
                net=load.net_name,
            ))

    # ERC
    if erc_report is not None:
        report.coverage.erc = True
        report.section_summaries["ERC"] = erc_report.summary()
        for f in erc_report.findings:
            if f.severity == FindingSeverity.ERROR:
                report.risks.append(Risk(
                    level=RiskLevel.HIGH,
                    title=f.message,
                    detail="",
                    category=f.category,
                    refs=f.refs,
                    nets=f.nets,
                ))
        for c in erc_report.checks:
            if not c.passed:
                report.risks.append(Risk(
                    level=RiskLevel.HIGH,
                    title=f"Check failed: {c.name}",
                    detail=c.reason or "",
                    refs=c.refs,
                    nets=c.nets,
                ))
        if erc_report.missing_models:
            report.risks.append(Risk(
                level=RiskLevel.LOW,
                title=f"{len(erc_report.missing_models)} parts without SPICE models",
                detail="Passive-only analysis; active parts not simulated",
                category="missing_model",
            ))

    # Decoupling
    if decoupling_report is not None:
        report.coverage.decoupling = True
        report.section_summaries["Decoupling"] = decoupling_report.summary()
        for finding in getattr(decoupling_report, "findings", []):
            if finding.category == "missing_local":
                report.risks.append(Risk(
                    level=RiskLevel.MEDIUM,
                    title=f"Missing local decoupling: {finding.ic_ref} on {finding.rail}",
                    detail="Add 100nF cap between power pin and nearest ground",
                    category="missing_decoupling",
                    refs=[finding.ic_ref],
                    nets=[finding.rail],
                ))

    # Power tree
    if power_tree_report is not None:
        report.coverage.power_tree = True
        report.section_summaries["Power Tree"] = power_tree_report.summary()

    # Rail sanity
    if rail_sanity_report is not None:
        report.coverage.rail_sanity = True
        report.section_summaries["Rail Sanity"] = rail_sanity_report.summary()
        for rc in rail_sanity_report.resistor_checks:
            if rc.power_w is not None and rc.power_w > 0.5:
                report.risks.append(Risk(
                    level=RiskLevel.MEDIUM,
                    title=f"{rc.ref} dissipates {rc.power_w * 1000:.0f}mW",
                    detail="Check power rating of component",
                    category="high_power",
                    refs=[rc.ref],
                ))
        for ra in rail_sanity_report.rail_assertions:
            if ra.passed is False:
                report.risks.append(Risk(
                    level=RiskLevel.CRITICAL,
                    title=f"Rail assertion failed: {ra.net_name}",
                    detail=(f"Expected {ra.nominal}V ±{ra.tolerance*100:.0f}%, "
                            f"got {ra.actual}V"),
                    category="rail_assertion_fail",
                    nets=[ra.net_name],
                ))

    # PDN
    if pdn_report is not None:
        report.coverage.pdn = True
        report.section_summaries["PDN"] = pdn_report.summary()
        for rail in pdn_report.rails:
            if rail.violations:
                pct = len(rail.violations) / max(len(rail.frequencies), 1) * 100
                level = RiskLevel.HIGH if pct > 50 else RiskLevel.MEDIUM
                report.risks.append(Risk(
                    level=level,
                    title=(f"PDN impedance on {rail.net_name} exceeds target "
                           f"at {pct:.0f}% of frequencies"),
                    detail=(f"Target: {rail.z_target * 1000:.1f}mΩ, "
                            f"Worst: {(rail.worst_z or 0) * 1000:.1f}mΩ"),
                    category="pdn_violation",
                    nets=[rail.net_name],
                ))

    # Layout feedback
    if layout_feedback_report is not None:
        report.coverage.layout_feedback = True
        report.section_summaries["Layout Feedback"] = layout_feedback_report.summary()
        for s in layout_feedback_report.suggestions:
            if s.severity == "error":
                report.risks.append(Risk(
                    level=RiskLevel.HIGH,
                    title=s.message,
                    detail="",
                    category=s.category,
                    refs=[s.ref] if s.ref else [],
                ))

    # Coverage gaps → "declare next" suggestions
    if not harness or not harness.sources:
        report.gaps.append(CoverageGap(
            area="Power sources",
            suggestion="Declare voltage sources with sim_source() to enable rail analysis",
        ))
    if not report.coverage.erc:
        report.gaps.append(CoverageGap(
            area="ERC",
            suggestion="Run simulation_erc() for passive readiness check",
        ))
    if not report.coverage.decoupling:
        report.gaps.append(CoverageGap(
            area="Decoupling",
            suggestion="Run analyze_decoupling() to check cap placement",
        ))
    if not report.coverage.pdn:
        report.gaps.append(CoverageGap(
            area="PDN",
            suggestion="Run analyze_pdn() for frequency-domain impedance check",
        ))
    if not report.coverage.layout_feedback:
        report.gaps.append(CoverageGap(
            area="Layout feedback",
            suggestion="Run analyze_layout_feedback() with placement data",
        ))
    if harness and not harness.loads:
        report.gaps.append(CoverageGap(
            area="Load declarations",
            suggestion="Declare load currents with sim_load() for PDN target accuracy",
        ))

    return report
