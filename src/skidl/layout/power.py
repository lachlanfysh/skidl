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
class PowerChain:
    source_ref: str
    source_net: str
    protection_refs: list[str] = field(default_factory=list)
    converter_refs: list[str] = field(default_factory=list)
    storage_refs: list[str] = field(default_factory=list)
    load_refs: list[str] = field(default_factory=list)
    output_nets: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)

    @property
    def ordered_refs(self) -> list[str]:
        ordered: list[str] = []
        for ref in [
            self.source_ref,
            *self.protection_refs,
            *self.converter_refs,
            *self.storage_refs,
            *self.load_refs,
        ]:
            if ref and ref not in ordered:
                ordered.append(ref)
        return ordered


@dataclass
class PowerTopology:
    chains: list[PowerChain] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def refs(self) -> list[str]:
        refs: list[str] = []
        for chain in self.chains:
            for ref in chain.ordered_refs:
                if ref not in refs:
                    refs.append(ref)
        return refs

    def summary(self) -> str:
        lines = ["Power topology:"]
        if not self.chains:
            lines.append("  no directed power chains inferred")
        for chain in self.chains:
            refs = " -> ".join(chain.ordered_refs[:10])
            outputs = ", ".join(chain.output_nets[:6])
            lines.append(
                f"  {chain.source_net}: source {chain.source_ref}"
                + (f" to {outputs}" if outputs else "")
            )
            if refs:
                lines.append(f"    order: {refs}")
            for reason in chain.reasons[:4]:
                lines.append(f"    reason: {reason}")
        if self.warnings:
            lines.append("Warnings:")
            for warning in self.warnings[:20]:
                lines.append(f"  {warning}")
        return "\n".join(lines)


@dataclass
class PowerRoutePlan:
    nets: list[PowerNet] = field(default_factory=list)
    route_intents: list[PowerRouteIntent] = field(default_factory=list)
    corridors: list[PowerCorridor] = field(default_factory=list)
    topology: PowerTopology = field(default_factory=PowerTopology)
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
        if self.topology.chains:
            lines.append(self.topology.summary())
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


def _part_nets(part) -> list[str]:
    nets: list[str] = []
    for pin in getattr(part, "pins", []) or []:
        name = getattr(getattr(pin, "net", None), "name", None)
        if name and str(name) not in nets:
            nets.append(str(name))
    return nets


def _part_text(part) -> str:
    chunks = [
        getattr(part, "ref", ""),
        getattr(part, "name", ""),
        getattr(part, "value", ""),
        getattr(part, "foot", ""),
        getattr(part, "footprint", ""),
        getattr(part, "description", ""),
    ]
    return " ".join(str(chunk or "") for chunk in chunks).lower()


def _is_source_part(part, role: str, nets: list[str]) -> bool:
    text = _part_text(part)
    source_like = any(
        token in text
        for token in ("usb", "battery", "batt", "barrel", "power", "jst", "terminal")
    )
    if role == "connector" and any(HIGH_CURRENT_NET_RE.match(net) for net in nets):
        return True
    return source_like and any(
        POWER_NET_RE.match(net) or HIGH_CURRENT_NET_RE.match(net) for net in nets
    )


def _is_protection_part(part, role: str) -> bool:
    text = _part_text(part)
    return role in {"diode", "fuse"} or any(
        token in text
        for token in ("fuse", "polyfuse", "tvs", "esd", "reverse", "protection")
    )


def _is_storage_part(part, nets: list[str]) -> bool:
    ref = str(getattr(part, "ref", "") or "").upper()
    if not ref.startswith("C"):
        return False
    return any(POWER_NET_RE.match(net) for net in nets) and any(
        GND_NET_RE.match(net) for net in nets
    )


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


def infer_power_topology(circuit) -> PowerTopology:
    """Infer coarse source/protection/conversion/storage/load power chains."""
    if circuit is None:
        return PowerTopology()

    roles = classify_parts(circuit)
    parts_by_ref = {part.ref: part for part in circuit.parts}
    nets_by_ref = {part.ref: _part_nets(part) for part in circuit.parts}
    refs_by_net: dict[str, list[str]] = {}
    for ref, nets in nets_by_ref.items():
        for net in nets:
            refs_by_net.setdefault(net, []).append(ref)

    source_refs: list[str] = []
    for ref, part in parts_by_ref.items():
        role = roles.get(ref)
        role_name = role.role if role is not None else "unknown"
        if _is_source_part(part, role_name, nets_by_ref.get(ref, [])):
            source_refs.append(ref)

    chains: list[PowerChain] = []
    warnings: list[str] = []
    for source_ref in sorted(source_refs):
        source_nets = [
            net
            for net in nets_by_ref.get(source_ref, [])
            if POWER_NET_RE.match(net) or HIGH_CURRENT_NET_RE.match(net)
        ]
        source_nets = [net for net in source_nets if not GND_NET_RE.match(net)]
        if not source_nets:
            continue

        for source_net in sorted(source_nets):
            connected_refs = [
                ref for ref in refs_by_net.get(source_net, []) if ref != source_ref
            ]
            protection_refs = sorted(
                ref
                for ref in connected_refs
                if _is_protection_part(
                    parts_by_ref[ref],
                    roles.get(ref).role if roles.get(ref) is not None else "unknown",
                )
            )
            converter_refs = sorted(
                ref
                for ref in connected_refs
                if roles.get(ref) is not None and roles[ref].role == "regulator"
            )

            output_nets: list[str] = []
            for converter_ref in converter_refs:
                for net in nets_by_ref.get(converter_ref, []):
                    if (
                        net != source_net
                        and not GND_NET_RE.match(net)
                        and (POWER_NET_RE.match(net) or HIGH_CURRENT_NET_RE.match(net))
                        and net not in output_nets
                    ):
                        output_nets.append(net)
            if not output_nets:
                output_nets = [source_net]

            storage_refs = sorted(
                ref
                for ref, nets in nets_by_ref.items()
                if ref != source_ref
                and _is_storage_part(parts_by_ref[ref], nets)
                and any(net in output_nets or net == source_net for net in nets)
            )
            infrastructure_refs = {
                source_ref,
                *protection_refs,
                *converter_refs,
                *storage_refs,
            }
            load_refs = sorted(
                ref
                for ref, nets in nets_by_ref.items()
                if ref not in infrastructure_refs
                and any(net in output_nets for net in nets)
                and roles.get(ref) is not None
                and roles[ref].role in {"ic", "connector", "unknown"}
            )

            reasons = [
                f"{source_ref} is connector-like source on {source_net}",
            ]
            if converter_refs:
                reasons.append(
                    "conversion refs: " + ", ".join(converter_refs[:6])
                )
            if storage_refs:
                reasons.append("storage refs: " + ", ".join(storage_refs[:6]))
            if load_refs:
                reasons.append("load refs: " + ", ".join(load_refs[:6]))

            chains.append(
                PowerChain(
                    source_ref=source_ref,
                    source_net=source_net,
                    protection_refs=protection_refs,
                    converter_refs=converter_refs,
                    storage_refs=storage_refs,
                    load_refs=load_refs,
                    output_nets=sorted(output_nets),
                    reasons=reasons,
                )
            )

    if not chains:
        high_current_nets = [
            net.name
            for net in identify_power_nets(circuit)
            if HIGH_CURRENT_NET_RE.match(net.name)
        ]
        if high_current_nets:
            warnings.append(
                "high-current nets present but no connector-like source was inferred: "
                + ", ".join(high_current_nets[:6])
            )
    return PowerTopology(chains=chains, warnings=warnings)


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
    topology = infer_power_topology(circuit)
    route_intents = _route_intents(power_nets, placed_parts, board_layers)
    corridors = _corridors(route_intents, placed_parts)
    warnings = _power_warnings(circuit, placed_parts, power_nets)
    warnings.extend(topology.warnings)
    return PowerRoutePlan(
        nets=power_nets,
        route_intents=route_intents,
        corridors=corridors,
        topology=topology,
        warnings=warnings,
    )
