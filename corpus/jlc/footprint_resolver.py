"""Resolve KiCad footprints from JLCPCB/LCSC part data.

Given a part that has no valid KiCad footprint, try to:
1. Search JLC for the part by description/value/package
2. Get the LCSC part number
3. Convert the EasyEDA footprint to KiCad format
4. Return the KiCad footprint path

This is the bridge between "part exists on JLC" and "we can place it in KiCad."
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from corpus.jlc.lookup import JLCLookup, JLCPart

CACHE_DIR = Path(__file__).parent / "footprint_cache"

# Common package -> KiCad footprint mapping (no API needed)
_PACKAGE_TO_KICAD = {
    "0201": "Resistor_SMD:R_0201_0603Metric",
    "0402": "Resistor_SMD:R_0402_1005Metric",
    "0603": "Resistor_SMD:R_0603_1608Metric",
    "0805": "Resistor_SMD:R_0805_2012Metric",
    "1206": "Resistor_SMD:R_1206_3216Metric",
    "1210": "Resistor_SMD:R_1210_3225Metric",
    "2010": "Resistor_SMD:R_2010_5025Metric",
    "2512": "Resistor_SMD:R_2512_6332Metric",
    "SOT-23": "Package_TO_SOT_SMD:SOT-23",
    "SOT-23-5": "Package_TO_SOT_SMD:SOT-23-5",
    "SOT-23-6": "Package_TO_SOT_SMD:SOT-23-6",
    "SOD-123": "Diode_SMD:D_SOD-123",
    "SOD-323": "Diode_SMD:D_SOD-323",
    "SOD-123FL": "Diode_SMD:D_SOD-123F",
    "SMA": "Diode_SMD:D_SMA",
    "SMB": "Diode_SMD:D_SMB",
    "SMC": "Diode_SMD:D_SMC",
    "DO-214AC(SMA)": "Diode_SMD:D_SMA",
    "DO-214AA(SMB)": "Diode_SMD:D_SMB",
    "DO-214AB(SMC)": "Diode_SMD:D_SMC",
    "SOIC-8": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    "SOIC-16": "Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    "SSOP-8": "Package_SO:SSOP-8_3.95x5.21mm_P1.27mm",
    "TSSOP-8": "Package_SO:TSSOP-8_4.4x3mm_P0.65mm",
    "TSSOP-14": "Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
    "TSSOP-16": "Package_SO:TSSOP-16_4.4x5mm_P0.65mm",
    "DIP-8": "Package_DIP:DIP-8_W7.62mm",
    "DIP-14": "Package_DIP:DIP-14_W7.62mm",
    "DIP-16": "Package_DIP:DIP-16_W7.62mm",
    "SC-70-5": "Package_TO_SOT_SMD:SC-70-5",
    "SC-70-6": "Package_TO_SOT_SMD:SC-70-6",
    "SOT-323": "Package_TO_SOT_SMD:SOT-323_SC-70",
    "SOT-89": "Package_TO_SOT_SMD:SOT-89-3",
    "TO-252": "Package_TO_SOT_SMD:TO-252-2",
    "TO-263": "Package_TO_SOT_SMD:TO-263-2",
    "QFN-16": "Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm_EP1.7x1.7mm",
    "QFN-20": "Package_DFN_QFN:QFN-20-1EP_4x4mm_P0.5mm_EP2.5x2.5mm",
    "QFN-24": "Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.45x2.45mm",
    "QFN-32": "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
    "QFN-48": "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm",
    "LQFP-32": "Package_QFP:LQFP-32_7x7mm_P0.8mm",
    "LQFP-48": "Package_QFP:LQFP-48_7x7mm_P0.5mm",
    "LQFP-64": "Package_QFP:LQFP-64_10x10mm_P0.5mm",
    "LQFP-100": "Package_QFP:LQFP-100_14x14mm_P0.5mm",
    "TQFP-32": "Package_QFP:TQFP-32_7x7mm_P0.8mm",
    "TQFP-44": "Package_QFP:TQFP-44_10x10mm_P0.8mm",
    "TQFP-48": "Package_QFP:TQFP-48_7x7mm_P0.5mm",
    "TQFP-64": "Package_QFP:TQFP-64_10x10mm_P0.5mm",
    "USB-C": "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
    # MSOP
    "MSOP-8": "Package_SO:MSOP-8-1EP_3x3mm_P0.65mm_EP1.68x1.88mm",
    "MSOP-10": "Package_SO:MSOP-10-1EP_3x3mm_P0.5mm_EP1.68x1.88mm",
    # DFN
    "DFN-8": "Package_DFN_QFN:DFN-8-1EP_3x2mm_P0.5mm_EP1.36x1.46mm",
    "DFN-6": "Package_DFN_QFN:DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm",
    "DFN-10": "Package_DFN_QFN:DFN-10-1EP_3x3mm_P0.5mm_EP1.55x2.48mm",
    # More QFN
    "QFN-28": "Package_DFN_QFN:QFN-28-1EP_4x4mm_P0.4mm_EP2.4x2.4mm",
    "QFN-40": "Package_DFN_QFN:QFN-40-1EP_5x5mm_P0.4mm_EP3.1x3.1mm",
    "QFN-56": "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP5.6x5.6mm",
    "QFN-64": "Package_DFN_QFN:QFN-64-1EP_9x9mm_P0.5mm_EP4.65x4.65mm",
    # More QFP
    "LQFP-144": "Package_QFP:LQFP-144_20x20mm_P0.5mm",
    "TQFP-100": "Package_QFP:TQFP-100_14x14mm_P0.5mm",
    # SSOP
    "SSOP-16": "Package_SO:SSOP-16_5.3x6.2mm_P0.65mm",
    "SSOP-20": "Package_SO:SSOP-20_5.3x7.2mm_P0.65mm",
    "SSOP-24": "Package_SO:SSOP-24_5.3x8.2mm_P0.65mm",
    "SSOP-28": "Package_SO:SSOP-28_5.3x10.2mm_P0.65mm",
    # DIP
    "DIP-20": "Package_DIP:DIP-20_W7.62mm",
    "DIP-24": "Package_DIP:DIP-24_W7.62mm",
    "DIP-28": "Package_DIP:DIP-28_W7.62mm",
    "DIP-40": "Package_DIP:DIP-40_W15.24mm",
    # SOT
    "SOT-25": "Package_TO_SOT_SMD:SOT-23-5",
    "SOT-26": "Package_TO_SOT_SMD:SOT-23-6",
    "SOT-353": "Package_TO_SOT_SMD:SC-70-5",
    "SOT-363": "Package_TO_SOT_SMD:SC-70-6",
    "SOT-223": "Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    # VQFN
    "VQFN-32": "Package_DFN_QFN:QFN-32-1EP_5x5mm_P0.5mm_EP3.45x3.45mm",
    "VQFN-48": "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm",
    # UFQFPN
    "UFQFPN-48": "Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.15x5.15mm",
    # WLCSP — no standard KiCad footprint, but flag it
    # BGA
    "BGA-256": "Package_BGA:BGA-256_17.0x17.0mm_Layout16x16_P1.0mm",
}

import re

_LCSC_SUFFIX_RE = re.compile(r"[\(\-].*$")


def footprint_from_package(package: str) -> str | None:
    """Package string -> KiCad footprint lookup. Handles LCSC naming quirks."""
    fp = _PACKAGE_TO_KICAD.get(package)
    if fp:
        return fp
    # Strip LCSC suffixes: "TQFP-32(7x7)" -> "TQFP-32", "DIP-28-300mil" -> "DIP-28"
    base = package
    for suffix in ("-EP", "-300mil", "-208mil"):
        base = base.replace(suffix, "")
    base = re.sub(r"\([^)]*\)$", "", base).strip()
    if base != package:
        fp = _PACKAGE_TO_KICAD.get(base)
        if fp:
            return fp
    # Try with pin count only: "SOT-25-5" -> "SOT-25"
    m = re.match(r"^([A-Z]+-\d+)-\d+$", package)
    if m:
        return _PACKAGE_TO_KICAD.get(m.group(1))
    return None


_FP_QUERY_MAP = {
    "thonkiconn": "PJ-398 3.5mm jack",
    "pj398": "PJ-398 3.5mm jack",
    "pj301": "PJ-301 3.5mm jack",
    "pj324": "PJ-324 3.5mm jack",
    "wqp518": "3.5mm jack WQP518",
    "wqp729": "3.5mm jack WQP729",
    "audiojack": "3.5mm audio jack",
    "alphapot": "RK09 potentiometer alpha",
    "pot_underside": "RK09 potentiometer",
    "pot.*9mm": "RK09 9mm potentiometer",
    "songhuei": "songhuei 9mm potentiometer",
    "pec11r": "PEC11R rotary encoder",
    "bourns.*pec": "PEC11R rotary encoder",
    "eurorack.*power": "IDC 2x5 shrouded header",
    "2x05.*shroud": "IDC 2x5 shrouded header",
    "2x08.*shroud": "IDC 2x8 shrouded header",
    "d6r": "toggle switch SPST",
    "dailywell": "toggle switch SPDT",
    "sw_spdt": "toggle switch SPDT",
    "sw_spst": "toggle switch SPST",
    "potentiometer.*alpha": "RK09 potentiometer alpha",
    "alpha.*pot": "RK09 potentiometer alpha",
    "alpha.*r09": "RK09 potentiometer alpha",
    "potentiometer.*vertical": "RK09 potentiometer vertical",
    "power.*2x8": "IDC 2x8 shrouded header",
    "power.*horizontal": "IDC 2x5 shrouded header eurorack",
    "daisypatch": "Daisy Seed module header",
    "ts06": "tactile switch 6mm SMD",
    "b3u": "tactile switch SMD B3U",
    "sk6812": "SK6812 addressable LED",
    "ws2812": "WS2812 addressable LED",
    "oled.*ssd1306": "SSD1306 OLED 128x32",
    "ssd1306": "SSD1306 OLED display",
    "pmf42": "fuse SMD 0603",
    "eehza": "electrolytic capacitor SMD",
    "m20.*998": "pin header socket",
}


def _query_from_footprint(fp: str) -> str | None:
    """Extract a JLC search query from a project-specific footprint name."""
    import re
    if ":" in fp:
        _, name = fp.split(":", 1)
    else:
        name = fp
    full = fp.lower()

    for pattern, query in _FP_QUERY_MAP.items():
        if re.search(pattern, full, re.IGNORECASE):
            return query
    return None


def resolve_footprint(
    part_description: str,
    value: str = "",
    package_hint: str = "",
    jlc: JLCLookup | None = None,
) -> tuple[str | None, str | None]:
    """Try to find a KiCad footprint via JLC lookup.

    Returns (kicad_footprint, lcsc_number) or (None, None).
    """
    if jlc is None:
        jlc = JLCLookup()

    query = f"{value} {package_hint} {part_description}".strip()
    if not query:
        return None, None

    results = jlc.search(query, limit=3)
    if not results:
        return None, None

    for part in results:
        # Try direct package mapping first
        fp = footprint_from_package(part.package)
        if fp:
            return fp, part.lcsc

    # If no direct mapping, return the LCSC number for easyeda2kicad conversion
    return None, results[0].lcsc


def convert_easyeda_footprint(lcsc: str) -> str | None:
    """Convert an EasyEDA footprint to KiCad format via easyeda2kicad.

    Returns the KiCad footprint string (lib:name) or None on failure.
    Caches results locally.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file = CACHE_DIR / f"{lcsc}.json"

    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            return data.get("footprint")
        except (json.JSONDecodeError, TypeError):
            pass

    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            result = subprocess.run(
                ["easyeda2kicad", "--full", f"--lcsc_id={lcsc}",
                 f"--output={tmpdir}/out"],
                capture_output=True, text=True, timeout=30,
            )
            # Find generated .pretty directory
            pretty_dirs = list(Path(tmpdir).rglob("*.pretty"))
            if pretty_dirs:
                kicad_mods = list(pretty_dirs[0].glob("*.kicad_mod"))
                if kicad_mods:
                    lib_name = pretty_dirs[0].stem
                    fp_name = kicad_mods[0].stem
                    footprint = f"{lib_name}:{fp_name}"
                    cache_file.write_text(json.dumps({"footprint": footprint, "lcsc": lcsc}))
                    return footprint
    except (subprocess.TimeoutExpired, OSError):
        pass

    cache_file.write_text(json.dumps({"footprint": None, "lcsc": lcsc}))
    return None


@dataclass
class JLCCandidate:
    """A JLC-sourced footprint candidate with confidence tier."""
    new_fp: str | None
    lcsc: str
    source: str         # jlc_package_map | jlc_domain_query | jlc_value_search | jlc_lcsc_only
    confidence: float   # 0.0-1.0; >=0.8 = auto-apply, <0.8 = LLM reviews
    description: str    # human-readable summary for the reviewing agent


def jlc_footprint_candidates(
    fp: str,
    part_value: str = "",
    part_description: str = "",
    jlc: JLCLookup | None = None,
) -> list[JLCCandidate]:
    """Generate confidence-tiered JLC candidates for a missing footprint.

    Returns candidates sorted by confidence (highest first). The correction
    loop auto-applies >=0.8; lower-confidence results are surfaced for LLM review.
    """
    if jlc is None:
        jlc = JLCLookup()

    candidates: list[JLCCandidate] = []
    seen_fps: set[str] = set()

    # Strategy 1: domain-specific name pattern -> JLC query (medium confidence)
    query = _query_from_footprint(fp)
    if query:
        results = jlc.search(query, limit=3)
        for r in results:
            kicad_fp = footprint_from_package(r.package)
            if kicad_fp and kicad_fp not in seen_fps:
                seen_fps.add(kicad_fp)
                candidates.append(JLCCandidate(
                    new_fp=kicad_fp, lcsc=r.lcsc,
                    source="jlc_domain_query", confidence=0.6,
                    description=f"JLC domain match: {r.package} ({r.description[:60]})",
                ))
        if not candidates and results:
            candidates.append(JLCCandidate(
                new_fp=None, lcsc=results[0].lcsc,
                source="jlc_lcsc_only", confidence=0.2,
                description=f"JLC part found but no standard footprint: {results[0].description[:60]}",
            ))

    # Strategy 2: search by part value/description (low confidence — prone to cross-type matching)
    # Extract package hint from the original footprint name for cross-check
    import re
    fp_pkg_hint = None
    fp_name = fp.split(":", 1)[-1] if ":" in fp else fp
    pkg_match = re.search(r"(?:^|[_\-\s])(0201|0402|0603|0805|1206|1210|2010|2512)(?:[_\-\s]|$)", fp_name)
    if pkg_match:
        fp_pkg_hint = pkg_match.group(1)

    value_query = f"{part_value} {part_description}".strip()
    if value_query:
        results = jlc.search(value_query, limit=3)
        for r in results:
            kicad_fp = footprint_from_package(r.package)
            if kicad_fp and kicad_fp not in seen_fps:
                seen_fps.add(kicad_fp)
                # Only high confidence when package matches what the footprint name implies
                if fp_pkg_hint and r.package == fp_pkg_hint:
                    conf = 0.85
                else:
                    conf = 0.4
                candidates.append(JLCCandidate(
                    new_fp=kicad_fp, lcsc=r.lcsc,
                    source="jlc_value_search", confidence=conf,
                    description=f"JLC value search: {r.package} ({r.description[:60]})",
                ))

    candidates.sort(key=lambda c: c.confidence, reverse=True)
    return candidates


def resolve_spec_footprints(
    spec: dict,
    fp_dirs: list[str] | None = None,
    jlc: JLCLookup | None = None,
) -> dict[str, dict]:
    """Resolve missing footprints in a CircuitSpec using JLC lookup.

    Returns {old_fp: {"new_fp": str, "lcsc": str, "source": str, "confidence": float}}
    for each resolved. Does NOT mutate spec — caller applies changes.
    """
    if fp_dirs is None:
        fp_dirs = ["/usr/share/kicad/footprints"]
    if jlc is None:
        jlc = JLCLookup()

    from schemas.translator import _footprint_exists

    resolved: dict[str, dict] = {}
    seen: set[str] = set()

    for part in spec.get("parts", []):
        fp = part.get("footprint", "")
        if fp in seen or _footprint_exists(fp, fp_dirs):
            continue
        seen.add(fp)

        cands = jlc_footprint_candidates(
            fp, part.get("value", ""), part.get("part", ""), jlc,
        )
        if cands:
            best = cands[0]
            resolved[fp] = {
                "new_fp": best.new_fp,
                "lcsc": best.lcsc,
                "source": best.source,
                "confidence": best.confidence,
            }

    return resolved


def generate_bom_csv(
    parts: list[dict],
    output_path: str | Path,
) -> None:
    """Generate JLCPCB-format BOM CSV.

    parts: list of dicts with keys: comment, designator, footprint, lcsc
    """
    import csv
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Comment", "Designator", "Footprint", "LCSC Part Number"])
        for p in parts:
            writer.writerow([
                p.get("comment", ""),
                p.get("designator", ""),
                p.get("footprint", ""),
                p.get("lcsc", ""),
            ])


def generate_cpl_csv(
    placements: list[dict],
    output_path: str | Path,
) -> None:
    """Generate JLCPCB-format CPL (pick-and-place) CSV.

    placements: list of dicts with keys: designator, mid_x, mid_y, rotation, layer
    """
    import csv
    with open(output_path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["Designator", "Mid X", "Mid Y", "Rotation", "Layer"])
        for p in placements:
            writer.writerow([
                p.get("designator", ""),
                f"{p.get('mid_x', 0):.4f}",
                f"{p.get('mid_y', 0):.4f}",
                f"{p.get('rotation', 0):.1f}",
                p.get("layer", "Top"),
            ])
