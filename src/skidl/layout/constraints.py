from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class FixedPosition:
    ref: str
    x_mm: float
    y_mm: float
    rot_deg: float = 0.0


@dataclass
class AnchorZone:
    group_name: str
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass
class KeepOut:
    x_min: float
    y_min: float
    x_max: float
    y_max: float


@dataclass(init=False)
class BoardOutline:
    vertices: list[tuple[float, float]] = field(default_factory=list)

    def __init__(
        self,
        width_mm: float = 0.0,
        height_mm: float = 0.0,
        vertices=None,
    ):
        if vertices is not None:
            self.vertices = [(float(x), float(y)) for x, y in vertices]
        elif width_mm > 0 and height_mm > 0:
            width_mm = float(width_mm)
            height_mm = float(height_mm)
            self.vertices = [
                (0.0, 0.0),
                (width_mm, 0.0),
                (width_mm, height_mm),
                (0.0, height_mm),
            ]
        else:
            self.vertices = []

    @property
    def x_min(self) -> float:
        if not self.vertices:
            return 0.0
        return min(x for x, _ in self.vertices)

    @property
    def y_min(self) -> float:
        if not self.vertices:
            return 0.0
        return min(y for _, y in self.vertices)

    @property
    def x_max(self) -> float:
        if not self.vertices:
            return 0.0
        return max(x for x, _ in self.vertices)

    @property
    def y_max(self) -> float:
        if not self.vertices:
            return 0.0
        return max(y for _, y in self.vertices)

    @property
    def width_mm(self) -> float:
        return self.x_max - self.x_min

    @property
    def height_mm(self) -> float:
        return self.y_max - self.y_min


@dataclass
class LayoutConstraints:
    fixed: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    keepouts: list = field(default_factory=list)
    outline: BoardOutline = None
