"""Run the SKiDL layout pipeline on the Si5351A circuit."""
import os, sys, json, builtins, re

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# Read and modify circuit.py — remove generate_schematic call
circuit_path = "/home/lachlan/Projects/skidl/benchmarks/results/si5351a/circuit.py"
with open(circuit_path) as f:
    code = f.read()
code = re.sub(r'^generate_schematic\(.*\).*$', '# generate_schematic removed for layout eval', code, flags=re.MULTILINE)
code = re.sub(r'^print\("Schematic generated.*\)$', '# print removed', code, flags=re.MULTILINE)

# Remove the imports/env that are already done
code = re.sub(r'^import os$', '# import os', code, flags=re.MULTILINE)
code = re.sub(r'^os\.environ\[.*$', '# env already set', code, flags=re.MULTILINE)
code = re.sub(r'^from skidl import.*$', '# already imported', code, flags=re.MULTILINE)
code = re.sub(r'^set_default_tool.*$', '# already set', code, flags=re.MULTILINE)

exec(compile(code, circuit_path, 'exec'))

ckt = builtins.default_circuit
print(f"Circuit has {len(ckt.parts)} parts, {len(ckt.nets)} nets")

from skidl.layout import (
    plan_layout, write_kicad_pcb,
    LayoutConstraints, BoardOutline, load_footprint_bboxes,
)

fp_lib_dirs = ["/usr/share/kicad/footprints"]

# Use plan_layout — the high-level orchestrator
result = plan_layout(
    circuit=ckt,
    fp_lib_dirs=fp_lib_dirs,
    derive_outline_if_missing=True,
)

print("\n=== Layout Summary ===")
print(result.summary())

# Write PCB
pcb_path = "/home/lachlan/Projects/skidl/benchmarks/results/si5351a/board.kicad_pcb"
pcb_ok = False
pcb_error = ""
try:
    write_kicad_pcb(
        result.placed_parts, ckt, fp_lib_dirs, pcb_path,
        outline=result.outline,
    )
    pcb_ok = True
    print(f"\nPCB written to {pcb_path}")
except Exception as e:
    pcb_error = str(e)
    print(f"\nPCB write failed: {e}")

# Output structured JSON result
output = {
    "board": "si5351a",
    "layout_ok": result.validation.ok,
    "overlaps": len(result.validation.overlaps),
    "outline_violations": len(result.validation.outline_violations),
    "missing_refs": len(result.validation.missing_refs),
    "parts_placed": len(result.placed_parts),
    "pcb_written": pcb_ok,
}
if pcb_error:
    output["pcb_error"] = pcb_error
print("\n=== JSON Result ===")
print(json.dumps(output))
