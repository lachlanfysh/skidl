"""Structured simulation intent contract.

Parses a strict, versioned intent dict and applies it to circuit.sim_harness.
Designed as a rigid handoff format for LLMs — every agent-derived item must
carry provenance and confidence. SKiDL only validates and applies; it does
not guess, infer SPICE models, or create pin maps.
"""
from __future__ import annotations

import math
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


def _is_finite_positive(val) -> bool:
    if not isinstance(val, (int, float)):
        return False
    if isinstance(val, bool):
        return False
    return math.isfinite(val) and val > 0


def _is_finite_number(val) -> bool:
    if not isinstance(val, (int, float)):
        return False
    if isinstance(val, bool):
        return False
    return math.isfinite(val)


def _validate_numeric(
    d: dict,
    key: str,
    path: str,
    findings: list[IntentFinding],
    *,
    required: bool = True,
    positive: bool = False,
    default: float | None = None,
) -> float | None:
    """Validate a numeric field. Returns the value or None on error.

    Appends an IntentFinding on validation failure.
    """
    if key not in d:
        if required:
            findings.append(IntentFinding(
                severity="error",
                message=f"Missing required field '{key}' at {path}",
                path=path,
                category="missing_field",
            ))
            return None
        return default

    val = d[key]
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        findings.append(IntentFinding(
            severity="error",
            message=f"Field '{key}' at {path} must be a number, "
                    f"got {type(val).__name__}: {val!r}",
            path=path,
            category="type_error",
        ))
        return None

    if not math.isfinite(val):
        findings.append(IntentFinding(
            severity="error",
            message=f"Field '{key}' at {path} must be finite, got {val}",
            path=path,
            category="invalid_value",
        ))
        return None

    if positive and val <= 0:
        findings.append(IntentFinding(
            severity="error",
            message=f"Field '{key}' at {path} must be positive, got {val}",
            path=path,
            category="invalid_value",
        ))
        return None

    return float(val)


def _validate_string(
    d: dict,
    key: str,
    path: str,
    findings: list[IntentFinding],
    *,
    required: bool = True,
    default: str = "",
    nonempty: bool = False,
) -> str | None:
    if key not in d:
        if required:
            findings.append(IntentFinding(
                severity="error",
                message=f"Missing required field '{key}' at {path}",
                path=path,
                category="missing_field",
            ))
            return None
        return default

    val = d[key]
    if not isinstance(val, str):
        findings.append(IntentFinding(
            severity="error",
            message=f"Field '{key}' at {path} must be a string, "
                    f"got {type(val).__name__}",
            path=path,
            category="type_error",
        ))
        return None

    if nonempty and not val.strip():
        findings.append(IntentFinding(
            severity="error",
            message=f"Field '{key}' at {path} must be a non-empty string",
            path=path,
            category="invalid_value",
        ))
        return None

    return val


def _check_provenance(
    d: dict,
    path: str,
    strict: bool,
    findings: list[IntentFinding],
    low_confidence: list[str],
) -> tuple[str, float] | None:
    """Validate provenance and confidence fields.

    In strict mode, both are required and must be non-empty/numeric.
    Returns (provenance, confidence) or None on error.
    """
    provenance = d.get("provenance")
    confidence = d.get("confidence")

    if strict:
        if provenance is None:
            findings.append(IntentFinding(
                severity="error",
                message=f"Missing required 'provenance' at {path} (strict mode)",
                path=path,
                category="missing_provenance",
            ))
            return None
        if not isinstance(provenance, str) or not provenance.strip():
            findings.append(IntentFinding(
                severity="error",
                message=f"'provenance' at {path} must be a non-empty string",
                path=path,
                category="missing_provenance",
            ))
            return None
        if confidence is None:
            findings.append(IntentFinding(
                severity="error",
                message=f"Missing required 'confidence' at {path} (strict mode)",
                path=path,
                category="missing_confidence",
            ))
            return None
        if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            findings.append(IntentFinding(
                severity="error",
                message=f"'confidence' at {path} must be a number, "
                        f"got {type(confidence).__name__}: {confidence!r}",
                path=path,
                category="type_error",
            ))
            return None
        if not math.isfinite(confidence):
            findings.append(IntentFinding(
                severity="error",
                message=f"'confidence' at {path} must be finite, got {confidence}",
                path=path,
                category="invalid_value",
            ))
            return None
        if not (0.0 <= confidence <= 1.0):
            findings.append(IntentFinding(
                severity="error",
                message=f"'confidence' at {path} must be 0.0–1.0, got {confidence}",
                path=path,
                category="invalid_value",
            ))
            return None
    else:
        if provenance is None or (isinstance(provenance, str) and not provenance.strip()):
            provenance = ""
            findings.append(IntentFinding(
                severity="warning",
                message=f"No provenance at {path} — origin unknown",
                path=path,
                category="missing_provenance",
            ))

        if confidence is None:
            confidence = 1.0
        elif isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
            findings.append(IntentFinding(
                severity="error",
                message=f"'confidence' at {path} must be a number, "
                        f"got {type(confidence).__name__}: {confidence!r}",
                path=path,
                category="type_error",
            ))
            return None
        elif not math.isfinite(confidence):
            findings.append(IntentFinding(
                severity="error",
                message=f"'confidence' at {path} must be finite, got {confidence}",
                path=path,
                category="invalid_value",
            ))
            return None
        elif not (0.0 <= confidence <= 1.0):
            findings.append(IntentFinding(
                severity="error",
                message=f"'confidence' at {path} must be 0.0–1.0, got {confidence}",
                path=path,
                category="invalid_value",
            ))
            return None

    prov_str = str(provenance or "")
    conf_val = float(confidence)

    if conf_val < 0.5:
        low_confidence.append(
            f"{path}: confidence={conf_val}, provenance={prov_str!r}"
        )
        findings.append(IntentFinding(
            severity="info",
            message=f"Low confidence ({conf_val}) at {path}: {prov_str}",
            path=path,
            category="low_confidence",
        ))

    return prov_str, conf_val


def _validate_intent(
    intent: dict,
    strict: bool,
    findings: list[IntentFinding],
    low_confidence: list[str],
) -> bool:
    if not isinstance(intent, dict):
        findings.append(IntentFinding(
            severity="error",
            message=f"Intent must be a dict, got {type(intent).__name__}",
            category="type_error",
        ))
        return False

    version = intent.get("version")
    if version is None:
        findings.append(IntentFinding(
            severity="error",
            message="Missing required field 'version'",
            category="missing_field",
        ))
        return False

    if version != INTENT_VERSION:
        findings.append(IntentFinding(
            severity="error",
            message=f"Unsupported intent version {version} (expected {INTENT_VERSION})",
            category="version_mismatch",
        ))
        return False

    if not _validate_keys(intent, _KNOWN_TOP_KEYS, "root", strict, findings):
        return False

    # Validate sources
    if not _validate_section_list(intent, "sources", findings):
        return False
    for i, item in enumerate(intent.get("sources", [])):
        path = f"sources[{i}]"
        if not isinstance(item, dict):
            findings.append(IntentFinding(
                severity="error", message=f"Item at {path} must be a dict",
                path=path, category="type_error",
            ))
            return False
        if not _validate_keys(item, _KNOWN_SOURCE_KEYS, path, strict, findings):
            return False
        if _validate_string(item, "net", path, findings, nonempty=True) is None:
            return False
        if _validate_numeric(item, "voltage", path, findings) is None:
            return False
        if _check_provenance(item, path, strict, findings, low_confidence) is None:
            return False

    # Validate loads
    if not _validate_section_list(intent, "loads", findings):
        return False
    for i, item in enumerate(intent.get("loads", [])):
        path = f"loads[{i}]"
        if not isinstance(item, dict):
            findings.append(IntentFinding(
                severity="error", message=f"Item at {path} must be a dict",
                path=path, category="type_error",
            ))
            return False
        if not _validate_keys(item, _KNOWN_LOAD_KEYS, path, strict, findings):
            return False
        if _validate_string(item, "net", path, findings, nonempty=True) is None:
            return False
        has_r = "resistance" in item and item["resistance"] is not None
        has_i = "current" in item and item["current"] is not None
        if not has_r and not has_i:
            findings.append(IntentFinding(
                severity="error",
                message=f"Load at {path} must specify resistance or current",
                path=path, category="missing_field",
            ))
            return False
        if has_r and has_i:
            findings.append(IntentFinding(
                severity="error",
                message=f"Load at {path} must specify exactly one of "
                        f"resistance or current, not both",
                path=path, category="invalid_value",
            ))
            return False
        if has_r:
            if _validate_numeric(item, "resistance", path, findings,
                                 positive=True) is None:
                return False
        if has_i:
            if _validate_numeric(item, "current", path, findings,
                                 positive=True) is None:
                return False
        if _check_provenance(item, path, strict, findings, low_confidence) is None:
            return False

    # Validate probes
    if not _validate_section_list(intent, "probes", findings):
        return False
    for i, item in enumerate(intent.get("probes", [])):
        path = f"probes[{i}]"
        if not isinstance(item, dict):
            findings.append(IntentFinding(
                severity="error", message=f"Item at {path} must be a dict",
                path=path, category="type_error",
            ))
            return False
        if not _validate_keys(item, _KNOWN_PROBE_KEYS, path, strict, findings):
            return False
        if _validate_string(item, "net", path, findings, nonempty=True) is None:
            return False
        if _check_provenance(item, path, strict, findings, low_confidence) is None:
            return False

    # Validate rail_assertions
    if not _validate_section_list(intent, "rail_assertions", findings):
        return False
    for i, item in enumerate(intent.get("rail_assertions", [])):
        path = f"rail_assertions[{i}]"
        if not isinstance(item, dict):
            findings.append(IntentFinding(
                severity="error", message=f"Item at {path} must be a dict",
                path=path, category="type_error",
            ))
            return False
        if not _validate_keys(item, _KNOWN_RAIL_ASSERTION_KEYS, path, strict, findings):
            return False
        if _validate_string(item, "net", path, findings, nonempty=True) is None:
            return False
        if _validate_numeric(item, "nominal", path, findings) is None:
            return False
        if "tolerance" in item:
            if _validate_numeric(item, "tolerance", path, findings,
                                 required=True, positive=True) is None:
                return False
        if _check_provenance(item, path, strict, findings, low_confidence) is None:
            return False

    # Validate ratio_assertions
    if not _validate_section_list(intent, "ratio_assertions", findings):
        return False
    for i, item in enumerate(intent.get("ratio_assertions", [])):
        path = f"ratio_assertions[{i}]"
        if not isinstance(item, dict):
            findings.append(IntentFinding(
                severity="error", message=f"Item at {path} must be a dict",
                path=path, category="type_error",
            ))
            return False
        if not _validate_keys(item, _KNOWN_RATIO_ASSERTION_KEYS, path, strict, findings):
            return False
        if _validate_string(item, "output_net", path, findings, nonempty=True) is None:
            return False
        if _validate_string(item, "input_net", path, findings, nonempty=True) is None:
            return False
        if _validate_numeric(item, "ratio", path, findings) is None:
            return False
        if "tolerance" in item:
            if _validate_numeric(item, "tolerance", path, findings,
                                 required=True, positive=True) is None:
                return False
        if _check_provenance(item, path, strict, findings, low_confidence) is None:
            return False

    return not any(f.severity == "error" for f in findings)


def _validate_section_list(
    intent: dict,
    section: str,
    findings: list[IntentFinding],
) -> bool:
    items = intent.get(section, [])
    if not isinstance(items, list):
        findings.append(IntentFinding(
            severity="error",
            message=f"'{section}' must be a list, got {type(items).__name__}",
            path=section,
            category="type_error",
        ))
        return False
    return True


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

    In strict mode (default): every item requires non-empty provenance
    (string) and numeric confidence. Unknown keys are errors.

    In non-strict mode: missing provenance is warned, confidence defaults
    to 1.0, and unknown keys are warned but ignored.

    Args:
        intent: Structured intent dict (version 1).
        circuit: Target circuit. Defaults to builtins.default_circuit.
        strict: If True (default), provenance/confidence/keys are required.

    Returns:
        SimulationIntentReport with applied status and findings.
    """
    report = SimulationIntentReport()
    low_confidence: list[str] = []

    valid = _validate_intent(intent, strict, report.findings, low_confidence)

    if not valid:
        report.applied = False
        return report

    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    # Build all items (validation already passed — safe to convert)
    sources: list[DeclaredSource] = []
    loads: list[DeclaredLoad] = []
    probes: list[DeclaredProbe] = []
    rail_assertions: list[RailAssertion] = []
    ratio_assertions: list[RatioAssertion] = []

    for item in intent.get("sources", []):
        prov = item.get("provenance", "")
        conf = item.get("confidence", 1.0)
        sources.append(DeclaredSource(
            net_name=_resolve_net_name(item["net"]),
            voltage=float(item["voltage"]),
            ref=item.get("ref", ""),
            provenance=f"{prov} [confidence={conf}]",
        ))

    for item in intent.get("loads", []):
        prov = item.get("provenance", "")
        conf = item.get("confidence", 1.0)
        loads.append(DeclaredLoad(
            net_name=_resolve_net_name(item["net"]),
            resistance=float(item["resistance"]) if item.get("resistance") is not None else None,
            current=float(item["current"]) if item.get("current") is not None else None,
            provenance=f"{prov} [confidence={conf}]",
        ))

    for item in intent.get("probes", []):
        prov = item.get("provenance", "")
        conf = item.get("confidence", 1.0)
        probes.append(DeclaredProbe(
            net_name=_resolve_net_name(item["net"]),
            kind=item.get("kind", "voltage"),
            provenance=f"{prov} [confidence={conf}]",
        ))

    for item in intent.get("rail_assertions", []):
        prov = item.get("provenance", "")
        conf = item.get("confidence", 1.0)
        rail_assertions.append(RailAssertion(
            net_name=_resolve_net_name(item["net"]),
            nominal=float(item["nominal"]),
            tolerance=float(item.get("tolerance", 0.05)),
            provenance=f"{prov} [confidence={conf}]",
        ))

    for item in intent.get("ratio_assertions", []):
        prov = item.get("provenance", "")
        conf = item.get("confidence", 1.0)
        ratio_assertions.append(RatioAssertion(
            output_net=_resolve_net_name(item["output_net"]),
            input_net=_resolve_net_name(item["input_net"]),
            ratio=float(item["ratio"]),
            tolerance=float(item.get("tolerance", 0.05)),
            provenance=f"{prov} [confidence={conf}]",
        ))

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
