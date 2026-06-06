from __future__ import annotations

import math
from dataclasses import dataclass, field

from .power import plan_power_routes
from .roles import PartRole, classify_parts, pin_net_names
from .validator import validate
from .writer import PlacedPart


@dataclass
class LayoutScore:
    score: float
    total_hpwl_mm: float = 0.0
    overlap_count: int = 0
    outline_violation_count: int = 0
    missing_count: int = 0
    warning_count: int = 0
    role_counts: dict[str, int] = field(default_factory=dict)
    power_net_count: int = 0
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.overlap_count == 0
            and self.outline_violation_count == 0
            and self.missing_count == 0
        )

    def summary(self) -> str:
        lines = [f"Layout score: {self.score:.1f}/100"]
        lines.append(f"Total HPWL: {self.total_hpwl_mm:.1f}mm")
        if self.overlap_count:
            lines.append(f"Overlaps: {self.overlap_count}")
        if self.outline_violation_count:
            lines.append(f"Outside outline: {self.outline_violation_count}")
        if self.missing_count:
            lines.append(f"Missing placements: {self.missing_count}")
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings[:20]:
                lines.append(f"  {warning}")
        return "\n".join(lines)


def _distance(a: PlacedPart, b: PlacedPart) -> float:
    return math.hypot(a.x_mm - b.x_mm, a.y_mm - b.y_mm)


def _total_hpwl(placed_parts: list[PlacedPart], circuit) -> float:
    if circuit is None:
        return 0.0

    try:
        from skidl.net import NCNet
    except Exception:
        NCNet = None

    pos_by_ref = {pp.ref: (pp.x_mm, pp.y_mm) for pp in placed_parts}
    total = 0.0
    for net in circuit.get_nets():
        if NCNet is not None and isinstance(net, NCNet):
            continue
        xs, ys = [], []
        for pin in net.get_pins():
            ref = getattr(getattr(pin, "part", None), "ref", None)
            if ref in pos_by_ref:
                x, y = pos_by_ref[ref]
                xs.append(x)
                ys.append(y)
        if len(xs) >= 2:
            total += (max(xs) - min(xs)) + (max(ys) - min(ys))
    return total


def _edge_distance(pp: PlacedPart, fp_bboxes, outline) -> float:
    w, h = fp_bboxes.get(pp.footprint, (2.0, 2.0))
    return min(
        abs(pp.x_mm - w / 2 - outline.x_min),
        abs(outline.x_max - (pp.x_mm + w / 2)),
        abs(pp.y_mm - h / 2 - outline.y_min),
        abs(outline.y_max - (pp.y_mm + h / 2)),
    )


def _role_counts(roles: dict[str, PartRole]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for role in roles.values():
        counts[role.role] = counts.get(role.role, 0) + 1
    return counts


def _role_warnings(
    placed_parts: list[PlacedPart],
    circuit,
    roles: dict[str, PartRole],
    fp_bboxes: dict[str, tuple[float, float]],
    outline=None,
) -> list[str]:
    placed_by_ref = {pp.ref: pp for pp in placed_parts}
    warnings: list[str] = []

    if outline is not None:
        for ref, role in roles.items():
            if role.role != "connector" or ref not in placed_by_ref:
                continue
            distance = _edge_distance(placed_by_ref[ref], fp_bboxes, outline)
            if distance > 5.0:
                warnings.append(
                    f"{ref}: connector is {distance:.1f}mm from nearest board edge"
                )

    if circuit is None:
        return warnings

    part_by_ref = {part.ref: part for part in circuit.parts}
    nets_by_ref = {ref: set(pin_net_names(part)) for ref, part in part_by_ref.items()}

    parent_roles = {"ic", "regulator"}
    for ref, role in roles.items():
        if role.role != "decoupling_cap" or ref not in placed_by_ref:
            continue
        cap_nets = nets_by_ref.get(ref, set())
        candidates = [
            other_ref
            for other_ref, other_role in roles.items()
            if other_ref in placed_by_ref
            and other_role.role in parent_roles
            and cap_nets.intersection(nets_by_ref.get(other_ref, set()))
        ]
        if not candidates:
            warnings.append(f"{ref}: no placed IC/regulator shares its supply nets")
            continue
        nearest_ref = min(
            candidates,
            key=lambda other_ref: _distance(
                placed_by_ref[ref], placed_by_ref[other_ref]
            ),
        )
        distance = _distance(placed_by_ref[ref], placed_by_ref[nearest_ref])
        if distance > 5.0:
            warnings.append(
                f"{ref}: decoupling cap is {distance:.1f}mm from {nearest_ref}"
            )

    for ref, role in roles.items():
        if role.role != "crystal" or ref not in placed_by_ref:
            continue
        ic_refs = [
            other_ref
            for other_ref, other_role in roles.items()
            if other_ref in placed_by_ref and other_role.role == "ic"
        ]
        if not ic_refs:
            continue
        nearest_ref = min(
            ic_refs,
            key=lambda other_ref: _distance(
                placed_by_ref[ref], placed_by_ref[other_ref]
            ),
        )
        distance = _distance(placed_by_ref[ref], placed_by_ref[nearest_ref])
        if distance > 10.0:
            warnings.append(
                f"{ref}: crystal is {distance:.1f}mm from nearest IC {nearest_ref}"
            )

    return warnings


def score_placement(
    placed_parts: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    outline=None,
    clearance_mm: float = 0.5,
    board_layers: int = 2,
) -> LayoutScore:
    validation = validate(
        placed_parts,
        circuit,
        fp_bboxes,
        clearance_mm=clearance_mm,
        outline=outline,
    )
    roles = classify_parts(circuit) if circuit is not None else {}
    warnings = _role_warnings(placed_parts, circuit, roles, fp_bboxes, outline)
    power_plan = None
    if circuit is not None:
        power_plan = plan_power_routes(circuit, placed_parts, board_layers=board_layers)
        warnings.extend(power_plan.warnings)
    total_hpwl = _total_hpwl(placed_parts, circuit)

    penalty = 0.0
    penalty += len(validation.overlaps) * 25.0
    penalty += len(validation.outline_violations) * 20.0
    penalty += len(validation.missing_refs) * 10.0
    penalty += min(total_hpwl / 50.0, 30.0)
    penalty += min(len(warnings) * 5.0, 25.0)

    return LayoutScore(
        score=max(0.0, 100.0 - penalty),
        total_hpwl_mm=total_hpwl,
        overlap_count=len(validation.overlaps),
        outline_violation_count=len(validation.outline_violations),
        missing_count=len(validation.missing_refs),
        warning_count=len(warnings),
        role_counts=_role_counts(roles),
        power_net_count=len(power_plan.nets) if power_plan is not None else 0,
        warnings=warnings,
    )
