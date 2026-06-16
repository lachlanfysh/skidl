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

from mcp_server.exception_mapper import classify_routing_failure


ERROR_SEVERITIES = {"error", "fatal"}
PRODUCT_BLOCKING_SEVERITIES = {"warning", "error", "fatal"}
PRODUCT_BLOCKING_EXCEPTION_CODES = {
    "CODE_EXEC_ERROR",
    "DRC_CLEARANCE",
    "DRC_COURTYARD",
    "DRC_SHORT",
    "DRC_UNCONNECTED",
    "ENGINE_CRASH",
    "ENGINE_TIMEOUT",
    "FOOTPRINT_MISSING",
    "LAYOUT_KEEPOUT",
    "LAYOUT_MISSING_REF",
    "LAYOUT_OUTLINE_VIOLATION",
    "LAYOUT_OVERLAP",
    "LONG_POWER_NET",
    "MANUFACTURING_OUTPUT_FAILURE",
    "POST_ARTIFACT_FAILURE",
    "ROUTE_UNCONNECTED",
    "ROUTE_TIMEOUT",
}
PRODUCT_ADVISORY_EXCEPTION_CODES = {
    "HIGH_CONGESTION",
    "PLACEMENT_REVIEW_ONLY",
    "ROUTE_CONGESTION",
    "ROUTE_UNAVAILABLE",
}
AGGREGATE_ISSUE_CODES = {
    "HIGH_CONGESTION",
    "LAYOUT_KEEPOUT",
    "LAYOUT_MISSING_REF",
    "LAYOUT_OUTLINE_VIOLATION",
    "LAYOUT_OVERLAP",
}


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
    outline_x_min = _float(outline.get("x_min_mm"))
    outline_y_min = _float(outline.get("y_min_mm"))
    outline_x_max = _float(outline.get("x_max_mm"), outline_x_min + width)
    outline_y_max = _float(outline.get("y_max_mm"), outline_y_min + height)
    if not width or not height:
        area = _float(metrics.get("board_area_mm2"))
        if area:
            width = width or area ** 0.5
            height = height or area / width
            outline_x_max = outline_x_min + width
            outline_y_max = outline_y_min + height

    placed = layout.get("placed_parts") if isinstance(layout.get("placed_parts"), list) else []
    xs = [_float(part.get("x_mm")) for part in placed if isinstance(part, dict)]
    ys = [_float(part.get("y_mm")) for part in placed if isinstance(part, dict)]
    stats = {
        "part_count": len(placed),
        "outline_mm": {"width": width, "height": height},
        "outline_bounds_mm": {
            "x_min": outline_x_min,
            "y_min": outline_y_min,
            "x_max": outline_x_max,
            "y_max": outline_y_max,
        },
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
        "left": max(0.0, x_min - outline_x_min),
        "right": max(0.0, outline_x_max - x_max),
        "top": max(0.0, y_min - outline_y_min),
        "bottom": max(0.0, outline_y_max - y_max),
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


def _placed_by_ref(layout: dict) -> dict[str, dict]:
    placed = layout.get("placed_parts") if isinstance(layout.get("placed_parts"), list) else []
    return {
        str(part.get("ref")): part
        for part in placed
        if isinstance(part, dict) and part.get("ref")
    }


def _validation_list(validation: dict, key: str) -> list:
    value = validation.get(key)
    return value if isinstance(value, list) else []


def _intent_plan(layout: dict) -> dict:
    value = layout.get("intent_plan")
    return value if isinstance(value, dict) else {}


def _score_warnings(layout: dict) -> list[str]:
    score = layout.get("score")
    if not isinstance(score, dict):
        return []
    warnings = score.get("warnings")
    return [str(warning) for warning in warnings] if isinstance(warnings, list) else []


def _edge_geometry_warning_present(layout: dict, ref: str, edge: str) -> bool:
    needle = f"{ref}: violates {edge}-edge mating intent"
    row_needle = f"{ref}: connector row is not parallel to the {edge} edge"
    return any(
        needle in warning or row_needle in warning
        for warning in _score_warnings(layout)
    )


def _edge_origin_distance_is_ambiguous(
    *,
    layout: dict,
    anchor: dict,
    part: dict,
    ref: str,
    edge: str,
) -> bool:
    """Return True when origin distance is a weak proxy for edge placement."""

    # Layout engine score warnings use actual footprint bounds / mating edge
    # geometry. If they are present, trust them over the footprint origin.
    if not isinstance(layout.get("score"), dict):
        return False
    if _edge_geometry_warning_present(layout, ref, edge):
        return False

    text = " ".join(
        str(value or "")
        for value in (
            part.get("footprint"),
            part.get("ref"),
            anchor.get("edge"),
        )
    ).lower()
    intent_plan = _intent_plan(layout)
    intents_by_ref = intent_plan.get("intents")
    intent_items = (
        intents_by_ref.get(ref)
        if isinstance(intents_by_ref, dict)
        else []
    )
    intent_kinds = {
        str(intent.get("kind") or "")
        for intent in (intent_items or [])
        if isinstance(intent, dict)
    }
    connector_text = any(
        token in text
        for token in (
            "horizontal",
            "right_angle",
            "right-angle",
            "angled",
            "pinheader",
            "pin_header",
            "usb",
            "jst",
            "qwiic",
            "stemma",
            "terminalblock",
            "terminal_block",
        )
    )
    has_geometry_intent = (
        anchor.get("rot_deg") is not None
        or anchor.get("inset_mm") is not None
        or intent_kinds & {"edge_connector", "mechanical_mating"}
    )
    return bool(connector_text or has_geometry_intent)


def _axis_value(part: dict, axis: str) -> float:
    return _float(part.get("x_mm") if axis == "x" else part.get("y_mm"))


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
    validation = layout.get("validation") if isinstance(layout.get("validation"), dict) else {}
    intent_plan = _intent_plan(layout)
    placed_by_ref = _placed_by_ref(layout)
    outline = layout.get("outline") if isinstance(layout.get("outline"), dict) else {}
    outline_w = _float(outline.get("width_mm"))
    outline_h = _float(outline.get("height_mm"))
    outline_x_min = _float(outline.get("x_min_mm"))
    outline_y_min = _float(outline.get("y_min_mm"))
    outline_x_max = _float(outline.get("x_max_mm"), outline_x_min + outline_w)
    outline_y_max = _float(outline.get("y_max_mm"), outline_y_min + outline_h)
    overlaps = _validation_list(validation, "overlaps")
    outline_violations = _validation_list(validation, "outline_violations")
    keepout_violations = _validation_list(validation, "keepout_violations")
    missing_refs = _validation_list(validation, "missing_refs")

    if overlaps:
        issues.append(
            _issue(
                "LAYOUT_OVERLAP",
                "error",
                f"{len(overlaps)} footprint overlap(s) remain in placement",
                evidence={"overlaps": overlaps[:12], "count": len(overlaps)},
                recommendation=(
                    "Resolve placement before routing or outline growth. Move "
                    "non-mechanical parts away from locked edge/grid/mounting "
                    "constraints and re-run layout."
                ),
            )
        )
    if outline_violations:
        issues.append(
            _issue(
                "LAYOUT_OUTLINE_VIOLATION",
                "error",
                f"{len(outline_violations)} part(s) are outside the board outline",
                evidence={"refs": outline_violations[:20], "count": len(outline_violations)},
                recommendation=(
                    "Treat this as a placement/floorplan problem first unless "
                    "the outline was explicitly too small."
                ),
            )
        )
    if keepout_violations:
        issues.append(
            _issue(
                "LAYOUT_KEEPOUT",
                "error",
                f"{len(keepout_violations)} part(s) violate keepout geometry",
                evidence={"refs": keepout_violations[:20], "count": len(keepout_violations)},
                recommendation="Move parts clear of mounting/mechanical/no-place regions.",
            )
        )
    if missing_refs:
        issues.append(
            _issue(
                "LAYOUT_MISSING_REF",
                "error",
                f"{len(missing_refs)} schematic part(s) were not placed",
                evidence={"refs": missing_refs[:20], "count": len(missing_refs)},
                recommendation="Fix placement before treating the board as product-ready.",
            )
        )

    off_edge_refs: list[dict] = []
    tolerance_mm = 3.0
    for anchor in intent_plan.get("edge_anchors") or []:
        if not isinstance(anchor, dict):
            continue
        ref = str(anchor.get("ref") or "")
        edge = str(anchor.get("edge") or "").lower()
        part = placed_by_ref.get(ref)
        if not ref or part is None or edge not in {"left", "right", "top", "bottom"}:
            continue
        x = _float(part.get("x_mm"))
        y = _float(part.get("y_mm"))
        distance = {
            "left": abs(x - outline_x_min),
            "right": abs(outline_x_max - x) if outline_w else 0.0,
            "top": abs(y - outline_y_min),
            "bottom": abs(outline_y_max - y) if outline_h else 0.0,
        }[edge]
        # Footprint origins are not always at the mating face, so only flag
        # obvious drift. Geometry-specific checks happen in the layout engine.
        if distance > max(tolerance_mm, 8.0, min(outline_w or 0.0, outline_h or 0.0) * 0.25):
            if _edge_origin_distance_is_ambiguous(
                layout=layout,
                anchor=anchor,
                part=part,
                ref=ref,
                edge=edge,
            ):
                continue
            off_edge_refs.append({"ref": ref, "edge": edge, "distance_mm": round(distance, 2)})
    if off_edge_refs:
        issues.append(
            _issue(
                "EDGE_ANCHOR_OFF_EDGE",
                "warning",
                f"{len(off_edge_refs)} edge-anchored part(s) are far from the requested edge",
                evidence={"refs": off_edge_refs},
                recommendation=(
                    "Preserve edge anchors through soft constraints and local "
                    "refinement; use explicit edge anchors rather than guessed "
                    "fixed positions for mating connectors."
                ),
            )
        )

    drifted: list[dict] = []
    for constraint in intent_plan.get("align_constraints") or []:
        if not isinstance(constraint, dict):
            continue
        axis = str(constraint.get("axis") or "").lower()
        if axis not in {"x", "y"}:
            continue
        refs = [str(ref) for ref in constraint.get("refs") or [] if str(ref) in placed_by_ref]
        if len(refs) < 2:
            continue
        expected = constraint.get("value_mm")
        if expected is None:
            values = [_axis_value(placed_by_ref[ref], axis) for ref in refs]
            expected = sum(values) / len(values)
        expected_f = _float(expected)
        bad_refs = [
            ref
            for ref in refs
            if abs(_axis_value(placed_by_ref[ref], axis) - expected_f) > 1.0
        ]
        if bad_refs:
            drifted.append({"axis": axis, "refs": bad_refs, "expected_mm": round(expected_f, 2)})
    if drifted:
        issues.append(
            _issue(
                "GRID_ALIGNMENT_DRIFT",
                "warning",
                "aligned UI/grid parts drifted from their inferred grid",
                evidence={"constraints": drifted[:8]},
                recommendation=(
                    "Treat grid alignment for jacks, pots, LEDs, switches, "
                    "and keys as mechanical intent unless the user overrides it."
                ),
            )
        )

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

    routing_diagnosis = classify_routing_failure(
        exceptions,
        layout=layout,
        metrics=metrics,
    )
    if routing_diagnosis:
        classification = str(routing_diagnosis.get("classification") or "")
        issue_severity = "warning"
        if any(
            _exception_severity(exc) in ERROR_SEVERITIES
            for exc in exceptions
            if _exception_code(exc) in {
                "ROUTE_UNCONNECTED",
                "ROUTE_TIMEOUT",
                "DRC_UNCONNECTED",
                "DRC_CLEARANCE",
                "DRC_SHORT",
            }
        ):
            issue_severity = "error"
        issues.append(
            _issue(
                "ROUTING_FAILURE_DIAGNOSIS",
                issue_severity,
                (
                    "routing failure classified as "
                    f"{classification.replace('_', '-')}"
                ),
                evidence={
                    "classification": classification,
                    "reason": routing_diagnosis.get("reason"),
                    **(
                        routing_diagnosis.get("evidence")
                        if isinstance(routing_diagnosis.get("evidence"), dict)
                        else {}
                    ),
                },
                recommendation=str(routing_diagnosis.get("recommendation") or ""),
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

    seen_exception_codes: set[str] = set()
    for exc in exceptions:
        code = _exception_code(exc)
        if not code or code in seen_exception_codes:
            continue
        seen_exception_codes.add(code)
        severity = _exception_severity(exc)
        if code in PRODUCT_BLOCKING_EXCEPTION_CODES | PRODUCT_ADVISORY_EXCEPTION_CODES:
            data = _exception_dict(exc)
            issue_severity = (
                "warning"
                if code in PRODUCT_BLOCKING_EXCEPTION_CODES
                else "advisory"
            )
            if severity in ERROR_SEVERITIES:
                issue_severity = "error"
            issues.append(
                _issue(
                    code,
                    issue_severity,
                    str(data.get("message") or code),
                    evidence=data.get("subject") if isinstance(data.get("subject"), dict) else {},
                    recommendation=str(data.get("retry_hint") or ""),
                )
            )

    return issues


def _dedupe_issues(issues: list[dict]) -> list[dict]:
    """Collapse duplicate aggregate issues emitted from validation and exceptions."""
    deduped: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for issue in issues:
        code = str(issue.get("code") or "")
        if code in AGGREGATE_ISSUE_CODES:
            subject = "aggregate"
        else:
            try:
                subject = json.dumps(issue.get("evidence") or {}, sort_keys=True)
            except TypeError:
                subject = str(issue.get("evidence") or {})
        key = (code, subject)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(issue)
    return deduped


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
    visual_review_ready = bool(has_layout and previews)
    issues = _dedupe_issues(_issues(
        exceptions=exceptions,
        layout=layout,
        metrics=metrics,
        artifacts=artifacts,
        placement=placement,
    ))
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
