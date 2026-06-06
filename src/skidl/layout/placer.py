from __future__ import annotations

import math
from typing import Optional

from .constraints import (
    AnchorZone,
    BoardOutline,
    EdgeAnchor,
    FixedPosition,
    KeepOut,
    LayoutConstraints,
)
from .hierarchy import PlacementGroup
from .roles import DECAP_VALUE_RE, GND_NET_RE, POWER_NET_RE
from .writer import PlacedPart

_DEFAULT_BBOX = (2.0, 2.0)


def _footprint_name(part) -> str:
    return (
        getattr(part, "foot", None)
        or getattr(part, "footprint", None)
        or ""
    )


def _pin_net_names(part) -> list[str]:
    names = []
    try:
        from skidl.net import NCNet
        for pin in part.pins:
            net = getattr(pin, 'net', None)
            if net is not None and not isinstance(net, NCNet):
                name = getattr(net, 'name', None)
                if name:
                    names.append(name)
    except Exception:
        pass
    return names


def _is_decoupling_cap(part) -> bool:
    if len(part) != 2:
        return False
    val = (getattr(part, 'value', '') or '').strip()
    if not DECAP_VALUE_RE.match(val):
        return False
    nets = _pin_net_names(part)
    return any(POWER_NET_RE.match(n) for n in nets) and any(
        GND_NET_RE.match(n) for n in nets
    )


def _bbox(part, fp_bboxes: dict) -> tuple[float, float]:
    return fp_bboxes.get(_footprint_name(part), _DEFAULT_BBOX)


def _overlaps(x1, y1, w1, h1, x2, y2, w2, h2, clearance=0.5) -> bool:
    return (abs(x1 - x2) < (w1 + w2) / 2 + clearance and
            abs(y1 - y2) < (h1 + h2) / 2 + clearance)


def _overlaps_any(x, y, w, h, occupied: list[tuple], clearance=0.5) -> bool:
    for ox, oy, ow, oh in occupied:
        if _overlaps(x, y, w, h, ox, oy, ow, oh, clearance):
            return True
    return False


def _fits_bounds(x, y, w, h, bounds) -> bool:
    if bounds is None:
        return True
    half_w, half_h = w / 2, h / 2
    return (
        x - half_w >= bounds.x_min
        and y - half_h >= bounds.y_min
        and x + half_w <= bounds.x_max
        and y + half_h <= bounds.y_max
    )


def _find_clear_position(
    target_x: float,
    target_y: float,
    width: float,
    height: float,
    occupied: list[tuple],
    bounds=None,
    step: float = 1.0,
    max_radius: float = 50.0,
) -> tuple[float, float]:
    if _fits_bounds(target_x, target_y, width, height, bounds) and not _overlaps_any(
        target_x, target_y, width, height, occupied
    ):
        return target_x, target_y
    steps = max(1, int(max_radius / step))
    for i in range(1, steps):
        radius = step * i
        angle_count = max(4, int(radius * 2 * math.pi))
        for j in range(angle_count):
            angle = j * (2 * math.pi / angle_count)
            x = target_x + radius * math.cos(angle)
            y = target_y + radius * math.sin(angle)
            if _fits_bounds(x, y, width, height, bounds) and not _overlaps_any(
                x, y, width, height, occupied
            ):
                return x, y
    return target_x, target_y


def _clamp_to_bounds(x, y, w, h, bounds) -> tuple[float, float]:
    if bounds is None:
        return x, y
    half_w, half_h = w / 2, h / 2
    x = max(bounds.x_min + half_w, min(bounds.x_max - half_w, x))
    y = max(bounds.y_min + half_h, min(bounds.y_max - half_h, y))
    return x, y


def _clamp_to_outline(x, y, w, h, outline) -> tuple[float, float]:
    return _clamp_to_bounds(x, y, w, h, outline)


def _group_matches(zone_name: str, group_name: str) -> bool:
    if not zone_name:
        return False
    if zone_name == "*":
        return True
    group_name = group_name or ""
    return (
        group_name == zone_name
        or group_name.endswith(zone_name)
        or zone_name in group_name
    )


def _zone_for_part(part, group: PlacementGroup, constraints: LayoutConstraints):
    for zone in constraints.zones or []:
        if getattr(part, "ref", None) in (zone.refs or []):
            return zone
    for zone in constraints.zones or []:
        if _group_matches(zone.group_name, group.name):
            return zone
    return None


def _bounds_for_part(part, group: PlacementGroup, constraints: LayoutConstraints):
    return _zone_for_part(part, group, constraints) or constraints.outline


def _bounds_center(bounds) -> tuple[float, float]:
    return (bounds.x_min + bounds.x_max) / 2, (bounds.y_min + bounds.y_max) / 2


def _edge_anchor_position(
    anchor: EdgeAnchor,
    width: float,
    height: float,
    outline: BoardOutline,
) -> tuple[float, float, float]:
    if outline is None:
        return 10.0, 10.0, anchor.rot_deg or 0.0
    edge = anchor.edge.lower()
    x_mid, y_mid = _bounds_center(outline)
    if edge in {"top", "bottom"}:
        x = anchor.offset_mm if anchor.offset_mm is not None else x_mid
        y = (
            outline.y_min + height / 2 + anchor.inset_mm
            if edge == "top"
            else outline.y_max - height / 2 - anchor.inset_mm
        )
    elif edge in {"left", "right"}:
        x = (
            outline.x_min + width / 2 + anchor.inset_mm
            if edge == "left"
            else outline.x_max - width / 2 - anchor.inset_mm
        )
        y = anchor.offset_mm if anchor.offset_mm is not None else y_mid
    else:
        raise ValueError(f"Unknown edge anchor '{anchor.edge}' for {anchor.ref}")
    return x, y, anchor.rot_deg if anchor.rot_deg is not None else 0.0


def _is_primary_part(part) -> bool:
    return len(part) != 2


def _most_adjacent_placed(
    ref: str,
    adjacency: dict,
    placed_map: dict,
    *,
    candidate_refs: set[str] | None = None,
    usage_counts: dict[str, int] | None = None,
) -> Optional[str]:
    """Return the ref of the already-placed part sharing the most nets with `ref`."""
    neighbors = adjacency.get(ref, {})
    best_ref, best_count, best_usage = None, 0, float("inf")
    for other_ref, count in neighbors.items():
        if other_ref not in placed_map:
            continue
        if candidate_refs is not None and other_ref not in candidate_refs:
            continue
        usage = (usage_counts or {}).get(other_ref, 0)
        if count > best_count or (count == best_count and usage < best_usage):
            best_count = count
            best_ref = other_ref
            best_usage = usage
    return best_ref


def _largest_ic_ref(group: PlacementGroup) -> Optional[str]:
    """Return ref of the part with the most pins (tie: first encountered)."""
    best_ref, best_pins = None, -1
    for part in group.parts:
        n = len(part)
        if n > best_pins:
            best_pins = n
            best_ref = part.ref
    return best_ref


def place_parts(
    groups: dict,
    constraints: LayoutConstraints,
    fp_bboxes: dict[str, tuple[float, float]],
) -> list[PlacedPart]:
    """Place all parts, honoring fixed positions and filling in the rest."""

    fixed_map = {fp.ref: fp for fp in (constraints.fixed or [])}
    edge_map = {ea.ref: ea for ea in (constraints.edge_anchors or [])}

    # placed_map: ref → PlacedPart
    placed_map: dict[str, PlacedPart] = {}
    # occupied: list of (x, y, w, h) tuples for overlap checks
    occupied: list[tuple] = []

    # Seed keepout zones as occupied regions
    for ko in (constraints.keepouts or []):
        cx = (ko.x_min + ko.x_max) / 2
        cy = (ko.y_min + ko.y_max) / 2
        w = ko.x_max - ko.x_min
        h = ko.y_max - ko.y_min
        occupied.append((cx, cy, w, h))

    def _commit(pp: PlacedPart, w: float, h: float):
        placed_map[pp.ref] = pp
        occupied.append((pp.x_mm, pp.y_mm, w, h))

    all_parts = []
    for group in groups.values():
        for part in group.parts:
            all_parts.append((part, group))

    primary_refs = {
        part.ref for part, _ in all_parts if _is_primary_part(part)
    }

    # Layer 1: fixed positions
    for part, group in all_parts:
        if part.ref in fixed_map:
            fp_constraint = fixed_map[part.ref]
            w, h = _bbox(part, fp_bboxes)
            pp = PlacedPart(
                ref=part.ref,
                x_mm=fp_constraint.x_mm,
                y_mm=fp_constraint.y_mm,
                rot_deg=fp_constraint.rot_deg,
                footprint=_footprint_name(part),
            )
            _commit(pp, w, h)

    # Layer 2: explicit edge anchors, e.g. USB jacks that must meet the outline.
    for part, group in all_parts:
        if part.ref in placed_map or part.ref not in edge_map:
            continue
        w, h = _bbox(part, fp_bboxes)
        target_x, target_y, rot = _edge_anchor_position(
            edge_map[part.ref], w, h, constraints.outline
        )
        bounds = constraints.outline
        target_x, target_y = _clamp_to_bounds(target_x, target_y, w, h, bounds)
        x, y = _find_clear_position(
            target_x, target_y, w, h, occupied, bounds=bounds
        )
        x, y = _clamp_to_bounds(x, y, w, h, bounds)
        _commit(
            PlacedPart(
                ref=part.ref,
                x_mm=x,
                y_mm=y,
                rot_deg=rot,
                footprint=_footprint_name(part),
            ),
            w,
            h,
        )

    # Layer 3: primary parts before passives. This gives capacitors and
    # resistors meaningful parent candidates instead of only the fixed refs.
    for group in groups.values():
        anchor_ref = _largest_ic_ref(group)
        for part in group.parts:
            if part.ref in placed_map or not _is_primary_part(part):
                continue
            w, h = _bbox(part, fp_bboxes)
            bounds = _bounds_for_part(part, group, constraints)
            if anchor_ref and anchor_ref in placed_map:
                anchor = placed_map[anchor_ref]
                aw, ah = _bbox_for_ref(anchor_ref, all_parts, fp_bboxes)
                target_x = anchor.x_mm - aw / 2 - w / 2 - 2.0
                target_y = anchor.y_mm
            elif bounds is not None:
                target_x, target_y = _bounds_center(bounds)
            else:
                target_x, target_y = _spillover_position(placed_map, constraints)
            target_x, target_y = _clamp_to_bounds(target_x, target_y, w, h, bounds)
            x, y = _find_clear_position(
                target_x, target_y, w, h, occupied, bounds=bounds
            )
            x, y = _clamp_to_bounds(x, y, w, h, bounds)
            _commit(
                PlacedPart(
                    ref=part.ref,
                    x_mm=x,
                    y_mm=y,
                    rot_deg=0.0,
                    footprint=_footprint_name(part),
                ),
                w,
                h,
            )

    # Layer 4: decoupling caps
    decap_parent_counts: dict[str, int] = {}
    for part, group in all_parts:
        if part.ref in placed_map:
            continue
        if not _is_decoupling_cap(part):
            continue
        w, h = _bbox(part, fp_bboxes)
        bounds = _bounds_for_part(part, group, constraints)
        parent_ref = _most_adjacent_placed(
            part.ref,
            group.adjacency,
            placed_map,
            candidate_refs=primary_refs,
            usage_counts=decap_parent_counts,
        )
        if parent_ref:
            decap_parent_counts[parent_ref] = (
                decap_parent_counts.get(parent_ref, 0) + 1
            )
            parent = placed_map[parent_ref]
            pw, ph = _bbox_for_ref(parent_ref, all_parts, fp_bboxes)
            target_x = parent.x_mm + pw / 2 + w / 2 + 1.5
            target_y = parent.y_mm
            rot = parent.rot_deg
        else:
            target_x, target_y = (
                _bounds_center(bounds)
                if bounds is not None
                else _spillover_position(placed_map, constraints)
            )
            rot = 0.0
        target_x, target_y = _clamp_to_bounds(target_x, target_y, w, h, bounds)
        x, y = _find_clear_position(
            target_x, target_y, w, h, occupied, bounds=bounds
        )
        x, y = _clamp_to_bounds(x, y, w, h, bounds)
        _commit(PlacedPart(ref=part.ref, x_mm=x, y_mm=y, rot_deg=rot,
                           footprint=_footprint_name(part)), w, h)

    # Layer 5: signal passives (2-pin, not decoupling caps)
    # Track how many passives have been stacked per parent ref
    stack_count: dict[str, int] = {}
    for part, group in all_parts:
        if part.ref in placed_map:
            continue
        if len(part) != 2:
            continue
        w, h = _bbox(part, fp_bboxes)
        bounds = _bounds_for_part(part, group, constraints)
        parent_ref = _most_adjacent_placed(
            part.ref,
            group.adjacency,
            placed_map,
            candidate_refs=primary_refs,
            usage_counts=stack_count,
        )
        if parent_ref:
            parent = placed_map[parent_ref]
            pw, ph = _bbox_for_ref(parent_ref, all_parts, fp_bboxes)
            n = stack_count.get(parent_ref, 0)
            stack_count[parent_ref] = n + 1
            # Stack below the parent, offset by (n+1) steps
            target_x = parent.x_mm
            target_y = parent.y_mm + ph / 2 + h / 2 + 1.0 + n * (h + 1.0)
            rot = parent.rot_deg
        else:
            target_x, target_y = (
                _bounds_center(bounds)
                if bounds is not None
                else _spillover_position(placed_map, constraints)
            )
            rot = 0.0
        target_x, target_y = _clamp_to_bounds(target_x, target_y, w, h, bounds)
        x, y = _find_clear_position(
            target_x, target_y, w, h, occupied, bounds=bounds
        )
        x, y = _clamp_to_bounds(x, y, w, h, bounds)
        _commit(PlacedPart(ref=part.ref, x_mm=x, y_mm=y, rot_deg=rot,
                           footprint=_footprint_name(part)), w, h)

    return list(placed_map.values())


def _bbox_for_ref(ref: str, all_parts, fp_bboxes: dict) -> tuple[float, float]:
    for part, _ in all_parts:
        if part.ref == ref:
            return _bbox(part, fp_bboxes)
    return _DEFAULT_BBOX


def derive_outline(
    placed_parts: list[PlacedPart],
    fp_bboxes: dict[str, tuple[float, float]],
    margin_mm: float = 3.0,
) -> BoardOutline:
    """Return a rectangular outline enclosing placed parts plus margin."""
    if not placed_parts:
        return BoardOutline(50.0, 50.0)

    x_min = float("inf")
    y_min = float("inf")
    x_max = float("-inf")
    y_max = float("-inf")
    for pp in placed_parts:
        w, h = fp_bboxes.get(pp.footprint, _DEFAULT_BBOX)
        x_min = min(x_min, pp.x_mm - w / 2)
        y_min = min(y_min, pp.y_mm - h / 2)
        x_max = max(x_max, pp.x_mm + w / 2)
        y_max = max(y_max, pp.y_mm + h / 2)

    x_min -= margin_mm
    y_min -= margin_mm
    x_max += margin_mm
    y_max += margin_mm
    return BoardOutline(
        vertices=[
            (x_min, y_min),
            (x_max, y_min),
            (x_max, y_max),
            (x_min, y_max),
        ]
    )


def _spillover_position(placed_map: dict, constraints) -> tuple[float, float]:
    """Find a position in the spillover area below all placed parts."""
    if not placed_map:
        return 10.0, 10.0
    max_y = max(pp.y_mm for pp in placed_map.values())
    avg_x = sum(pp.x_mm for pp in placed_map.values()) / len(placed_map)
    if constraints.outline:
        return avg_x, max_y + 10.0
    return avg_x, max_y + 10.0
