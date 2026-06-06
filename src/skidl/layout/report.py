from __future__ import annotations

from dataclasses import dataclass, field

from .candidates import PlacementCandidate
from .power import PowerRoutePlan
from .scoring import LayoutScore
from .validator import ValidationResult


@dataclass
class CandidateReport:
    name: str
    score: float
    overlap_count: int = 0
    outline_violation_count: int = 0
    keepout_violation_count: int = 0
    total_hpwl_mm: float = 0.0
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


@dataclass
class PlacementReport:
    selected: str
    candidates: list[CandidateReport] = field(default_factory=list)
    hard_violations: list[str] = field(default_factory=list)
    risky_nets: list[tuple[str, float]] = field(default_factory=list)
    power_corridors: list[str] = field(default_factory=list)
    power_topology: list[str] = field(default_factory=list)
    part_reasons: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Selected placement candidate: {self.selected}"]
        if self.reasons:
            lines.append("Reasons:")
            for reason in self.reasons[:10]:
                lines.append(f"  {reason}")
        if self.candidates:
            lines.append("Candidate scorecard:")
            for candidate in self.candidates[:8]:
                lines.append(
                    f"  {candidate.name}: {candidate.score:.1f}/100, "
                    f"HPWL {candidate.total_hpwl_mm:.1f}mm, "
                    f"overlaps {candidate.overlap_count}, "
                    f"outline {candidate.outline_violation_count}, "
                    f"keepout {candidate.keepout_violation_count}"
                )
        if self.hard_violations:
            lines.append("Hard violations:")
            for violation in self.hard_violations[:20]:
                lines.append(f"  {violation}")
        if self.risky_nets:
            lines.append("Top risky nets:")
            for name, hpwl in self.risky_nets[:10]:
                lines.append(f"  {name}: {hpwl:.1f}mm")
        if self.power_corridors:
            lines.append("Power corridors:")
            for corridor in self.power_corridors[:10]:
                lines.append(f"  {corridor}")
        if self.power_topology:
            lines.append("Power topology:")
            for chain in self.power_topology[:10]:
                lines.append(f"  {chain}")
        if self.part_reasons:
            lines.append("Part placement reasons:")
            for ref in sorted(self.part_reasons)[:12]:
                reason_text = "; ".join(self.part_reasons[ref][:3])
                lines.append(f"  {ref}: {reason_text}")
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings[:20]:
                lines.append(f"  {warning}")
        return "\n".join(lines)


def build_placement_report(
    selected: PlacementCandidate,
    candidate_scores: dict[str, LayoutScore],
    candidate_validations: dict[str, ValidationResult],
    power_plan: PowerRoutePlan,
) -> PlacementReport:
    candidate_reports: list[CandidateReport] = []
    for candidate in sorted(
        candidate_scores,
        key=lambda name: candidate_scores[name].score,
        reverse=True,
    ):
        score = candidate_scores[candidate]
        validation = candidate_validations[candidate]
        candidate_reports.append(
            CandidateReport(
                name=candidate,
                score=score.score,
                overlap_count=score.overlap_count,
                outline_violation_count=score.outline_violation_count,
                keepout_violation_count=score.keepout_violation_count,
                total_hpwl_mm=score.total_hpwl_mm,
                reasons=(
                    list(selected.reasons[:10])
                    if candidate == selected.name
                    else []
                ),
                warnings=list(score.warnings[:10]),
            )
        )

    selected_score = candidate_scores[selected.name]
    selected_validation = candidate_validations[selected.name]
    hard_violations = [
        *(f"overlap: {a} <-> {b}" for a, b in selected_validation.overlaps),
        *(f"outside outline: {ref}" for ref in selected_validation.outline_violations),
        *(f"inside keepout: {ref}" for ref in selected_validation.keepout_violations),
    ]
    reasons = list(selected.reasons)
    reasons.append(f"highest score among {len(candidate_scores)} candidate(s)")
    power_corridors = [
        (
            f"{corridor.net_name}: {corridor.width_mm:.2f}mm on {corridor.layer} "
            f"across {len(corridor.refs)} refs"
        )
        for corridor in power_plan.corridors
    ]
    power_topology = [
        (
            f"{chain.source_net}: "
            + " -> ".join(chain.ordered_refs[:10])
        )
        for chain in power_plan.topology.chains
    ]

    return PlacementReport(
        selected=selected.name,
        candidates=candidate_reports,
        hard_violations=hard_violations,
        risky_nets=list(selected_validation.worst_hpwl_nets),
        power_corridors=power_corridors,
        power_topology=power_topology,
        part_reasons=dict(selected.ref_reasons),
        warnings=list(selected_score.warnings[:20]) + list(power_plan.warnings[:20]),
        reasons=reasons,
    )
