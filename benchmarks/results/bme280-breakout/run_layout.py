#!/usr/bin/env python3
"""
BME280 Breakout Board — PCB Layout
Small breakout board: ~25mm x 18mm
"""

import os
import sys

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

# Run the circuit first to populate default_circuit
exec(open(os.path.join(os.path.dirname(__file__), "circuit.py")).read())

from skidl.layout import (
    extract_groups,
    place_parts,
    write_kicad_pcb,
    validate,
    LayoutConstraints,
    BoardOutline,
    load_footprint_bboxes,
)

ckt = default_circuit

fp_names = {str(p.footprint) for p in ckt.parts if getattr(p, "footprint", None)}
fp_lib_dirs = ["/usr/share/kicad/footprints"]
fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)

# Small breakout board: 25mm x 18mm
constraints = LayoutConstraints(outline=BoardOutline(25.0, 18.0))

groups = extract_groups(ckt)
placed = place_parts(groups, constraints, fp_bboxes)

result = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(result.summary())

output_path = os.path.join(os.path.dirname(__file__), "board.kicad_pcb")
write_kicad_pcb(placed, ckt, fp_lib_dirs, output_path, outline=constraints.outline)
print(f"PCB written to {output_path}")
