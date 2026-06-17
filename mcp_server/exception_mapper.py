"""Map engine outcomes into structured DesignException objects."""

from __future__ import annotations

import re
from typing import Iterable

from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity

ROUTING_FAILURE_CODES = {
    ExcCode.ROUTE_UNCONNECTED.value,
    ExcCode.ROUTE_TIMEOUT.value,
    ExcCode.DRC_UNCONNECTED.value,
    ExcCode.DRC_CLEARANCE.value,
    ExcCode.DRC_SHORT.value,
    ExcCode.DRC_COURTYARD.value,
}


def suppress_waived(
    exceptions: Iterable[DesignException],
    spec: CircuitSpec,
) -> list[DesignException]:
    """Drop advisory exceptions whose stable waiver key is in the spec."""

    waived = set(spec.waivers or [])
    return [
        exc
        for exc in exceptions
        if not (exc.severity == Severity.ADVISORY and exc.waiver_key() in waived)
    ]


def order_exceptions_for_agent(
    exceptions: Iterable[DesignException],
) -> list[DesignException]:
    """Order exceptions by actionability while preserving local stage order."""

    severity_rank = {
        Severity.FATAL: 0,
        Severity.ERROR: 1,
        Severity.ADVISORY: 2,
    }
    return [
        exc for _, exc in sorted(
            enumerate(exceptions),
            key=lambda item: (
                severity_rank.get(item[1].severity, 3),
                item[0],
            ),
        )
    ]


def _candidate(
    cid: str,
    action: ActionType,
    params: dict,
    summary: str,
    cost_hint: str = "free",
) -> Candidate:
    return Candidate(
        id=cid,
        action=action,
        params=params,
        human_summary=summary,
        cost_hint=cost_hint,
    )


def _outline_size(outline) -> tuple[float, float, float]:
    if outline is None:
        return 0.0, 0.0, 0.0
    w_mm = float(getattr(outline, "width_mm", 0.0) or 0.0)
    h_mm = float(getattr(outline, "height_mm", 0.0) or 0.0)
    return w_mm, h_mm, w_mm * h_mm


def _outline_is_spacious(outline) -> bool:
    """Heuristic guard against telling agents to grow already-large boards."""

    w_mm, h_mm, area = _outline_size(outline)
    return area >= 3000.0 or max(w_mm, h_mm) >= 80.0


def _code_value(exc: DesignException | dict) -> str:
    code = exc.get("code") if isinstance(exc, dict) else getattr(exc, "code", "")
    return getattr(code, "value", code) or ""


def _subject_dict(exc: DesignException | dict) -> dict:
    subject = exc.get("subject", {}) if isinstance(exc, dict) else getattr(exc, "subject", {})
    return subject if isinstance(subject, dict) else {}


def _as_dict(value) -> dict:
    if isinstance(value, dict):
        return value
    if value is None:
        return {}
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if hasattr(value, "to_dict"):
        try:
            converted = value.to_dict()
        except TypeError:
            converted = {}
        if isinstance(converted, dict):
            return converted
    return {}


def _obj_float(value, attr: str, default: float = 0.0) -> float:
    if isinstance(value, dict):
        raw = value.get(attr, default)
    else:
        raw = getattr(value, attr, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


def _layout_snapshot(layout) -> dict:
    data = _as_dict(layout)
    if data:
        return data

    outline = getattr(layout, "outline", None)
    validation = getattr(layout, "validation", None)
    score = getattr(layout, "score", None)
    placed_parts = getattr(layout, "placed_parts", []) or []
    return {
        "outline": {
            "width_mm": _obj_float(outline, "width_mm"),
            "height_mm": _obj_float(outline, "height_mm"),
            "x_min_mm": _obj_float(outline, "x_min_mm"),
            "y_min_mm": _obj_float(outline, "y_min_mm"),
            "x_max_mm": _obj_float(outline, "x_max_mm"),
            "y_max_mm": _obj_float(outline, "y_max_mm"),
        },
        "placed_parts": [
            _as_dict(part) or {
                "ref": getattr(part, "ref", ""),
                "x_mm": _obj_float(part, "x_mm"),
                "y_mm": _obj_float(part, "y_mm"),
            }
            for part in placed_parts
        ],
        "validation": {
            "overlaps": getattr(validation, "overlaps", []) or [],
            "outline_violations": getattr(validation, "outline_violations", []) or [],
            "keepout_violations": getattr(validation, "keepout_violations", []) or [],
            "cutout_violations": getattr(validation, "cutout_violations", []) or [],
            "missing_refs": getattr(validation, "missing_refs", []) or [],
        },
        "score": {
            "congestion_score": _obj_float(score, "congestion_score"),
            "warnings": getattr(score, "warnings", []) or [],
        },
    }


def _layout_list(layout: dict, section: str, key: str) -> list:
    value = layout.get(section)
    if isinstance(value, dict):
        items = value.get(key)
        return items if isinstance(items, list) else []
    return []


def _outline_from_layout(layout: dict) -> dict:
    outline = layout.get("outline")
    return outline if isinstance(outline, dict) else {}


def _placed_parts(layout: dict) -> list[dict]:
    parts = layout.get("placed_parts")
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


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


def _placement_density(layout: dict, metrics: dict | None) -> dict:
    metrics = metrics or {}
    outline = _outline_from_layout(layout)
    score = layout.get("score") if isinstance(layout.get("score"), dict) else {}
    width = _obj_float(outline, "width_mm")
    height = _obj_float(outline, "height_mm")
    area = width * height if width and height else _obj_float(metrics, "board_area_mm2")
    parts = _placed_parts(layout)
    xs = [_obj_float(part, "x_mm") for part in parts if part.get("x_mm") is not None]
    ys = [_obj_float(part, "y_mm") for part in parts if part.get("y_mm") is not None]
    spread_ratio = 0.0
    max_margin_ratio = 1.0
    compact_outline_area_ratio = _obj_float(score, "compact_outline_area_ratio")
    footprint_envelope_area_ratio = _obj_float(score, "footprint_envelope_area_ratio")
    has_compact_metrics = bool(
        compact_outline_area_ratio or footprint_envelope_area_ratio
    )
    if xs and ys and width > 0 and height > 0:
        spread_w = max(xs) - min(xs)
        spread_h = max(ys) - min(ys)
        spread_ratio = max(0.0, spread_w * spread_h / (width * height))
        x_min = _obj_float(outline, "x_min_mm")
        y_min = _obj_float(outline, "y_min_mm")
        x_max = _obj_float(outline, "x_max_mm", x_min + width) or x_min + width
        y_max = _obj_float(outline, "y_max_mm", y_min + height) or y_min + height
        margins = (
            max(0.0, min(xs) - x_min) / width,
            max(0.0, x_max - max(xs)) / width,
            max(0.0, min(ys) - y_min) / height,
            max(0.0, y_max - max(ys)) / height,
        )
        max_margin_ratio = _obj_float(score, "max_empty_margin_ratio") or max(margins)
    effective_spread_ratio = (
        footprint_envelope_area_ratio
        or compact_outline_area_ratio
        or spread_ratio
    )
    return {
        "part_count": len(parts),
        "width_mm": width,
        "height_mm": height,
        "area_mm2": area,
        "spread_area_ratio": spread_ratio,
        "effective_spread_area_ratio": effective_spread_ratio,
        "compact_outline_area_ratio": compact_outline_area_ratio,
        "footprint_envelope_area_ratio": footprint_envelope_area_ratio,
        "max_margin_ratio": max_margin_ratio,
        "spacious": area >= 3000.0 or max(width, height) >= 80.0,
        "clustered_on_spacious_outline": bool(
            len(parts) >= 4 and area >= 1000.0 and 0.0 < spread_ratio < 0.25
        ),
        "sparse_on_large_outline": bool(
            has_compact_metrics
            and len(parts) >= 4
            and area >= 1000.0
            and 0.0 < effective_spread_ratio < 0.30
            and max_margin_ratio >= 0.30
        ),
        "tight_outline": bool(
            len(parts) >= 4
            and area > 0
            and area < 3000.0
            and (effective_spread_ratio >= 0.65 or max_margin_ratio <= 0.10)
        ),
    }


def _edge_anchor_drift(layout: dict) -> list[dict]:
    intent_plan = layout.get("intent_plan")
    if not isinstance(intent_plan, dict):
        return []
    outline = _outline_from_layout(layout)
    width = _obj_float(outline, "width_mm")
    height = _obj_float(outline, "height_mm")
    x_min = _obj_float(outline, "x_min_mm")
    y_min = _obj_float(outline, "y_min_mm")
    x_max = _obj_float(outline, "x_max_mm", x_min + width) or x_min + width
    y_max = _obj_float(outline, "y_max_mm", y_min + height) or y_min + height
    by_ref = {str(part.get("ref")): part for part in _placed_parts(layout) if part.get("ref")}
    drifted: list[dict] = []
    for anchor in intent_plan.get("edge_anchors") or []:
        if not isinstance(anchor, dict):
            continue
        ref = str(anchor.get("ref") or "")
        edge = str(anchor.get("edge") or "").lower()
        part = by_ref.get(ref)
        if not ref or not part or edge not in {"left", "right", "top", "bottom"}:
            continue
        x = _obj_float(part, "x_mm")
        y = _obj_float(part, "y_mm")
        distance = {
            "left": abs(x - x_min),
            "right": abs(x_max - x) if width else 0.0,
            "top": abs(y - y_min),
            "bottom": abs(y_max - y) if height else 0.0,
        }[edge]
        limit = max(3.0, 8.0, min(width or 0.0, height or 0.0) * 0.25)
        if distance > limit:
            if _edge_origin_distance_is_ambiguous(
                layout=layout,
                anchor=anchor,
                part=part,
                ref=ref,
                edge=edge,
            ):
                continue
            drifted.append({"ref": ref, "edge": edge, "distance_mm": round(distance, 2)})
    return drifted


def _edge_origin_distance_is_ambiguous(
    *,
    layout: dict,
    anchor: dict,
    part: dict,
    ref: str,
    edge: str,
) -> bool:
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
    intent_plan = layout.get("intent_plan")
    intent_kinds: set[str] = set()
    if isinstance(intent_plan, dict):
        intents = intent_plan.get("intents", {}).get(ref) if isinstance(intent_plan.get("intents"), dict) else []
        intent_kinds = {
            str(intent.get("kind") or "")
            for intent in (intents or [])
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
        or bool(intent_kinds & {"edge_connector", "mechanical_mating"})
    )
    return bool(connector_text or has_geometry_intent)


def _routing_refs_hotspot(exceptions: list[DesignException | dict]) -> dict | None:
    for exc in exceptions:
        code = _code_value(exc)
        if code not in {ExcCode.DRC_CLEARANCE.value, ExcCode.DRC_SHORT.value}:
            continue
        subject = _subject_dict(exc)
        same_item = _same_footprint_conflict(subject)
        if same_item:
            same_item["code"] = code
            return same_item
        refs = subject.get("refs")
        try:
            count = int(subject.get("count", 0) or 0)
        except (TypeError, ValueError):
            count = 0
        if isinstance(refs, list) and len(refs) == 1 and count >= 2:
            return {"ref": refs[0], "count": count, "code": code}
    return None


_DRC_ITEM_FOOTPRINT_RE = re.compile(r"\bof\s+([A-Za-z][A-Za-z0-9_*?~+\-]*)")


def _drc_item_footprint_tokens(description: str) -> list[str]:
    return [
        token
        for token in _DRC_ITEM_FOOTPRINT_RE.findall(description or "")
        if token and token != "<no"
    ]


def _same_footprint_conflict(subject: dict) -> dict | None:
    """Detect DRC examples where conflicting pads are on one footprint body.

    KiCad may report inline/custom footprint text using the placeholder
    reference `REF**` in DRC item descriptions. That is not a routability or
    outline-size signal; two conflicting pads on the same footprint indicate a
    footprint/package/pin-mapping problem even when no normal ref like `U1` can
    be extracted.
    """

    examples = subject.get("examples")
    if not isinstance(examples, list):
        return None
    for example in examples:
        if not isinstance(example, dict):
            continue
        descriptions = example.get("descriptions")
        if not isinstance(descriptions, list):
            continue
        tokens: list[str] = []
        for description in descriptions:
            tokens.extend(_drc_item_footprint_tokens(str(description or "")))
        for token in sorted(set(tokens)):
            count = tokens.count(token)
            if count >= 2:
                return {
                    "ref": token,
                    "count": count,
                    "placeholder_ref": "*" in token or "?" in token,
                    "source": "same_footprint_drc_example",
                }
    return None


def classify_routing_failure(
    exceptions: Iterable[DesignException | dict],
    *,
    layout=None,
    metrics: dict | None = None,
) -> dict:
    """Classify routing/DRC failures using placement, footprint, and density evidence."""

    excs = list(exceptions or [])
    codes = {_code_value(exc) for exc in excs}
    if not (codes & ROUTING_FAILURE_CODES):
        return {}

    layout_data = _layout_snapshot(layout)
    validation_evidence = {
        "overlaps": _layout_list(layout_data, "validation", "overlaps"),
        "outline_violations": _layout_list(layout_data, "validation", "outline_violations"),
        "keepout_violations": _layout_list(layout_data, "validation", "keepout_violations"),
        "cutout_violations": _layout_list(layout_data, "validation", "cutout_violations"),
        "missing_refs": _layout_list(layout_data, "validation", "missing_refs"),
    }
    edge_drift = _edge_anchor_drift(layout_data)
    hard_placement = {
        key: value for key, value in validation_evidence.items() if value
    }
    if hard_placement or edge_drift:
        evidence = {**hard_placement}
        if edge_drift:
            evidence["off_edge_anchors"] = edge_drift
        return {
            "classification": "placement_blocked",
            "reason": "placement or floorplan geometry blocks reliable routing",
            "evidence": evidence,
            "recommendation": (
                "Fix placement, connector edge anchors, keepouts, or explicit "
                "floorplan coordinates before changing outline size."
            ),
        }

    if ExcCode.FOOTPRINT_MISSING.value in codes:
        return {
            "classification": "footprint_issue",
            "reason": "one or more footprints are missing before routing can be trusted",
            "evidence": {
                "footprint_exceptions": [
                    _subject_dict(exc)
                    for exc in excs
                    if _code_value(exc) == ExcCode.FOOTPRINT_MISSING.value
                ]
            },
            "recommendation": (
                "Resolve footprint library/package selection first; route "
                "diagnostics after missing footprints are placeholders are unreliable."
            ),
        }

    hotspot = _routing_refs_hotspot(excs)
    if hotspot:
        return {
            "classification": "footprint_issue",
            "reason": "DRC failures cluster on one component footprint or package",
            "evidence": {"hotspot": hotspot},
            "recommendation": (
                "Inspect the listed component's symbol-to-footprint mapping, "
                "package variant, pad clearances, and pin usage before growing the board."
            ),
        }

    density = _placement_density(layout_data, metrics)
    congestion = _obj_float(metrics or {}, "congestion_score")
    score = layout_data.get("score") if isinstance(layout_data.get("score"), dict) else {}
    congestion = max(congestion, _obj_float(score, "congestion_score"))

    if density["sparse_on_large_outline"]:
        return {
            "classification": "sparse_or_underused_outline",
            "reason": "the board is sparse relative to its compact placement envelope",
            "evidence": {"density": density, "congestion_score": congestion},
            "recommendation": (
                "Shrink auto-sized boards toward the compact outline estimate, "
                "or redistribute fixed-outline UI/mechanical parts across the "
                "available area, before considering any outline growth."
            ),
        }

    if density["spacious"] or density["clustered_on_spacious_outline"]:
        return {
            "classification": "congestion_router_limitation",
            "reason": "the board outline is already spacious relative to placement",
            "evidence": {"density": density, "congestion_score": congestion},
            "recommendation": (
                "Treat the failure as routing congestion, layer-budget, or local "
                "placement feedback; defer outline growth unless a human confirms "
                "the mechanical outline should change."
            ),
        }

    if congestion >= 40.0:
        return {
            "classification": "congestion_router_limitation",
            "reason": "routing congestion is high without hard placement violations",
            "evidence": {"density": density, "congestion_score": congestion},
            "recommendation": (
                "Try local placement movement, route-aware regrouping, or more "
                "layers before increasing the whole board outline."
            ),
        }

    if density["tight_outline"]:
        return {
            "classification": "outline_too_small",
            "reason": "placed parts consume most of the available outline",
            "evidence": {"density": density},
            "recommendation": (
                "Outline growth is plausible here because there are no hard "
                "placement or footprint blockers and the placement already fills the board."
            ),
        }

    return {
        "classification": "congestion_router_limitation",
        "reason": "no hard placement, footprint, or small-outline evidence was found",
        "evidence": {"density": density, "congestion_score": congestion},
        "recommendation": (
            "Retry routing or adjust local placement/layer budget before using "
            "outline growth as a last resort."
        ),
    }


def _routing_candidate_priority(action: ActionType, classification: str, params: dict) -> int:
    if classification == "outline_too_small":
        if action == ActionType.REGENERATE and isinstance(params, dict) and params.get("run_options"):
            return 0
        if action == ActionType.SCALE_OUTLINE:
            return 1
        if action == ActionType.SET_LAYERS:
            return 2
        if action == ActionType.REGENERATE:
            return 3
        return 4
    if action == ActionType.REGENERATE:
        return 0
    if action == ActionType.SET_LAYERS:
        return 1
    if action == ActionType.ACCEPT_ADVISORY:
        return 2
    if action == ActionType.SCALE_OUTLINE:
        return 9
    return 3


def _retune_routing_candidates(exc: DesignException, diagnosis: dict) -> None:
    classification = str(diagnosis.get("classification") or "")
    for candidate in exc.candidates:
        if candidate.action == ActionType.SCALE_OUTLINE:
            if classification == "outline_too_small":
                candidate.confidence = max(candidate.confidence, 0.65)
                candidate.human_summary = (
                    "Grow the outline because routing evidence suggests the "
                    "current board is genuinely too tight"
                )
            else:
                candidate.confidence = min(candidate.confidence, 0.2)
                if classification == "sparse_or_underused_outline":
                    candidate.human_summary = (
                        "Defer outline growth; shrink or redistribute the sparse board first"
                    )
                else:
                    candidate.human_summary = (
                        "Defer outline growth; use only after fixing the diagnosed "
                        f"{classification.replace('_', ' ')} cause"
                    )
        elif candidate.action == ActionType.SET_LAYERS and classification == "congestion_router_limitation":
            candidate.confidence = max(candidate.confidence, 0.6)
            candidate.human_summary = (
                "Try layer budget or router settings before increasing board size"
            )
        elif candidate.action == ActionType.REGENERATE and not (
            isinstance(candidate.params, dict) and candidate.params.get("run_options")
        ):
            if classification == "placement_blocked":
                candidate.confidence = max(candidate.confidence, 0.7)
                candidate.human_summary = (
                    "Fix placement/floorplan blockers, then rerun routing"
                )
            elif classification == "footprint_issue":
                candidate.confidence = max(candidate.confidence, 0.7)
                candidate.human_summary = (
                    "Fix the footprint/package issue around the listed refs, then rerun"
                )
            elif classification == "congestion_router_limitation":
                candidate.human_summary = (
                    "Retry routing after local congestion-aware placement changes"
                )
            elif classification == "sparse_or_underused_outline":
                candidate.human_summary = (
                    "Shrink auto outline or redistribute fixed-outline parts before retrying routing"
                )
    exc.candidates = sorted(
        exc.candidates,
        key=lambda cand: _routing_candidate_priority(
            cand.action,
            classification,
            cand.params,
        ),
    )
    for idx, candidate in enumerate(exc.candidates, start=1):
        candidate.id = f"c{idx}"


def enrich_routing_failure_exceptions(
    exceptions: Iterable[DesignException],
    *,
    layout=None,
    metrics: dict | None = None,
) -> list[DesignException]:
    """Attach routing diagnosis and route growth candidates behind better fixes."""

    excs = list(exceptions or [])
    diagnosis = classify_routing_failure(excs, layout=layout, metrics=metrics)
    if not diagnosis:
        return excs

    enriched: list[DesignException] = []
    label = str(diagnosis["classification"]).replace("_", "-")
    for exc in excs:
        if exc.code.value not in ROUTING_FAILURE_CODES:
            enriched.append(exc)
            continue
        updated = exc.model_copy(deep=True)
        subject = dict(updated.subject or {})
        subject["routing_diagnosis"] = diagnosis["classification"]
        subject["routing_diagnosis_reason"] = diagnosis["reason"]
        subject["routing_diagnosis_evidence"] = diagnosis["evidence"]
        updated.subject = subject
        prefix = (
            f"Routing diagnosis: {label}. {diagnosis['reason']}. "
            f"{diagnosis['recommendation']}"
        )
        updated.retry_hint = (
            prefix
            if not updated.retry_hint
            else f"{prefix} Existing guidance: {updated.retry_hint}"
        )
        _retune_routing_candidates(updated, diagnosis)
        enriched.append(updated)
    return enriched


def product_layout_exception(quality: dict) -> DesignException:
    """Convert a failed reviewable product-layout gate into agent feedback."""

    issues = [
        issue for issue in (quality.get("issues") or [])
        if isinstance(issue, dict)
    ]
    issue_codes = [
        str(issue.get("code") or "")
        for issue in issues
        if issue.get("code")
    ]
    blocking_codes = [
        str(issue.get("code") or "")
        for issue in issues
        if str(issue.get("severity") or "").lower() in {"error", "fatal"}
        and issue.get("code")
    ]
    preview_files = (
        quality.get("artifacts", {}).get("preview_files")
        if isinstance(quality.get("artifacts"), dict)
        else []
    )
    top_issues = [
        {
            "code": str(issue.get("code") or ""),
            "severity": str(issue.get("severity") or ""),
            "message": str(issue.get("message") or ""),
            "recommendation": str(issue.get("recommendation") or ""),
        }
        for issue in issues[:5]
    ]
    return DesignException(
        id="e-product-layout",
        code=ExcCode.PRODUCT_LAYOUT_FAILED,
        severity=Severity.ERROR,
        message=(
            "product layout quality gates failed; preview artifacts are "
            "available for human review"
        ),
        subject={
            "review_state": str(quality.get("review", {}).get("state") or ""),
            "product_layout_ok": bool(
                quality.get("gates", {}).get("product_layout_ok")
            ),
            "visual_review_ready": bool(
                quality.get("gates", {}).get("visual_review_ready")
            ),
            "issue_codes": issue_codes,
            "blocking_issue_codes": blocking_codes,
            "top_issues": top_issues,
            "preview_files": list(preview_files or []),
        },
        candidates=[
            _candidate(
                "c1",
                ActionType.REGENERATE,
                {},
                "revise placement or floorplan intent, then rerun with the preview artifacts preserved for review",
                "free",
            )
        ],
        retry_hint=(
            "Do not treat placement_review-only advisories as success when "
            "layout_quality.gates.product_layout_ok is false. Inspect the "
            "preview files and layout_quality issues, then revise SKiDL "
            "placement intent, edge anchors, fixed positions, footprint "
            "choices, or outline constraints before resubmitting."
        ),
    )


def _placement_candidates(
    *,
    scale_params: dict,
    scale_summary: str,
    spacious_summary: str,
    outline,
) -> list[Candidate]:
    regenerate = _candidate(
        "c1",
        ActionType.REGENERATE,
        {},
        spacious_summary,
        "free",
    )
    scale = _candidate(
        "c2",
        ActionType.SCALE_OUTLINE,
        scale_params,
        scale_summary,
        "cheap",
    )
    if _outline_is_spacious(outline):
        scale.confidence = 0.25
        return [regenerate, scale]
    scale.id = "c1"
    regenerate.id = "c2"
    return [scale, regenerate]


def timeout_exception(
    timeout_s: float,
    *,
    artifact_keys: list[str] | None = None,
    stderr: str = "",
) -> DesignException:
    subject = {
        "timeout_s": timeout_s,
        "stage": "timeout",
        "partial_artifacts": sorted(artifact_keys or []),
    }
    if stderr:
        subject["stderr_tail"] = stderr[-4000:]
    return DesignException(
        id="e-timeout",
        code=ExcCode.ENGINE_TIMEOUT,
        severity=Severity.FATAL,
        message=f"engine worker exceeded timeout ({timeout_s:.1f}s)",
        subject=subject,
        candidates=[
            _candidate(
                "c1",
                ActionType.REGENERATE,
                {},
                "retry unchanged with a larger timeout; this is backend/runtime feedback",
                "cheap",
            )
        ],
        retry_hint=(
            "Retry once unchanged with a larger timeout. If partial_artifacts "
            "contains a schematic or PCB, inspect those files before changing "
            "the circuit; if it is empty, report the timeout as a backend "
            "progress/checkpointing issue."
        ),
    )


def crash_exception(
    message: str,
    stderr: str = "",
    *,
    stage: str = "",
    artifact_keys: list[str] | None = None,
) -> DesignException:
    text = f"{message}\n{stderr}"
    if "TerminalClashException" in text:
        subject = {
            "message": message,
            "stage": "schematic_routing",
            "exception": "TerminalClashException",
        }
        if stderr:
            subject["stderr_tail"] = stderr[-4000:]
        if artifact_keys:
            subject["partial_artifacts"] = sorted(artifact_keys)
        return DesignException(
            id="e-sch-route",
            code=ExcCode.SCH_ROUTING_FAILURE,
            severity=Severity.FATAL,
            message=(
                "schematic auto-router failed while rendering the circuit "
                "(TerminalClashException)"
            ),
            subject=subject,
            candidates=[
                _candidate(
                    "c1",
                    ActionType.REGENERATE,
                    {},
                    "retry unchanged; this is a schematic rendering failure, not PCB layout feedback",
                    "cheap",
                )
            ],
            retry_hint=(
                "Retry once unchanged. If it repeats, treat it as a schematic "
                "renderer limitation; preserve the circuit and report the "
                "stderr_tail rather than rewriting unrelated circuitry."
            ),
        )

    if (
        stage in {"schematic_generation", "schematic_routing"}
        and "schematics/route.py" in text
        and (
            "AssertionError" in text
            or "Schematic router internal invariant failed" in text
        )
    ):
        subject = {
            "message": message,
            "stage": "schematic_routing",
            "exception": "AssertionError",
        }
        if stderr:
            subject["stderr_tail"] = stderr[-4000:]
        if artifact_keys:
            subject["partial_artifacts"] = sorted(artifact_keys)
        return DesignException(
            id="e-sch-route",
            code=ExcCode.SCH_ROUTING_FAILURE,
            severity=Severity.FATAL,
            message=(
                "schematic auto-router failed while rendering the circuit "
                "(AssertionError)"
            ),
            subject=subject,
            candidates=[
                _candidate(
                    "c1",
                    ActionType.REGENERATE,
                    {},
                    "retry unchanged; this is a schematic rendering failure, not PCB layout feedback",
                    "cheap",
                )
            ],
            retry_hint=(
                "Retry once unchanged. If it repeats, treat it as a schematic "
                "renderer limitation; preserve the circuit and report the "
                "stderr_tail rather than rewriting unrelated circuitry."
            ),
        )

    if stage == "after_pcb_write" and artifact_keys:
        subject = {
            "message": message,
            "stage": stage,
            "partial_artifacts": sorted(artifact_keys),
        }
        if stderr:
            subject["stderr_tail"] = stderr[-4000:]
        return DesignException(
            id="e-post-artifact-failure",
            code=ExcCode.POST_ARTIFACT_FAILURE,
            severity=Severity.ERROR,
            message=(
                f"{message}; KiCad artifacts were produced before backend "
                "finalization failed"
            ),
            subject=subject,
            candidates=[
                _candidate(
                    "c1",
                    ActionType.REGENERATE,
                    {},
                    "retry unchanged if the fetched KiCad artifacts are unusable",
                    "cheap",
                )
            ],
            retry_hint=(
                "Fetch the run artifacts and inspect the generated schematic/PCB "
                "before changing the circuit. If the files are usable, treat this "
                "as a backend finalization issue rather than circuit feedback."
            ),
        )

    subject = {"message": message}
    if stderr:
        subject["stderr_tail"] = stderr[-4000:]
    if stage:
        subject["stage"] = stage
    if artifact_keys:
        subject["partial_artifacts"] = sorted(artifact_keys)
    return DesignException(
        id="e-crash",
        code=ExcCode.ENGINE_CRASH,
        severity=Severity.FATAL,
        message=message,
        subject=subject,
        candidates=[
            _candidate(
                "c1",
                ActionType.REGENERATE,
                {},
                "retry unchanged; this is a service/backend failure, not circuit feedback",
                "cheap",
            )
        ],
        retry_hint=(
            "Retry once unchanged. If it repeats, treat it as a backend issue; "
            "inspect stderr_tail and any partial_artifacts rather than rewriting "
            "the circuit."
        ),
    )


def spec_malformed_exception(message: str) -> DesignException:
    return DesignException(
        id="e-spec",
        code=ExcCode.SPEC_MALFORMED,
        severity=Severity.FATAL,
        message=message,
        subject={},
        candidates=[],
        retry_hint="submit a complete CircuitSpec JSON object",
    )


def layout_exceptions(layout_result) -> list[DesignException]:
    """Convert a layout result's validation/score signals into exceptions."""

    out: list[DesignException] = []
    validation = getattr(layout_result, "validation", None)
    score = getattr(layout_result, "score", None)
    outline = getattr(layout_result, "outline", None)

    if validation is not None:
        overlaps = getattr(validation, "overlaps", []) or []
        if overlaps:
            pairs = [list(pair) for pair in overlaps]
            n = len(pairs)
            # Scale factor proportional to how many overlaps — more overlaps = bigger jump
            factor = min(1.25 + 0.05 * (n - 1), 2.0)
            scale_params: dict = {"area_factor": factor}
            if outline is not None:
                scale_params["base_w_mm"] = getattr(outline, "width_mm", 50.0)
                scale_params["base_h_mm"] = getattr(outline, "height_mm", 50.0)
            out.append(
                DesignException(
                    id="e-layout-overlap",
                    code=ExcCode.LAYOUT_OVERLAP,
                    severity=Severity.ERROR,
                    message=f"{n} placement overlap(s): {', '.join(f'{p[0]}/{p[1]}' for p in pairs[:5])}"
                            + (f" and {n-5} more" if n > 5 else ""),
                    subject={"pairs": pairs, "count": n},
                    candidates=_placement_candidates(
                        scale_params=scale_params,
                        scale_summary=(
                            f"increase board area by {int((factor-1)*100)}% "
                            f"({n} overlaps) and re-run"
                        ),
                        spacious_summary=(
                            "retry placement after improving floorplan intent; "
                            "the current outline is already spacious"
                        ),
                        outline=outline,
                    ),
                    retry_hint=(
                        "This is a mechanical placement failure. For a "
                        "submit_skidl_code() run, first improve the SKiDL "
                        "source for placement: group related parts with "
                        "@subcircuit, keep decoupling caps in the same block "
                        "as their IC, choose smaller/appropriate connector "
                        "footprints, and put user-facing connectors on "
                        "sensible board edges. If the board is already large "
                        "or sparse, do not keep scaling; fix floorplan intent, "
                        "connector style, or footprint choice first. If you "
                        "used EDA_FLOORPLAN fixed_positions for a panel grid "
                        "or explicit mechanical layout, increase the pitch "
                        "between the overlapping refs in subject.pairs using "
                        "the actual KiCad footprint/courtyard size rather than "
                        "guessing coordinates. If the "
                        "board is genuinely too dense, resubmit with a larger "
                        "outline_mm."
                    ),
                )
            )

        outline_viols = getattr(validation, "outline_violations", []) or []
        if outline_viols:
            n = len(outline_viols)
            factor = min(1.25 + 0.05 * (n - 1), 2.0)
            params = {"area_factor": factor}
            if outline is not None:
                params.update(
                    {
                        "base_w_mm": getattr(outline, "width_mm", 50.0),
                        "base_h_mm": getattr(outline, "height_mm", 50.0),
                    }
                )
            out.append(
                DesignException(
                    id="e-layout-outline",
                    code=ExcCode.LAYOUT_OUTLINE_VIOLATION,
                    severity=Severity.ERROR,
                    message=f"{n} part(s) outside board outline: {', '.join(outline_viols[:5])}"
                            + (f" and {n-5} more" if n > 5 else ""),
                    subject={"refs": outline_viols, "count": n},
                    candidates=_placement_candidates(
                        scale_params=params,
                        scale_summary=(
                            f"grow board outline ({n} violations) and re-run"
                        ),
                        spacious_summary=(
                            "retry placement after improving edge/panel "
                            "intent; the current outline is already spacious"
                        ),
                        outline=outline,
                    ),
                    retry_hint=(
                        "A footprint is outside the board. For a "
                        "submit_skidl_code() run, preserve real mechanical "
                        "constraints, then improve placement intent before "
                        "blindly growing the outline: group circuitry with "
                        "@subcircuit, use appropriate vertical/right-angle "
                        "connector footprints, and place panel/edge parts "
                        "deliberately. For USB, Qwiic/JST, pin headers, "
                        "jacks, barrels, and other board-edge connectors, "
                        "prefer part.edge_preference or "
                        "EDA_FLOORPLAN['edge_anchors'] over guessed "
                        "fixed_positions; KiCad footprint origins are often "
                        "not the footprint center, so a fixed origin can put "
                        "the real connector outside the outline. If the "
                        "requested form factor is too "
                        "small, resubmit with a larger outline_mm. If the "
                        "outline is already large or mechanically fixed, do "
                        "not scale again; fix connector orientation, panel "
                        "placement, or the requested floorplan."
                    ),
                )
            )

        for idx, ref in enumerate(
            getattr(validation, "keepout_violations", []) or [], start=1
        ):
            out.append(
                DesignException(
                    id=f"e-layout-keepout-{idx}",
                    code=ExcCode.LAYOUT_KEEPOUT,
                    severity=Severity.ERROR,
                    message=f"{ref} violates a keepout region",
                    subject={"ref": ref},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.REGENERATE,
                            {},
                            "retry placement unchanged",
                            "free",
                        )
                    ],
                )
            )

        for idx, ref in enumerate(
            getattr(validation, "cutout_violations", []) or [], start=1
        ):
            out.append(
                DesignException(
                    id=f"e-layout-cutout-{idx}",
                    code=ExcCode.LAYOUT_CUTOUT,
                    severity=Severity.ERROR,
                    message=f"{ref} intersects a physical board cutout/aperture",
                    subject={"ref": ref},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.REGENERATE,
                            {},
                            "retry placement with the cutout preserved",
                            "free",
                        )
                    ],
                    retry_hint=(
                        "A footprint intersects physical board-void geometry "
                        "from EDA_FLOORPLAN['cutouts'], ['apertures'], or "
                        "['slots']. Preserve the cutout as mechanical intent; "
                        "move components, adjust the floorplan grid, or change "
                        "the outline around the aperture instead of deleting "
                        "the cutout."
                    ),
                )
            )

        for idx, ref in enumerate(getattr(validation, "missing_refs", []) or [], start=1):
            out.append(
                DesignException(
                    id=f"e-layout-missing-{idx}",
                    code=ExcCode.LAYOUT_MISSING_REF,
                    severity=Severity.ERROR,
                    message=f"{ref} was not placed on the PCB",
                    subject={"ref": ref},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.REGENERATE,
                            {},
                            "retry layout generation unchanged",
                            "free",
                        ),
                        _candidate(
                            "c2",
                            ActionType.REMOVE_PART,
                            {"ref": ref},
                            f"remove {ref} and all of its net endpoints",
                            "expensive",
                        ),
                    ],
                    retry_hint=(
                        "High congestion is a placement/routing-quality "
                        "warning. Before only increasing outline_mm, revise "
                        "the SKiDL source to group functional blocks with "
                        "@subcircuit, reduce unnecessary long cross-board "
                        "nets, use edge connectors deliberately, and keep "
                        "decoupling caps in the same block as their IC. If the "
                        "design is still dense, resubmit with more area."
                    ),
                )
            )

    if score is not None:
        congestion = float(getattr(score, "congestion_score", 0.0) or 0.0)
        if congestion >= 80.0:
            out.append(
                DesignException(
                    id="e-high-congestion",
                    code=ExcCode.HIGH_CONGESTION,
                    severity=Severity.ADVISORY,
                    message=f"layout congestion is high ({congestion:.1f})",
                    subject={"congestion_score": congestion},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.ACCEPT_ADVISORY,
                            {},
                            "accept this congestion advisory for the current spec",
                        ),
                        _candidate(
                            "c2",
                            ActionType.SCALE_OUTLINE,
                            {"area_factor": 1.2},
                            "increase board area by 20% and re-run placement",
                            "cheap",
                        ),
                    ],
                )
            )

        for idx, warning in enumerate(getattr(score, "warnings", []) or [], start=1):
            warning_l = warning.lower()
            if (
                "larger than placed footprint envelope" in warning_l
                or "larger than compact footprint envelope" in warning_l
            ):
                candidates = [
                    _candidate(
                        "c2",
                        ActionType.ACCEPT_ADVISORY,
                        {},
                        "keep this board outline because it reflects real mechanical constraints",
                    )
                ]
                match = re.search(
                    r"estimated compact outline ([0-9.]+)x([0-9.]+)mm",
                    warning,
                )
                if match:
                    w_mm = float(match.group(1))
                    h_mm = float(match.group(2))
                    candidates.insert(
                        0,
                        _candidate(
                            "c1",
                            ActionType.SET_OUTLINE,
                            {"w_mm": w_mm, "h_mm": h_mm},
                            f"shrink outline to about {w_mm:.1f}mm x {h_mm:.1f}mm and re-run",
                            "cheap",
                        ),
                    )
                out.append(
                    DesignException(
                        id=f"e-layout-oversized-{idx}",
                        code=ExcCode.LAYOUT_OVERSIZED,
                        severity=Severity.ADVISORY,
                        message=warning,
                        subject={"warning": warning},
                        candidates=candidates,
                        retry_hint=(
                            "If the outline was just an agent guess, choose the "
                            "smaller outline candidate. If it reflects an "
                            "enclosure, panel, mounting, or keepout constraint, "
                            "accept the advisory and preserve the outline."
                        ),
                    )
                )
                continue
            if "power" not in warning.lower() and "decoupling" not in warning.lower():
                continue
            out.append(
                DesignException(
                    id=f"e-long-power-net-{idx}",
                    code=ExcCode.LONG_POWER_NET,
                    severity=Severity.ADVISORY,
                    message=warning,
                    subject={"warning": warning},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.ACCEPT_ADVISORY,
                            {},
                            "accept this power/layout advisory for the current spec",
                        )
                    ],
                )
            )

    return out


def payload_exceptions(payload: dict, spec: CircuitSpec | None) -> list[DesignException]:
    """Validate/suppress exception dicts returned by the worker."""

    exceptions = [
        exc if isinstance(exc, DesignException) else DesignException.model_validate(exc)
        for exc in payload.get("exceptions", [])
    ]
    if spec is not None:
        exceptions = suppress_waived(exceptions, spec)
    return order_exceptions_for_agent(exceptions)
