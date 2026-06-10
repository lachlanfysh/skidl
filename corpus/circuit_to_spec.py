"""Extract CircuitSpec JSON from existing benchmark circuit.py files.

Executes each circuit.py in a subprocess (full state isolation), walks
builtins.default_circuit, and emits a validated CircuitSpec JSON file.

Usage:
    python3 -m corpus.circuit_to_spec                     # all 50 benchmarks
    python3 -m corpus.circuit_to_spec ads1115-adc          # single board
    python3 -m corpus.circuit_to_spec --round-trip         # also translate back
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCHMARKS = REPO_ROOT / "benchmarks" / "results"
SPECS_DIR = REPO_ROOT / "corpus" / "specs"

# Reverse map: skidl pin_types enum name -> spec func name
_SKIDL_FUNC_TO_SPEC = {
    "INPUT": "input",
    "OUTPUT": "output",
    "BIDIR": "bidirectional",
    "TRISTATE": "tristate",
    "PASSIVE": "passive",
    "UNSPEC": "unspecified",
    "PWRIN": "power_in",
    "PWROUT": "power_out",
    "OPENCOLL": "passive",
    "OPENEMIT": "passive",
    "PULLUP": "passive",
    "PULLDN": "passive",
    "NOCONNECT": "no_connect",
    "FREE": "unspecified",
}


def _strip_generation_calls(source: str) -> str:
    """Remove generate_schematic/generate_netlist/layout calls that would
    trigger file I/O. Keep circuit construction intact."""
    lines = []
    for line in source.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(call) for call in (
            "generate_schematic", "generate_netlist",
            "write_kicad_pcb", "plan_layout",
        )):
            continue
        lines.append(line)
    return "\n".join(lines)


_WALKER_SCRIPT = textwrap.dedent(r'''
import builtins, json, sys, os
os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

# Suppress skidl warnings to stderr
import logging
logging.disable(logging.CRITICAL)

# Execute the circuit file
circuit_path = sys.argv[1]
with open(circuit_path) as f:
    source = f.read()

# Strip generation calls
import re
lines = []
for line in source.splitlines():
    s = line.strip()
    if any(s.startswith(c) for c in (
        "generate_schematic", "generate_netlist",
        "write_kicad_pcb", "plan_layout",
    )):
        continue
    lines.append(line)
code = "\n".join(lines)

exec(compile(code, circuit_path, "exec"), {"__name__": "__main__", "__file__": circuit_path})

ckt = builtins.default_circuit
from skidl import POWER

# Walk parts
parts = []
for p in ckt.parts:
    is_custom = (p.tool == "skidl")
    raw_lib = None if is_custom else getattr(p.lib, "filename", None)
    lib_name = os.path.basename(raw_lib).removesuffix(".kicad_sym") if raw_lib else None
    part_name = p.name if not is_custom else p.name

    pins_out = None
    if is_custom:
        pins_out = []
        func_map = {
            "INPUT": "input", "OUTPUT": "output", "BIDIR": "bidirectional",
            "TRISTATE": "tristate", "PASSIVE": "passive", "UNSPEC": "unspecified",
            "PWRIN": "power_in", "PWROUT": "power_out", "OPENCOLL": "passive",
            "OPENEMIT": "passive", "PULLUP": "passive", "PULLDN": "passive",
            "NOCONNECT": "no_connect", "FREE": "unspecified",
        }
        for pin in sorted(p.pins, key=lambda x: (int(x.num) if x.num.isdigit() else 999, x.num)):
            func_name = getattr(pin.func, "name", "PASSIVE")
            pins_out.append({
                "num": str(pin.num),
                "name": str(pin.name),
                "func": func_map.get(func_name, "passive"),
            })

    # Derive group from subcircuit hierarchy
    hier = p.hiertuple
    group = hier[1].rstrip("0123456789") if len(hier) > 1 and hier[1] else None

    entry = {
        "ref": p.ref,
        "lib": lib_name,
        "part": part_name if lib_name else None,
        "value": p.value if p.value and p.value != p.name else None,
        "footprint": str(p.footprint) if getattr(p, "footprint", None) else "",
        "group": group,
    }
    if pins_out is not None:
        entry["pins"] = pins_out
    parts.append(entry)

# Walk nets
nets = []
for n in ckt.nets:
    if n.name.startswith("__"):
        continue
    if not n.pins:
        continue
    is_power = (n.drive == POWER)
    pin_refs = []
    for pin in n.pins:
        ref = pin.part.ref
        # For library parts, prefer pin name; for custom parts, use pin name too
        pname = str(pin.name) if pin.name else ""
        # Use pin number for passives, anonymous pins (~), or when name matches num
        if pname in ("", "~", str(pin.num)) or pname.startswith("Pin_"):
            pin_id = str(pin.num)
        else:
            pin_id = pname
        pin_refs.append(f"{ref}.{pin_id}")
    nets.append({
        "name": n.name,
        "power": is_power,
        "pins": sorted(set(pin_refs)),
    })

# Board name from directory
board_name = os.path.basename(os.path.dirname(circuit_path))

spec = {
    "schema_version": "1",
    "board": {"name": board_name},
    "parts": parts,
    "nets": nets,
}

json.dump(spec, sys.stdout, indent=2, default=str)
''')


def extract_spec(board_dir: Path) -> tuple[dict | None, str]:
    """Run circuit.py in a subprocess, return (spec_dict, status)."""
    circuit_py = board_dir / "circuit.py"
    if not circuit_py.exists():
        return None, "no circuit.py"

    try:
        result = subprocess.run(
            [sys.executable, "-c", _WALKER_SCRIPT, str(circuit_py)],
            capture_output=True, text=True, timeout=60,
            cwd=str(REPO_ROOT),
        )
    except subprocess.TimeoutExpired:
        return None, "timeout"

    if result.returncode != 0:
        err = result.stderr.strip().splitlines()
        return None, f"exec error: {err[-1] if err else 'unknown'}"

    try:
        spec = json.loads(result.stdout)
    except json.JSONDecodeError as e:
        return None, f"bad JSON: {e}"

    return spec, "ok"


def validate_spec(spec: dict) -> tuple[bool, str]:
    """Validate a spec dict against CircuitSpec schema."""
    try:
        from schemas.circuit_spec import CircuitSpec
        CircuitSpec.model_validate(spec)
        return True, "valid"
    except Exception as e:
        return False, str(e)


def round_trip_spec(spec: dict) -> tuple[bool, str]:
    """Translate spec back through the translator and check it builds."""
    try:
        os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")
        from schemas.circuit_spec import CircuitSpec
        from schemas.translator import translate
        cs = CircuitSpec.model_validate(spec)
        result = translate(cs)
        if result.ok:
            return True, f"ok ({len(result.circuit.parts)} parts)"
        else:
            codes = [e.code.value for e in result.exceptions]
            return False, f"exceptions: {codes}"
    except Exception as e:
        return False, str(e)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("boards", nargs="*", help="Board slugs to extract (default: all)")
    parser.add_argument("--round-trip", action="store_true", help="Also translate back")
    args = parser.parse_args(argv)

    SPECS_DIR.mkdir(parents=True, exist_ok=True)

    if args.boards:
        dirs = [BENCHMARKS / b for b in args.boards]
    else:
        dirs = sorted(d for d in BENCHMARKS.iterdir() if d.is_dir())

    results = {"ok": 0, "valid": 0, "round_trip": 0, "failed": []}

    for board_dir in dirs:
        slug = board_dir.name
        spec, status = extract_spec(board_dir)

        if spec is None:
            print(f"  FAIL  {slug:40s} {status}")
            results["failed"].append((slug, status))
            continue

        valid, vmsg = validate_spec(spec)
        if not valid:
            print(f"  INVALID {slug:38s} {vmsg[:80]}")
            results["failed"].append((slug, f"invalid: {vmsg[:80]}"))
            continue

        results["ok"] += 1
        results["valid"] += 1

        out_path = SPECS_DIR / f"{slug}.json"
        out_path.write_text(json.dumps(spec, indent=2) + "\n")

        rt_msg = ""
        if args.round_trip:
            rt_ok, rt_status = round_trip_spec(spec)
            if rt_ok:
                results["round_trip"] += 1
                rt_msg = f" RT:{rt_status}"
            else:
                rt_msg = f" RT-FAIL:{rt_status[:60]}"

        n_parts = len(spec["parts"])
        n_nets = len(spec["nets"])
        print(f"  OK    {slug:40s} {n_parts:3d}P {n_nets:3d}N{rt_msg}")

    total = len(dirs)
    print(f"\n{results['ok']}/{total} extracted, {results['valid']} valid", end="")
    if args.round_trip:
        print(f", {results['round_trip']} round-trip", end="")
    if results["failed"]:
        print(f", {len(results['failed'])} failed:")
        for slug, reason in results["failed"]:
            print(f"    {slug}: {reason}")
    else:
        print()

    return 0 if results["ok"] >= total * 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
