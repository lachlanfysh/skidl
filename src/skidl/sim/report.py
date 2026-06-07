from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class FindingSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class SimulationFinding:
    severity: FindingSeverity
    message: str
    refs: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    category: str = ""

    def summary(self) -> str:
        parts = [f"[{self.severity.value.upper()}]"]
        if self.category:
            parts.append(f"({self.category})")
        parts.append(self.message)
        if self.refs:
            parts.append(f"refs={','.join(self.refs)}")
        if self.nets:
            parts.append(f"nets={','.join(self.nets)}")
        return " ".join(parts)


@dataclass
class SimulationMeasurement:
    name: str
    value: float
    unit: str = "V"
    net: str = ""
    ref: str = ""

    def summary(self) -> str:
        loc = self.net or self.ref or ""
        return f"{self.name}: {self.value:.4g}{self.unit}" + (f" @ {loc}" if loc else "")


@dataclass
class SimulationCheck:
    name: str
    passed: bool
    measured: float | None = None
    expected: float | None = None
    tolerance: float | None = None
    unit: str = "V"
    refs: list[str] = field(default_factory=list)
    nets: list[str] = field(default_factory=list)
    model_provenance: str = ""
    reason: str = ""

    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        parts = [f"[{status}] {self.name}"]
        if self.measured is not None:
            parts.append(f"measured={self.measured:.4g}{self.unit}")
        if self.expected is not None:
            parts.append(f"expected={self.expected:.4g}{self.unit}")
        if self.reason:
            parts.append(self.reason)
        return " ".join(parts)


@dataclass
class SimulationReport:
    executable: bool = False
    findings: list[SimulationFinding] = field(default_factory=list)
    measurements: list[SimulationMeasurement] = field(default_factory=list)
    checks: list[SimulationCheck] = field(default_factory=list)
    missing_models: list[str] = field(default_factory=list)
    skipped_checks: list[str] = field(default_factory=list)
    spice_netlist: str = ""

    @property
    def error_count(self) -> int:
        return sum(
            1
            for f in self.findings
            if f.severity == FindingSeverity.ERROR
        )

    @property
    def warning_count(self) -> int:
        return sum(
            1
            for f in self.findings
            if f.severity == FindingSeverity.WARNING
        )

    @property
    def ok(self) -> bool:
        return self.error_count == 0 and all(c.passed for c in self.checks)

    def summary(self) -> str:
        lines = []
        status = "executable" if self.executable else "readiness-only"
        lines.append(f"Simulation report ({status}):")
        if self.missing_models:
            lines.append(f"  Missing models: {', '.join(self.missing_models[:10])}")
        for finding in self.findings:
            lines.append(f"  {finding.summary()}")
        for check in self.checks:
            lines.append(f"  {check.summary()}")
        for m in self.measurements:
            lines.append(f"  {m.summary()}")
        if self.skipped_checks:
            lines.append(f"  Skipped: {', '.join(self.skipped_checks[:10])}")
        errs = self.error_count
        warns = self.warning_count
        passed = sum(1 for c in self.checks if c.passed)
        failed = sum(1 for c in self.checks if not c.passed)
        lines.append(
            f"  Summary: {errs} errors, {warns} warnings, "
            f"{passed} checks passed, {failed} checks failed"
        )
        return "\n".join(lines)
