"""Decoupling confidence report.

Answers: are plausible local/bulk decaps present and near the right pins?
Works at schematic level (no layout needed) and refines with placement data.
"""
from __future__ import annotations

import math
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
LOCAL_CAP_RE = re.compile(r"^(100n|0\.1u)", re.IGNORECASE)
BULK_CAP_VALUE_MIN = 1e-6  # 1uF


@dataclass
class ICPowerPin:
    ic_ref: str
    pin_name: str
    pin_num: str
    net_name: str
    detection: str  # "pin_type" or "net_name"


@dataclass
class CapInfo:
    ref: str
    value_str: str
    value_f: float | None
    classification: str  # "local", "bulk", "unknown"
    power_net: str
    ground_net: str


@dataclass
class CapAssociation:
    cap_ref: str
    ic_ref: str
    rail: str
    distance_mm: float | None = None
    classification: str = ""


@dataclass
class DecouplingFinding:
    severity: str  # "error", "warning", "info"
    message: str
    ic_ref: str = ""
    rail: str = ""
    cap_ref: str = ""
    category: str = ""


@dataclass
class DecouplingReport:
    ic_power_pins: list[ICPowerPin] = field(default_factory=list)
    caps: list[CapInfo] = field(default_factory=list)
    associations: list[CapAssociation] = field(default_factory=list)
    findings: list[DecouplingFinding] = field(default_factory=list)
    layout_available: bool = False

    def summary(self) -> str:
        ics = {p.ic_ref for p in self.ic_power_pins}
        rails = {p.net_name for p in self.ic_power_pins}
        local = [c for c in self.caps if c.classification == "local"]
        bulk = [c for c in self.caps if c.classification == "bulk"]
        errors = [f for f in self.findings if f.severity == "error"]
        warnings = [f for f in self.findings if f.severity == "warning"]
        lines = [
            f"Decoupling report: {len(ics)} ICs, {len(rails)} power rails",
            f"  Local caps: {len(local)}, Bulk caps: {len(bulk)}",
            f"  Associations: {len(self.associations)}",
            f"  Findings: {len(errors)} errors, {len(warnings)} warnings",
        ]
        if self.layout_available:
            lines.append("  Layout data: available")
        return "\n".join(lines)


@dataclass
class DecouplingThresholds:
    local_distance_warn_mm: float = 5.0
    bulk_distance_warn_mm: float = 15.0
    require_local_per_power_pin: bool = True
    require_bulk_per_ic: bool = False


def _parse_cap_value(val_str: str) -> float | None:
    if not val_str:
        return None
    from .registry import parse_value
    return parse_value(val_str)


def _net_name(net) -> str:
    return str(getattr(net, "name", "") or "")


def _is_power_net(name: str) -> bool:
    return bool(POWER_NET_RE.match(name))


def _is_ground_net(name: str) -> bool:
    return bool(GND_NET_RE.match(name))


def _pin_net_name(pin) -> str:
    net = getattr(pin, "net", None)
    if net is None:
        return ""
    return _net_name(net)


def _ref_prefix(ref: str) -> str:
    m = re.match(r"[A-Za-z]+", ref)
    return m.group(0).upper() if m else ""


def _find_ic_power_pins(circuit) -> list[ICPowerPin]:
    results = []
    for part in circuit.parts:
        prefix = _ref_prefix(part.ref)
        if prefix in {"R", "C", "L", "D", "F", "FB"}:
            continue
        pins = getattr(part, "pins", [])
        if len(pins) <= 2:
            continue
        for pin in pins:
            net_name = _pin_net_name(pin)
            if not net_name:
                continue
            func = getattr(pin, "func", None)
            pin_name = str(getattr(pin, "name", "") or "")
            pin_num = str(getattr(pin, "num", "") or "")

            if func == pin_types.PWRIN and _is_power_net(net_name):
                results.append(ICPowerPin(
                    ic_ref=part.ref,
                    pin_name=pin_name,
                    pin_num=pin_num,
                    net_name=net_name,
                    detection="pin_type",
                ))
            elif _is_power_net(net_name) and pin_name.upper() in {
                "VCC", "VDD", "VDDA", "AVDD", "DVDD", "IOVDD",
                "VBAT", "VBUS", "VIN",
            }:
                results.append(ICPowerPin(
                    ic_ref=part.ref,
                    pin_name=pin_name,
                    pin_num=pin_num,
                    net_name=net_name,
                    detection="net_name",
                ))
    return results


def _find_power_caps(circuit) -> list[CapInfo]:
    results = []
    for part in circuit.parts:
        prefix = _ref_prefix(part.ref)
        if prefix != "C":
            continue
        pins = getattr(part, "pins", [])
        if len(pins) != 2:
            continue
        n0 = _pin_net_name(pins[0])
        n1 = _pin_net_name(pins[1])
        power_net = None
        ground_net = None
        if _is_power_net(n0) and _is_ground_net(n1):
            power_net, ground_net = n0, n1
        elif _is_power_net(n1) and _is_ground_net(n0):
            power_net, ground_net = n1, n0
        if power_net is None:
            continue

        val_str = str(getattr(part, "value", "") or "")
        val_f = _parse_cap_value(val_str)

        if LOCAL_CAP_RE.match(val_str):
            classification = "local"
        elif val_f is not None and val_f >= BULK_CAP_VALUE_MIN:
            classification = "bulk"
        elif val_f is not None:
            classification = "local"
        else:
            classification = "unknown"

        results.append(CapInfo(
            ref=part.ref,
            value_str=val_str,
            value_f=val_f,
            classification=classification,
            power_net=power_net,
            ground_net=ground_net,
        ))
    return results


def _associate_caps(
    ic_pins: list[ICPowerPin],
    caps: list[CapInfo],
    placed: dict[str, tuple[float, float]] | None,
) -> list[CapAssociation]:
    associations = []
    for cap in caps:
        rail = cap.power_net
        matching_ics = {p.ic_ref for p in ic_pins if p.net_name == rail}
        for ic_ref in matching_ics:
            dist = None
            if placed and cap.ref in placed and ic_ref in placed:
                cx, cy = placed[cap.ref]
                ix, iy = placed[ic_ref]
                dist = math.hypot(cx - ix, cy - iy)
            associations.append(CapAssociation(
                cap_ref=cap.ref,
                ic_ref=ic_ref,
                rail=rail,
                distance_mm=dist,
                classification=cap.classification,
            ))
    return associations


def _generate_findings(
    ic_pins: list[ICPowerPin],
    caps: list[CapInfo],
    associations: list[CapAssociation],
    thresholds: DecouplingThresholds,
    layout_available: bool,
) -> list[DecouplingFinding]:
    findings = []

    ic_rails: dict[str, set[str]] = {}
    for pin in ic_pins:
        ic_rails.setdefault(pin.ic_ref, set()).add(pin.net_name)

    cap_by_rail: dict[str, list[CapInfo]] = {}
    for cap in caps:
        cap_by_rail.setdefault(cap.power_net, []).append(cap)

    for ic_ref, rails in ic_rails.items():
        for rail in rails:
            rail_caps = cap_by_rail.get(rail, [])
            ic_assocs = [a for a in associations
                         if a.ic_ref == ic_ref and a.rail == rail]
            local_assocs = [a for a in ic_assocs if a.classification == "local"]
            bulk_assocs = [a for a in ic_assocs if a.classification == "bulk"]

            if not rail_caps:
                findings.append(DecouplingFinding(
                    severity="error",
                    message=f"No decoupling cap on {rail} for {ic_ref}",
                    ic_ref=ic_ref,
                    rail=rail,
                    category="missing_decap",
                ))
                continue

            if thresholds.require_local_per_power_pin and not local_assocs:
                findings.append(DecouplingFinding(
                    severity="warning",
                    message=f"No local (~100nF) cap on {rail} for {ic_ref}",
                    ic_ref=ic_ref,
                    rail=rail,
                    category="missing_local",
                ))

            if thresholds.require_bulk_per_ic and not bulk_assocs:
                findings.append(DecouplingFinding(
                    severity="warning",
                    message=f"No bulk (>=1uF) cap on {rail} for {ic_ref}",
                    ic_ref=ic_ref,
                    rail=rail,
                    category="missing_bulk",
                ))

            if layout_available:
                for assoc in local_assocs:
                    if (assoc.distance_mm is not None
                            and assoc.distance_mm > thresholds.local_distance_warn_mm):
                        findings.append(DecouplingFinding(
                            severity="warning",
                            message=(
                                f"Local cap {assoc.cap_ref} is {assoc.distance_mm:.1f}mm "
                                f"from {ic_ref} (>{thresholds.local_distance_warn_mm}mm)"
                            ),
                            ic_ref=ic_ref,
                            rail=assoc.rail,
                            cap_ref=assoc.cap_ref,
                            category="far_local",
                        ))
                for assoc in bulk_assocs:
                    if (assoc.distance_mm is not None
                            and assoc.distance_mm > thresholds.bulk_distance_warn_mm):
                        findings.append(DecouplingFinding(
                            severity="info",
                            message=(
                                f"Bulk cap {assoc.cap_ref} is {assoc.distance_mm:.1f}mm "
                                f"from {ic_ref} (>{thresholds.bulk_distance_warn_mm}mm)"
                            ),
                            ic_ref=ic_ref,
                            rail=assoc.rail,
                            cap_ref=assoc.cap_ref,
                            category="far_bulk",
                        ))

    unknown_caps = [c for c in caps if c.classification == "unknown"]
    for cap in unknown_caps:
        findings.append(DecouplingFinding(
            severity="info",
            message=f"Cap {cap.ref} value '{cap.value_str}' could not be parsed",
            cap_ref=cap.ref,
            rail=cap.power_net,
            category="unknown_value",
        ))

    return findings


def analyze_decoupling(
    circuit=None,
    placed: dict[str, tuple[float, float]] | None = None,
    fp_bboxes: dict[str, tuple[float, float]] | None = None,
    thresholds: DecouplingThresholds | None = None,
) -> DecouplingReport:
    if circuit is None:
        import builtins
        circuit = builtins.default_circuit

    if thresholds is None:
        thresholds = DecouplingThresholds()

    ic_pins = _find_ic_power_pins(circuit)
    caps = _find_power_caps(circuit)
    layout_available = placed is not None and len(placed) > 0

    associations = _associate_caps(ic_pins, caps, placed)
    findings = _generate_findings(
        ic_pins, caps, associations, thresholds, layout_available,
    )

    return DecouplingReport(
        ic_power_pins=ic_pins,
        caps=caps,
        associations=associations,
        findings=findings,
        layout_available=layout_available,
    )
