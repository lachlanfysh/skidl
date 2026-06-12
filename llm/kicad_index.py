"""KiCad symbol library index for LLM-assisted circuit design.

Two-tier approach:
  1. Fast regex index of all 22K+ symbols (~0.5s cold, cached after)
  2. Detailed pin extraction via simp_sexp for specific symbols on demand

Used by the LLM pipeline to:
  - Inject known IC pin/footprint data into the generation prompt
  - Validate lib:part names in generated specs
  - Suggest corrections for wrong library/footprint references
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_SYM_DIR = Path(os.environ.get("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols"))
_FP_DIR = Path(os.environ.get("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints"))

_SUB_UNIT_RE = re.compile(r"_\d+_\d+$")


@dataclass
class SymbolEntry:
    lib: str
    name: str
    description: str = ""
    keywords: str = ""
    footprint: str = ""
    pin_count: int = 0


@dataclass
class PinInfo:
    num: str
    name: str
    func: str


@dataclass
class SymbolDetail:
    lib: str
    name: str
    description: str
    keywords: str
    footprint: str
    pins: list[PinInfo] = field(default_factory=list)

    def to_prompt_block(self) -> str:
        lines = [f"KiCad Symbol: {self.lib}:{self.name}"]
        if self.description:
            lines.append(f"  Description: {self.description}")
        if self.footprint:
            lines.append(f"  Default Footprint: {self.footprint}")
        if self.pins:
            lines.append(f"  Pins ({len(self.pins)}):")
            for p in self.pins:
                lines.append(f"    {p.num}: {p.name} ({p.func})")
        return "\n".join(lines)


# Module-level cache
_index: dict[str, list[SymbolEntry]] | None = None
_footprint_index: set[str] | None = None


def _build_index() -> dict[str, list[SymbolEntry]]:
    """Parse all .kicad_sym files with regex for speed (~0.5s)."""
    index: dict[str, list[SymbolEntry]] = {}

    if not _SYM_DIR.is_dir():
        return index

    sym_re = re.compile(r'\(symbol\s+"([^"]+)"')
    prop_re = re.compile(
        r'\(property\s+"(Description|ki_keywords|Footprint)"\s+"([^"]*)"',
        re.IGNORECASE,
    )
    pin_re = re.compile(r"\(pin\s+\w+\s+\w+")

    for fpath in sorted(_SYM_DIR.glob("*.kicad_sym")):
        lib_name = fpath.stem
        content = fpath.read_text(errors="replace")

        current_sym: str | None = None
        current_entry: SymbolEntry | None = None
        depth = 0

        for line in content.split("\n"):
            stripped = line.strip()

            sym_m = sym_re.match(stripped)
            if sym_m:
                sym_name = sym_m.group(1)
                if _SUB_UNIT_RE.search(sym_name):
                    if current_entry and pin_re.search(stripped):
                        current_entry.pin_count += 1
                    continue
                if current_entry:
                    index.setdefault(current_entry.lib, []).append(current_entry)
                current_sym = sym_name
                current_entry = SymbolEntry(lib=lib_name, name=sym_name)
                continue

            if current_entry:
                prop_m = prop_re.search(stripped)
                if prop_m:
                    key, val = prop_m.group(1), prop_m.group(2)
                    if key == "Description":
                        current_entry.description = val
                    elif key == "ki_keywords":
                        current_entry.keywords = val
                    elif key.lower() == "footprint":
                        current_entry.footprint = val

                if pin_re.search(stripped):
                    current_entry.pin_count += 1

        if current_entry:
            index.setdefault(current_entry.lib, []).append(current_entry)

    return index


def _build_footprint_index() -> set[str]:
    """Index all available footprint names as 'Library:Name' strings."""
    fps: set[str] = set()
    if not _FP_DIR.is_dir():
        return fps
    for lib_dir in sorted(_FP_DIR.iterdir()):
        if not lib_dir.is_dir() or not lib_dir.name.endswith(".pretty"):
            continue
        lib_name = lib_dir.name[:-7]  # strip .pretty
        for fp_file in lib_dir.glob("*.kicad_mod"):
            fps.add(f"{lib_name}:{fp_file.stem}")
    return fps


def get_index() -> dict[str, list[SymbolEntry]]:
    global _index
    if _index is None:
        _index = _build_index()
    return _index


def get_footprint_index() -> set[str]:
    global _footprint_index
    if _footprint_index is None:
        _footprint_index = _build_footprint_index()
    return _footprint_index


def search_symbols(query: str, limit: int = 10) -> list[SymbolEntry]:
    """Fuzzy search across all symbol libraries."""
    index = get_index()
    query_lower = query.lower()
    query_parts = query_lower.split()

    scored: list[tuple[float, SymbolEntry]] = []
    for entries in index.values():
        for entry in entries:
            text = f"{entry.lib} {entry.name} {entry.description} {entry.keywords}".lower()
            score = 0.0
            for part in query_parts:
                if part in entry.name.lower():
                    score += 3.0
                elif part in entry.keywords.lower():
                    score += 2.0
                elif part in entry.description.lower():
                    score += 1.0
                elif part in entry.lib.lower():
                    score += 0.5
            if score > 0:
                scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:limit]]


def search_footprints(query: str, limit: int = 10) -> list[str]:
    """Search available footprints by name."""
    fps = get_footprint_index()
    query_lower = query.lower()
    matches = [fp for fp in fps if query_lower in fp.lower()]
    matches.sort(key=lambda x: (not x.lower().startswith(query_lower), len(x)))
    return matches[:limit]


def validate_footprint(fp_name: str) -> bool:
    """Check if a footprint exists in the KiCad libraries."""
    return fp_name in get_footprint_index()


def get_symbol_detail(lib: str, name: str) -> Optional[SymbolDetail]:
    """Get full pin information for a specific symbol using simp_sexp."""
    sym_file = _SYM_DIR / f"{lib}.kicad_sym"
    if not sym_file.is_file():
        return None

    try:
        from simp_sexp import Sexp
    except ImportError:
        return None

    content = sym_file.read_text(errors="replace")
    lib_sexp = Sexp(content)

    all_symbols = {
        s[1]: s for s in lib_sexp.search("/kicad_symbol_lib/symbol", ignore_case=True)
    }
    sym = all_symbols.get(name)
    if sym is None:
        return None

    detail = SymbolDetail(lib=lib, name=name, description="", keywords="", footprint="")

    props = sym.search("/symbol/property", ignore_case=True)
    for prop in props:
        if len(prop) >= 3:
            if prop[1] == "Description":
                detail.description = prop[2]
            elif prop[1] == "ki_keywords":
                detail.keywords = prop[2]
            elif prop[1] == "Footprint":
                detail.footprint = prop[2]

    # Follow 'extends' chain for inherited symbols
    extends = sym.search("/symbol/extends", ignore_case=True)
    pin_source = sym
    if extends:
        parent_name = extends[0][1]
        parent = all_symbols.get(parent_name)
        if parent:
            pin_source = parent
            if not detail.description:
                for prop in parent.search("/symbol/property", ignore_case=True):
                    if len(prop) >= 3 and prop[1] == "Description":
                        detail.description = prop[2]

    pin_type_map = {
        "input": "input", "output": "output", "bidirectional": "bidirectional",
        "tri_state": "tristate", "passive": "passive", "power_in": "power_in",
        "power_out": "power_out", "open_collector": "output",
        "open_emitter": "output", "free": "unspecified",
        "unspecified": "unspecified", "no_connect": "no_connect",
    }

    sub_syms = pin_source.search("/symbol/symbol", ignore_case=True)
    all_pin_sources = [pin_source] + (sub_syms if sub_syms else [])
    seen_pins: set[str] = set()
    for source in all_pin_sources:
        pins = source.search("/symbol/pin", ignore_case=True)
        for pin in pins:
            try:
                pin_func = pin_type_map.get(pin[1].lower(), "unspecified")
                pin_name_node = pin.search("/pin/name")
                pin_num_node = pin.search("/pin/number")
                if pin_name_node and pin_num_node:
                    pin_num = pin_num_node[0][1]
                    if pin_num in seen_pins:
                        continue
                    seen_pins.add(pin_num)
                    detail.pins.append(PinInfo(
                        num=pin_num,
                        name=pin_name_node[0][1],
                        func=pin_func,
                    ))
            except (IndexError, TypeError, AttributeError):
                continue

    return detail


def lookup_ic_context(ic_names: list[str]) -> str:
    """Given a list of IC names/values mentioned in a description, look up
    their KiCad library entries and return a prompt-ready context block."""
    blocks = []
    seen = set()

    for ic_name in ic_names:
        results = search_symbols(ic_name, limit=3)
        for entry in results:
            key = f"{entry.lib}:{entry.name}"
            if key in seen:
                continue
            seen.add(key)

            detail = get_symbol_detail(entry.lib, entry.name)
            if detail:
                blocks.append(detail.to_prompt_block())
            else:
                lines = [f"KiCad Symbol: {entry.lib}:{entry.name}"]
                if entry.description:
                    lines.append(f"  Description: {entry.description}")
                if entry.footprint:
                    lines.append(f"  Default Footprint: {entry.footprint}")
                if entry.pin_count:
                    lines.append(f"  Pins: {entry.pin_count}")
                blocks.append("\n".join(lines))

    if not blocks:
        return ""

    return (
        "\nKiCad Library Reference (use these EXACT lib:part names and pin assignments):\n"
        + "\n\n".join(blocks)
        + "\n"
    )


def extract_ic_names(text: str) -> list[str]:
    """Extract likely IC part numbers from marketing/description text."""
    ic_patterns = [
        r"\b(ESP32[A-Za-z0-9-]*)\b",
        r"\b(nRF52[A-Za-z0-9]*)\b",
        r"\b(RP20[0-9]{2}[A-Za-z]*)\b",
        r"\b(SAMD[0-9]+[A-Za-z0-9]*)\b",
        r"\b(ATmega[0-9]+[A-Za-z0-9]*)\b",
        r"\b(STM32[A-Za-z0-9]+)\b",
        r"\b(MCP73[0-9]+[A-Za-z0-9-]*)\b",
        r"\b(AP2112[A-Za-z0-9-]*)\b",
        r"\b(ADS[0-9]+[A-Za-z0-9]*)\b",
        r"\b(BME[0-9]+)\b",
        r"\b(BMP[0-9]+)\b",
        r"\b(INA[0-9]+)\b",
        r"\b(MCP[0-9]+[A-Za-z0-9]*)\b",
        r"\b(MAX[0-9]+[A-Za-z0-9]*)\b",
        r"\b(W25Q[0-9]+[A-Za-z]*)\b",
        r"\b(LC709[0-9]+[A-Za-z]*)\b",
        r"\b(CP2102[A-Za-z0-9]*)\b",
        r"\b(LIS[23][A-Za-z0-9]+)\b",
        r"\b(LSM[0-9]+[A-Za-z0-9]*)\b",
        r"\b(SHT[0-9]+[A-Za-z0-9]*)\b",
        r"\b(TSL[0-9]+[A-Za-z0-9]*)\b",
        r"\b(VEML[0-9]+[A-Za-z0-9]*)\b",
        r"\b(SI[0-9]+[A-Za-z0-9]*)\b",
        r"\b(DS[0-9]+[A-Za-z0-9]*)\b",
    ]
    found = []
    for pattern in ic_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        found.extend(matches)
    return list(dict.fromkeys(found))


def validate_spec_libraries(spec_dict: dict) -> list[dict]:
    """Validate lib:part and footprint references in a CircuitSpec.

    Returns a list of issues with suggestions.
    """
    index = get_index()
    fp_index = get_footprint_index()
    issues = []

    all_libs = set(index.keys())

    for part in spec_dict.get("parts", []):
        ref = part.get("ref", "")
        lib = part.get("lib")
        part_name = part.get("part")
        footprint = part.get("footprint", "")

        if lib and lib not in all_libs:
            suggestions = search_symbols(f"{lib} {part_name or ''}", limit=3)
            issue = {
                "ref": ref,
                "type": "bad_lib",
                "message": f"Library '{lib}' does not exist in KiCad",
                "suggestions": [f"{s.lib}:{s.name}" for s in suggestions],
            }
            issues.append(issue)
        elif lib and part_name:
            lib_entries = index.get(lib, [])
            if not any(e.name == part_name for e in lib_entries):
                suggestions = search_symbols(part_name, limit=3)
                issue = {
                    "ref": ref,
                    "type": "bad_part",
                    "message": f"Symbol '{part_name}' not found in library '{lib}'",
                    "suggestions": [f"{s.lib}:{s.name}" for s in suggestions],
                }
                issues.append(issue)

        if footprint and not validate_footprint(footprint):
            fp_lib = footprint.split(":")[0] if ":" in footprint else ""
            suggestions = search_footprints(fp_lib or footprint, limit=3)
            issue = {
                "ref": ref,
                "type": "bad_footprint",
                "message": f"Footprint '{footprint}' does not exist",
                "suggestions": suggestions,
            }
            issues.append(issue)

    return issues
