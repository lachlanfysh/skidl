#!/usr/bin/env python3
"""Evaluate a generated SKiDL circuit through layout and optional routing."""

import json
import os
import sys
import traceback

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

def evaluate(circuit_dir):
    """Run layout evaluation on a generated circuit directory."""
    circuit_py = os.path.join(circuit_dir, "circuit.py")
    if not os.path.isfile(circuit_py):
        return {"error": "circuit.py not found"}

    result = {
        "layout_ok": False,
        "layout_overlaps": 0,
        "layout_outline_violations": 0,
        "layout_missing_refs": 0,
        "hpwl_total": 0.0,
        "errors": [],
    }

    try:
        import builtins
        from skidl import *
        set_default_tool(KICAD9)
        from skidl.layout import (
            extract_groups,
            place_parts,
            validate,
            LayoutConstraints,
            BoardOutline,
            derive_outline,
            load_footprint_bboxes,
        )

        builtins.default_circuit.reset()
        exec(open(circuit_py).read(), {"__name__": "__skidl_bench__"})

        ckt = builtins.default_circuit
        parts_with_fp = [p for p in ckt.parts if getattr(p, "footprint", None)]
        if not parts_with_fp:
            result["errors"].append("No parts with footprints found")
            return result

        fp_names = {str(p.footprint) for p in parts_with_fp}
        fp_lib_dirs = ["/usr/share/kicad/footprints"]

        try:
            fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)
        except Exception as e:
            result["errors"].append(f"Failed to load footprint bboxes: {e}")
            fp_bboxes = {}

        outline = derive_outline(ckt, fp_bboxes)
        constraints = LayoutConstraints(outline=outline)
        groups = extract_groups(ckt)
        placed = place_parts(groups, constraints, fp_bboxes, circuit=ckt)

        validation = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
        result["layout_ok"] = validation.ok
        result["layout_overlaps"] = len(validation.overlaps)
        result["layout_outline_violations"] = len(validation.outline_violations)
        result["layout_missing_refs"] = len(validation.missing_refs)

        total_hpwl = sum(
            net_hpwl for net_hpwl in validation.net_hpwl.values()
        ) if hasattr(validation, 'net_hpwl') and validation.net_hpwl else 0.0
        result["hpwl_total"] = total_hpwl

        pcb_path = os.path.join(circuit_dir, "board.kicad_pcb")
        try:
            from skidl.layout import write_kicad_pcb
            write_kicad_pcb(
                placed, ckt, fp_lib_dirs, pcb_path,
                outline=constraints.outline,
            )
            result["pcb_written"] = True
        except Exception as e:
            result["pcb_written"] = False
            result["errors"].append(f"PCB write failed: {e}")

        result["part_count_placed"] = len(placed)

    except Exception as e:
        result["errors"].append(f"Layout evaluation failed: {traceback.format_exc()}")

    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 evaluate_layout.py <circuit_dir>")
        sys.exit(1)

    circuit_dir = sys.argv[1]
    result = evaluate(circuit_dir)
    print(json.dumps(result, indent=2))

    score_path = os.path.join(circuit_dir, "layout_score.json")
    with open(score_path, "w") as f:
        json.dump(result, f, indent=2)
    print(f"Saved to {score_path}")
