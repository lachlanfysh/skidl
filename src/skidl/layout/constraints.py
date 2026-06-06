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


@dataclass
class BoardOutline:
    width_mm: float
    height_mm: float


@dataclass
class LayoutConstraints:
    fixed: list = field(default_factory=list)
    zones: list = field(default_factory=list)
    keepouts: list = field(default_factory=list)
    outline: BoardOutline = None
