"""Phase 8: Per-part and per-net placement explainability.

Provides human-readable answers to "why is this here?" and
"what's risky about this net?" from a LayoutResult.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .constraints import BoardOutline
from .roles import (
    DECAP_VALUE_RE,
    GND_NET_RE,
    POWER_NET_RE,
    PartRole,
    classify_parts,
    pin_net_names,
)
from .writer import PlacedPart


@dataclass
class PartExplanation:
    ref: str
    footprint: str
    position: tuple[float, float]
    rotation: float
    placement_reasons: list[str]
    role: str
    warnings: list[str]
    nearby_parts: list[tuple[str, float]]
    edge_distances: dict[str, float]
    suggestions: list[str]

    def summary(self) -> str:
        lines = [f"{self.ref} ({self.role}) at ({self.position[0]:.1f}, {self.position[1]:.1f})mm, {self.rotation:.0f}°"]
        if self.placement_reasons:
            lines.append("  Placement reasons:")
            for r in self.placement_reasons[:5]:
                lines.append(f"    - {r}")
        if self.warnings:
            lines.append("  Warnings:")
            for w in self.warnings[:5]:
                lines.append(f"    - {w}")
        if self.nearby_parts:
            nearby_str = ", ".join(f"{ref} ({d:.1f}mm)" for ref, d in self.nearby_parts[:5])
            lines.append(f"  Nearest: {nearby_str}")
        if self.edge_distances:
            edges_str = ", ".join(f"{e}: {d:.1f}mm" for e, d in self.edge_distances.items())
            lines.append(f"  Edge distances: {edges_str}")
        if self.suggestions:
            lines.append("  Suggestions:")
            for s in self.suggestions[:3]:
                lines.append(f"    → {s}")
        return "\n".join(lines)


@dataclass
class NetExplanation:
    name: str
    hpwl_mm: float
    pin_count: int
    part_refs: list[str]
    is_power: bool
    is_ground: bool
    span_x_mm: float
    span_y_mm: float
    risks: list[str]
    suggestions: list[str]

    def summary(self) -> str:
        net_type = "ground" if self.is_ground else "power" if self.is_power else "signal"
        lines = [f"Net {self.name} ({net_type}): HPWL {self.hpwl_mm:.1f}mm, {self.pin_count} pins across {len(self.part_refs)} parts"]
        lines.append(f"  Span: {self.span_x_mm:.1f}mm x {self.span_y_mm:.1f}mm")
        if self.risks:
            lines.append("  Risks:")
            for r in self.risks[:5]:
                lines.append(f"    - {r}")
        if self.suggestions:
            lines.append("  Suggestions:")
            for s in self.suggestions[:3]:
                lines.append(f"    → {s}")
        return "\n".join(lines)


@dataclass
class RiskItem:
    severity: str
    category: str
    description: str
    ref_or_net: str
    suggestion: str

    def summary(self) -> str:
        return f"[{self.severity}] {self.category}: {self.description} → {self.suggestion}"


def _distance(a: PlacedPart, b: PlacedPart) -> float:
    return math.hypot(a.x_mm - b.x_mm, a.y_mm - b.y_mm)


def _edge_distances(part: PlacedPart, outline: BoardOutline | None) -> dict[str, float]:
    if outline is None or not outline.vertices:
        return {}
    return {
        "left": part.x_mm - outline.x_min,
        "right": outline.x_max - part.x_mm,
        "top": part.y_mm - outline.y_min,
        "bottom": outline.y_max - part.y_mm,
    }


def _nearest_parts(
    target: PlacedPart,
    all_parts: list[PlacedPart],
    n: int = 5,
) -> list[tuple[str, float]]:
    distances = []
    for p in all_parts:
        if p.ref == target.ref:
            continue
        distances.append((p.ref, _distance(target, p)))
    distances.sort(key=lambda x: x[1])
    return distances[:n]


def explain_part(
    ref: str,
    result,
    circuit=None,
) -> PartExplanation:
    """Explain why a part is placed where it is."""
    placed_map = {p.ref: p for p in result.placed_parts}
    part = placed_map.get(ref)
    if part is None:
        return PartExplanation(
            ref=ref, footprint="", position=(0, 0), rotation=0,
            placement_reasons=["Part not found in placement"],
            role="unknown", warnings=[], nearby_parts=[],
            edge_distances={}, suggestions=[],
        )

    reasons = []
    if result.report and ref in result.report.part_reasons:
        reasons = list(result.report.part_reasons[ref])

    role = "unknown"
    if circuit is not None:
        roles = classify_parts(circuit)
        if ref in roles:
            role = roles[ref].role

    warnings = []
    if result.report:
        for w in result.report.warnings:
            if ref in w:
                warnings.append(w)

    nearby = _nearest_parts(part, result.placed_parts)
    edges = _edge_distances(part, result.outline)

    suggestions = []
    if role == "decoupling_cap" and nearby:
        closest_ref, closest_dist = nearby[0]
        if closest_dist > 5.0:
            suggestions.append(
                f"Move closer to {closest_ref} (currently {closest_dist:.1f}mm, target <5mm)"
            )

    if role == "connector" and edges:
        min_edge = min(edges.values())
        if min_edge > 5.0:
            closest_edge = min(edges, key=edges.get)
            suggestions.append(
                f"Consider placing closer to {closest_edge} edge "
                f"(currently {min_edge:.1f}mm away)"
            )

    if not reasons:
        reasons.append("No specific placement reason recorded — placed by default strategy")

    return PartExplanation(
        ref=ref,
        footprint=part.footprint,
        position=(part.x_mm, part.y_mm),
        rotation=part.rot_deg,
        placement_reasons=reasons,
        role=role,
        warnings=warnings,
        nearby_parts=nearby,
        edge_distances=edges,
        suggestions=suggestions,
    )


def explain_net(
    name: str,
    result,
    circuit=None,
) -> NetExplanation:
    """Explain the placement quality of a specific net."""
    is_power = bool(POWER_NET_RE.match(name))
    is_ground = bool(GND_NET_RE.match(name))

    placed_map = {p.ref: p for p in result.placed_parts}
    part_refs = []
    pin_count = 0

    if circuit is not None:
        for net in circuit.get_nets():
            if getattr(net, "name", None) != name:
                continue
            for pin in net.get_pins():
                pin_count += 1
                ref = getattr(pin.part, "ref", None)
                if ref and ref not in part_refs:
                    part_refs.append(ref)

    positions = [placed_map[ref] for ref in part_refs if ref in placed_map]
    span_x = 0.0
    span_y = 0.0
    if len(positions) >= 2:
        xs = [p.x_mm for p in positions]
        ys = [p.y_mm for p in positions]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)

    hpwl_mm = 0.0
    if result.report:
        for net_name, hpwl in result.report.risky_nets:
            if net_name == name:
                hpwl_mm = hpwl
                break
    if hpwl_mm == 0.0 and len(positions) >= 2:
        xs = [p.x_mm for p in positions]
        ys = [p.y_mm for p in positions]
        hpwl_mm = (max(xs) - min(xs)) + (max(ys) - min(ys))

    risks = []
    suggestions = []

    if is_power or is_ground:
        if hpwl_mm > 40.0:
            risks.append(f"High wirelength ({hpwl_mm:.1f}mm) for power net — parts spread across board")
        if span_x > 30.0 and span_y > 20.0:
            risks.append(f"Large 2D span ({span_x:.0f}x{span_y:.0f}mm) — routing will require long traces or wide pours")
    else:
        if hpwl_mm > 20.0:
            risks.append(f"Signal net spans {hpwl_mm:.1f}mm — consider grouping connected parts closer")
        if len(part_refs) > 4:
            risks.append(f"High fanout ({len(part_refs)} parts) — may cause congestion")

    if is_ground and result.power_plan:
        gnd_plan = result.power_plan.net("GND")
        if gnd_plan is not None:
            strategy = next(
                (i.strategy for i in result.power_plan.route_intents if i.net_name == "GND"),
                None,
            )
            if strategy:
                suggestions.append(f"GND routing strategy: {strategy}")

    if hpwl_mm > 30.0 and not (is_power or is_ground):
        suggestions.append("Group the connected parts closer together to reduce trace length")

    return NetExplanation(
        name=name,
        hpwl_mm=hpwl_mm,
        pin_count=pin_count,
        part_refs=part_refs,
        is_power=is_power,
        is_ground=is_ground,
        span_x_mm=span_x,
        span_y_mm=span_y,
        risks=risks,
        suggestions=suggestions,
    )


def top_risks(
    result,
    circuit=None,
    limit: int = 10,
) -> list[RiskItem]:
    """Identify the top placement risks for actionable review."""
    risks: list[RiskItem] = []

    if result.validation.overlaps:
        for a, b in result.validation.overlaps:
            risks.append(RiskItem(
                severity="HIGH",
                category="overlap",
                description=f"{a} and {b} overlap",
                ref_or_net=f"{a}, {b}",
                suggestion="Move one part to clear the overlap",
            ))

    for ref in result.validation.outline_violations:
        risks.append(RiskItem(
            severity="HIGH",
            category="outline",
            description=f"{ref} is outside the board outline",
            ref_or_net=ref,
            suggestion="Move inside the board boundary",
        ))

    for ref in result.validation.keepout_violations:
        risks.append(RiskItem(
            severity="HIGH",
            category="keepout",
            description=f"{ref} is inside a keepout zone",
            ref_or_net=ref,
            suggestion="Move outside the keepout area",
        ))

    if result.report:
        for w in result.report.warnings:
            if "decoupling cap" in w.lower() and "mm from" in w.lower():
                ref = w.split(":")[0].strip() if ":" in w else ""
                risks.append(RiskItem(
                    severity="MEDIUM",
                    category="decap_distance",
                    description=w,
                    ref_or_net=ref,
                    suggestion="Move decoupling cap closer to its parent IC power pins",
                ))
            elif "connector" in w.lower() and "edge" in w.lower():
                ref = w.split(":")[0].strip() if ":" in w else ""
                risks.append(RiskItem(
                    severity="LOW",
                    category="connector_edge",
                    description=w,
                    ref_or_net=ref,
                    suggestion="Place connector flush with board edge for cable access",
                ))

        for net_name, hpwl in result.report.risky_nets[:5]:
            if hpwl > 40.0:
                is_pwr = bool(POWER_NET_RE.match(net_name)) or bool(GND_NET_RE.match(net_name))
                severity = "MEDIUM" if is_pwr else "HIGH"
                risks.append(RiskItem(
                    severity=severity,
                    category="wirelength",
                    description=f"Net {net_name} has {hpwl:.1f}mm HPWL",
                    ref_or_net=net_name,
                    suggestion="Group connected parts closer" if not is_pwr
                    else "Ensure power pour/plane covers this net",
                ))

    if result.score and result.score.congestion_score > 50.0:
        risks.append(RiskItem(
            severity="MEDIUM",
            category="congestion",
            description=f"Pin escape congestion score: {result.score.congestion_score:.0f}",
            ref_or_net="board",
            suggestion="Spread dense IC clusters or add vias for layer transitions",
        ))

    risks.sort(key=lambda r: {"HIGH": 0, "MEDIUM": 1, "LOW": 2}.get(r.severity, 3))
    return risks[:limit]
