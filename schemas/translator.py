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
from collections import defaultdict
from dataclasses import dataclass, field

from .circuit_spec import PIN_FUNCS, PIN_REF_RE, CircuitSpec, PartSpec
from .exceptions import ActionType, Candidate, DesignException, ExcCode, Severity

DEFAULT_SYM_DIR = "/usr/share/kicad/symbols"
DEFAULT_FP_DIR = "/usr/share/kicad/footprints"


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
        schlib = self.get_lib(lib)
        tmpl = None
        if schlib is not None:
            found = schlib.get_parts_by_name(part, allow_failure=True, partial_parse=False)
            if found:
                tmpl = found[0] if isinstance(found, list) else found
        self._templates[key] = tmpl
        return tmpl

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
            cands = [Candidate(id=f"c{i+1}", action=ActionType.REPLACE_LIB,
                               params={"ref": "*", "old": lib, "new": m},
                               human_summary=f"use library {m!r} instead of {lib!r}")
                     for i, m in enumerate(_close(lib, resolver.lib_names()))]
            excs.append(_exc(
                next_id(), ExcCode.SPEC_UNKNOWN_LIB,
                f"KiCad symbol library {lib!r} does not exist",
                {"lib": lib, "refs": [p.ref for p in lib_parts if p.lib == lib]},
                cands,
            ))
    if excs:
        return result

    # pass 3: parts within libraries
    bad_parts: set[tuple[str, str]] = set()
    for pspec in lib_parts:
        if resolver.get_template(pspec.lib, pspec.part) is None:
            bad_parts.add((pspec.lib, pspec.part))
            cands = [Candidate(id=f"c{i+1}", action=ActionType.REPLACE_PART,
                               params={"ref": pspec.ref, "new": m},
                               human_summary=f"use symbol {m!r} from {pspec.lib}")
                     for i, m in enumerate(_close(pspec.part, resolver.part_names(pspec.lib)))]
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

    # pass 4: footprints
    for fp in sorted({p.footprint for p in spec.parts}):
        if not _footprint_exists(fp, fp_dirs):
            cands = [Candidate(id=f"c{i+1}", action=ActionType.REPLACE_FOOTPRINT,
                               params={"old": fp, "new": m},
                               human_summary=f"use footprint {m!r}")
                     for i, m in enumerate(_footprint_candidates(fp, fp_dirs))]
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
                kwargs = {"footprint": pspec.footprint}
                if pspec.value:
                    kwargs["value"] = pspec.value
                part = Part(pspec.lib, pspec.part, **kwargs)
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
