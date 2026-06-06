from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass, field

from .writer import PlacedPart


_MACOS_KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"


@dataclass
class ValidationResult:
    overlaps: list[tuple[str, str]] = field(default_factory=list)
    outline_violations: list[str] = field(default_factory=list)
    worst_hpwl_nets: list[tuple[str, float]] = field(default_factory=list)
    missing_refs: list[str] = field(default_factory=list)
    extra_refs: list[str] = field(default_factory=list)
    total_parts: int = 0
    placed_parts: int = 0

    @property
    def ok(self) -> bool:
        return (
            not self.overlaps
            and not self.missing_refs
            and not self.outline_violations
        )

    def summary(self) -> str:
        lines = []
        lines.append(f"Parts: {self.placed_parts}/{self.total_parts} placed")
        if self.missing_refs:
            lines.append(f"MISSING: {', '.join(self.missing_refs[:20])}")
        if self.overlaps:
            lines.append(f"OVERLAPS ({len(self.overlaps)}):")
            for a, b in self.overlaps[:20]:
                lines.append(f"  {a} ↔ {b}")
        else:
            lines.append("No overlaps")
        if self.outline_violations:
            lines.append(f"OUTSIDE OUTLINE ({len(self.outline_violations)}):")
            for ref in self.outline_violations[:20]:
                lines.append(f"  {ref}")
        if self.worst_hpwl_nets:
            lines.append("Worst HPWL nets:")
            for name, hpwl in self.worst_hpwl_nets[:10]:
                lines.append(f"  {name}: {hpwl:.1f}mm")
        return "\n".join(lines)


def _check_overlaps(
    placed: list[PlacedPart],
    fp_bboxes: dict[str, tuple[float, float]],
    clearance_mm: float,
) -> list[tuple[str, str]]:
    overlaps = []
    for i, a in enumerate(placed):
        wa, ha = fp_bboxes.get(a.footprint, (2.0, 2.0))
        for b in placed[i + 1:]:
            wb, hb = fp_bboxes.get(b.footprint, (2.0, 2.0))
            if (abs(a.x_mm - b.x_mm) < (wa + wb) / 2 + clearance_mm and
                    abs(a.y_mm - b.y_mm) < (ha + hb) / 2 + clearance_mm):
                overlaps.append((a.ref, b.ref))
    return overlaps


def _check_outline_violations(
    placed: list[PlacedPart],
    fp_bboxes: dict[str, tuple[float, float]],
    outline,
) -> list[str]:
    if outline is None:
        return []

    violations = []
    for pp in placed:
        w, h = fp_bboxes.get(pp.footprint, (2.0, 2.0))
        half_w, half_h = w / 2, h / 2
        if (
            pp.x_mm - half_w < outline.x_min
            or pp.y_mm - half_h < outline.y_min
            or pp.x_mm + half_w > outline.x_max
            or pp.y_mm + half_h > outline.y_max
        ):
            violations.append(pp.ref)
    return violations


def _compute_hpwl(
    placed: list[PlacedPart],
    circuit,
) -> list[tuple[str, float]]:
    from skidl.net import NCNet

    pos_by_ref = {pp.ref: (pp.x_mm, pp.y_mm) for pp in placed}
    net_hpwl: list[tuple[str, float]] = []

    for net in circuit.get_nets():
        if isinstance(net, NCNet):
            continue
        xs, ys = [], []
        for pin in net.get_pins():
            ref = getattr(getattr(pin, "part", None), "ref", None)
            if ref and ref in pos_by_ref:
                x, y = pos_by_ref[ref]
                xs.append(x)
                ys.append(y)
        if len(xs) < 2:
            continue
        hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
        net_hpwl.append((net.name, hpwl))

    net_hpwl.sort(key=lambda t: t[1], reverse=True)
    return net_hpwl[:10]


def validate(
    placed_parts: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    clearance_mm: float = 0.5,
    outline=None,
) -> ValidationResult:
    result = ValidationResult(placed_parts=len(placed_parts))

    result.overlaps = _check_overlaps(placed_parts, fp_bboxes, clearance_mm)
    result.outline_violations = _check_outline_violations(
        placed_parts, fp_bboxes, outline
    )

    if circuit is not None:
        result.total_parts = len(circuit.parts)
        circuit_refs = {getattr(p, "ref", None) for p in circuit.parts}
        placed_refs = {pp.ref for pp in placed_parts}
        result.missing_refs = sorted(circuit_refs - placed_refs - {None})
        result.extra_refs = sorted(placed_refs - circuit_refs)
        result.worst_hpwl_nets = _compute_hpwl(placed_parts, circuit)

    return result


def find_kicad_cli() -> str | None:
    return shutil.which("kicad-cli") or (
        _MACOS_KICAD_CLI if os.path.isfile(_MACOS_KICAD_CLI) else None
    )


def run_kicad_drc(pcb_path: str) -> tuple[bool, str]:
    kicad_cli = find_kicad_cli()
    if kicad_cli is None:
        return True, "kicad-cli not available"

    try:
        result = subprocess.run(
            [kicad_cli, "pcb", "drc", "--output", pcb_path + ".drc.json", pcb_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        report = result.stdout + result.stderr
        passed = result.returncode == 0
        return passed, report
    except FileNotFoundError:
        return True, "kicad-cli not available"
    except subprocess.TimeoutExpired:
        return False, "DRC timed out after 60s"
