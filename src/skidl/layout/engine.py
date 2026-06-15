from __future__ import annotations

import math
import re
from dataclasses import dataclass, replace

from .candidates import (
    PlacementCandidate,
    copy_constraints,
    generate_placement_candidates,
)
from .constraints import BoardOutline, EdgeAnchor, LayoutConstraints
from .context import LayoutContext
from .decaps import refine_candidate_decaps
from .geometry import FootprintGeometry, geometry_bboxes, load_footprint_geometries
from .hierarchy import PlacementGroup, extract_groups
from .intent import PlacementIntentPlan, infer_placement_intents
from .orientation import refine_candidate_orientations
from .placer import (
    derive_outline,
    derive_outline_from_circuit,
    _edge_anchor_origin_position,
    _footprint_name,
)
from .power import PowerRoutePlan, infer_power_topology, plan_power_routes
from .reader import read_board_outline
from .refinement import refine_candidate_placement
from .report import PlacementReport, build_placement_report
from .roles import GND_NET_RE, POWER_NET_RE, classify_parts
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
            "placed_parts": [
                {
                    "ref": placed.ref,
                    "x_mm": placed.x_mm,
                    "y_mm": placed.y_mm,
                    "rot_deg": placed.rot_deg,
                    "footprint": placed.footprint,
                    "side": getattr(placed, "side", "front"),
                }
                for placed in self.placed_parts
            ],
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
        if self.intent_plan is not None:
            result["intent_plan"] = self.intent_plan.to_dict()
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


def _auto_outline_from_circuit(
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    form_factor: str | None,
) -> BoardOutline:
    if form_factor:
        return derive_outline([], fp_bboxes, form_factor=form_factor)
    return derive_outline_from_circuit(circuit, fp_bboxes)


def _placed_bounds(
    placed: PlacedPart,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None = None,
) -> tuple[float, float, float, float]:
    geometry = (fp_geometries or {}).get(placed.footprint)
    if geometry is not None:
        return geometry.transformed_bounds(placed)
    width, height = fp_bboxes.get(placed.footprint, (2.0, 2.0))
    if placed.rot_deg % 180 == 90:
        width, height = height, width
    return (
        placed.x_mm - width / 2,
        placed.y_mm - height / 2,
        placed.x_mm + width / 2,
        placed.y_mm + height / 2,
    )


def _edge_anchor_map(
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None = None,
):
    anchors = {
        anchor.ref: anchor
        for anchor in ((intent_plan.edge_anchors if intent_plan else []) or [])
    }
    for anchor in ((constraints.edge_anchors if constraints else []) or []):
        anchors[anchor.ref] = anchor
    return anchors


def _edge_parallel(edge: str, bounds: tuple[float, float, float, float]) -> bool:
    width = bounds[2] - bounds[0]
    height = bounds[3] - bounds[1]
    if edge in {"top", "bottom"}:
        return width + 0.2 >= height
    if edge in {"left", "right"}:
        return height + 0.2 >= width
    return True


def _edge_distance(
    edge: str,
    bounds: tuple[float, float, float, float],
    outline: BoardOutline,
    inset_mm: float,
) -> float | None:
    if edge == "top":
        return abs(bounds[1] - (outline.y_min + inset_mm))
    if edge == "bottom":
        return abs(bounds[3] - (outline.y_max - inset_mm))
    if edge == "left":
        return abs(bounds[0] - (outline.x_min + inset_mm))
    if edge == "right":
        return abs(bounds[2] - (outline.x_max - inset_mm))
    return None


def _apply_edge_intent_score(
    score: LayoutScore,
    placed_parts: list[PlacedPart],
    fp_bboxes: dict[str, tuple[float, float]],
    outline: BoardOutline | None,
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None = None,
    fp_geometries: dict[str, FootprintGeometry] | None = None,
) -> LayoutScore:
    """Treat violated edge-mating intent as product risk, not decoration."""
    if outline is None:
        return score

    anchors = _edge_anchor_map(intent_plan, constraints)
    if not anchors:
        return score

    placed_by_ref = {placed.ref: placed for placed in placed_parts}
    warnings = list(score.warnings)
    penalty = 0.0
    for ref, anchor in sorted(anchors.items()):
        placed = placed_by_ref.get(ref)
        if placed is None:
            continue
        edge = anchor.edge.lower()
        bounds = _placed_bounds(placed, fp_bboxes, fp_geometries)
        distance = _edge_distance(edge, bounds, outline, anchor.inset_mm)
        if distance is not None and distance > 1.0:
            warnings.append(
                f"{ref}: violates {edge}-edge mating intent "
                f"by {distance:.1f}mm"
            )
            penalty += 30.0 + min(distance * 2.0, 20.0)
        if not _edge_parallel(edge, bounds):
            warnings.append(
                f"{ref}: connector row is not parallel to the {edge} edge"
            )
            penalty += 30.0

    if penalty <= 0.0:
        return score
    return replace(
        score,
        score=max(0.0, score.score - penalty),
        warning_count=len(warnings),
        warnings=warnings,
    )


def _derive_outline_for_edge_anchors(
    placed_parts: list[PlacedPart],
    fp_bboxes: dict[str, tuple[float, float]],
    *,
    margin_mm: float,
    form_factor: str | None,
    min_area_mm2: float,
    max_min_area_growth: float,
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None,
    fp_geometries: dict[str, FootprintGeometry] | None,
) -> BoardOutline:
    anchors = _edge_anchor_map(intent_plan, constraints)
    effective_margin_mm = margin_mm
    if anchors:
        effective_margin_mm = min(margin_mm, 1.5)

    outline = derive_outline(
        placed_parts,
        fp_bboxes,
        margin_mm=effective_margin_mm,
        form_factor=form_factor,
        min_area_mm2=min_area_mm2,
        max_min_area_growth=max_min_area_growth,
    )
    if form_factor or not placed_parts:
        return outline

    placed_by_ref = {placed.ref: placed for placed in placed_parts}
    bounds_by_ref = {
        placed.ref: _placed_bounds(placed, fp_bboxes, fp_geometries)
        for placed in placed_parts
    }
    edge_refs = {
        edge: {ref for ref, anchor in anchors.items() if anchor.edge.lower() == edge}
        for edge in ("top", "bottom", "left", "right")
    }
    if not any(edge_refs.values()):
        return outline

    pin_access_refs = {
        intent.ref
        for intent in (intent_plan.mating_intents if intent_plan else [])
        if intent.kind in {"header", "generic_connector"}
        and intent.mating_side == "pin_access"
    }

    x_min = outline.x_min
    y_min = outline.y_min
    x_max = outline.x_max
    y_max = outline.y_max

    top_refs = [
        ref for ref in edge_refs["top"] if ref in bounds_by_ref and ref in anchors
    ]
    if top_refs:
        desired = min(
            bounds_by_ref[ref][1] - anchors[ref].inset_mm
            for ref in top_refs
        )
        y_min = min(y_min, desired)

    bottom_refs = [
        ref for ref in edge_refs["bottom"] if ref in bounds_by_ref and ref in anchors
    ]
    if bottom_refs:
        desired = max(
            bounds_by_ref[ref][3] + anchors[ref].inset_mm
            for ref in bottom_refs
        )
        y_max = max(y_max, desired)

    left_refs = [
        ref for ref in edge_refs["left"] if ref in bounds_by_ref and ref in anchors
    ]
    if left_refs:
        desired = min(
            bounds_by_ref[ref][0] - anchors[ref].inset_mm
            for ref in left_refs
        )
        x_min = min(x_min, desired)

    right_refs = [
        ref for ref in edge_refs["right"] if ref in bounds_by_ref and ref in anchors
    ]
    if right_refs:
        desired = max(
            bounds_by_ref[ref][2] + anchors[ref].inset_mm
            for ref in right_refs
        )
        x_max = max(x_max, desired)

    def _center_limits(low: float, high: float, center: float) -> tuple[float, float]:
        half_span = max(center - low, high - center)
        return center - half_span, center + half_span

    horizontal_refs = top_refs + bottom_refs
    if len(horizontal_refs) == 1 and horizontal_refs[0] in pin_access_refs:
        bounds = bounds_by_ref[horizontal_refs[0]]
        center = (bounds[0] + bounds[2]) / 2
        x_min, x_max = _center_limits(x_min, x_max, center)

    vertical_refs = left_refs + right_refs
    if vertical_refs:
        centers = [
            (bounds_by_ref[ref][1] + bounds_by_ref[ref][3]) / 2
            for ref in vertical_refs
        ]
        if max(centers) - min(centers) <= 0.2:
            y_min, y_max = _center_limits(
                y_min,
                y_max,
                sum(centers) / len(centers),
            )

    if x_max <= x_min or y_max <= y_min:
        return outline
    return BoardOutline(
        vertices=[
            (x_min, y_min),
            (x_max, y_min),
            (x_max, y_max),
            (x_min, y_max),
        ],
        corner_radius_mm=getattr(outline, "corner_radius_mm", 0.0),
    )


def _snap_mounting_holes_to_outline_corners(
    placed_parts: list[PlacedPart],
    outline: BoardOutline | None,
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None,
) -> tuple[list[PlacedPart], list[str]]:
    if outline is None or intent_plan is None:
        return placed_parts, []

    mounting_refs = set(intent_plan.refs_with_kind("mounting_hole"))
    if not mounting_refs:
        return placed_parts, []

    explicit_floorplan_refs = _constraint_floorplan_refs(constraints)
    center_x = (outline.x_min + outline.x_max) / 2
    center_y = (outline.y_min + outline.y_max) / 2
    moved: list[str] = []
    snapped: list[PlacedPart] = []
    clearance = 0.8

    for placed in placed_parts:
        if placed.ref not in mounting_refs or placed.ref in explicit_floorplan_refs:
            snapped.append(placed)
            continue

        bounds = _placed_bounds(placed, fp_bboxes, fp_geometries)
        left_margin = max(0.0, placed.x_mm - bounds[0])
        right_margin = max(0.0, bounds[2] - placed.x_mm)
        top_margin = max(0.0, placed.y_mm - bounds[1])
        bottom_margin = max(0.0, bounds[3] - placed.y_mm)

        if placed.x_mm <= center_x:
            x_mm = outline.x_min + left_margin + clearance
        else:
            x_mm = outline.x_max - right_margin - clearance

        if placed.y_mm <= center_y:
            y_mm = outline.y_min + top_margin + clearance
        else:
            y_mm = outline.y_max - bottom_margin - clearance

        if abs(x_mm - placed.x_mm) > 1e-6 or abs(y_mm - placed.y_mm) > 1e-6:
            moved.append(placed.ref)
            snapped.append(
                PlacedPart(
                    ref=placed.ref,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    rot_deg=placed.rot_deg,
                    footprint=placed.footprint,
                    side=getattr(placed, "side", "front"),
                )
            )
        else:
            snapped.append(placed)

    return snapped, moved


def _snap_edge_anchors_to_outline(
    placed_parts: list[PlacedPart],
    outline: BoardOutline | None,
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None,
) -> tuple[list[PlacedPart], list[str]]:
    if outline is None:
        return placed_parts, []

    anchors = _edge_anchor_map(intent_plan, constraints)
    if not anchors:
        return placed_parts, []

    fixed_refs = {
        fixed.ref for fixed in (constraints.fixed if constraints else []) or []
    }
    pin_access_refs = {
        intent.ref
        for intent in (intent_plan.mating_intents if intent_plan else [])
        if intent.kind in {"header", "generic_connector"}
        and intent.mating_side == "pin_access"
    }
    edge_refs = {
        edge: [ref for ref, anchor in anchors.items() if anchor.edge.lower() == edge]
        for edge in ("top", "bottom", "left", "right")
    }
    vertical_pair_refs = edge_refs["left"] + edge_refs["right"]
    align_vertical_pair = (
        len(edge_refs["left"]) == 1
        and len(edge_refs["right"]) == 1
        and set(vertical_pair_refs).issubset(pin_access_refs)
    )

    moved: list[str] = []
    snapped: list[PlacedPart] = []
    for placed in placed_parts:
        anchor = anchors.get(placed.ref)
        if anchor is None or placed.ref in fixed_refs:
            snapped.append(placed)
            continue

        width, height = fp_bboxes.get(placed.footprint, (2.0, 2.0))
        geometry = (fp_geometries or {}).get(placed.footprint)
        edge = anchor.edge.lower()
        offset = anchor.offset_mm
        if align_vertical_pair and edge in {"left", "right"}:
            offset = (outline.y_min + outline.y_max) / 2
        elif placed.ref in pin_access_refs and edge in {"top", "bottom"}:
            refs = edge_refs["top"] + edge_refs["bottom"]
            if len(refs) == 1:
                offset = (outline.x_min + outline.x_max) / 2

        final_anchor = EdgeAnchor(
            ref=anchor.ref,
            edge=anchor.edge,
            offset_mm=offset,
            inset_mm=anchor.inset_mm,
            rot_deg=anchor.rot_deg,
        )
        x_mm, y_mm, rot_deg, *_ = _edge_anchor_position_avoiding_keepouts(
            final_anchor,
            width,
            height,
            outline,
            geometry=geometry,
            ref=placed.ref,
            footprint=placed.footprint,
            keepouts=(constraints.keepouts if constraints else []) or [],
        )
        if (
            abs(x_mm - placed.x_mm) > 1e-6
            or abs(y_mm - placed.y_mm) > 1e-6
            or abs(rot_deg - placed.rot_deg) > 1e-6
        ):
            moved.append(placed.ref)
            snapped.append(
                PlacedPart(
                    ref=placed.ref,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    rot_deg=rot_deg,
                    footprint=placed.footprint,
                    side=getattr(placed, "side", "front"),
                )
            )
        else:
            snapped.append(placed)

    return snapped, moved


def _bounds_touch_keepout(bounds: tuple[float, float, float, float], keepout) -> bool:
    return not (
        bounds[2] <= keepout.x_min
        or bounds[0] >= keepout.x_max
        or bounds[3] <= keepout.y_min
        or bounds[1] >= keepout.y_max
    )


def _edge_anchor_position_avoiding_keepouts(
    anchor: EdgeAnchor,
    width: float,
    height: float,
    outline: BoardOutline,
    *,
    geometry: FootprintGeometry | None,
    ref: str,
    footprint: str,
    keepouts: list | None,
    clearance_mm: float = 0.5,
) -> tuple[float, float, float, float, float, float, float]:
    original = _edge_anchor_origin_position(
        anchor,
        width,
        height,
        outline,
        geometry=geometry,
        ref=ref,
        footprint=footprint,
    )
    if not keepouts:
        return original

    def _bounds(candidate):
        _, _, _, center_x, center_y, ew, eh = candidate
        return (
            center_x - ew / 2,
            center_y - eh / 2,
            center_x + ew / 2,
            center_y + eh / 2,
        )

    if not any(_bounds_touch_keepout(_bounds(original), ko) for ko in keepouts):
        return original

    edge = anchor.edge.lower()
    offsets: list[float] = []
    if anchor.offset_mm is not None:
        offsets.append(anchor.offset_mm)
    _, _, _, _, _, original_w, original_h = original
    if edge in {"left", "right"}:
        for ko in keepouts:
            offsets.extend((
                ko.y_min - original_h / 2 - clearance_mm,
                ko.y_max + original_h / 2 + clearance_mm,
            ))
        offsets.append((outline.y_min + outline.y_max) / 2)
    elif edge in {"top", "bottom"}:
        for ko in keepouts:
            offsets.extend((
                ko.x_min - original_w / 2 - clearance_mm,
                ko.x_max + original_w / 2 + clearance_mm,
            ))
        offsets.append((outline.x_min + outline.x_max) / 2)

    best = original
    best_hits = sum(_bounds_touch_keepout(_bounds(original), ko) for ko in keepouts)
    seen: set[float] = set()
    for offset in offsets:
        rounded = round(float(offset), 6)
        if rounded in seen:
            continue
        seen.add(rounded)
        candidate_anchor = EdgeAnchor(
            ref=anchor.ref,
            edge=anchor.edge,
            offset_mm=float(offset),
            inset_mm=anchor.inset_mm,
            rot_deg=anchor.rot_deg,
        )
        candidate = _edge_anchor_origin_position(
            candidate_anchor,
            width,
            height,
            outline,
            geometry=geometry,
            ref=ref,
            footprint=footprint,
        )
        hits = sum(_bounds_touch_keepout(_bounds(candidate), ko) for ko in keepouts)
        if hits < best_hits:
            best = candidate
            best_hits = hits
            if hits == 0:
                break
    return best


def _apply_assembly_sides(
    placed_parts: list[PlacedPart],
    intent_plan: PlacementIntentPlan | None,
) -> list[PlacedPart]:
    sides = getattr(intent_plan, "assembly_sides", None) or {}
    if not sides:
        return placed_parts

    result: list[PlacedPart] = []
    for placed in placed_parts:
        side = sides.get(placed.ref, getattr(placed, "side", "front"))
        side = str(side or "front").lower()
        if side not in {"front", "back", "mechanical"}:
            side = "front"
        if side == getattr(placed, "side", "front"):
            result.append(placed)
            continue
        result.append(
            PlacedPart(
                ref=placed.ref,
                x_mm=placed.x_mm,
                y_mm=placed.y_mm,
                rot_deg=placed.rot_deg,
                footprint=placed.footprint,
                side=side,
            )
        )
    return result


def _bounds_overlap(
    a: tuple[float, float, float, float],
    b: tuple[float, float, float, float],
    clearance_mm: float,
) -> bool:
    return not (
        a[2] + clearance_mm <= b[0]
        or b[2] + clearance_mm <= a[0]
        or a[3] + clearance_mm <= b[1]
        or b[3] + clearance_mm <= a[1]
    )


def _translated_bounds(
    bounds: tuple[float, float, float, float],
    dx: float,
    dy: float,
) -> tuple[float, float, float, float]:
    return bounds[0] + dx, bounds[1] + dy, bounds[2] + dx, bounds[3] + dy


def _clamp_delta_to_outline(
    bounds: tuple[float, float, float, float],
    dx: float,
    dy: float,
    outline: BoardOutline,
    clearance_mm: float,
) -> tuple[float, float]:
    moved = _translated_bounds(bounds, dx, dy)
    if moved[0] < outline.x_min + clearance_mm:
        dx += outline.x_min + clearance_mm - moved[0]
    if moved[2] > outline.x_max - clearance_mm:
        dx -= moved[2] - (outline.x_max - clearance_mm)
    moved = _translated_bounds(bounds, dx, dy)
    if moved[1] < outline.y_min + clearance_mm:
        dy += outline.y_min + clearance_mm - moved[1]
    if moved[3] > outline.y_max - clearance_mm:
        dy -= moved[3] - (outline.y_max - clearance_mm)
    return dx, dy


def _legalize_edge_anchor_neighbors(
    placed_parts: list[PlacedPart],
    outline: BoardOutline | None,
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None,
    clearance_mm: float,
) -> tuple[list[PlacedPart], list[str]]:
    if outline is None:
        return placed_parts, []

    anchors = _edge_anchor_map(intent_plan, constraints)
    if not anchors:
        return placed_parts, []

    mounting_refs = set(intent_plan.refs_with_kind("mounting_hole")) if intent_plan else set()
    anchor_refs = set(anchors)
    explicit_floorplan_refs = _constraint_floorplan_refs(constraints)
    placed_by_ref = {placed.ref: placed for placed in placed_parts}
    moved_refs: set[str] = set()

    for _ in range(2):
        changed = False
        bounds_by_ref = {
            ref: _placed_bounds(placed, fp_bboxes, fp_geometries)
            for ref, placed in placed_by_ref.items()
        }
        for anchor_ref, anchor in anchors.items():
            anchor_part = placed_by_ref.get(anchor_ref)
            anchor_bounds = bounds_by_ref.get(anchor_ref)
            if anchor_part is None or anchor_bounds is None:
                continue
            edge = anchor.edge.lower()
            for ref, placed in list(placed_by_ref.items()):
                if (
                    ref in anchor_refs
                    or ref in mounting_refs
                    or ref in explicit_floorplan_refs
                ):
                    continue
                bounds = bounds_by_ref[ref]
                if not _bounds_overlap(anchor_bounds, bounds, clearance_mm):
                    continue
                dx = dy = 0.0
                if edge == "left":
                    dx = anchor_bounds[2] + clearance_mm - bounds[0]
                elif edge == "right":
                    dx = anchor_bounds[0] - clearance_mm - bounds[2]
                elif edge == "top":
                    dy = anchor_bounds[3] + clearance_mm - bounds[1]
                elif edge == "bottom":
                    dy = anchor_bounds[1] - clearance_mm - bounds[3]
                else:
                    continue
                dx, dy = _clamp_delta_to_outline(bounds, dx, dy, outline, clearance_mm)
                if abs(dx) <= 1e-6 and abs(dy) <= 1e-6:
                    continue
                placed_by_ref[ref] = PlacedPart(
                    ref=placed.ref,
                    x_mm=placed.x_mm + dx,
                    y_mm=placed.y_mm + dy,
                    rot_deg=placed.rot_deg,
                    footprint=placed.footprint,
                    side=getattr(placed, "side", "front"),
                )
                moved_refs.add(ref)
                changed = True
        if not changed:
            break

    if not moved_refs:
        return placed_parts, []
    return [placed_by_ref[placed.ref] for placed in placed_parts], sorted(moved_refs)


def _legalize_small_parts_from_outline(
    placed_parts: list[PlacedPart],
    circuit,
    outline: BoardOutline | None,
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None,
    clearance_mm: float,
) -> tuple[list[PlacedPart], list[str]]:
    """Keep small non-mechanical parts from hugging the board edge."""
    if outline is None or circuit is None:
        return placed_parts, []

    roles = classify_parts(circuit)
    small_roles = {"decoupling_cap", "signal_passive", "crystal", "diode", "inductor"}
    anchors = _edge_anchor_map(intent_plan, constraints)
    protected_refs = set(anchors)
    protected_refs.update(
        intent_plan.refs_with_kind("mounting_hole") if intent_plan else []
    )
    protected_refs.update(_constraint_floorplan_refs(constraints))

    placed_by_ref = {placed.ref: placed for placed in placed_parts}
    moved_refs: set[str] = set()
    interior_clearance = max(clearance_mm, 1.5)

    def _bounds_for(ref: str) -> tuple[float, float, float, float]:
        return _placed_bounds(placed_by_ref[ref], fp_bboxes, fp_geometries)

    def _overlaps_others(
        ref: str,
        bounds: tuple[float, float, float, float],
    ) -> bool:
        for other_ref in placed_by_ref:
            if other_ref == ref:
                continue
            if _bounds_overlap(bounds, _bounds_for(other_ref), clearance_mm):
                return True
        return False

    def _candidate_deltas(
        bounds: tuple[float, float, float, float],
        base_dx: float,
        base_dy: float,
    ) -> list[tuple[float, float]]:
        candidates = [(base_dx, base_dy)]
        for radius in (0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 8.0):
            for angle in range(0, 360, 45):
                dx = base_dx + radius * math.cos(math.radians(angle))
                dy = base_dy + radius * math.sin(math.radians(angle))
                candidates.append(
                    _clamp_delta_to_outline(
                        bounds,
                        dx,
                        dy,
                        outline,
                        interior_clearance,
                    )
                )
        return candidates

    for ref in sorted(placed_by_ref, key=_natural_ref_key):
        if ref in protected_refs:
            continue
        role = roles.get(ref)
        if role is None or role.role not in small_roles:
            continue

        placed = placed_by_ref[ref]
        bounds = _placed_bounds(placed, fp_bboxes, fp_geometries)
        base_dx, base_dy = _clamp_delta_to_outline(
            bounds,
            0.0,
            0.0,
            outline,
            interior_clearance,
        )
        if abs(base_dx) <= 1e-6 and abs(base_dy) <= 1e-6:
            continue

        best: tuple[float, float] | None = None
        best_distance = float("inf")
        seen: set[tuple[float, float]] = set()
        for dx, dy in _candidate_deltas(bounds, base_dx, base_dy):
            key = (round(dx, 6), round(dy, 6))
            if key in seen:
                continue
            seen.add(key)
            candidate_bounds = _translated_bounds(bounds, dx, dy)
            if _overlaps_others(ref, candidate_bounds):
                continue
            distance = math.hypot(dx, dy)
            if distance < best_distance:
                best = (dx, dy)
                best_distance = distance
                if abs(dx - base_dx) <= 1e-6 and abs(dy - base_dy) <= 1e-6:
                    break

        if best is None:
            continue
        dx, dy = best
        if abs(dx) <= 1e-6 and abs(dy) <= 1e-6:
            continue
        placed_by_ref[ref] = PlacedPart(
            ref=placed.ref,
            x_mm=placed.x_mm + dx,
            y_mm=placed.y_mm + dy,
            rot_deg=placed.rot_deg,
            footprint=placed.footprint,
            side=getattr(placed, "side", "front"),
        )
        moved_refs.add(ref)

    if not moved_refs:
        return placed_parts, []
    return [placed_by_ref[placed.ref] for placed in placed_parts], sorted(moved_refs)


def _natural_ref_key(ref: str) -> tuple[str, int, str]:
    match = re.match(r"([A-Za-z]+)(\d+)", str(ref))
    if match:
        return match.group(1), int(match.group(2)), str(ref)
    return str(ref), 0, str(ref)


def _pin_net_names_for_part(part) -> list[str]:
    names: list[str] = []
    for pin in getattr(part, "pins", []) or []:
        net = getattr(pin, "net", None)
        name = str(getattr(net, "name", "") or "")
        if name:
            names.append(name)
    return names


def _constraint_floorplan_refs(constraints: LayoutConstraints | None) -> set[str]:
    if constraints is None:
        return set()
    refs = {fixed.ref for fixed in constraints.fixed or []}
    refs.update(anchor.ref for anchor in constraints.edge_anchors or [])
    refs.update(face.ref for face in constraints.face_edges or [])
    for zone in constraints.zones or []:
        refs.update(zone.refs or [])
    for constraint in constraints.align or []:
        refs.update(constraint.refs or [])
    for constraint in constraints.distribute or []:
        refs.update(constraint.refs or [])
    for constraint in constraints.near or []:
        refs.add(constraint.ref)
        refs.add(constraint.target_ref)
    for constraint in constraints.far or []:
        refs.add(constraint.ref)
        refs.add(constraint.target_ref)
    return refs


def _arrange_passive_grid_between_opposing_headers(
    placed_parts: list[PlacedPart],
    circuit,
    outline: BoardOutline | None,
    intent_plan: PlacementIntentPlan | None,
    constraints: LayoutConstraints | None,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None,
) -> tuple[list[PlacedPart], list[str]]:
    if outline is None or circuit is None:
        return placed_parts, []

    anchors = _edge_anchor_map(intent_plan, constraints)
    left_refs = [ref for ref, anchor in anchors.items() if anchor.edge.lower() == "left"]
    right_refs = [ref for ref, anchor in anchors.items() if anchor.edge.lower() == "right"]
    if len(left_refs) != 1 or len(right_refs) != 1:
        return placed_parts, []

    mounting_refs = set(intent_plan.refs_with_kind("mounting_hole")) if intent_plan else set()
    edge_refs = set(left_refs + right_refs)
    explicit_floorplan_refs = _constraint_floorplan_refs(constraints)
    excluded_refs = mounting_refs | edge_refs | explicit_floorplan_refs

    part_by_ref = {
        str(getattr(part, "ref", "") or ""): part
        for part in getattr(circuit, "parts", []) or []
    }

    passive_refs: list[str] = []
    primary_refs: list[str] = []
    for ref, part in part_by_ref.items():
        if ref in excluded_refs:
            continue
        try:
            pin_count = len(part)
        except Exception:
            pin_count = 0
        prefix = re.match(r"[A-Za-z]+", ref)
        prefix_text = prefix.group(0).upper() if prefix else ""
        if pin_count == 2 and prefix_text in {"R", "C", "L", "FB", "D"}:
            passive_refs.append(ref)
        elif pin_count > 2:
            primary_refs.append(ref)

    if len(passive_refs) < 3 or primary_refs:
        return placed_parts, []

    placed_by_ref = {placed.ref: placed for placed in placed_parts}
    if not all(ref in placed_by_ref for ref in passive_refs + left_refs + right_refs):
        return placed_parts, []

    left_bounds = _placed_bounds(placed_by_ref[left_refs[0]], fp_bboxes, fp_geometries)
    right_bounds = _placed_bounds(placed_by_ref[right_refs[0]], fp_bboxes, fp_geometries)
    usable_x_min = left_bounds[2] + 4.0
    usable_x_max = right_bounds[0] - 4.0
    usable_y_min = outline.y_min + max(5.0, outline.height_mm * 0.24)
    usable_y_max = outline.y_max - max(5.0, outline.height_mm * 0.24)
    if usable_x_max <= usable_x_min or usable_y_max <= usable_y_min:
        return placed_parts, []

    header_y_by_net: dict[str, list[float]] = {}
    for ref in edge_refs:
        part = part_by_ref.get(ref)
        placed = placed_by_ref.get(ref)
        geometry = (fp_geometries or {}).get(placed.footprint) if placed else None
        if part is None or placed is None or geometry is None:
            continue
        centers = geometry.pad_world_centers(placed)
        pins = list(getattr(part, "pins", []) or [])
        for index, pin in enumerate(pins, start=1):
            net = getattr(pin, "net", None)
            name = str(getattr(net, "name", "") or "")
            if not name:
                continue
            pin_num = str(getattr(pin, "num", "") or index)
            center = centers.get(pin_num)
            if center is not None:
                header_y_by_net.setdefault(name, []).append(center[1])

    def _passive_target_y(ref: str) -> float:
        part = part_by_ref.get(ref)
        ys: list[float] = []
        for name in _pin_net_names_for_part(part):
            ys.extend(header_y_by_net.get(name, []))
        if ys:
            return sum(ys) / len(ys)
        return (placed_by_ref[ref].y_mm if ref in placed_by_ref else 0.0)

    def _passive_group_key(ref: str) -> tuple[str, str]:
        part = part_by_ref.get(ref)
        nets = _pin_net_names_for_part(part)
        signal_nets = [
            name
            for name in nets
            if not POWER_NET_RE.match(name) and not GND_NET_RE.match(name)
        ]
        if signal_nets:
            return "signal", sorted(signal_nets)[0]
        if any(POWER_NET_RE.match(name) for name in nets):
            return "power", "supply"
        if any(GND_NET_RE.match(name) for name in nets):
            return "ground", "ground"
        return "misc", ref

    grouped: dict[tuple[str, str], list[str]] = {}
    for ref in passive_refs:
        grouped.setdefault(_passive_group_key(ref), []).append(ref)

    groups = [
        sorted(refs, key=_natural_ref_key)
        for _, refs in sorted(
            grouped.items(),
            key=lambda item: (
                sum(_passive_target_y(ref) for ref in item[1]) / len(item[1]),
                item[0],
            ),
        )
    ]

    group_count = len(groups)
    group_columns = 1 if group_count <= 3 else 2

    def _part_size(ref: str) -> tuple[float, float]:
        placed = placed_by_ref[ref]
        bounds = _placed_bounds(placed, fp_bboxes, fp_geometries)
        return bounds[2] - bounds[0], bounds[3] - bounds[1]

    def _group_metrics(refs: list[str]) -> tuple[int, int, float, float, float, float]:
        widths, heights = zip(*(_part_size(ref) for ref in refs))
        max_width = max(widths)
        max_height = max(heights)
        local_cols = min(len(refs), 2)
        local_rows = math.ceil(len(refs) / local_cols)
        x_step = max(3.2, max_width + 0.9)
        y_step = max(3.2, max_height + 0.9)
        span_width = max_width + (local_cols - 1) * x_step
        span_height = max_height + (local_rows - 1) * y_step
        return local_cols, local_rows, x_step, y_step, span_width, span_height

    metrics_by_group = [_group_metrics(refs) for refs in groups]
    max_group_width = max(metric[4] for metric in metrics_by_group)
    max_group_height = max(metric[5] for metric in metrics_by_group)
    usable_width = usable_x_max - usable_x_min
    usable_height = usable_y_max - usable_y_min

    def _grid_fits(columns: int) -> bool:
        rows = math.ceil(group_count / columns)
        if columns > 1 and usable_width / (columns - 1) < max_group_width + 1.0:
            return False
        if rows > 1 and usable_height / (rows - 1) < max_group_height + 1.0:
            return False
        return usable_width >= max_group_width and usable_height >= max_group_height

    if group_columns > 1 and not _grid_fits(group_columns):
        group_columns = 1
    if not _grid_fits(group_columns):
        return placed_parts, []

    group_rows = math.ceil(group_count / group_columns)

    def _group_center(index: int) -> tuple[float, float]:
        _, _, _, _, span_width, span_height = metrics_by_group[index]
        row = index // group_columns
        col = index % group_columns
        if group_columns == 1:
            x = (usable_x_min + usable_x_max) / 2
        else:
            x = usable_x_min + (usable_x_max - usable_x_min) * col / (group_columns - 1)
        if group_rows == 1:
            y = (usable_y_min + usable_y_max) / 2
        else:
            y = usable_y_min + (usable_y_max - usable_y_min) * row / (group_rows - 1)
        x_min = usable_x_min + span_width / 2
        x_max = usable_x_max - span_width / 2
        y_min = usable_y_min + span_height / 2
        y_max = usable_y_max - span_height / 2
        return (
            max(x_min, min(x_max, x)) if x_min <= x_max else x,
            max(y_min, min(y_max, y)) if y_min <= y_max else y,
        )

    def _local_position(
        group_center: tuple[float, float],
        index: int,
        metrics: tuple[int, int, float, float, float, float],
    ) -> tuple[float, float]:
        local_cols, local_rows, x_step, y_step, _, _ = metrics
        if local_cols == 1 and local_rows == 1:
            return group_center
        row = index // local_cols
        col = index % local_cols
        x = group_center[0] + (col - (local_cols - 1) / 2) * x_step
        y = group_center[1] + (row - (local_rows - 1) / 2) * y_step
        return (
            max(usable_x_min, min(usable_x_max, x)),
            max(usable_y_min, min(usable_y_max, y)),
        )

    replacements: dict[str, PlacedPart] = {}
    moved_refs: list[str] = []
    for group_index, refs in enumerate(groups):
        center = _group_center(group_index)
        metrics = metrics_by_group[group_index]
        for local_index, ref in enumerate(refs):
            placed = placed_by_ref[ref]
            x_mm, y_mm = _local_position(center, local_index, metrics)
            replacements[ref] = PlacedPart(
                ref=placed.ref,
                x_mm=x_mm,
                y_mm=y_mm,
                rot_deg=placed.rot_deg,
                footprint=placed.footprint,
                side=getattr(placed, "side", "front"),
            )
            moved_refs.append(ref)

    return [replacements.get(placed.ref, placed) for placed in placed_parts], moved_refs


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
    routability: RoutabilityFeedback | None = None,
    assembly_policy: str | None = None,
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
    auto_outline = resolved_outline is None and derive_outline_if_missing
    density_outline: BoardOutline | None = None
    form_factor = getattr(resolved_constraints, "form_factor", None)
    if auto_outline:
        if form_factor:
            resolved_outline = _auto_outline_from_circuit(
                circuit,
                resolved_bboxes,
                form_factor,
            )
        else:
            density_outline = derive_outline_from_circuit(circuit, resolved_bboxes)
            resolved_outline = density_outline
        resolved_constraints.outline = resolved_outline

    groups = extract_groups(circuit)
    intent_plan = infer_placement_intents(
        circuit,
        outline=resolved_outline,
        assembly_policy=assembly_policy,
    )
    power_topology = infer_power_topology(circuit)
    candidates = generate_placement_candidates(
        groups,
        resolved_constraints,
        resolved_bboxes,
        intent_plan=intent_plan,
        power_topology=power_topology,
        fp_geometries=fp_geometries,
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
        candidate.placed_parts = _apply_assembly_sides(
            candidate.placed_parts,
            intent_plan,
        )
        candidate_constraints = candidate.constraints or resolved_constraints
        if resolved_outline is not None and not auto_outline:
            candidate.placed_parts, moved_edge_refs = _snap_edge_anchors_to_outline(
                candidate.placed_parts,
                resolved_outline,
                intent_plan,
                candidate_constraints,
                resolved_bboxes,
                fp_geometries,
            )
            if moved_edge_refs:
                candidate.reasons.append("edge connectors snapped to outline edges")
                for ref in moved_edge_refs:
                    candidate.ref_reasons.setdefault(ref, []).append(
                        "snapped to outline edge"
                    )
            candidate.placed_parts, moved_neighbor_refs = _legalize_edge_anchor_neighbors(
                candidate.placed_parts,
                resolved_outline,
                intent_plan,
                candidate_constraints,
                resolved_bboxes,
                fp_geometries,
                clearance_mm,
            )
            if moved_neighbor_refs:
                candidate.reasons.append(
                    "near-edge parts nudged clear of edge connectors"
                )
                for ref in moved_neighbor_refs:
                    candidate.ref_reasons.setdefault(ref, []).append(
                        "nudged clear of edge connector"
                    )
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
            raw_score = score_placement_quick(
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
            raw_score = score_placement(
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
        candidate_scores[candidate.name] = _apply_edge_intent_score(
            raw_score,
            candidate.placed_parts,
            resolved_bboxes,
            resolved_outline,
            intent_plan,
            constraints=candidate.constraints,
            fp_geometries=fp_geometries,
        )
        candidate.score = candidate_scores[candidate.name].score

    any_valid = any(
        candidate_validations[c.name].ok for c in candidates
    )
    if not any_valid:
        for candidate in candidates:
            candidate_constraints = candidate.constraints or resolved_constraints
            raw_score = score_placement(
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
            candidate_scores[candidate.name] = _apply_edge_intent_score(
                raw_score,
                candidate.placed_parts,
                resolved_bboxes,
                resolved_outline,
                intent_plan,
                constraints=candidate.constraints,
                fp_geometries=fp_geometries,
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

    if auto_outline:
        min_area = (
            density_outline.width_mm * density_outline.height_mm
            if density_outline is not None
            else 0.0
        )
        resolved_outline = _derive_outline_for_edge_anchors(
            placed_parts,
            resolved_bboxes,
            margin_mm=margin_mm,
            form_factor=form_factor,
            min_area_mm2=min_area,
            max_min_area_growth=1.35,
            intent_plan=intent_plan,
            constraints=selected_constraints,
            fp_geometries=fp_geometries,
        )
        selected_constraints.outline = resolved_outline
        placed_parts, moved_edge_refs = _snap_edge_anchors_to_outline(
            placed_parts,
            resolved_outline,
            intent_plan,
            resolved_constraints,
            resolved_bboxes,
            fp_geometries,
        )
        if moved_edge_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "edge connectors snapped to final auto-outline edges"
            )
            for ref in moved_edge_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "snapped to final auto-outline edge"
                )
        placed_parts, gridded_passive_refs = _arrange_passive_grid_between_opposing_headers(
            placed_parts,
            circuit,
            resolved_outline,
            intent_plan,
            resolved_constraints,
            resolved_bboxes,
            fp_geometries,
        )
        if gridded_passive_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "simple passives arranged on an even grid between opposing headers"
            )
            for ref in gridded_passive_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "arranged on passive grid between opposing headers"
                )
        placed_parts, moved_neighbor_refs = _legalize_edge_anchor_neighbors(
            placed_parts,
            resolved_outline,
            intent_plan,
            resolved_constraints,
            resolved_bboxes,
            fp_geometries,
            clearance_mm,
        )
        if moved_neighbor_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "near-edge parts nudged clear of final edge connectors"
            )
            for ref in moved_neighbor_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "nudged clear of final edge connector"
                )
        placed_parts, moved_mounting_refs = _snap_mounting_holes_to_outline_corners(
            placed_parts,
            resolved_outline,
            intent_plan,
            resolved_constraints,
            resolved_bboxes,
            fp_geometries,
        )
        if moved_mounting_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "mounting holes snapped to final auto-outline corners"
            )
            for ref in moved_mounting_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "snapped to final auto-outline corner"
                )
        placed_parts, moved_interior_refs = _legalize_small_parts_from_outline(
            placed_parts,
            circuit,
            resolved_outline,
            intent_plan,
            resolved_constraints,
            resolved_bboxes,
            fp_geometries,
            clearance_mm,
        )
        if moved_interior_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "small passive parts nudged away from board outline"
            )
            for ref in moved_interior_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "nudged away from board outline"
                )
    elif resolved_outline is None and derive_outline_if_missing:
        min_area = 0.0
        if not form_factor:
            density_outline = derive_outline_from_circuit(
                circuit, resolved_bboxes
            )
            min_area = density_outline.width_mm * density_outline.height_mm
        resolved_outline = derive_outline(
            placed_parts,
            resolved_bboxes,
            margin_mm=margin_mm,
            form_factor=form_factor,
            min_area_mm2=min_area,
            max_min_area_growth=1.35,
        )

    if resolved_outline is not None and not auto_outline:
        placed_parts, moved_edge_refs = _snap_edge_anchors_to_outline(
            placed_parts,
            resolved_outline,
            intent_plan,
            selected_constraints,
            resolved_bboxes,
            fp_geometries,
        )
        if moved_edge_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "edge connectors snapped to fixed-outline edges"
            )
            for ref in moved_edge_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "snapped to fixed-outline edge"
                )
        placed_parts, moved_neighbor_refs = _legalize_edge_anchor_neighbors(
            placed_parts,
            resolved_outline,
            intent_plan,
            selected_constraints,
            resolved_bboxes,
            fp_geometries,
            clearance_mm,
        )
        if moved_neighbor_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "near-edge parts nudged clear of fixed-outline edge connectors"
            )
            for ref in moved_neighbor_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "nudged clear of fixed-outline edge connector"
                )
        placed_parts, moved_interior_refs = _legalize_small_parts_from_outline(
            placed_parts,
            circuit,
            resolved_outline,
            intent_plan,
            resolved_constraints,
            resolved_bboxes,
            fp_geometries,
            clearance_mm,
        )
        if moved_interior_refs:
            selected_candidate.placed_parts = placed_parts
            selected_candidate.reasons.append(
                "small passive parts nudged away from fixed board outline"
            )
            for ref in moved_interior_refs:
                selected_candidate.ref_reasons.setdefault(ref, []).append(
                    "nudged away from fixed board outline"
                )

    placed_parts = _apply_assembly_sides(placed_parts, intent_plan)
    selected_candidate.placed_parts = placed_parts

    validation = validate(
        placed_parts,
        circuit,
        resolved_bboxes,
        clearance_mm=clearance_mm,
        outline=resolved_outline,
        keepouts=selected_constraints.keepouts,
        fp_geometries=fp_geometries,
    )
    raw_score = score_placement(
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
    score = _apply_edge_intent_score(
        raw_score,
        placed_parts,
        resolved_bboxes,
        resolved_outline,
        intent_plan,
        constraints=selected_constraints,
        fp_geometries=fp_geometries,
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
        routability=routability,
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
        routability=routability,
    )
