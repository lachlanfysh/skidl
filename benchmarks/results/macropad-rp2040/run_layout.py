"""Run layout pipeline for macropad-rp2040 circuit."""

import os, sys, json, builtins, re

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# Read and modify circuit.py -- remove generate_schematic call
circuit_path = "/home/lachlan/Projects/skidl/benchmarks/results/macropad-rp2040/circuit.py"
with open(circuit_path) as f:
    code = f.read()
code = re.sub(
    r'^generate_schematic\(.*\).*$',
    '# generate_schematic removed for layout eval',
    code,
    flags=re.MULTILINE,
)
# Also remove the import of os and set_default_tool since we already did that
code = re.sub(r'^os\.environ\["KICAD9_SYMBOL_DIR"\].*$', '# env already set', code, flags=re.MULTILINE)
code = re.sub(r'^from skidl import \*.*$', '# already imported', code, flags=re.MULTILINE)
code = re.sub(r'^set_default_tool\(KICAD9\).*$', '# already set', code, flags=re.MULTILINE)
code = re.sub(r'^import os$', '# os already imported', code, flags=re.MULTILINE)

exec(compile(code, circuit_path, 'exec'))

ckt = builtins.default_circuit
print(f"Circuit has {len(ckt.parts)} parts, {len(ckt.nets)} nets")

from skidl.layout import (
    plan_layout,
    write_kicad_pcb,
    LayoutConstraints,
    BoardOutline,
    load_footprint_bboxes,
)

fp_lib_dirs = ["/usr/share/kicad/footprints"]

# Use plan_layout as the high-level orchestrator
layout_result = plan_layout(
    ckt,
    fp_lib_dirs=fp_lib_dirs,
    derive_outline_if_missing=True,
    margin_mm=3.0,
    clearance_mm=0.5,
    board_layers=2,
)

print("\n" + layout_result.summary())

validation = layout_result.validation
placed = layout_result.placed_parts

pcb_path = "/home/lachlan/Projects/skidl/benchmarks/results/macropad-rp2040/board.kicad_pcb"
pcb_ok = False
pcb_error = ""
try:
    write_kicad_pcb(
        placed, ckt, fp_lib_dirs, pcb_path,
        outline=layout_result.outline,
    )
    pcb_ok = True
    print(f"\nPCB written to {pcb_path}")
except Exception as e:
    pcb_error = str(e)
    print(f"PCB write failed: {e}")

result = {
    "board": "macropad-rp2040",
    "layout_ok": validation.ok,
    "overlaps": len(validation.overlaps),
    "outline_violations": len(validation.outline_violations),
    "missing_refs": len(validation.missing_refs),
    "parts_placed": len(placed),
    "pcb_written": pcb_ok,
}
if pcb_error:
    result["pcb_error"] = pcb_error

print("\n=== JSON RESULT ===")
print(json.dumps(result))
