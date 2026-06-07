from __future__ import annotations

from .plan import plan_simulation
from .report import FindingSeverity, SimulationReport
from .runner import run_simulation


def simulation_erc(
    circuit=None,
    profile: str = "power_sanity",
    execute: bool = True,
    severity: str = "WARNING",
) -> SimulationReport:
    """Run simulation-based ERC on a circuit.

    Returns a ``SimulationReport``. When *execute* is True and InSpice is
    available, runs the actual SPICE simulation. Otherwise returns a
    readiness-only report.

    The *severity* parameter controls how missing models and failed checks
    are reported through the ERC logger: ``"WARNING"`` (default) or
    ``"ERROR"``.
    """
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    plan = plan_simulation(circuit=circuit, profile=profile)

    if execute:
        report = run_simulation(plan=plan, circuit=circuit, profile=profile)
    else:
        report = SimulationReport(
            executable=False,
            findings=list(plan.findings),
            missing_models=list(plan.skipped_parts),
            skipped_checks=[c.name for c in plan.checks],
        )

    _log_findings(report, severity)
    circuit.last_simulation_report = report
    return report


_ESCALATE_CATEGORIES = frozenset({
    "missing_model", "missing_source", "missing_ground",
    "inspice_unavailable", "not_executable", "not_spice_ready",
    "netlist_error", "simulation_error", "import_error",
})


def _is_error_severity(severity) -> bool:
    if isinstance(severity, str):
        return severity.upper() == "ERROR"
    try:
        from ..skidlbaseobj import ERROR as SKIDL_ERROR
        return severity == SKIDL_ERROR
    except ImportError:
        return False


def _log_findings(report: SimulationReport, severity) -> None:
    """Log simulation findings through the ERC logger."""
    from ..logger import active_logger

    escalate = _is_error_severity(severity)

    for finding in report.findings:
        if finding.severity == FindingSeverity.ERROR:
            if escalate:
                active_logger.error(f"Sim ERC: {finding.summary()}")
            else:
                active_logger.warning(f"Sim ERC: {finding.summary()}")
        elif finding.severity == FindingSeverity.WARNING:
            if escalate and finding.category in _ESCALATE_CATEGORIES:
                active_logger.error(f"Sim ERC: {finding.summary()}")
            else:
                active_logger.warning(f"Sim ERC: {finding.summary()}")

    for check in report.checks:
        if not check.passed:
            msg = f"Sim ERC check failed: {check.summary()}"
            if escalate:
                active_logger.error(msg)
            else:
                active_logger.warning(msg)


def enable_simulation_erc(
    circuit=None,
    profile: str = "power_sanity",
    execute: bool = True,
    severity: str = "WARNING",
) -> None:
    """Attach simulation ERC as a callback to the circuit's erc_list.

    This is opt-in: calling this function adds exactly one callback.
    The existing default ``ERC()`` behavior is unchanged unless this
    function is called.
    """
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    def _sim_erc_callback(ckt, *args, **kwargs):
        simulation_erc(
            circuit=ckt,
            profile=profile,
            execute=execute,
            severity=severity,
        )

    _sim_erc_callback.__name__ = f"simulation_erc_{profile}"
    _sim_erc_callback._is_simulation_erc = True

    for fn in circuit.erc_list:
        if getattr(fn, "_is_simulation_erc", False):
            return

    circuit.erc_list.append(_sim_erc_callback)
