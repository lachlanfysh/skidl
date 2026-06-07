"""Rail inventory and power tree analysis.

Maps source rails to derived rails and loads. Identifies regulators,
converters, ferrites, fuses, and other power-path components. Reports
rails with loads but no source, regulators with missing I/O caps, and
ambiguous rail names.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..pin import pin_types


POWER_NET_RE = re.compile(
    r"^("
    r"VCC|AVCC|DVCC|IOVCC|"
    r"VDD|VDDA|VDDD|AVDD|DVDD|IOVDD|"
    r"VBUS|VIN|VOUT|VRAW|VBAT|BAT|BATT|VREF|V\d+|"
    r"[+-]?\d+(?:V\d*|\.\d+V)"
    r")$",
    re.IGNORECASE,
)
GND_NET_RE = re.compile(r"^(GND|VSS|DGND|AGND|GNDA|GNDD|0)$", re.IGNORECASE)

_REGULATOR_RE = re.compile(
    r"(regulator|ldo|buck|boost|dcdc|dc-dc|converter|linear)",
    re.IGNORECASE,
)
_FERRITE_RE = re.compile(r"(ferrite|bead)", re.IGNORECASE)
_FUSE_RE = re.compile(r"(fuse|ptc|polyfuse)", re.IGNORECASE)


@dataclass
class PowerNode:
    """A node in the power tree — a rail or power-path component."""
    name: str
    node_type: str  # "rail", "source", "regulator", "ferrite", "fuse", "switch", "jumper"
    ref: str = ""
    voltage: float | None = None
    provenance: str = ""
    children: list[str] = field(default_factory=list)
    parents: list[str] = field(default_factory=list)


@dataclass
class RailInfo:
    name: str
    sources: list[str] = field(default_factory=list)  # refs of source-like parts
    loads: list[str] = field(default_factory=list)  # refs of parts consuming from this rail
    caps: list[str] = field(default_factory=list)  # refs of caps on this rail
    voltage: float | None = None
    provenance: str = ""


@dataclass
class PowerTreeFinding:
    severity: str  # "error", "warning", "info"
    message: str
    rail: str = ""
    ref: str = ""
    category: str = ""


@dataclass
class PowerTreeReport:
    rails: list[RailInfo] = field(default_factory=list)
    nodes: list[PowerNode] = field(default_factory=list)
    edges: list[tuple[str, str]] = field(default_factory=list)  # (from_rail, to_rail)
    findings: list[PowerTreeFinding] = field(default_factory=list)

    def summary(self) -> str:
        sources = [n for n in self.nodes if n.node_type == "source"]
        regs = [n for n in self.nodes if n.node_type == "regulator"]
        errors = [f for f in self.findings if f.severity == "error"]
        warnings = [f for f in self.findings if f.severity == "warning"]
        lines = [
            f"Power tree: {len(self.rails)} rails, {len(sources)} sources, "
            f"{len(regs)} regulators",
            f"  Edges: {len(self.edges)}",
            f"  Findings: {len(errors)} errors, {len(warnings)} warnings",
        ]
        return "\n".join(lines)


def _net_name(net) -> str:
    return str(getattr(net, "name", "") or "")


def _pin_net_name(pin) -> str:
    net = getattr(pin, "net", None)
    if net is None:
        return ""
    return _net_name(net)


def _is_power_net(name: str) -> bool:
    return bool(POWER_NET_RE.match(name))


def _is_ground_net(name: str) -> bool:
    return bool(GND_NET_RE.match(name))


def _ref_prefix(ref: str) -> str:
    m = re.match(r"[A-Za-z]+", ref)
    return m.group(0).upper() if m else ""


def _part_text(part) -> str:
    chunks = [
        getattr(part, "ref", ""),
        getattr(part, "name", ""),
        getattr(part, "value", ""),
        getattr(part, "description", ""),
    ]
    return " ".join(str(c or "") for c in chunks).lower()


def _classify_power_role(part) -> str:
    """Classify a part's role in the power tree."""
    prefix = _ref_prefix(part.ref)
    text = _part_text(part)
    pins = getattr(part, "pins", [])

    if prefix in {"BT", "BAT"}:
        return "source"

    if _REGULATOR_RE.search(text):
        return "regulator"

    if prefix == "FB" or _FERRITE_RE.search(text):
        return "ferrite"

    if prefix == "F" or _FUSE_RE.search(text):
        return "fuse"

    if prefix in {"SW"} or "switch" in text:
        return "switch"

    if prefix in {"JP"} or "jumper" in text:
        return "jumper"

    if prefix == "U" and len(pins) > 2:
        pin_nets = [_pin_net_name(p) for p in pins]
        power_nets = [n for n in pin_nets if _is_power_net(n)]
        if len(set(power_nets)) >= 2:
            has_in = any(
                getattr(p, "func", None) == pin_types.PWRIN
                for p in pins
                if _is_power_net(_pin_net_name(p))
            )
            has_out = any(
                getattr(p, "func", None) == pin_types.PWROUT
                for p in pins
                if _is_power_net(_pin_net_name(p))
            )
            if has_in and has_out:
                return "regulator"

    return ""


def _find_regulator_io(part) -> tuple[list[str], list[str]]:
    """Return (input_rails, output_rails) for a regulator."""
    input_rails = []
    output_rails = []
    pins = getattr(part, "pins", [])

    for pin in pins:
        net = _pin_net_name(pin)
        if not net or not _is_power_net(net) or _is_ground_net(net):
            continue
        func = getattr(pin, "func", None)
        name = str(getattr(pin, "name", "") or "").upper()

        if func == pin_types.PWROUT or name in {"VOUT", "OUT"}:
            if net not in output_rails:
                output_rails.append(net)
        elif func == pin_types.PWRIN or name in {"VIN", "IN", "EN"}:
            if name != "EN" and net not in input_rails:
                input_rails.append(net)
            elif name == "EN":
                pass  # EN pin is not an input rail
        else:
            if net not in input_rails:
                input_rails.append(net)

    return input_rails, output_rails


def _find_passthrough_rails(part) -> tuple[list[str], list[str]]:
    """Return (input_rails, output_rails) for ferrite/fuse/switch/jumper."""
    pins = getattr(part, "pins", [])
    power_nets = []
    for pin in pins:
        net = _pin_net_name(pin)
        if net and _is_power_net(net) and not _is_ground_net(net):
            if net not in power_nets:
                power_nets.append(net)

    if len(power_nets) == 2:
        return [power_nets[0]], [power_nets[1]]
    return power_nets[:1], power_nets[1:]


def analyze_power_tree(circuit=None) -> PowerTreeReport:
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    rails: dict[str, RailInfo] = {}
    nodes: list[PowerNode] = []
    edges: list[tuple[str, str]] = []
    findings: list[PowerTreeFinding] = []

    # Collect harness-declared sources as authoritative roots
    sim_harness = getattr(circuit, "sim_harness", None)
    if sim_harness is not None:
        for src in sim_harness.sources:
            rail_name = src.net_name
            if rail_name not in rails:
                rails[rail_name] = RailInfo(
                    name=rail_name,
                    voltage=src.voltage,
                    provenance=f"sim_source({rail_name!r}, voltage={src.voltage})",
                )
            else:
                rails[rail_name].voltage = src.voltage
                rails[rail_name].provenance = (
                    f"sim_source({rail_name!r}, voltage={src.voltage})"
                )
            rails[rail_name].sources.append(f"__sim_{rail_name}")
            nodes.append(PowerNode(
                name=f"sim_source_{rail_name}",
                node_type="source",
                ref=f"__sim_{rail_name}",
                voltage=src.voltage,
                provenance=f"sim_source({rail_name!r}, voltage={src.voltage})",
                children=[rail_name],
            ))

    # Scan all parts
    for part in circuit.parts:
        prefix = _ref_prefix(part.ref)
        role = _classify_power_role(part)
        pins = getattr(part, "pins", [])

        # Discover which rails this part touches
        part_power_nets = set()
        part_gnd_nets = set()
        for pin in pins:
            net = _pin_net_name(pin)
            if _is_power_net(net):
                part_power_nets.add(net)
                if net not in rails:
                    rails[net] = RailInfo(name=net)
            if _is_ground_net(net):
                part_gnd_nets.add(net)

        if role == "source":
            for net in part_power_nets:
                rails[net].sources.append(part.ref)
            nodes.append(PowerNode(
                name=part.ref,
                node_type="source",
                ref=part.ref,
                children=list(part_power_nets),
            ))

        elif role == "regulator":
            in_rails, out_rails = _find_regulator_io(part)
            for r in in_rails:
                if r not in rails:
                    rails[r] = RailInfo(name=r)
            for r in out_rails:
                if r not in rails:
                    rails[r] = RailInfo(name=r)
                rails[r].sources.append(part.ref)

            for ir in in_rails:
                for orr in out_rails:
                    edges.append((ir, orr))

            nodes.append(PowerNode(
                name=part.ref,
                node_type="regulator",
                ref=part.ref,
                parents=in_rails,
                children=out_rails,
            ))

        elif role in ("ferrite", "fuse", "switch", "jumper"):
            in_r, out_r = _find_passthrough_rails(part)
            for ir in in_r:
                for orr in out_r:
                    edges.append((ir, orr))
            nodes.append(PowerNode(
                name=part.ref,
                node_type=role,
                ref=part.ref,
                parents=in_r,
                children=out_r,
            ))

        else:
            # Regular part — record as load on its power rails
            if prefix == "C" and len(pins) == 2:
                for net in part_power_nets:
                    rails[net].caps.append(part.ref)
            elif part_power_nets and prefix not in {"R", "L", "D"}:
                for net in part_power_nets:
                    if part.ref not in rails[net].sources:
                        rails[net].loads.append(part.ref)

    # Generate findings
    for rail_name, rail in rails.items():
        if rail.loads and not rail.sources:
            # Check if reachable via edges from a sourced rail
            reachable = _is_reachable_from_source(rail_name, rails, edges)
            if not reachable:
                findings.append(PowerTreeFinding(
                    severity="warning",
                    message=f"Rail {rail_name} has {len(rail.loads)} load(s) "
                            f"but no source or upstream regulator",
                    rail=rail_name,
                    category="missing_source",
                ))

    # Check regulators for missing caps
    for node in nodes:
        if node.node_type != "regulator":
            continue
        for ir in node.parents:
            rail = rails.get(ir)
            if rail and not rail.caps:
                findings.append(PowerTreeFinding(
                    severity="warning",
                    message=f"Regulator {node.ref} input rail {ir} has no decoupling cap",
                    rail=ir,
                    ref=node.ref,
                    category="regulator_missing_input_cap",
                ))
        for orr in node.children:
            rail = rails.get(orr)
            if rail and not rail.caps:
                findings.append(PowerTreeFinding(
                    severity="warning",
                    message=f"Regulator {node.ref} output rail {orr} has no decoupling cap",
                    rail=orr,
                    ref=node.ref,
                    category="regulator_missing_output_cap",
                ))

    return PowerTreeReport(
        rails=list(rails.values()),
        nodes=nodes,
        edges=edges,
        findings=findings,
    )


def _is_reachable_from_source(
    rail_name: str,
    rails: dict[str, RailInfo],
    edges: list[tuple[str, str]],
) -> bool:
    """BFS from sourced rails through edges to see if rail_name is reachable."""
    sourced = {name for name, rail in rails.items() if rail.sources}
    if rail_name in sourced:
        return True

    adj: dict[str, list[str]] = {}
    for a, b in edges:
        adj.setdefault(a, []).append(b)

    visited = set()
    queue = list(sourced)
    while queue:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        if current == rail_name:
            return True
        for neighbor in adj.get(current, []):
            if neighbor not in visited:
                queue.append(neighbor)

    return False
