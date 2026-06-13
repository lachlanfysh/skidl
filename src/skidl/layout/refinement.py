from __future__ import annotations

import math
from dataclasses import dataclass, field

from .candidates import PlacementCandidate
from .constraints import LayoutConstraints
from .geometry import FootprintGeometry
from .placer import _find_clear_position
from .roles import GND_NET_RE, POWER_NET_RE, classify_parts
from .scoring import LayoutScore, score_placement
from .validator import validate
from .writer import PlacedPart


@dataclass
class RefinementResult:
    placed_parts: list[PlacedPart]
    start_score: float
    final_score: float
    accepted_moves: int = 0
    accepted_rotations: int = 0
    accepted_swaps: int = 0
    ref_reasons: dict[str, list[str]] = field(default_factory=dict)

    @property
    def accepted_count(self) -> int:
        return self.accepted_moves + self.accepted_rotations + self.accepted_swaps


def _clone_placed(placed_parts: list[PlacedPart]) -> list[PlacedPart]:
    return [
        PlacedPart(
            ref=part.ref,
            x_mm=part.x_mm,
            y_mm=part.y_mm,
            rot_deg=part.rot_deg,
            footprint=part.footprint,
        )
        for part in placed_parts
    ]


def _score(
    placed_parts: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    constraints: LayoutConstraints | None,
    fp_geometries: dict[str, FootprintGeometry] | None,
    clearance_mm: float,
    board_layers: int,
) -> LayoutScore:
    return score_placement(
        placed_parts,
        circuit,
        fp_bboxes,
        outline=constraints.outline if constraints is not None else None,
        keepouts=constraints.keepouts if constraints is not None else None,
        fp_geometries=fp_geometries,
        clearance_mm=clearance_mm,
        board_layers=board_layers,
    )


def _hard_count(score: LayoutScore) -> int:
    return (
        score.overlap_count
        + score.outline_violation_count
        + score.keepout_violation_count
        + score.missing_count
    )


def _is_better(current: LayoutScore, trial: LayoutScore) -> bool:
    current_hard = _hard_count(current)
    trial_hard = _hard_count(trial)
    if trial_hard < current_hard:
        return True
    if trial_hard > current_hard:
        return False
    return trial.score > current.score + 1e-6


def _replace_ref(
    placed_parts: list[PlacedPart],
    ref: str,
    replacement: PlacedPart,
) -> list[PlacedPart]:
    return [replacement if part.ref == ref else part for part in placed_parts]


def _replace_refs(
    placed_parts: list[PlacedPart],
    replacements: dict[str, PlacedPart],
) -> list[PlacedPart]:
    return [replacements.get(part.ref, part) for part in placed_parts]


def _part_dimensions(
    placed: PlacedPart,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None,
) -> tuple[float, float]:
    geometry = (fp_geometries or {}).get(placed.footprint)
    if geometry is not None:
        x_min, y_min, x_max, y_max = geometry.transformed_bounds(
            PlacedPart(placed.ref, 0.0, 0.0, placed.rot_deg, placed.footprint)
        )
        return x_max - x_min, y_max - y_min
    return fp_bboxes.get(placed.footprint, (2.0, 2.0))


def _bounds_for_ref(ref: str, constraints: LayoutConstraints | None):
    if constraints is None:
        return None
    for zone in constraints.zones or []:
        if ref in (zone.refs or []):
            return zone
    return constraints.outline


def _bounds_key(bounds) -> tuple[float, float, float, float] | None:
    if bounds is None:
        return None
    return (bounds.x_min, bounds.y_min, bounds.x_max, bounds.y_max)


def _clamp_to_bounds(
    x_mm: float,
    y_mm: float,
    width_mm: float,
    height_mm: float,
    bounds,
) -> tuple[float, float]:
    if bounds is None:
        return x_mm, y_mm
    half_w = width_mm / 2
    half_h = height_mm / 2
    return (
        max(bounds.x_min + half_w, min(bounds.x_max - half_w, x_mm)),
        max(bounds.y_min + half_h, min(bounds.y_max - half_h, y_mm)),
    )


def _locked_position_refs(constraints: LayoutConstraints | None) -> set[str]:
    if constraints is None:
        return set()
    locked = {fixed.ref for fixed in constraints.fixed or []}
    locked.update(anchor.ref for anchor in constraints.edge_anchors or [])
    return locked


def _locked_rotation_refs(constraints: LayoutConstraints | None) -> set[str]:
    if constraints is None:
        return set()
    locked = _locked_position_refs(constraints)
    locked.update(face.ref for face in constraints.face_edges or [])
    return locked


def _decap_refs(circuit) -> set[str]:
    if circuit is None:
        return set()
    return {
        ref
        for ref, role in classify_parts(circuit).items()
        if role.role == "decoupling_cap"
    }


def _net_weight(name: str) -> float:
    if GND_NET_RE.match(name):
        return 2.0
    if POWER_NET_RE.match(name):
        return 1.7
    upper = name.upper()
    if any(token in upper for token in ("USB", "D+", "D-", "CLK", "XTAL")):
        return 1.5
    return 1.0


def _ref_neighbors(circuit, placed_by_ref: dict[str, PlacedPart]):
    neighbors: dict[str, list[tuple[str, float]]] = {}
    degrees: dict[str, int] = {}
    if circuit is None:
        return neighbors, degrees

    try:
        from skidl.net import NCNet
    except Exception:
        NCNet = None

    for net in circuit.get_nets():
        if NCNet is not None and isinstance(net, NCNet):
            continue
        refs: list[str] = []
        for pin in net.get_pins():
            ref = getattr(getattr(pin, "part", None), "ref", None)
            if ref in placed_by_ref and ref not in refs:
                refs.append(ref)
        if len(refs) < 2:
            continue
        weight = _net_weight(str(getattr(net, "name", "") or ""))
        for ref in refs:
            others = [other for other in refs if other != ref]
            degrees[ref] = degrees.get(ref, 0) + len(others)
            for other in others:
                neighbors.setdefault(ref, []).append((other, weight))
    return neighbors, degrees


def _neighbor_centroid(
    ref: str,
    neighbors: dict[str, list[tuple[str, float]]],
    placed_by_ref: dict[str, PlacedPart],
) -> tuple[float, float] | None:
    weighted = neighbors.get(ref, [])
    if not weighted:
        return None
    total = 0.0
    x_sum = 0.0
    y_sum = 0.0
    for other_ref, weight in weighted:
        other = placed_by_ref.get(other_ref)
        if other is None:
            continue
        total += weight
        x_sum += other.x_mm * weight
        y_sum += other.y_mm * weight
    if total <= 0:
        return None
    return x_sum / total, y_sum / total


def _move_trials(
    placed: PlacedPart,
    centroid: tuple[float, float],
    width_mm: float,
    height_mm: float,
    bounds,
) -> list[PlacedPart]:
    dx = centroid[0] - placed.x_mm
    dy = centroid[1] - placed.y_mm
    distance = math.hypot(dx, dy)
    if distance <= 1e-6:
        return []

    unit_x = dx / distance
    unit_y = dy / distance
    sign_x = 1.0 if dx > 0 else -1.0
    sign_y = 1.0 if dy > 0 else -1.0
    trials: list[PlacedPart] = []
    seen: set[tuple[float, float]] = set()

    directions = [(unit_x, unit_y)]
    if abs(dx) > 1e-6:
        directions.append((sign_x, 0.0))
    if abs(dy) > 1e-6:
        directions.append((0.0, sign_y))

    for step_mm in (6.0, 3.0, 1.0):
        for dir_x, dir_y in directions:
            step = min(step_mm, distance)
            x_mm = placed.x_mm + dir_x * step
            y_mm = placed.y_mm + dir_y * step
            x_mm, y_mm = _clamp_to_bounds(
                x_mm,
                y_mm,
                width_mm,
                height_mm,
                bounds,
            )
            key = (round(x_mm, 4), round(y_mm, 4))
            if key in seen or key == (round(placed.x_mm, 4), round(placed.y_mm, 4)):
                continue
            seen.add(key)
            trials.append(
                PlacedPart(
                    ref=placed.ref,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    rot_deg=placed.rot_deg,
                    footprint=placed.footprint,
                )
            )
    return trials


def _rotation_trials(placed: PlacedPart) -> list[PlacedPart]:
    trials = []
    for rotation in (0.0, 90.0, 180.0, 270.0):
        if abs(rotation - placed.rot_deg) <= 1e-6:
            continue
        trials.append(
            PlacedPart(
                ref=placed.ref,
                x_mm=placed.x_mm,
                y_mm=placed.y_mm,
                rot_deg=rotation,
                footprint=placed.footprint,
            )
        )
    return trials


def _best_single_ref_trial(
    placed_parts: list[PlacedPart],
    current_score: LayoutScore,
    ref: str,
    trials: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    constraints: LayoutConstraints | None,
    fp_geometries: dict[str, FootprintGeometry] | None,
    clearance_mm: float,
    board_layers: int,
) -> tuple[list[PlacedPart], LayoutScore, PlacedPart] | None:
    best_parts = None
    best_score = current_score
    best_trial = None
    for trial in trials:
        trial_parts = _replace_ref(placed_parts, ref, trial)
        trial_score = _score(
            trial_parts,
            circuit,
            fp_bboxes,
            constraints,
            fp_geometries,
            clearance_mm,
            board_layers,
        )
        if _is_better(best_score, trial_score):
            best_parts = trial_parts
            best_score = trial_score
            best_trial = trial
    if best_parts is None or best_trial is None:
        return None
    return best_parts, best_score, best_trial


def _occupied_without_ref(
    placed_parts: list[PlacedPart],
    ref: str,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None,
    constraints: LayoutConstraints | None,
) -> list[tuple[float, float, float, float]]:
    occupied: list[tuple[float, float, float, float]] = []
    if constraints is not None:
        for keepout in constraints.keepouts or []:
            occupied.append(
                (
                    (keepout.x_min + keepout.x_max) / 2,
                    (keepout.y_min + keepout.y_max) / 2,
                    keepout.x_max - keepout.x_min,
                    keepout.y_max - keepout.y_min,
                )
            )
    for part in placed_parts:
        if part.ref == ref:
            continue
        width_mm, height_mm = _part_dimensions(part, fp_bboxes, fp_geometries)
        occupied.append((part.x_mm, part.y_mm, width_mm, height_mm))
    return occupied


def _clearance_search_radius(bounds) -> float:
    if bounds is None:
        return 160.0
    return max(80.0, bounds.x_max - bounds.x_min, bounds.y_max - bounds.y_min)


def _legalize_one_overlap(
    placed_parts: list[PlacedPart],
    current_score: LayoutScore,
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    constraints: LayoutConstraints | None,
    fp_geometries: dict[str, FootprintGeometry] | None,
    clearance_mm: float,
    board_layers: int,
    position_locked: set[str],
    degrees: dict[str, int],
) -> tuple[list[PlacedPart], LayoutScore, str, str] | None:
    validation = validate(
        placed_parts,
        circuit,
        fp_bboxes,
        clearance_mm=clearance_mm,
        outline=constraints.outline if constraints is not None else None,
        keepouts=constraints.keepouts if constraints is not None else None,
        fp_geometries=fp_geometries,
    )
    if not validation.overlaps:
        return None

    placed_by_ref = {part.ref: part for part in placed_parts}
    for ref_a, ref_b in validation.overlaps:
        candidates = [
            ref
            for ref in (ref_a, ref_b)
            if ref in placed_by_ref and ref not in position_locked
        ]
        candidates.sort(key=lambda ref: (degrees.get(ref, 0), ref))
        for ref in candidates:
            placed = placed_by_ref[ref]
            width_mm, height_mm = _part_dimensions(
                placed,
                fp_bboxes,
                fp_geometries,
            )
            bounds = _bounds_for_ref(ref, constraints)
            x_mm, y_mm = _find_clear_position(
                placed.x_mm,
                placed.y_mm,
                width_mm,
                height_mm,
                _occupied_without_ref(
                    placed_parts,
                    ref,
                    fp_bboxes,
                    fp_geometries,
                    constraints,
                ),
                bounds=bounds,
                step=1.0,
                max_radius=_clearance_search_radius(bounds),
            )
            x_mm, y_mm = _clamp_to_bounds(
                x_mm,
                y_mm,
                width_mm,
                height_mm,
                bounds,
            )
            if (
                abs(x_mm - placed.x_mm) <= 1e-6
                and abs(y_mm - placed.y_mm) <= 1e-6
            ):
                continue
            trial = PlacedPart(
                ref=placed.ref,
                x_mm=x_mm,
                y_mm=y_mm,
                rot_deg=placed.rot_deg,
                footprint=placed.footprint,
            )
            trial_parts = _replace_ref(placed_parts, ref, trial)
            trial_score = _score(
                trial_parts,
                circuit,
                fp_bboxes,
                constraints,
                fp_geometries,
                clearance_mm,
                board_layers,
            )
            if _is_better(current_score, trial_score):
                other = ref_b if ref == ref_a else ref_a
                reason = (
                    f"legalized overlap with {other} by moving to "
                    f"({x_mm:.1f},{y_mm:.1f})"
                )
                return trial_parts, trial_score, ref, reason
    return None


def _same_swap_class(
    a: PlacedPart,
    b: PlacedPart,
    constraints: LayoutConstraints | None,
) -> bool:
    if a.footprint != b.footprint:
        return False
    return _bounds_key(_bounds_for_ref(a.ref, constraints)) == _bounds_key(
        _bounds_for_ref(b.ref, constraints)
    )


def refine_placement(
    placed_parts: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    constraints: LayoutConstraints | None = None,
    fp_geometries: dict[str, FootprintGeometry] | None = None,
    clearance_mm: float = 0.5,
    board_layers: int = 2,
    max_passes: int = 1,
    max_movable_refs: int = 32,
    max_pair_swaps: int = 16,
    max_legalization_moves: int = 16,
) -> RefinementResult:
    """Apply deterministic score-gated local placement adjustments."""
    current_parts = _clone_placed(placed_parts)
    current_score = _score(
        current_parts,
        circuit,
        fp_bboxes,
        constraints,
        fp_geometries,
        clearance_mm,
        board_layers,
    )
    start_score = current_score.score
    position_locked = _locked_position_refs(constraints)
    position_locked.update(_decap_refs(circuit))
    rotation_locked = _locked_rotation_refs(constraints)
    accepted_moves = 0
    accepted_rotations = 0
    accepted_swaps = 0
    ref_reasons: dict[str, list[str]] = {}

    for _ in range(max_passes):
        changed = False
        placed_by_ref = {part.ref: part for part in current_parts}
        neighbors, degrees = _ref_neighbors(circuit, placed_by_ref)
        movable_refs = [
            part.ref for part in current_parts if part.ref not in position_locked
        ]
        movable_refs.sort(key=lambda ref: (-degrees.get(ref, 0), ref))
        movable_refs = movable_refs[:max_movable_refs]

        for ref in movable_refs:
            placed_by_ref = {part.ref: part for part in current_parts}
            placed = placed_by_ref[ref]
            width_mm, height_mm = _part_dimensions(
                placed,
                fp_bboxes,
                fp_geometries,
            )
            centroid = _neighbor_centroid(ref, neighbors, placed_by_ref)
            if centroid is not None:
                move_trials = _move_trials(
                    placed,
                    centroid,
                    width_mm,
                    height_mm,
                    _bounds_for_ref(ref, constraints),
                )
                best = _best_single_ref_trial(
                    current_parts,
                    current_score,
                    ref,
                    move_trials,
                    circuit,
                    fp_bboxes,
                    constraints,
                    fp_geometries,
                    clearance_mm,
                    board_layers,
                )
                if best is not None:
                    current_parts, current_score, trial = best
                    accepted_moves += 1
                    changed = True
                    ref_reasons.setdefault(ref, []).append(
                        (
                            "locally moved toward connected-net centroid "
                            f"({trial.x_mm:.1f},{trial.y_mm:.1f})"
                        )
                    )
                    placed = trial

            if (
                ref in rotation_locked
                or not fp_geometries
                or placed.footprint not in fp_geometries
            ):
                continue
            best = _best_single_ref_trial(
                current_parts,
                current_score,
                ref,
                _rotation_trials(placed),
                circuit,
                fp_bboxes,
                constraints,
                fp_geometries,
                clearance_mm,
                board_layers,
            )
            if best is not None:
                current_parts, current_score, trial = best
                accepted_rotations += 1
                changed = True
                ref_reasons.setdefault(ref, []).append(
                    f"locally rotated to {trial.rot_deg:.0f} deg after scoring"
                )

        placed_by_ref = {part.ref: part for part in current_parts}
        swap_attempts = 0
        for idx, ref_a in enumerate(movable_refs):
            if swap_attempts >= max_pair_swaps:
                break
            a = placed_by_ref.get(ref_a)
            if a is None:
                continue
            for ref_b in movable_refs[idx + 1:]:
                if swap_attempts >= max_pair_swaps:
                    break
                b = placed_by_ref.get(ref_b)
                if b is None or not _same_swap_class(a, b, constraints):
                    continue
                swap_attempts += 1
                trial_a = PlacedPart(a.ref, b.x_mm, b.y_mm, a.rot_deg, a.footprint)
                trial_b = PlacedPart(b.ref, a.x_mm, a.y_mm, b.rot_deg, b.footprint)
                trial_parts = _replace_refs(
                    current_parts,
                    {a.ref: trial_a, b.ref: trial_b},
                )
                trial_score = _score(
                    trial_parts,
                    circuit,
                    fp_bboxes,
                    constraints,
                    fp_geometries,
                    clearance_mm,
                    board_layers,
                )
                if not _is_better(current_score, trial_score):
                    continue
                current_parts = trial_parts
                current_score = trial_score
                placed_by_ref = {part.ref: part for part in current_parts}
                accepted_swaps += 1
                changed = True
                ref_reasons.setdefault(a.ref, []).append(
                    f"locally swapped position with {b.ref}"
                )
                ref_reasons.setdefault(b.ref, []).append(
                    f"locally swapped position with {a.ref}"
                )
                break

        legalizations = 0
        while legalizations < max_legalization_moves:
            placed_by_ref = {part.ref: part for part in current_parts}
            neighbors, degrees = _ref_neighbors(circuit, placed_by_ref)
            legalized = _legalize_one_overlap(
                current_parts,
                current_score,
                circuit,
                fp_bboxes,
                constraints,
                fp_geometries,
                clearance_mm,
                board_layers,
                position_locked,
                degrees,
            )
            if legalized is None:
                break
            current_parts, current_score, moved_ref, reason = legalized
            accepted_moves += 1
            legalizations += 1
            changed = True
            ref_reasons.setdefault(moved_ref, []).append(reason)

        if not changed:
            break

    return RefinementResult(
        placed_parts=current_parts,
        start_score=start_score,
        final_score=current_score.score,
        accepted_moves=accepted_moves,
        accepted_rotations=accepted_rotations,
        accepted_swaps=accepted_swaps,
        ref_reasons=ref_reasons,
    )


def refine_candidate_placement(
    candidate: PlacementCandidate,
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    fp_geometries: dict[str, FootprintGeometry] | None = None,
    clearance_mm: float = 0.5,
    board_layers: int = 2,
) -> RefinementResult:
    result = refine_placement(
        candidate.placed_parts,
        circuit,
        fp_bboxes,
        constraints=candidate.constraints,
        fp_geometries=fp_geometries,
        clearance_mm=clearance_mm,
        board_layers=board_layers,
    )
    if result.accepted_count == 0:
        return result

    candidate.placed_parts = result.placed_parts
    candidate.reasons.append(
        (
            f"local refinement accepted {result.accepted_count} "
            f"score-gated adjustment(s): "
            f"{result.start_score:.1f} -> {result.final_score:.1f}"
        )
    )
    for ref, reasons in result.ref_reasons.items():
        candidate.ref_reasons.setdefault(ref, []).extend(reasons)
    return result
