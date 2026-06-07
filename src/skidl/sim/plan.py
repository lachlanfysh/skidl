from __future__ import annotations

import re
from dataclasses import dataclass, field

from .registry import ModelRegistry, ModelEntry, parse_value as _parse_value
from .report import (
    FindingSeverity,
    SimulationCheck,
    SimulationFinding,
    SimulationReport,
)


@dataclass
class ProbeSpec:
    net_name: str
    probe_type: str = "voltage"


@dataclass
class SourceSpec:
    ref: str
    net_name: str
    source_type: str = "dc"
    value: float | None = None
    unit: str = "V"


@dataclass
class CheckSpec:
    name: str
    check_type: str
    refs: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    expected: float | None = None
    tolerance: float | None = None
    unit: str = "V"
    model_provenance: str = ""


@dataclass
class SimulationPlan:
    profile: str
    eligible_parts: list[ModelEntry] = field(default_factory=list)
    skipped_parts: list[str] = field(default_factory=list)
    sources: list[SourceSpec] = field(default_factory=list)
    probes: list[ProbeSpec] = field(default_factory=list)
    checks: list[CheckSpec] = field(default_factory=list)
    findings: list[SimulationFinding] = field(default_factory=list)
    executable: bool = False

    def summary(self) -> str:
        lines = [f"Simulation plan (profile={self.profile}):"]
        lines.append(f"  Eligible parts: {len(self.eligible_parts)}")
        lines.append(f"  Skipped parts: {len(self.skipped_parts)}")
        lines.append(f"  Sources: {len(self.sources)}")
        lines.append(f"  Probes: {len(self.probes)}")
        lines.append(f"  Checks: {len(self.checks)}")
        lines.append(f"  Executable: {self.executable}")
        if self.findings:
            lines.append(f"  Findings: {len(self.findings)}")
        return "\n".join(lines)


_GND_RE = re.compile(r"^(GND|VSS|DGND|AGND|GNDA|GNDD|0)$", re.IGNORECASE)
_POWER_RE = re.compile(
    r"^(VCC|VDD|VDDA|DVDD|AVDD|IOVDD|VBUS|VIN|VOUT|VBAT|VREF"
    r"|\+\d+(\.\d+)?V)$",
    re.IGNORECASE,
)



def _net_name(net) -> str:
    return str(getattr(net, "name", "") or "")


def _net_names_for_part(part) -> list[str]:
    names = []
    for pin in getattr(part, "pins", []):
        net = getattr(pin, "net", None)
        if net is not None:
            name = _net_name(net)
            if name and name not in names:
                names.append(name)
    return names


def _find_ground_nets(circuit) -> list[str]:
    ground_nets = []
    for net in circuit.get_nets():
        name = _net_name(net)
        if _GND_RE.match(name):
            ground_nets.append(name)
    return ground_nets


def _find_power_nets(circuit) -> list[str]:
    power_nets = []
    for net in circuit.get_nets():
        name = _net_name(net)
        if _POWER_RE.match(name):
            power_nets.append(name)
    return power_nets


def _find_voltage_sources(circuit, registry: ModelRegistry) -> list[SourceSpec]:
    sources = []
    for part in circuit.parts:
        entry = registry.get(part.ref)
        if entry is None:
            continue
        if entry.spice_element != "V":
            continue
        net_names = _net_names_for_part(part)
        value = _parse_value(getattr(part, "dc_value", None))
        if value is None:
            value = _parse_value(getattr(part, "value", "") or "")
        for nn in net_names:
            if not _GND_RE.match(nn):
                sources.append(SourceSpec(
                    ref=part.ref,
                    net_name=nn,
                    source_type="dc",
                    value=value,
                    unit="V",
                ))
                break
    return sources


def _find_dividers(circuit, registry: ModelRegistry) -> list[CheckSpec]:
    checks = []
    seen_pairs: set[frozenset[str]] = set()
    for part in circuit.parts:
        entry = registry.get(part.ref)
        if entry is None or entry.spice_element != "R":
            continue
        nets = _net_names_for_part(part)
        if len(nets) < 2:
            continue
        for other in circuit.parts:
            if other.ref == part.ref:
                continue
            pair = frozenset({part.ref, other.ref})
            if pair in seen_pairs:
                continue
            other_entry = registry.get(other.ref)
            if other_entry is None or other_entry.spice_element != "R":
                continue
            other_nets = _net_names_for_part(other)
            shared = set(nets) & set(other_nets)
            shared_signal = [n for n in shared if not _GND_RE.match(n) and not _POWER_RE.match(n)]
            if not shared_signal:
                continue
            has_power = any(_POWER_RE.match(n) for n in nets + other_nets)
            has_gnd = any(_GND_RE.match(n) for n in nets + other_nets)
            if has_power and has_gnd:
                r1_val = _parse_value(getattr(part, "value", "") or "")
                r2_val = _parse_value(getattr(other, "value", "") or "")
                if r1_val and r2_val and r1_val > 0 and r2_val > 0:
                    top_r = r1_val if any(_POWER_RE.match(n) for n in nets) else r2_val
                    bot_r = r2_val if top_r == r1_val else r1_val
                    ratio = bot_r / (top_r + bot_r)
                    seen_pairs.add(pair)
                    checks.append(CheckSpec(
                        name=f"divider_{part.ref}_{other.ref}_ratio",
                        check_type="divider_ratio",
                        refs=[part.ref, other.ref],
                        nets=list(shared_signal[:1]),
                        expected=ratio,
                        tolerance=0.05,
                        unit="ratio",
                        model_provenance="builtin_primitive R values",
                    ))
                    break
    return checks


def _find_rc_networks(circuit, registry: ModelRegistry) -> list[CheckSpec]:
    checks = []
    for part in circuit.parts:
        entry = registry.get(part.ref)
        if entry is None or entry.spice_element != "R":
            continue
        r_nets = set(_net_names_for_part(part))
        for cap in circuit.parts:
            cap_entry = registry.get(cap.ref)
            if cap_entry is None or cap_entry.spice_element != "C":
                continue
            c_nets = set(_net_names_for_part(cap))
            shared = r_nets & c_nets
            shared_signal = [n for n in shared if not _GND_RE.match(n)]
            if not shared_signal:
                continue
            r_val = _parse_value(getattr(part, "value", "") or "")
            c_val = _parse_value(getattr(cap, "value", "") or "")
            if r_val and c_val and r_val > 0 and c_val > 0:
                tau = r_val * c_val
                checks.append(CheckSpec(
                    name=f"rc_{part.ref}_{cap.ref}_tau",
                    check_type="rc_time_constant",
                    refs=[part.ref, cap.ref],
                    nets=list(shared_signal[:1]),
                    expected=tau,
                    tolerance=0.1,
                    unit="s",
                    model_provenance="builtin_primitive R*C",
                ))
                break
    return checks


def _passive_power_checks(circuit, registry: ModelRegistry) -> list[CheckSpec]:
    checks = []
    for part in circuit.parts:
        entry = registry.get(part.ref)
        if entry is None or entry.spice_element != "R":
            continue
        nets = _net_names_for_part(part)
        power_nets = [n for n in nets if _POWER_RE.match(n)]
        gnd_nets = [n for n in nets if _GND_RE.match(n)]
        if not power_nets or not gnd_nets:
            continue
        r_val = _parse_value(getattr(part, "value", "") or "")
        if r_val and r_val > 0:
            checks.append(CheckSpec(
                name=f"power_{part.ref}",
                check_type="passive_power",
                refs=[part.ref],
                nets=power_nets[:1] + gnd_nets[:1],
                unit="W",
                model_provenance="builtin_primitive R between power and ground",
            ))
    return checks


def plan_simulation(
    circuit=None,
    profile: str = "power_sanity",
) -> SimulationPlan:
    """Build a simulation plan for the given circuit.

    Identifies eligible parts (those with exact SPICE models), required
    sources, measurable probes, and executable checks. Parts without
    exact models are reported as skipped.
    """
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    registry = ModelRegistry()
    registry.build(circuit)

    eligible = list(registry.entries.values())
    unmapped = sorted(registry.unmapped_refs_for(circuit))

    findings: list[SimulationFinding] = []
    ground_nets = _find_ground_nets(circuit)
    power_nets = _find_power_nets(circuit)

    if not ground_nets:
        findings.append(SimulationFinding(
            severity=FindingSeverity.ERROR,
            message="No ground net found — simulation requires a reference node",
            category="missing_ground",
        ))

    for ref in unmapped:
        findings.append(SimulationFinding(
            severity=FindingSeverity.WARNING,
            message=f"No exact SPICE model for {ref} — part will be excluded from simulation",
            refs=[ref],
            category="missing_model",
        ))

    sources = _find_voltage_sources(circuit, registry)
    if not sources and power_nets:
        findings.append(SimulationFinding(
            severity=FindingSeverity.WARNING,
            message="Power nets found but no explicit voltage source — "
                    "cannot determine rail voltages without a source model",
            nets=power_nets[:5],
            category="missing_source",
        ))

    probes = [ProbeSpec(net_name=n) for n in power_nets + ground_nets]

    checks: list[CheckSpec] = []
    if profile == "power_sanity":
        for src in sources:
            if src.value is not None:
                checks.append(CheckSpec(
                    name=f"rail_{src.net_name}_presence",
                    check_type="rail_presence",
                    refs=[src.ref],
                    nets=[src.net_name],
                    expected=src.value,
                    tolerance=0.01,
                    unit="V",
                    model_provenance=f"source {src.ref} value",
                ))
        checks.extend(_find_dividers(circuit, registry))
        checks.extend(_find_rc_networks(circuit, registry))
        checks.extend(_passive_power_checks(circuit, registry))

    not_ready = []
    for entry in eligible:
        if not entry.spice_ready:
            not_ready.append(entry.ref)
            findings.append(SimulationFinding(
                severity=FindingSeverity.WARNING,
                message=f"{entry.ref} identified as {entry.spice_element} but lacks "
                        f"SPICE conversion — use convert_for_spice() or SPICE library parts",
                refs=[entry.ref],
                category="not_spice_ready",
            ))

    has_ground = len(ground_nets) > 0
    has_source = len(sources) > 0
    all_checks_have_models = all(
        registry.has_model(ref) for c in checks for ref in c.refs
    )
    all_spice_ready = len(not_ready) == 0
    executable = (
        has_ground and has_source and all_checks_have_models
        and len(unmapped) == 0 and all_spice_ready
    )

    return SimulationPlan(
        profile=profile,
        eligible_parts=eligible,
        skipped_parts=unmapped,
        sources=sources,
        probes=probes,
        checks=checks,
        findings=findings,
        executable=executable,
    )
