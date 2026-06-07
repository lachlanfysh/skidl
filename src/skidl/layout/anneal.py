"""Simulated annealing placement refinement.

Replaces the greedy local search with a temperature-controlled stochastic
optimizer that can escape local minima. Uses a fast inner-loop scorer
(HPWL + overlaps + outline + decap distance) for speed, and the full
scorer for final comparison.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

from .constraints import BoardOutline, KeepOut, LayoutConstraints
from .geometry import FootprintGeometry
from .placer import _clamp_to_outline
from .roles import classify_parts, pin_net_names, GND_NET_RE, POWER_NET_RE
from .writer import PlacedPart


@dataclass
class AnnealConfig:
    initial_temp: float = 30.0
    cooling_rate: float = 0.97
    min_temp: float = 0.1
    moves_per_temp: int | None = None
    seed: int = 42
    max_move_mm: float | None = None
    move_prob: float = 0.65
    rotate_prob: float = 0.20


@dataclass
class AnnealResult:
    improved: bool
    iterations: int
    accepted_moves: int
    rejected_moves: int
    score_before: float
    score_after: float
    best_score: float
    temperature_steps: int
    final_temp: float
    moves: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if not self.improved:
            return (
                f"Annealing: no improvement after {self.iterations} moves "
                f"({self.temperature_steps} temp steps)"
            )
        delta = self.score_after - self.score_before
        accept_rate = (
            self.accepted_moves / max(self.accepted_moves + self.rejected_moves, 1) * 100
        )
        return (
            f"Annealing: +{delta:.1f} score ({self.score_before:.1f} → "
            f"{self.score_after:.1f}) over {self.iterations} moves, "
            f"{self.accepted_moves} accepted ({accept_rate:.0f}%), "
            f"{self.temperature_steps} temp steps"
        )


def _locked_refs(constraints: LayoutConstraints | None) -> set[str]:
    if constraints is None:
        return set()
    locked = {f.ref for f in constraints.fixed or []}
    locked.update(a.ref for a in constraints.edge_anchors or [])
    locked.update(f.ref for f in constraints.face_edges or [])
    return locked


def _net_weight(name: str) -> float:
    if GND_NET_RE.match(name):
        return 2.0
    if POWER_NET_RE.match(name):
        return 1.6
    upper = name.upper()
    if any(tok in upper for tok in ("USB", "D+", "D-", "CLK", "XTAL")):
        return 1.5
    return 1.0


class _FastScorer:
    """Precomputed scorer for the SA inner loop.

    Caches net connectivity and part roles so each evaluation only
    recomputes position-dependent metrics: HPWL, overlaps, outline
    violations, and decap distances.
    """

    def __init__(
        self,
        circuit,
        fp_bboxes: dict[str, tuple[float, float]],
        outline: BoardOutline | None,
        clearance_mm: float,
    ):
        self._fp_bboxes = fp_bboxes
        self._outline = outline
        self._clearance = clearance_mm
        self._has_outline = outline is not None and bool(
            getattr(outline, "vertices", None)
        )
        if self._has_outline:
            self._ox_min = outline.x_min
            self._oy_min = outline.y_min
            self._ox_max = outline.x_max
            self._oy_max = outline.y_max

        self._net_refs: list[tuple[float, list[str]]] = []
        self._decap_parents: dict[str, list[str]] = {}

        if circuit is None:
            return

        try:
            from skidl.net import NCNet
        except Exception:
            NCNet = None

        for net in circuit.get_nets():
            if NCNet is not None and isinstance(net, NCNet):
                continue
            refs = []
            for pin in net.get_pins():
                ref = getattr(getattr(pin, "part", None), "ref", None)
                if ref and ref not in refs:
                    refs.append(ref)
            if len(refs) >= 2:
                w = _net_weight(str(getattr(net, "name", "") or ""))
                self._net_refs.append((w, refs))

        roles = classify_parts(circuit)
        part_by_ref = {p.ref: p for p in circuit.parts}
        net_names_by_ref = {
            ref: set(pin_net_names(part)) for ref, part in part_by_ref.items()
        }
        decap_refs = [r for r, role in roles.items() if role.role == "decoupling_cap"]
        ic_refs = [r for r, role in roles.items() if role.role in ("ic", "regulator")]
        for dref in decap_refs:
            dnets = net_names_by_ref.get(dref, set())
            parents = [
                iref for iref in ic_refs if dnets & net_names_by_ref.get(iref, set())
            ]
            if parents:
                self._decap_parents[dref] = parents

    def score(self, parts: list[PlacedPart]) -> float:
        pos = {p.ref: (p.x_mm, p.y_mm) for p in parts}

        overlap_count = self._count_overlaps(parts)
        outline_count = self._count_outline_violations(parts) if self._has_outline else 0
        total_hpwl, weighted_hpwl = self._compute_hpwl(pos)
        decap_penalty = self._decap_penalty(pos)

        penalty = 0.0
        penalty += overlap_count * 25.0
        penalty += outline_count * 20.0
        penalty += min(total_hpwl / 50.0, 30.0)
        penalty += min(weighted_hpwl / 120.0, 20.0)
        penalty += min(decap_penalty, 15.0)

        return max(0.0, 100.0 - penalty)

    def _count_overlaps(self, parts: list[PlacedPart]) -> int:
        n = len(parts)
        rects = [
            (
                p.x_mm,
                p.y_mm,
                *self._fp_bboxes.get(p.footprint, (2.0, 2.0)),
            )
            for p in parts
        ]
        count = 0
        cl = self._clearance
        for i in range(n):
            x1, y1, w1, h1 = rects[i]
            for j in range(i + 1, n):
                x2, y2, w2, h2 = rects[j]
                if abs(x1 - x2) < (w1 + w2) / 2 + cl and abs(y1 - y2) < (
                    h1 + h2
                ) / 2 + cl:
                    count += 1
        return count

    def _count_outline_violations(self, parts: list[PlacedPart]) -> int:
        count = 0
        tol = 0.1
        for p in parts:
            w, h = self._fp_bboxes.get(p.footprint, (2.0, 2.0))
            if (
                p.x_mm - w / 2 < self._ox_min - tol
                or p.x_mm + w / 2 > self._ox_max + tol
                or p.y_mm - h / 2 < self._oy_min - tol
                or p.y_mm + h / 2 > self._oy_max + tol
            ):
                count += 1
        return count

    def _compute_hpwl(
        self, pos: dict[str, tuple[float, float]]
    ) -> tuple[float, float]:
        total = 0.0
        weighted = 0.0
        for w, refs in self._net_refs:
            xs = []
            ys = []
            for ref in refs:
                if ref in pos:
                    x, y = pos[ref]
                    xs.append(x)
                    ys.append(y)
            if len(xs) >= 2:
                hpwl = (max(xs) - min(xs)) + (max(ys) - min(ys))
                total += hpwl
                weighted += hpwl * w
        return total, weighted

    def _decap_penalty(self, pos: dict[str, tuple[float, float]]) -> float:
        penalty = 0.0
        for dref, parents in self._decap_parents.items():
            if dref not in pos:
                continue
            dx, dy = pos[dref]
            nearest = min(
                (
                    math.hypot(dx - pos[p][0], dy - pos[p][1])
                    for p in parents
                    if p in pos
                ),
                default=0.0,
            )
            if nearest > 5.0:
                penalty += min((nearest - 5.0) / 5.0, 3.0)
        return penalty


def _build_swap_map(
    placed_parts: list[PlacedPart],
    locked: set[str],
) -> dict[str, list[str]]:
    by_fp: dict[str, list[str]] = {}
    for p in placed_parts:
        if p.ref in locked:
            continue
        by_fp.setdefault(p.footprint, []).append(p.ref)
    swap_map: dict[str, list[str]] = {}
    for refs in by_fp.values():
        if len(refs) < 2:
            continue
        for ref in refs:
            swap_map[ref] = [r for r in refs if r != ref]
    return swap_map


def _perturb(
    parts: list[PlacedPart],
    idx_by_ref: dict[str, int],
    rng: random.Random,
    unlocked: list[str],
    fp_bboxes: dict[str, tuple[float, float]],
    outline: BoardOutline | None,
    radius: float,
    swap_map: dict[str, list[str]],
    move_prob: float,
    rotate_prob: float,
) -> list[tuple[int, PlacedPart]] | None:
    """Generate a random perturbation.

    Returns list of (index, new_PlacedPart) pairs to apply, or None.
    """
    if not unlocked:
        return None

    roll = rng.random()

    if roll < move_prob:
        ref = rng.choice(unlocked)
        part = parts[idx_by_ref[ref]]
        dx = rng.uniform(-radius, radius)
        dy = rng.uniform(-radius, radius)
        nx, ny = part.x_mm + dx, part.y_mm + dy
        w, h = fp_bboxes.get(part.footprint, (2.0, 2.0))
        if outline is not None and getattr(outline, "vertices", None):
            nx, ny = _clamp_to_outline(nx, ny, w, h, outline)
        new_part = PlacedPart(ref, nx, ny, part.rot_deg, part.footprint)
        return [(idx_by_ref[ref], new_part)]

    if roll < move_prob + rotate_prob:
        ref = rng.choice(unlocked)
        part = parts[idx_by_ref[ref]]
        angle = rng.choice([90.0, 180.0, 270.0])
        new_rot = (part.rot_deg + angle) % 360.0
        new_part = PlacedPart(ref, part.x_mm, part.y_mm, new_rot, part.footprint)
        return [(idx_by_ref[ref], new_part)]

    candidates = [r for r in unlocked if r in swap_map]
    if not candidates:
        ref = rng.choice(unlocked)
        part = parts[idx_by_ref[ref]]
        dx = rng.uniform(-radius, radius)
        dy = rng.uniform(-radius, radius)
        nx, ny = part.x_mm + dx, part.y_mm + dy
        w, h = fp_bboxes.get(part.footprint, (2.0, 2.0))
        if outline is not None and getattr(outline, "vertices", None):
            nx, ny = _clamp_to_outline(nx, ny, w, h, outline)
        return [(idx_by_ref[ref], PlacedPart(ref, nx, ny, part.rot_deg, part.footprint))]

    ref = rng.choice(candidates)
    other_ref = rng.choice(swap_map[ref])
    a = parts[idx_by_ref[ref]]
    b = parts[idx_by_ref[other_ref]]
    new_a = PlacedPart(ref, b.x_mm, b.y_mm, a.rot_deg, a.footprint)
    new_b = PlacedPart(other_ref, a.x_mm, a.y_mm, b.rot_deg, b.footprint)
    return [(idx_by_ref[ref], new_a), (idx_by_ref[other_ref], new_b)]


def anneal_placement(
    placed_parts: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    constraints: LayoutConstraints | None = None,
    outline: BoardOutline | None = None,
    keepouts: list[KeepOut] | None = None,
    fp_geometries: dict[str, FootprintGeometry] | None = None,
    clearance_mm: float = 0.5,
    board_layers: int = 2,
    config: AnnealConfig | None = None,
) -> AnnealResult:
    """Simulated annealing placement refinement.

    Modifies placed_parts in-place with the best configuration found.
    """
    if config is None:
        config = AnnealConfig()

    locked = _locked_refs(constraints)
    unlocked = [p.ref for p in placed_parts if p.ref not in locked]
    if not unlocked:
        scorer = _FastScorer(circuit, fp_bboxes, outline, clearance_mm)
        s = scorer.score(placed_parts)
        return AnnealResult(
            improved=False,
            iterations=0,
            accepted_moves=0,
            rejected_moves=0,
            score_before=s,
            score_after=s,
            best_score=s,
            temperature_steps=0,
            final_temp=config.initial_temp,
        )

    rng = random.Random(config.seed)
    scorer = _FastScorer(circuit, fp_bboxes, outline, clearance_mm)
    swap_map = _build_swap_map(placed_parts, locked)

    current_parts = [
        PlacedPart(p.ref, p.x_mm, p.y_mm, p.rot_deg, p.footprint)
        for p in placed_parts
    ]
    idx_by_ref = {p.ref: i for i, p in enumerate(current_parts)}

    current_score = scorer.score(current_parts)
    initial_score = current_score
    best_parts = [
        PlacedPart(p.ref, p.x_mm, p.y_mm, p.rot_deg, p.footprint)
        for p in current_parts
    ]
    best_score = current_score

    max_radius = config.max_move_mm
    if max_radius is None and outline is not None:
        max_radius = min(
            getattr(outline, "width_mm", 30.0),
            getattr(outline, "height_mm", 30.0),
        ) / 3.0
    elif max_radius is None:
        max_radius = 15.0

    moves_per_temp = config.moves_per_temp or max(len(unlocked) * 4, 20)

    temp = config.initial_temp
    temp_steps = 0
    total_moves = 0
    accepted = 0
    rejected = 0

    while temp > config.min_temp:
        temp_steps += 1
        radius = max(1.0, max_radius * (temp / config.initial_temp))

        for _ in range(moves_per_temp):
            total_moves += 1

            changes = _perturb(
                current_parts,
                idx_by_ref,
                rng,
                unlocked,
                fp_bboxes,
                outline,
                radius,
                swap_map,
                config.move_prob,
                config.rotate_prob,
            )
            if changes is None:
                rejected += 1
                continue

            old_values = [(idx, current_parts[idx]) for idx, _ in changes]
            for idx, new_part in changes:
                current_parts[idx] = new_part

            new_score = scorer.score(current_parts)
            delta = new_score - current_score

            if delta > 0 or rng.random() < math.exp(
                delta / max(temp, 0.001)
            ):
                accepted += 1
                current_score = new_score
                if new_score > best_score:
                    best_score = new_score
                    best_parts = [
                        PlacedPart(p.ref, p.x_mm, p.y_mm, p.rot_deg, p.footprint)
                        for p in current_parts
                    ]
            else:
                rejected += 1
                for idx, old_part in old_values:
                    current_parts[idx] = old_part

        temp *= config.cooling_rate

    placed_parts.clear()
    placed_parts.extend(best_parts)

    return AnnealResult(
        improved=best_score > initial_score,
        iterations=total_moves,
        accepted_moves=accepted,
        rejected_moves=rejected,
        score_before=initial_score,
        score_after=best_score,
        best_score=best_score,
        temperature_steps=temp_steps,
        final_temp=temp,
    )
