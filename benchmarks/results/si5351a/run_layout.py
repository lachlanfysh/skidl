import os, sys, json, builtins, re
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")
from skidl import *
set_default_tool(KICAD9)
with open("/home/lachlan/Projects/skidl/benchmarks/results/si5351a/circuit.py") as f:
    code = f.read()
code = re.sub(r'^generate_schematic\(.*\).*$', '# removed', code, flags=re.MULTILINE)
code = re.sub(r'^generate_netlist\(.*\).*$', '# removed', code, flags=re.MULTILINE)
exec(compile(code, "circuit.py", "exec"))
ckt = builtins.default_circuit
from skidl.layout import (
    extract_groups, place_parts, validate, write_kicad_pcb,
    LayoutConstraints, derive_outline, derive_outline_from_circuit,
    load_footprint_bboxes, validate_footprints, FORM_FACTORS,
)
parts_with_fp = [p for p in ckt.parts if getattr(p, "footprint", None)]
fp_names = {str(p.footprint) for p in parts_with_fp}
fp_bboxes = load_footprint_bboxes(fp_names, ["/usr/share/kicad/footprints"])

# Check for missing footprints
valid_fps, missing_fps = validate_footprints(fp_names, ["/usr/share/kicad/footprints"])
if missing_fps:
    print(f"WARNING: Missing footprints: {missing_fps}")

# Form factor detection
form_factor = None  # no standard form factor detected
if form_factor and form_factor in FORM_FACTORS:
    outline = FORM_FACTORS[form_factor]
    print(f"Using form factor: {form_factor} ({outline.width_mm}x{outline.height_mm}mm)")
else:
    # Density-aware outline
    density_outline = derive_outline_from_circuit(ckt, fp_bboxes)
    min_area = density_outline.width_mm * density_outline.height_mm
    outline = derive_outline([], fp_bboxes, min_area_mm2=min_area)
    print(f"Auto-derived outline: {outline.width_mm:.1f}x{outline.height_mm:.1f}mm (min area: {min_area:.0f}mm2)")

constraints = LayoutConstraints(outline=outline, form_factor=None)
groups = extract_groups(ckt)
placed = place_parts(groups, constraints, fp_bboxes, circuit=ckt)
validation = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(f"Layout: ok={validation.ok}, overlaps={len(validation.overlaps)}, outline_viol={len(validation.outline_violations)}, placed={len(placed)}")
print(f"Outline: {constraints.outline.width_mm:.1f}x{constraints.outline.height_mm:.1f}mm")
if validation.overlaps:
    print(f"Overlapping pairs: {validation.overlaps[:5]}")
if validation.outline_violations:
    print(f"Outline violations: {validation.outline_violations[:5]}")
try:
    write_kicad_pcb(placed, ckt, ["/usr/share/kicad/footprints"], "/home/lachlan/Projects/skidl/benchmarks/results/si5351a/board.kicad_pcb", outline=constraints.outline)
    print("PCB written")
except Exception as e:
    print(f"PCB write failed: {e}")
