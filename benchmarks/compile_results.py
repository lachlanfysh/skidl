#!/usr/bin/env python3
"""Compile benchmark results from all generated circuits."""

import json
import os
import re
import sys

RESULTS_DIR = "/home/lachlan/Projects/skidl/benchmarks/results"


def analyze_circuit(slug):
    """Analyze a single generated circuit directory."""
    d = os.path.join(RESULTS_DIR, slug)
    circuit_py = os.path.join(d, "circuit.py")
    if not os.path.isfile(circuit_py):
        return None

    with open(circuit_py) as f:
        code = f.read()

    lines = len(code.splitlines())

    # Count parts
    part_count = len(re.findall(r'\bPart\s*\(', code))
    subcircuit_count = len(re.findall(r'@subcircuit', code))
    net_count = len(re.findall(r'\bNet\s*\(', code))

    # Detect Part creation patterns
    skidl_tool_parts = len(re.findall(r'tool\s*=\s*SKIDL', code))
    library_parts = part_count - skidl_tool_parts

    # Extract footprints used
    footprints = re.findall(r'footprint\s*=\s*"([^"]+)"', code)
    unique_fps = set(footprints)

    # Check for common patterns
    has_decoupling = bool(re.search(r'100nF|100n', code, re.I))
    has_power_nets = bool(re.search(r'\.drive\s*=\s*POWER', code))
    has_usb = bool(re.search(r'USB', code, re.I))
    has_crystal = bool(re.search(r'Crystal', code, re.I))
    has_ldo = bool(re.search(r'AP2112|AMS1117|NCP1117|MCP1700|Regulator_Linear', code))
    has_battery = bool(re.search(r'MCP73831|battery|lipo|charger', code, re.I))

    # Check for board.kicad_pcb
    has_pcb = os.path.isfile(os.path.join(d, "board.kicad_pcb"))

    # Check for layout results
    has_layout = os.path.isfile(os.path.join(d, "run_layout.py"))

    # Extract IC names from SKIDL tool parts
    skidl_ics = re.findall(r'Part\s*\(\s*name\s*=\s*"([^"]+)".*?tool\s*=\s*SKIDL', code, re.DOTALL)
    # Extract IC names from library parts
    lib_ics = re.findall(r'Part\s*\(\s*"(\w+)"\s*,\s*"([^"]+)"', code)

    return {
        "slug": slug,
        "lines": lines,
        "part_count": part_count,
        "subcircuit_count": subcircuit_count,
        "net_count": net_count,
        "skidl_tool_parts": skidl_tool_parts,
        "library_parts": library_parts,
        "unique_footprints": len(unique_fps),
        "footprints": sorted(unique_fps),
        "has_decoupling": has_decoupling,
        "has_power_nets": has_power_nets,
        "has_usb": has_usb,
        "has_crystal": has_crystal,
        "has_ldo": has_ldo,
        "has_battery": has_battery,
        "has_pcb": has_pcb,
        "has_layout_script": has_layout,
        "skidl_ics": skidl_ics,
        "library_ics": [f"{lib}:{name}" for lib, name in lib_ics],
    }


def main():
    results = []
    for slug in sorted(os.listdir(RESULTS_DIR)):
        if not os.path.isdir(os.path.join(RESULTS_DIR, slug)):
            continue
        r = analyze_circuit(slug)
        if r:
            results.append(r)

    # Summary stats
    total = len(results)
    print(f"=== Benchmark Results: {total} boards ===\n")

    # Part counts
    part_counts = [r["part_count"] for r in results]
    print(f"Part counts: min={min(part_counts)}, max={max(part_counts)}, "
          f"avg={sum(part_counts)/total:.0f}, total={sum(part_counts)}")

    # Code lines
    lines = [r["lines"] for r in results]
    print(f"Code lines: min={min(lines)}, max={max(lines)}, "
          f"avg={sum(lines)/total:.0f}, total={sum(lines)}")

    # SKIDL tool vs library parts
    skidl_total = sum(r["skidl_tool_parts"] for r in results)
    lib_total = sum(r["library_parts"] for r in results)
    print(f"\nPart sourcing: {lib_total} from KiCad libs ({lib_total/(lib_total+skidl_total)*100:.0f}%), "
          f"{skidl_total} SKIDL-tool ({skidl_total/(lib_total+skidl_total)*100:.0f}%)")

    # PCB generation
    pcb_count = sum(1 for r in results if r["has_pcb"])
    print(f"PCB files: {pcb_count}/{total} ({pcb_count/total*100:.0f}%)")

    # Common patterns
    print(f"\nDesign patterns:")
    print(f"  Decoupling caps: {sum(1 for r in results if r['has_decoupling'])}/{total}")
    print(f"  Power net drive: {sum(1 for r in results if r['has_power_nets'])}/{total}")
    print(f"  USB connector: {sum(1 for r in results if r['has_usb'])}/{total}")
    print(f"  Crystal/oscillator: {sum(1 for r in results if r['has_crystal'])}/{total}")
    print(f"  LDO regulator: {sum(1 for r in results if r['has_ldo'])}/{total}")
    print(f"  Battery charging: {sum(1 for r in results if r['has_battery'])}/{total}")
    print(f"  Subcircuits used: {sum(1 for r in results if r['subcircuit_count'] > 0)}/{total}")

    # Footprint analysis
    all_fps = set()
    for r in results:
        all_fps.update(r["footprints"])
    print(f"\nUnique footprints across all boards: {len(all_fps)}")

    # Most common footprints
    from collections import Counter
    fp_counter = Counter()
    for r in results:
        fp_counter.update(r["footprints"])
    print("Top 10 footprints:")
    for fp, count in fp_counter.most_common(10):
        print(f"  {count:3d}x {fp}")

    # Check which footprints actually exist
    print(f"\nFootprint validation:")
    missing_fps = set()
    for fp in all_fps:
        lib, name = fp.split(":", 1) if ":" in fp else (fp, "")
        path = f"/usr/share/kicad/footprints/{lib}.pretty/{name}.kicad_mod"
        if not os.path.isfile(path):
            missing_fps.add(fp)
    print(f"  Valid: {len(all_fps) - len(missing_fps)}/{len(all_fps)}")
    print(f"  Missing: {len(missing_fps)}")
    if missing_fps:
        for fp in sorted(missing_fps):
            print(f"    - {fp}")

    # Library usage
    lib_counter = Counter()
    for r in results:
        for ic in r["library_ics"]:
            lib = ic.split(":")[0]
            lib_counter[lib] += 1
    print(f"\nKiCad library usage (top 15):")
    for lib, count in lib_counter.most_common(15):
        print(f"  {count:3d}x {lib}")

    # Per-board table
    print(f"\n{'Board':<35} {'Parts':>5} {'Subs':>4} {'Lines':>5} {'SKIDL':>5} {'Lib':>4} {'FPs':>3} {'PCB':>3}")
    print("-" * 85)
    for r in sorted(results, key=lambda x: -x["part_count"]):
        print(f"{r['slug']:<35} {r['part_count']:>5} {r['subcircuit_count']:>4} "
              f"{r['lines']:>5} {r['skidl_tool_parts']:>5} {r['library_parts']:>4} "
              f"{r['unique_footprints']:>3} {'Y' if r['has_pcb'] else 'N':>3}")

    # Save full results
    output_path = os.path.join(RESULTS_DIR, "..", "all_results.json")
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nFull results saved to {output_path}")


if __name__ == "__main__":
    main()
