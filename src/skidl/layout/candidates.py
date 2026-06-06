from __future__ import annotations

from dataclasses import dataclass, field

from .constraints import AnchorZone, LayoutConstraints
from .intent import PlacementIntentPlan
from .placer import place_parts
from .writer import PlacedPart


@dataclass
class PlacementCandidate:
    name: str
    placed_parts: list[PlacedPart]
    reasons: list[str] = field(default_factory=list)
    score: float | None = None


def copy_constraints(constraints: LayoutConstraints | None) -> LayoutConstraints:
    constraints = constraints or LayoutConstraints()
    return LayoutConstraints(
        fixed=list(constraints.fixed or []),
        zones=list(constraints.zones or []),
        edge_anchors=list(constraints.edge_anchors or []),
        keepouts=list(constraints.keepouts or []),
        align=list(constraints.align or []),
        distribute=list(constraints.distribute or []),
        near=list(constraints.near or []),
        far=list(constraints.far or []),
        face_edges=list(constraints.face_edges or []),
        outline=constraints.outline,
    )


def _merge_inferred_edge_anchors(
    constraints: LayoutConstraints,
    intent_plan: PlacementIntentPlan | None,
) -> LayoutConstraints:
    merged = copy_constraints(constraints)
    if intent_plan is None:
        return merged

    explicit_refs = {anchor.ref for anchor in merged.edge_anchors}
    for anchor in intent_plan.edge_anchors:
        if anchor.ref not in explicit_refs:
            merged.edge_anchors.append(anchor)
            explicit_refs.add(anchor.ref)

    explicit_keepouts = {
        (keepout.x_min, keepout.y_min, keepout.x_max, keepout.y_max)
        for keepout in merged.keepouts
    }
    for keepout in intent_plan.keepouts:
        key = (keepout.x_min, keepout.y_min, keepout.x_max, keepout.y_max)
        if key not in explicit_keepouts:
            merged.keepouts.append(keepout)
            explicit_keepouts.add(key)
    return merged


def _with_power_zone(
    constraints: LayoutConstraints,
    intent_plan: PlacementIntentPlan | None,
) -> LayoutConstraints:
    zoned = _merge_inferred_edge_anchors(constraints, intent_plan)
    if zoned.outline is None or intent_plan is None:
        return zoned

    power_refs = sorted(
        set(intent_plan.refs_with_kind("power_input"))
        | set(intent_plan.refs_with_kind("power_cluster"))
    )
    if not power_refs:
        return zoned

    outline = zoned.outline
    y_mid = outline.y_min + outline.height_mm * 0.55
    zoned.zones.append(
        AnchorZone(
            group_name="",
            x_min=outline.x_min,
            y_min=y_mid,
            x_max=outline.x_min + outline.width_mm * 0.55,
            y_max=outline.y_max,
            refs=power_refs,
        )
    )
    return zoned


def _with_cluster_zone(
    constraints: LayoutConstraints,
    intent_plan: PlacementIntentPlan | None,
) -> LayoutConstraints:
    zoned = _merge_inferred_edge_anchors(constraints, intent_plan)
    if zoned.outline is None or intent_plan is None:
        return zoned

    service_refs = sorted(
        set(intent_plan.refs_with_kind("edge_connector"))
        | set(intent_plan.refs_with_kind("board_ui"))
        | set(intent_plan.refs_with_kind("power_input"))
        | set(intent_plan.refs_with_kind("power_cluster"))
        | set(intent_plan.refs_with_kind("test_debug"))
    )
    if not service_refs:
        return zoned

    outline = zoned.outline
    zoned.zones.append(
        AnchorZone(
            group_name="",
            x_min=outline.x_min,
            y_min=outline.y_min + outline.height_mm * 0.60,
            x_max=outline.x_max,
            y_max=outline.y_max,
            refs=service_refs,
        )
    )
    return zoned


def _append_candidate(
    candidates: list[PlacementCandidate],
    name: str,
    groups: dict,
    constraints: LayoutConstraints,
    fp_bboxes: dict[str, tuple[float, float]],
    reasons: list[str],
):
    placed = place_parts(groups, constraints, fp_bboxes)
    candidates.append(PlacementCandidate(name=name, placed_parts=placed, reasons=reasons))


def generate_placement_candidates(
    groups: dict,
    constraints: LayoutConstraints,
    fp_bboxes: dict[str, tuple[float, float]],
    intent_plan: PlacementIntentPlan | None = None,
) -> list[PlacementCandidate]:
    """Generate deterministic placement candidates from available intent."""
    candidates: list[PlacementCandidate] = []

    _append_candidate(
        candidates,
        "baseline",
        groups,
        copy_constraints(constraints),
        fp_bboxes,
        ["explicit constraints and default placement order"],
    )
    _append_candidate(
        candidates,
        "connector_edge_first",
        groups,
        _merge_inferred_edge_anchors(constraints, intent_plan),
        fp_bboxes,
        ["inferred connector edge anchors applied before primary parts"],
    )
    _append_candidate(
        candidates,
        "power_first",
        groups,
        _with_power_zone(constraints, intent_plan),
        fp_bboxes,
        ["power input and regulator-like parts biased into a power zone"],
    )
    _append_candidate(
        candidates,
        "cluster_first",
        groups,
        _with_cluster_zone(constraints, intent_plan),
        fp_bboxes,
        ["edge/UI/power/debug refs biased into a shared service zone"],
    )

    if intent_plan is not None and intent_plan.backend_status.enabled:
        _append_candidate(
            candidates,
            "optional_backend_ready",
            groups,
            _with_cluster_zone(constraints, intent_plan),
            fp_bboxes,
            [
                "optional optimization backends detected; using deterministic "
                "core strategy until backend-specific solvers are enabled"
            ],
        )

    return candidates
