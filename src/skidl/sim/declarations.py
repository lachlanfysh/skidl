"""Explicit simulation harness declarations.

User-facing functions that declare sources, loads, probes, and assertions
for simulation without mutating schematic or PCB output.  Declarations
are stored on ``circuit.sim_harness`` and consumed by the planner and
harness builder.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DeclaredSource:
    net_name: str
    voltage: float
    ref: str = ""
    provenance: str = "user"


@dataclass
class DeclaredLoad:
    net_name: str
    resistance: float | None = None
    current: float | None = None
    ref: str = ""
    provenance: str = "user"


@dataclass
class DeclaredProbe:
    net_name: str
    kind: str = "voltage"
    provenance: str = "user"


@dataclass
class RailAssertion:
    net_name: str
    nominal: float
    tolerance: float = 0.05
    provenance: str = "user"


@dataclass
class RatioAssertion:
    output_net: str
    input_net: str
    ratio: float
    tolerance: float = 0.05
    provenance: str = "user"


@dataclass
class SimHarness:
    sources: list[DeclaredSource] = field(default_factory=list)
    loads: list[DeclaredLoad] = field(default_factory=list)
    probes: list[DeclaredProbe] = field(default_factory=list)
    rail_assertions: list[RailAssertion] = field(default_factory=list)
    ratio_assertions: list[RatioAssertion] = field(default_factory=list)


def _resolve_net_name(net_or_name) -> str:
    if isinstance(net_or_name, str):
        return net_or_name
    return str(getattr(net_or_name, "name", "") or "")


def _get_harness(circuit=None) -> SimHarness:
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit
    harness = getattr(circuit, "sim_harness", None)
    if harness is None:
        harness = SimHarness()
        circuit.sim_harness = harness
    return harness


def sim_source(net_or_name, voltage, ref=None, provenance="user", circuit=None):
    """Declare a shadow voltage source for simulation.

    Does not add a part to the schematic or PCB — only affects the
    simulation harness.
    """
    harness = _get_harness(circuit)
    harness.sources.append(DeclaredSource(
        net_name=_resolve_net_name(net_or_name),
        voltage=voltage,
        ref=ref or "",
        provenance=provenance,
    ))


def sim_load(net_or_name, resistance=None, current=None, ref=None,
             provenance="user", circuit=None):
    """Declare a simulation load on a net.

    Specify *resistance* (ohms, adds a resistor to GND) or *current*
    (amps, adds a current sink to GND).  Does not add a part to the
    schematic or PCB.
    """
    if resistance is None and current is None:
        raise ValueError("sim_load requires resistance or current")
    harness = _get_harness(circuit)
    harness.loads.append(DeclaredLoad(
        net_name=_resolve_net_name(net_or_name),
        resistance=resistance,
        current=current,
        ref=ref or "",
        provenance=provenance,
    ))


def sim_probe(net_or_name, kind="voltage", provenance="user", circuit=None):
    """Declare a simulation probe point.

    The probe is recorded in the plan and its value reported in the
    simulation report.
    """
    harness = _get_harness(circuit)
    harness.probes.append(DeclaredProbe(
        net_name=_resolve_net_name(net_or_name),
        kind=kind,
        provenance=provenance,
    ))


def sim_assert_rail(net_or_name, nominal, tolerance=0.05, provenance="user",
                    circuit=None):
    """Assert that a rail should measure *nominal* volts ± *tolerance* (fraction).

    Only executes when a source exists on the rail (either a circuit
    part or a ``sim_source()`` declaration).
    """
    harness = _get_harness(circuit)
    harness.rail_assertions.append(RailAssertion(
        net_name=_resolve_net_name(net_or_name),
        nominal=nominal,
        tolerance=tolerance,
        provenance=provenance,
    ))


def sim_assert_node_ratio(output, input, ratio, tolerance=0.05,
                          provenance="user", circuit=None):
    """Assert that V(output)/V(input) ≈ *ratio* ± *tolerance*."""
    harness = _get_harness(circuit)
    harness.ratio_assertions.append(RatioAssertion(
        output_net=_resolve_net_name(output),
        input_net=_resolve_net_name(input),
        ratio=ratio,
        tolerance=tolerance,
        provenance=provenance,
    ))
