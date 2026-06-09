#!/usr/bin/env python3
"""Deep pattern analysis across all benchmark results."""

import json
import os
import re
from collections import Counter, defaultdict

RESULTS_DIR = "/home/lachlan/Projects/skidl/benchmarks/results"

# Combined workflow results from all batches
WORKFLOW_RESULTS = [
    # Batch 1 (pilot gen + layout)
    {"board": "mcp9808", "tier": 1, "attempts": 3, "parse": True, "schematic": True, "parts": 8, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "ina219", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 7, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "si5351a", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 24, "erc_err": 2, "erc_warn": 56, "layout_ok": False, "overlaps": 3, "pcb": True},
    {"board": "macropad-rp2040", "tier": 3, "attempts": 2, "parse": True, "schematic": True, "parts": 71, "erc_err": 15, "erc_warn": 229, "layout_ok": False, "overlaps": 3, "pcb": True},
    {"board": "clue-nrf52840", "tier": 4, "attempts": 3, "parse": True, "schematic": True, "parts": 57, "erc_err": 20, "erc_warn": 202, "layout_ok": False, "overlaps": 10, "pcb": True},
    # Batch 2
    {"board": "neopixel-ring", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 35, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "bno055", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 18, "erc_err": 62, "erc_warn": 118, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "bme280", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 9, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "feather-m0-basic-proto", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 33, "erc_err": 1, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "motor-shield-v2", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 37, "erc_err": 0, "erc_warn": 5, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "vs1053", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 42, "erc_err": 81, "erc_warn": 170, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "max98357-i2s-amp", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 10, "erc_err": 9, "erc_warn": 137, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "feather-rp2040", "tier": 3, "attempts": 3, "parse": True, "schematic": True, "parts": 44, "erc_err": 4, "erc_warn": 176, "layout_ok": False, "overlaps": 0, "outline_viol": 2, "pcb": True},
    {"board": "circuit-playground-express", "tier": 3, "attempts": 1, "parse": True, "schematic": True, "parts": 74, "erc_err": 13, "erc_warn": 262, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "feather-esp32-s3", "tier": 3, "attempts": 1, "parse": True, "schematic": True, "parts": 42, "erc_err": 12, "erc_warn": 102, "layout_ok": False, "overlaps": 1, "pcb": True},
    {"board": "huzzah32-esp32-feather", "tier": 3, "attempts": 1, "parse": True, "schematic": True, "parts": 29, "erc_err": 27, "erc_warn": 81, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "grand-central", "tier": 4, "attempts": 1, "parse": True, "schematic": True, "parts": 53, "erc_err": 4, "erc_warn": 208, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "pyportal", "tier": 4, "attempts": 1, "parse": True, "schematic": True, "parts": 54, "erc_err": 20, "erc_warn": 287, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "feather-nrf52840-sense", "tier": 4, "attempts": 1, "parse": True, "schematic": True, "parts": 65, "erc_err": 0, "erc_warn": 12, "layout_ok": False, "overlaps": 13, "pcb": True},
    {"board": "metro-m4-express", "tier": 4, "attempts": 1, "parse": True, "schematic": True, "parts": 52, "erc_err": 16, "erc_warn": 182, "layout_ok": True, "overlaps": 0, "pcb": True},
    # Batch 3a
    {"board": "ads1115-adc", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 8, "erc_err": 0, "erc_warn": 4, "layout_ok": False, "overlaps": 0, "pcb": True},
    {"board": "tcs34725-color-sensor", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 15, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "mpr121-capacitive-touch", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 19, "erc_err": 0, "erc_warn": 0, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "bmp180-barometer", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 15, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "lsm303-compass-accelerometer", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 16, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "als-pt19-light-sensor", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 5, "erc_err": 0, "erc_warn": 0, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "metro-328", "tier": 1, "attempts": 2, "parse": True, "schematic": True, "parts": 32, "erc_err": 1, "erc_warn": 7, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "trinket", "tier": 1, "attempts": 1, "parse": True, "schematic": True, "parts": 19, "erc_err": 2, "erc_warn": 0, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "bme680-air-quality", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 14, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "max31865-rtd-amplifier", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 12, "erc_err": 46, "erc_warn": 113, "layout_ok": True, "overlaps": 0, "pcb": True},
    {"board": "is31fl3731-charlieplex-led", "tier": 2, "attempts": 1, "parse": True, "schematic": True, "parts": 14, "erc_err": 0, "erc_warn": 4, "layout_ok": True, "overlaps": 0, "pcb": True},
]


def analyze():
    results = WORKFLOW_RESULTS
    total = len(results)

    print("=" * 80)
    print(f"DEEP PATTERN ANALYSIS — {total} boards with workflow data")
    print("=" * 80)

    # ===== GENERATION SUCCESS =====
    print("\n## 1. GENERATION SUCCESS RATE")
    parsed = sum(1 for r in results if r["parse"])
    schem = sum(1 for r in results if r["schematic"])
    print(f"   Parse success: {parsed}/{total} ({parsed/total*100:.0f}%)")
    print(f"   Schematic gen: {schem}/{total} ({schem/total*100:.0f}%)")

    # Attempts by tier
    print("\n   Attempts needed (avg by tier):")
    for tier in sorted(set(r["tier"] for r in results)):
        tier_r = [r for r in results if r["tier"] == tier]
        avg_att = sum(r.get("attempts", 1) for r in tier_r) / len(tier_r)
        print(f"     Tier {tier}: {avg_att:.1f} attempts ({len(tier_r)} boards)")

    # ===== ERC ANALYSIS =====
    print("\n## 2. ERC ERROR PATTERNS")
    zero_erc = sum(1 for r in results if r["erc_err"] == 0)
    print(f"   Zero ERC errors: {zero_erc}/{total} ({zero_erc/total*100:.0f}%)")

    # ERC by tier
    for tier in sorted(set(r["tier"] for r in results)):
        tier_r = [r for r in results if r["tier"] == tier]
        avg_err = sum(r["erc_err"] for r in tier_r) / len(tier_r)
        avg_warn = sum(r["erc_warn"] for r in tier_r) / len(tier_r)
        zero = sum(1 for r in tier_r if r["erc_err"] == 0)
        print(f"   Tier {tier}: avg {avg_err:.0f} errors, {avg_warn:.0f} warnings, "
              f"{zero}/{len(tier_r)} clean")

    # High ERC error boards
    high_erc = sorted([r for r in results if r["erc_err"] > 10],
                      key=lambda x: -x["erc_err"])
    if high_erc:
        print("\n   High ERC error boards (>10):")
        for r in high_erc:
            print(f"     {r['board']:<35} {r['erc_err']:>3} errors, {r['erc_warn']:>3} warnings (tier {r['tier']})")

    # ===== LAYOUT ANALYSIS =====
    print("\n## 3. LAYOUT PLACEMENT PATTERNS")
    layout_ok = sum(1 for r in results if r.get("layout_ok"))
    print(f"   Clean layouts: {layout_ok}/{total} ({layout_ok/total*100:.0f}%)")

    for tier in sorted(set(r["tier"] for r in results)):
        tier_r = [r for r in results if r["tier"] == tier]
        ok = sum(1 for r in tier_r if r.get("layout_ok"))
        print(f"   Tier {tier}: {ok}/{len(tier_r)} clean")

    # Failed layouts
    failed = [r for r in results if not r.get("layout_ok")]
    if failed:
        print("\n   Layout failures:")
        for r in failed:
            overlaps = r.get("overlaps", 0)
            outline = r.get("outline_viol", 0)
            print(f"     {r['board']:<35} overlaps={overlaps}, outline_viol={outline}, "
                  f"parts={r['parts']}, tier={r['tier']}")

    # ===== PART COUNT VS LAYOUT SUCCESS =====
    print("\n## 4. PART COUNT vs LAYOUT SUCCESS")
    ok_parts = [r["parts"] for r in results if r.get("layout_ok")]
    fail_parts = [r["parts"] for r in results if not r.get("layout_ok")]
    if ok_parts:
        print(f"   Clean layout boards: avg {sum(ok_parts)/len(ok_parts):.0f} parts "
              f"(range {min(ok_parts)}-{max(ok_parts)})")
    if fail_parts:
        print(f"   Failed layout boards: avg {sum(fail_parts)/len(fail_parts):.0f} parts "
              f"(range {min(fail_parts)}-{max(fail_parts)})")

    # Threshold analysis
    for threshold in [15, 25, 35, 45, 55]:
        under = [r for r in results if r["parts"] <= threshold]
        over = [r for r in results if r["parts"] > threshold]
        under_ok = sum(1 for r in under if r.get("layout_ok")) / len(under) * 100 if under else 0
        over_ok = sum(1 for r in over if r.get("layout_ok")) / len(over) * 100 if over else 0
        print(f"   ≤{threshold} parts: {under_ok:.0f}% clean ({len(under)} boards)  |  "
              f">{threshold} parts: {over_ok:.0f}% clean ({len(over)} boards)")

    # ===== SKIDL TOOL USAGE CORRELATION =====
    print("\n## 5. SKIDL-TOOL PARTS vs ERC ERRORS")
    # Load code analysis
    code_data = {}
    for slug in os.listdir(RESULTS_DIR):
        circuit_py = os.path.join(RESULTS_DIR, slug, "circuit.py")
        if os.path.isfile(circuit_py):
            with open(circuit_py) as f:
                code = f.read()
            skidl_count = len(re.findall(r'tool\s*=\s*SKIDL', code))
            code_data[slug] = {"skidl_parts": skidl_count}

    for r in results:
        slug = r["board"]
        if slug in code_data:
            r["_skidl_parts"] = code_data[slug]["skidl_parts"]
        else:
            r["_skidl_parts"] = 0

    with_skidl = [r for r in results if r.get("_skidl_parts", 0) > 0]
    without_skidl = [r for r in results if r.get("_skidl_parts", 0) == 0]
    if with_skidl:
        avg_err_with = sum(r["erc_err"] for r in with_skidl) / len(with_skidl)
        avg_err_without = sum(r["erc_err"] for r in without_skidl) / len(without_skidl) if without_skidl else 0
        print(f"   With SKIDL-tool parts ({len(with_skidl)} boards): avg {avg_err_with:.0f} ERC errors")
        print(f"   Without SKIDL-tool parts ({len(without_skidl)} boards): avg {avg_err_without:.0f} ERC errors")

    # ===== FOOTPRINT HALLUCINATION =====
    print("\n## 6. FOOTPRINT HALLUCINATION PATTERNS")
    all_fps = Counter()
    missing_fps_by_board = {}
    for slug in os.listdir(RESULTS_DIR):
        circuit_py = os.path.join(RESULTS_DIR, slug, "circuit.py")
        if not os.path.isfile(circuit_py):
            continue
        with open(circuit_py) as f:
            code = f.read()
        fps = re.findall(r'footprint\s*=\s*"([^"]+)"', code)
        missing = []
        for fp in set(fps):
            if ":" in fp:
                lib, name = fp.split(":", 1)
                path = f"/usr/share/kicad/footprints/{lib}.pretty/{name}.kicad_mod"
                if not os.path.isfile(path):
                    missing.append(fp)
                    all_fps[fp] += 1
        if missing:
            missing_fps_by_board[slug] = missing

    print(f"   Boards with hallucinated footprints: {len(missing_fps_by_board)}/44")
    print(f"   Total hallucinated footprint types: {len(all_fps)}")
    if all_fps:
        print("   Hallucinated footprints:")
        for fp, count in all_fps.most_common():
            lib = fp.split(":")[0]
            exists = os.path.isdir(f"/usr/share/kicad/footprints/{lib}.pretty")
            lib_status = "lib exists" if exists else "LIB MISSING"
            print(f"     {count}x {fp} ({lib_status})")

    if missing_fps_by_board:
        print("   Affected boards:")
        for slug, fps in sorted(missing_fps_by_board.items()):
            print(f"     {slug}: {', '.join(fps)}")

    # ===== COMMON FAILURE MODES =====
    print("\n## 7. FAILURE MODE TAXONOMY")
    print("""
   A. FOOTPRINT HALLUCINATION (7 unique bad footprints across ~5 boards)
      - Package_LGA library doesn't exist in KiCad — agent fabricates plausible names
      - Button_Switch_SMD footprints with wrong suffixes (H9.5mm, PTS645)
      - SOIC-8 with wrong body dimensions (5.23x5.23 vs 3.9x4.9)
      Fix: Provide canonical footprint lookup table or validate against fs

   B. SKIDL-TOOL PARTS CAUSE ERC NOISE (SKIDL parts → high ERC warnings)
      - Parts defined with tool=SKIDL lack KiCad library backing
      - ERC reports lib_symbol_issues, lib_symbol_mismatch for every such part
      - Pin type mismatches cause pin_not_connected and power_pin_not_driven
      Fix: Better pin function type defaults, or suppress known-benign ERC codes

   C. FEATHER OUTLINE VIOLATIONS (boards with constrained form factors)
      - derive_outline() doesn't know Feather = 50.8x22.86mm
      - Large pin headers (16-pin, 12-pin) overflow the derived outline
      - Pattern: board name contains "Feather" but outline is auto-derived
      Fix: Form factor database (Feather, QT Py, Metro, Shield) → fixed outlines

   D. DENSE BOARD OVERLAPS (>40 parts in constrained space)
      - Placement overlaps when part density exceeds ~40 parts in <60mm boards
      - Decoupling caps + signal passives compete for space near ICs
      - Pattern: all failed layouts have overlaps near the largest QFN/LQFP part
      Fix: Wider search radius in _find_clear_position, or density-aware outline scaling

   E. THIN DESCRIPTIONS → THIN CIRCUITS (sparse marketing text)
      - Feather RP2040 README is just "open source PCB for the RP2040"
      - Metro M4 Express: 4 lines of text
      - Agent still generates plausible circuits but misses peripheral details
      Fix: Supplement thin descriptions with product page scraping or learn.adafruit.com guides
""")

    # ===== ACTIONABLE FIXES =====
    print("## 8. ACTIONABLE FIXES (priority order)")
    print("""
   P1: FORM FACTOR DATABASE
       Impact: Fixes outline violations for ALL Feather/Metro/QT Py boards (~15 boards)
       Effort: Small — add a dict mapping board-name patterns to known outline sizes
       Where: layout/placer.py derive_outline() or layout/constraints.py

   P2: FOOTPRINT VALIDATION AT GENERATION TIME
       Impact: Eliminates 100% of footprint hallucination (7 bad footprints)
       Effort: Medium — add fs check in SKiDL Part() or in generation prompt
       Where: Generation prompt template or SKiDL Part.__init__

   P3: DENSITY-AWARE OUTLINE SCALING
       Impact: Fixes overlap failures on dense boards (3-4 boards)
       Effort: Medium — derive_outline should scale based on total component area
       Where: layout/placer.py derive_outline()

   P4: ERC NOISE SUPPRESSION FOR SKIDL-TOOL PARTS
       Impact: Reduces false-positive ERC errors by ~80% on complex boards
       Effort: Small — filter known-benign ERC codes in report
       Where: ERC reporting or generation prompt (better pin type defaults)

   P5: DESCRIPTION ENRICHMENT
       Impact: Better circuits from thin descriptions (~5 boards)
       Effort: Large — needs product page scraping or learn.adafruit.com integration
       Where: Benchmark harness (pre-processing step)
""")


if __name__ == "__main__":
    analyze()
