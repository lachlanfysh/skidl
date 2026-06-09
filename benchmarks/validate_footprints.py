#!/usr/bin/env python3
"""Scan benchmark circuits and validate all footprints against the filesystem."""

import os
import re
import sys

RESULTS_DIR = os.path.join(os.path.dirname(__file__), "results")
FP_BASE = os.environ.get("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints")


def extract_footprints(code: str) -> set[str]:
    return set(re.findall(r'footprint\s*=\s*"([^"]+)"', code))


def check_footprint(fp_name: str) -> bool:
    if ":" not in fp_name:
        return False
    lib, name = fp_name.split(":", 1)
    return os.path.isfile(os.path.join(FP_BASE, f"{lib}.pretty", f"{name}.kicad_mod"))


def main():
    all_fps: dict[str, list[str]] = {}
    for slug in sorted(os.listdir(RESULTS_DIR)):
        circuit_py = os.path.join(RESULTS_DIR, slug, "circuit.py")
        if not os.path.isfile(circuit_py):
            continue
        with open(circuit_py) as f:
            fps = extract_footprints(f.read())
        for fp in fps:
            all_fps.setdefault(fp, []).append(slug)

    valid = {fp for fp in all_fps if check_footprint(fp)}
    missing = {fp for fp in all_fps if not check_footprint(fp)}

    print(f"Footprints: {len(valid)} valid, {len(missing)} missing out of {len(all_fps)} unique\n")

    if missing:
        print("Missing footprints:")
        for fp in sorted(missing):
            boards = all_fps[fp]
            lib = fp.split(":")[0] if ":" in fp else fp
            lib_exists = os.path.isdir(os.path.join(FP_BASE, f"{lib}.pretty"))
            print(f"  {fp}")
            print(f"    library {'exists' if lib_exists else 'MISSING'}, used by: {', '.join(boards)}")
        print()

    if missing:
        sys.exit(1)
    print("All footprints valid.")


if __name__ == "__main__":
    main()
