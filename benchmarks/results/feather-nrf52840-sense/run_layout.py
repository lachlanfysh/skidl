"""Run the SKiDL layout pipeline on the Feather nRF52840 Sense circuit."""

import os, sys, json, builtins, re, traceback

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# Read and modify circuit.py — remove generate_schematic call
circuit_path = "/home/lachlan/Projects/skidl/benchmarks/results/feather-nrf52840-sense/circuit.py"
with open(circuit_path) as f:
    code = f.read()

code = re.sub(
    r'^generate_schematic\(.*?\).*$',
    '# generate_schematic removed for layout eval',
    code,
    flags=re.MULTILINE,
)
code = re.sub(r'^os\.environ.*KICAD9_SYMBOL_DIR.*$', '# env already set', code, flags=re.MULTILINE)
code = re.sub(r'^from skidl import \*.*$', '# already imported', code, flags=re.MULTILINE)
code = re.sub(r'^set_default_tool\(KICAD9\).*$', '# already set', code, flags=re.MULTILINE)
code = re.sub(r'^import os, sys$', '# already imported', code, flags=re.MULTILINE)

exec(compile(code, circuit_path, 'exec'))

ckt = builtins.default_circuit
print(f"Circuit has {len(ckt.parts)} parts, {len(ckt.nets)} nets")

from skidl.layout import (
    plan_layout, write_kicad_pcb,
    LayoutConstraints, BoardOutline,
    load_footprint_bboxes,
)

fp_lib_dirs = ["/usr/share/kicad/footprints"]

# Use plan_layout — the high-level orchestrator
result = plan_layout(
    circuit=ckt,
    fp_lib_dirs=fp_lib_dirs,
    derive_outline_if_missing=True,
    margin_mm=3.0,
    clearance_mm=0.5,
)

print("\n--- Layout Result ---")
print(result.summary())

validation = result.validation
placed = result.placed_parts

# Write PCB
pcb_path = "/home/lachlan/Projects/skidl/benchmarks/results/feather-nrf52840-sense/board.kicad_pcb"
pcb_ok = False
pcb_error = None
try:
    write_kicad_pcb(
        placed, ckt, fp_lib_dirs, pcb_path,
        outline=result.outline,
        strict_missing_footprints=False,
    )
    pcb_ok = True
    print(f"\nPCB written to {pcb_path}")
except Exception as e:
    pcb_error = str(e)
    print(f"\nPCB write failed: {e}")
    traceback.print_exc()

print(f"\nLayout: ok={validation.ok}, overlaps={len(validation.overlaps)}, outline_viol={len(validation.outline_violations)}, placed={len(placed)}")

output = {
    "board": "feather-nrf52840-sense",
    "layout_ok": validation.ok,
    "overlaps": len(validation.overlaps),
    "outline_violations": len(validation.outline_violations),
    "missing_refs": len(validation.missing_refs),
    "parts_placed": len(placed),
    "pcb_written": pcb_ok,
}
print("\n--- JSON ---")
print(json.dumps(output))
