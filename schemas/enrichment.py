"""Deterministic passive enrichment pass.

Applied AFTER LLM generates a CircuitSpec, BEFORE the engine runs.
Adds "obvious" passives that any competent EE would include but that
LLMs consistently forget: decoupling caps, pull-ups, CC resistors, etc.

Two categories:
  - Silent: always correct, no engineering judgment needed.
  - Loud:   sensible default added, but value is application-dependent.
            Logged so the user/model knows and can adjust.

Usage:
    from schemas.enrichment import enrich

    spec_dict, log = enrich(spec_dict)
    # log is a list of EnrichmentAction dicts
"""

from __future__ import annotations

import copy
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

# ---------------------------------------------------------------------------
# Detection regexes
# ---------------------------------------------------------------------------

POWER_PIN_RE = re.compile(
    r"^(V(CC|DD|DDIO|DDA|SSA|SS|IN|OUT|BAT|REF|BUS)|"
    r"A?V(CC|DD)|D?V(CC|DD)|IOV(DD))$",
    re.IGNORECASE,
)

GROUND_PIN_RE = re.compile(
    r"^(GND|A?GND|D?GND|VSS|AVSS|DVSS|EP)$",
    re.IGNORECASE,
)

POWER_NET_RE = re.compile(
    r"^(V(CC|DD|DDA|DDIO|SS|IN|OUT|BAT|REF|BUS)|"
    r"A?V(CC|DD)|D?V(CC|DD)|IOV(DD)|"
    r"\+\d+(\.\d+)?V(\d+)?|"
    r"\+3\.?3V?|\+5V?)$",
    re.IGNORECASE,
)

GROUND_NET_RE = re.compile(
    r"^(GND|A?GND|D?GND|VSS|AVSS|DVSS|GNDA|GNDD)$",
    re.IGNORECASE,
)

RESET_PIN_RE = re.compile(r"^[/~]?(N?RST|RESET|RSTN|nRESET)$", re.IGNORECASE)
BOOT_PIN_RE = re.compile(r"^BOOT[0-1]?$|^BOOTSEL$", re.IGNORECASE)
I2C_NET_RE = re.compile(r"^(SDA|SCL)\d*$", re.IGNORECASE)
SPI_CS_RE = re.compile(r"^[/~]?(CS[BN]?|SS|NSS|CE[0-9]?)$", re.IGNORECASE)
OPEN_DRAIN_RE = re.compile(
    r"^[/~]?(INT|IRQ|ALERT|DRDY|RDY|ALARM|SQW|nINT|INT_N)$", re.IGNORECASE
)
ADDR_PIN_RE = re.compile(r"^(ADDR|A[0-2]|ADD[0-1]|ADR[0-2])$", re.IGNORECASE)
USB_DATA_RE = re.compile(r"^(D[+-]|DP|DM|USB_D[PM]|USB_DP|USB_DM)$", re.IGNORECASE)
USB_CC_RE = re.compile(r"^CC[12]$", re.IGNORECASE)
DECAP_VALUE_RE = re.compile(r"^(100n|0\.1u)", re.IGNORECASE)
BULK_CAP_RE = re.compile(r"^(1[0-9]u|2[2-9]u|[3-9]\du|[1-9]\d\du|10u|22u|47u|100u)", re.IGNORECASE)

_PASSIVE_PREFIXES = {"R", "C", "L", "D", "J", "SW", "F", "FB"}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class EnrichmentAction:
    rule: str
    category: Literal["silent", "loud"]
    description: str
    parts_added: list[str] = field(default_factory=list)
    nets_modified: list[str] = field(default_factory=list)
    message: str = ""

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "category": self.category,
            "description": self.description,
            "parts_added": self.parts_added,
            "nets_modified": self.nets_modified,
            "message": self.message,
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_passive(ref: str) -> bool:
    prefix = re.match(r"[A-Za-z]+", ref)
    return prefix is not None and prefix.group(0).upper() in _PASSIVE_PREFIXES


def _next_ref(parts: list[dict], prefix: str) -> str:
    existing = [
        int(re.search(r"\d+", p["ref"]).group())
        for p in parts
        if p["ref"].startswith(prefix) and re.search(r"\d+", p["ref"])
    ]
    n = max(existing, default=0) + 1
    return f"{prefix}{n}"


def _part_on_net(net: dict, ref: str) -> bool:
    return any(p.startswith(f"{ref}.") for p in net.get("pins", []))


def _refs_on_net(net: dict) -> list[str]:
    return list({p.split(".")[0] for p in net.get("pins", []) if "." in p})


def _find_net_by_name(nets: list[dict], name: str) -> dict | None:
    for n in nets:
        if n.get("name", "").upper() == name.upper():
            return n
    return None


def _find_nets_for_ref(nets: list[dict], ref: str) -> list[dict]:
    return [n for n in nets if _part_on_net(n, ref)]


def _is_power_net(net: dict) -> bool:
    return net.get("power", False) or bool(POWER_NET_RE.match(net.get("name", "")))


def _is_ground_net(net: dict) -> bool:
    return bool(GROUND_NET_RE.match(net.get("name", "")))


def _get_power_nets_for_ref(nets: list[dict], ref: str) -> tuple[set[str], set[str]]:
    power_nets = set()
    ground_nets = set()
    for net in nets:
        if not _part_on_net(net, ref):
            continue
        name = net.get("name", "")
        if _is_ground_net(net):
            ground_nets.add(name)
        elif _is_power_net(net):
            power_nets.add(name)
    return power_nets, ground_nets


def _has_cap_on_net_pair(parts: list[dict], nets: list[dict],
                         power_net: str, ground_net: str,
                         min_value_re: re.Pattern = DECAP_VALUE_RE) -> bool:
    for p in parts:
        ref = p.get("ref", "")
        if not ref.startswith("C"):
            continue
        val = str(p.get("value", "") or "")
        if not min_value_re.match(val):
            continue
        on_power = False
        on_ground = False
        for net in nets:
            if not _part_on_net(net, ref):
                continue
            if net.get("name", "") == power_net:
                on_power = True
            if net.get("name", "") == ground_net:
                on_ground = True
        if on_power and on_ground:
            return True
    return False


def _has_resistor_to_power(parts: list[dict], nets: list[dict],
                           target_net_name: str) -> bool:
    for p in parts:
        ref = p.get("ref", "")
        if not ref.startswith("R"):
            continue
        on_target = False
        on_power = False
        for net in nets:
            if not _part_on_net(net, ref):
                continue
            if net.get("name", "") == target_net_name:
                on_target = True
            elif _is_power_net(net):
                on_power = True
        if on_target and on_power:
            return True
    return False


def _has_resistor_to_ground(parts: list[dict], nets: list[dict],
                            target_net_name: str) -> bool:
    for p in parts:
        ref = p.get("ref", "")
        if not ref.startswith("R"):
            continue
        on_target = False
        on_gnd = False
        for net in nets:
            if not _part_on_net(net, ref):
                continue
            if net.get("name", "") == target_net_name:
                on_target = True
            elif _is_ground_net(net):
                on_gnd = True
        if on_target and on_gnd:
            return True
    return False


def _add_part(parts: list[dict], prefix: str, lib: str, part_name: str,
              value: str, footprint: str, group: str | None) -> dict:
    ref = _next_ref(parts, prefix)
    p = {"ref": ref, "lib": lib, "part": part_name,
         "value": value, "footprint": footprint}
    if group:
        p["group"] = group
    parts.append(p)
    return p


def _add_pin_to_net(nets: list[dict], net_name: str, pin_ref: str,
                    power: bool = False) -> dict:
    net = _find_net_by_name(nets, net_name)
    if net is None:
        net = {"name": net_name, "power": power, "pins": []}
        nets.append(net)
    if pin_ref not in net["pins"]:
        net["pins"].append(pin_ref)
    return net


def _find_ground_net_name(nets: list[dict]) -> str:
    for n in nets:
        if _is_ground_net(n):
            return n["name"]
    return "GND"


# ---------------------------------------------------------------------------
# Individual rules
# ---------------------------------------------------------------------------

def _rule_a1_ic_decoupling(parts, nets, actions):
    """100nF decoupling cap per IC power/ground pair."""
    gnd_name = _find_ground_net_name(nets)
    for p in list(parts):
        ref = p.get("ref", "")
        if _is_passive(ref):
            continue
        power_nets, ground_nets = _get_power_nets_for_ref(nets, ref)
        if not power_nets or not ground_nets:
            continue
        for pn in sorted(power_nets):
            for gn in sorted(ground_nets):
                if _has_cap_on_net_pair(parts, nets, pn, gn, DECAP_VALUE_RE):
                    continue
                cap = _add_part(parts, "C", "Device", "C", "100nF",
                                "Capacitor_SMD:C_0603_1608Metric",
                                p.get("group"))
                _add_pin_to_net(nets, pn, f"{cap['ref']}.1", power=True)
                _add_pin_to_net(nets, gn, f"{cap['ref']}.2", power=True)
                actions.append(EnrichmentAction(
                    rule="A1", category="silent",
                    description=f"Added 100nF decoupling cap {cap['ref']} for {ref} ({pn}/{gn})",
                    parts_added=[cap["ref"]],
                    nets_modified=[pn, gn],
                ))


def _rule_a2_a3_regulator_caps(parts, nets, actions):
    """10uF input and output caps for voltage regulators."""
    regulator_libs = {"Regulator_Linear", "Regulator_Switching"}
    gnd_name = _find_ground_net_name(nets)

    for p in list(parts):
        lib = p.get("lib", "") or ""
        if lib not in regulator_libs:
            continue
        ref = p.get("ref", "")
        power_nets, ground_nets = _get_power_nets_for_ref(nets, ref)
        if not ground_nets:
            continue
        gn = sorted(ground_nets)[0]

        for pn in sorted(power_nets):
            if _has_cap_on_net_pair(parts, nets, pn, gn, BULK_CAP_RE):
                continue
            cap = _add_part(parts, "C", "Device", "C", "10uF",
                            "Capacitor_SMD:C_0805_2012Metric",
                            p.get("group"))
            _add_pin_to_net(nets, pn, f"{cap['ref']}.1", power=True)
            _add_pin_to_net(nets, gn, f"{cap['ref']}.2", power=True)
            which = "input/output"
            actions.append(EnrichmentAction(
                rule="A2/A3", category="silent",
                description=f"Added 10uF {which} cap {cap['ref']} for regulator {ref} on {pn}",
                parts_added=[cap["ref"]],
                nets_modified=[pn, gn],
            ))


def _rule_a4_usb_cc_pulldowns(parts, nets, actions):
    """5.1K pull-downs on USB-C CC1/CC2 pins."""
    has_usb_c = any(
        "USB_C" in str(p.get("part", "") or "").upper() or
        "USB_C" in str(p.get("footprint", "") or "").upper() or
        "Type-C" in str(p.get("footprint", "") or "")
        for p in parts
    )
    if not has_usb_c:
        return

    gnd_name = _find_ground_net_name(nets)
    for cc_name in ["CC1", "CC2"]:
        cc_net = _find_net_by_name(nets, cc_name)
        if cc_net is None:
            continue
        if _has_resistor_to_ground(parts, nets, cc_name):
            continue
        r = _add_part(parts, "R", "Device", "R", "5.1K",
                      "Resistor_SMD:R_0603_1608Metric", None)
        _add_pin_to_net(nets, cc_name, f"{r['ref']}.1")
        _add_pin_to_net(nets, gnd_name, f"{r['ref']}.2", power=True)
        actions.append(EnrichmentAction(
            rule="A4", category="silent",
            description=f"Added 5.1K USB-C {cc_name} pull-down {r['ref']}",
            parts_added=[r["ref"]],
            nets_modified=[cc_name, gnd_name],
        ))


def _rule_a5_bulk_power_input(parts, nets, actions):
    """10uF bulk cap on power input nets (VIN, VBUS, VBAT)."""
    gnd_name = _find_ground_net_name(nets)
    input_names = {"VIN", "VBUS", "VBAT"}

    for net in nets:
        name = net.get("name", "").upper()
        if name not in input_names:
            continue
        if _has_cap_on_net_pair(parts, nets, net["name"], gnd_name, BULK_CAP_RE):
            continue
        cap = _add_part(parts, "C", "Device", "C", "10uF",
                        "Capacitor_SMD:C_0805_2012Metric", None)
        _add_pin_to_net(nets, net["name"], f"{cap['ref']}.1", power=True)
        _add_pin_to_net(nets, gnd_name, f"{cap['ref']}.2", power=True)
        actions.append(EnrichmentAction(
            rule="A5", category="silent",
            description=f"Added 10uF bulk cap {cap['ref']} on {net['name']}",
            parts_added=[cap["ref"]],
            nets_modified=[net["name"], gnd_name],
        ))


def _rule_a7_a8_reset_circuit(parts, nets, actions):
    """10K pull-up + 100nF cap on reset pins."""
    for net in list(nets):
        name = net.get("name", "")
        if _is_power_net(net) or _is_ground_net(net):
            continue
        if not RESET_PIN_RE.match(name):
            pins_on_net = net.get("pins", [])
            has_reset_pin = False
            for pin_ref in pins_on_net:
                if "." in pin_ref:
                    pin_name = pin_ref.split(".", 1)[1]
                    if RESET_PIN_RE.match(pin_name):
                        has_reset_pin = True
                        break
            if not has_reset_pin:
                continue

        refs_on = _refs_on_net(net)
        ic_refs = [r for r in refs_on if not _is_passive(r)]
        if not ic_refs:
            continue

        ic_ref = ic_refs[0]
        ic_part = next((p for p in parts if p.get("ref") == ic_ref), None)
        power_nets, _ = _get_power_nets_for_ref(nets, ic_ref)
        vcc_name = sorted(power_nets)[0] if power_nets else "+3V3"
        gnd_name = _find_ground_net_name(nets)
        net_name = net["name"]

        if not _has_resistor_to_power(parts, nets, net_name):
            r = _add_part(parts, "R", "Device", "R", "10K",
                          "Resistor_SMD:R_0603_1608Metric",
                          ic_part.get("group") if ic_part else None)
            _add_pin_to_net(nets, vcc_name, f"{r['ref']}.1", power=True)
            _add_pin_to_net(nets, net_name, f"{r['ref']}.2")
            actions.append(EnrichmentAction(
                rule="A7", category="silent",
                description=f"Added 10K reset pull-up {r['ref']} on {net_name}",
                parts_added=[r["ref"]],
                nets_modified=[vcc_name, net_name],
            ))

        has_filter_cap = any(
            p.get("ref", "").startswith("C") and
            _part_on_net(net, p["ref"]) and
            any(_is_ground_net(n) and _part_on_net(n, p["ref"]) for n in nets)
            for p in parts
        )
        if not has_filter_cap:
            cap = _add_part(parts, "C", "Device", "C", "100nF",
                            "Capacitor_SMD:C_0603_1608Metric",
                            ic_part.get("group") if ic_part else None)
            _add_pin_to_net(nets, net_name, f"{cap['ref']}.1")
            _add_pin_to_net(nets, gnd_name, f"{cap['ref']}.2", power=True)
            actions.append(EnrichmentAction(
                rule="A8", category="silent",
                description=f"Added 100nF reset filter cap {cap['ref']} on {net_name}",
                parts_added=[cap["ref"]],
                nets_modified=[net_name, gnd_name],
            ))


def _rule_a14_open_drain_pullups(parts, nets, actions):
    """10K pull-up on open-drain INT/ALERT/DRDY pins."""
    for net in list(nets):
        name = net.get("name", "")
        if _is_power_net(net) or _is_ground_net(net):
            continue
        pins = net.get("pins", [])

        is_od_net = OPEN_DRAIN_RE.match(name)
        if not is_od_net:
            for pin_ref in pins:
                if "." in pin_ref:
                    pin_name = pin_ref.split(".", 1)[1]
                    if OPEN_DRAIN_RE.match(pin_name):
                        is_od_net = True
                        break
        if not is_od_net:
            continue

        refs_on = _refs_on_net(net)
        ic_refs = [r for r in refs_on if not _is_passive(r)]
        if not ic_refs:
            continue

        if _has_resistor_to_power(parts, nets, name):
            continue

        ic_ref = ic_refs[0]
        ic_part = next((p for p in parts if p.get("ref") == ic_ref), None)
        power_nets, _ = _get_power_nets_for_ref(nets, ic_ref)
        vcc_name = sorted(power_nets)[0] if power_nets else "+3V3"

        r = _add_part(parts, "R", "Device", "R", "10K",
                      "Resistor_SMD:R_0603_1608Metric",
                      ic_part.get("group") if ic_part else None)
        _add_pin_to_net(nets, vcc_name, f"{r['ref']}.1", power=True)
        _add_pin_to_net(nets, name, f"{r['ref']}.2")
        actions.append(EnrichmentAction(
            rule="A14", category="silent",
            description=f"Added 10K open-drain pull-up {r['ref']} on {name}",
            parts_added=[r["ref"]],
            nets_modified=[vcc_name, name],
        ))


def _rule_a6_led_current_limiter(parts, nets, actions):
    """1K series resistor for LEDs connected directly to power or IC pins."""
    led_refs = [
        p for p in parts
        if p.get("ref", "").startswith("D") and
        "LED" in str(p.get("part", "") or "").upper() and
        "WS2812" not in str(p.get("part", "") or "").upper() and
        "SK6812" not in str(p.get("part", "") or "").upper()
    ]

    for led in led_refs:
        ref = led["ref"]
        led_nets = _find_nets_for_ref(nets, ref)

        has_series_r = any(
            not _is_power_net(ln) and not _is_ground_net(ln) and
            any(p.get("ref", "").startswith("R") and _part_on_net(ln, p["ref"])
                for p in parts)
            for ln in led_nets
        )
        if has_series_r:
            continue

        power_net = None
        gnd_net = None
        signal_net = None
        for ln in led_nets:
            if _is_ground_net(ln):
                gnd_net = ln
            elif _is_power_net(ln):
                power_net = ln
            else:
                signal_net = ln

        if not power_net and not signal_net:
            continue

        drive_net = signal_net or power_net
        led_pin = next(
            (p for p in drive_net["pins"] if p.startswith(f"{ref}.")), None
        )
        if not led_pin:
            continue

        r = _add_part(parts, "R", "Device", "R", "1K",
                      "Resistor_SMD:R_0603_1608Metric",
                      led.get("group"))
        drive_net["pins"].remove(led_pin)
        new_net_name = f"LED_{ref}"
        _add_pin_to_net(nets, new_net_name, f"{r['ref']}.2")
        _add_pin_to_net(nets, new_net_name, led_pin)
        _add_pin_to_net(nets, drive_net["name"], f"{r['ref']}.1",
                        power=_is_power_net(drive_net))

        actions.append(EnrichmentAction(
            rule="A6", category="silent",
            description=f"Added 1K current-limiting resistor {r['ref']} for LED {ref}",
            parts_added=[r["ref"]],
            nets_modified=[drive_net["name"], new_net_name],
        ))


def _rule_a15_addr_pin_tying(parts, nets, actions):
    """Tie floating I2C address pins (A0/A1/A2/ADDR) to GND."""
    gnd_name = _find_ground_net_name(nets)

    all_connected_pins = set()
    for net in nets:
        for pin_ref in net.get("pins", []):
            all_connected_pins.add(pin_ref)

    for p in parts:
        ref = p.get("ref", "")
        if _is_passive(ref):
            continue
        pins_def = p.get("pins") or []
        for pin in pins_def:
            pin_name = pin.get("name", "") if isinstance(pin, dict) else ""
            pin_num = pin.get("num", "") if isinstance(pin, dict) else ""
            if not ADDR_PIN_RE.match(pin_name):
                continue
            pin_ref_name = f"{ref}.{pin_name}"
            pin_ref_num = f"{ref}.{pin_num}"
            if pin_ref_name in all_connected_pins or pin_ref_num in all_connected_pins:
                continue
            _add_pin_to_net(nets, gnd_name, pin_ref_name, power=True)
            actions.append(EnrichmentAction(
                rule="A15", category="silent",
                description=f"Tied floating address pin {pin_ref_name} to GND (default address)",
                nets_modified=[gnd_name],
            ))


def _rule_b1_i2c_pullups(parts, nets, actions):
    """10K pull-ups on I2C SDA/SCL nets."""
    for net in list(nets):
        name = net.get("name", "")
        if not I2C_NET_RE.match(name):
            continue

        refs_on = _refs_on_net(net)
        ic_refs = [r for r in refs_on if not _is_passive(r)]
        if not ic_refs:
            continue

        if _has_resistor_to_power(parts, nets, name):
            continue

        ic_ref = ic_refs[0]
        ic_part = next((p for p in parts if p.get("ref") == ic_ref), None)
        power_nets, _ = _get_power_nets_for_ref(nets, ic_ref)
        vcc_name = sorted(power_nets)[0] if power_nets else "+3V3"

        r = _add_part(parts, "R", "Device", "R", "4.7K",
                      "Resistor_SMD:R_0603_1608Metric",
                      ic_part.get("group") if ic_part else None)
        _add_pin_to_net(nets, vcc_name, f"{r['ref']}.1", power=True)
        _add_pin_to_net(nets, name, f"{r['ref']}.2")
        actions.append(EnrichmentAction(
            rule="B1", category="loud",
            description=f"Added 4.7K I2C pull-up {r['ref']} on {name}",
            parts_added=[r["ref"]],
            nets_modified=[vcc_name, name],
            message=f"Added 4.7K I2C pull-up on {name}. "
                    "Adafruit standard: 4.7K for 400kHz. "
                    "Use 10K for 100kHz, 2.2K for 1MHz.",
        ))


def _rule_b2_crystal_load_caps(parts, nets, actions):
    """20pF load caps on crystal oscillator pins."""
    gnd_name = _find_ground_net_name(nets)
    crystal_refs = [
        p for p in parts
        if (p.get("ref", "").startswith("Y") or
            "Crystal" in str(p.get("part", "") or ""))
    ]

    for crystal in crystal_refs:
        ref = crystal["ref"]
        crystal_nets = [n for n in nets if _part_on_net(n, ref)
                        and not _is_power_net(n) and not _is_ground_net(n)]

        for cn in crystal_nets:
            has_load_cap = any(
                p.get("ref", "").startswith("C") and
                _part_on_net(cn, p["ref"]) and
                any(_is_ground_net(n2) and _part_on_net(n2, p["ref"]) for n2 in nets)
                for p in parts
            )
            if has_load_cap:
                continue

            cap = _add_part(parts, "C", "Device", "C", "20pF",
                            "Capacitor_SMD:C_0603_1608Metric",
                            crystal.get("group"))
            _add_pin_to_net(nets, cn["name"], f"{cap['ref']}.1")
            _add_pin_to_net(nets, gnd_name, f"{cap['ref']}.2", power=True)
            actions.append(EnrichmentAction(
                rule="B2", category="loud",
                description=f"Added 20pF crystal load cap {cap['ref']} on {cn['name']}",
                parts_added=[cap["ref"]],
                nets_modified=[cn["name"], gnd_name],
                message=f"Added 20pF load cap on {cn['name']}. "
                        "Actual value depends on crystal CL spec. "
                        "Common: 6.8pF (32.768kHz), 10-22pF (MHz). "
                        "Formula: CLoad = 2*(CL - Cstray), Cstray ~3-5pF.",
            ))


def _rule_b3_spi_cs_pullup(parts, nets, actions):
    """10K pull-up on SPI chip-select lines."""
    for net in list(nets):
        name = net.get("name", "")
        if _is_power_net(net) or _is_ground_net(net):
            continue
        if not SPI_CS_RE.match(name):
            pins = net.get("pins", [])
            has_cs = any(
                "." in pr and SPI_CS_RE.match(pr.split(".", 1)[1])
                for pr in pins
            )
            if not has_cs:
                continue

        refs_on = _refs_on_net(net)
        ic_refs = [r for r in refs_on if not _is_passive(r)]
        if not ic_refs:
            continue

        if _has_resistor_to_power(parts, nets, name):
            continue

        ic_ref = ic_refs[0]
        ic_part = next((p for p in parts if p.get("ref") == ic_ref), None)
        power_nets, _ = _get_power_nets_for_ref(nets, ic_ref)
        vcc_name = sorted(power_nets)[0] if power_nets else "+3V3"

        r = _add_part(parts, "R", "Device", "R", "10K",
                      "Resistor_SMD:R_0603_1608Metric",
                      ic_part.get("group") if ic_part else None)
        _add_pin_to_net(nets, vcc_name, f"{r['ref']}.1", power=True)
        _add_pin_to_net(nets, name, f"{r['ref']}.2")
        actions.append(EnrichmentAction(
            rule="B3", category="loud",
            description=f"Added 10K SPI CS pull-up {r['ref']} on {name}",
            parts_added=[r["ref"]],
            nets_modified=[vcc_name, name],
            message=f"Added 10K pull-up on {name}. "
                    "Keeps SPI device deselected during MCU boot/reset.",
        ))


def _rule_b4_boot_pulldown(parts, nets, actions):
    """10K pull-down on BOOT/BOOTSEL pins."""
    gnd_name = _find_ground_net_name(nets)

    for net in list(nets):
        name = net.get("name", "")
        if not BOOT_PIN_RE.match(name):
            pins = net.get("pins", [])
            has_boot = any(
                "." in pr and BOOT_PIN_RE.match(pr.split(".", 1)[1])
                for pr in pins
            )
            if not has_boot:
                continue

        if _has_resistor_to_ground(parts, nets, name):
            continue

        refs_on = _refs_on_net(net)
        ic_refs = [r for r in refs_on if not _is_passive(r)]
        if not ic_refs:
            continue

        ic_part = next((p for p in parts if p.get("ref") == ic_refs[0]), None)
        r = _add_part(parts, "R", "Device", "R", "10K",
                      "Resistor_SMD:R_0603_1608Metric",
                      ic_part.get("group") if ic_part else None)
        _add_pin_to_net(nets, name, f"{r['ref']}.1")
        _add_pin_to_net(nets, gnd_name, f"{r['ref']}.2", power=True)
        actions.append(EnrichmentAction(
            rule="B4", category="loud",
            description=f"Added 10K BOOT pull-down {r['ref']} on {name}",
            parts_added=[r["ref"]],
            nets_modified=[name, gnd_name],
            message=f"Added 10K pull-down on {name}. "
                    "Defaults to normal flash boot. "
                    "Connect to VCC through a button for DFU/bootloader entry.",
        ))


# ---------------------------------------------------------------------------
# Block-level enrichment (multi-part functional blocks)
# ---------------------------------------------------------------------------

_BLOCK_TEMPLATES_DIR = Path(__file__).parent / "block_templates"

_REF_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


def _load_block_templates() -> list[dict]:
    if not _BLOCK_TEMPLATES_DIR.is_dir():
        return []
    templates = []
    for fp in sorted(_BLOCK_TEMPLATES_DIR.glob("*.json")):
        with open(fp) as f:
            templates.append(json.load(f))
    return templates


def _block_already_present(template: dict, parts: list[dict]) -> bool:
    """Check if the block's key component is already in the spec.

    Checks both the detection.ic_present list AND the template's own
    primary part (first part in the template) to avoid duplicates.
    """
    detection = template.get("detection", {})
    ic_names = detection.get("ic_present", [])

    # Check explicit IC names
    for ic_name in ic_names:
        pat = ic_name.upper()
        for p in parts:
            part_str = str(p.get("part", "") or "").upper()
            val_str = str(p.get("value", "") or "").upper()
            if pat in part_str or pat in val_str:
                return True

    # Check if the template's primary part (first) is already present
    # Only match on value (e.g. "STEMMA_QT", "MCP73831") — part name and
    # footprint are too generic (Conn_01x04, SOT-23-5) and cause false positives
    tpl_parts = template.get("parts", [])
    if tpl_parts:
        primary = tpl_parts[0]
        primary_val = str(primary.get("value", "") or "").upper()
        if primary_val:
            for p in parts:
                p_val = str(p.get("value", "") or "").upper()
                if not p_val:
                    continue
                if primary_val in p_val or p_val in primary_val:
                    return True

    return False


def _block_keywords_match(template: dict, marketing_text: str) -> bool:
    detection = template.get("detection", {})
    keywords = detection.get("keywords", [])
    if not keywords:
        return False
    text_lower = marketing_text.lower()
    return any(kw.lower() in text_lower for kw in keywords)


def _block_implied_by_spec(template: dict, parts: list[dict], nets: list[dict]) -> bool:
    """Detect if the LLM's own output implies this block should exist.

    If the LLM included a USB-C connector but forgot CC pull-downs, or put a
    charger IC but forgot the PROG resistor, the spec itself implies the block.
    We check for partial matches: interface nets present, or parts that
    suggest the block's function even if the main IC name doesn't match exactly.
    """
    detection = template.get("detection", {})
    interface_nets = detection.get("interface_nets", [])

    # Check if the spec's parts or nets suggest this block
    part_strings = set()
    for p in parts:
        for field in ("part", "value", "lib", "footprint"):
            val = str(p.get(field, "") or "").upper()
            if val:
                part_strings.add(val)

    net_names = {n.get("name", "").upper() for n in nets}

    tid = template["id"]

    if tid == "usb_c_input":
        return any("USB_C" in s or "USB-C" in s or "TYPE-C" in s for s in part_strings)

    if tid == "lipo_charger":
        charger_hints = ("MCP7383", "BQ2407", "TP4056", "LT3652", "CHARGER", "LIPO")
        has_charger_part = any(any(h in s for h in charger_hints) for s in part_strings)
        has_vbat = "VBAT" in net_names
        return has_charger_part or has_vbat

    if tid == "stemma_qt":
        return any("JST_SH" in s or "STEMMA" in s or "QWIIC" in s for s in part_strings)

    if tid == "neopixel_status":
        # Don't trigger if any addressable LED is already present
        return False

    if tid == "swd_header":
        return "SWDIO" in net_names and "SWCLK" in net_names

    if tid == "ldo_3v3":
        return False  # handled by _block_needs_regulator

    return False


def _block_needs_regulator(template: dict, parts: list[dict], nets: list[dict]) -> bool:
    """Special detection for LDO: only inject if +3V3 net exists but no regulator."""
    if template.get("id") != "ldo_3v3":
        return True
    has_3v3 = any(
        n.get("name", "") in ("+3V3", "3V3", "+3.3V")
        for n in nets
    )
    has_regulator = any(
        "Regulator" in str(p.get("lib", "") or "")
        for p in parts
    )
    return has_3v3 and not has_regulator


def enrich_blocks(
    spec_dict: dict,
    marketing_text: str,
) -> tuple[dict, list[dict]]:
    """Inject multi-part functional blocks based on marketing text keywords.

    Only injects a block if:
    1. Keywords from marketing text match the template's detection keywords
    2. The block's main IC is NOT already present in the spec
    3. Template-specific conditions are met (e.g., LDO only if no regulator exists)

    Runs BEFORE passive enrichment so injected ICs get decoupling caps etc.

    Returns (enriched_spec_dict, actions).
    """
    templates = _load_block_templates()
    if not templates:
        return spec_dict, []

    spec = copy.deepcopy(spec_dict)
    parts = spec.get("parts", [])
    nets = spec.get("nets", [])
    actions: list[EnrichmentAction] = []

    # Track highest existing ref number per prefix for fresh numbering
    ref_counters: dict[str, int] = {}
    for p in parts:
        m = _REF_PREFIX_RE.match(p.get("ref", ""))
        if m:
            prefix = m.group(1)
            num_match = re.search(r"\d+", p["ref"][len(prefix):])
            if num_match:
                num = int(num_match.group())
                ref_counters[prefix] = max(ref_counters.get(prefix, 100), num + 1)
    # Start block refs at 100+ to avoid collisions with LLM-generated refs
    for prefix in ref_counters:
        ref_counters[prefix] = max(ref_counters[prefix], 100)

    for template in templates:
        from_keywords = _block_keywords_match(template, marketing_text)
        from_spec = _block_implied_by_spec(template, parts, nets)
        if not from_keywords and not from_spec:
            continue
        if _block_already_present(template, parts):
            continue
        if not _block_needs_regulator(template, parts, nets):
            continue

        # Map template refs to fresh real refs
        ref_map: dict[str, str] = {}
        added_parts: list[str] = []

        for tpl_part in template["parts"]:
            tpl_ref = tpl_part["ref"]
            m = _REF_PREFIX_RE.match(tpl_ref)
            prefix = m.group(1) if m else "X"
            num = ref_counters.get(prefix, 100)
            ref_counters[prefix] = num + 1
            real_ref = f"{prefix}{num}"
            ref_map[tpl_ref] = real_ref

            new_part = {
                "ref": real_ref,
                "lib": tpl_part["lib"],
                "part": tpl_part["part"],
                "value": tpl_part.get("value"),
                "footprint": tpl_part["footprint"],
            }
            parts.append(new_part)
            added_parts.append(real_ref)

        # Merge nets: replace template refs with real refs, merge into existing nets
        modified_nets: list[str] = []
        for tpl_net in template["nets"]:
            net_name = tpl_net["name"]
            real_pins = []
            for pin in tpl_net["pins"]:
                # Replace template ref prefix with real ref
                tpl_ref, pin_name = pin.split(".", 1)
                real_ref = ref_map.get(tpl_ref, tpl_ref)
                real_pins.append(f"{real_ref}.{pin_name}")

            # Internal nets (prefixed with _) get unique names
            if net_name.startswith("_"):
                net_name = f"{net_name}_{template['id']}"

            existing_net = _find_net_by_name(nets, net_name)
            if existing_net is not None:
                for rp in real_pins:
                    if rp not in existing_net["pins"]:
                        existing_net["pins"].append(rp)
            else:
                is_power = _is_power_net({"name": net_name}) or _is_ground_net({"name": net_name})
                nets.append({"name": net_name, "power": is_power, "pins": real_pins})
            modified_nets.append(net_name)

        trigger = "keywords" if from_keywords else "spec-implied"
        actions.append(EnrichmentAction(
            rule=f"BLOCK:{template['id']}",
            category="silent",
            description=f"Injected {template['description']} ({len(added_parts)} parts, {trigger})",
            parts_added=added_parts,
            nets_modified=modified_nets,
            message=f"Block template '{template['id']}' triggered by {trigger}. "
                    f"Parts: {', '.join(added_parts)}",
        ))

    spec["parts"] = parts
    spec["nets"] = nets
    return spec, [a.to_dict() for a in actions]


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

ALL_RULES = [
    _rule_a1_ic_decoupling,
    _rule_a2_a3_regulator_caps,
    _rule_a4_usb_cc_pulldowns,
    _rule_a5_bulk_power_input,
    _rule_a6_led_current_limiter,
    _rule_a7_a8_reset_circuit,
    _rule_a14_open_drain_pullups,
    _rule_a15_addr_pin_tying,
    _rule_b1_i2c_pullups,
    _rule_b2_crystal_load_caps,
    _rule_b3_spi_cs_pullup,
    _rule_b4_boot_pulldown,
]


def _design_review_bulk_caps(parts, nets, exceptions, eid_counter):
    """Power rails with ICs but no bulk cap (10uF+) after enrichment."""
    from schemas.exceptions import (
        ActionType, Candidate, DesignException, ExcCode, Severity,
    )

    gnd_name = _find_ground_net_name(nets)
    ic_power_nets: set[str] = set()
    for p in parts:
        ref = p.get("ref", "")
        if _is_passive(ref):
            continue
        power_nets, _ = _get_power_nets_for_ref(nets, ref)
        ic_power_nets.update(power_nets)

    for pn in sorted(ic_power_nets):
        if _has_cap_on_net_pair(parts, nets, pn, gnd_name, BULK_CAP_RE):
            continue
        eid_counter[0] += 1
        eid = f"e-design-{eid_counter[0]}"
        cap_ref = _next_ref(parts, "C")
        exceptions.append(DesignException(
            id=eid,
            code=ExcCode.DESIGN_MISSING_BULK_CAP,
            severity=Severity.ADVISORY,
            message=f"No bulk capacitor (10uF+) on power rail {pn}",
            subject={"net": pn},
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.ADD_PARTS,
                    params={
                        "parts": [{"ref": cap_ref, "lib": "Device", "part": "C",
                                    "value": "10uF",
                                    "footprint": "Capacitor_SMD:C_0805_2012Metric"}],
                        "net_connections": [
                            {"net": pn, "pin": f"{cap_ref}.1"},
                            {"net": gnd_name, "pin": f"{cap_ref}.2"},
                        ],
                    },
                    human_summary=f"Add 10uF bulk capacitor on {pn}",
                    confidence=0.85,
                ),
                Candidate(
                    id="c2",
                    action=ActionType.ACCEPT_ADVISORY,
                    params={},
                    human_summary="Waive — bulk cap not needed for this design",
                    confidence=0.3,
                ),
            ],
        ))


def _design_review_connectors(parts, exceptions, eid_counter):
    """Board must have at least one connector."""
    from schemas.exceptions import (
        Candidate, DesignException, ExcCode, Severity, ActionType,
    )

    has_connector = any(
        p.get("ref", "").startswith("J") or
        "Connector" in str(p.get("lib", "") or "")
        for p in parts
    )
    if has_connector:
        return
    eid_counter[0] += 1
    exceptions.append(DesignException(
        id=f"e-design-{eid_counter[0]}",
        code=ExcCode.DESIGN_NO_CONNECTOR,
        severity=Severity.ERROR,
        message="No connectors on the board — how does the user connect to it?",
        subject={},
        candidates=[
            Candidate(
                id="c1",
                action=ActionType.ADD_PARTS,
                params={
                    "parts": [{"ref": "J1", "lib": "Connector_Generic",
                               "part": "Conn_01x04",
                               "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical"}],
                    "net_connections": [],
                },
                human_summary="Add a 4-pin header for power and I/O",
                confidence=0.5,
            ),
        ],
    ))


def _design_review_power_rails(parts, nets, exceptions, eid_counter):
    """Check power/ground rail existence and power flag."""
    from schemas.exceptions import (
        Candidate, DesignException, ExcCode, Severity, ActionType,
    )

    has_power = any(_is_power_net(n) for n in nets)
    has_ground = any(_is_ground_net(n) for n in nets)

    if not has_power:
        eid_counter[0] += 1
        exceptions.append(DesignException(
            id=f"e-design-{eid_counter[0]}",
            code=ExcCode.DESIGN_NO_POWER_RAIL,
            severity=Severity.ERROR,
            message="No power rail defined (VCC, 3V3, 5V, etc.)",
            subject={"missing": "power"},
            candidates=[],
            retry_hint="Add a power net with a standard name (VCC, 3V3, 5V) and set power=true",
        ))

    if not has_ground:
        eid_counter[0] += 1
        exceptions.append(DesignException(
            id=f"e-design-{eid_counter[0]}",
            code=ExcCode.DESIGN_NO_POWER_RAIL,
            severity=Severity.ERROR,
            message="No ground rail defined",
            subject={"missing": "ground"},
            candidates=[],
            retry_hint="Add a GND net with power=true",
        ))

    for net in nets:
        if (_is_power_net(net) or _is_ground_net(net)) and not net.get("power"):
            eid_counter[0] += 1
            exceptions.append(DesignException(
                id=f"e-design-{eid_counter[0]}",
                code=ExcCode.DESIGN_POWER_FLAG,
                severity=Severity.ADVISORY,
                message=f"Net '{net['name']}' looks like a power rail but power=true is not set",
                subject={"net": net["name"]},
                candidates=[
                    Candidate(
                        id="c1",
                        action=ActionType.ACCEPT_ADVISORY,
                        params={},
                        human_summary=f"Accept — {net['name']} is not a power rail",
                        confidence=0.3,
                    ),
                ],
                retry_hint=f"Set power=true on net '{net['name']}' for proper placement",
            ))


def design_review_exceptions(
    spec_dict: dict,
    marketing_text: str = "",
) -> list:
    """Post-enrichment design review that produces DesignException objects.

    Checks things enrichment doesn't handle: bulk caps on general power rails,
    connector presence, power rail existence/flags, and marketing-text
    feature cross-reference. Returns a list of DesignException objects.
    """
    parts = spec_dict.get("parts", [])
    nets = spec_dict.get("nets", [])
    exceptions: list = []
    eid_counter = [0]

    _design_review_bulk_caps(parts, nets, exceptions, eid_counter)
    _design_review_connectors(parts, exceptions, eid_counter)
    _design_review_power_rails(parts, nets, exceptions, eid_counter)

    if marketing_text:
        _design_review_marketing(parts, nets, marketing_text, exceptions, eid_counter)

    return exceptions


def _design_review_marketing(parts, nets, marketing_text, exceptions, eid_counter):
    """Cross-reference marketing text with spec components."""
    from schemas.exceptions import (
        Candidate, DesignException, ExcCode, Severity, ActionType,
    )

    marketing = marketing_text.lower()
    checks = [
        (["i2c", "i²c"], "I2C interface",
         lambda: any(I2C_NET_RE.match(n.get("name", "")) for n in nets)),
        (["spi"], "SPI interface",
         lambda: any(n.get("name", "").upper() in {"MOSI", "MISO", "SCK", "SCLK", "SDI", "SDO", "COPI", "CIPO"} for n in nets)),
        (["lipo", "lipoly", "battery charg", "rechargeable"], "Battery/LiPo charger",
         lambda: any(
             any(kw in str(p.get(f, "") or "").lower() for kw in ("mcp73831", "bq24", "tp4056", "battery_management"))
             for p in parts for f in ("part", "lib", "value"))),
        (["usb-c", "usb c", "type-c"], "USB-C connector",
         lambda: any("USB_C" in str(p.get("part", "") or "").upper() or "USB_C" in str(p.get("footprint", "") or "").upper() for p in parts)),
        (["neopixel", "ws2812"], "NeoPixel/addressable LED",
         lambda: any("WS2812" in str(p.get("part", "") or "").upper() for p in parts)),
        (["stemma", "qwiic"], "STEMMA QT/Qwiic connector",
         lambda: any("JST_SH" in str(p.get("footprint", "") or "") or "STEMMA" in str(p.get("value", "") or "").upper() for p in parts)),
        (["sense resistor", "shunt resistor", "current sense", "current measur"], "Current sense resistor",
         lambda: any(p.get("ref", "").startswith("R") and _is_low_ohm(str(p.get("value", "") or "")) for p in parts)),
        (["voltage regulator", "3.3v regulator", "ldo"], "Voltage regulator",
         lambda: any("Regulator" in str(p.get("lib", "") or "") for p in parts)),
    ]

    for keywords, feature_name, present_fn in checks:
        if not any(kw in marketing for kw in keywords):
            continue
        if present_fn():
            continue
        eid_counter[0] += 1
        exceptions.append(DesignException(
            id=f"e-design-{eid_counter[0]}",
            code=ExcCode.DESIGN_MISSING_FEATURE,
            severity=Severity.ADVISORY,
            message=f"Marketing mentions {feature_name} but not found in spec",
            subject={"feature": feature_name, "keywords_matched": [kw for kw in keywords if kw in marketing]},
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.ACCEPT_ADVISORY,
                    params={},
                    human_summary=f"Waive — {feature_name} not needed",
                    confidence=0.3,
                ),
            ],
        ))


def _is_low_ohm(val: str) -> bool:
    val = val.strip().lower()
    m = re.match(r"^([\d.]+)\s*(m?ohm|r|ω)?$", val)
    if m:
        try:
            v = float(m.group(1))
            if "mohm" in val or "mω" in val:
                return True
            return v < 1.0
        except ValueError:
            pass
    return bool(re.match(r"^0[rR.]", val))


def enrich(spec_dict: dict) -> tuple[dict, list[dict]]:
    """Apply all enrichment rules to a CircuitSpec dict.

    Returns (enriched_spec_dict, actions) where actions is a list of
    EnrichmentAction dicts describing what was added and why.
    """
    spec = copy.deepcopy(spec_dict)
    parts = spec.get("parts", [])
    nets = spec.get("nets", [])
    actions: list[EnrichmentAction] = []

    for rule_fn in ALL_RULES:
        rule_fn(parts, nets, actions)

    spec["parts"] = parts
    spec["nets"] = nets

    return spec, [a.to_dict() for a in actions]


def format_enrichment_log(actions: list[dict]) -> str:
    """Human-readable summary of enrichment actions."""
    if not actions:
        return "No enrichment needed — spec already complete."

    silent = [a for a in actions if a["category"] == "silent"]
    loud = [a for a in actions if a["category"] == "loud"]

    lines = [f"Enrichment: {len(actions)} changes ({len(silent)} silent, {len(loud)} flagged)"]
    lines.append("")

    if silent:
        lines.append("Silent fixes (always correct):")
        for a in silent:
            lines.append(f"  [{a['rule']}] {a['description']}")
        lines.append("")

    if loud:
        lines.append("Flagged additions (review defaults):")
        for a in loud:
            lines.append(f"  [{a['rule']}] {a['description']}")
            if a.get("message"):
                lines.append(f"         {a['message']}")
        lines.append("")

    total_parts = sum(len(a["parts_added"]) for a in actions)
    lines.append(f"Total: {total_parts} parts added")
    return "\n".join(lines)
