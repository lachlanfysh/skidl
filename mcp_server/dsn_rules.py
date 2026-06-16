"""Semantic net class generation for Freerouting DSN files.

Replaces KiCad's flat kicad_default class with circuit-aware net classes:
power nets get wider traces, analog gets extra clearance, etc.

The DSN format's (class ...) entries control per-net routing rules.
Freerouting respects width, clearance, via selection, and layer restrictions.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# ---------------------------------------------------------------------------
# Net classification regexes (aligned with schemas/enrichment.py)
# ---------------------------------------------------------------------------

POWER_NET_RE = re.compile(
    r"^(V(CC|DD|DDA|DDIO|SS|IN|OUT|BAT|REF|BUS)|"
    r"A?V(CC|DD)|D?V(CC|DD)|IOV(DD)|"
    r"[-+]?\d+(\.\d+)?V(\d+)?|"
    r"[-+]?3\.?3V?|[-+]?5V?)$",
    re.IGNORECASE,
)

GROUND_NET_RE = re.compile(
    r"^(GND|A?GND|D?GND|VSS|AVSS|DVSS|GNDA|GNDD)$",
    re.IGNORECASE,
)

I2C_RE = re.compile(r"^(SDA|SCL)\d*$", re.IGNORECASE)
SPI_RE = re.compile(r"^(MOSI|MISO|SCK|SCLK|SDI|SDO|COPI|CIPO)\d*$", re.IGNORECASE)
USB_RE = re.compile(r"^(D[+-]|DP|DM|USB_D[PM]|USB_DP|USB_DM)$", re.IGNORECASE)
ANALOG_RE = re.compile(r"^(AIN\d+|ADC\d*|SENSE|VREF|AOUT\d*|AN\d+)$", re.IGNORECASE)
CLOCK_RE = re.compile(r"^(CLK|XTAL|OSC|XI|XO)\d*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Net class definitions
# ---------------------------------------------------------------------------

@dataclass
class NetClassDef:
    """Routing rules for a group of nets."""
    name: str
    width_um: int
    clearance_um: int
    via_drill_um: int = 300
    via_pad_um: int = 600
    description: str = ""


POWER_CLASS = NetClassDef(
    name="Power",
    width_um=250,      # fine-pitch-safe default; widen only with explicit policy
    clearance_um=200,  # standard
    via_drill_um=300,
    via_pad_um=600,
    description="Power supply rails — fine default escape traces",
)

GROUND_CLASS = NetClassDef(
    name="Ground",
    width_um=250,
    clearance_um=200,
    via_drill_um=300,
    via_pad_um=600,
    description="Ground rails — fine default escape traces",
)

ANALOG_CLASS = NetClassDef(
    name="Analog",
    width_um=250,
    clearance_um=300,  # extra clearance for noise isolation
    via_drill_um=300,
    via_pad_um=600,
    description="Analog signals — extra clearance from digital",
)

BUS_CLASS = NetClassDef(
    name="Bus",
    width_um=250,
    clearance_um=200,
    description="I2C/SPI/digital bus signals",
)

USB_CLASS = NetClassDef(
    name="USB",
    width_um=250,
    clearance_um=200,
    description="USB data lines",
)

CLOCK_CLASS = NetClassDef(
    name="Clock",
    width_um=200,
    clearance_um=250,  # extra clearance to reduce coupling
    description="Clock/crystal signals — minimize coupling",
)

SIGNAL_CLASS = NetClassDef(
    name="Signal",
    width_um=250,
    clearance_um=200,
    description="Default digital signals",
)


# ---------------------------------------------------------------------------
# Net classifier
# ---------------------------------------------------------------------------

def classify_nets(net_names: list[str]) -> dict[str, NetClassDef]:
    """Classify each net name into a routing net class.

    Returns {net_name: NetClassDef} for every net.
    """
    result: dict[str, NetClassDef] = {}

    for name in net_names:
        if not name or name == '""':
            result[name] = SIGNAL_CLASS
        elif GROUND_NET_RE.match(name):
            result[name] = GROUND_CLASS
        elif POWER_NET_RE.match(name):
            result[name] = POWER_CLASS
        elif ANALOG_RE.match(name):
            result[name] = ANALOG_CLASS
        elif USB_RE.match(name):
            result[name] = USB_CLASS
        elif CLOCK_RE.match(name):
            result[name] = CLOCK_CLASS
        elif I2C_RE.match(name) or SPI_RE.match(name):
            result[name] = BUS_CLASS
        else:
            result[name] = SIGNAL_CLASS

    return result


def _group_by_class(classified: dict[str, NetClassDef]) -> dict[str, list[str]]:
    """Group net names by their class name."""
    groups: dict[str, list[str]] = {}
    for net_name, cls in classified.items():
        groups.setdefault(cls.name, []).append(net_name)
    for nets in groups.values():
        nets.sort()
    return groups


# ---------------------------------------------------------------------------
# DSN file manipulation
# ---------------------------------------------------------------------------

def _via_padstack_name(cls: NetClassDef) -> str:
    return f"Via[0-1]_{cls.via_pad_um}:{cls.via_drill_um}_um"


def _generate_class_section(cls: NetClassDef, net_names: list[str]) -> str:
    """Generate a DSN (class ...) s-expression."""
    via_name = _via_padstack_name(cls)
    nets_str = " ".join(net_names)
    return (
        f"    (class {cls.name} {nets_str}\n"
        f"      (circuit\n"
        f"        (use_via \"{via_name}\")\n"
        f"      )\n"
        f"      (rule\n"
        f"        (width {cls.width_um})\n"
        f"        (clearance {cls.clearance_um})\n"
        f"      )\n"
        f"    )"
    )


def _generate_via_padstack(cls: NetClassDef, layer_count: int = 2) -> str:
    """Generate a DSN (padstack ...) s-expression for a via."""
    name = _via_padstack_name(cls)
    layers = ["F.Cu", "B.Cu"] if layer_count <= 2 else ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
    shapes = "\n".join(f"      (shape (circle {layer} {cls.via_pad_um}))" for layer in layers)
    return (
        f"    (padstack \"{name}\"\n"
        f"{shapes}\n"
        f"      (attach off)\n"
        f"    )"
    )


def inject_net_classes(dsn_path: str, net_names: list[str] | None = None) -> dict:
    """Rewrite a DSN file with semantic net classes.

    Reads the DSN exported by pcbnew, classifies nets, replaces the
    kicad_default class with per-function classes, and adds any
    missing via padstacks.

    Returns a summary dict: {class_name: [net_names], ...}
    """
    content = Path(dsn_path).read_text()

    # Extract net names from the DSN if not provided
    if net_names is None:
        net_names = _extract_net_names(content)

    classified = classify_nets(net_names)
    groups = _group_by_class(classified)

    # Collect unique class definitions used
    class_defs: dict[str, NetClassDef] = {}
    for net_name, cls in classified.items():
        class_defs[cls.name] = cls

    # Generate new class sections
    class_sections = []
    for cls_name in sorted(groups.keys()):
        cls = class_defs[cls_name]
        class_sections.append(_generate_class_section(cls, groups[cls_name]))
    new_classes = "\n".join(class_sections)

    # Remove ALL existing (class ...) entries so re-injection is idempotent
    while True:
        idx = content.find("(class ")
        if idx < 0:
            break
        end = _find_matching_paren(content, idx)
        if end < 0:
            break
        # Strip leading whitespace on the same line + trailing newline
        start = idx
        while start > 0 and content[start - 1] in " \t":
            start -= 1
        tail = end + 1
        if tail < len(content) and content[tail] == "\n":
            tail += 1
        content = content[:start] + content[tail:]

    # Inject new classes before closing of (network ...)
    wiring_pos = content.find("(wiring")
    if wiring_pos < 0:
        wiring_pos = len(content)
    network_end = content.rfind(")", 0, wiring_pos)
    if network_end > 0:
        content = content[:network_end] + new_classes + "\n  " + content[network_end:]

    # Add via padstacks that don't already exist
    for cls in class_defs.values():
        via_name = _via_padstack_name(cls)
        if via_name not in content:
            padstack = _generate_via_padstack(cls)
            # Insert before closing of (library ...)
            lib_end = content.find("  )\n  (network")
            if lib_end > 0:
                content = content[:lib_end] + padstack + "\n" + content[lib_end:]

    Path(dsn_path).write_text(content)

    return {cls_name: nets for cls_name, nets in sorted(groups.items())}


def _find_matching_paren(text: str, start: int) -> int:
    """Find the index of the closing paren matching the opening paren at start."""
    depth = 0
    in_quote = False
    for i in range(start, len(text)):
        c = text[i]
        if c == '"' and (i == 0 or text[i - 1] != '\\'):
            in_quote = not in_quote
        elif not in_quote:
            if c == '(':
                depth += 1
            elif c == ')':
                depth -= 1
                if depth == 0:
                    return i
    return -1


def _extract_net_names(dsn_content: str) -> list[str]:
    """Extract net names from DSN (net ...) entries."""
    # Match (net <name> (pins ...))
    pattern = re.compile(r"\(net\s+([^\s()]+)")
    return [m.group(1) for m in pattern.finditer(dsn_content)]


def summarize_classes(groups: dict[str, list[str]]) -> str:
    """Human-readable summary of net class assignments."""
    lines = ["Net class assignments:"]
    for cls_name, nets in sorted(groups.items()):
        lines.append(f"  {cls_name}: {', '.join(nets[:8])}"
                      + (f" (+{len(nets)-8} more)" if len(nets) > 8 else ""))
    return "\n".join(lines)
