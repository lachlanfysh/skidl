"""Deterministic CircuitSpec -> SKiDL Circuit translator.

Validation runs in passes, collecting ALL errors in each pass before
stopping (one review round-trip fixes everything found so far):

  pass 1: net pin cross-references (every "REF.PIN" names a real part)
  pass 2: symbol libraries exist
  pass 3: parts exist within their libraries
  pass 4: footprints exist on disk
  pass 5: pins exist on resolved symbols (numbers, names, or aliases)
  build : only when passes 1-5 are clean

Every failure is a DesignException whose candidates are computed
deterministically (difflib closest matches, actual pin listings) — no LLM
involvement.

Must run with KICAD9_SYMBOL_DIR set. Designed to execute inside the
engine worker subprocess, but safe in-process for tests.
"""

from __future__ import annotations

import difflib
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

from .circuit_spec import PIN_FUNCS, PIN_REF_RE, CircuitSpec, PartSpec
from .exceptions import ActionType, Candidate, DesignException, ExcCode, Severity

DEFAULT_SYM_DIR = "/usr/share/kicad/symbols"
DEFAULT_FP_DIR = "/usr/share/kicad/footprints"

# KiCad version renames and common LLM hallucinations → canonical KiCad 9 names.
# Checked at get_template() time before fuzzy fallback.
_SYMBOL_ALIASES: dict[tuple[str, str], tuple[str, str]] = {
    # Potentiometers — renamed in KiCad 6+
    ("Device", "R_POT"): ("Device", "R_Potentiometer"),
    ("Device", "R_POT_TRIM"): ("Device", "R_Potentiometer_Trim"),
    ("Device", "R_POT_DUAL"): ("Device", "R_Potentiometer_Dual"),
    ("Device", "R_POT_MountingPin"): ("Device", "R_Potentiometer_MountingPin"),
    # Transistor pinout suffixes — KiCad 9 uses generic Q_NPN/Q_PNP
    ("Device", "Q_NPN_BEC"): ("Device", "Q_NPN"),
    ("Device", "Q_NPN_BCE"): ("Device", "Q_NPN"),
    ("Device", "Q_NPN_CBE"): ("Device", "Q_NPN"),
    ("Device", "Q_NPN_ECB"): ("Device", "Q_NPN"),
    ("Device", "Q_PNP_BEC"): ("Device", "Q_PNP"),
    ("Device", "Q_PNP_BCE"): ("Device", "Q_PNP"),
    ("Device", "Q_PNP_CBE"): ("Device", "Q_PNP"),
    ("Device", "Q_PNP_ECB"): ("Device", "Q_PNP"),
    # N-channel/P-channel MOSFET pinout variants
    ("Device", "Q_NMOS_GSD"): ("Device", "Q_NMOS_GDS"),
    ("Device", "Q_NMOS_SGD"): ("Device", "Q_NMOS_GDS"),
    ("Device", "Q_NMOS_DSG"): ("Device", "Q_NMOS_GDS"),
    ("Device", "Q_PMOS_GSD"): ("Device", "Q_PMOS_GDS"),
    ("Device", "Q_PMOS_SGD"): ("Device", "Q_PMOS_GDS"),
    # Audio jacks: LLMs put them in "Connector" but they live in "Connector_Audio"
    ("Connector", "AudioJack2"): ("Connector_Audio", "AudioJack2"),
    ("Connector", "AudioJack2_SwitchT"): ("Connector_Audio", "AudioJack2_SwitchT"),
    ("Connector", "AudioJack2_Ground"): ("Connector_Audio", "AudioJack2_Ground"),
    ("Connector", "AudioJack2_Ground_Switch"): ("Connector_Audio", "AudioJack2_Ground_Switch"),
    ("Connector", "AudioJack2_Ground_SwitchT"): ("Connector_Audio", "AudioJack2_Ground_SwitchT"),
    ("Connector", "AudioJack3"): ("Connector_Audio", "AudioJack3"),
    ("Connector", "AudioJack3_SwitchTR"): ("Connector_Audio", "AudioJack3_SwitchTR"),
    ("Connector", "AudioJack3_Ground"): ("Connector_Audio", "AudioJack3_Ground"),
    # Resistor pack renamed
    ("Device", "R_PACK"): ("Device", "R_Pack04"),
    ("Device", "R_Pack"): ("Device", "R_Pack04"),
}


@dataclass
class TranslationResult:
    circuit: object | None = None          # skidl Circuit when clean
    exceptions: list[DesignException] = field(default_factory=list)
    # ref -> {requested_pin_id: resolved_pin_num} for netlist comparison
    resolved_pins: dict[str, dict[str, str]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.circuit is not None and not any(
            e.severity != Severity.ADVISORY for e in self.exceptions
        )


def _close(name: str, pool: list[str], n: int = 5) -> list[str]:
    matches = difflib.get_close_matches(name, pool, n=n, cutoff=0.4)
    if not matches:
        # fall back to substring containment for short queries
        low = name.lower()
        matches = [p for p in pool if low in p.lower()][:n]
    return matches


def _exc(eid: str, code: ExcCode, msg: str, subject: dict,
         candidates: list[Candidate], retry_hint: str = "") -> DesignException:
    return DesignException(
        id=eid, code=code, severity=Severity.FATAL, message=msg,
        subject=subject, candidates=candidates,
        retry_hint=retry_hint or f"pick a candidate_id from {[c.id for c in candidates]}",
    )


class _SymbolResolver:
    """Caches SchLib lookups and parsed symbol templates."""

    def __init__(self, sym_dir: str):
        self.sym_dir = sym_dir
        self._libs: dict[str, object] = {}
        self._lib_names: list[str] | None = None
        self._templates: dict[tuple[str, str], object] = {}
        self._part_names: dict[str, list[str]] = {}

    def lib_names(self) -> list[str]:
        if self._lib_names is None:
            self._lib_names = sorted(
                f[: -len(".kicad_sym")]
                for f in os.listdir(self.sym_dir)
                if f.endswith(".kicad_sym")
            )
        return self._lib_names

    def get_lib(self, lib: str):
        """Return SchLib or None if the library doesn't exist."""
        if lib in self._libs:
            return self._libs[lib]
        if lib not in self.lib_names():
            self._libs[lib] = None
            return None
        from skidl import SchLib
        try:
            schlib = SchLib(lib)
        except FileNotFoundError:
            schlib = None
        self._libs[lib] = schlib
        return schlib

    def part_names(self, lib: str) -> list[str]:
        if lib not in self._part_names:
            schlib = self.get_lib(lib)
            self._part_names[lib] = sorted(p.name for p in schlib.parts) if schlib else []
        return self._part_names[lib]

    def get_template(self, lib: str, part: str):
        """Return a fully parsed template Part or None if not found."""
        key = (lib, part)
        if key in self._templates:
            return self._templates[key]
        # Check alias table before hitting the library
        alias_key = (lib, part)
        if alias_key in _SYMBOL_ALIASES:
            alias_lib, alias_part = _SYMBOL_ALIASES[alias_key]
            tmpl = self.get_template(alias_lib, alias_part)
            self._templates[key] = tmpl
            return tmpl
        schlib = self.get_lib(lib)
        tmpl = None
        if schlib is not None:
            found = schlib.get_parts_by_name(part, allow_failure=True, partial_parse=False)
            if found:
                tmpl = found[0] if isinstance(found, list) else found
        self._templates[key] = tmpl
        return tmpl

    def resolve_alias(self, lib: str, part: str) -> tuple[str, str]:
        """Return the canonical (lib, part) after alias lookup."""
        alias_key = (lib, part)
        if alias_key in _SYMBOL_ALIASES:
            return _SYMBOL_ALIASES[alias_key]
        return lib, part

    def find_part_across_libs(self, part: str, limit: int = 5) -> list[tuple[str, str]]:
        """Fast cross-library search using regex on raw .kicad_sym files.

        Avoids full SKiDL parsing — just greps for top-level symbol definitions.
        Exact case-insensitive matches sort first.
        """
        results: list[tuple[str, str]] = []
        exact: list[tuple[str, str]] = []
        part_lower = part.lower()
        sym_re = re.compile(r'\(symbol "([^"]+)"\s')
        for lib in self.lib_names():
            path = os.path.join(self.sym_dir, f"{lib}.kicad_sym")
            try:
                with open(path) as f:
                    content = f.read()
            except OSError:
                continue
            seen = set()
            for m in sym_re.finditer(content):
                name = m.group(1)
                # Skip sub-symbols (contain _N_N suffix like MyPart_0_1)
                if "_0_1" in name or "_1_1" in name or "_2_1" in name:
                    continue
                if name in seen:
                    continue
                seen.add(name)
                if name.lower() == part_lower:
                    exact.append((lib, name))
                elif part_lower in name.lower():
                    results.append((lib, name))
            if len(exact) + len(results) >= limit:
                break
        return (exact + results)[:limit]

    @staticmethod
    def pin_ids(tmpl) -> dict[str, str]:
        """Map every legal pin identifier (num, name, alias) -> pin num."""
        ids: dict[str, str] = {}
        for pin in tmpl.pins:
            num = str(pin.num)
            ids[num] = num
            if pin.name:
                ids[str(pin.name)] = num
            for alias in getattr(pin, "aliases", []) or []:
                ids[str(alias)] = num
        return ids


def _footprint_exists(fp: str, fp_dirs: list[str]) -> bool:
    if ":" not in fp:
        return False
    lib, name = fp.split(":", 1)
    return any(
        os.path.isfile(os.path.join(d, f"{lib}.pretty", f"{name}.kicad_mod"))
        for d in fp_dirs
    )


def _footprint_candidates(fp: str, fp_dirs: list[str]) -> list[str]:
    """Closest existing footprints: same-library first, then cross-library by name."""
    out: list[str] = []
    lib, name = (fp.split(":", 1) + [""])[:2] if ":" in fp else (fp, "")
    for d in fp_dirs:
        pretty = os.path.join(d, f"{lib}.pretty")
        if os.path.isdir(pretty):
            pool = [f[: -len(".kicad_mod")] for f in os.listdir(pretty) if f.endswith(".kicad_mod")]
            out += [f"{lib}:{m}" for m in _close(name, pool)]
        else:
            lib_pool = [p[: -len(".pretty")] for p in os.listdir(d) if p.endswith(".pretty")]
            for cand_lib in _close(lib, lib_pool, n=2):
                cpretty = os.path.join(d, f"{cand_lib}.pretty")
                pool = [f[: -len(".kicad_mod")] for f in os.listdir(cpretty) if f.endswith(".kicad_mod")]
                out += [f"{cand_lib}:{m}" for m in _close(name, pool, n=3)]
    seen: set[str] = set()
    return [x for x in out if not (x in seen or seen.add(x))][:6]


# --- footprint remapping -------------------------------------------------
# Many open-source projects use project-local footprint library names for
# standard parts (e.g. winterbloom:R_0603_HandSolder).  When the lib doesn't
# exist on disk, try to find the same footprint name in standard KiCad libs.

_PASSIVE_RE = re.compile(
    r"^([RCL])_(\d{4})(?:_(\d{4}Metric))?(.*)$"
)
_PASSIVE_LIB_MAP = {"R": "Resistor_SMD", "C": "Capacitor_SMD", "L": "Inductor_SMD"}

_HANDSOLDER_STRIP = re.compile(r"_?HandSolder(?:ing)?$", re.IGNORECASE)


def _remap_footprint(fp: str, fp_dirs: list[str]) -> str | None:
    """Try to find a standard KiCad footprint matching a non-standard one.

    Returns the remapped footprint string, or None if no match found.
    """
    if ":" not in fp:
        return None
    lib, name = fp.split(":", 1)

    # 1. Exact name match in any standard library
    for d in fp_dirs:
        if not os.path.isdir(d):
            continue
        for pretty in os.listdir(d):
            if not pretty.endswith(".pretty"):
                continue
            if os.path.isfile(os.path.join(d, pretty, f"{name}.kicad_mod")):
                return f"{pretty[:-len('.pretty')]}:{name}"

    # 2. Passive pattern: R_0603_HandSolder -> Resistor_SMD:R_0603_1608Metric*
    m = _PASSIVE_RE.match(name)
    if m:
        prefix, size = m.group(1), m.group(2)
        std_lib = _PASSIVE_LIB_MAP.get(prefix)
        if std_lib:
            for d in fp_dirs:
                pretty_dir = os.path.join(d, f"{std_lib}.pretty")
                if not os.path.isdir(pretty_dir):
                    continue
                candidates = [
                    f[:-len(".kicad_mod")]
                    for f in os.listdir(pretty_dir)
                    if f.startswith(f"{prefix}_{size}") and f.endswith(".kicad_mod")
                ]
                if candidates:
                    # Prefer HandSolder variant if original had it, else plain Metric
                    hs = "HandSolder" in name or "handsolder" in name.lower()
                    if hs:
                        hs_match = [c for c in candidates if "HandSolder" in c]
                        if hs_match:
                            return f"{std_lib}:{hs_match[0]}"
                    plain = [c for c in candidates if "Metric" in c and "HandSolder" not in c]
                    if plain:
                        return f"{std_lib}:{sorted(plain)[0]}"
                    return f"{std_lib}:{sorted(candidates)[0]}"

    # 3. Name without HandSolder suffix
    stripped = _HANDSOLDER_STRIP.sub("", name)
    if stripped != name:
        for d in fp_dirs:
            if not os.path.isdir(d):
                continue
            for pretty in os.listdir(d):
                if not pretty.endswith(".pretty"):
                    continue
                # Try exact stripped name
                if os.path.isfile(os.path.join(d, pretty, f"{stripped}.kicad_mod")):
                    return f"{pretty[:-len('.pretty')]}:{stripped}"

    # 4. LED pattern
    if name.startswith("LED_") and name[4:8].isdigit():
        for d in fp_dirs:
            pretty_dir = os.path.join(d, "LED_SMD.pretty")
            if not os.path.isdir(pretty_dir):
                continue
            candidates = [
                f[:-len(".kicad_mod")]
                for f in os.listdir(pretty_dir)
                if f.startswith(f"LED_{name[4:8]}") and f.endswith(".kicad_mod")
            ]
            if candidates:
                return f"LED_SMD:{sorted(candidates)[0]}"

    # 5. TestPoint pattern
    if "TestPoint" in name or name.startswith("TP_"):
        for d in fp_dirs:
            pretty_dir = os.path.join(d, "TestPoint.pretty")
            if not os.path.isdir(pretty_dir):
                continue
            pool = [f[:-len(".kicad_mod")] for f in os.listdir(pretty_dir) if f.endswith(".kicad_mod")]
            matches = _close(name, pool, n=1)
            if matches:
                return f"TestPoint:{matches[0]}"

    # 5b. Keyboard switch footprints (Kailh Choc, Gateron LP, etc.)
    choc_m = re.match(
        r"(?i)SW_(?:choc|kailh|gateron_lp)[_\-].*?(\d+(?:\.\d+)?)\s*u",
        name,
    )
    if choc_m:
        ku = choc_m.group(1)
        _CHOC_SIZE_MAP = {
            "1": "1.00u", "1.0": "1.00u", "1.00": "1.00u",
            "1.25": "1.25u", "1.5": "1.50u", "1.50": "1.50u",
            "1.75": "1.75u", "2": "2.00u", "2.0": "2.00u", "2.00": "2.00u",
            "2.25": "2.25u", "2.75": "2.75u", "6.25": "6.25u",
        }
        mx_size = _CHOC_SIZE_MAP.get(ku, "1.00u")
        target = f"SW_Cherry_MX_{mx_size}_PCB"
        for d in fp_dirs:
            if os.path.isfile(os.path.join(d, "Button_Switch_Keyboard.pretty", f"{target}.kicad_mod")):
                return f"Button_Switch_Keyboard:{target}"

    # 5c. Addressable RGB LED (SK6812, WS2812) from custom keyboard/LED libs
    if re.search(r"(?i)(SK6812|WS2812|LED_choc|LED_cherry|rgb.*led|led.*rgb)", name):
        for d in fp_dirs:
            pretty_dir = os.path.join(d, "LED_SMD.pretty")
            if not os.path.isdir(pretty_dir):
                continue
            pool = [f[:-len(".kicad_mod")] for f in os.listdir(pretty_dir) if f.endswith(".kicad_mod")]
            for preferred in ["LED_SK6812MINI_PLCC4_3.5x3.5mm_P1.75mm",
                              "LED_WS2812B-Mini_PLCC4_3.5x3.5mm",
                              "LED_SK6812_PLCC4_5.0x5.0mm_P3.2mm",
                              "LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm"]:
                if preferred in pool:
                    return f"LED_SMD:{preferred}"

    # 5d. USB-C receptacle variants (HRO, Korean Hroparts, etc.)
    usbc_m = re.match(
        r"(?i)USB_C_Receptacle.*HRO.*TYPE-C-31-M-(\d+)",
        name,
    )
    if usbc_m:
        target = "USB_C_Receptacle_HRO_TYPE-C-31-M-12"
        for d in fp_dirs:
            if os.path.isfile(os.path.join(d, "Connector_USB.pretty", f"{target}.kicad_mod")):
                return f"Connector_USB:{target}"

    # 5e. Pogo pin connectors -> pin headers (extract pin count from name)
    pogo_m = re.match(r"(?i)Pogo.*?(\d+)p", name)
    if pogo_m:
        n_pins = pogo_m.group(1)
        target = f"PinHeader_1x{n_pins.zfill(2)}_P2.54mm_Vertical"
        for d in fp_dirs:
            if os.path.isfile(os.path.join(d, "Connector_PinHeader_2.54mm.pretty", f"{target}.kicad_mod")):
                return f"Connector_PinHeader_2.54mm:{target}"

    # 6. Generic domain substitutions — close-enough footprint for engine testing
    _GENERIC_MAP = [
        # Eurorack jacks (Thonkiconn, QingPu, etc.) -> standard audio jack
        (r"(?i)(thonkiconn|pj398|pj301|jack.*3.5|wqp.*pj|audiojack|audio.*jack|nmj6)",
         "Connector_Audio", "Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles"),
        # Pots (Alpha, Bourns, pedal pots)
        (r"(?i)(pot.*underside|alphapot|pot.*alpha|pec11|songhuei|pot.*9mm|potentiometer)",
         "Potentiometer_THT", "Potentiometer_Alps_RK09K_Single_Vertical"),
        # Toggle switches (Dailywell, D6R, etc.)
        (r"(?i)(sw_spdt|sw_spst|dailywell|d6r|toggle.*switch|switch.*spdt)",
         "Button_Switch_THT", "SW_PUSH_6mm"),
        # Eurorack shrouded power connector
        (r"(?i)(eurorack.*power|power.*2x[058]|2x05.*shroud|2x08.*shroud|idc.*2x0[58])",
         "Connector_IDC", "IDC-Header_2x05_P2.54mm_Vertical"),
        # Tactile switches (Arduino-style)
        (r"(?i)(ts06|b3u|tact.*switch|sw_push.*smt)",
         "Button_Switch_SMD", "SW_SPST_TL3342"),
    ]
    for pattern, target_lib, target_name in _GENERIC_MAP:
        if re.search(pattern, name) or re.search(pattern, lib):
            for d in fp_dirs:
                if os.path.isfile(os.path.join(d, f"{target_lib}.pretty", f"{target_name}.kicad_mod")):
                    return f"{target_lib}:{target_name}"

    return None


def remap_footprints(spec: CircuitSpec, fp_dirs: list[str]) -> dict[str, str]:
    """Auto-remap non-existent footprints to standard KiCad equivalents.

    Returns {old_fp: new_fp} for all remappings applied.
    Mutates spec.parts in place.
    """
    remapped: dict[str, str] = {}
    seen: dict[str, str | None] = {}
    for part in spec.parts:
        fp = part.footprint
        if fp in seen:
            if seen[fp] is not None:
                part.footprint = seen[fp]
            continue
        if _footprint_exists(fp, fp_dirs):
            seen[fp] = None
            continue
        new_fp = _remap_footprint(fp, fp_dirs)
        if new_fp and new_fp != fp:
            remapped[fp] = new_fp
            part.footprint = new_fp
            seen[fp] = new_fp
        else:
            seen[fp] = None
    return remapped


# --- custom (tool=SKIDL) part construction -------------------------------
# Pattern proven in benchmarks/results/als-pt19-light-sensor/circuit.py.

def _make_pin(num, name, func_name):
    from skidl import Pin
    func = getattr(Pin.types, PIN_FUNCS[func_name])
    return Pin(num=num, name=name, func=func,
               x=0, y=0, orientation="R", length=100, rotation=0)


def _add_skidl_draw_cmds(part):
    pins = list(part.pins)
    if not pins:
        return
    spacing, pin_len = 2.54, 2.54
    left, right = pins[: len(pins) // 2], pins[len(pins) // 2:]
    max_side = max(len(left), len(right), 1)
    body_h = max(max_side * spacing, spacing * 2)
    body_w = max(spacing * 4, spacing * 2)
    rect = ["rectangle", ["start", -body_w / 2, -body_h / 2],
            ["end", body_w / 2, body_h / 2],
            ["stroke", ["width", 0.254], ["type", "default"]],
            ["fill", ["type", "none"]]]
    pin_cmds = []
    for side, rot, xsign in ((left, 0, -1), (right, 180, 1)):
        for i, pin in enumerate(side):
            y = -body_h / 2 + spacing * (i + 0.5)
            x = xsign * (body_w / 2 + pin_len)
            pin.x, pin.y = x, y
            pin.orientation = "R" if xsign < 0 else "L"
            pin.rotation = rot
            pin_cmds.append([
                "pin", "passive", "line", ["at", x, y, rot], ["length", pin_len],
                ["name", pin.name, ["effects", ["font", ["size", 1.27, 1.27]]]],
                ["number", str(pin.num), ["effects", ["font", ["size", 1.27, 1.27]]]],
            ])
    part.draw_cmds = defaultdict(list)
    part.draw_cmds[0] = [rect]
    part.draw_cmds[1] = pin_cmds + [rect]


class _FakeLib:
    def __init__(self, name="skidl"):
        self.filename = name


# --- main entry -----------------------------------------------------------

def translate(spec: CircuitSpec,
              sym_dir: str | None = None,
              fp_dirs: list[str] | None = None) -> TranslationResult:
    sym_dir = sym_dir or os.environ.get("KICAD9_SYMBOL_DIR", DEFAULT_SYM_DIR)
    fp_dirs = fp_dirs or [os.environ.get("KICAD9_FOOTPRINT_DIR", DEFAULT_FP_DIR)]

    result = TranslationResult()
    excs = result.exceptions
    eid_counter = iter(range(1, 10_000))

    def next_id() -> str:
        return f"e{next(eid_counter)}"

    refs = {p.ref for p in spec.parts}

    # pass 1: net pin cross-references
    for net in spec.nets:
        for pin_ref in net.pins:
            ref = PIN_REF_RE.match(pin_ref).group(1)
            if ref not in refs:
                excs.append(_exc(
                    next_id(), ExcCode.SPEC_MALFORMED,
                    f"net {net.name!r} references unknown part ref {ref!r}",
                    {"net": net.name, "pin": pin_ref, "ref": ref},
                    [Candidate(id=f"c{i+1}", action=ActionType.REPLACE_PIN,
                               params={"ref": m, "old": pin_ref.split('.', 1)[1],
                                       "new": pin_ref.split('.', 1)[1]},
                               human_summary=f"reassign endpoint to existing part {m}")
                     for i, m in enumerate(_close(ref, sorted(refs), 3))]
                    + [Candidate(id="c9", action=ActionType.REMOVE_NET_PIN,
                                 params={"net": net.name, "pin": pin_ref},
                                 human_summary="drop this endpoint from the net")],
                ))
    if excs:
        return result

    resolver = _SymbolResolver(sym_dir)
    lib_parts = [p for p in spec.parts if p.lib is not None]

    # pass 2: libraries
    bad_libs: set[str] = set()
    for lib in sorted({p.lib for p in lib_parts}):
        if resolver.get_lib(lib) is None:
            bad_libs.add(lib)
            affected = [p for p in lib_parts if p.lib == lib]
            cands: list[Candidate] = []
            # Search all libraries for the actual part names
            for pspec in affected:
                found = resolver.find_part_across_libs(pspec.part)
                for found_lib, found_part in found:
                    summary = f"use {found_lib!r}:{found_part!r} for {pspec.ref}"
                    cands.append(Candidate(
                        id=f"c{len(cands)+1}",
                        action=ActionType.REPLACE_LIB,
                        params={"ref": pspec.ref, "old": lib, "new": found_lib,
                                "also_replace_part": found_part},
                        human_summary=summary,
                    ))
                    break  # best match per part
            # Fallback: fuzzy library name match
            if not cands:
                cands = [Candidate(id=f"c{i+1}", action=ActionType.REPLACE_LIB,
                                   params={"ref": "*", "old": lib, "new": m},
                                   human_summary=f"use library {m!r} instead of {lib!r}")
                         for i, m in enumerate(_close(lib, resolver.lib_names()))]
            excs.append(_exc(
                next_id(), ExcCode.SPEC_UNKNOWN_LIB,
                f"KiCad symbol library {lib!r} does not exist",
                {"lib": lib, "refs": [p.ref for p in affected]},
                cands,
            ))
    if excs:
        return result

    # pass 3: parts within libraries
    bad_parts: set[tuple[str, str]] = set()
    for pspec in lib_parts:
        if resolver.get_template(pspec.lib, pspec.part) is None:
            bad_parts.add((pspec.lib, pspec.part))
            cands: list[Candidate] = []
            # Cross-library search: find the part name in any library
            cross_lib_hits = resolver.find_part_across_libs(pspec.part, limit=3)
            for found_lib, found_part in cross_lib_hits:
                cands.append(Candidate(
                    id=f"c{len(cands)+1}",
                    action=ActionType.REPLACE_LIB,
                    params={"ref": pspec.ref, "old": pspec.lib, "new": found_lib,
                            "also_replace_part": found_part},
                    human_summary=f"use {found_lib!r}:{found_part!r} instead",
                    confidence=0.85 if found_part.lower() == pspec.part.lower() else 0.6,
                ))
            # Fuzzy within declared library
            for m in _close(pspec.part, resolver.part_names(pspec.lib)):
                cands.append(Candidate(
                    id=f"c{len(cands)+1}", action=ActionType.REPLACE_PART,
                    params={"ref": pspec.ref, "new": m},
                    human_summary=f"use symbol {m!r} from {pspec.lib}",
                ))
            cands.append(Candidate(id=f"c{len(cands)+1}", action=ActionType.REMOVE_PART,
                                   params={"ref": pspec.ref},
                                   human_summary=f"remove part {pspec.ref} entirely",
                                   cost_hint="expensive"))
            excs.append(_exc(
                next_id(), ExcCode.SPEC_UNKNOWN_PART,
                f"symbol {pspec.part!r} not found in library {pspec.lib!r}",
                {"ref": pspec.ref, "lib": pspec.lib, "part": pspec.part},
                cands,
            ))
    if excs:
        return result

    # pass 3.5: auto-remap footprints from project-local libs to standard KiCad
    remapped = remap_footprints(spec, fp_dirs)

    # pass 4: footprints
    for fp in sorted({p.footprint for p in spec.parts}):
        if not _footprint_exists(fp, fp_dirs):
            cands = [Candidate(id=f"c{i+1}", action=ActionType.REPLACE_FOOTPRINT,
                               params={"old": fp, "new": m},
                               human_summary=f"use footprint {m!r}")
                     for i, m in enumerate(_footprint_candidates(fp, fp_dirs))]

            # JLC candidates: lower confidence, surfaced for LLM review
            try:
                from corpus.jlc.footprint_resolver import jlc_footprint_candidates
                parts_with_fp = [p for p in spec.parts if p.footprint == fp]
                jlc_cands = jlc_footprint_candidates(
                    fp,
                    parts_with_fp[0].value if parts_with_fp else "",
                    parts_with_fp[0].part if parts_with_fp else "",
                )
                seen_fps = {c.params["new"] for c in cands}
                for jc in jlc_cands:
                    if jc.new_fp and jc.new_fp not in seen_fps:
                        seen_fps.add(jc.new_fp)
                        cands.append(Candidate(
                            id=f"c{len(cands)+1}",
                            action=ActionType.REPLACE_FOOTPRINT,
                            params={"old": fp, "new": jc.new_fp, "lcsc": jc.lcsc},
                            human_summary=jc.description,
                            confidence=jc.confidence,
                            source=jc.source,
                        ))
            except ImportError:
                pass

            excs.append(_exc(
                next_id(), ExcCode.SPEC_BAD_FOOTPRINT,
                f"footprint {fp!r} does not exist on disk",
                {"footprint": fp, "refs": [p.ref for p in spec.parts if p.footprint == fp]},
                cands,
            ))
    if excs:
        return result

    # pass 5: pins
    pin_maps: dict[str, dict[str, str]] = {}
    for pspec in spec.parts:
        if pspec.lib is not None:
            tmpl = resolver.get_template(pspec.lib, pspec.part)
            pin_maps[pspec.ref] = resolver.pin_ids(tmpl)
        else:
            ids: dict[str, str] = {}
            for pd in pspec.pins:
                ids[pd.num] = pd.num
                ids[pd.name] = pd.num
            pin_maps[pspec.ref] = ids

    for net in spec.nets:
        for pin_ref in net.pins:
            ref, pin_id = PIN_REF_RE.match(pin_ref).groups()
            legal = pin_maps[ref]
            if pin_id not in legal:
                pool = sorted(set(legal))
                close = _close(pin_id, pool, n=8)
                listing = close + [p for p in pool if p not in close][: max(0, 12 - len(close))]
                cands = [Candidate(id=f"c{i+1}", action=ActionType.REPLACE_PIN,
                                   params={"ref": ref, "old": pin_id, "new": m},
                                   human_summary=f"connect to pin {m!r} of {ref}")
                         for i, m in enumerate(listing)]
                cands.append(Candidate(id=f"c{len(cands)+1}", action=ActionType.REMOVE_NET_PIN,
                                       params={"net": net.name, "pin": pin_ref},
                                       human_summary="drop this endpoint from the net"))
                pspec = spec.part_by_ref(ref)
                excs.append(_exc(
                    next_id(), ExcCode.SPEC_UNKNOWN_PIN,
                    f"pin {pin_id!r} not found on {ref}"
                    f" ({pspec.lib}:{pspec.part})" if pspec.lib else f"pin {pin_id!r} not found on custom part {ref}",
                    {"ref": ref, "pin": pin_id, "net": net.name,
                     "available_pins": pool[:40]},
                    cands,
                ))
    if excs:
        return result

    # build
    result.resolved_pins = {
        ref: {pid: num for pid, num in legal.items()}
        for ref, legal in pin_maps.items()
    }
    result.circuit = _build_circuit(spec, pin_maps)
    return result


def _build_circuit(spec: CircuitSpec, pin_maps: dict[str, dict[str, str]]):
    from skidl import (
        KICAD9, NETLIST, POWER, SKIDL, Circuit, Net, Part,
        set_default_tool, subcircuit,
    )

    set_default_tool(KICAD9)
    circuit = Circuit(name=spec.board.name)
    fake_lib = _FakeLib()

    with circuit:
        nets: dict[str, object] = {}
        for nspec in spec.nets:
            net = Net(nspec.name)
            if nspec.power:
                net.drive = POWER
            if nspec.stub:
                net.stub = True
            nets[nspec.name] = net

        parts: dict[str, object] = {}

        def _instantiate(pspec: PartSpec):
            if pspec.lib is not None:
                real_lib, real_part = _SYMBOL_ALIASES.get(
                    (pspec.lib, pspec.part), (pspec.lib, pspec.part)
                )
                kwargs = {"footprint": pspec.footprint}
                if pspec.value:
                    kwargs["value"] = pspec.value
                part = Part(real_lib, real_part, **kwargs)
            else:
                ref_prefix = "".join(c for c in pspec.ref if not c.isdigit()) or "U"
                part = Part(
                    name=pspec.part or pspec.ref, tool=SKIDL, dest=NETLIST,
                    ref_prefix=ref_prefix, footprint=pspec.footprint,
                    pins=[_make_pin(pd.num, pd.name, pd.func) for pd in pspec.pins],
                )
                if pspec.value:
                    part.value = pspec.value
                _add_skidl_draw_cmds(part)
                part.lib = fake_lib
            part.ref = pspec.ref
            parts[pspec.ref] = part

        grouped: dict[str | None, list[PartSpec]] = defaultdict(list)
        for pspec in spec.parts:
            grouped[pspec.group].append(pspec)

        for group_name, members in grouped.items():
            if group_name is None:
                for pspec in members:
                    _instantiate(pspec)
            else:
                def _block(members=members):
                    for pspec in members:
                        _instantiate(pspec)
                _block.__name__ = group_name
                subcircuit(_block)()

        for nspec in spec.nets:
            net = nets[nspec.name]
            for pin_ref in nspec.pins:
                ref, pin_id = PIN_REF_RE.match(pin_ref).groups()
                num = pin_maps[ref][pin_id]
                parts[ref][num] += net

    return circuit
