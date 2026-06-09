"""Run layout pipeline for MCP9808 breakout board."""

import os, sys, json, builtins, re

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# Read and modify circuit.py — remove generate_schematic call
circuit_path = "/home/lachlan/Projects/skidl/benchmarks/results/mcp9808/circuit.py"
with open(circuit_path) as f:
    code = f.read()

# Remove the generate_schematic line and any os.environ / import / set_default_tool
# that would conflict with our setup
code = re.sub(r'^generate_schematic\(.*\).*$', '# generate_schematic removed for layout eval', code, flags=re.MULTILINE)
code = re.sub(r'^os\.environ\[.*\].*$', '# env already set', code, flags=re.MULTILINE)
code = re.sub(r'^from skidl import.*$', '# already imported', code, flags=re.MULTILINE)
code = re.sub(r'^set_default_tool\(.*\).*$', '# already set', code, flags=re.MULTILINE)
code = re.sub(r'^import os$', '# already imported', code, flags=re.MULTILINE)

exec(compile(code, circuit_path, 'exec'))

ckt = builtins.default_circuit
print(f"Circuit has {len(ckt.parts)} parts, {len(ckt.nets)} nets")

from skidl.layout import (
    extract_groups, place_parts, validate, write_kicad_pcb,
    LayoutConstraints, BoardOutline, derive_outline, load_footprint_bboxes,
)

parts_with_fp = [p for p in ckt.parts if getattr(p, "footprint", None)]
fp_names = {str(p.footprint) for p in parts_with_fp}
print(f"Footprints needed: {fp_names}")

fp_lib_dirs = ["/usr/share/kicad/footprints"]
fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)
print(f"Loaded bboxes for: {list(fp_bboxes.keys())}")

missing_fps = fp_names - set(fp_bboxes.keys())
if missing_fps:
    print(f"WARNING: Missing footprint bboxes for: {missing_fps}")

# Use a sensible default outline for a small breakout board
# MCP9808 breakout: 1 IC + 6 passives + 1 header = 8 parts, small board
outline = BoardOutline(30.0, 25.0)
constraints = LayoutConstraints(outline=outline)
groups = extract_groups(ckt)
print(f"Placement groups: {list(groups.keys())}")

placed = place_parts(groups, constraints, fp_bboxes, circuit=ckt)
print(f"Placed {len(placed)} parts")

for pp in placed:
    print(f"  {pp.ref}: ({pp.x_mm:.1f}, {pp.y_mm:.1f}) fp={pp.footprint}")

validation = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(f"\nValidation: ok={validation.ok}")
print(f"  overlaps={len(validation.overlaps)}")
print(f"  outline_violations={len(validation.outline_violations)}")
print(f"  missing_refs={len(validation.missing_refs)}")
if validation.overlaps:
    for ov in validation.overlaps:
        print(f"    overlap: {ov}")
if validation.outline_violations:
    for ov in validation.outline_violations:
        print(f"    outline violation: {ov}")
if validation.missing_refs:
    print(f"    missing: {validation.missing_refs}")

pcb_path = "/home/lachlan/Projects/skidl/benchmarks/results/mcp9808/board.kicad_pcb"
try:
    write_kicad_pcb(placed, ckt, fp_lib_dirs, pcb_path, outline=constraints.outline)
    pcb_ok = True
    print(f"\nPCB written to {pcb_path}")
except Exception as e:
    pcb_ok = False
    print(f"\nPCB write failed: {e}")
    import traceback
    traceback.print_exc()

result = {
    "board": "mcp9808",
    "layout_ok": validation.ok,
    "overlaps": len(validation.overlaps),
    "outline_violations": len(validation.outline_violations),
    "missing_refs": len(validation.missing_refs),
    "parts_placed": len(placed),
    "pcb_written": pcb_ok,
}
print("\n" + json.dumps(result))
