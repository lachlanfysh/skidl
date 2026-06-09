"""Run the SKiDL layout pipeline on the INA219 circuit."""

import os, sys, json, builtins, re

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# Read and modify circuit.py — remove generate_schematic call
circuit_path = "/home/lachlan/Projects/skidl/benchmarks/results/ina219/circuit.py"
with open(circuit_path) as f:
    code = f.read()
code = re.sub(r'^generate_schematic\(.*\).*$', '# generate_schematic removed for layout eval', code, flags=re.MULTILINE)
code = re.sub(r'^print\("SUCCESS.*$', '# print removed', code, flags=re.MULTILINE)

exec(compile(code, circuit_path, 'exec'))

ckt = builtins.default_circuit
print(f"Circuit has {len(ckt.parts)} parts, {len(ckt.nets)} nets")

from skidl.layout import (
    extract_groups, place_parts, validate, write_kicad_pcb,
    LayoutConstraints, derive_outline, load_footprint_bboxes,
)

parts_with_fp = [p for p in ckt.parts if getattr(p, "footprint", None)]
fp_names = {str(p.footprint) for p in parts_with_fp}
print(f"Footprint names: {fp_names}")

fp_lib_dirs = ["/usr/share/kicad/footprints"]
fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)
print(f"Loaded {len(fp_bboxes)}/{len(fp_names)} footprint bboxes")

missing_fps = fp_names - set(fp_bboxes.keys())
if missing_fps:
    print(f"Missing footprints: {missing_fps}")

groups = extract_groups(ckt)
print(f"Extracted {len(groups)} groups")

outline = derive_outline([], fp_bboxes, margin_mm=3.0)
constraints = LayoutConstraints(outline=outline)

placed = place_parts(groups, constraints, fp_bboxes, circuit=ckt)
print(f"Placed {len(placed)} parts")

# Re-derive outline from actual placed positions
outline = derive_outline(placed, fp_bboxes, margin_mm=3.0)
constraints = LayoutConstraints(outline=outline)

validation = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(f"Validation ok: {validation.ok}")
print(validation.summary())

pcb_path = "/home/lachlan/Projects/skidl/benchmarks/results/ina219/board.kicad_pcb"
pcb_ok = False
pcb_error = ""
try:
    write_kicad_pcb(placed, ckt, fp_lib_dirs, pcb_path, outline=constraints.outline)
    pcb_ok = True
    print(f"PCB written to {pcb_path}")
except Exception as e:
    pcb_error = str(e)
    print(f"PCB write failed: {e}")

result = {
    "board": "ina219",
    "layout_ok": validation.ok,
    "overlaps": len(validation.overlaps),
    "outline_violations": len(validation.outline_violations),
    "missing_refs": len(validation.missing_refs),
    "parts_placed": len(placed),
    "pcb_written": pcb_ok,
}
print("RESULT_JSON:" + json.dumps(result))
