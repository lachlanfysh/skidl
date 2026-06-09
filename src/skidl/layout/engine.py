from __future__ import annotations

from dataclasses import dataclass

from .candidates import (
    PlacementCandidate,
    copy_constraints,
    generate_placement_candidates,
)
from .constraints import BoardOutline, LayoutConstraints
from .context import LayoutContext
from .decaps import refine_candidate_decaps
from .geometry import FootprintGeometry, geometry_bboxes, load_footprint_geometries
from .hierarchy import PlacementGroup, extract_groups
from .intent import PlacementIntentPlan, infer_placement_intents
from .orientation import refine_candidate_orientations
from .placer import derive_outline, _footprint_name
from .power import PowerRoutePlan, infer_power_topology, plan_power_routes
from .reader import read_board_outline
from .refinement import refine_candidate_placement
from .report import PlacementReport, build_placement_report
from .routability import RoutabilityFeedback
from .scoring import LayoutScore, score_placement, score_placement_quick
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
    candidates: list[PlacementCandidate] | None = None
    intent_plan: PlacementIntentPlan | None = None
    report: PlacementReport | None = None
    fp_geometries: dict[str, FootprintGeometry] | None = None
    routability: RoutabilityFeedback | None = None

    @property
    def ok(self) -> bool:
        return self.validation.ok and self.score.ok

    def to_dict(self) -> dict:
        result = {
            "ok": self.ok,
            "score": self.score.to_dict(),
            "validation": {
                "ok": self.validation.ok,
                "overlaps": list(self.validation.overlaps),
                "outline_violations": list(self.validation.outline_violations),
                "keepout_violations": list(self.validation.keepout_violations),
                "missing_refs": list(self.validation.missing_refs),
                "total_parts": self.validation.total_parts,
                "placed_parts": self.validation.placed_parts,
            },
        }
        if self.report is not None:
            result["report"] = self.report.to_dict()
        if self.routability is not None:
            result["routability"] = self.routability.to_dict()
        if self.outline is not None:
            result["outline"] = {
                "width_mm": self.outline.width_mm,
                "height_mm": self.outline.height_mm,
            }
        return result

    def summary(self) -> str:
        lines = [
            self.validation.summary(),
            self.score.summary(),
            self.power_plan.summary(),
        ]
        if self.report is not None:
            lines.append(self.report.summary())
        if self.routability is not None:
            lines.append(self.routability.summary())
        if self.intent_plan is not None:
            lines.append(self.intent_plan.summary())
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
    copied = copy_constraints(constraints)
    copied.outline = outline
    return copied


def _footprint_names(circuit) -> set[str]:
    names = set()
    for part in circuit.parts:
        fp = _footprint_name(part)
        if fp:
            names.add(fp)
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


def _resolve_geometries(
    circuit,
    fp_lib_dirs: list[str] | None,
) -> dict[str, FootprintGeometry]:
    if fp_lib_dirs is None:
        return {}
    return load_footprint_geometries(_footprint_names(circuit), fp_lib_dirs)


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
    fp_geometries = _resolve_geometries(circuit, fp_lib_dirs)
    resolved_bboxes = _resolve_bboxes(circuit, fp_bboxes, fp_lib_dirs)
    geometry_boxes = geometry_bboxes(fp_geometries)
    if fp_bboxes is None:
        resolved_bboxes.update(geometry_boxes)
    else:
        for footprint, bbox in geometry_boxes.items():
            resolved_bboxes.setdefault(footprint, bbox)

    resolved_outline = _resolve_outline(constraints, outline, existing_pcb_path)
    resolved_constraints = _copy_constraints(constraints, resolved_outline)

    groups = extract_groups(circuit)
    intent_plan = infer_placement_intents(circuit, outline=resolved_outline)
    power_topology = infer_power_topology(circuit)
    candidates = generate_placement_candidates(
        groups,
        resolved_constraints,
        resolved_bboxes,
        intent_plan=intent_plan,
        power_topology=power_topology,
    )

    ctx = LayoutContext.from_circuit(circuit)

    candidate_scores: dict[str, LayoutScore] = {}
    candidate_validations: dict[str, ValidationResult] = {}
    for candidate in candidates:
        refine_candidate_orientations(candidate, circuit, fp_geometries)
        refine_candidate_decaps(
            candidate,
            circuit,
            fp_geometries,
            resolved_bboxes,
        )
        refine_candidate_placement(
            candidate,
            circuit,
            resolved_bboxes,
            fp_geometries=fp_geometries,
            clearance_mm=clearance_mm,
            board_layers=board_layers,
        )
        candidate_constraints = candidate.constraints or resolved_constraints
        candidate_validations[candidate.name] = validate(
            candidate.placed_parts,
            circuit,
            resolved_bboxes,
            clearance_mm=clearance_mm,
            outline=resolved_outline,
            keepouts=candidate_constraints.keepouts,
            fp_geometries=fp_geometries,
        )
        if not candidate_validations[candidate.name].ok:
            candidate_scores[candidate.name] = score_placement_quick(
                candidate.placed_parts,
                circuit,
                resolved_bboxes,
                outline=resolved_outline,
                keepouts=candidate_constraints.keepouts,
                fp_geometries=fp_geometries,
                clearance_mm=clearance_mm,
                ctx=ctx,
            )
        else:
            candidate_scores[candidate.name] = score_placement(
                candidate.placed_parts,
                circuit,
                resolved_bboxes,
                outline=resolved_outline,
                keepouts=candidate_constraints.keepouts,
                fp_geometries=fp_geometries,
                clearance_mm=clearance_mm,
                board_layers=board_layers,
                ctx=ctx,
            )
        candidate.score = candidate_scores[candidate.name].score

    any_valid = any(
        candidate_validations[c.name].ok for c in candidates
    )
    if not any_valid:
        for candidate in candidates:
            candidate_constraints = candidate.constraints or resolved_constraints
            candidate_scores[candidate.name] = score_placement(
                candidate.placed_parts,
                circuit,
                resolved_bboxes,
                outline=resolved_outline,
                keepouts=candidate_constraints.keepouts,
                fp_geometries=fp_geometries,
                clearance_mm=clearance_mm,
                board_layers=board_layers,
                ctx=ctx,
            )
            candidate.score = candidate_scores[candidate.name].score

    selected_candidate = max(
        candidates,
        key=lambda candidate: (
            1 if candidate_scores.get(candidate.name, None) is not None
            and candidate_scores[candidate.name].ok else 0,
            candidate.score if candidate.score is not None else 0.0,
            candidate.name,
        ),
    )
    placed_parts = selected_candidate.placed_parts
    selected_constraints = selected_candidate.constraints or resolved_constraints

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
        keepouts=selected_constraints.keepouts,
        fp_geometries=fp_geometries,
    )
    score = score_placement(
        placed_parts,
        circuit,
        resolved_bboxes,
        outline=resolved_outline,
        keepouts=selected_constraints.keepouts,
        fp_geometries=fp_geometries,
        clearance_mm=clearance_mm,
        board_layers=board_layers,
        ctx=ctx,
    )
    selected_candidate.score = score.score
    power_plan = plan_power_routes(
        circuit,
        placed_parts,
        board_layers=board_layers,
    )
    candidate_validations[selected_candidate.name] = validation
    candidate_scores[selected_candidate.name] = score
    report = build_placement_report(
        selected_candidate,
        candidate_scores,
        candidate_validations,
        power_plan,
    )

    return LayoutResult(
        placed_parts=placed_parts,
        outline=resolved_outline,
        validation=validation,
        score=score,
        power_plan=power_plan,
        groups=groups,
        fp_bboxes=resolved_bboxes,
        candidates=candidates,
        intent_plan=intent_plan,
        report=report,
        fp_geometries=fp_geometries,
    )
