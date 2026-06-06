from __future__ import annotations

from dataclasses import dataclass

from .constraints import BoardOutline, LayoutConstraints
from .hierarchy import PlacementGroup, extract_groups
from .placer import derive_outline, place_parts
from .power import PowerRoutePlan, plan_power_routes
from .reader import read_board_outline
from .scoring import LayoutScore, score_placement
from .validator import ValidationResult, validate
from .writer import PlacedPart, load_footprint_bboxes


@dataclass
class LayoutResult:
    placed_parts: list[PlacedPart]
    outline: BoardOutline | None
    validation: ValidationResult
    score: LayoutScore
    power_plan: PowerRoutePlan
    groups: dict[int | None, PlacementGroup]
    fp_bboxes: dict[str, tuple[float, float]]

    @property
    def ok(self) -> bool:
        return self.validation.ok and self.score.ok

    def summary(self) -> str:
        lines = [
            self.validation.summary(),
            self.score.summary(),
            self.power_plan.summary(),
        ]
        if self.outline is not None:
            lines.insert(
                0,
                (
                    f"Outline: {self.outline.width_mm:.1f}mm x "
                    f"{self.outline.height_mm:.1f}mm"
                ),
            )
        return "\n\n".join(lines)


def _copy_constraints(
    constraints: LayoutConstraints | None,
    outline: BoardOutline | None,
) -> LayoutConstraints:
    constraints = constraints or LayoutConstraints()
    return LayoutConstraints(
        fixed=list(constraints.fixed or []),
        zones=list(constraints.zones or []),
        keepouts=list(constraints.keepouts or []),
        outline=outline,
    )


def _footprint_names(circuit) -> set[str]:
    names = set()
    for part in circuit.parts:
        foot = getattr(part, "foot", None) or getattr(part, "footprint", None)
        if foot:
            names.add(foot)
    return names


def _resolve_bboxes(
    circuit,
    fp_bboxes: dict[str, tuple[float, float]] | None,
    fp_lib_dirs: list[str] | None,
) -> dict[str, tuple[float, float]]:
    if fp_bboxes is not None:
        return dict(fp_bboxes)
    if fp_lib_dirs is None:
        return {}
    return load_footprint_bboxes(_footprint_names(circuit), fp_lib_dirs)


def _resolve_outline(
    constraints: LayoutConstraints | None,
    outline: BoardOutline | None,
    existing_pcb_path: str | None,
) -> BoardOutline | None:
    if outline is not None:
        return outline
    if constraints is not None and constraints.outline is not None:
        return constraints.outline
    if existing_pcb_path is not None:
        return read_board_outline(existing_pcb_path)
    return None


def plan_layout(
    circuit,
    fp_bboxes: dict[str, tuple[float, float]] | None = None,
    fp_lib_dirs: list[str] | None = None,
    constraints: LayoutConstraints | None = None,
    outline: BoardOutline | None = None,
    existing_pcb_path: str | None = None,
    board_layers: int = 2,
    margin_mm: float = 3.0,
    clearance_mm: float = 0.5,
    derive_outline_if_missing: bool = True,
) -> LayoutResult:
    """Place and score a board attempt without writing copper geometry."""
    resolved_bboxes = _resolve_bboxes(circuit, fp_bboxes, fp_lib_dirs)
    resolved_outline = _resolve_outline(constraints, outline, existing_pcb_path)
    resolved_constraints = _copy_constraints(constraints, resolved_outline)

    groups = extract_groups(circuit)
    placed_parts = place_parts(groups, resolved_constraints, resolved_bboxes)

    if resolved_outline is None and derive_outline_if_missing:
        resolved_outline = derive_outline(
            placed_parts,
            resolved_bboxes,
            margin_mm=margin_mm,
        )

    validation = validate(
        placed_parts,
        circuit,
        resolved_bboxes,
        clearance_mm=clearance_mm,
        outline=resolved_outline,
    )
    score = score_placement(
        placed_parts,
        circuit,
        resolved_bboxes,
        outline=resolved_outline,
        clearance_mm=clearance_mm,
        board_layers=board_layers,
    )
    power_plan = plan_power_routes(
        circuit,
        placed_parts,
        board_layers=board_layers,
    )

    return LayoutResult(
        placed_parts=placed_parts,
        outline=resolved_outline,
        validation=validation,
        score=score,
        power_plan=power_plan,
        groups=groups,
        fp_bboxes=resolved_bboxes,
    )
