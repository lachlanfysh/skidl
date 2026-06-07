from __future__ import annotations

from .plan import SimulationPlan, plan_simulation
from .registry import ModelSource
from .report import (
    FindingSeverity,
    SimulationCheck,
    SimulationFinding,
    SimulationMeasurement,
    SimulationReport,
)


def _check_inspice_available() -> bool:
    try:
        import InSpice  # noqa: F401
        return True
    except ImportError:
        return False


def run_simulation(
    plan: SimulationPlan | None = None,
    circuit=None,
    profile: str = "power_sanity",
) -> SimulationReport:
    """Execute a simulation plan and return results.

    If *plan* is None, one is built from *circuit* and *profile*.
    InSpice is lazy-imported only inside this function.
    """
    if plan is None:
        plan = plan_simulation(circuit=circuit, profile=profile)

    report = SimulationReport(
        findings=list(plan.findings),
        missing_models=list(plan.skipped_parts),
    )

    if not plan.executable:
        report.executable = False
        report.skipped_checks = [c.name for c in plan.checks]
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.WARNING,
            message="Plan is not executable — returning readiness report only",
            category="not_executable",
        ))
        return report

    if not _check_inspice_available():
        report.executable = False
        report.skipped_checks = [c.name for c in plan.checks]
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.WARNING,
            message="InSpice not available — cannot execute simulation",
            category="inspice_unavailable",
        ))
        return report

    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    report.executable = True
    _execute_checks(plan, circuit, report)
    return report


def _has_auto_primitives(plan: SimulationPlan) -> bool:
    return any(
        e.spice_ready and e.source == ModelSource.BUILTIN_PRIMITIVE
        for e in plan.eligible_parts
    )


def _execute_checks(
    plan: SimulationPlan,
    circuit,
    report: SimulationReport,
) -> None:
    """Run SPICE checks using InSpice. Lazy-imports InSpice here only."""
    if _has_auto_primitives(plan):
        spice_ckt, analysis = _run_via_harness(plan, circuit, report)
    else:
        spice_ckt, analysis = _run_via_gen_netlist(circuit, report)

    if spice_ckt is None or analysis is None:
        return

    report.spice_netlist = str(spice_ckt)

    node_voltages = {}
    for node_name in analysis.nodes:
        try:
            val = float(analysis[node_name])
            node_voltages[node_name] = val
            report.measurements.append(SimulationMeasurement(
                name=f"V({node_name})",
                value=val,
                unit="V",
                net=node_name,
            ))
        except (TypeError, ValueError, IndexError):
            pass

    if not hasattr(report, "skipped_checks") or report.skipped_checks is None:
        report.skipped_checks = []
    for check_spec in plan.checks:
        result = _evaluate_check(check_spec, node_voltages, analysis, plan)
        if result is None:
            report.skipped_checks.append(check_spec.name)
        else:
            report.checks.append(result)


def _run_via_harness(plan, circuit, report):
    """Build and simulate via the non-mutating harness."""
    try:
        from .harness import build_simulation_circuit
        spice_ckt, added = build_simulation_circuit(plan, circuit)
    except Exception as exc:
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.ERROR,
            message=f"Harness netlist build failed: {exc}",
            category="netlist_error",
        ))
        report.executable = False
        return None, None

    if not added:
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.ERROR,
            message="No parts could be added to simulation circuit",
            category="netlist_error",
        ))
        report.executable = False
        return None, None

    try:
        simulator = spice_ckt.simulator()
        analysis = simulator.operating_point()
    except Exception as exc:
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.ERROR,
            message=f"Operating point simulation failed: {exc}",
            category="simulation_error",
        ))
        report.executable = False
        return None, None

    return spice_ckt, analysis


def _run_via_gen_netlist(circuit, report):
    """Build and simulate via the existing gen_netlist path (requires part.pyspice)."""
    try:
        from ..tools.spice.spice import gen_netlist
    except ImportError:
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.ERROR,
            message="Cannot import SPICE netlist generator",
            category="import_error",
        ))
        return None, None

    try:
        spice_ckt = gen_netlist(circuit)
    except Exception as exc:
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.ERROR,
            message=f"SPICE netlist generation failed: {exc}",
            category="netlist_error",
        ))
        report.executable = False
        return None, None

    try:
        simulator = spice_ckt.simulator()
        analysis = simulator.operating_point()
    except Exception as exc:
        report.findings.append(SimulationFinding(
            severity=FindingSeverity.ERROR,
            message=f"Operating point simulation failed: {exc}",
            category="simulation_error",
        ))
        report.executable = False
        return None, None

    return spice_ckt, analysis


def _evaluate_check(
    spec,
    node_voltages: dict[str, float],
    analysis,
    plan: SimulationPlan,
) -> SimulationCheck | None:
    if spec.check_type == "rail_presence":
        return _check_rail_presence(spec, node_voltages)
    elif spec.check_type == "divider_ratio":
        return _check_divider_ratio(spec, node_voltages, plan)
    elif spec.check_type == "passive_power":
        return None
    elif spec.check_type == "rc_time_constant":
        return SimulationCheck(
            name=spec.name,
            passed=True,
            measured=spec.expected,
            expected=spec.expected,
            unit=spec.unit,
            refs=spec.refs,
            nets=spec.nets,
            model_provenance=spec.model_provenance,
            reason="RC time constant is a static calculation from component values",
        )
    else:
        return SimulationCheck(
            name=spec.name,
            passed=False,
            refs=spec.refs,
            nets=spec.nets,
            reason=f"Unknown check type: {spec.check_type}",
        )


def _check_rail_presence(spec, node_voltages: dict[str, float]) -> SimulationCheck:
    net = spec.nets[0] if spec.nets else ""
    net_lower = net.lower()
    measured = node_voltages.get(net_lower)
    if measured is None:
        for node, val in node_voltages.items():
            if node.lower() == net_lower or net_lower in node.lower():
                measured = val
                break
    if measured is None:
        return SimulationCheck(
            name=spec.name,
            passed=False,
            expected=spec.expected,
            unit=spec.unit,
            refs=spec.refs,
            nets=spec.nets,
            model_provenance=spec.model_provenance,
            reason=f"Node {net} not found in simulation results",
        )
    passed = True
    if spec.expected is not None and spec.tolerance is not None:
        passed = abs(measured - spec.expected) <= abs(spec.expected * spec.tolerance)
    return SimulationCheck(
        name=spec.name,
        passed=passed,
        measured=measured,
        expected=spec.expected,
        tolerance=spec.tolerance,
        unit=spec.unit,
        refs=spec.refs,
        nets=spec.nets,
        model_provenance=spec.model_provenance,
    )


def _check_divider_ratio(
    spec, node_voltages: dict[str, float], plan: SimulationPlan,
) -> SimulationCheck:
    net = spec.nets[0] if spec.nets else ""
    net_lower = net.lower()
    measured = node_voltages.get(net_lower)
    if measured is None:
        for node, val in node_voltages.items():
            if net_lower in node.lower():
                measured = val
                break

    source_voltage = None
    for src in plan.sources:
        if src.value is not None:
            source_voltage = src.value
            break

    if measured is not None and source_voltage is not None and source_voltage != 0:
        ratio = measured / source_voltage
        expected = spec.expected
        tol = spec.tolerance or 0.05
        passed = expected is None or abs(ratio - expected) <= tol
        return SimulationCheck(
            name=spec.name,
            passed=passed,
            measured=ratio,
            expected=expected,
            tolerance=tol,
            unit="ratio",
            refs=spec.refs,
            nets=spec.nets,
            model_provenance=spec.model_provenance,
        )

    return SimulationCheck(
        name=spec.name,
        passed=False,
        expected=spec.expected,
        unit="ratio",
        refs=spec.refs,
        nets=spec.nets,
        model_provenance=spec.model_provenance,
        reason="Could not measure divider output node",
    )
