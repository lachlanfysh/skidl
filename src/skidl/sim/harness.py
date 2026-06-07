"""Non-mutating SPICE simulation harness.

Builds an InSpice Circuit from auto-ready parts (2-pin R/C/L with
parseable values) without modifying user circuit objects.  Parts that
already have ``part.pyspice`` are included via InSpice's programmatic
API using the stored element type and value.
"""
from __future__ import annotations

import re

from .registry import ModelSource, parse_value

_GND_RE = re.compile(r"^(GND|VSS|DGND|AGND|GNDA|GNDD|0)$", re.IGNORECASE)
_NODE_CLEAN_RE = re.compile(r"[^a-zA-Z0-9_]")


def _safe_node_name(name: str) -> str:
    cleaned = _NODE_CLEAN_RE.sub("_", name)
    if not cleaned or cleaned[0].isdigit():
        cleaned = "n_" + cleaned
    return cleaned.lower()


def _node_for_pin(pin, net_nodes: dict[int, object]):
    net = getattr(pin, "net", None)
    if net is None:
        return None
    return net_nodes.get(id(net))


def _map_nets(circuit, spice_gnd) -> dict[int, object]:
    net_nodes: dict[int, object] = {}
    for net in circuit.get_nets():
        name = str(getattr(net, "name", "") or "")
        if _GND_RE.match(name):
            net_nodes[id(net)] = spice_gnd
        elif name:
            net_nodes[id(net)] = _safe_node_name(name)
        else:
            net_nodes[id(net)] = f"n_{id(net)}"
    for part in circuit.parts:
        for pin in getattr(part, "pins", []):
            net = getattr(pin, "net", None)
            if net is not None and id(net) not in net_nodes:
                name = str(getattr(net, "name", "") or "")
                if _GND_RE.match(name):
                    net_nodes[id(net)] = spice_gnd
                elif name:
                    net_nodes[id(net)] = _safe_node_name(name)
                else:
                    net_nodes[id(net)] = f"n_{id(net)}"
    return net_nodes


def _ref_suffix(ref: str, element: str) -> str:
    if ref.upper().startswith(element.upper()):
        return ref[len(element):] or "0"
    return ref


def build_simulation_circuit(plan, circuit):
    """Build an InSpice Circuit for simulation without mutating user parts.

    Returns an InSpice ``Circuit`` object ready for ``.simulator()``.
    Only includes parts that are ``spice_ready`` in the plan.
    """
    from InSpice.Spice.Netlist import Circuit as SpiceCircuit

    ckt = SpiceCircuit("simulation_erc")
    net_nodes = _map_nets(circuit, ckt.gnd)
    part_map = {p.ref: p for p in circuit.parts}
    added = set()

    for entry in plan.eligible_parts:
        if not entry.spice_ready:
            continue
        part = part_map.get(entry.ref)
        if part is None:
            continue
        pins = getattr(part, "pins", [])
        if len(pins) != 2:
            continue

        n1 = _node_for_pin(pins[0], net_nodes)
        n2 = _node_for_pin(pins[1], net_nodes)
        if n1 is None or n2 is None:
            continue

        suffix = _ref_suffix(entry.ref, entry.spice_element)

        if entry.source == ModelSource.BUILTIN_PRIMITIVE:
            if entry.value is None or entry.value <= 0:
                continue
            if entry.spice_element == "R":
                ckt.R(suffix, n1, n2, entry.value)
            elif entry.spice_element == "C":
                ckt.C(suffix, n1, n2, entry.value)
            elif entry.spice_element == "L":
                ckt.L(suffix, n1, n2, entry.value)
            elif entry.spice_element == "V":
                ckt.V(suffix, n1, n2, entry.value)
            elif entry.spice_element == "I":
                ckt.I(suffix, n1, n2, entry.value)
            else:
                continue
            added.add(entry.ref)

        elif entry.source in (ModelSource.PYSPICE_ATTRIBUTE,
                              ModelSource.CONVERT_FOR_SPICE):
            val = parse_value(getattr(part, "dc_value", None))
            if val is None:
                val = parse_value(getattr(part, "value", None))
            if val is None:
                continue
            elem = entry.spice_element
            if elem == "R":
                ckt.R(suffix, n1, n2, val)
            elif elem == "C":
                ckt.C(suffix, n1, n2, val)
            elif elem == "L":
                ckt.L(suffix, n1, n2, val)
            elif elem == "V":
                ckt.V(suffix, n1, n2, val)
            elif elem == "I":
                ckt.I(suffix, n1, n2, val)
            else:
                continue
            added.add(entry.ref)

    return ckt, added
