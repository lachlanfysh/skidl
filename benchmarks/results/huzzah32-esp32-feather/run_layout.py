import os, sys, json, builtins, re
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")
from skidl import *
set_default_tool(KICAD9)
with open("/home/lachlan/Projects/skidl/benchmarks/results/huzzah32-esp32-feather/circuit.py") as f:
    code = f.read()
code = re.sub(r'^generate_schematic\(.*\).*$', '# removed', code, flags=re.MULTILINE)
code = re.sub(r'^generate_netlist\(.*\).*$', '# removed', code, flags=re.MULTILINE)
exec(compile(code, "circuit.py", "exec"))
ckt = builtins.default_circuit
from skidl.layout import extract_groups, place_parts, validate, write_kicad_pcb, LayoutConstraints, BoardOutline, load_footprint_bboxes
parts_with_fp = [p for p in ckt.parts if getattr(p, "footprint", None)]
fp_names = {str(p.footprint) for p in parts_with_fp}
fp_bboxes = load_footprint_bboxes(fp_names, ["/usr/share/kicad/footprints"])
# Board sized to accommodate 16-pin vertical headers (~40mm) plus ESP32 module
outline = BoardOutline(60.0, 50.0)
constraints = LayoutConstraints(outline=outline)
groups = extract_groups(ckt)
placed = place_parts(groups, constraints, fp_bboxes, circuit=ckt)
validation = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(f"Layout: ok={validation.ok}, overlaps={len(validation.overlaps)}, outline_viol={len(validation.outline_violations)}, placed={len(placed)}")
if validation.outline_violations:
    print("Outline violations:")
    for v in validation.outline_violations:
        print(f"  {v}")
if validation.overlaps:
    print("Overlaps:")
    for o in validation.overlaps:
        print(f"  {o}")
try:
    write_kicad_pcb(placed, ckt, ["/usr/share/kicad/footprints"], "/home/lachlan/Projects/skidl/benchmarks/results/huzzah32-esp32-feather/board.kicad_pcb", outline=constraints.outline)
    print("PCB written")
except Exception as e:
    print(f"PCB write failed: {e}")
