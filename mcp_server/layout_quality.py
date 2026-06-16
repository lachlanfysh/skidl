"""Product-layout quality reporting for generated board runs.

This layer deliberately sits above schematic/layout generation. It converts the
raw response payload into stable, board-level quality gates and issue classes so
human visual feedback can be tracked without changing placement behavior.
"""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any


ERROR_SEVERITIES = {"error", "fatal"}
PRODUCT_BLOCKING_SEVERITIES = {"warning", "error", "fatal"}


def _enum_value(value: Any) -> str:
    if hasattr(value, "value"):
        return str(value.value)
    return str(value or "")


def _exception_dict(exc: Any) -> dict:
    if hasattr(exc, "model_dump"):
        return exc.model_dump(mode="json")
    if isinstance(exc, dict):
        return dict(exc)
    return {}


def _exception_code(exc: Any) -> str:
    data = _exception_dict(exc)
    return _enum_value(data.get("code"))


def _exception_severity(exc: Any) -> str:
    data = _exception_dict(exc)
    return _enum_value(data.get("severity")).lower()


def _has_hard_error(exceptions: list[Any]) -> bool:
    return any(_exception_severity(exc) in ERROR_SEVERITIES for exc in exceptions)


def _preview_files(artifacts: dict | None) -> list[str]:
    artifacts = artifacts or {}
    previews = artifacts.get("previews") or {}
    files = previews.get("files") if isinstance(previews, dict) else None
    if isinstance(files, list):
        return [str(name) for name in files if name]
    return []


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _placement_stats(layout: dict | None, metrics: dict | None) -> dict:
    layout = layout or {}
    metrics = metrics or {}
    outline = layout.get("outline") if isinstance(layout.get("outline"), dict) else {}
    width = _float(outline.get("width_mm"))
    height = _float(outline.get("height_mm"))
    if not width or not height:
        area = _float(metrics.get("board_area_mm2"))
        if area:
            width = width or area ** 0.5
            height = height or area / width

    placed = layout.get("placed_parts") if isinstance(layout.get("placed_parts"), list) else []
    xs = [_float(part.get("x_mm")) for part in placed if isinstance(part, dict)]
    ys = [_float(part.get("y_mm")) for part in placed if isinstance(part, dict)]
    stats = {
        "part_count": len(placed),
        "outline_mm": {"width": width, "height": height},
        "board_area_mm2": width * height if width and height else _float(metrics.get("board_area_mm2")),
        "spread_bbox_mm": {"width": 0.0, "height": 0.0},
        "spread_area_ratio": 0.0,
        "edge_margins_mm": {},
        "max_margin_ratio": 0.0,
    }
    if not xs or not ys or not width or not height:
        return stats

    x_min, x_max = min(xs), max(xs)
    y_min, y_max = min(ys), max(ys)
    spread_w = max(0.0, x_max - x_min)
    spread_h = max(0.0, y_max - y_min)
    margins = {
        "left": max(0.0, x_min),
        "right": max(0.0, width - x_max),
        "top": max(0.0, y_min),
        "bottom": max(0.0, height - y_max),
    }
    stats["spread_bbox_mm"] = {"width": spread_w, "height": spread_h}
    stats["spread_area_ratio"] = (
        (spread_w * spread_h) / (width * height)
        if width > 0 and height > 0
        else 0.0
    )
    stats["edge_margins_mm"] = margins
    stats["max_margin_ratio"] = max(
        margins["left"] / width,
        margins["right"] / width,
        margins["top"] / height,
        margins["bottom"] / height,
    )
    return stats


def _issue(
    code: str,
    severity: str,
    message: str,
    *,
    evidence: dict | None = None,
    recommendation: str = "",
) -> dict:
    return {
        "code": code,
        "severity": severity,
        "message": message,
        "evidence": dict(evidence or {}),
        "recommendation": recommendation,
    }


def _issues(
    *,
    exceptions: list[Any],
    layout: dict,
    metrics: dict,
    artifacts: dict,
    placement: dict,
) -> list[dict]:
    issues: list[dict] = []
    congestion = _float(metrics.get("congestion_score"))
    if congestion >= 40.0:
        issues.append(
            _issue(
                "HIGH_CONGESTION",
                "warning" if congestion >= 80.0 else "advisory",
                f"layout congestion is high ({congestion:.1f})",
                evidence={"congestion_score": congestion},
                recommendation=(
                    "Use this as placement feedback before growing the outline; "
                    "try group movement, connector orientation, or route-aware "
                    "retries first."
                ),
            )
        )

    if not _preview_files(artifacts) and (layout or metrics.get("manufacturable")):
        issues.append(
            _issue(
                "MISSING_VISUAL_PREVIEW",
                "warning",
                "no human-reviewable preview artifact was reported",
                recommendation="Generate a top/assembly preview before asking for layout approval.",
            )
        )

    part_count = int(placement.get("part_count", 0) or 0)
    area = _float(placement.get("board_area_mm2"))
    spread_ratio = _float(placement.get("spread_area_ratio"))
    max_margin_ratio = _float(placement.get("max_margin_ratio"))
    if part_count >= 5 and area >= 1000.0:
        if spread_ratio and spread_ratio < 0.25:
            issues.append(
                _issue(
                    "LOW_PART_SPREAD",
                    "warning",
                    "parts occupy a small fraction of the board outline",
                    evidence={
                        "spread_area_ratio": round(spread_ratio, 3),
                        "board_area_mm2": round(area, 2),
                    },
                    recommendation=(
                        "If the outline is not mechanically fixed, shrink it. "
                        "If it is fixed, distribute user-facing parts across the "
                        "meaningful area."
                    ),
                )
            )
        if max_margin_ratio >= 0.35:
            issues.append(
                _issue(
                    "UNUSED_OUTLINE_REGION",
                    "warning",
                    "one or more board margins are very large relative to the outline",
                    evidence={
                        "max_margin_ratio": round(max_margin_ratio, 3),
                        "edge_margins_mm": placement.get("edge_margins_mm", {}),
                    },
                    recommendation=(
                        "Review whether the board should compact or whether the "
                        "fixed mechanical area should be used more deliberately."
                    ),
                )
            )

    floorplan = layout.get("floorplan") if isinstance(layout.get("floorplan"), dict) else {}
    if (
        int(floorplan.get("fixed_positions", 0) or 0) >= 30
        and int(floorplan.get("keepouts", 0) or 0) == 0
        and area >= 8000.0
    ):
        issues.append(
            _issue(
                "THIN_MECHANICAL_FLOORPLAN",
                "advisory",
                "large explicit floorplan has fixed placements but no mechanical keepouts/cutouts",
                evidence={
                    "fixed_positions": floorplan.get("fixed_positions", 0),
                    "keepouts": floorplan.get("keepouts", 0),
                    "board_area_mm2": round(area, 2),
                },
                recommendation=(
                    "Confirm that apertures, panel voids, enclosure keepouts, "
                    "or other mechanical constraints were not dropped."
                ),
            )
        )

    for exc in exceptions:
        code = _exception_code(exc)
        severity = _exception_severity(exc)
        if code in {"LONG_POWER_NET", "ROUTE_UNCONNECTED", "DRC_CLEARANCE", "DRC_SHORT"}:
            data = _exception_dict(exc)
            issues.append(
                _issue(
                    code,
                    "warning" if severity in ERROR_SEVERITIES else "advisory",
                    str(data.get("message") or code),
                    evidence=data.get("subject") if isinstance(data.get("subject"), dict) else {},
                    recommendation=str(data.get("retry_hint") or ""),
                )
            )

    return issues


def build_layout_quality(
    *,
    run_id: str,
    status: str,
    stage: str,
    ok: bool,
    exceptions: list[Any] | None = None,
    layout: dict | None = None,
    metrics: dict | None = None,
    artifacts: dict | None = None,
) -> dict:
    """Build a stable quality report from a pipeline response."""
    exceptions = list(exceptions or [])
    layout = dict(layout or {})
    metrics = dict(metrics or {})
    artifacts = dict(artifacts or {})
    placement = _placement_stats(layout, metrics)
    validation = layout.get("validation") if isinstance(layout.get("validation"), dict) else {}
    previews = _preview_files(artifacts)
    hard_error = _has_hard_error(exceptions)
    has_layout = bool(layout.get("placed_parts"))
    placement_ok = bool(layout.get("ok") or validation.get("ok")) and has_layout
    manufacturable = bool(metrics.get("manufacturable"))
    pipeline_goal = str(metrics.get("pipeline_goal") or "")

    schematic_ok = bool(has_layout or (ok and not hard_error) or artifacts.get("schematic"))
    drc_ok = bool(manufacturable and metrics.get("manufacturing_complete", manufacturable))
    visual_review_ready = bool(placement_ok and previews)
    issues = _issues(
        exceptions=exceptions,
        layout=layout,
        metrics=metrics,
        artifacts=artifacts,
        placement=placement,
    )
    product_blockers = [
        issue for issue in issues
        if str(issue.get("severity", "")).lower() in PRODUCT_BLOCKING_SEVERITIES
    ]
    product_layout_ok = bool(visual_review_ready and not product_blockers)

    gates = {
        "schematic_ok": schematic_ok,
        "placement_ok": placement_ok,
        "drc_ok": drc_ok,
        "manufacturable": manufacturable,
        "visual_review_ready": visual_review_ready,
        "product_layout_ok": product_layout_ok,
    }
    counts = Counter(str(issue["severity"]) for issue in issues)
    return {
        "version": 1,
        "run_id": run_id,
        "status": status,
        "stage": stage,
        "ok": bool(ok),
        "pipeline_goal": pipeline_goal or None,
        "gates": gates,
        "issue_counts": dict(sorted(counts.items())),
        "issues": issues,
        "placement": placement,
        "artifacts": {
            "preview_files": previews,
            "has_preview": bool(previews),
            "has_pcb": bool(artifacts.get("pcb")),
            "has_schematic": bool(artifacts.get("schematic")),
            "has_manufacturing": bool(artifacts.get("manufacturing")),
        },
        "metrics": {
            "layout_score": metrics.get("layout_score"),
            "congestion_score": metrics.get("congestion_score"),
            "total_hpwl_mm": metrics.get("total_hpwl_mm"),
            "board_area_mm2": metrics.get("board_area_mm2"),
        },
    }


def write_layout_quality(run_dir: str | Path, quality: dict) -> Path:
    """Persist a layout quality report and return its path."""
    path = Path(run_dir) / "layout_quality.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(quality, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path
