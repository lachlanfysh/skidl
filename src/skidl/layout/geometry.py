from __future__ import annotations

import math
from dataclasses import dataclass, field

from simp_sexp import Sexp

from .writer import PlacedPart, load_footprint


@dataclass(frozen=True)
class PadGeometry:
    number: str
    x_mm: float
    y_mm: float
    width_mm: float
    height_mm: float
    shape: str = "rect"
    rot_deg: float = 0.0
    layers: tuple[str, ...] = ()
    net_name: str | None = None

    @property
    def local_bounds(self) -> tuple[float, float, float, float]:
        return (
            self.x_mm - self.width_mm / 2,
            self.y_mm - self.height_mm / 2,
            self.x_mm + self.width_mm / 2,
            self.y_mm + self.height_mm / 2,
        )


@dataclass(frozen=True)
class FootprintGeometry:
    footprint: str
    pads: list[PadGeometry] = field(default_factory=list)
    body_bounds: tuple[float, float, float, float] | None = None
    courtyard_bounds: tuple[float, float, float, float] | None = None

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        if self.courtyard_bounds is not None:
            return self.courtyard_bounds
        if self.body_bounds is not None:
            return self.body_bounds
        if self.pads:
            return _bounds_union(pad.local_bounds for pad in self.pads)
        return (-1.0, -1.0, 1.0, 1.0)

    @property
    def width_mm(self) -> float:
        x_min, _, x_max, _ = self.bounds
        return x_max - x_min

    @property
    def height_mm(self) -> float:
        _, y_min, _, y_max = self.bounds
        return y_max - y_min

    def transformed_bounds(self, placed: PlacedPart) -> tuple[float, float, float, float]:
        x_min, y_min, x_max, y_max = self.bounds
        corners = [
            (x_min, y_min),
            (x_max, y_min),
            (x_max, y_max),
            (x_min, y_max),
        ]
        points = [
            transform_point(placed.x_mm, placed.y_mm, placed.rot_deg, x, y)
            for x, y in corners
        ]
        return _bounds_union((x, y, x, y) for x, y in points)

    def pad_world_centers(self, placed: PlacedPart) -> dict[str, tuple[float, float]]:
        return {
            pad.number: transform_point(
                placed.x_mm, placed.y_mm, placed.rot_deg, pad.x_mm, pad.y_mm
            )
            for pad in self.pads
        }

    def pad_side_counts(self) -> dict[str, int]:
        """Summarize which side of the footprint pads mostly face."""
        x_min, y_min, x_max, y_max = self.bounds
        x_mid = (x_min + x_max) / 2
        y_mid = (y_min + y_max) / 2
        counts = {"left": 0, "right": 0, "top": 0, "bottom": 0}
        for pad in self.pads:
            dx = pad.x_mm - x_mid
            dy = pad.y_mm - y_mid
            if abs(dx) >= abs(dy):
                counts["right" if dx >= 0 else "left"] += 1
            else:
                counts["bottom" if dy >= 0 else "top"] += 1
        return counts


def transform_point(
    origin_x: float,
    origin_y: float,
    rot_deg: float,
    local_x: float,
    local_y: float,
) -> tuple[float, float]:
    radians = math.radians(rot_deg)
    return (
        origin_x + local_x * math.cos(radians) - local_y * math.sin(radians),
        origin_y + local_x * math.sin(radians) + local_y * math.cos(radians),
    )


def _find_child(node, key: str):
    for child in node:
        if isinstance(child, list) and child and child[0] == key:
            return child
    return None


def _as_float(value, default=0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _layer_name(node) -> str:
    layer = _find_child(node, "layer")
    if layer is not None and len(layer) > 1:
        return str(layer[1]).strip('"')
    return ""


def _xy(node, key: str) -> tuple[float, float] | None:
    child = _find_child(node, key)
    if child is not None and len(child) >= 3:
        return _as_float(child[1]), _as_float(child[2])
    return None


def _bounds_union(bounds_iter) -> tuple[float, float, float, float]:
    x_min = y_min = float("inf")
    x_max = y_max = float("-inf")
    for bx_min, by_min, bx_max, by_max in bounds_iter:
        x_min = min(x_min, bx_min)
        y_min = min(y_min, by_min)
        x_max = max(x_max, bx_max)
        y_max = max(y_max, by_max)
    if x_min == float("inf"):
        return (-1.0, -1.0, 1.0, 1.0)
    return x_min, y_min, x_max, y_max


def _graphic_bounds(
    fp: Sexp,
    layer_suffix: str,
) -> tuple[float, float, float, float] | None:
    bounds = []
    for line in fp.search("fp_line"):
        if not _layer_name(line).endswith(layer_suffix):
            continue
        start = _xy(line, "start")
        end = _xy(line, "end")
        if start and end:
            xs = [start[0], end[0]]
            ys = [start[1], end[1]]
            bounds.append((min(xs), min(ys), max(xs), max(ys)))
    for rect in fp.search("fp_rect"):
        if not _layer_name(rect).endswith(layer_suffix):
            continue
        start = _xy(rect, "start")
        end = _xy(rect, "end")
        if start and end:
            xs = [start[0], end[0]]
            ys = [start[1], end[1]]
            bounds.append((min(xs), min(ys), max(xs), max(ys)))
    if not bounds:
        return None
    return _bounds_union(bounds)


def footprint_geometry_from_sexp(footprint: str, fp: Sexp) -> FootprintGeometry:
    pads: list[PadGeometry] = []
    for pad in fp.search("pad"):
        if len(pad) < 4:
            continue
        at = _find_child(pad, "at")
        size = _find_child(pad, "size")
        if at is None or size is None or len(size) < 3:
            continue
        layers = _find_child(pad, "layers")
        net = _find_child(pad, "net")
        pads.append(
            PadGeometry(
                number=str(pad[1]).strip('"'),
                x_mm=_as_float(at[1]) if len(at) > 1 else 0.0,
                y_mm=_as_float(at[2]) if len(at) > 2 else 0.0,
                rot_deg=_as_float(at[3]) if len(at) > 3 else 0.0,
                width_mm=_as_float(size[1], 1.0),
                height_mm=_as_float(size[2], 1.0),
                shape=str(pad[3]).strip('"'),
                layers=tuple(str(layer).strip('"') for layer in (layers or [])[1:]),
                net_name=(
                    str(net[2]).strip('"')
                    if net is not None and len(net) > 2
                    else None
                ),
            )
        )

    courtyard = _graphic_bounds(fp, ".CrtYd")
    body = _graphic_bounds(fp, ".Fab") or _graphic_bounds(fp, ".SilkS")
    return FootprintGeometry(
        footprint=footprint,
        pads=pads,
        body_bounds=body,
        courtyard_bounds=courtyard,
    )


def load_footprint_geometry(
    fp_name: str,
    fp_lib_dirs: list[str],
) -> FootprintGeometry:
    return footprint_geometry_from_sexp(fp_name, load_footprint(fp_name, fp_lib_dirs))


def load_footprint_geometries(
    fp_names: set[str],
    fp_lib_dirs: list[str],
) -> dict[str, FootprintGeometry]:
    geometries: dict[str, FootprintGeometry] = {}
    for fp_name in fp_names:
        try:
            geometries[fp_name] = load_footprint_geometry(fp_name, fp_lib_dirs)
        except FileNotFoundError:
            pass
    return geometries


def geometry_bboxes(
    geometries: dict[str, FootprintGeometry],
) -> dict[str, tuple[float, float]]:
    return {
        fp_name: (geometry.width_mm, geometry.height_mm)
        for fp_name, geometry in geometries.items()
    }
