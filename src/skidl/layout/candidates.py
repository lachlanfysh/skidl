from __future__ import annotations

from dataclasses import dataclass, field

from .constraints import (
    AlignConstraint,
    AnchorZone,
    DistributeConstraint,
    LayoutConstraints,
    NearConstraint,
)
from .intent import PlacementIntentPlan, RepeatedChannelIntent
from .placer import place_parts
from .power import PowerTopology
from .writer import PlacedPart


@dataclass
class PlacementCandidate:
    name: str
    placed_parts: list[PlacedPart]
    reasons: list[str] = field(default_factory=list)
    ref_reasons: dict[str, list[str]] = field(default_factory=dict)
    constraints: LayoutConstraints | None = None
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

    explicit_face_refs = {face.ref for face in merged.face_edges}
    for face_edge in intent_plan.face_edges:
        if face_edge.ref not in explicit_face_refs:
            merged.face_edges.append(face_edge)
            explicit_face_refs.add(face_edge.ref)

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


def _with_power_topology(
    constraints: LayoutConstraints,
    intent_plan: PlacementIntentPlan | None,
    power_topology: PowerTopology | None,
) -> LayoutConstraints:
    powered = _with_power_zone(constraints, intent_plan)
    if power_topology is None or not power_topology.chains:
        return powered

    refs = power_topology.refs()
    if powered.outline is not None and refs:
        outline = powered.outline
        powered.zones.append(
            AnchorZone(
                group_name="",
                x_min=outline.x_min,
                y_min=outline.y_min + outline.height_mm * 0.55,
                x_max=outline.x_min + outline.width_mm * 0.70,
                y_max=outline.y_max,
                refs=refs,
            )
        )

    for chain in power_topology.chains:
        ordered = chain.ordered_refs
        for target_ref, ref in zip(ordered, ordered[1:]):
            powered.near.append(
                NearConstraint(ref=ref, target_ref=target_ref, distance_mm=10.0)
            )
    return powered


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


def _channel_slot_refs(channel: RepeatedChannelIntent) -> list[str]:
    ref_counts: dict[str, int] = {}
    for refs in channel.refs_by_channel.values():
        for ref in refs:
            ref_counts[ref] = ref_counts.get(ref, 0) + 1

    slot_refs: list[str] = []
    for channel_number in sorted(channel.refs_by_channel):
        unique_refs = [
            ref
            for ref in channel.refs_by_channel[channel_number]
            if ref_counts.get(ref, 0) == 1
        ]
        refs = unique_refs or channel.refs_by_channel[channel_number]
        for ref in sorted(refs):
            if ref not in slot_refs:
                slot_refs.append(ref)
    return slot_refs


def _with_repeated_channel_array(
    constraints: LayoutConstraints,
    intent_plan: PlacementIntentPlan | None,
) -> LayoutConstraints:
    arrayed = _merge_inferred_edge_anchors(constraints, intent_plan)
    if arrayed.outline is None or intent_plan is None:
        return arrayed

    outline = arrayed.outline
    for channel in intent_plan.repeated_channels:
        slot_refs = _channel_slot_refs(channel)
        if len(slot_refs) < 2:
            continue

        x_pad = outline.width_mm * 0.12
        y = outline.y_min + outline.height_mm * 0.25
        arrayed.distribute.append(
            DistributeConstraint(
                refs=slot_refs,
                axis="x",
                start_mm=outline.x_min + x_pad,
                end_mm=outline.x_max - x_pad,
            )
        )
        arrayed.align.append(AlignConstraint(refs=slot_refs, axis="y", value_mm=y))
        arrayed.zones.append(
            AnchorZone(
                group_name="",
                x_min=outline.x_min,
                y_min=outline.y_min,
                x_max=outline.x_max,
                y_max=outline.y_min + outline.height_mm * 0.55,
                refs=slot_refs,
            )
        )
    return arrayed


def _annotate_ref_reasons(
    candidate: PlacementCandidate,
    constraints: LayoutConstraints,
    intent_plan: PlacementIntentPlan | None,
    power_topology: PowerTopology | None = None,
) -> None:
    fixed_refs = {fixed.ref for fixed in constraints.fixed or []}
    edge_by_ref = {anchor.ref: anchor for anchor in constraints.edge_anchors or []}
    face_refs = {face.ref for face in constraints.face_edges or []}
    mating_by_ref = {
        mating.ref: mating for mating in (intent_plan.mating_intents if intent_plan else [])
    }
    power_chain_by_ref = {}
    for chain in power_topology.chains if power_topology else []:
        for ref in chain.ordered_refs:
            power_chain_by_ref[ref] = chain
    zone_by_ref = {}
    for zone in constraints.zones or []:
        for ref in zone.refs or []:
            zone_by_ref[ref] = zone

    for placed in candidate.placed_parts:
        reasons: list[str] = []
        if placed.ref in fixed_refs:
            reasons.append("locked by fixed-position constraint")
        if placed.ref in edge_by_ref:
            reasons.append(f"anchored to {edge_by_ref[placed.ref].edge} board edge")
        if placed.ref in zone_by_ref:
            reasons.append("assigned to a placement zone")
        if placed.ref in face_refs:
            reasons.append("rotation constrained by face-edge intent")
        if placed.ref in mating_by_ref:
            mating = mating_by_ref[placed.ref]
            detail = mating.kind
            if mating.edge_preference:
                detail += f" facing {mating.edge_preference}"
            if mating.mating_side:
                detail += f" ({mating.mating_side})"
            reasons.append(f"mating intent: {detail}")
        if placed.ref in power_chain_by_ref:
            chain = power_chain_by_ref[placed.ref]
            reasons.append(
                f"power chain: {chain.source_net} from {chain.source_ref}"
            )
        if intent_plan is not None:
            kinds = sorted(
                {intent.kind for intent in intent_plan.intents_for(placed.ref)}
            )
            if kinds:
                reasons.append("inferred intent: " + ", ".join(kinds))
        if not reasons:
            reasons.append(f"placed by {candidate.name} strategy")
        candidate.ref_reasons[placed.ref] = reasons


def _append_candidate(
    candidates: list[PlacementCandidate],
    name: str,
    groups: dict,
    constraints: LayoutConstraints,
    fp_bboxes: dict[str, tuple[float, float]],
    reasons: list[str],
    intent_plan: PlacementIntentPlan | None = None,
    power_topology: PowerTopology | None = None,
):
    placed = place_parts(groups, constraints, fp_bboxes)
    candidate = PlacementCandidate(
        name=name,
        placed_parts=placed,
        reasons=reasons,
        constraints=constraints,
    )
    _annotate_ref_reasons(candidate, constraints, intent_plan, power_topology)
    candidates.append(candidate)


def generate_placement_candidates(
    groups: dict,
    constraints: LayoutConstraints,
    fp_bboxes: dict[str, tuple[float, float]],
    intent_plan: PlacementIntentPlan | None = None,
    power_topology: PowerTopology | None = None,
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
        intent_plan,
        power_topology,
    )
    _append_candidate(
        candidates,
        "connector_edge_first",
        groups,
        _merge_inferred_edge_anchors(constraints, intent_plan),
        fp_bboxes,
        ["inferred connector edge anchors applied before primary parts"],
        intent_plan,
        power_topology,
    )
    _append_candidate(
        candidates,
        "power_first",
        groups,
        _with_power_zone(constraints, intent_plan),
        fp_bboxes,
        ["power input and regulator-like parts biased into a power zone"],
        intent_plan,
        power_topology,
    )
    _append_candidate(
        candidates,
        "power_topology_first",
        groups,
        _with_power_topology(constraints, intent_plan, power_topology),
        fp_bboxes,
        ["source/protection/conversion/storage/load power chains biased together"],
        intent_plan,
        power_topology,
    )
    _append_candidate(
        candidates,
        "cluster_first",
        groups,
        _with_cluster_zone(constraints, intent_plan),
        fp_bboxes,
        ["edge/UI/power/debug refs biased into a shared service zone"],
        intent_plan,
        power_topology,
    )
    _append_candidate(
        candidates,
        "repeated_channel_array",
        groups,
        _with_repeated_channel_array(constraints, intent_plan),
        fp_bboxes,
        ["repeated channel refs aligned and distributed as an ordered array"],
        intent_plan,
        power_topology,
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
            intent_plan,
            power_topology,
        )

    return candidates
