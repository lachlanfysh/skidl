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


def _search_tokens(text: str) -> list[str]:
    """Tokenize names/descriptions across KiCad's hyphen/underscore variants."""
    lower = text.lower()
    tokens = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", lower)
    expanded: list[str] = []
    for token in tokens:
        if len(token) > 1:
            expanded.append(token)
        if token in {"usb", "type"}:
            expanded.append(token)
        if token in {"micro", "mini"}:
            expanded.append(token)

    if re.search(r"\busb[-_\s]*c\b|\btype[-_\s]*c\b", lower):
        expanded.extend(["usb", "type", "receptacle"])
    if re.search(r"\busb[-_\s]*(micro|mini)[-_\s]*b\b|\b(micro|mini)[-_\s]*b\b", lower):
        expanded.extend(["usb", "receptacle"])
    if "usb" in expanded and any(t in expanded for t in ("connector", "receptacle")):
        expanded.append("receptacle")
    return expanded


def _is_round_din_text(query_lower: str) -> bool:
    """True for circular DIN/MIDI connector text, not DIN41612 backplanes."""
    if "din41612" in query_lower:
        return False
    if re.search(r"\bdin[-_\s]*(?:[3-8]|[3-8][-_ ]?pin|pin)\b", query_lower):
        return True
    return "din" in query_lower and any(
        term in query_lower for term in ("midi", "connector", "jack", "socket", "5-pin", "5 pin")
    )


def _symbol_connector_boost(query_lower: str, query_tokens: set[str], entry: SymbolEntry) -> float:
    """Boost common connector aliases that KiCad stores in generic libraries."""
    name_lower = entry.name.lower()
    if "usb" not in query_tokens:
        return 0.0
    if entry.lib != "Connector" or "usb" not in name_lower:
        return 0.0

    score = 4.0
    if any(t in query_tokens for t in ("connector", "receptacle")):
        score += 4.0
    if "receptacle" in name_lower:
        score += 3.0
    if re.search(r"\busb[-_\s]*c\b|\btype[-_\s]*c\b", query_lower):
        if "usb_c" in name_lower or "type-c" in entry.description.lower():
            score += 6.0
    if re.search(r"\bmicro[-_\s]*b\b|\busb[-_\s]*micro\b", query_lower):
        if "micro" in name_lower and "b" in name_lower:
            score += 6.0
    if "16p" in query_lower and "16p" in name_lower:
        score += 2.0
    if "14p" in query_lower and "14p" in name_lower:
        score += 2.0
    if "6p" in query_lower and "6p" in name_lower:
        score += 2.0
    return score


def _symbol_din_connector_boost(query_lower: str, entry: SymbolEntry) -> float:
    """Boost circular DIN connector symbols without conflating DIN41612."""
    if not _is_round_din_text(query_lower) or entry.lib != "Connector":
        return 0.0

    name_lower = entry.name.lower()
    if not name_lower.startswith("din-"):
        return 0.0

    score = 8.0
    requested_pin_count = None
    match = re.search(r"\bdin[-_\s]*([3-8])\b|\b([3-8])[-_\s]*pin\b", query_lower)
    if match:
        requested_pin_count = next(group for group in match.groups() if group)
    if requested_pin_count:
        if f"din-{requested_pin_count}" in name_lower:
            score += 10.0
        else:
            score -= 3.0
    if "180" in query_lower and "180" in name_lower:
        score += 2.0
    return score


def _symbol_audio_connector_boost(query_lower: str, entry: SymbolEntry) -> float:
    """Bias audio/control jack symbol search toward the requested contact style."""
    if entry.lib != "Connector_Audio":
        return 0.0

    if _is_round_din_text(query_lower) and not any(
        term in query_lower for term in ("trs", "trrs", "3.5", "audio", "stereo", "mono")
    ):
        return 0.0

    name_lower = entry.name.lower()
    combined = f"{entry.name} {entry.description}".lower()
    if not any(
        term in query_lower
        for term in (
            "audio",
            "jack",
            "trs",
            "trrs",
            "mono",
            "stereo",
            "midi",
            "shutter",
            "camera",
            "3.5",
            "6.35",
        )
    ):
        return 0.0

    score = 2.0
    switch_requested = (
        re.search(r"\b(switched|switching|normalled|normalling|detect)\b", query_lower)
        is not None
        and re.search(r"\bunswitched\b", query_lower) is None
    )
    unswitched_requested = re.search(r"\bunswitched\b", query_lower) is not None

    if any(term in name_lower for term in ("audiojack", "audioplug")):
        score += 2.0
    if switch_requested:
        if "switch" in combined or "normalling" in combined:
            score += 8.0
        else:
            score -= 5.0
        if "plug" in name_lower and "switch" not in combined:
            score -= 4.0
    elif unswitched_requested and ("switch" in combined or "normalling" in combined):
        score -= 5.0

    if any(term in query_lower for term in ("trs", "stereo")):
        if "audiojack3" in name_lower or "audioplug3" in name_lower or "3 poles" in combined:
            score += 4.0
    if any(term in query_lower for term in ("trrs", "4 pole", "4-pole")):
        if "audiojack4" in name_lower or "audioplug4" in name_lower or "4 poles" in combined:
            score += 4.0
    if any(term in query_lower for term in ("mono", "ts ")):
        if "audiojack2" in name_lower or "audioplug2" in name_lower or "2 poles" in combined:
            score += 4.0
    if "dual" in query_lower and "dual" in combined:
        score += 3.0
    return score


def _is_plain_switch_query(query_lower: str) -> bool:
    """True for user controls/switches, not switched jacks or connectors."""
    if any(term in query_lower for term in ("jack", "audio", "trs", "trrs", "barrel", "din")):
        return False
    return any(
        term in query_lower
        for term in (
            "switch",
            "pushbutton",
            "push button",
            "button",
            "key switch",
            "keyboard",
            "cherry mx",
            "tactile",
            "reed",
        )
    )


def _symbol_switch_boost(query_lower: str, entry: SymbolEntry) -> float:
    """Prefer the Switch library for mechanical/user switch queries."""
    if not _is_plain_switch_query(query_lower):
        return 0.0

    combined = f"{entry.lib} {entry.name} {entry.description} {entry.keywords}".lower()
    if entry.lib == "Switch":
        score = 8.0
        if any(term in query_lower for term in ("keyboard", "cherry mx", "key switch")):
            if entry.name == "SW_Push":
                score += 10.0
            if "sw_push" in combined or "push" in combined:
                score += 5.0
        if any(term in query_lower for term in ("tactile", "pushbutton", "push button", "button")):
            if "push" in combined:
                score += 4.0
        if "reed" in query_lower and "reed" in combined:
            score += 5.0
        return score

    if entry.lib.startswith("Connector"):
        return -8.0
    return 0.0


def search_symbols(query: str, limit: int = 10) -> list[SymbolEntry]:
    """Fuzzy search across all symbol libraries."""
    index = get_index()
    query_lower = query.lower()
    query_parts = _search_tokens(query_lower)
    query_token_set = set(query_parts)

    scored: list[tuple[float, SymbolEntry]] = []
    for entries in index.values():
        for entry in entries:
            name_tokens = set(_search_tokens(entry.name))
            keyword_tokens = set(_search_tokens(entry.keywords))
            description_tokens = set(_search_tokens(entry.description))
            lib_tokens = set(_search_tokens(entry.lib))
            score = 0.0
            for part in query_parts:
                if part in name_tokens:
                    score += 3.0
                elif part in keyword_tokens:
                    score += 2.0
                elif part in description_tokens:
                    score += 1.0
                elif part in lib_tokens:
                    score += 0.5
            score += _symbol_connector_boost(query_lower, query_token_set, entry)
            score += _symbol_din_connector_boost(query_lower, entry)
            score += _symbol_audio_connector_boost(query_lower, entry)
            score += _symbol_switch_boost(query_lower, entry)
            if score > 0:
                scored.append((score, entry))

    scored.sort(key=lambda x: -x[0])
    return [e for _, e in scored[:limit]]


def _fp_tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    expanded: list[str] = []
    for token in tokens:
        expanded.append(token)
        if token in {"right", "angle", "edge", "edgefacing"}:
            expanded.append("horizontal")
        if token in {"tht", "through", "hole"}:
            expanded.append("throughhole")
        if token in {"smd", "smt"}:
            expanded.extend(["smd", "smt"])
        if token in {"dual", "gang"}:
            expanded.extend(["dual", "double"])
        if token in {"2", "02", "2p"}:
            expanded.extend(["1x02", "02p"])
        if token in {"3", "03", "3p"}:
            expanded.extend(["1x03", "03p"])
    return [token for token in expanded if len(token) > 1]


def _is_round_din_query(query_lower: str) -> bool:
    return _is_round_din_text(query_lower)


def _is_round_din_footprint(fp_lower: str) -> bool:
    """True for circular DIN footprints; false for DIN41612 card-edge families."""
    if "connector_din:" not in fp_lower or "din41612" in fp_lower:
        return False
    return "din" in fp_lower


def _mechanical_family_score(query_lower: str, fp_lower: str) -> float:
    score = 0.0
    if _is_round_din_query(query_lower):
        if _is_round_din_footprint(fp_lower):
            score += 10.0
            if re.search(r"\bdin[-_\s]*5\b|\b5[-_\s]*pin\b", query_lower):
                if re.search(r"din[-_]?5|din_5", fp_lower):
                    score += 6.0
            if "horizontal" in query_lower and "horizontal" in fp_lower:
                score += 2.0
            if "vertical" in query_lower and "vertical" in fp_lower:
                score += 2.0
        return score
    if any(term in query_lower for term in ("jack", "audio", "trs", "mono", "3.5", "pj-3", "pj3")):
        if "connector_audio:" in fp_lower:
            score += 6.0
        if "jack_3.5mm" in fp_lower:
            score += 6.0
        if "pj320d" in fp_lower or "pj-320" in query_lower:
            score += 2.0
    if any(term in query_lower for term in ("terminal", "screw", "field input")):
        if "terminalblock" in fp_lower:
            score += 7.0
        if "1x02" in fp_lower and any(term in query_lower for term in ("2 pin", "2-pin", "02")):
            score += 3.0
    if "5.08" in query_lower and "p5.08mm" in fp_lower:
        score += 5.0
    if "5.00" in query_lower and "p5.00mm" in fp_lower:
        score += 4.0
    if "3.50" in query_lower or "3.5mm" in query_lower:
        if "p3.50mm" in fp_lower:
            score += 4.0
    if any(term in query_lower for term in ("usb-c", "usb c", "usb_c", "type-c", "type c")):
        if "connector_usb:" in fp_lower:
            score += 5.0
        if "usb_c_receptacle" in fp_lower:
            score += 7.0
        if "16p" in query_lower and "16p" in fp_lower:
            score += 3.0
        if "6p" in query_lower and "6p" in fp_lower:
            score += 3.0
    if any(term in query_lower for term in ("micro-b", "micro b", "usb_b_micro", "usb micro")):
        if "connector_usb:" in fp_lower:
            score += 5.0
        if "micro" in fp_lower and ("usb" in fp_lower or "usb_b" in fp_lower):
            score += 7.0
    if any(term in query_lower for term in ("pot", "potentiometer", "volume")):
        if "potentiometer" in fp_lower:
            score += 7.0
        if any(term in query_lower for term in ("dual", "gang", "stereo")) and (
            "dual" in fp_lower or "double" in fp_lower
        ):
            score += 4.0
    if any(term in query_lower for term in ("right angle", "edge-facing", "horizontal")):
        if "horizontal" in fp_lower:
            score += 3.0
    if "vertical" in query_lower and "vertical" in fp_lower:
        score += 3.0
    if any(term in query_lower for term in ("smd", "smt")):
        if "smd" in fp_lower or "smt" in fp_lower:
            score += 3.0
    elif any(term in query_lower for term in ("through hole", "through-hole", "tht")):
        if "smd" not in fp_lower and "smt" not in fp_lower:
            score += 1.5
    return score


def _score_footprint(query: str, fp: str) -> float:
    query_lower = query.lower()
    fp_lower = fp.lower()
    if _is_round_din_query(query_lower) and not _is_round_din_footprint(fp_lower):
        return 0.0
    if query_lower in fp_lower:
        return 1000.0 - len(fp) * 0.001
    score = _mechanical_family_score(query_lower, fp_lower)
    fp_words = set(_fp_tokens(fp_lower))
    for token in _fp_tokens(query_lower):
        if token in fp_words:
            score += 2.0
        elif token in fp_lower:
            score += 1.0
    return score


def search_footprints(query: str, limit: int = 10) -> list[str]:
    """Search available footprints by token and mechanical-family relevance."""
    fps = get_footprint_index()
    scored = [
        (_score_footprint(query, fp), fp)
        for fp in fps
    ]
    scored = [(score, fp) for score, fp in scored if score > 0]
    scored.sort(key=lambda item: (-item[0], len(item[1]), item[1]))
    return [fp for _, fp in scored[:limit]]


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
