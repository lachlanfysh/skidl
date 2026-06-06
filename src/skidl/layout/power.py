from __future__ import annotations

import re
from dataclasses import dataclass, field

from .roles import GND_NET_RE, POWER_NET_RE, classify_parts
from .writer import PlacedPart


HIGH_CURRENT_NET_RE = re.compile(
    r"^(VBUS|VIN|VRAW|BAT|BATT|5V|\+5V)$",
    re.IGNORECASE,
)


@dataclass
class PowerNet:
    name: str
    kind: str
    refs: list[str] = field(default_factory=list)
    suggested_width_mm: float = 0.25
    suggested_layer: str = "F.Cu"
    priority: int = 50


@dataclass
class PowerRouteIntent:
    net_name: str
    strategy: str
    layer: str
    width_mm: float
    priority: int
    refs: list[str] = field(default_factory=list)
    ordered_refs: list[str] = field(default_factory=list)
    span_mm: float = 0.0


@dataclass
class PowerCorridor:
    net_name: str
    layer: str
    width_mm: float
    priority: int
    x_min: float
    y_min: float
    x_max: float
    y_max: float
    refs: list[str] = field(default_factory=list)

    @property
    def span_mm(self) -> float:
        return (self.x_max - self.x_min) + (self.y_max - self.y_min)


@dataclass
class PowerRoutePlan:
    nets: list[PowerNet] = field(default_factory=list)
    route_intents: list[PowerRouteIntent] = field(default_factory=list)
    corridors: list[PowerCorridor] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def net(self, name: str) -> PowerNet | None:
        for power_net in self.nets:
            if power_net.name == name:
                return power_net
        return None

    def summary(self) -> str:
        lines = ["Power route plan:"]
        for net in self.nets:
            refs = ", ".join(net.refs[:8])
            lines.append(
                f"  {net.name}: {net.kind}, {net.suggested_width_mm:.2f}mm, "
                f"{net.suggested_layer}, priority {net.priority}"
            )
            if refs:
                lines.append(f"    refs: {refs}")
        if self.route_intents:
            lines.append("Route intents:")
            for intent in self.route_intents[:20]:
                refs = " -> ".join(intent.ordered_refs[:8])
                lines.append(
                    f"  {intent.net_name}: {intent.strategy}, "
                    f"{intent.width_mm:.2f}mm on {intent.layer}, "
                    f"span {intent.span_mm:.1f}mm"
                )
                if refs:
                    lines.append(f"    order: {refs}")
        if self.corridors:
            lines.append("Reserved power corridors:")
            for corridor in self.corridors[:20]:
                refs = " -> ".join(corridor.refs[:8])
                lines.append(
                    f"  {corridor.net_name}: {corridor.width_mm:.2f}mm on "
                    f"{corridor.layer}, bounds "
                    f"({corridor.x_min:.1f},{corridor.y_min:.1f}) to "
                    f"({corridor.x_max:.1f},{corridor.y_max:.1f})"
                )
                if refs:
                    lines.append(f"    refs: {refs}")
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings[:20]:
                lines.append(f"  {warning}")
        return "\n".join(lines)


def _net_kind(name: str) -> str | None:
    if GND_NET_RE.match(name):
        return "ground"
    if POWER_NET_RE.match(name) or HIGH_CURRENT_NET_RE.match(name):
        return "supply"
    return None


def _pin_refs(net) -> list[str]:
    refs = []
    for pin in net.get_pins():
        ref = getattr(getattr(pin, "part", None), "ref", None)
        if ref and ref not in refs:
            refs.append(ref)
    return refs


def _suggest_width(name: str, kind: str, refs: list[str]) -> float:
    if kind == "ground":
        return 0.5
    if HIGH_CURRENT_NET_RE.match(name):
        return 0.8
    if len(refs) >= 6:
        return 0.5
    return 0.3


def _suggest_layer(kind: str, board_layers: int) -> str:
    if kind == "ground" and board_layers >= 4:
        return "In1.Cu"
    if kind == "supply" and board_layers >= 4:
        return "In2.Cu"
    return "F.Cu"


def _priority(name: str, kind: str, refs: list[str]) -> int:
    if kind == "ground":
        return 100
    if HIGH_CURRENT_NET_RE.match(name):
        return 95
    return min(90, 60 + len(refs) * 3)


def _strategy(net: PowerNet, board_layers: int, placed_ref_count: int) -> str:
    if placed_ref_count <= 1:
        return "fanout_only"
    if net.kind == "ground":
        return "plane" if board_layers >= 4 else "pour"
    if board_layers >= 4:
        return "internal_rail"
    if net.suggested_width_mm >= 0.8:
        return "wide_trunk"
    return "trunk"


def identify_power_nets(circuit, board_layers: int = 2) -> list[PowerNet]:
    power_nets: list[PowerNet] = []
    for net in circuit.get_nets():
        name = str(getattr(net, "name", "") or "")
        kind = _net_kind(name)
        if kind is None:
            continue
        refs = _pin_refs(net)
        power_nets.append(
            PowerNet(
                name=name,
                kind=kind,
                refs=refs,
                suggested_width_mm=_suggest_width(name, kind, refs),
                suggested_layer=_suggest_layer(kind, board_layers),
                priority=_priority(name, kind, refs),
            )
        )
    power_nets.sort(key=lambda n: (-n.priority, n.name))
    return power_nets


def _distance(a: PlacedPart, b: PlacedPart) -> float:
    return ((a.x_mm - b.x_mm) ** 2 + (a.y_mm - b.y_mm) ** 2) ** 0.5


def _span(refs: list[str], placed: dict[str, PlacedPart]) -> float:
    if len(refs) < 2:
        return 0.0
    xs = [placed[ref].x_mm for ref in refs]
    ys = [placed[ref].y_mm for ref in refs]
    return (max(xs) - min(xs)) + (max(ys) - min(ys))


def _ordered_refs(refs: list[str], placed: dict[str, PlacedPart]) -> list[str]:
    remaining = sorted(refs)
    if len(remaining) <= 1:
        return remaining

    current = min(
        remaining,
        key=lambda ref: (placed[ref].x_mm, placed[ref].y_mm, ref),
    )
    ordered = [current]
    remaining.remove(current)

    while remaining:
        current = min(
            remaining,
            key=lambda ref: (_distance(placed[ordered[-1]], placed[ref]), ref),
        )
        ordered.append(current)
        remaining.remove(current)
    return ordered


def _route_intents(
    power_nets: list[PowerNet],
    placed_parts: list[PlacedPart],
    board_layers: int,
) -> list[PowerRouteIntent]:
    placed = {pp.ref: pp for pp in placed_parts}
    intents: list[PowerRouteIntent] = []
    for net in power_nets:
        refs = [ref for ref in net.refs if ref in placed]
        ordered_refs = _ordered_refs(refs, placed)
        intents.append(
            PowerRouteIntent(
                net_name=net.name,
                strategy=_strategy(net, board_layers, len(refs)),
                layer=net.suggested_layer,
                width_mm=net.suggested_width_mm,
                priority=net.priority,
                refs=refs,
                ordered_refs=ordered_refs,
                span_mm=_span(refs, placed),
            )
        )
    intents.sort(key=lambda intent: (-intent.priority, intent.net_name))
    return intents


def _corridors(
    route_intents: list[PowerRouteIntent],
    placed_parts: list[PlacedPart],
) -> list[PowerCorridor]:
    placed = {pp.ref: pp for pp in placed_parts}
    corridors: list[PowerCorridor] = []
    for intent in route_intents:
        refs = [ref for ref in intent.ordered_refs if ref in placed]
        if len(refs) < 2 or intent.priority < 80:
            continue
        xs = [placed[ref].x_mm for ref in refs]
        ys = [placed[ref].y_mm for ref in refs]
        margin = max(2.0, intent.width_mm * 3.0)
        corridors.append(
            PowerCorridor(
                net_name=intent.net_name,
                layer=intent.layer,
                width_mm=intent.width_mm,
                priority=intent.priority,
                x_min=min(xs) - margin,
                y_min=min(ys) - margin,
                x_max=max(xs) + margin,
                y_max=max(ys) + margin,
                refs=refs,
            )
        )
    corridors.sort(key=lambda corridor: (-corridor.priority, corridor.net_name))
    return corridors


def _power_warnings(
    circuit,
    placed_parts: list[PlacedPart],
    power_nets: list[PowerNet],
) -> list[str]:
    placed = {pp.ref: pp for pp in placed_parts}
    roles = classify_parts(circuit)
    warnings: list[str] = []

    for net in power_nets:
        unplaced_refs = [ref for ref in net.refs if ref not in placed]
        if unplaced_refs:
            ref_list = ", ".join(unplaced_refs[:8])
            warnings.append(
                f"{net.name}: power net has unplaced refs: {ref_list}"
            )

        placed_refs = [ref for ref in net.refs if ref in placed]
        if len(placed_refs) >= 2:
            xs = [placed[ref].x_mm for ref in placed_refs]
            ys = [placed[ref].y_mm for ref in placed_refs]
            hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
            if net.kind == "supply" and hpwl > 80.0:
                warnings.append(
                    f"{net.name}: supply rail spans {hpwl:.1f}mm before routing"
                )

    regulator_refs = [
        ref for ref, role in roles.items() if role.role == "regulator" and ref in placed
    ]
    decap_refs = [
        ref
        for ref, role in roles.items()
        if role.role == "decoupling_cap" and ref in placed
    ]
    for regulator_ref in regulator_refs:
        close_decaps = [
            ref
            for ref in decap_refs
            if _distance(placed[regulator_ref], placed[ref]) <= 5.0
        ]
        if not close_decaps:
            warnings.append(
                f"{regulator_ref}: regulator has no decoupling cap within 5mm"
            )

    return warnings


def plan_power_routes(
    circuit,
    placed_parts: list[PlacedPart],
    board_layers: int = 2,
) -> PowerRoutePlan:
    power_nets = identify_power_nets(circuit, board_layers=board_layers)
    route_intents = _route_intents(power_nets, placed_parts, board_layers)
    corridors = _corridors(route_intents, placed_parts)
    warnings = _power_warnings(circuit, placed_parts, power_nets)
    return PowerRoutePlan(
        nets=power_nets,
        route_intents=route_intents,
        corridors=corridors,
        warnings=warnings,
    )
