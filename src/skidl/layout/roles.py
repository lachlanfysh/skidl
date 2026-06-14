from __future__ import annotations

import re
from dataclasses import dataclass, field


POWER_NET_RE = re.compile(
    r"^("
    r"VCC|VDD|VDDA|VDDD|AVDD|DVDD|IOVDD|"
    r"VBUS|VIN|VOUT|VRAW|VBAT|BAT|BATT|VREF|V\d+|"
    r"[+-]?\d+(?:V\d*|\.\d+V)"
    r")$",
    re.IGNORECASE,
)
GND_NET_RE = re.compile(r"^(GND|VSS|DGND|AGND|GNDA|GNDD)$", re.IGNORECASE)
DECAP_VALUE_RE = re.compile(r"^(100n|0\.1u)", re.IGNORECASE)
PANEL_JACK_RE = re.compile(
    r"(thonk|pj398|pj301|audio.?jack|3\.5\s*mm|eurorack.?jack|mono.?jack|stereo.?jack)",
    re.IGNORECASE,
)
POWER_JACK_RE = re.compile(r"(dc.?jack|barrel|power.?jack)", re.IGNORECASE)


@dataclass
class PartRole:
    ref: str
    role: str
    confidence: float
    reasons: list[str] = field(default_factory=list)


def _ref_prefix(part) -> str:
    ref = str(getattr(part, "ref", "") or "")
    match = re.match(r"[A-Za-z]+", ref)
    return match.group(0).upper() if match else ""


def _part_text(part) -> str:
    chunks = [
        getattr(part, "ref", ""),
        getattr(part, "name", ""),
        getattr(part, "value", ""),
        getattr(part, "foot", ""),
        getattr(part, "footprint", ""),
        getattr(part, "description", ""),
    ]
    return " ".join(str(chunk or "") for chunk in chunks).lower()


def _pin_count(part) -> int:
    try:
        return len(part)
    except Exception:
        return len(getattr(part, "pins", []) or [])


def pin_net_names(part) -> list[str]:
    names = []
    try:
        from skidl.net import NCNet

        for pin in getattr(part, "pins", []) or []:
            net = getattr(pin, "net", None)
            if net is not None and not isinstance(net, NCNet):
                name = getattr(net, "name", None)
                if name:
                    names.append(str(name))
    except Exception:
        pass
    return names


def has_power_and_ground(part) -> bool:
    nets = pin_net_names(part)
    return any(POWER_NET_RE.match(n) for n in nets) and any(
        GND_NET_RE.match(n) for n in nets
    )


def classify_part(part) -> PartRole:
    ref = str(getattr(part, "ref", "") or "")
    prefix = _ref_prefix(part)
    text = _part_text(part)
    pin_count = _pin_count(part)
    reasons: list[str] = []

    if (
        prefix == "C"
        and pin_count == 2
        and DECAP_VALUE_RE.match(str(getattr(part, "value", "") or ""))
    ):
        if has_power_and_ground(part):
            return PartRole(
                ref,
                "decoupling_cap",
                0.95,
                ["2-pin capacitor on power and ground"],
            )

    normalized_text = text.replace("_", " ").replace("-", " ")
    if (
        prefix in {"H", "MH"}
        or "mountinghole" in text
        or "mounting hole" in normalized_text
        or "mountinghole" in str(getattr(part, "footprint", "") or "").lower()
    ):
        reasons.append("mechanical mounting-hole reference or footprint")
        return PartRole(ref, "mounting_hole", 0.95, reasons)

    if prefix in {"SW", "S", "RV", "POT"} or any(
        term in text
        for term in (
            "switch",
            "button",
            "potentiometer",
            "pot ",
            "trimmer",
            "encoder",
        )
    ):
        reasons.append("panel/user-control reference or metadata")
        return PartRole(ref, "control", 0.85, reasons)

    if PANEL_JACK_RE.search(text) and not POWER_JACK_RE.search(text):
        reasons.append("panel/audio jack metadata")
        return PartRole(ref, "panel_jack", 0.9, reasons)

    if prefix in {"J", "P", "CON", "CN"} or any(
        term in text for term in ("connector", "header", "usb", "jack", "terminal")
    ):
        reasons.append("connector-like reference or metadata")
        return PartRole(ref, "connector", 0.9, reasons)

    if prefix in {"Y", "X"} or any(
        term in text for term in ("crystal", "resonator", "oscillator")
    ):
        reasons.append("timing-source reference or metadata")
        return PartRole(ref, "crystal", 0.85, reasons)

    if prefix == "L":
        return PartRole(ref, "inductor", 0.85, ["inductor reference prefix"])

    if prefix == "D":
        return PartRole(ref, "diode", 0.8, ["diode reference prefix"])

    if any(
        term in text
        for term in ("regulator", "ldo", "buck", "boost", "dcdc", "dc-dc", "converter")
    ):
        return PartRole(ref, "regulator", 0.85, ["power-regulator metadata"])

    if prefix == "U" or pin_count > 2:
        reasons.append("IC-like reference or pin count")
        return PartRole(ref, "ic", 0.75, reasons)

    if prefix in {"R", "C"} and pin_count == 2:
        return PartRole(ref, "signal_passive", 0.7, ["2-pin passive"])

    return PartRole(ref, "unknown", 0.1, [])


def classify_parts(circuit) -> dict[str, PartRole]:
    return {part.ref: classify_part(part) for part in circuit.parts}
