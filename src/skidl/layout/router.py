"""Global routing estimation for placement quality assessment.

Computes a rectilinear minimum spanning tree (RMST) per net, traces
edges on a coarse grid, and reports routing congestion — cells where
demand exceeds capacity indicate placement that will be hard to route.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .constraints import BoardOutline
from .writer import PlacedPart


@dataclass
class RoutingEstimate:
    total_rmst_mm: float
    overflow_cells: int
    max_demand: int
    avg_demand: float
    blocked_cells: int
    cell_size_mm: float
    grid_rows: int
    grid_cols: int
    capacity_per_cell: int

    @property
    def congestion_penalty(self) -> float:
        return min(self.overflow_cells * 2.0, 15.0)

    def summary(self) -> str:
        lines = [
            f"Routing estimate: RMST {self.total_rmst_mm:.1f}mm, "
            f"grid {self.grid_cols}x{self.grid_rows} @ {self.cell_size_mm}mm"
        ]
        lines.append(
            f"  Demand: peak {self.max_demand}, avg {self.avg_demand:.1f}, "
            f"capacity {self.capacity_per_cell}"
        )
        if self.overflow_cells:
            lines.append(
                f"  Overflow: {self.overflow_cells} cells exceed capacity"
            )
        else:
            lines.append("  No routing overflow detected")
        if self.blocked_cells:
            lines.append(f"  Blocked by parts: {self.blocked_cells} cells")
        return "\n".join(lines)


def _manhattan(a: tuple[float, float], b: tuple[float, float]) -> float:
    return abs(a[0] - b[0]) + abs(a[1] - b[1])


def _rmst_edges(
    points: list[tuple[float, float]],
) -> list[tuple[int, int]]:
    """Rectilinear MST edges via Prim's algorithm.

    Returns list of (i, j) index pairs into the points list.
    """
    n = len(points)
    if n <= 1:
        return []

    in_tree = [False] * n
    min_dist = [float("inf")] * n
    min_edge = [0] * n
    in_tree[0] = True
    edges: list[tuple[int, int]] = []

    for j in range(1, n):
        min_dist[j] = _manhattan(points[0], points[j])
        min_edge[j] = 0

    for _ in range(n - 1):
        best = -1
        best_d = float("inf")
        for j in range(n):
            if not in_tree[j] and min_dist[j] < best_d:
                best_d = min_dist[j]
                best = j
        if best == -1:
            break
        in_tree[best] = True
        edges.append((min_edge[best], best))
        for j in range(n):
            if not in_tree[j]:
                d = _manhattan(points[best], points[j])
                if d < min_dist[j]:
                    min_dist[j] = d
                    min_edge[j] = best
    return edges


def rmst_length(points: list[tuple[float, float]]) -> float:
    """Total rectilinear MST wirelength for a set of points."""
    total = 0.0
    for i, j in _rmst_edges(points):
        total += _manhattan(points[i], points[j])
    return total


def _trace_l_path(
    p1: tuple[float, float],
    p2: tuple[float, float],
    grid: list[list[int]],
    x_min: float,
    y_min: float,
    cell_size: float,
):
    """Trace an L-shaped rectilinear path on the demand grid."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0

    c1 = min(max(int((p1[0] - x_min) / cell_size), 0), cols - 1)
    r1 = min(max(int((p1[1] - y_min) / cell_size), 0), rows - 1)
    c2 = min(max(int((p2[0] - x_min) / cell_size), 0), cols - 1)
    r2 = min(max(int((p2[1] - y_min) / cell_size), 0), rows - 1)

    c_lo, c_hi = min(c1, c2), max(c1, c2)
    for c in range(c_lo, c_hi + 1):
        grid[r1][c] += 1

    r_lo, r_hi = min(r1, r2), max(r1, r2)
    for r in range(r_lo, r_hi + 1):
        grid[r][c2] += 1

    if 0 <= r1 < rows and 0 <= c2 < cols:
        grid[r1][c2] = max(grid[r1][c2] - 1, 0)


def _block_part_cells(
    parts: list[PlacedPart],
    fp_bboxes: dict[str, tuple[float, float]],
    grid: list[list[int]],
    x_min: float,
    y_min: float,
    cell_size: float,
) -> int:
    """Mark grid cells occupied by part bodies. Returns blocked count."""
    rows = len(grid)
    cols = len(grid[0]) if rows else 0
    blocked = 0

    for p in parts:
        w, h = fp_bboxes.get(p.footprint, (2.0, 2.0))
        c_lo = min(max(int((p.x_mm - w / 2 - x_min) / cell_size), 0), cols - 1)
        c_hi = min(max(int((p.x_mm + w / 2 - x_min) / cell_size), 0), cols - 1)
        r_lo = min(max(int((p.y_mm - h / 2 - y_min) / cell_size), 0), rows - 1)
        r_hi = min(max(int((p.y_mm + h / 2 - y_min) / cell_size), 0), rows - 1)
        for r in range(r_lo, r_hi + 1):
            for c in range(c_lo, c_hi + 1):
                grid[r][c] += 1
                blocked += 1
    return blocked


def estimate_routing(
    placed_parts: list[PlacedPart],
    circuit,
    fp_bboxes: dict[str, tuple[float, float]],
    outline: BoardOutline | None = None,
    cell_size_mm: float = 1.0,
    board_layers: int = 2,
) -> RoutingEstimate:
    """Estimate global routing congestion from part positions.

    Creates a demand grid, traces RMST edges for every net, and reports
    overflow where demand exceeds per-cell capacity.
    """
    if outline is None or not getattr(outline, "vertices", None):
        return RoutingEstimate(
            total_rmst_mm=0.0,
            overflow_cells=0,
            max_demand=0,
            avg_demand=0.0,
            blocked_cells=0,
            cell_size_mm=cell_size_mm,
            grid_rows=0,
            grid_cols=0,
            capacity_per_cell=0,
        )

    x_min, y_min = outline.x_min, outline.y_min
    x_max, y_max = outline.x_max, outline.y_max
    cols = max(1, int(math.ceil((x_max - x_min) / cell_size_mm)))
    rows = max(1, int(math.ceil((y_max - y_min) / cell_size_mm)))
    capacity = board_layers * 3

    grid = [[0] * cols for _ in range(rows)]
    blocked = _block_part_cells(
        placed_parts, fp_bboxes, grid, x_min, y_min, cell_size_mm
    )

    pos_by_ref = {p.ref: (p.x_mm, p.y_mm) for p in placed_parts}
    total_rmst = 0.0

    if circuit is not None:
        try:
            from skidl.net import NCNet
        except Exception:
            NCNet = None

        for net in circuit.get_nets():
            if NCNet is not None and isinstance(net, NCNet):
                continue
            seen: dict[str, tuple[float, float]] = {}
            for pin in net.get_pins():
                ref = getattr(getattr(pin, "part", None), "ref", None)
                if ref and ref in pos_by_ref and ref not in seen:
                    seen[ref] = pos_by_ref[ref]
            points = list(seen.values())
            if len(points) < 2:
                continue
            edges = _rmst_edges(points)
            for i, j in edges:
                total_rmst += _manhattan(points[i], points[j])
                _trace_l_path(
                    points[i], points[j], grid, x_min, y_min, cell_size_mm
                )

    max_demand = 0
    total_demand = 0
    overflow = 0
    for r in range(rows):
        for c in range(cols):
            d = grid[r][c]
            total_demand += d
            if d > max_demand:
                max_demand = d
            if d > capacity:
                overflow += 1

    avg_demand = total_demand / max(rows * cols, 1)

    return RoutingEstimate(
        total_rmst_mm=total_rmst,
        overflow_cells=overflow,
        max_demand=max_demand,
        avg_demand=avg_demand,
        blocked_cells=blocked,
        cell_size_mm=cell_size_mm,
        grid_rows=rows,
        grid_cols=cols,
        capacity_per_cell=capacity,
    )
