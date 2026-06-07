"""Rail sanity execution — static operating-point checks.

Uses declared sources, harness assertions, and auto-primitive resistor
values to compute rail voltages, resistor current/power, and evaluate
rail and ratio assertions. No SPICE simulation required — this is
pure static analysis from declared voltages and component values.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from .registry import ModelRegistry, parse_value
from .report import FindingSeverity, SimulationFinding

_GND_RE = re.compile(r"^(GND|VSS|DGND|AGND|GNDA|GNDD|0)$", re.IGNORECASE)


@dataclass
class RailStatus:
    net_name: str
    voltage: float | None = None
    voltage_source: str = ""
    checked: bool = False
    skipped_reason: str = ""


@dataclass
class ResistorPowerCheck:
    ref: str
    resistance: float
    net_a: str
    net_b: str
    voltage_a: float | None = None
    voltage_b: float | None = None
    voltage_across: float | None = None
    current_a: float | None = None
    power_w: float | None = None
    skipped_reason: str = ""


@dataclass
class RailAssertionResult:
    net_name: str
    nominal: float
    tolerance: float
    actual: float | None = None
    passed: bool | None = None
    provenance: str = ""
    skipped_reason: str = ""


@dataclass
class RatioAssertionResult:
    output_net: str
    input_net: str
    expected_ratio: float
    tolerance: float
    actual_ratio: float | None = None
    passed: bool | None = None
    provenance: str = ""
    skipped_reason: str = ""


@dataclass
class RailSanityReport:
    rails: list[RailStatus] = field(default_factory=list)
    resistor_checks: list[ResistorPowerCheck] = field(default_factory=list)
    rail_assertions: list[RailAssertionResult] = field(default_factory=list)
    ratio_assertions: list[RatioAssertionResult] = field(default_factory=list)
    findings: list[SimulationFinding] = field(default_factory=list)

    @property
    def resistors_checked(self) -> int:
        return sum(1 for r in self.resistor_checks if r.power_w is not None)

    @property
    def resistors_skipped(self) -> int:
        return sum(1 for r in self.resistor_checks if r.skipped_reason)

    @property
    def assertions_passed(self) -> int:
        return (
            sum(1 for a in self.rail_assertions if a.passed is True)
            + sum(1 for a in self.ratio_assertions if a.passed is True)
        )

    @property
    def assertions_failed(self) -> int:
        return (
            sum(1 for a in self.rail_assertions if a.passed is False)
            + sum(1 for a in self.ratio_assertions if a.passed is False)
        )

    @property
    def assertions_skipped(self) -> int:
        return (
            sum(1 for a in self.rail_assertions if a.passed is None)
            + sum(1 for a in self.ratio_assertions if a.passed is None)
        )

    def summary(self) -> str:
        lines = ["Rail sanity report:"]
        sourced = [r for r in self.rails if r.voltage is not None]
        unsourced = [r for r in self.rails if r.voltage is None]
        lines.append(
            f"  Rails: {len(sourced)} with known voltage, "
            f"{len(unsourced)} unknown"
        )
        for r in sourced:
            lines.append(f"    {r.net_name}: {r.voltage:.3g}V ({r.voltage_source})")
        for r in unsourced:
            lines.append(f"    {r.net_name}: unknown — {r.skipped_reason}")
        if self.resistor_checks:
            lines.append(
                f"  Resistors: {self.resistors_checked} checked, "
                f"{self.resistors_skipped} skipped"
            )
            for rc in self.resistor_checks:
                if rc.power_w is not None:
                    lines.append(
                        f"    {rc.ref}: {rc.resistance:.3g}Ω, "
                        f"{rc.voltage_across:.3g}V across, "
                        f"{rc.current_a * 1000:.3g}mA, "
                        f"{rc.power_w * 1000:.3g}mW"
                    )
                elif rc.skipped_reason:
                    lines.append(f"    {rc.ref}: skipped — {rc.skipped_reason}")
        if self.rail_assertions or self.ratio_assertions:
            lines.append(
                f"  Assertions: {self.assertions_passed} passed, "
                f"{self.assertions_failed} failed, "
                f"{self.assertions_skipped} skipped"
            )
            for ra in self.rail_assertions:
                if ra.passed is True:
                    lines.append(
                        f"    PASS: {ra.net_name} = {ra.actual:.3g}V "
                        f"(expected {ra.nominal:.3g}V ±{ra.tolerance * 100:.0f}%)"
                    )
                elif ra.passed is False:
                    lines.append(
                        f"    FAIL: {ra.net_name} = {ra.actual:.3g}V "
                        f"(expected {ra.nominal:.3g}V ±{ra.tolerance * 100:.0f}%)"
                    )
                else:
                    lines.append(
                        f"    SKIP: {ra.net_name} — {ra.skipped_reason}"
                    )
            for ra in self.ratio_assertions:
                if ra.passed is True:
                    lines.append(
                        f"    PASS: V({ra.output_net})/V({ra.input_net}) = "
                        f"{ra.actual_ratio:.4g} "
                        f"(expected {ra.expected_ratio:.4g} ±{ra.tolerance:.2g})"
                    )
                elif ra.passed is False:
                    lines.append(
                        f"    FAIL: V({ra.output_net})/V({ra.input_net}) = "
                        f"{ra.actual_ratio:.4g} "
                        f"(expected {ra.expected_ratio:.4g} ±{ra.tolerance:.2g})"
                    )
                else:
                    lines.append(
                        f"    SKIP: V({ra.output_net})/V({ra.input_net}) — "
                        f"{ra.skipped_reason}"
                    )
        for f in self.findings:
            lines.append(f"  {f.summary()}")
        return "\n".join(lines)


def _net_name(net) -> str:
    return str(getattr(net, "name", "") or "")


def _pin_nets(part) -> list[str]:
    names = []
    for pin in getattr(part, "pins", []):
        net = getattr(pin, "net", None)
        if net is not None:
            name = _net_name(net)
            if name:
                names.append(name)
            else:
                names.append("")
        else:
            names.append("")
    return names


def _build_node_voltages(circuit) -> dict[str, tuple[float, str]]:
    """Build map of net_name → (voltage, source_description).

    Sources:
    - GND nets → 0.0V
    - Harness declared sources → declared voltage
    """
    voltages: dict[str, tuple[float, str]] = {}

    for net in circuit.get_nets():
        name = _net_name(net)
        if name and _GND_RE.match(name):
            voltages[name] = (0.0, "ground reference")

    for part in circuit.parts:
        for pin in getattr(part, "pins", []):
            net = getattr(pin, "net", None)
            if net is not None:
                name = _net_name(net)
                if name and _GND_RE.match(name) and name not in voltages:
                    voltages[name] = (0.0, "ground reference")

    harness = getattr(circuit, "sim_harness", None)
    if harness is not None:
        for src in harness.sources:
            voltages[src.net_name] = (
                src.voltage,
                f"sim_source({src.net_name!r}, {src.voltage}V) [{src.provenance}]",
            )

    return voltages


def _identify_power_rails(circuit) -> set[str]:
    """Find all net names that look like power or ground rails."""
    from .power_tree import POWER_NET_RE, GND_NET_RE

    rails = set()
    for net in circuit.get_nets():
        name = _net_name(net)
        if name and (POWER_NET_RE.match(name) or GND_NET_RE.match(name)):
            rails.add(name)

    for part in circuit.parts:
        for pin in getattr(part, "pins", []):
            net = getattr(pin, "net", None)
            if net is not None:
                name = _net_name(net)
                if name and (POWER_NET_RE.match(name) or GND_NET_RE.match(name)):
                    rails.add(name)

    harness = getattr(circuit, "sim_harness", None)
    if harness is not None:
        for src in harness.sources:
            rails.add(src.net_name)
        for assertion in harness.rail_assertions:
            rails.add(assertion.net_name)

    return rails


def analyze_rail_sanity(circuit=None) -> RailSanityReport:
    """Run static rail sanity checks on a circuit.

    Computes:
    - Known rail voltages from declared sources
    - Resistor current and power for parts between known-voltage nodes
    - Rail assertion pass/fail
    - Ratio assertion pass/fail
    - Skipped-rail reasons for rails without known voltages
    """
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    report = RailSanityReport()
    node_voltages = _build_node_voltages(circuit)
    power_rails = _identify_power_rails(circuit)

    # Build rail status for all identified power rails
    for rail_name in sorted(power_rails):
        if rail_name in node_voltages:
            v, src = node_voltages[rail_name]
            report.rails.append(RailStatus(
                net_name=rail_name,
                voltage=v,
                voltage_source=src,
                checked=True,
            ))
        elif _GND_RE.match(rail_name):
            report.rails.append(RailStatus(
                net_name=rail_name,
                voltage=0.0,
                voltage_source="ground reference",
                checked=True,
            ))
        else:
            report.rails.append(RailStatus(
                net_name=rail_name,
                skipped_reason="no sim_source() declared for this rail",
            ))

    # Resistor current/power checks
    registry = ModelRegistry()
    registry.build(circuit)

    for part in circuit.parts:
        entry = registry.get(part.ref)
        if entry is None or entry.spice_element != "R":
            continue
        if not entry.spice_ready or entry.value is None or entry.value <= 0:
            continue

        nets = _pin_nets(part)
        if len(nets) < 2:
            continue
        net_a, net_b = nets[0], nets[1]

        va = node_voltages.get(net_a)
        vb = node_voltages.get(net_b)

        rc = ResistorPowerCheck(
            ref=part.ref,
            resistance=entry.value,
            net_a=net_a,
            net_b=net_b,
        )

        if va is not None and vb is not None:
            rc.voltage_a = va[0]
            rc.voltage_b = vb[0]
            rc.voltage_across = abs(va[0] - vb[0])
            rc.current_a = rc.voltage_across / entry.value
            rc.power_w = rc.voltage_across * rc.current_a
        else:
            missing = []
            if va is None and net_a:
                missing.append(net_a)
            if vb is None and net_b:
                missing.append(net_b)
            if missing:
                rc.skipped_reason = (
                    f"unknown voltage on {', '.join(missing)} — "
                    f"declare with sim_source()"
                )
            elif not net_a or not net_b:
                rc.skipped_reason = "unconnected pin"

        report.resistor_checks.append(rc)

    # Evaluate rail assertions
    harness = getattr(circuit, "sim_harness", None)
    if harness is not None:
        for assertion in harness.rail_assertions:
            result = RailAssertionResult(
                net_name=assertion.net_name,
                nominal=assertion.nominal,
                tolerance=assertion.tolerance,
                provenance=assertion.provenance,
            )
            v = node_voltages.get(assertion.net_name)
            if v is not None:
                result.actual = v[0]
                margin = abs(assertion.nominal * assertion.tolerance)
                result.passed = abs(v[0] - assertion.nominal) <= margin
                if not result.passed:
                    report.findings.append(SimulationFinding(
                        severity=FindingSeverity.ERROR,
                        message=(
                            f"Rail {assertion.net_name}: {v[0]:.3g}V vs "
                            f"expected {assertion.nominal:.3g}V "
                            f"(±{assertion.tolerance * 100:.0f}%)"
                        ),
                        nets=[assertion.net_name],
                        category="rail_assertion_failed",
                    ))
            else:
                result.skipped_reason = (
                    f"no known voltage for {assertion.net_name} — "
                    f"declare with sim_source()"
                )
                report.findings.append(SimulationFinding(
                    severity=FindingSeverity.WARNING,
                    message=(
                        f"Cannot check rail assertion on {assertion.net_name}: "
                        f"no source declared"
                    ),
                    nets=[assertion.net_name],
                    category="assertion_skipped",
                ))
            report.rail_assertions.append(result)

        # Evaluate ratio assertions
        for assertion in harness.ratio_assertions:
            result = RatioAssertionResult(
                output_net=assertion.output_net,
                input_net=assertion.input_net,
                expected_ratio=assertion.ratio,
                tolerance=assertion.tolerance,
                provenance=assertion.provenance,
            )
            v_out = node_voltages.get(assertion.output_net)
            v_in = node_voltages.get(assertion.input_net)
            if v_out is not None and v_in is not None:
                if v_in[0] == 0:
                    result.skipped_reason = "input voltage is 0 — cannot compute ratio"
                    report.findings.append(SimulationFinding(
                        severity=FindingSeverity.WARNING,
                        message=(
                            f"Cannot check ratio V({assertion.output_net})/"
                            f"V({assertion.input_net}): input is 0V"
                        ),
                        nets=[assertion.output_net, assertion.input_net],
                        category="assertion_skipped",
                    ))
                else:
                    result.actual_ratio = v_out[0] / v_in[0]
                    result.passed = (
                        abs(result.actual_ratio - assertion.ratio) <= assertion.tolerance
                    )
                    if not result.passed:
                        report.findings.append(SimulationFinding(
                            severity=FindingSeverity.ERROR,
                            message=(
                                f"Ratio V({assertion.output_net})/"
                                f"V({assertion.input_net}) = "
                                f"{result.actual_ratio:.4g}, expected "
                                f"{assertion.ratio:.4g} ±{assertion.tolerance:.2g}"
                            ),
                            nets=[assertion.output_net, assertion.input_net],
                            category="ratio_assertion_failed",
                        ))
            else:
                missing = []
                if v_out is None:
                    missing.append(assertion.output_net)
                if v_in is None:
                    missing.append(assertion.input_net)
                result.skipped_reason = (
                    f"unknown voltage on {', '.join(missing)} — "
                    f"declare with sim_source()"
                )
                report.findings.append(SimulationFinding(
                    severity=FindingSeverity.WARNING,
                    message=(
                        f"Cannot check ratio assertion: unknown voltage "
                        f"on {', '.join(missing)}"
                    ),
                    nets=[assertion.output_net, assertion.input_net],
                    category="assertion_skipped",
                ))
            report.ratio_assertions.append(result)

    return report
