"""Phase 6: Deterministic local placement refinement.

After a candidate is selected, this module tries small moves, rotations,
and compatible swaps to improve the placement score without violating
constraints. Every step is deterministic — no randomness.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .constraints import (
    BoardOutline,
    KeepOut,
    LayoutConstraints,
)
from .geometry import FootprintGeometry
from .placer import _bbox, _clamp_to_outline, _overlaps_any
from .scoring import LayoutScore, score_placement
from .writer import PlacedPart


@dataclass
class RefinementResult:
    improved: bool
    iterations: int
    score_before: float
    score_after: float
    moves: list[str] = field(default_factory=list)

    def summary(self) -> str:
        delta = self.score_after - self.score_before
        if not self.improved:
            return f"Refinement: no improvement after {self.iterations} iterations"
        return (
            f"Refinement: +{delta:.1f} score ({self.score_before:.1f} -> "
            f"{self.score_after:.1f}) in {self.iterations} iterations, "
            f"{len(self.moves)} moves"
        )


def _locked_refs(constraints: LayoutConstraints | None) -> set[str]:
    if constraints is None:
        return set()
    locked = {f.ref for f in constraints.fixed or []}
    locked.update(a.ref for a in constraints.edge_anchors or [])
    locked.update(f.ref for f in constraints.face_edges or [])
    return locked


def _occupied_list(
    placed_parts: list[PlacedPart],
    fp_bboxes: dict[str, tuple[float, float]],
    exclude_ref: str | None = None,
) -> list[tuple[float, float, float, float]]:
    occupied = []
    for p in placed_parts:
        if p.ref == exclude_ref:
            continue
        w, h = fp_bboxes.get(p.footprint, (2.0, 1.25))
        occupied.append((p.x_mm, p.y_mm, w, h))
    return occupied


def _outline_bounds(outline: BoardOutline | None):
    if outline is None or not outline.vertices:
        return None
    return (outline.x_min, outline.y_min, outline.x_max, outline.y_max)


def _try_move(
    part: PlacedPart,
    dx: float,
    dy: float,
    fp_bboxes: dict[str, tuple[float, float]],
    occupied: list[tuple],
    outline: BoardOutline | None,
    clearance: float = 0.5,
) -> PlacedPart | None:
    """Try moving a part by (dx, dy). Return new PlacedPart if valid, else None."""
    nx, ny = part.x_mm + dx, part.y_mm + dy
    w, h = fp_bboxes.get(part.footprint, (2.0, 1.25))

    if outline is not None and outline.vertices:
        nx, ny = _clamp_to_outline(nx, ny, w, h, outline)

    if _overlaps_any(nx, ny, w, h, occupied, clearance):
        return None

    return PlacedPart(
        ref=part.ref, x_mm=nx, y_mm=ny,
        rot_deg=part.rot_deg, footprint=part.footprint,
    )


def _try_swap(
    a: PlacedPart,
    b: PlacedPart,
    fp_bboxes: dict[str, tuple[float, float]],
    all_occupied: list[tuple],
    outline: BoardOutline | None,
    clearance: float = 0.5,
) -> tuple[PlacedPart, PlacedPart] | None:
    """Try swapping positions of two parts. Return new pair if valid."""
    wa, ha = fp_bboxes.get(a.footprint, (2.0, 1.25))
    wb, hb = fp_bboxes.get(b.footprint, (2.0, 1.25))

    na = PlacedPart(ref=a.ref, x_mm=b.x_mm, y_mm=b.y_mm,
                    rot_deg=a.rot_deg, footprint=a.footprint)
    nb = PlacedPart(ref=b.ref, x_mm=a.x_mm, y_mm=a.y_mm,
                    rot_deg=b.rot_deg, footprint=b.footprint)

    exclude = {a.ref, b.ref}
    filtered = [(x, y, w, h) for x, y, w, h in all_occupied
                if not any(abs(x - p.x_mm) < 0.01 and abs(y - p.y_mm) < 0.01
                           for p in (a, b))]

    if _overlaps_any(na.x_mm, na.y_mm, wa, ha, filtered, clearance):
        return None
    filtered.append((na.x_mm, na.y_mm, wa, ha))
    if _overlaps_any(nb.x_mm, nb.y_mm, wb, hb, filtered, clearance):
        return None

    return na, nb


def _try_rotation(
    part: PlacedPart,
    angles: tuple[float, ...] = (90.0, 180.0, 270.0),
) -> list[PlacedPart]:
    """Generate rotation candidates (position unchanged)."""
    results = []
    for angle in angles:
        new_rot = (part.rot_deg + angle) % 360.0
        if abs(new_rot - part.rot_deg) > 0.1:
            results.append(PlacedPart(
                ref=part.ref, x_mm=part.x_mm, y_mm=part.y_mm,
                rot_deg=new_rot, footprint=part.footprint,
            ))
    return results


_MOVE_OFFSETS = [
    (1.0, 0.0), (-1.0, 0.0), (0.0, 1.0), (0.0, -1.0),
    (0.5, 0.0), (-0.5, 0.0), (0.0, 0.5), (0.0, -0.5),
    (2.0, 0.0), (-2.0, 0.0), (0.0, 2.0), (0.0, -2.0),
]


def _compatible_for_swap(a: PlacedPart, b: PlacedPart) -> bool:
    """Two parts are swap-compatible if they have the same footprint."""
    return a.footprint == b.footprint


def refine_placement(
    placed_parts: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    constraints: LayoutConstraints | None = None,
    outline: BoardOutline | None = None,
    keepouts: list[KeepOut] | None = None,
    fp_geometries: dict[str, FootprintGeometry] | None = None,
    clearance_mm: float = 0.5,
    board_layers: int = 2,
    max_iterations: int = 5,
) -> RefinementResult:
    """Deterministic local refinement loop.

    Tries small moves, rotations, and compatible swaps.
    Only accepts changes that improve the score.
    """
    locked = _locked_refs(constraints)
    parts = list(placed_parts)
    by_ref = {p.ref: i for i, p in enumerate(parts)}

    def _score(part_list):
        return score_placement(
            part_list, circuit, fp_bboxes,
            outline=outline, keepouts=keepouts,
            fp_geometries=fp_geometries,
            clearance_mm=clearance_mm,
            board_layers=board_layers,
        )

    current_score = _score(parts)
    initial_score = current_score.score
    moves = []

    for iteration in range(max_iterations):
        improved_this_round = False
        unlocked = [p for p in parts if p.ref not in locked]

        for part in unlocked:
            idx = by_ref[part.ref]
            occupied = _occupied_list(parts, fp_bboxes, exclude_ref=part.ref)

            best_candidate = None
            best_score = current_score.score

            for dx, dy in _MOVE_OFFSETS:
                moved = _try_move(
                    part, dx, dy, fp_bboxes, occupied, outline, clearance_mm,
                )
                if moved is None:
                    continue
                trial = list(parts)
                trial[idx] = moved
                trial_score = _score(trial)
                if trial_score.score > best_score:
                    best_score = trial_score.score
                    best_candidate = moved

            for rotated in _try_rotation(part):
                trial = list(parts)
                trial[idx] = rotated
                trial_score = _score(trial)
                if trial_score.score > best_score:
                    best_score = trial_score.score
                    best_candidate = rotated

            if best_candidate is not None:
                parts[idx] = best_candidate
                current_score = _score(parts)
                moves.append(
                    f"{part.ref}: moved/rotated (+{best_score - initial_score:.1f})"
                )
                improved_this_round = True

        unlocked_indices = [by_ref[p.ref] for p in unlocked]
        for i, ai in enumerate(unlocked_indices):
            for bi in unlocked_indices[i + 1:]:
                a, b = parts[ai], parts[bi]
                if not _compatible_for_swap(a, b):
                    continue
                occupied = _occupied_list(parts, fp_bboxes)
                swapped = _try_swap(a, b, fp_bboxes, occupied, outline, clearance_mm)
                if swapped is None:
                    continue
                trial = list(parts)
                trial[ai], trial[bi] = swapped
                trial_score = _score(trial)
                if trial_score.score > current_score.score:
                    parts[ai], parts[bi] = swapped
                    current_score = trial_score
                    moves.append(
                        f"swap {a.ref} <-> {b.ref} (+{trial_score.score - initial_score:.1f})"
                    )
                    improved_this_round = True

        if not improved_this_round:
            break

    placed_parts.clear()
    placed_parts.extend(parts)

    return RefinementResult(
        improved=current_score.score > initial_score,
        iterations=iteration + 1 if max_iterations > 0 else 0,
        score_before=initial_score,
        score_after=current_score.score,
        moves=moves,
    )
