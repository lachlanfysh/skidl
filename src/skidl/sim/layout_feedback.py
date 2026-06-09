"""Layout feedback loop — simulation findings inform placement scoring.

Bridges sim analysis (decoupling, PDN, rail sanity) with layout scoring.
Produces actionable placement suggestions and computes a sim-weighted
penalty for the layout score.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from .report import FindingSeverity, SimulationFinding


@dataclass
class PlacementSuggestion:
    severity: str  # "error", "warning", "info"
    ref: str
    message: str
    target_ref: str = ""
    current_distance_mm: float | None = None
    recommended_distance_mm: float | None = None
    category: str = ""

    def to_dict(self) -> dict:
        return {
            "severity": self.severity,
            "ref": self.ref,
            "message": self.message,
            "target_ref": self.target_ref,
            "current_distance_mm": self.current_distance_mm,
            "recommended_distance_mm": self.recommended_distance_mm,
            "category": self.category,
        }


@dataclass
class LayoutFeedbackReport:
    suggestions: list[PlacementSuggestion] = field(default_factory=list)
    sim_penalty: float = 0.0
    findings: list[SimulationFinding] = field(default_factory=list)
    decoupling_analyzed: bool = False
    pdn_analyzed: bool = False
    rail_sanity_analyzed: bool = False

    @property
    def suggestion_count(self) -> int:
        return len(self.suggestions)

    @property
    def error_count(self) -> int:
        return sum(1 for s in self.suggestions if s.severity == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for s in self.suggestions if s.severity == "warning")

    def to_dict(self) -> dict:
        return {
            "suggestions": [s.to_dict() for s in self.suggestions],
            "sim_penalty": self.sim_penalty,
            "suggestion_count": self.suggestion_count,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "decoupling_analyzed": self.decoupling_analyzed,
            "pdn_analyzed": self.pdn_analyzed,
            "rail_sanity_analyzed": self.rail_sanity_analyzed,
        }

    def summary(self) -> str:
        lines = ["Layout feedback:"]
        lines.append(
            f"  {self.suggestion_count} suggestions "
            f"({self.error_count} errors, {self.warning_count} warnings)"
        )
        lines.append(f"  Sim penalty: {self.sim_penalty:.1f}")
        analyzed = []
        if self.decoupling_analyzed:
            analyzed.append("decoupling")
        if self.pdn_analyzed:
            analyzed.append("PDN")
        if self.rail_sanity_analyzed:
            analyzed.append("rail sanity")
        if analyzed:
            lines.append(f"  Analyzed: {', '.join(analyzed)}")
        for s in self.suggestions:
            prefix = s.severity.upper()
            lines.append(f"  [{prefix}] {s.ref}: {s.message}")
        return "\n".join(lines)


_DECAP_WARN_MM = 5.0
_DECAP_ERROR_MM = 10.0
_BULK_REGULATOR_WARN_MM = 15.0


def analyze_layout_feedback(
    circuit=None,
    placed: dict[str, tuple[float, float]] | None = None,
    fp_bboxes: dict[str, tuple[float, float]] | None = None,
) -> LayoutFeedbackReport:
    """Analyze placement quality using simulation findings.

    Runs decoupling, PDN, and rail sanity analysis, then produces
    specific placement improvement suggestions.

    Args:
        circuit: Target circuit. Defaults to builtins.default_circuit.
        placed: Map of ref → (x_mm, y_mm) placement positions.
        fp_bboxes: Footprint bounding boxes.
    """
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    report = LayoutFeedbackReport()

    # Run decoupling analysis
    from .decoupling import analyze_decoupling
    decoupling = analyze_decoupling(circuit, placed, fp_bboxes)
    report.decoupling_analyzed = True

    # Decap distance suggestions
    for assoc in decoupling.associations:
        if assoc.distance_mm is None:
            continue
        if assoc.distance_mm > _DECAP_ERROR_MM:
            report.suggestions.append(PlacementSuggestion(
                severity="error",
                ref=assoc.cap_ref,
                message=(
                    f"decoupling cap is {assoc.distance_mm:.1f}mm from "
                    f"{assoc.ic_ref} — should be <{_DECAP_WARN_MM}mm for "
                    f"effective high-frequency decoupling on {assoc.rail}"
                ),
                target_ref=assoc.ic_ref,
                current_distance_mm=assoc.distance_mm,
                recommended_distance_mm=_DECAP_WARN_MM,
                category="decap_too_far",
            ))
            report.sim_penalty += 5.0
        elif assoc.distance_mm > _DECAP_WARN_MM:
            report.suggestions.append(PlacementSuggestion(
                severity="warning",
                ref=assoc.cap_ref,
                message=(
                    f"decoupling cap is {assoc.distance_mm:.1f}mm from "
                    f"{assoc.ic_ref} — closer placement improves "
                    f"high-frequency performance on {assoc.rail}"
                ),
                target_ref=assoc.ic_ref,
                current_distance_mm=assoc.distance_mm,
                recommended_distance_mm=_DECAP_WARN_MM,
                category="decap_far",
            ))
            report.sim_penalty += 2.0

    # Missing decoupling findings
    for finding in decoupling.findings:
        if finding.category == "missing_local":
            report.suggestions.append(PlacementSuggestion(
                severity="warning",
                ref=finding.ic_ref,
                message=(
                    f"IC {finding.ic_ref} has no local decoupling cap on "
                    f"{finding.rail} — add 100nF cap"
                ),
                category="missing_decap",
            ))
            report.sim_penalty += 3.0

    # Bulk cap near regulator check
    _check_bulk_near_regulator(circuit, decoupling, placed, report)

    # Rail sanity
    from .rail_sanity import analyze_rail_sanity
    rail_report = analyze_rail_sanity(circuit)
    report.rail_sanity_analyzed = True

    for rc in rail_report.resistor_checks:
        if rc.power_w is not None and rc.power_w > 0.25:
            report.suggestions.append(PlacementSuggestion(
                severity="warning",
                ref=rc.ref,
                message=(
                    f"dissipates {rc.power_w * 1000:.0f}mW — consider "
                    f"thermal relief or larger footprint"
                ),
                category="high_power_resistor",
            ))
            report.sim_penalty += 1.0

    # PDN analysis (only if harness declares sources)
    harness = getattr(circuit, "sim_harness", None)
    if harness and harness.sources:
        from .pdn import analyze_pdn, PDNConstraints
        pdn_report = analyze_pdn(
            circuit, PDNConstraints(freq_points=50), placed=placed,
            fp_bboxes=fp_bboxes,
        )
        report.pdn_analyzed = True

        for rail in pdn_report.rails:
            if rail.violations:
                pct = len(rail.violations) / max(len(rail.frequencies), 1) * 100
                report.suggestions.append(PlacementSuggestion(
                    severity="warning" if pct < 50 else "error",
                    ref="",
                    message=(
                        f"PDN impedance on {rail.net_name} exceeds target at "
                        f"{pct:.0f}% of frequencies — worst "
                        f"{rail.worst_z * 1000:.0f}mΩ at "
                        f"{_freq_str(rail.worst_freq)}"
                    ),
                    category="pdn_violation",
                ))
                report.sim_penalty += min(pct / 10, 5.0)

    return report


def _check_bulk_near_regulator(circuit, decoupling, placed, report):
    """Check that bulk caps are placed near regulator outputs."""
    if placed is None:
        return

    import re
    regulator_re = re.compile(r"^U", re.IGNORECASE)
    regulator_refs = set()
    for part in circuit.parts:
        if not regulator_re.match(part.ref):
            continue
        name = str(getattr(part, "name", "") or "").lower()
        if any(kw in name for kw in ("regulator", "ldo", "vreg", "buck", "boost")):
            regulator_refs.add(part.ref)

    if not regulator_refs:
        return

    bulk_caps = [c for c in decoupling.caps if c.classification == "bulk"]
    for cap in bulk_caps:
        if cap.ref not in placed:
            continue
        cx, cy = placed[cap.ref]

        nearest_reg = None
        nearest_dist = float("inf")
        for reg_ref in regulator_refs:
            if reg_ref not in placed:
                continue
            rx, ry = placed[reg_ref]
            dist = math.hypot(cx - rx, cy - ry)
            if dist < nearest_dist:
                nearest_dist = dist
                nearest_reg = reg_ref

        if nearest_reg and nearest_dist > _BULK_REGULATOR_WARN_MM:
            report.suggestions.append(PlacementSuggestion(
                severity="warning",
                ref=cap.ref,
                message=(
                    f"bulk cap is {nearest_dist:.1f}mm from nearest regulator "
                    f"{nearest_reg} — should be <{_BULK_REGULATOR_WARN_MM}mm "
                    f"for effective input/output filtering"
                ),
                target_ref=nearest_reg,
                current_distance_mm=nearest_dist,
                recommended_distance_mm=_BULK_REGULATOR_WARN_MM,
                category="bulk_far_from_regulator",
            ))
            report.sim_penalty += 2.0


def _freq_str(freq: float | None) -> str:
    if freq is None:
        return "?"
    if freq >= 1e6:
        return f"{freq / 1e6:.1f}MHz"
    if freq >= 1e3:
        return f"{freq / 1e3:.1f}kHz"
    return f"{freq:.0f}Hz"
