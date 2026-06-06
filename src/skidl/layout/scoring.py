from __future__ import annotations

import math
from dataclasses import dataclass, field

from .congestion import build_congestion_map
from .geometry import FootprintGeometry
from .power import plan_power_routes
from .roles import GND_NET_RE, POWER_NET_RE, PartRole, classify_parts, pin_net_names
from .validator import validate
from .writer import PlacedPart


@dataclass
class LayoutScore:
    score: float
    total_hpwl_mm: float = 0.0
    overlap_count: int = 0
    outline_violation_count: int = 0
    keepout_violation_count: int = 0
    missing_count: int = 0
    warning_count: int = 0
    weighted_hpwl_mm: float = 0.0
    crossing_count: int = 0
    congestion_score: float = 0.0
    power_corridor_count: int = 0
    role_counts: dict[str, int] = field(default_factory=dict)
    power_net_count: int = 0
    congestion_regions: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return (
            self.overlap_count == 0
            and self.outline_violation_count == 0
            and self.keepout_violation_count == 0
            and self.missing_count == 0
        )

    def summary(self) -> str:
        lines = [f"Layout score: {self.score:.1f}/100"]
        lines.append(f"Total HPWL: {self.total_hpwl_mm:.1f}mm")
        if self.overlap_count:
            lines.append(f"Overlaps: {self.overlap_count}")
        if self.outline_violation_count:
            lines.append(f"Outside outline: {self.outline_violation_count}")
        if self.keepout_violation_count:
            lines.append(f"Inside keepout: {self.keepout_violation_count}")
        if self.missing_count:
            lines.append(f"Missing placements: {self.missing_count}")
        if self.crossing_count:
            lines.append(f"Estimated crossings: {self.crossing_count}")
        if self.congestion_score:
            lines.append(f"Pin escape congestion: {self.congestion_score:.1f}")
        if self.congestion_regions:
            lines.append("Top congested regions:")
            for region in self.congestion_regions[:5]:
                lines.append(f"  {region}")
        if self.power_corridor_count:
            lines.append(f"Power corridors: {self.power_corridor_count}")
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


def _net_weight(name: str) -> float:
    if GND_NET_RE.match(name):
        return 2.0
    if POWER_NET_RE.match(name):
        return 1.6
    if any(token in name.upper() for token in ("USB", "D+", "D-", "CLK", "XTAL")):
        return 1.5
    return 1.0


def _weighted_hpwl(placed_parts: list[PlacedPart], circuit) -> float:
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
            hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
            total += hpwl * _net_weight(str(getattr(net, "name", "") or ""))
    return total


def _segment_intersects(a1, a2, b1, b2) -> bool:
    def orient(p, q, r):
        return (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])

    o1 = orient(a1, a2, b1)
    o2 = orient(a1, a2, b2)
    o3 = orient(b1, b2, a1)
    o4 = orient(b1, b2, a2)
    return o1 * o2 < 0 and o3 * o4 < 0


def _estimate_crossings(placed_parts: list[PlacedPart], circuit) -> int:
    if circuit is None:
        return 0
    try:
        from skidl.net import NCNet
    except Exception:
        NCNet = None

    pos_by_ref = {pp.ref: (pp.x_mm, pp.y_mm) for pp in placed_parts}
    segments = []
    for net in circuit.get_nets():
        if NCNet is not None and isinstance(net, NCNet):
            continue
        refs = []
        for pin in net.get_pins():
            ref = getattr(getattr(pin, "part", None), "ref", None)
            if ref in pos_by_ref and ref not in refs:
                refs.append(ref)
        if len(refs) < 2:
            continue
        anchor = min(refs, key=lambda ref: (pos_by_ref[ref][0], pos_by_ref[ref][1], ref))
        for ref in refs:
            if ref != anchor:
                segments.append((anchor, ref, pos_by_ref[anchor], pos_by_ref[ref]))

    crossings = 0
    for idx, (a_ref, b_ref, a1, a2) in enumerate(segments):
        for c_ref, d_ref, b1, b2 in segments[idx + 1:]:
            if {a_ref, b_ref}.intersection({c_ref, d_ref}):
                continue
            if _segment_intersects(a1, a2, b1, b2):
                crossings += 1
    return crossings


def _pin_escape_congestion(placed_parts: list[PlacedPart], circuit) -> float:
    if circuit is None:
        return 0.0
    placed = {pp.ref: pp for pp in placed_parts}
    part_by_ref = {part.ref: part for part in circuit.parts if part.ref in placed}
    congestion = 0.0
    refs = sorted(part_by_ref)
    for i, ref in enumerate(refs):
        a = placed[ref]
        try:
            a_pins = len(part_by_ref[ref])
        except Exception:
            a_pins = 2
        for other_ref in refs[i + 1:]:
            b = placed[other_ref]
            dist = max(_distance(a, b), 0.1)
            if dist > 12.0:
                continue
            try:
                b_pins = len(part_by_ref[other_ref])
            except Exception:
                b_pins = 2
            congestion += (a_pins + b_pins) / dist
    return congestion


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
    keepouts=None,
    fp_geometries: dict[str, FootprintGeometry] | None = None,
    clearance_mm: float = 0.5,
    board_layers: int = 2,
) -> LayoutScore:
    validation = validate(
        placed_parts,
        circuit,
        fp_bboxes,
        clearance_mm=clearance_mm,
        outline=outline,
        keepouts=keepouts,
        fp_geometries=fp_geometries,
    )
    roles = classify_parts(circuit) if circuit is not None else {}
    warnings = _role_warnings(placed_parts, circuit, roles, fp_bboxes, outline)
    power_plan = None
    if circuit is not None:
        power_plan = plan_power_routes(circuit, placed_parts, board_layers=board_layers)
        warnings.extend(power_plan.warnings)
    total_hpwl = _total_hpwl(placed_parts, circuit)
    weighted_hpwl = _weighted_hpwl(placed_parts, circuit)
    crossing_count = _estimate_crossings(placed_parts, circuit)
    pin_escape_score = _pin_escape_congestion(placed_parts, circuit)
    congestion_map = build_congestion_map(
        placed_parts,
        circuit,
        outline=outline,
        keepouts=keepouts,
        power_plan=power_plan,
        board_layers=board_layers,
    )
    congestion_score = (
        pin_escape_score
        + congestion_map.peak_demand
        + congestion_map.average_demand * 0.5
    )
    congestion_regions = [
        region.label for region in congestion_map.top_regions(limit=5)
    ]

    penalty = 0.0
    penalty += len(validation.overlaps) * 25.0
    penalty += len(validation.outline_violations) * 20.0
    penalty += len(validation.keepout_violations) * 25.0
    penalty += len(validation.missing_refs) * 10.0
    penalty += min(total_hpwl / 50.0, 30.0)
    penalty += min(weighted_hpwl / 120.0, 20.0)
    penalty += min(crossing_count * 2.0, 20.0)
    penalty += min(congestion_score / 8.0, 15.0)
    penalty += min(len(warnings) * 5.0, 25.0)
    if power_plan is not None:
        for intent in power_plan.route_intents:
            if intent.width_mm >= 0.8 and intent.span_mm > 50.0:
                layer_relief = 0.45 if board_layers >= 4 else 1.0
                penalty += min((intent.span_mm - 50.0) / 10.0, 10.0) * layer_relief

    return LayoutScore(
        score=max(0.0, 100.0 - penalty),
        total_hpwl_mm=total_hpwl,
        overlap_count=len(validation.overlaps),
        outline_violation_count=len(validation.outline_violations),
        keepout_violation_count=len(validation.keepout_violations),
        missing_count=len(validation.missing_refs),
        warning_count=len(warnings),
        weighted_hpwl_mm=weighted_hpwl,
        crossing_count=crossing_count,
        congestion_score=congestion_score,
        role_counts=_role_counts(roles),
        power_net_count=len(power_plan.nets) if power_plan is not None else 0,
        congestion_regions=congestion_regions,
        power_corridor_count=(
            len(power_plan.corridors) if power_plan is not None else 0
        ),
        warnings=warnings,
    )
