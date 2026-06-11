"""JSON circuit specification — the rigid input format for generate_design.

A CircuitSpec fully describes a board: parts (KiCad library symbols or custom
pin-defined parts), nets connecting "REF.PIN" endpoints, and board metadata.
It is deterministic data, never code. The translator (schemas/translator.py)
converts it into a SKiDL Circuit, reporting every problem as a structured
DesignException with resolution candidates.
"""

from __future__ import annotations

import re
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

# Maps spec pin function names to skidl Pin.types attribute names.
PIN_FUNCS = {
    "power_in": "PWRIN",
    "power_out": "PWROUT",
    "input": "INPUT",
    "output": "OUTPUT",
    "bidirectional": "BIDIR",
    "tristate": "TRISTATE",
    "passive": "PASSIVE",
    "unspecified": "UNSPEC",
    "no_connect": "NOCONNECT",
}

PIN_REF_RE = re.compile(r"^([A-Za-z][A-Za-z0-9_]*)\.(.+)$")


class PinDef(BaseModel):
    """Pin definition for a custom (non-library) part."""

    num: str = Field(description="Pin number as printed on the package, e.g. '1' or 'A3'")
    name: str = Field(description="Pin name, e.g. 'VCC', 'SDA'")
    func: str = Field(default="passive", description=f"Electrical function, one of: {', '.join(PIN_FUNCS)}")

    @field_validator("func")
    @classmethod
    def _func_known(cls, v: str) -> str:
        if v not in PIN_FUNCS:
            raise ValueError(f"func must be one of {sorted(PIN_FUNCS)}, got {v!r}")
        return v


class PartSpec(BaseModel):
    """One component. Either a KiCad library symbol (lib+part set) or a
    custom part (lib=None, pins required)."""

    ref: str = Field(description="Unique reference designator, e.g. 'U1', 'C3'")
    lib: Optional[str] = Field(default=None, description="KiCad symbol library name, e.g. 'Device'. None => custom part defined by pins")
    part: Optional[str] = Field(default=None, description="Symbol name within lib, e.g. 'R'. Required when lib is set")
    value: Optional[str] = Field(default=None, description="Component value, e.g. '10K', '100nF'")
    footprint: str = Field(description="KiCad footprint as 'Library:Name', e.g. 'Resistor_SMD:R_0603_1608Metric'")
    pins: Optional[list[PinDef]] = Field(default=None, description="Explicit pin definitions — required for custom parts (lib=None), forbidden for library parts")
    group: Optional[str] = Field(default=None, description="Functional block name; parts sharing a group are placed together and get their own schematic sheet")

    @model_validator(mode="after")
    def _lib_xor_pins(self):
        if self.lib is not None:
            if not self.part:
                raise ValueError(f"part {self.ref}: 'part' is required when 'lib' is set")
            if self.pins:
                raise ValueError(f"part {self.ref}: 'pins' is only allowed for custom parts (lib=null)")
        else:
            if not self.pins:
                raise ValueError(f"part {self.ref}: custom parts (lib=null) require 'pins'")
        return self


class NetSpec(BaseModel):
    """One electrical net and the pins it connects."""

    name: str = Field(description="Net name, e.g. 'VCC', 'GND', 'SDA'. Use standard power names for rails")
    power: bool = Field(default=False, description="True for power/ground rails — sets drive strength and enables power-net handling")
    stub: bool = Field(default=False, description="True to render as named stubs/labels in the schematic instead of routed wires")
    pins: list[str] = Field(description="Endpoints as 'REF.PIN' where PIN is a pin number or pin name, e.g. ['U1.VDD', 'C1.1']")

    @field_validator("pins")
    @classmethod
    def _pin_format(cls, v: list[str]) -> list[str]:
        for p in v:
            if not PIN_REF_RE.match(p):
                raise ValueError(f"net pin {p!r} must be 'REF.PIN' format")
        return v


class BoardSpec(BaseModel):
    """Board-level metadata and physical hints."""

    name: str = Field(description="Board name — used for output file naming")
    form_factor: Optional[str] = Field(default=None, description="Standard form factor: feather, qt_py, metro, metro_mini, trinket, itsybitsy, shield_uno. Fixes the board outline")
    outline_hint_mm: Optional[tuple[float, float]] = Field(default=None, description="(width_mm, height_mm) outline hint when no form_factor applies")
    layers: int = Field(default=2, description="Copper layer count (2 or 4)")

    @field_validator("form_factor")
    @classmethod
    def _known_form_factor(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        known = _known_form_factors()
        if v not in known:
            raise ValueError(
                f"unknown form_factor {v!r} — valid values: {', '.join(known)}. "
                f"For a custom board size, omit form_factor and set "
                f"outline_hint_mm: [width_mm, height_mm] instead"
            )
        return v


def _known_form_factors() -> list[str]:
    """Authoritative list lives in the layout engine; fall back to a static
    copy when skidl isn't importable (lightweight schema-only contexts)."""
    try:
        from skidl.layout.constraints import FORM_FACTORS
        return sorted(FORM_FACTORS)
    except Exception:
        return ["feather", "itsybitsy", "metro", "metro_mini",
                "qt_py", "shield_uno", "trinket"]


class CircuitSpec(BaseModel):
    """Complete board specification — the input to generate_design."""

    schema_version: Literal["1"] = Field(default="1", description="Spec schema version")
    board: BoardSpec
    parts: list[PartSpec] = Field(description="All components on the board")
    nets: list[NetSpec] = Field(description="All electrical connections")
    waivers: list[str] = Field(default_factory=list, description="Exception waiver keys ('CODE:subject') accepted as advisory — set via accept_advisory corrections")

    @model_validator(mode="after")
    def _unique_refs(self):
        seen: set[str] = set()
        dupes = [p.ref for p in self.parts if p.ref in seen or seen.add(p.ref)]
        if dupes:
            raise ValueError(f"duplicate part refs: {sorted(set(dupes))}")
        return self

    def part_by_ref(self, ref: str) -> Optional[PartSpec]:
        for p in self.parts:
            if p.ref == ref:
                return p
        return None
