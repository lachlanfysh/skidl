from __future__ import annotations

import subprocess
from dataclasses import dataclass, field

from .writer import PlacedPart


@dataclass
class ValidationResult:
    overlaps: list[tuple[str, str]] = field(default_factory=list)
    worst_hpwl_nets: list[tuple[str, float]] = field(default_factory=list)
    missing_refs: list[str] = field(default_factory=list)
    extra_refs: list[str] = field(default_factory=list)
    total_parts: int = 0
    placed_parts: int = 0

    @property
    def ok(self) -> bool:
        return not self.overlaps and not self.missing_refs

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
) -> ValidationResult:
    result = ValidationResult(placed_parts=len(placed_parts))

    result.overlaps = _check_overlaps(placed_parts, fp_bboxes, clearance_mm)

    if circuit is not None:
        result.total_parts = len(circuit.parts)
        circuit_refs = {getattr(p, "ref", None) for p in circuit.parts}
        placed_refs = {pp.ref for pp in placed_parts}
        result.missing_refs = sorted(circuit_refs - placed_refs - {None})
        result.extra_refs = sorted(placed_refs - circuit_refs)
        result.worst_hpwl_nets = _compute_hpwl(placed_parts, circuit)

    return result


def run_kicad_drc(pcb_path: str) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["kicad-cli", "pcb", "drc", "--output", pcb_path + ".drc.json", pcb_path],
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
