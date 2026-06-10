"""Geometry feature extraction from a circuit spec dict + worker metrics.

Operates on plain dicts (a CircuitSpec.model_dump() or raw user input)
so it stays decoupled from the schemas package and the engine.

Pure stdlib + pydantic. No transport- or host-specific imports.
"""

from __future__ import annotations

from telemetry.models import GeometryFeatures


def extract_geometry(spec_dict: dict, worker_metrics: dict) -> GeometryFeatures:
    """Build GeometryFeatures from a spec dict and engine worker metrics.

    - component/net/pin counts come from the spec ("parts", "nets")
    - pad_count and board_area_mm2 come from worker_metrics (0 defaults)
    - layer_count comes from spec board.layers (0 if absent = unknown)
    - pad_density_per_cm2 = pad_count / (board_area_mm2 / 100), 0 when
      the area is unknown or non-positive
    """
    spec_dict = spec_dict or {}
    worker_metrics = worker_metrics or {}

    parts = spec_dict.get("parts") or []
    nets = spec_dict.get("nets") or []
    board = spec_dict.get("board") or {}

    pin_count = sum(len(net.get("pins") or []) for net in nets)
    pad_count = int(worker_metrics.get("pad_count", 0) or 0)
    board_area_mm2 = float(worker_metrics.get("board_area_mm2", 0.0) or 0.0)
    area_cm2 = board_area_mm2 / 100.0
    pad_density = pad_count / area_cm2 if area_cm2 > 0 else 0.0

    return GeometryFeatures(
        component_count=len(parts),
        net_count=len(nets),
        pin_count=pin_count,
        pad_count=pad_count,
        layer_count=int(board.get("layers", 0) or 0),
        board_area_mm2=board_area_mm2,
        pad_density_per_cm2=pad_density,
    )
