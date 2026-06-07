"""Structured simulation intent contract.

Parses a strict, versioned intent dict and applies it to circuit.sim_harness.
Designed as a rigid handoff format for LLMs — every agent-derived item must
carry provenance and confidence. SKiDL only validates and applies; it does
not guess, infer SPICE models, or create pin maps.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .declarations import (
    SimHarness,
    DeclaredSource,
    DeclaredLoad,
    DeclaredProbe,
    RailAssertion,
    RatioAssertion,
    _get_harness,
    _resolve_net_name,
)

INTENT_VERSION = 1

_KNOWN_TOP_KEYS = {
    "version", "sources", "loads", "probes",
    "rail_assertions", "ratio_assertions", "metadata",
}

_KNOWN_SOURCE_KEYS = {"net", "voltage", "ref", "provenance", "confidence"}
_KNOWN_LOAD_KEYS = {"net", "resistance", "current", "provenance", "confidence"}
_KNOWN_PROBE_KEYS = {"net", "kind", "provenance", "confidence"}
_KNOWN_RAIL_ASSERTION_KEYS = {"net", "nominal", "tolerance", "provenance", "confidence"}
_KNOWN_RATIO_ASSERTION_KEYS = {
    "output_net", "input_net", "ratio", "tolerance",
    "provenance", "confidence",
}


@dataclass
class IntentFinding:
    severity: str  # "error", "warning", "info"
    message: str
    path: str = ""
    category: str = ""


@dataclass
class SimulationIntentReport:
    applied: bool = False
    sources_added: int = 0
    loads_added: int = 0
    probes_added: int = 0
    rail_assertions_added: int = 0
    ratio_assertions_added: int = 0
    low_confidence_items: list[str] = field(default_factory=list)
    findings: list[IntentFinding] = field(default_factory=list)

    def summary(self) -> str:
        lines = [f"Intent applied: {self.applied}"]
        if self.applied:
            lines.append(
                f"  Added: {self.sources_added} sources, "
                f"{self.loads_added} loads, {self.probes_added} probes, "
                f"{self.rail_assertions_added} rail assertions, "
                f"{self.ratio_assertions_added} ratio assertions"
            )
        if self.low_confidence_items:
            lines.append(
                f"  Low confidence items: {len(self.low_confidence_items)}"
            )
        errors = [f for f in self.findings if f.severity == "error"]
        if errors:
            lines.append(f"  Errors: {len(errors)}")
        return "\n".join(lines)


def _validate_keys(
    d: dict,
    known: set[str],
    path: str,
    strict: bool,
    findings: list[IntentFinding],
) -> bool:
    unknown = set(d.keys()) - known
    if unknown and strict:
        findings.append(IntentFinding(
            severity="error",
            message=f"Unknown keys at {path}: {sorted(unknown)}",
            path=path,
            category="unknown_key",
        ))
        return False
    elif unknown:
        findings.append(IntentFinding(
            severity="warning",
            message=f"Ignoring unknown keys at {path}: {sorted(unknown)}",
            path=path,
            category="unknown_key",
        ))
    return True


def _require_field(
    d: dict,
    key: str,
    expected_type: type,
    path: str,
    findings: list[IntentFinding],
) -> bool:
    if key not in d:
        findings.append(IntentFinding(
            severity="error",
            message=f"Missing required field '{key}' at {path}",
            path=path,
            category="missing_field",
        ))
        return False
    if not isinstance(d[key], expected_type):
        findings.append(IntentFinding(
            severity="error",
            message=f"Field '{key}' at {path} must be {expected_type.__name__}, "
                    f"got {type(d[key]).__name__}",
            path=path,
            category="type_error",
        ))
        return False
    return True


def _check_provenance(
    d: dict,
    path: str,
    findings: list[IntentFinding],
    low_confidence: list[str],
) -> tuple[str, float]:
    provenance = d.get("provenance", "")
    confidence = d.get("confidence", 1.0)

    if not provenance:
        findings.append(IntentFinding(
            severity="warning",
            message=f"No provenance at {path} — origin unknown",
            path=path,
            category="missing_provenance",
        ))

    if not isinstance(confidence, (int, float)):
        findings.append(IntentFinding(
            severity="error",
            message=f"Confidence at {path} must be a number, got {type(confidence).__name__}",
            path=path,
            category="type_error",
        ))
        return str(provenance), 0.0

    if confidence < 0.5:
        low_confidence.append(
            f"{path}: confidence={confidence}, provenance={provenance!r}"
        )
        findings.append(IntentFinding(
            severity="info",
            message=f"Low confidence ({confidence}) at {path}: {provenance}",
            path=path,
            category="low_confidence",
        ))

    return str(provenance), float(confidence)


def _validate_intent(
    intent: dict,
    strict: bool,
) -> tuple[list[IntentFinding], bool]:
    findings: list[IntentFinding] = []

    if not isinstance(intent, dict):
        findings.append(IntentFinding(
            severity="error",
            message=f"Intent must be a dict, got {type(intent).__name__}",
            category="type_error",
        ))
        return findings, False

    version = intent.get("version")
    if version is None:
        findings.append(IntentFinding(
            severity="error",
            message="Missing required field 'version'",
            category="missing_field",
        ))
        return findings, False

    if version != INTENT_VERSION:
        findings.append(IntentFinding(
            severity="error",
            message=f"Unsupported intent version {version} (expected {INTENT_VERSION})",
            category="version_mismatch",
        ))
        return findings, False

    valid = _validate_keys(intent, _KNOWN_TOP_KEYS, "root", strict, findings)
    if not valid:
        return findings, False

    # Validate each section's entries
    for section, known_keys, required in [
        ("sources", _KNOWN_SOURCE_KEYS, [("net", str), ("voltage", (int, float))]),
        ("loads", _KNOWN_LOAD_KEYS, [("net", str)]),
        ("probes", _KNOWN_PROBE_KEYS, [("net", str)]),
        ("rail_assertions", _KNOWN_RAIL_ASSERTION_KEYS,
         [("net", str), ("nominal", (int, float))]),
        ("ratio_assertions", _KNOWN_RATIO_ASSERTION_KEYS,
         [("output_net", str), ("input_net", str), ("ratio", (int, float))]),
    ]:
        items = intent.get(section, [])
        if not isinstance(items, list):
            findings.append(IntentFinding(
                severity="error",
                message=f"'{section}' must be a list, got {type(items).__name__}",
                path=section,
                category="type_error",
            ))
            return findings, False

        for i, item in enumerate(items):
            path = f"{section}[{i}]"
            if not isinstance(item, dict):
                findings.append(IntentFinding(
                    severity="error",
                    message=f"Item at {path} must be a dict",
                    path=path,
                    category="type_error",
                ))
                return findings, False

            _validate_keys(item, known_keys, path, strict, findings)

            for key, expected_type in required:
                if isinstance(expected_type, tuple):
                    if key in item and not isinstance(item[key], expected_type):
                        findings.append(IntentFinding(
                            severity="error",
                            message=f"Field '{key}' at {path} must be "
                                    f"{' or '.join(t.__name__ for t in expected_type)}",
                            path=path,
                            category="type_error",
                        ))
                        return findings, False
                    elif key not in item:
                        findings.append(IntentFinding(
                            severity="error",
                            message=f"Missing required field '{key}' at {path}",
                            path=path,
                            category="missing_field",
                        ))
                        return findings, False
                else:
                    if not _require_field(item, key, expected_type, path, findings):
                        return findings, False

        # Validate loads have at least resistance or current
        if section == "loads":
            for i, item in enumerate(items):
                path = f"loads[{i}]"
                has_r = "resistance" in item and item["resistance"] is not None
                has_i = "current" in item and item["current"] is not None
                if not has_r and not has_i:
                    findings.append(IntentFinding(
                        severity="error",
                        message=f"Load at {path} must specify resistance or current",
                        path=path,
                        category="missing_field",
                    ))
                    return findings, False

    has_errors = any(f.severity == "error" for f in findings)
    return findings, not has_errors


def apply_simulation_intent(
    intent: dict,
    circuit=None,
    *,
    strict: bool = True,
) -> SimulationIntentReport:
    """Validate and apply a structured simulation intent to a circuit.

    The intent dict is the handoff contract between an LLM/agent and
    SKiDL's simulation engine. It is validated transactionally — either
    all items are applied, or none are.

    Args:
        intent: Structured intent dict (version 1).
        circuit: Target circuit. Defaults to builtins.default_circuit.
        strict: If True (default), unknown keys cause errors.

    Returns:
        SimulationIntentReport with applied status and findings.
    """
    report = SimulationIntentReport()

    validation_findings, valid = _validate_intent(intent, strict)
    report.findings.extend(validation_findings)

    if not valid:
        report.applied = False
        return report

    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    # Build all items first (transactional — don't mutate until validated)
    sources: list[DeclaredSource] = []
    loads: list[DeclaredLoad] = []
    probes: list[DeclaredProbe] = []
    rail_assertions: list[RailAssertion] = []
    ratio_assertions: list[RatioAssertion] = []
    low_confidence: list[str] = []

    for i, item in enumerate(intent.get("sources", [])):
        prov, conf = _check_provenance(
            item, f"sources[{i}]", report.findings, low_confidence,
        )
        sources.append(DeclaredSource(
            net_name=_resolve_net_name(item["net"]),
            voltage=float(item["voltage"]),
            ref=item.get("ref", ""),
            provenance=f"{prov} [confidence={conf}]",
        ))

    for i, item in enumerate(intent.get("loads", [])):
        prov, conf = _check_provenance(
            item, f"loads[{i}]", report.findings, low_confidence,
        )
        loads.append(DeclaredLoad(
            net_name=_resolve_net_name(item["net"]),
            resistance=item.get("resistance"),
            current=item.get("current"),
            provenance=f"{prov} [confidence={conf}]",
        ))

    for i, item in enumerate(intent.get("probes", [])):
        prov, conf = _check_provenance(
            item, f"probes[{i}]", report.findings, low_confidence,
        )
        probes.append(DeclaredProbe(
            net_name=_resolve_net_name(item["net"]),
            kind=item.get("kind", "voltage"),
            provenance=f"{prov} [confidence={conf}]",
        ))

    for i, item in enumerate(intent.get("rail_assertions", [])):
        prov, conf = _check_provenance(
            item, f"rail_assertions[{i}]", report.findings, low_confidence,
        )
        rail_assertions.append(RailAssertion(
            net_name=_resolve_net_name(item["net"]),
            nominal=float(item["nominal"]),
            tolerance=float(item.get("tolerance", 0.05)),
            provenance=f"{prov} [confidence={conf}]",
        ))

    for i, item in enumerate(intent.get("ratio_assertions", [])):
        prov, conf = _check_provenance(
            item, f"ratio_assertions[{i}]", report.findings, low_confidence,
        )
        ratio_assertions.append(RatioAssertion(
            output_net=_resolve_net_name(item["output_net"]),
            input_net=_resolve_net_name(item["input_net"]),
            ratio=float(item["ratio"]),
            tolerance=float(item.get("tolerance", 0.05)),
            provenance=f"{prov} [confidence={conf}]",
        ))

    # Check for errors from provenance validation
    if any(f.severity == "error" for f in report.findings):
        report.applied = False
        return report

    # Apply transactionally
    harness = _get_harness(circuit)
    harness.sources.extend(sources)
    harness.loads.extend(loads)
    harness.probes.extend(probes)
    harness.rail_assertions.extend(rail_assertions)
    harness.ratio_assertions.extend(ratio_assertions)

    report.applied = True
    report.sources_added = len(sources)
    report.loads_added = len(loads)
    report.probes_added = len(probes)
    report.rail_assertions_added = len(rail_assertions)
    report.ratio_assertions_added = len(ratio_assertions)
    report.low_confidence_items = low_confidence

    return report
