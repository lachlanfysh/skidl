import os, sys, json, builtins, re
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")
from skidl import *
set_default_tool(KICAD9)
with open("/home/lachlan/Projects/skidl/benchmarks/results/metro-328/circuit.py") as f:
    code = f.read()
code = re.sub(r'^generate_schematic\(.*\).*$', '# removed', code, flags=re.MULTILINE)
code = re.sub(r'^generate_netlist\(.*\).*$', '# removed', code, flags=re.MULTILINE)
exec(compile(code, "circuit.py", "exec"))
ckt = builtins.default_circuit
from skidl.layout import (
    extract_groups, place_parts, validate, write_kicad_pcb,
    LayoutConstraints, BoardOutline, derive_outline, load_footprint_bboxes,
)
parts_with_fp = [p for p in ckt.parts if getattr(p, "footprint", None)]
fp_names = {str(p.footprint) for p in parts_with_fp}
fp_bboxes = load_footprint_bboxes(fp_names, ["/usr/share/kicad/footprints"])

# Use a fixed board outline appropriate for Arduino-sized board (68.6mm x 53.3mm standard)
outline = BoardOutline(70.0, 55.0)
constraints = LayoutConstraints(outline=outline)
groups = extract_groups(ckt)
placed = place_parts(groups, constraints, fp_bboxes, circuit=ckt)

# Derive actual outline from placed parts
final_outline = derive_outline(placed, fp_bboxes)
validation = validate(placed, ckt, fp_bboxes, outline=final_outline)
print(f"Layout: ok={validation.ok}, overlaps={len(validation.overlaps)}, outline_viol={len(validation.outline_violations)}, placed={len(placed)}")
try:
    write_kicad_pcb(placed, ckt, ["/usr/share/kicad/footprints"], "/home/lachlan/Projects/skidl/benchmarks/results/metro-328/board.kicad_pcb", outline=final_outline)
    print("PCB written")
except Exception as e:
    print(f"PCB write failed: {e}")
