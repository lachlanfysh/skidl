"""Reverse a real KiCad schematic into a CircuitSpec JSON.

Takes a .kicad_sch (or project directory), exports the netlist via kicad-cli,
and emits a validated CircuitSpec with correct library references, footprints,
pin connections, and functional groups inferred from hierarchical sheets.

This produces ground-truth specs from production boards — pin names, footprints,
and nets are correct by construction since they come from the actual KiCad symbols.

Usage:
    python3 -m corpus.kicad_to_spec path/to/project_dir
    python3 -m corpus.kicad_to_spec path/to/schematic.kicad_sch -o spec.json
    python3 -m corpus.kicad_to_spec --all-tier1          # all Tier 1 reference repos
    python3 -m corpus.kicad_to_spec --round-trip path/    # reverse then translate back
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from corpus.reference_oracle import (
    OracleError,
    _find_root_schematic,
    extract_netlist,
)
from schemas.circuit_spec import PIN_REF_RE

REPO_ROOT = Path(__file__).resolve().parent.parent
SPECS_DIR = REPO_ROOT / "corpus" / "specs"
SOURCES_DIR = REPO_ROOT / "corpus" / "sources"

# Standard KiCad library names that map directly to CircuitSpec lib field.
# Project-local libs (not in this set) become custom parts with explicit pins.
STANDARD_LIBS = {
    "Analog_ADC", "Analog_DAC", "Analog_Switch", "Audio",
    "Battery_Management", "Comparator", "Connector", "Connector_Generic",
    "Connector_Generic_MountingPin", "Connector_Generic_Shielded",
    "Connector_PinHeader_1.00mm", "Connector_PinHeader_1.27mm",
    "Connector_PinHeader_2.00mm", "Connector_PinHeader_2.54mm",
    "Connector_PinSocket_2.54mm", "Connector_USB",
    "Converter_ACDC", "Converter_DCDC",
    "Device", "Diode", "Display_Character",
    "Driver_FET", "Driver_LED", "Driver_Motor",
    "Filter", "Graphic",
    "Interface", "Interface_CAN_LIN", "Interface_Ethernet",
    "Interface_Expansion", "Interface_HID", "Interface_LineDriver",
    "Interface_Optical", "Interface_Telecom", "Interface_UART", "Interface_USB",
    "Isolator", "Jumper",
    "LED", "Logic_LevelTranslator",
    "MCU_Espressif_ESP32", "MCU_Microchip_ATmega",
    "MCU_Microchip_ATtiny", "MCU_Microchip_PIC16",
    "MCU_Microchip_PIC18", "MCU_Microchip_SAMD",
    "MCU_Nordic_nRF", "MCU_NXP_LPC", "MCU_RaspberryPi",
    "MCU_RaspberryPi_RP2xxx", "MCU_ST_STM32F0", "MCU_ST_STM32F1",
    "MCU_ST_STM32F4", "MCU_ST_STM32L0", "MCU_ST_STM32L4",
    "Memory_EEPROM", "Memory_Flash", "Memory_RAM", "Memory_ROM",
    "Motor", "Oscillator",
    "Power_Management", "Power_Protection", "Power_Supervisor",
    "Regulator_Linear", "Regulator_Switching", "Relay",
    "RF", "RF_Bluetooth", "RF_GPS", "RF_WiFi",
    "Sensor", "Sensor_Audio", "Sensor_Current", "Sensor_Gas",
    "Sensor_Humidity", "Sensor_Magnetic", "Sensor_Motion",
    "Sensor_Optical", "Sensor_Pressure", "Sensor_Proximity",
    "Sensor_Temperature", "Sensor_Touch", "Sensor_Voltage",
    "Switch", "Timer", "Transformer", "Transistor_BJT",
    "Transistor_FET", "Transistor_IGBT",
}

# Power net patterns (from layout/roles.py) — nets matching these are power=True
_POWER_RE = re.compile(
    r"^(V(CC|DD|SS|DDA?|BAT|BUS|IN|OUT|REF)|"
    r"[AD]?GND[AD]?|"
    r"\+\d+\.?\d*V|"
    r"\+3\.?3V|"
    r"\+5V|"
    r"\+12V|"
    r"\+24V)$",
    re.IGNORECASE,
)


def _is_power_net(name: str) -> bool:
    return bool(_POWER_RE.match(name))


def _infer_group(ref: str, sheet_map: dict[str, str]) -> str | None:
    """Infer a group name from hierarchical sheet membership."""
    group = sheet_map.get(ref)
    if group:
        return re.sub(r"[^a-z0-9_]", "_", group.lower()).strip("_") or None
    return None


def _extract_sheet_map(sch_path: Path) -> dict[str, str]:
    """Parse hierarchical sheet instances from the root schematic to map
    refs to sheet names. Returns {ref: sheet_name}."""
    sheet_map = {}
    try:
        text = sch_path.read_text(errors="replace")
    except OSError:
        return sheet_map

    # Find (sheet (at ...) (property "Sheetname" "...") (uuid ...) instances ...)
    # Then find which sub-schematics exist and extract their refs
    # Simple heuristic: look for child .kicad_sch files and extract refs from them
    parent_dir = sch_path.parent
    root_stem = sch_path.stem

    for child_sch in sorted(parent_dir.glob("*.kicad_sch")):
        if child_sch.stem == root_stem:
            continue
        sheet_name = child_sch.stem
        # Clean up sheet name — strip parent prefix if present
        if sheet_name.startswith(root_stem + "_"):
            sheet_name = sheet_name[len(root_stem) + 1:]
        child_text = child_sch.read_text(errors="replace")
        for m in re.finditer(r'\(property\s+"Reference"\s+"([^"]+)"', child_text):
            ref = m.group(1)
            if not ref.startswith("#"):
                sheet_map[ref] = sheet_name
    return sheet_map


def reverse_schematic(sch_path: str | Path) -> dict:
    """Reverse a .kicad_sch into a CircuitSpec dict.

    Returns a dict ready for CircuitSpec.model_validate().
    """
    sch_path = Path(sch_path)
    if sch_path.is_dir():
        sch_path = _find_root_schematic(sch_path)

    netlist = extract_netlist(sch_path)
    sheet_map = _extract_sheet_map(sch_path)
    board_name = sch_path.stem.lower().replace(" ", "-")

    # Build parts
    parts = []
    ref_set = set()
    for comp in netlist.components:
        ref = comp["ref"]
        if ref.startswith("#") or ref in ref_set:
            continue
        if not re.match(r"^[A-Za-z][A-Za-z0-9_]*$", ref):
            continue
        ref_set.add(ref)

        libsource = comp["libsource"]
        lib, part_name = None, None
        if ":" in libsource:
            lib, part_name = libsource.split(":", 1)
            if lib not in STANDARD_LIBS:
                lib, part_name = None, None

        value = comp["value"]
        footprint = comp["footprint"]
        group = _infer_group(ref, sheet_map)

        entry = {
            "ref": ref,
            "lib": lib,
            "part": part_name,
            "value": value if value and value != part_name else None,
            "footprint": footprint,
            "group": group,
        }

        # Non-standard-lib parts need explicit pins (custom part)
        if lib is None:
            pins = _collect_pins_for_ref(ref, netlist)
            if not pins:
                continue  # skip fiducials, mounting holes, etc. with no net connections
            entry["pins"] = pins

        parts.append(entry)

    # Build nets
    nets = []
    for net in netlist.nets:
        if not net["nodes"]:
            continue
        name = net["name"]
        if name.startswith("unconnected-"):
            continue

        pin_refs = []
        for ref, pin, pinfunction in net["nodes"]:
            if ref.startswith("#") or ref not in ref_set:
                continue
            # Use pinfunction (name) when available, else pin number
            pin_id = pinfunction if pinfunction else pin
            pin_refs.append(f"{ref}.{pin_id}")

        if not pin_refs:
            continue

        nets.append({
            "name": name,
            "power": _is_power_net(name),
            "pins": sorted(set(pin_refs)),
        })

    spec = {
        "schema_version": "1",
        "board": {"name": board_name},
        "parts": parts,
        "nets": nets,
    }
    return spec


def _collect_pins_for_ref(ref: str, netlist) -> list[dict]:
    """Collect all pins for a ref from the netlist and build PinDef entries."""
    pins_seen = {}
    for net in netlist.nets:
        for r, pin, pinfunction in net["nodes"]:
            if r == ref and pin not in pins_seen:
                name = pinfunction or f"P{pin}"
                func = _guess_pin_func(name, net["name"])
                pins_seen[pin] = {"num": str(pin), "name": name, "func": func}

    return sorted(pins_seen.values(), key=lambda p: (int(p["num"]) if p["num"].isdigit() else 999, p["num"]))


def _guess_pin_func(pin_name: str, net_name: str) -> str:
    """Heuristic pin function from name and connected net."""
    pn = pin_name.upper()
    nn = net_name.upper()

    if any(p in pn for p in ("VCC", "VDD", "VIN", "VBUS", "VBAT")):
        return "power_in"
    if any(p in pn for p in ("VOUT",)):
        return "power_out"
    if "GND" in pn or "VSS" in pn:
        return "power_in"
    if _is_power_net(net_name):
        return "power_in"
    if any(p in pn for p in ("SDA", "SPI", "MISO", "MOSI", "SCK")):
        return "bidirectional"
    if "SCL" in pn or "CLK" in pn:
        return "input"
    if any(p in pn for p in ("TX", "OUT", "DO", "DOUT")):
        return "output"
    if any(p in pn for p in ("RX", "IN", "DI", "DIN", "RESET", "EN")):
        return "input"
    if "NC" in pn or "NO_CONNECT" in pn:
        return "no_connect"
    return "passive"


def validate_spec(spec: dict) -> tuple[bool, str]:
    """Validate against CircuitSpec pydantic model."""
    try:
        from schemas.circuit_spec import CircuitSpec
        CircuitSpec.model_validate(spec)
        return True, "valid"
    except Exception as e:
        return False, str(e)


def round_trip_spec(spec: dict) -> tuple[bool, str]:
    """Translate the spec back through the translator and check it builds."""
    import os
    os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")
    try:
        from schemas.circuit_spec import CircuitSpec
        from schemas.translator import translate
        cs = CircuitSpec.model_validate(spec)
        result = translate(cs)
        if result.ok:
            return True, f"ok ({len(result.circuit.parts)} parts)"
        codes = [e.code.value for e in result.exceptions]
        return False, f"exceptions: {codes}"
    except Exception as e:
        return False, str(e)


def _find_tier1_projects() -> list[tuple[str, Path]]:
    """Find all Tier 1 KiCad projects in corpus/sources/."""
    projects = []
    for source_dir in sorted(SOURCES_DIR.iterdir()):
        if not source_dir.is_dir():
            continue
        for sch in sorted(source_dir.rglob("*.kicad_sch")):
            # Only root schematics (ones with a matching .kicad_pro)
            pro = sch.with_suffix(".kicad_pro")
            if pro.exists():
                slug = re.sub(r"[^a-z0-9]+", "-", sch.stem.lower()).strip("-")
                projects.append((slug, sch.parent))
    return projects


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("path", nargs="?", help="Path to .kicad_sch or project directory")
    parser.add_argument("-o", "--output", help="Output JSON file (default: corpus/specs/ref-{slug}.json)")
    parser.add_argument("--all-tier1", action="store_true", help="Process all Tier 1 reference projects")
    parser.add_argument("--round-trip", action="store_true", help="Also translate back through the engine")
    args = parser.parse_args(argv)

    if not args.path and not args.all_tier1:
        parser.print_help()
        return 2

    SPECS_DIR.mkdir(parents=True, exist_ok=True)

    if args.all_tier1:
        projects = _find_tier1_projects()
        if not projects:
            print("No Tier 1 projects found in corpus/sources/")
            return 1
        results = {"ok": 0, "failed": []}
        for slug, proj_path in projects:
            try:
                spec = reverse_schematic(proj_path)
            except OracleError as e:
                print(f"  FAIL  ref-{slug:40s} {e}")
                results["failed"].append((slug, str(e)))
                continue

            valid, vmsg = validate_spec(spec)
            if not valid:
                print(f"  INVALID ref-{slug:38s} {vmsg[:80]}")
                results["failed"].append((slug, f"invalid: {vmsg[:80]}"))
                continue

            results["ok"] += 1
            out_path = SPECS_DIR / f"ref-{slug}.json"
            out_path.write_text(json.dumps(spec, indent=2) + "\n")

            n_parts = len(spec["parts"])
            n_nets = len(spec["nets"])
            rt_msg = ""
            if args.round_trip:
                rt_ok, rt_status = round_trip_spec(spec)
                rt_msg = f" RT:{rt_status}" if rt_ok else f" RT-FAIL:{rt_status[:60]}"

            print(f"  OK    ref-{slug:40s} {n_parts:3d}P {n_nets:3d}N{rt_msg}")

        total = len(projects)
        print(f"\n{results['ok']}/{total} reversed")
        if results["failed"]:
            print(f"{len(results['failed'])} failed:")
            for slug, reason in results["failed"]:
                print(f"    ref-{slug}: {reason}")
        return 0

    # Single project
    try:
        spec = reverse_schematic(args.path)
    except OracleError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1

    valid, vmsg = validate_spec(spec)
    if not valid:
        print(f"Validation failed: {vmsg}", file=sys.stderr)
        return 1

    slug = re.sub(r"[^a-z0-9]+", "-", Path(args.path).stem.lower()).strip("-")
    out_path = Path(args.output) if args.output else SPECS_DIR / f"ref-{slug}.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(spec, indent=2) + "\n")

    n_parts = len(spec["parts"])
    n_nets = len(spec["nets"])
    print(f"Reversed {args.path} -> {out_path} ({n_parts} parts, {n_nets} nets)")

    if args.round_trip:
        rt_ok, rt_status = round_trip_spec(spec)
        print(f"Round-trip: {rt_status}")
        if not rt_ok:
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
