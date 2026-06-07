from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum


class ModelSource(Enum):
    BUILTIN_PRIMITIVE = "builtin_primitive"
    CONVERT_FOR_SPICE = "convert_for_spice"
    PYSPICE_ATTRIBUTE = "pyspice_attribute"
    SPICE_LIBRARY = "spice_library"


@dataclass
class ModelEntry:
    ref: str
    source: ModelSource
    spice_element: str
    description: str = ""
    spice_ready: bool = False


_RESISTOR_RE = re.compile(r"^R", re.IGNORECASE)
_CAPACITOR_RE = re.compile(r"^C", re.IGNORECASE)
_INDUCTOR_RE = re.compile(r"^L", re.IGNORECASE)

_RESISTOR_NAME_RE = re.compile(r"\b(resistor|res)\b", re.IGNORECASE)
_CAPACITOR_NAME_RE = re.compile(r"\b(capacitor|cap)\b", re.IGNORECASE)
_INDUCTOR_NAME_RE = re.compile(r"\b(inductor|ind|choke)\b", re.IGNORECASE)

_SOURCE_V_RE = re.compile(r"^V", re.IGNORECASE)
_SOURCE_I_RE = re.compile(r"^I", re.IGNORECASE)
_SOURCE_NAME_RE = re.compile(
    r"\b(voltage.source|current.source|v_dc|i_dc|battery)\b", re.IGNORECASE
)


def _is_resistor(part) -> bool:
    ref = getattr(part, "ref", "")
    name = getattr(part, "name", "") or ""
    if _RESISTOR_RE.match(ref):
        return True
    if _RESISTOR_NAME_RE.search(name):
        return True
    return False


def _is_capacitor(part) -> bool:
    ref = getattr(part, "ref", "")
    name = getattr(part, "name", "") or ""
    if _CAPACITOR_RE.match(ref):
        return True
    if _CAPACITOR_NAME_RE.search(name):
        return True
    return False


def _is_inductor(part) -> bool:
    ref = getattr(part, "ref", "")
    name = getattr(part, "name", "") or ""
    if _INDUCTOR_RE.match(ref):
        return True
    if _INDUCTOR_NAME_RE.search(name):
        return True
    return False


def _is_voltage_source(part) -> bool:
    ref = getattr(part, "ref", "")
    name = getattr(part, "name", "") or ""
    if _SOURCE_V_RE.match(ref) and len(getattr(part, "pins", [])) == 2:
        return True
    if _SOURCE_NAME_RE.search(name) and "voltage" in name.lower():
        return True
    return False


def _is_current_source(part) -> bool:
    ref = getattr(part, "ref", "")
    name = getattr(part, "name", "") or ""
    if _SOURCE_I_RE.match(ref) and len(getattr(part, "pins", [])) == 2:
        return True
    if _SOURCE_NAME_RE.search(name) and "current" in name.lower():
        return True
    return False


class ModelRegistry:
    """Maps parts to exact SPICE model entries.

    Only maps canonical primitives (R, C, L, ideal V/I sources) and parts
    that already have ``part.pyspice`` set via ``convert_for_spice()`` or
    SPICE library loading.  Active devices, ICs, regulators, and anything
    else require explicit user models — no guessing.
    """

    def __init__(self):
        self._entries: dict[str, ModelEntry] = {}

    def build(self, circuit) -> None:
        self._entries.clear()
        for part in circuit.parts:
            entry = self._classify(part)
            if entry is not None:
                self._entries[entry.ref] = entry

    def get(self, ref: str) -> ModelEntry | None:
        return self._entries.get(ref)

    def has_model(self, ref: str) -> bool:
        return ref in self._entries

    @property
    def entries(self) -> dict[str, ModelEntry]:
        return dict(self._entries)

    @property
    def mapped_refs(self) -> set[str]:
        return set(self._entries.keys())

    @property
    def unmapped_refs(self) -> set[str]:
        return set()

    def unmapped_refs_for(self, circuit) -> set[str]:
        all_refs = {p.ref for p in circuit.parts}
        return all_refs - self._entries.keys()

    def _classify(self, part) -> ModelEntry | None:
        ref = getattr(part, "ref", None)
        if ref is None:
            return None

        if hasattr(part, "pyspice") and part.pyspice:
            if hasattr(part, "reordered_part_pins"):
                return ModelEntry(
                    ref=ref,
                    source=ModelSource.CONVERT_FOR_SPICE,
                    spice_element=part.pyspice.get("name", "X"),
                    description="converted via convert_for_spice()",
                    spice_ready=True,
                )
            return ModelEntry(
                ref=ref,
                source=ModelSource.PYSPICE_ATTRIBUTE,
                spice_element=part.pyspice.get("name", "X"),
                description="SPICE model from library or pyspice attribute",
                spice_ready=True,
            )

        if _is_resistor(part):
            return ModelEntry(
                ref=ref,
                source=ModelSource.BUILTIN_PRIMITIVE,
                spice_element="R",
                description="ideal resistor",
            )
        if _is_capacitor(part):
            return ModelEntry(
                ref=ref,
                source=ModelSource.BUILTIN_PRIMITIVE,
                spice_element="C",
                description="ideal capacitor",
            )
        if _is_inductor(part):
            return ModelEntry(
                ref=ref,
                source=ModelSource.BUILTIN_PRIMITIVE,
                spice_element="L",
                description="ideal inductor",
            )
        if _is_voltage_source(part):
            return ModelEntry(
                ref=ref,
                source=ModelSource.BUILTIN_PRIMITIVE,
                spice_element="V",
                description="ideal voltage source",
            )
        if _is_current_source(part):
            return ModelEntry(
                ref=ref,
                source=ModelSource.BUILTIN_PRIMITIVE,
                spice_element="I",
                description="ideal current source",
            )

        return None
