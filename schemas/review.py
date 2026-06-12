"""Pre-submission design review — flags likely missing components.

Unlike enrichment (which silently fixes), review returns suggestions
the agent sees BEFORE submitting. No oracle needed — uses general
PCB design rules and marketing-text cross-referencing.

Usage:
    from schemas.review import review_design
    issues = review_design(spec_dict, marketing_text="...")
    # returns list of ReviewIssue dicts
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from schemas.enrichment import (
    BULK_CAP_RE,
    DECAP_VALUE_RE,
    I2C_NET_RE,
    POWER_NET_RE,
    GROUND_NET_RE,
    RESET_PIN_RE,
    ADDR_PIN_RE,
    OPEN_DRAIN_RE,
    USB_CC_RE,
    _is_passive,
    _is_power_net,
    _is_ground_net,
    _part_on_net,
    _refs_on_net,
    _find_net_by_name,
    _find_nets_for_ref,
    _get_power_nets_for_ref,
    _has_cap_on_net_pair,
    _has_resistor_to_power,
    _has_resistor_to_ground,
    _find_ground_net_name,
)


@dataclass
class ReviewIssue:
    severity: str  # "error", "warning", "suggestion"
    category: str  # "decoupling", "pull-up", "bulk-cap", "connector", "completeness"
    message: str
    fix_hint: str = ""

    def to_dict(self) -> dict:
        d = {"severity": self.severity, "category": self.category, "message": self.message}
        if self.fix_hint:
            d["fix_hint"] = self.fix_hint
        return d


def review_design(spec_dict: dict, marketing_text: str = "") -> list[dict]:
    """Analyze a CircuitSpec for likely missing components.

    Returns a list of issue dicts, each with severity/category/message/fix_hint.
    """
    parts = spec_dict.get("parts", [])
    nets = spec_dict.get("nets", [])
    marketing = marketing_text.lower()
    issues: list[ReviewIssue] = []

    _check_ic_decoupling(parts, nets, issues)
    _check_bulk_caps(parts, nets, issues)
    _check_i2c_pullups(parts, nets, issues)
    _check_addr_pins(parts, nets, issues)
    _check_reset_pins(parts, nets, issues)
    _check_open_drain_pullups(parts, nets, issues)
    _check_usb_cc(parts, nets, issues)
    _check_connectors(parts, nets, issues)
    _check_power_rails(parts, nets, issues)
    _check_marketing_cross_ref(parts, nets, marketing, issues)

    return [i.to_dict() for i in issues]


# ---------------------------------------------------------------------------
# Structural checks (no marketing text needed)
# ---------------------------------------------------------------------------

def _check_ic_decoupling(parts, nets, issues):
    """Every IC with power pins should have a 100nF decoupling cap."""
    gnd_name = _find_ground_net_name(nets)
    for p in parts:
        ref = p.get("ref", "")
        if _is_passive(ref):
            continue
        power_nets, ground_nets = _get_power_nets_for_ref(nets, ref)
        if not power_nets or not ground_nets:
            continue
        for pn in sorted(power_nets):
            for gn in sorted(ground_nets):
                if not _has_cap_on_net_pair(parts, nets, pn, gn, DECAP_VALUE_RE):
                    issues.append(ReviewIssue(
                        severity="warning",
                        category="decoupling",
                        message=f"{ref} has no 100nF decoupling cap between {pn} and {gn}",
                        fix_hint=f"Add a 100nF cap (lib=Device, part=C, value=100nF) with pin 1 on {pn} and pin 2 on {gn}",
                    ))


def _check_bulk_caps(parts, nets, issues):
    """ICs with power pins should have a bulk cap (10uF+) on the power rail."""
    gnd_name = _find_ground_net_name(nets)
    ic_power_nets = set()
    for p in parts:
        ref = p.get("ref", "")
        if _is_passive(ref):
            continue
        power_nets, ground_nets = _get_power_nets_for_ref(nets, ref)
        ic_power_nets.update(power_nets)

    for pn in sorted(ic_power_nets):
        if not _has_cap_on_net_pair(parts, nets, pn, gnd_name, BULK_CAP_RE):
            issues.append(ReviewIssue(
                severity="suggestion",
                category="bulk-cap",
                message=f"No bulk capacitor (10uF+) on power rail {pn}",
                fix_hint=f"Add a 10uF cap (lib=Device, part=C, value=10uF, footprint=Capacitor_SMD:C_0805_2012Metric) between {pn} and {gnd_name}",
            ))


def _check_i2c_pullups(parts, nets, issues):
    """I2C SDA/SCL nets need pull-up resistors."""
    for net in nets:
        name = net.get("name", "")
        if not I2C_NET_RE.match(name):
            continue
        if _is_power_net(net) or _is_ground_net(net):
            continue
        if not _has_resistor_to_power(parts, nets, name):
            issues.append(ReviewIssue(
                severity="warning",
                category="pull-up",
                message=f"I2C line {name} has no pull-up resistor",
                fix_hint=f"Add a 4.7K or 10K resistor from {name} to VCC/3V3",
            ))


def _check_addr_pins(parts, nets, issues):
    """Address pins (A0, A1, A2) should be tied to VCC or GND."""
    for net in nets:
        name = net.get("name", "")
        if not ADDR_PIN_RE.match(name):
            continue
        if _is_power_net(net) or _is_ground_net(net):
            continue
        refs = _refs_on_net(net)
        has_pullup = _has_resistor_to_power(parts, nets, name)
        has_pulldown = _has_resistor_to_ground(parts, nets, name)
        if not has_pullup and not has_pulldown:
            directly_on_power = any(
                _find_net_by_name(nets, name) is not None
                and (_is_power_net({"name": name}) or _is_ground_net({"name": name}))
                for _ in [None]
            )
            if not directly_on_power:
                issues.append(ReviewIssue(
                    severity="suggestion",
                    category="pull-up",
                    message=f"Address pin {name} is floating — not tied to VCC or GND",
                    fix_hint=f"Add a 10K pull-down resistor from {name} to GND to set a default address",
                ))


def _check_reset_pins(parts, nets, issues):
    """Reset pins should have a pull-up and optional decoupling cap."""
    for net in nets:
        name = net.get("name", "")
        if _is_power_net(net) or _is_ground_net(net):
            continue
        has_reset = RESET_PIN_RE.match(name)
        if not has_reset:
            for pin_ref in net.get("pins", []):
                if "." in pin_ref:
                    pin_name = pin_ref.split(".", 1)[1]
                    if RESET_PIN_RE.match(pin_name):
                        has_reset = True
                        break
        if not has_reset:
            continue
        if not _has_resistor_to_power(parts, nets, name):
            issues.append(ReviewIssue(
                severity="warning",
                category="pull-up",
                message=f"Reset line {name} has no pull-up resistor",
                fix_hint=f"Add a 10K pull-up resistor from {name} to VCC",
            ))


def _check_open_drain_pullups(parts, nets, issues):
    """Open-drain outputs (INT, ALERT, DRDY) need pull-ups."""
    for net in nets:
        name = net.get("name", "")
        if _is_power_net(net) or _is_ground_net(net):
            continue
        has_od = OPEN_DRAIN_RE.match(name)
        if not has_od:
            for pin_ref in net.get("pins", []):
                if "." in pin_ref:
                    pin_name = pin_ref.split(".", 1)[1]
                    if OPEN_DRAIN_RE.match(pin_name):
                        has_od = True
                        break
        if not has_od:
            continue
        if not _has_resistor_to_power(parts, nets, name):
            issues.append(ReviewIssue(
                severity="suggestion",
                category="pull-up",
                message=f"Open-drain output {name} has no pull-up resistor",
                fix_hint=f"Add a 10K pull-up resistor from {name} to VCC",
            ))


def _check_usb_cc(parts, nets, issues):
    """USB-C connectors need CC1/CC2 pull-down resistors."""
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
            issues.append(ReviewIssue(
                severity="warning",
                category="usb",
                message=f"USB-C connector present but no {cc_name} net defined",
                fix_hint=f"Add a {cc_name} net with a 5.1K pull-down to GND",
            ))
        elif not _has_resistor_to_ground(parts, nets, cc_name):
            issues.append(ReviewIssue(
                severity="warning",
                category="usb",
                message=f"USB-C {cc_name} pin has no 5.1K pull-down resistor",
                fix_hint=f"Add a 5.1K resistor from {cc_name} to GND",
            ))


def _check_connectors(parts, nets, issues):
    """Board should have at least one connector for external access."""
    has_connector = any(
        p.get("ref", "").startswith("J") or
        "Connector" in str(p.get("lib", "") or "")
        for p in parts
    )
    if not has_connector:
        issues.append(ReviewIssue(
            severity="error",
            category="connector",
            message="No connectors on the board — how does the user connect to it?",
            fix_hint="Add pin headers (lib=Connector_Generic, part=Conn_01xNN) for power, signals, and I/O",
        ))


def _check_power_rails(parts, nets, issues):
    """Check power rail naming and presence."""
    has_power = any(_is_power_net(n) for n in nets)
    has_ground = any(_is_ground_net(n) for n in nets)

    if not has_power:
        issues.append(ReviewIssue(
            severity="error",
            category="power",
            message="No power rail defined (VCC, 3V3, 5V, etc.)",
            fix_hint="Add a power net with a standard name (VCC, 3V3, 5V) and set power=true",
        ))
    if not has_ground:
        issues.append(ReviewIssue(
            severity="error",
            category="power",
            message="No ground rail defined",
            fix_hint="Add a GND net with power=true",
        ))

    for net in nets:
        if (_is_power_net(net) or _is_ground_net(net)) and not net.get("power"):
            issues.append(ReviewIssue(
                severity="warning",
                category="power",
                message=f"Net '{net['name']}' looks like a power rail but power=true is not set",
                fix_hint=f"Set power=true on net '{net['name']}' for proper placement",
            ))


# ---------------------------------------------------------------------------
# Marketing text cross-referencing
# ---------------------------------------------------------------------------

_MARKETING_CHECKS = [
    {
        "keywords": ["i2c", "i²c", "iic"],
        "check": "i2c_bus",
        "description": "I2C interface mentioned",
    },
    {
        "keywords": ["spi"],
        "check": "spi_bus",
        "description": "SPI interface mentioned",
    },
    {
        "keywords": ["sense resistor", "shunt resistor", "current sense", "current measuring"],
        "check": "sense_resistor",
        "description": "Current sensing mentioned",
    },
    {
        "keywords": ["lipo", "lipoly", "battery charg", "rechargeable"],
        "check": "battery_charger",
        "description": "Battery/LiPo charging mentioned",
    },
    {
        "keywords": ["usb-c", "usb c", "type-c", "type c"],
        "check": "usb_c",
        "description": "USB-C mentioned",
    },
    {
        "keywords": ["neopixel", "ws2812", "addressable led", "rgb led"],
        "check": "neopixel",
        "description": "NeoPixel/addressable LED mentioned",
    },
    {
        "keywords": ["stemma", "qwiic", "jst-sh"],
        "check": "stemma_qt",
        "description": "STEMMA QT/Qwiic connector mentioned",
    },
    {
        "keywords": ["led indicator", "status led", "power led"],
        "check": "status_led",
        "description": "Status LED mentioned",
    },
    {
        "keywords": ["crystal", "oscillator", "xtal"],
        "check": "crystal",
        "description": "Crystal oscillator mentioned",
    },
    {
        "keywords": ["voltage regulator", "3.3v regulator", "ldo", "vreg"],
        "check": "regulator",
        "description": "Voltage regulator mentioned",
    },
]


def _check_marketing_cross_ref(parts, nets, marketing, issues):
    """Cross-reference marketing text with spec components."""
    if not marketing:
        return

    for check in _MARKETING_CHECKS:
        if not any(kw in marketing for kw in check["keywords"]):
            continue

        checker = _MARKETING_CHECKERS.get(check["check"])
        if checker:
            checker(parts, nets, marketing, issues)


def _check_mktg_i2c_bus(parts, nets, marketing, issues):
    """Marketing mentions I2C — do we have SDA/SCL nets?"""
    has_i2c = any(I2C_NET_RE.match(n.get("name", "")) for n in nets)
    if not has_i2c:
        issues.append(ReviewIssue(
            severity="warning",
            category="completeness",
            message="Marketing mentions I2C but no SDA/SCL nets in the spec",
            fix_hint="Add SDA and SCL nets connecting the IC's I2C pins to pull-up resistors and a connector",
        ))


def _check_mktg_spi_bus(parts, nets, marketing, issues):
    """Marketing mentions SPI — do we have MOSI/MISO/SCK?"""
    spi_names = {"MOSI", "MISO", "SCK", "SCLK", "SDI", "SDO", "COPI", "CIPO"}
    has_spi = any(n.get("name", "").upper() in spi_names for n in nets)
    if not has_spi:
        issues.append(ReviewIssue(
            severity="warning",
            category="completeness",
            message="Marketing mentions SPI but no SPI nets (MOSI/MISO/SCK) in the spec",
            fix_hint="Add SPI signal nets connecting the IC to a connector",
        ))


def _check_mktg_sense_resistor(parts, nets, marketing, issues):
    """Marketing mentions current sensing — is there a low-value sense resistor?"""
    has_sense = any(
        p.get("ref", "").startswith("R") and
        _is_low_ohm_value(str(p.get("value", "") or ""))
        for p in parts
    )
    if not has_sense:
        issues.append(ReviewIssue(
            severity="error",
            category="completeness",
            message="Marketing describes current sensing but no low-value sense resistor found in spec",
            fix_hint="Add a sense resistor (e.g. 0.1 ohm, footprint R_2512 for power handling) in the current path",
        ))


def _check_mktg_battery_charger(parts, nets, marketing, issues):
    """Marketing mentions LiPo/battery — is there a charger IC?"""
    charger_keywords = ["MCP73831", "BQ24", "TP4056", "charger", "Battery_Management"]
    has_charger = any(
        any(kw.lower() in str(p.get(f, "") or "").lower() for kw in charger_keywords for f in ("part", "lib", "value"))
        for p in parts
    )
    if not has_charger:
        issues.append(ReviewIssue(
            severity="warning",
            category="completeness",
            message="Marketing mentions battery/LiPo but no charger IC found",
            fix_hint="Add a LiPo charger IC (e.g. MCP73831 from Battery_Management library) with PROG resistor and caps",
        ))


def _check_mktg_usb_c(parts, nets, marketing, issues):
    """Marketing mentions USB-C — is there a USB-C connector?"""
    has_usb_c = any(
        "USB_C" in str(p.get("part", "") or "").upper() or
        "USB_C" in str(p.get("footprint", "") or "").upper()
        for p in parts
    )
    if not has_usb_c:
        issues.append(ReviewIssue(
            severity="warning",
            category="completeness",
            message="Marketing mentions USB-C but no USB-C connector in spec",
            fix_hint="Add a USB-C connector (lib=Connector_USB) with CC pull-down resistors",
        ))


def _check_mktg_neopixel(parts, nets, marketing, issues):
    """Marketing mentions NeoPixel — is there a WS2812?"""
    has_neo = any(
        "WS2812" in str(p.get("part", "") or "").upper() or
        "NEOPIXEL" in str(p.get("value", "") or "").upper()
        for p in parts
    )
    if not has_neo:
        issues.append(ReviewIssue(
            severity="suggestion",
            category="completeness",
            message="Marketing mentions NeoPixel but no WS2812 LED in spec",
            fix_hint="Add a WS2812B LED (lib=LED, footprint=LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm) with 100nF decoupling",
        ))


def _check_mktg_stemma_qt(parts, nets, marketing, issues):
    """Marketing mentions STEMMA QT/Qwiic — is there a JST-SH 4-pin?"""
    has_stemma = any(
        "JST_SH" in str(p.get("footprint", "") or "") or
        "Qwiic" in str(p.get("value", "") or "") or
        "STEMMA" in str(p.get("value", "") or "").upper()
        for p in parts
    )
    if not has_stemma:
        issues.append(ReviewIssue(
            severity="suggestion",
            category="completeness",
            message="Marketing mentions STEMMA QT/Qwiic but no JST-SH connector found",
            fix_hint="Add a 4-pin JST-SH connector (lib=Connector_JST) for the Qwiic/STEMMA QT I2C port",
        ))


def _check_mktg_status_led(parts, nets, marketing, issues):
    """Marketing mentions status LED."""
    has_led = any(
        p.get("ref", "").startswith("D") and "LED" in str(p.get("lib", "") or "").upper()
        for p in parts
    )
    if not has_led:
        issues.append(ReviewIssue(
            severity="suggestion",
            category="completeness",
            message="Marketing mentions a status/power LED but none in spec",
            fix_hint="Add an LED (lib=Device, part=LED) with a current-limiting resistor (~1K for 3.3V)",
        ))


def _check_mktg_crystal(parts, nets, marketing, issues):
    """Marketing mentions crystal oscillator."""
    has_crystal = any(
        p.get("ref", "").startswith("Y") or
        "Crystal" in str(p.get("lib", "") or "")
        for p in parts
    )
    if not has_crystal:
        issues.append(ReviewIssue(
            severity="suggestion",
            category="completeness",
            message="Marketing mentions a crystal/oscillator but none in spec",
            fix_hint="Add a crystal (lib=Device, part=Crystal) with load capacitors",
        ))


def _check_mktg_regulator(parts, nets, marketing, issues):
    """Marketing mentions voltage regulator."""
    has_reg = any(
        "Regulator" in str(p.get("lib", "") or "")
        for p in parts
    )
    if not has_reg:
        issues.append(ReviewIssue(
            severity="suggestion",
            category="completeness",
            message="Marketing mentions a voltage regulator but none in spec",
            fix_hint="Add a voltage regulator (e.g. AP2112K-3.3 from Regulator_Linear) with input/output caps",
        ))


def _is_low_ohm_value(val: str) -> bool:
    """Check if a resistor value is low enough to be a sense resistor."""
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
    if re.match(r"^0[rR.]", val):
        return True
    return False


_MARKETING_CHECKERS = {
    "i2c_bus": _check_mktg_i2c_bus,
    "spi_bus": _check_mktg_spi_bus,
    "sense_resistor": _check_mktg_sense_resistor,
    "battery_charger": _check_mktg_battery_charger,
    "usb_c": _check_mktg_usb_c,
    "neopixel": _check_mktg_neopixel,
    "stemma_qt": _check_mktg_stemma_qt,
    "status_led": _check_mktg_status_led,
    "crystal": _check_mktg_crystal,
    "regulator": _check_mktg_regulator,
}
