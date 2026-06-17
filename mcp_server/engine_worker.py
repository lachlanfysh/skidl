"""Subprocess worker for the EDA generation engine.

Input is a JSON object on stdin. It may be a raw CircuitSpec or an envelope:
{"run_id": "...", "out_dir": "...", "spec": {...}}.
Output is exactly one JSON object on stdout.
"""

from __future__ import annotations

import json
import html
import os
import re
import shutil
import sys
import traceback
import uuid
from pathlib import Path
from resource import RUSAGE_SELF, getrusage

from pydantic import ValidationError

from mcp_server.exception_mapper import (
    crash_exception,
    enrich_routing_failure_exceptions,
    layout_exceptions,
    order_exceptions_for_agent,
    spec_malformed_exception,
    suppress_waived,
)
from schemas.circuit_spec import CircuitSpec
from schemas.enrichment import (
    design_review_exceptions,
    enrich as enrich_spec,
    enrich_blocks,
    is_power_net_name,
)
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity
from schemas.translator import DEFAULT_FP_DIR, DEFAULT_SYM_DIR, translate


EASYEDA_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "corpus", "jlc", "easyeda_cache"
)
_MACOS_KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
_MACOS_KICAD_SUPPORT = "/Applications/KiCad/KiCad.app/Contents/SharedSupport"
_MACOS_KICAD_PYTHON = (
    "/Applications/KiCad/KiCad.app/Contents/Frameworks/"
    "Python.framework/Versions/3.9/bin/python3"
)
DEFAULT_KICAD_PROJECT_RULES = {
    "min_hole_clearance": 0.15,
    "min_through_hole_diameter": 0.2,
}
PREVIEW_BACKGROUND = "#e7e7e3"
PREVIEW_TERRACOTTA = "#A66A53"
PREVIEW_SILKSCREEN = "#15110F"
PREVIEW_EDGE_CUTS = "#4D4843"
PREVIEW_BACK_OUTLINE = "#D8CEC8"
PREVIEW_FRONT_FILL = "#DCC1B3"
_KICAD_PREVIEW_COLOR_MAP = {
    "#C83434": PREVIEW_TERRACOTTA,
    "#D864FF": PREVIEW_TERRACOTTA,
    "#F2EDA1": PREVIEW_SILKSCREEN,
    "#D0D2CD": PREVIEW_EDGE_CUTS,
}


EURORACK_CONTEXT_RE = re.compile(
    r"\b(eurorack|doepfer|modular\s+synth|3u|[0-9]+hp|hp\s+module|"
    r"eurorack[_\s-]*power|box\s+header|shrouded\s+header)\b",
    re.I,
)
MOUNTING_HOLE_DIAMETER_RE = re.compile(
    r"MountingHole[_:-]([0-9]+(?:\.[0-9]+)?)mm",
    re.I,
)


def _default_corner_radius_mm(width_mm: float, height_mm: float) -> float:
    """Default product-board corner radius for rectangular MCP outlines."""
    smaller = min(float(width_mm), float(height_mm))
    if smaller <= 0:
        return 0.0
    return round(min(2.0, max(0.8, smaller * 0.08)), 2)


def _looks_eurorack_context(*texts) -> bool:
    text = " ".join(str(t or "") for t in texts)
    return bool(EURORACK_CONTEXT_RE.search(text))


def _corner_radius_hint(value, width_mm: float, height_mm: float, *context_texts) -> float:
    """Return explicit radius, or a default that avoids Eurorack panel boards."""
    if value is None:
        if _looks_eurorack_context(*context_texts):
            return 0.0
        return _default_corner_radius_mm(width_mm, height_mm)
    return max(0.0, float(value))


def _auto_layout_corner_radius_hint(circuit, explicit_value, *context_texts) -> float | None:
    """Return a radius hint before auto-outline placement starts."""
    if explicit_value is not None:
        return max(0.0, float(explicit_value))
    if _looks_eurorack_context(*context_texts):
        return 0.0

    diameters: list[float] = []
    for part in getattr(circuit, "parts", []) or []:
        text = " ".join(
            str(getattr(part, attr, "") or "")
            for attr in ("name", "value", "footprint", "foot")
        )
        if "mountinghole" not in text.lower().replace(" ", ""):
            continue
        for match in MOUNTING_HOLE_DIAMETER_RE.finditer(text):
            try:
                diameters.append(float(match.group(1)))
            except ValueError:
                pass
    return max(diameters) if diameters else None


def _spec_corner_context(spec: CircuitSpec) -> str:
    fields = [spec.board.name]
    for part in spec.parts:
        fields.extend([
            part.ref,
            part.part or "",
            part.value or "",
            part.footprint or "",
            part.group or "",
        ])
    for net in spec.nets:
        fields.append(net.name)
    return " ".join(fields)


class SkidlCodeExecutionError(Exception):
    """Execution failure with the partially populated SKiDL namespace attached."""

    def __init__(self, original: Exception, code: str, namespace: dict):
        super().__init__(f"{type(original).__name__}: {original}")
        self.original = original
        self.code = code
        self.namespace = namespace
        self.line = _traceback_string_line(original.__traceback__)
        self.line_text = _source_line(code, self.line)


def _traceback_string_line(tb) -> int | None:
    line = None
    for frame in traceback.extract_tb(tb):
        if frame.filename == "<string>":
            line = frame.lineno
    return line


def _source_line(code: str, line: int | None) -> str:
    if not line:
        return ""
    lines = code.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _first_existing_dir(*paths: str) -> str:
    for path in paths:
        if path and os.path.isdir(path):
            return path
    return next((path for path in paths if path), "")


def _kicad_library_dir(env_name: str, linux_default: str, mac_subdir: str) -> str:
    configured = os.environ.get(env_name, "")
    return _first_existing_dir(
        configured,
        linux_default,
        os.path.join(_MACOS_KICAD_SUPPORT, mac_subdir),
        configured or linux_default,
    )


def _configure_kicad_env() -> None:
    """Set KiCad paths broadly so SKiDL stderr keeps the useful signal."""
    symbol_dir = _kicad_library_dir("KICAD9_SYMBOL_DIR", DEFAULT_SYM_DIR, "symbols")
    footprint_dir = _kicad_library_dir("KICAD9_FOOTPRINT_DIR", DEFAULT_FP_DIR, "footprints")
    for version in ("9", "8", "7", "6"):
        os.environ[f"KICAD{version}_SYMBOL_DIR"] = symbol_dir
        os.environ[f"KICAD{version}_FOOTPRINT_DIR"] = footprint_dir
    os.environ["KICAD_SYMBOL_DIR"] = symbol_dir
    os.environ["KICAD_FOOTPRINT_DIR"] = footprint_dir


def _easyeda_fp_dirs() -> list[str]:
    """Collect .pretty dirs from easyeda2kicad cache for footprint resolution."""
    dirs: list[str] = []
    if not os.path.isdir(EASYEDA_CACHE_DIR):
        return dirs
    for lcsc_dir in os.listdir(EASYEDA_CACHE_DIR):
        lcsc_path = os.path.join(EASYEDA_CACHE_DIR, lcsc_dir)
        if not os.path.isdir(lcsc_path):
            continue
        for sub in os.listdir(lcsc_path):
            sub_path = os.path.join(lcsc_path, sub)
            if sub.endswith(".pretty") and os.path.isdir(sub_path):
                dirs.append(sub_path)
    return dirs


def _easyeda_sym_dirs() -> list[str]:
    """Collect symbol dirs from easyeda2kicad cache for SKiDL lib_search_paths."""
    dirs: list[str] = []
    if not os.path.isdir(EASYEDA_CACHE_DIR):
        return dirs
    for lcsc_dir in os.listdir(EASYEDA_CACHE_DIR):
        lcsc_path = os.path.join(EASYEDA_CACHE_DIR, lcsc_dir)
        if os.path.isdir(lcsc_path):
            dirs.append(lcsc_path)
    return dirs


def _safe_name(name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", name.strip()).strip("-")
    return safe or "board"


def _rss_mb() -> float:
    rss = float(getrusage(RUSAGE_SELF).ru_maxrss)
    if sys.platform == "darwin":
        return rss / (1024.0 * 1024.0)
    return rss / 1024.0


def _find_kicad_cli() -> str | None:
    import shutil

    return shutil.which("kicad-cli") or (
        _MACOS_KICAD_CLI if os.path.isfile(_MACOS_KICAD_CLI) else None
    )


def _find_kicad_python() -> str | None:
    override = os.environ.get("KICAD_PYTHON", "")
    if override:
        return override
    candidates = [
        _MACOS_KICAD_PYTHON,
        sys.executable,
    ]
    return next(
        (
            path
            for path in candidates
            if path and os.path.isfile(path) and os.access(path, os.X_OK)
        ),
        None,
    )


def _freerouting_jar_path() -> str:
    candidates = [
        os.environ.get("FREEROUTING_JAR", ""),
        "/opt/freerouting/freerouting-2.0.1.jar",
        str(
            Path(__file__).resolve().parent.parent
            / ".cache"
            / "freerouting"
            / "freerouting-2.0.1.jar"
        ),
    ]
    return next((path for path in candidates if path and Path(path).exists()), candidates[1])


def _brand_preview_png(
    png_path: Path,
    background: str = PREVIEW_BACKGROUND,
) -> str | None:
    """Flatten transparent KiCad preview PNGs onto the light Fysh review surface."""
    try:
        from PIL import Image, ImageColor
    except Exception as exc:
        return f"Pillow unavailable for light preview compositing: {exc}"

    try:
        rgb = ImageColor.getrgb(background)[:3]
        with Image.open(png_path) as image:
            rgba = image.convert("RGBA")
            matte = Image.new("RGBA", rgba.size, (*rgb, 255))
            matte.alpha_composite(rgba)
            matte.convert("RGB").save(png_path)
    except Exception as exc:
        return f"light preview compositing failed: {exc}"
    return None


def _brand_preview_svg(svg_path: Path) -> str | None:
    """Recolor KiCad's flat SVG export for high-contrast human review."""
    try:
        text = svg_path.read_text(encoding="utf-8")
        branded = text
        for source, target in _KICAD_PREVIEW_COLOR_MAP.items():
            branded = re.sub(re.escape(source), target, branded, flags=re.IGNORECASE)
        if branded != text:
            svg_path.write_text(branded, encoding="utf-8")
    except Exception as exc:
        return f"preview SVG recolor failed: {exc}"
    return None


def _part_mockup_bounds(placed, fp_bboxes: dict[str, tuple[float, float]]):
    width, height = fp_bboxes.get(placed.footprint, (2.0, 2.0))
    if placed.rot_deg % 180 == 90:
        width, height = height, width
    return (
        placed.x_mm - width / 2,
        placed.y_mm - height / 2,
        width,
        height,
    )


def _write_layout_mockup_svg(layout_result, out_dir: Path) -> str | None:
    """Write a side-aware SVG placement mockup for human review."""
    try:
        outline = getattr(layout_result, "outline", None)
        placed_parts = list(getattr(layout_result, "placed_parts", []) or [])
        if outline is None or not placed_parts:
            return "side-aware preview skipped: missing outline or placements"

        fp_bboxes = getattr(layout_result, "fp_bboxes", {}) or {}
        intent_plan = getattr(layout_result, "intent_plan", None)
        sides = dict(getattr(intent_plan, "assembly_sides", {}) or {})

        width = max(1.0, float(outline.width_mm))
        height = max(1.0, float(outline.height_mm))
        view_x = float(outline.x_min)
        view_y = float(outline.y_min)
        path = out_dir / "preview_assembly.svg"
        lines = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            (
                f'<svg xmlns="http://www.w3.org/2000/svg" width="{width:.4f}mm" '
                f'height="{height:.4f}mm" viewBox="{view_x:.4f} {view_y:.4f} '
                f'{width:.4f} {height:.4f}">'
            ),
            "<title>Side-aware PCB assembly mockup</title>",
            (
                f'<rect x="{view_x:.4f}" y="{view_y:.4f}" width="{width:.4f}" '
                f'height="{height:.4f}" rx="{getattr(outline, "corner_radius_mm", 0.0):.4f}" '
                f'fill="{PREVIEW_BACKGROUND}" stroke="{PREVIEW_EDGE_CUTS}" '
                'stroke-width="0.35"/>'
            ),
        ]
        for idx, cutout in enumerate(getattr(layout_result, "cutouts", []) or []):
            name = html.escape(str(getattr(cutout, "name", "") or f"cutout-{idx + 1}"))
            shape = str(getattr(cutout, "shape", "rect") or "rect").lower()
            vertices = list(getattr(cutout, "vertices", []) or [])
            if vertices:
                points = " ".join(f"{x:.4f},{y:.4f}" for x, y in vertices)
                lines.append(
                    f'<polygon data-cutout="{name}" points="{points}" '
                    f'fill="{PREVIEW_BACKGROUND}" stroke="{PREVIEW_EDGE_CUTS}" '
                    'stroke-width="0.35"/>'
                )
                continue
            if shape == "circle" and getattr(cutout, "radius_mm", None):
                lines.append(
                    f'<circle data-cutout="{name}" '
                    f'cx="{cutout.center_x_mm:.4f}" cy="{cutout.center_y_mm:.4f}" '
                    f'r="{float(cutout.radius_mm):.4f}" fill="{PREVIEW_BACKGROUND}" '
                    f'stroke="{PREVIEW_EDGE_CUTS}" stroke-width="0.35"/>'
                )
                continue
            lines.append(
                f'<rect data-cutout="{name}" x="{cutout.x_min:.4f}" '
                f'y="{cutout.y_min:.4f}" width="{cutout.width_mm:.4f}" '
                f'height="{cutout.height_mm:.4f}" fill="{PREVIEW_BACKGROUND}" '
                f'stroke="{PREVIEW_EDGE_CUTS}" stroke-width="0.35"/>'
            )

        for placed in sorted(placed_parts, key=lambda p: str(p.ref)):
            x, y, w, h = _part_mockup_bounds(placed, fp_bboxes)
            ref = html.escape(str(placed.ref))
            side = str(
                sides.get(str(placed.ref), getattr(placed, "side", "front"))
                or "front"
            ).lower()
            if side == "back":
                fill = "none"
                stroke = PREVIEW_BACK_OUTLINE
                stroke_width = "0.45"
                dash = ' stroke-dasharray="1.4 0.9"'
                text_fill = PREVIEW_TERRACOTTA
                text_opacity = "0.78"
            elif side == "mechanical":
                fill = "none"
                stroke = PREVIEW_EDGE_CUTS
                stroke_width = "0.35"
                dash = ""
                text_fill = PREVIEW_EDGE_CUTS
                text_opacity = "0.48"
            else:
                fill = PREVIEW_FRONT_FILL
                stroke = PREVIEW_TERRACOTTA
                stroke_width = "0.42"
                dash = ""
                text_fill = PREVIEW_SILKSCREEN
                text_opacity = "0.92"

            lines.append(
                f'<g data-ref="{ref}" data-side="{html.escape(side)}">'
                f'<rect x="{x:.4f}" y="{y:.4f}" width="{w:.4f}" height="{h:.4f}" '
                f'rx="{min(w, h, 1.2) * 0.16:.4f}" fill="{fill}" '
                f'stroke="{stroke}" stroke-width="{stroke_width}"{dash}/>'
                f'<text x="{placed.x_mm:.4f}" y="{placed.y_mm:.4f}" '
                f'font-family="Arial, Helvetica, sans-serif" font-size="1.75" '
                f'text-anchor="middle" dominant-baseline="central" '
                f'fill="{text_fill}" opacity="{text_opacity}">{ref}</text>'
                "</g>"
            )

        lines.append("</svg>")
        path.write_text("\n".join(lines), encoding="utf-8")
    except Exception as exc:
        return f"side-aware preview failed: {exc}"
    return None


def _footprint_pad_count(circuit, fp_dirs: list[str]) -> int:
    if circuit is None:
        return 0
    try:
        from skidl.layout.writer import load_footprint
    except Exception:
        return 0

    total = 0
    for part in circuit.parts:
        footprint = str(getattr(part, "footprint", "") or "")
        if not footprint:
            continue
        try:
            total += len(load_footprint(footprint, fp_dirs).search("pad"))
        except Exception:
            total += len(getattr(part, "pins", []) or [])
    return total


def _pin_count(circuit) -> int:
    if circuit is None:
        return 0
    return sum(len(getattr(part, "pins", []) or []) for part in circuit.parts)


def _power_analysis_metrics(circuit) -> dict:
    """Return compact non-blocking power/simulation summaries for agents."""
    if circuit is None:
        return {"available": False}
    try:
        from skidl.sim.power_tree import analyze_power_tree
        from skidl.sim.rail_sanity import analyze_rail_sanity

        power_tree = analyze_power_tree(circuit=circuit)
        rail_sanity = analyze_rail_sanity(circuit=circuit)

        source_count = sum(
            1 for node in power_tree.nodes if getattr(node, "node_type", "") == "source"
        )
        regulator_count = sum(
            1 for node in power_tree.nodes if getattr(node, "node_type", "") == "regulator"
        )
        known_rails = [
            rail for rail in rail_sanity.rails if getattr(rail, "voltage", None) is not None
        ]
        unknown_rails = [
            rail for rail in rail_sanity.rails if getattr(rail, "voltage", None) is None
        ]

        return {
            "available": True,
            "power_tree": {
                "rail_count": len(power_tree.rails),
                "source_count": source_count,
                "regulator_count": regulator_count,
                "edge_count": len(power_tree.edges),
                "findings": [
                    {
                        "severity": finding.severity,
                        "category": finding.category,
                        "rail": finding.rail,
                        "ref": finding.ref,
                        "message": finding.message,
                    }
                    for finding in power_tree.findings[:12]
                ],
            },
            "rail_sanity": {
                "known_rail_count": len(known_rails),
                "unknown_rail_count": len(unknown_rails),
                "resistors_checked": rail_sanity.resistors_checked,
                "resistors_skipped": rail_sanity.resistors_skipped,
                "assertions_passed": rail_sanity.assertions_passed,
                "assertions_failed": rail_sanity.assertions_failed,
                "assertions_skipped": rail_sanity.assertions_skipped,
                "unknown_rails": [
                    {
                        "net": rail.net_name,
                        "reason": rail.skipped_reason,
                    }
                    for rail in unknown_rails[:12]
                ],
                "findings": [
                    {
                        "severity": finding.severity.value
                        if hasattr(finding.severity, "value")
                        else str(finding.severity),
                        "category": finding.category,
                        "message": finding.message,
                        "nets": list(getattr(finding, "nets", []) or []),
                    }
                    for finding in rail_sanity.findings[:12]
                ],
            },
        }
    except Exception as exc:
        return {"available": False, "error": f"{type(exc).__name__}: {exc}"}


def _metrics(
    layout_result=None,
    circuit=None,
    fp_dirs: list[str] | None = None,
    erc_iterations: int = 0,
) -> dict:
    usage = getrusage(RUSAGE_SELF)
    metrics = {
        "cpu_time_s": usage.ru_utime + usage.ru_stime,
        "peak_rss_mb": _rss_mb(),
        "erc_iterations": erc_iterations,
        "candidates_scored": 0,
        "pin_count": _pin_count(circuit),
        "pad_count": 0,
        "board_area_mm2": 0.0,
    }
    if circuit is not None:
        metrics["pad_count"] = _footprint_pad_count(circuit, fp_dirs or [])
        metrics["power_analysis"] = _power_analysis_metrics(circuit)
    if layout_result is not None:
        metrics["candidates_scored"] = len(getattr(layout_result, "candidates", []) or [])
        outline = getattr(layout_result, "outline", None)
        if outline is not None:
            metrics["board_area_mm2"] = float(outline.width_mm * outline.height_mm)
        score = getattr(layout_result, "score", None)
        if score is not None:
            metrics["layout_score"] = float(getattr(score, "score", 0.0) or 0.0)
            metrics["total_hpwl_mm"] = float(getattr(score, "total_hpwl_mm", 0.0) or 0.0)
            metrics["congestion_score"] = float(
                getattr(score, "congestion_score", 0.0) or 0.0
            )
    return metrics


def _drop_clean_manufacturing_advisories(
    exceptions: list[DesignException],
    *,
    manufacturable: bool,
) -> list[DesignException]:
    """Drop pre-route advisories that clean manufacturing made obsolete."""
    if not manufacturable:
        return list(exceptions)
    return [
        exc
        for exc in exceptions
        if exc.code != ExcCode.HIGH_CONGESTION
    ]


def _json_result(
    *,
    run_id: str,
    ok: bool,
    stage: str,
    spec: CircuitSpec | None = None,
    exceptions=None,
    outputs=None,
    layout=None,
    metrics=None,
    summary: str = "",
) -> dict:
    exceptions = list(exceptions or [])
    if spec is not None:
        exceptions = suppress_waived(exceptions, spec)
    exceptions = order_exceptions_for_agent(exceptions)
    fatal_or_error = any(
        exc.severity in (Severity.FATAL, Severity.ERROR) for exc in exceptions
    )
    status = "succeeded" if ok and not fatal_or_error else "failed"
    if any(getattr(exc, "code", None) and exc.code.value == "ENGINE_TIMEOUT" for exc in exceptions):
        status = "timeout"
    elif any(getattr(exc, "code", None) and exc.code.value == "ENGINE_CRASH" for exc in exceptions):
        status = "crashed"
    elif ok and exceptions:
        status = "succeeded_with_warnings"
    outputs = dict(outputs or {})
    return {
        "run_id": run_id,
        "status": status,
        "ok": bool(ok and not fatal_or_error),
        "stage": stage,
        "summary": summary,
        "exceptions": [exc.model_dump(mode="json") for exc in exceptions],
        "outputs": outputs,
        "artifacts": outputs,
        "layout": dict(layout or {}),
        "metrics": dict(metrics or _metrics()),
    }


def _route_tool_exception(
    *,
    id: str,
    message: str,
    stage: str,
    subject: dict | None = None,
) -> DesignException:
    from schemas.exceptions import DesignException, ExcCode, Severity

    detail = dict(subject or {})
    detail.setdefault("stage", stage)
    return DesignException(
        id=id,
        code=ExcCode.ROUTE_UNAVAILABLE,
        severity=Severity.ERROR,
        message=message,
        subject=detail,
        candidates=[],
        retry_hint=(
            "Manufacturing is incomplete: do not call the board manufacturable "
            "or complete. This is a routing/export tool failure, not proof the "
            "schematic is wrong. Fetch the run artifacts for inspection and "
            "preserve the latest SKiDL source. If the run also reports "
            "congestion, long power nets, outline issues, or DRC errors, revise "
            "board size, edge placement, grouping, or part choices before "
            "retrying; otherwise report the pcbnew/Freerouting failure."
        ),
    )


def _subprocess_signal_message(returncode: int) -> str:
    if returncode < 0:
        return f"signal {-returncode}"
    return f"exit {returncode}"


def _run_pcbnew_child(script: str, *, timeout_s: float = 30.0):
    """Run native pcbnew work in a child so segfaults stay structured."""

    import subprocess as sp

    python_bin = _find_kicad_python() or sys.executable
    return sp.run(
        [python_bin, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _export_dsn_child(pcb_path: str, dsn_path: str, *, timeout_s: float = 30.0):
    script = f"""
import pcbnew
pcb_path = {json.dumps(pcb_path)}
dsn_path = {json.dumps(dsn_path)}
board = pcbnew.LoadBoard(pcb_path)
if not pcbnew.ExportSpecctraDSN(board, dsn_path):
    raise SystemExit(2)
"""
    return _run_pcbnew_child(script, timeout_s=timeout_s)


def _sanitize_pcb_for_dsn_export(pcb_path: str, output_path: str) -> dict[str, int]:
    """Write a DSN-export-friendly PCB copy with unsafe footprint metadata removed."""
    text = Path(pcb_path).read_text()
    removed = {
        "footprint_zones_removed": 0,
        "zone_connect_removed": 0,
        "pad_properties_removed": 0,
    }

    def block_end(src: str, start: int) -> int | None:
        depth = 0
        in_string = False
        escaped = False
        for idx in range(start, len(src)):
            char = src[idx]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    return idx + 1
        return None

    def removal_span(src: str, start: int, end: int) -> tuple[int, int]:
        line_start = src.rfind("\n", 0, start) + 1
        if not src[line_start:start].strip():
            start = line_start
        if end < len(src) and src[end] == "\n":
            end += 1
        return start, end

    def remove_blocks(
        src: str,
        pattern: str,
        count_key: str,
    ) -> str:
        pos = 0
        while match := re.search(pattern, src[pos:]):
            start = pos + match.start()
            end = block_end(src, start)
            if end is None:
                break
            start, end = removal_span(src, start, end)
            src = src[:start] + src[end:]
            removed[count_key] += 1
            pos = start
        return src

    def scrub_footprint_block(block: str) -> str:
        block = remove_blocks(block, r"\(\s*zone\b", "footprint_zones_removed")
        block = remove_blocks(
            block,
            r'\(\s*property\s+"?pad_prop_',
            "pad_properties_removed",
        )
        block, zone_connect_removed = re.subn(
            r"^[ \t]*\(zone_connect\b[^\n]*\)\n?",
            "",
            block,
            flags=re.MULTILINE,
        )
        removed["zone_connect_removed"] += zone_connect_removed
        return block

    scrubbed_chunks: list[str] = []
    pos = 0
    while match := re.search(r"\(\s*footprint\b", text[pos:]):
        start = pos + match.start()
        end = block_end(text, start)
        if end is None:
            break
        scrubbed_chunks.append(text[pos:start])
        scrubbed_chunks.append(scrub_footprint_block(text[start:end]))
        pos = end
    scrubbed_chunks.append(text[pos:])
    scrubbed_text = "".join(scrubbed_chunks)

    if sum(removed.values()):
        Path(output_path).write_text(scrubbed_text)
    return removed


def _write_pcb_without_footprint_zones(pcb_path: str, output_path: str) -> int:
    """Compatibility wrapper for older tests/callers."""

    return _sanitize_pcb_for_dsn_export(
        pcb_path, output_path,
    )["footprint_zones_removed"]


def _export_dsn_with_pcbnew(
    pcb_path: str,
    dsn_path: str,
    *,
    timeout_s: float = 30.0,
) -> DesignException | None:
    import subprocess as sp

    try:
        result = _export_dsn_child(pcb_path, dsn_path, timeout_s=timeout_s)
    except sp.TimeoutExpired:
        return _route_tool_exception(
            id="e-route-dsn-timeout",
            message="DSN export timed out",
            stage="dsn_export",
            subject={"timeout_s": timeout_s},
        )
    except OSError as exc:
        return _route_tool_exception(
            id="e-route-dsn-fail",
            message=f"DSN export could not start: {exc}",
            stage="dsn_export",
            subject={"error": str(exc)},
        )

    if result.returncode != 0:
        sanitized_subject = {}
        if result.returncode < 0:
            sanitized_path = str(
                Path(pcb_path).with_name(
                    Path(pcb_path).stem + ".dsn_export_sanitized.kicad_pcb"
                )
            )
            try:
                removed = _sanitize_pcb_for_dsn_export(
                    pcb_path, sanitized_path,
                )
            except Exception as exc:
                sanitized_subject["sanitized_retry_error"] = str(exc)
                removed = {}
            removed_total = sum(removed.values())
            if removed_total:
                sanitized_subject.update(
                    {f"sanitized_{key}": value for key, value in removed.items()}
                )
                try:
                    retry = _export_dsn_child(
                        sanitized_path,
                        dsn_path,
                        timeout_s=timeout_s,
                    )
                except Exception as exc:
                    sanitized_subject["sanitized_retry_error"] = str(exc)
                else:
                    if retry.returncode == 0 and Path(dsn_path).exists():
                        return None
                    sanitized_subject.update({
                        "sanitized_returncode": retry.returncode,
                        "sanitized_stderr_tail": retry.stderr[-1000:],
                    })
        return _route_tool_exception(
            id="e-route-dsn-fail",
            message=(
                "DSN export failed in isolated pcbnew worker "
                f"({_subprocess_signal_message(result.returncode)})"
            ),
            stage="dsn_export",
            subject={
                "returncode": result.returncode,
                "stderr_tail": result.stderr[-2000:],
                **sanitized_subject,
            },
        )
    if not Path(dsn_path).exists():
        return _route_tool_exception(
            id="e-route-dsn-fail",
            message="DSN export completed but produced no DSN file",
            stage="dsn_export",
        )
    return None


def _import_ses_with_pcbnew(
    pcb_path: str,
    ses_path: str,
    *,
    timeout_s: float = 30.0,
) -> DesignException | None:
    import subprocess as sp

    script = f"""
import pcbnew
pcb_path = {json.dumps(pcb_path)}
ses_path = {json.dumps(ses_path)}
board = pcbnew.LoadBoard(pcb_path)
pcbnew.ImportSpecctraSES(board, ses_path)
pcbnew.SaveBoard(pcb_path, board)
"""
    try:
        result = _run_pcbnew_child(script, timeout_s=timeout_s)
    except sp.TimeoutExpired:
        return _route_tool_exception(
            id="e-route-import-timeout",
            message="SES import timed out",
            stage="ses_import",
            subject={"timeout_s": timeout_s},
        )
    except OSError as exc:
        return _route_tool_exception(
            id="e-route-import-fail",
            message=f"SES import could not start: {exc}",
            stage="ses_import",
            subject={"error": str(exc)},
        )

    if result.returncode != 0:
        sanitized_subject = {}
        if result.returncode < 0:
            sanitized_path = str(
                Path(pcb_path).with_name(
                    Path(pcb_path).stem + ".ses_import_sanitized.kicad_pcb"
                )
            )
            try:
                removed = _sanitize_pcb_for_dsn_export(
                    pcb_path, sanitized_path,
                )
            except Exception as exc:
                sanitized_subject["sanitized_retry_error"] = str(exc)
                removed = {}
            removed_total = sum(removed.values())
            if removed_total:
                sanitized_subject.update(
                    {f"sanitized_{key}": value for key, value in removed.items()}
                )
                try:
                    retry = _import_ses_with_pcbnew(
                        sanitized_path,
                        ses_path,
                        timeout_s=timeout_s,
                    )
                except Exception as exc:
                    sanitized_subject["sanitized_retry_error"] = str(exc)
                else:
                    if retry is None:
                        Path(sanitized_path).replace(pcb_path)
                        return None
                    sanitized_subject.update({
                        "sanitized_retry_code": retry.code.value,
                        "sanitized_retry_subject": retry.subject,
                    })
        return _route_tool_exception(
            id="e-route-import-fail",
            message=(
                "SES import failed in isolated pcbnew worker "
                f"({_subprocess_signal_message(result.returncode)})"
            ),
            stage="ses_import",
            subject={
                "returncode": result.returncode,
                "stderr_tail": result.stderr[-2000:],
                **sanitized_subject,
            },
        )
    return None


def _route_pcb(pcb_path: str, timeout_s: float = 120) -> list:
    """Attempt PCB routing via Freerouting. Returns list of DesignException."""
    import shutil
    import subprocess as sp
    from schemas.exceptions import (
        ActionType, Candidate, DesignException, ExcCode, Severity,
    )

    java_bin = shutil.which("java")
    jar_path = _freerouting_jar_path()
    if not java_bin or not Path(jar_path).exists():
        return [DesignException(
            id="e-route-unavailable",
            code=ExcCode.ROUTE_UNAVAILABLE,
            severity=Severity.ERROR,
            message="Routing unavailable: Freerouting JAR or Java not found — board is unrouted but placement is valid",
            subject={"missing": "java_or_freerouting"},
            candidates=[],
            retry_hint="Do not rewrite the circuit for this. Install/configure Freerouting or inspect the unrouted PCB artifact.",
        )]

    dsn_path = str(Path(pcb_path).with_suffix(".dsn"))
    ses_path = str(Path(pcb_path).with_suffix(".ses"))

    dsn_exception = _export_dsn_with_pcbnew(pcb_path, dsn_path, timeout_s=timeout_s)
    if dsn_exception:
        return [dsn_exception]

    # Inject semantic net classes before routing
    try:
        from mcp_server.dsn_rules import inject_net_classes
        inject_net_classes(dsn_path)
    except Exception:
        pass  # non-fatal — Freerouting works fine with flat classes

    try:
        result = sp.run(
            [java_bin, "-jar", jar_path,
             "-de", dsn_path, "-do", ses_path,
             "-mp", "10", "-mt", "4"],
            timeout=timeout_s,
            capture_output=True, text=True,
        )
    except sp.TimeoutExpired:
        next_timeout = min(max(timeout_s * 2.0, timeout_s + 120.0), 900.0)
        return [DesignException(
            id="e-route-timeout",
            code=ExcCode.ROUTE_TIMEOUT,
            severity=Severity.ERROR,
            message=f"Freerouting exceeded {timeout_s:.0f}s timeout",
            subject={"timeout_s": timeout_s},
            candidates=[
                Candidate(id="c1", action=ActionType.REGENERATE,
                          params={"run_options": {"route_timeout_s": next_timeout}},
                          human_summary=f"Retry unchanged with route_timeout_s={next_timeout:.0f}",
                          confidence=0.75),
                Candidate(id="c2", action=ActionType.SET_LAYERS,
                          params={"layers": 4},
                          human_summary="Switch to 4-layer board for easier routing",
                          confidence=0.5),
                Candidate(id="c3", action=ActionType.SCALE_OUTLINE,
                          params={"area_factor": 1.3},
                          human_summary="Enlarge board 30% only after placement and layer-budget checks",
                          confidence=0.25),
            ],
            retry_hint=(
                f"First resubmit the same design with run_options.route_timeout_s="
                f"{next_timeout:.0f}. If timeout repeats, simplify placement, "
                "use a board stackup the engine supports, and grow the outline "
                "only when the layout is genuinely tight."
            ),
        )]

    # Parse unrouted count from Freerouting stdout
    unrouted = 0
    for line in result.stdout.splitlines():
        if "unrouted" in line.lower():
            import re as _re
            m = _re.search(r"(\d+)\s+unrouted", line, _re.IGNORECASE)
            if m:
                unrouted = int(m.group(1))

    if not Path(ses_path).exists():
        return [DesignException(
            id="e-route-no-ses",
            code=ExcCode.ROUTE_UNCONNECTED,
            severity=Severity.ERROR,
            message="Freerouting produced no output session file",
            subject={"stderr_tail": result.stderr[-2000:]},
            candidates=[
                Candidate(id="c1", action=ActionType.REGENERATE, params={},
                          human_summary="Retry with new placement",
                          confidence=0.4),
            ],
        )]

    import_exception = _import_ses_with_pcbnew(
        pcb_path,
        ses_path,
        timeout_s=timeout_s,
    )
    if import_exception:
        return [import_exception]

    # Freerouting stdout can report stale/non-authoritative unrouted counts
    # after a valid SES import. Once the session imports, KiCad DRC is the
    # source of truth for remaining unconnected copper.
    return []


def _run_drc(pcb_path: str) -> list:
    """Run kicad-cli DRC and parse the JSON report. Returns list of DesignException."""
    import subprocess as sp
    from schemas.exceptions import (
        ActionType, Candidate, DesignException, ExcCode, Severity,
    )

    kicad_cli = _find_kicad_cli()
    if not kicad_cli:
        return [DesignException(
            id="e-drc-unavailable",
            code=ExcCode.DRC_TOOL_FAILURE,
            severity=Severity.ERROR,
            message="kicad-cli not found — DRC skipped",
            subject={"missing": "kicad-cli"},
            candidates=[],
            retry_hint="Do not rewrite the circuit for this. Install/configure kicad-cli or inspect the generated PCB manually.",
        )]

    _ensure_kicad_project_profile(pcb_path)

    drc_json = str(Path(pcb_path).with_name(
        Path(pcb_path).stem + "_drc.json"
    ))

    try:
        result = sp.run(
            [kicad_cli, "pcb", "drc", "--exit-code-violations",
             "-o", drc_json, "--format", "json", pcb_path],
            capture_output=True, timeout=30, text=True,
        )
    except sp.TimeoutExpired:
        return [DesignException(
            id="e-drc-timeout",
            code=ExcCode.DRC_TOOL_FAILURE,
            severity=Severity.ERROR,
            message="kicad-cli DRC timed out",
            subject={"stage": "drc", "timeout_s": 30},
            candidates=[],
            retry_hint="Retry with a larger timeout or inspect the PCB manually; this is DRC tooling feedback.",
        )]

    if not Path(drc_json).exists():
        return [DesignException(
            id="e-drc-no-report",
            code=ExcCode.DRC_TOOL_FAILURE,
            severity=Severity.ERROR,
            message=f"DRC produced no report (exit {result.returncode})",
            subject={"stderr_tail": result.stderr[-1000:]},
            candidates=[],
            retry_hint="Inspect stderr_tail. This is a DRC tool failure unless another exception points to a design error.",
        )]

    try:
        with open(drc_json) as f:
            report = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return [DesignException(
            id="e-drc-parse-fail",
            code=ExcCode.DRC_TOOL_FAILURE,
            severity=Severity.ERROR,
            message=f"Failed to parse DRC report: {exc}",
            subject={"stage": "drc_parse", "error": str(exc)},
            candidates=[],
            retry_hint="Inspect the raw DRC report or rerun DRC. This is tool-output parsing feedback.",
        )]

    return _drc_to_exceptions(report)


def _ensure_kicad_project_profile(pcb_path: str) -> Path:
    """Write or patch a deterministic KiCad project profile.

    kicad-cli creates a default .kicad_pro during DRC if no project exists.
    Owning the small profile here keeps manufacturing checks stable across
    machines and avoids treating common USB-C locating holes and module thermal
    vias as failures under generic KiCad defaults.
    """

    pcb = Path(pcb_path)
    pro_path = pcb.with_suffix(".kicad_pro")
    if pro_path.exists():
        try:
            profile = json.loads(pro_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            profile = {}
    else:
        profile = {}

    profile.setdefault("meta", {"filename": pro_path.name, "version": 1})
    profile.setdefault("project", {}).setdefault(
        "meta", {"filename": pro_path.name, "version": 1}
    )
    rules = (
        profile
        .setdefault("board", {})
        .setdefault("design_settings", {})
        .setdefault("rules", {})
    )
    rules.update(DEFAULT_KICAD_PROJECT_RULES)
    pro_path.write_text(json.dumps(profile, indent=2), encoding="utf-8")
    return pro_path


def _drc_to_exceptions(report: dict) -> list:
    """Map kicad-cli DRC JSON to DesignException objects."""
    from collections import Counter
    from schemas.exceptions import (
        ActionType, Candidate, DesignException, ExcCode, Severity,
    )

    exceptions = []
    violations = report.get("violations", [])
    unconnected = report.get("unconnected_items", [])

    def drc_examples(items: list[dict], limit: int = 8) -> list[dict]:
        examples = []
        for item in items[:limit]:
            descriptions = []
            for sub in item.get("items", []) or []:
                desc = str(sub.get("description") or "").strip()
                if desc:
                    descriptions.append(desc)
            if not descriptions:
                desc = str(item.get("description") or "").strip()
                if desc:
                    descriptions.append(desc)
            example = {"descriptions": descriptions[:4]}
            for key in ("pos", "position", "location"):
                if key in item:
                    example[key] = item[key]
                    break
            examples.append(example)
        return examples

    def refs_from_examples(examples: list[dict]) -> list[str]:
        refs = set()
        for example in examples:
            for desc in example.get("descriptions", []):
                refs.update(re.findall(r"\b[A-Z]{1,4}\d+\b", desc))
        return sorted(refs)

    def nets_from_examples(examples: list[dict]) -> dict[str, int]:
        nets = Counter()
        for example in examples:
            for desc in example.get("descriptions", []):
                for net in re.findall(r"\[([^\]]+)\]", desc):
                    if net and net != "<no net>":
                        nets[net] += 1
        return dict(nets)

    def hotspot_hint(refs: list[str], count: int) -> str:
        if len(refs) == 1 and count >= 3:
            return (
                f" Most examples are on {refs[0]}, so suspect a mismatched "
                "symbol/footprint package, too-tight package for the current "
                "rules, or incorrect pin usage on that part before changing "
                "unrelated circuitry."
            )
        if refs:
            return (
                " Focus the next edit on the listed subject.refs rather than "
                "rewriting unrelated blocks."
            )
        return ""

    if unconnected:
        nets = Counter()
        for item in unconnected:
            for sub in item.get("items", []):
                m = re.search(r'\[([^\]]+)\]', sub.get("description", ""))
                if m:
                    nets[m.group(1)] += 1
        if nets:
            top_nets = ", ".join(f"{n}({c})" for n, c in nets.most_common(5))
            examples = drc_examples(unconnected)
            exceptions.append(DesignException(
                id="e-drc-unconnected",
                code=ExcCode.DRC_UNCONNECTED,
                severity=Severity.ERROR,
                message=f"{sum(nets.values())} unconnected item(s): {top_nets}",
                subject={
                    "nets": dict(nets),
                    "count": sum(nets.values()),
                    "examples": examples,
                    "refs": refs_from_examples(examples),
                },
                retry_hint=(
                    "Routing is incomplete, so the board is not manufacturable. "
                    "Inspect subject.examples for representative DRC items and "
                    "subject.refs for affected components. Do not blindly grow "
                    "the outline if the board is already sparse or oversized; "
                    "first move related parts closer, move edge connectors to "
                    "sensible edges, fix connector/floorplan intent, increase "
                    "layer budget when complexity warrants it, or choose "
                    "smaller/clearer footprints before resubmitting."
                ),
                candidates=[
                    Candidate(id="c1", action=ActionType.REGENERATE, params={},
                              human_summary=(
                                  "Retry with a new placement/floorplan before "
                                  "changing the outline"
                              ),
                              confidence=0.45),
                    Candidate(id="c2", action=ActionType.SET_LAYERS,
                              params={"layers": 4},
                              human_summary=(
                                  "Use 4 layers if routing complexity, not board "
                                  "size, is the limiting factor"
                              ),
                              confidence=0.4),
                    Candidate(id="c3", action=ActionType.SCALE_OUTLINE,
                              params={"area_factor": 1.15},
                              human_summary=(
                                  "Enlarge board 15% only if the current layout "
                                  "is visibly dense"
                              ),
                              confidence=0.25),
                ],
            ))

    clearance_count = 0
    courtyard_count = 0
    short_count = 0
    clearance_items = []
    short_items = []

    for v in violations:
        vtype = v.get("type", "").lower()
        if "clearance" in vtype:
            clearance_count += 1
            clearance_items.append(v)
        elif "courtyard" in vtype:
            courtyard_count += 1
        elif "short" in vtype:
            short_count += 1
            short_items.append(v)

    if clearance_count:
        examples = drc_examples(clearance_items)
        refs = refs_from_examples(examples)
        exceptions.append(DesignException(
            id="e-drc-clearance",
            code=ExcCode.DRC_CLEARANCE,
            severity=Severity.ERROR,
            message=f"{clearance_count} clearance violation(s)",
            subject={
                "count": clearance_count,
                "examples": examples,
                "refs": refs,
                "nets": nets_from_examples(examples),
            },
            retry_hint=(
                "Clearance DRC failed, so the board is not manufacturable. "
                "Inspect subject.examples, then reduce density around those "
                "items or choose footprints/placement with more clearance. "
                "If the board is already spacious, changing footprint choice, "
                "orientation, or local floorplan is usually more relevant than "
                "scaling the whole outline."
                + hotspot_hint(refs, clearance_count)
            ),
            candidates=[
                Candidate(id="c1", action=ActionType.REGENERATE, params={},
                          human_summary=(
                              "Retry placement/routing around the listed DRC "
                              "hotspots"
                          ),
                          confidence=0.4),
                Candidate(id="c2", action=ActionType.SCALE_OUTLINE,
                          params={"area_factor": 1.15},
                          human_summary=(
                              "Enlarge board 15% only if the local hotspot is "
                              "genuinely cramped"
                          ),
                          confidence=0.25),
            ],
        ))

    if courtyard_count:
        exceptions.append(DesignException(
            id="e-drc-courtyard",
            code=ExcCode.DRC_COURTYARD,
            severity=Severity.ADVISORY,
            message=f"{courtyard_count} courtyard overlap(s)",
            subject={"count": courtyard_count},
            candidates=[
                Candidate(id="c1", action=ActionType.ACCEPT_ADVISORY, params={},
                          human_summary="Accept courtyard overlaps",
                          confidence=0.7),
                Candidate(id="c2", action=ActionType.SCALE_OUTLINE,
                          params={"area_factor": 1.15},
                          human_summary="Enlarge board 15% to reduce density",
                          confidence=0.4),
            ],
        ))

    if short_count:
        examples = drc_examples(short_items)
        refs = refs_from_examples(examples)
        exceptions.append(DesignException(
            id="e-drc-short",
            code=ExcCode.DRC_SHORT,
            severity=Severity.ERROR,
            message=f"{short_count} short circuit(s) detected",
            subject={
                "count": short_count,
                "examples": examples,
                "refs": refs,
                "nets": nets_from_examples(examples),
            },
            retry_hint=(
                "Short-circuit DRC failed, so the board is not manufacturable. "
                "Inspect subject.examples to identify the conflicting items; "
                "fix placement/routing or the schematic connection before "
                "resubmitting."
                + hotspot_hint(refs, short_count)
            ),
            candidates=[
                Candidate(id="c1", action=ActionType.REGENERATE, params={},
                          human_summary="Regenerate placement and routing",
                          confidence=0.4),
            ],
        ))

    return exceptions


def _export_gerbers(pcb_path: str, out_dir: Path) -> dict:
    """Export Gerber + drill files via kicad-cli."""
    import subprocess as sp

    kicad_cli = _find_kicad_cli()
    gerber_dir = out_dir / "gerbers"
    result = {
        "ok": False,
        "dir": str(gerber_dir),
        "files": [],
        "errors": [],
    }
    if not kicad_cli:
        result["errors"].append("kicad-cli not found")
        return result

    gerber_dir.mkdir(exist_ok=True)

    try:
        gerber_proc = sp.run(
            [kicad_cli, "pcb", "export", "gerbers",
             "-o", str(gerber_dir) + "/", pcb_path],
            capture_output=True, timeout=30, text=True,
        )
        drill_proc = sp.run(
            [kicad_cli, "pcb", "export", "drill",
             "-o", str(gerber_dir) + "/", pcb_path],
            capture_output=True, timeout=30, text=True,
        )
    except sp.TimeoutExpired:
        result["errors"].append("kicad-cli export timed out")
        return result
    except OSError as exc:
        result["errors"].append(str(exc))
        return result

    if gerber_proc.returncode != 0:
        result["errors"].append(
            f"gerber export exited {gerber_proc.returncode}: {gerber_proc.stderr[-500:]}"
        )
    if drill_proc.returncode != 0:
        result["errors"].append(
            f"drill export exited {drill_proc.returncode}: {drill_proc.stderr[-500:]}"
        )

    files = sorted(path.name for path in gerber_dir.iterdir() if path.is_file())
    result["files"] = files
    has_gerber = any(name.endswith(".gbr") for name in files)
    has_drill = any(name.endswith(".drl") for name in files)
    if not has_gerber:
        result["errors"].append("no .gbr files generated")
    if not has_drill:
        result["errors"].append("no .drl drill file generated")
    result["ok"] = has_gerber and has_drill and not result["errors"]
    return result


def _rasterize_svg_preview(svg_path: Path, png_path: Path, width_px: int = 1600) -> str | None:
    """Rasterize a KiCad SVG export into a flat review PNG when possible."""
    import shutil
    import subprocess as sp

    rsvg = shutil.which("rsvg-convert")
    if rsvg:
        try:
            proc = sp.run(
                [
                    rsvg,
                    "--width",
                    str(width_px),
                    "--output",
                    str(png_path),
                    str(svg_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0 and png_path.exists() and png_path.stat().st_size:
                return None
            return f"rsvg-convert exited {proc.returncode}: {proc.stderr[-500:]}"
        except (OSError, sp.TimeoutExpired) as exc:
            return f"rsvg-convert failed: {exc}"

    sips = shutil.which("sips")
    if sips:
        try:
            proc = sp.run(
                [
                    sips,
                    "-s",
                    "format",
                    "png",
                    "-Z",
                    str(width_px),
                    str(svg_path),
                    "--out",
                    str(png_path),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if proc.returncode == 0 and png_path.exists() and png_path.stat().st_size:
                return None
            return f"sips exited {proc.returncode}: {proc.stderr[-500:]}"
        except (OSError, sp.TimeoutExpired) as exc:
            return f"sips failed: {exc}"

    magick = shutil.which("magick") or shutil.which("convert")
    if magick:
        cmd = [
            magick,
            str(svg_path),
            "-resize",
            f"{width_px}x",
            str(png_path),
        ]
        try:
            proc = sp.run(cmd, capture_output=True, text=True, timeout=30)
            if proc.returncode == 0 and png_path.exists() and png_path.stat().st_size:
                return None
            return f"{Path(magick).name} exited {proc.returncode}: {proc.stderr[-500:]}"
        except (OSError, sp.TimeoutExpired) as exc:
            return f"{Path(magick).name} failed: {exc}"

    try:
        import cairosvg  # type: ignore

        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            output_width=width_px,
        )
        if png_path.exists() and png_path.stat().st_size:
            return None
        return "cairosvg did not produce a PNG"
    except Exception as exc:
        return f"no SVG rasterizer available: {exc}"


def _generate_board_previews(pcb_path: str, out_dir: Path) -> dict:
    """Generate human-reviewable PCB preview artifacts.

    KiCad's SVG export is the deterministic 2D path. The 3D renderer is useful
    context when it works, but may fail on headless servers.
    """
    import subprocess as sp

    kicad_cli = _find_kicad_cli()
    result = {"files": [], "errors": [], "warnings": []}
    if not kicad_cli:
        result["errors"].append("kicad-cli not found")
        result["ok"] = False
        return result

    pcb = Path(pcb_path)
    preview_specs = [
        (
            "top",
            "F.Cu,F.Mask,F.Silkscreen,Edge.Cuts",
            out_dir / "preview_2d_top.svg",
            out_dir / "preview_2d_top.png",
        ),
        (
            "bottom",
            "B.Cu,B.Mask,B.Silkscreen,Edge.Cuts",
            out_dir / "preview_2d_bottom.svg",
            out_dir / "preview_2d_bottom.png",
        ),
        (
            "combined",
            "F.Cu,B.Cu,F.Mask,B.Mask,F.Silkscreen,B.Silkscreen,Edge.Cuts",
            out_dir / "preview_2d_combined.svg",
            out_dir / "preview_2d_combined.png",
        ),
    ]
    png_path = out_dir / "preview_top.png"

    for side, layers, svg_path, flat_png_path in preview_specs:
        try:
            svg = sp.run(
                [
                    kicad_cli,
                    "pcb",
                    "export",
                    "svg",
                    "--output",
                    str(svg_path),
                    "--mode-single",
                    "--page-size-mode",
                    "2",
                    "--exclude-drawing-sheet",
                    "--layers",
                    layers,
                    str(pcb),
                ],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if svg.returncode == 0 and svg_path.exists() and svg_path.stat().st_size:
                brand_svg_warning = _brand_preview_svg(svg_path)
                if brand_svg_warning is not None:
                    result["warnings"].append(brand_svg_warning)
                result["files"].append(svg_path.name)
                if side == "top":
                    legacy_svg_path = out_dir / "preview_top.svg"
                    legacy_svg_path.write_text(
                        svg_path.read_text(encoding="utf-8"),
                        encoding="utf-8",
                    )
                    result["files"].append(legacy_svg_path.name)
                raster_warning = _rasterize_svg_preview(svg_path, flat_png_path)
                if raster_warning is None:
                    result["files"].append(flat_png_path.name)
                    brand_warning = _brand_preview_png(flat_png_path)
                    if brand_warning is not None:
                        result["warnings"].append(brand_warning)
                else:
                    result["warnings"].append(raster_warning)
            else:
                result["errors"].append(
                    f"pcb export {side} svg exited {svg.returncode}: "
                    f"{svg.stderr[-500:]}"
                )
        except sp.TimeoutExpired:
            result["errors"].append(f"pcb export {side} svg timed out")
        except OSError as exc:
            result["errors"].append(f"pcb export {side} svg failed: {exc}")

    try:
        render = sp.run(
            [
                kicad_cli,
                "pcb",
                "render",
                "--output",
                str(png_path),
                "--width",
                "1600",
                "--height",
                "1000",
                "--side",
                "top",
                "--background",
                "opaque",
                "--quality",
                "high",
                str(pcb),
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if render.returncode == 0 and png_path.exists() and png_path.stat().st_size:
            result["files"].append(png_path.name)
        else:
            result["errors"].append(
                f"pcb render exited {render.returncode}: {render.stderr[-500:]}"
            )
    except sp.TimeoutExpired:
        result["errors"].append("pcb render timed out")
    except OSError as exc:
        result["errors"].append(f"pcb render failed: {exc}")

    result["ok"] = bool(result["files"])
    return result


def _placement_review_preview_mode(value) -> str:
    text = str(value or "fast").strip().lower().replace("-", "_")
    if text in {"full", "complete", "kicad", "all"}:
        return "full"
    return "fast"


def _placement_review_only_exception(pipeline_goal: str) -> DesignException:
    return DesignException(
        id="i-placement-review",
        code=ExcCode.PLACEMENT_REVIEW_ONLY,
        severity=Severity.ADVISORY,
        message=(
            "Placement review goal selected: routing, DRC, and "
            "manufacturing export were skipped deliberately."
        ),
        subject={"pipeline_goal": pipeline_goal},
        candidates=[],
        retry_hint=(
            "Show the PCB/preview artifact to the human. After visual "
            "and mechanical placement are acceptable, resubmit with "
            "run_options.pipeline_goal='manufacturing'."
        ),
    )


def _generate_pipeline_previews(
    pcb_path: str,
    out_dir: Path,
    layout_result,
    *,
    pipeline_goal: str,
    preview_mode: str,
) -> dict:
    if pipeline_goal == "placement_review" and preview_mode == "fast":
        previews = {
            "files": [],
            "errors": [],
            "warnings": [
                "placement_review fast preview mode: KiCad SVG/3D preview "
                "exports were skipped to return reviewable artifacts sooner"
            ],
            "mode": "fast",
        }
        side_preview_warning = _write_layout_mockup_svg(layout_result, out_dir)
        _add_layout_mockup_preview(
            previews,
            out_dir,
            side_preview_warning,
            fallback_reason=(
                "because placement_review fast preview mode skipped KiCad "
                "PCB preview export"
            ),
        )
        previews["ok"] = bool(previews.get("files"))
        return previews

    previews = _generate_board_previews(str(pcb_path), out_dir)
    previews["mode"] = "full"
    side_preview_warning = _write_layout_mockup_svg(layout_result, out_dir)
    _add_layout_mockup_preview(previews, out_dir, side_preview_warning)
    return previews


def _add_layout_mockup_preview(
    previews: dict,
    out_dir: Path,
    side_preview_warning: str | None,
    *,
    fallback_reason: str = "because KiCad PCB preview export was unavailable",
) -> None:
    """Add side-aware SVG and PNG fallback when KiCad preview export fails."""
    if side_preview_warning is not None:
        previews.setdefault("warnings", []).append(side_preview_warning)
        return

    files = previews.setdefault("files", [])
    if "preview_assembly.svg" not in files:
        files.append("preview_assembly.svg")

    if "preview_2d_top.png" in files:
        return

    svg_path = out_dir / "preview_assembly.svg"
    png_path = out_dir / "preview_2d_top.png"
    raster_warning = _rasterize_svg_preview(svg_path, png_path)
    if raster_warning is None:
        files.append("preview_2d_top.png")
        brand_warning = _brand_preview_png(png_path)
        if brand_warning is not None:
            previews.setdefault("warnings", []).append(brand_warning)
        previews.setdefault("warnings", []).append(
            "preview_2d_top.png was generated from preview_assembly.svg "
            f"{fallback_reason}"
        )
    else:
        previews.setdefault("warnings", []).append(
            f"layout mockup PNG fallback unavailable: {raster_warning}"
        )


def _generate_bom(circuit, spec=None, out_dir: Path = None) -> Path | None:
    """Generate JLCPCB BOM CSV from circuit parts."""
    from corpus.jlc.footprint_resolver import generate_bom_csv

    parts = []
    spec_parts_by_ref = {}
    if spec is not None:
        for p in getattr(spec, "parts", []):
            spec_parts_by_ref[p.ref] = p

    for part in circuit.parts:
        ref = getattr(part, "ref", "")
        lcsc = getattr(part, "lcsc", "") or ""
        if not lcsc and ref in spec_parts_by_ref:
            lcsc = getattr(spec_parts_by_ref[ref], "lcsc", "") or ""
        parts.append({
            "comment": getattr(part, "value", "") or "",
            "designator": ref,
            "footprint": str(getattr(part, "footprint", "") or ""),
            "lcsc": lcsc,
        })

    if not parts:
        return None

    bom_path = out_dir / "bom.csv"
    generate_bom_csv(parts, str(bom_path))
    return bom_path


def _generate_cpl(placed_parts, out_dir: Path) -> Path | None:
    """Generate JLCPCB CPL (pick-and-place) CSV from placed parts."""
    from corpus.jlc.footprint_resolver import generate_cpl_csv

    placements = []
    for pp in placed_parts:
        placements.append({
            "designator": pp.ref,
            "mid_x": pp.x_mm,
            "mid_y": pp.y_mm,
            "rotation": pp.rot_deg,
            "layer": "Top",
        })

    if not placements:
        return None

    cpl_path = out_dir / "cpl.csv"
    generate_cpl_csv(placements, str(cpl_path))
    return cpl_path


def _generate_manufacturing_files(
    pcb_path: str,
    circuit,
    layout_result,
    out_dir: Path,
    spec=None,
) -> dict:
    """Generate Gerbers, BOM, and CPL for manufacturable boards."""
    mfg = {}

    gerber_result = _export_gerbers(pcb_path, out_dir)
    mfg["gerbers"] = bool(gerber_result.get("ok"))
    mfg["gerber_files"] = list(gerber_result.get("files") or [])
    if gerber_result.get("errors"):
        mfg["gerber_errors"] = list(gerber_result.get("errors") or [])

    bom_path = _generate_bom(circuit, spec=spec, out_dir=out_dir)
    if bom_path and bom_path.exists() and bom_path.stat().st_size > 0:
        mfg["bom"] = str(bom_path)

    cpl_path = _generate_cpl(layout_result.placed_parts, out_dir)
    if cpl_path and cpl_path.exists() and cpl_path.stat().st_size > 0:
        mfg["cpl"] = str(cpl_path)

    return mfg


def _missing_manufacturing_outputs(mfg: dict) -> list[str]:
    missing = []
    if not mfg.get("gerbers"):
        missing.append("gerbers_and_drills")
    if not mfg.get("bom"):
        missing.append("bom.csv")
    if not mfg.get("cpl"):
        missing.append("cpl.csv")
    return missing


def _manufacturing_output_exception(mfg: dict) -> DesignException:
    missing = _missing_manufacturing_outputs(mfg)
    return DesignException(
        id="e-manufacturing-output",
        code=ExcCode.MANUFACTURING_OUTPUT_FAILURE,
        severity=Severity.ERROR,
        message=(
            "Manufacturing export incomplete: missing "
            f"{', '.join(missing)}"
        ),
        subject={
            "missing_outputs": missing,
            "gerber_files": list(mfg.get("gerber_files") or []),
            "gerber_errors": list(mfg.get("gerber_errors") or []),
        },
        candidates=[],
        retry_hint=(
            "Do not call the board manufacturable. Inspect export errors and "
            "the KiCad PCB artifact; this is a manufacturing output gate."
        ),
    )


def _footprint_search_queries(footprint: str) -> list[str]:
    raw = footprint.split(":", 1)[-1]
    readable = re.sub(r"[_-]+", " ", raw).strip()
    queries = [footprint, raw, readable]
    lower = f"{footprint} {raw} {readable}".lower()
    if "usb" in lower and "micro" in lower:
        queries.append("USB Micro-B connector")
    if "usb" in lower and ("usb_c" in lower or "type_c" in lower or "receptacle" in lower):
        queries.append("USB_C_Receptacle USB2.0 16P")
    if ("din" in lower and "5" in lower) or "midi" in lower:
        queries.extend([
            "5-pin DIN MIDI jack footprint",
            "DIN-5 180 degree connector",
        ])
    if "bme280" in lower or "bmp280" in lower or "bme680" in lower or "lga 8" in lower or "lga-8" in lower:
        queries.extend([
            "BME280 Bosch LGA-8 footprint",
            "Bosch LGA-8 sensor footprint",
        ])
    if "testpoint" in lower or "test point" in lower or "test_point" in lower:
        queries.extend([
            "test point pad footprint",
            "TestPoint_Pad_D1.5mm footprint",
        ])
    return [q for i, q in enumerate(queries) if q and q not in queries[:i]]


def _footprint_replacement_candidates(
    footprints: dict[str, str],
) -> tuple[list[Candidate], dict[str, list[str]]]:
    candidates: list[Candidate] = []
    suggestions: dict[str, list[str]] = {}
    seen_pairs: set[tuple[str, str]] = set()
    try:
        from llm.kicad_index import search_footprints
    except Exception:
        return candidates, suggestions

    for ref, old_fp in footprints.items():
        matches: list[str] = []
        for query in _footprint_search_queries(old_fp):
            try:
                found = search_footprints(query, limit=5)
            except Exception:
                continue
            for new_fp in found:
                if new_fp == old_fp or new_fp in matches:
                    continue
                matches.append(new_fp)
        if matches:
            suggestions[ref] = matches[:5]
        for new_fp in matches[:3]:
            pair = (old_fp, new_fp)
            if pair in seen_pairs:
                continue
            seen_pairs.add(pair)
            candidates.append(
                Candidate(
                    id=f"c{len(candidates) + 1}",
                    action=ActionType.REPLACE_FOOTPRINT,
                    params={"old": old_fp, "new": new_fp},
                    human_summary=(
                        f"replace missing footprint {old_fp!r} with {new_fp!r}"
                    ),
                    cost_hint="cheap",
                    confidence=0.75,
                    source="kicad_footprint_search",
                )
            )
            if len(candidates) >= 5:
                return candidates, suggestions
    return candidates, suggestions


def _footprint_missing_exception(exc: FileNotFoundError, circuit=None) -> DesignException:
    message = str(exc)
    refs: list[str] = []
    match = re.search(r"missing footprints:\s*(?P<refs>.+)$", message)
    if match:
        refs = [
            ref.strip()
            for ref in match.group("refs").split(",")
            if ref.strip()
        ]

    footprints = {}
    if circuit is not None:
        for part in getattr(circuit, "parts", []) or []:
            ref = str(getattr(part, "ref", "") or "")
            if refs and ref not in refs:
                continue
            footprint = str(getattr(part, "footprint", "") or "")
            if footprint:
                footprints[ref] = footprint

    subject = {
        "refs": refs,
        "footprints": footprints,
        "message": message,
    }
    candidates, suggestions = _footprint_replacement_candidates(footprints)
    if suggestions:
        subject["suggested_footprints"] = suggestions
    return DesignException(
        id="e-footprint-missing",
        code=ExcCode.FOOTPRINT_MISSING,
        severity=Severity.ERROR,
        message=message,
        subject=subject,
        candidates=candidates,
        retry_hint=(
            "One or more Part(..., footprint=...) values are not available in "
            "the configured KiCad footprint libraries. Use the candidates or "
            "subject.suggested_footprints when present only if they preserve "
            "the user's product intent. If this is a project-local or custom "
            "footprint, read the matching .kicad_mod text and pass it to "
            "submit_skidl_code(custom_footprints={\"Library:Footprint\": "
            "\"(footprint ...)\"}) using the same Library:Footprint name "
            "instead of downgrading the part just to satisfy hosted preflight. "
            "A global EDA_FOOTPRINTS dict in code is still accepted for "
            "compatibility. Do not pass local filesystem paths; hosted workers "
            "only accept submitted footprint text. Otherwise call "
            "search_kicad() for the affected part or footprint, update the "
            "SKiDL code, and resubmit with submit_skidl_code()."
        ),
    )


def _missing_footprint_exception_for_circuit(
    circuit,
    missing: set[str],
) -> DesignException:
    refs: list[str] = []
    missing = {str(fp) for fp in missing if fp}
    for part in getattr(circuit, "parts", []) or []:
        footprint = str(getattr(part, "footprint", "") or "")
        if footprint in missing:
            refs.append(str(getattr(part, "ref", "") or ""))
    message = (
        "INCOMPLETE PCB: "
        f"{len(refs)}/{len(getattr(circuit, 'parts', []) or [])} parts "
        f"missing footprints: {', '.join(refs[:20])}"
    )
    return _footprint_missing_exception(FileNotFoundError(message), circuit)


def _preflight_footprints(circuit, fp_dirs: list[str]) -> DesignException | None:
    from skidl.layout.writer import validate_footprints

    names = {
        str(getattr(part, "footprint", "") or "")
        for part in getattr(circuit, "parts", []) or []
        if str(getattr(part, "footprint", "") or "")
    }
    _valid, missing = validate_footprints(names, fp_dirs)
    if not missing:
        return None
    return _missing_footprint_exception_for_circuit(circuit, missing)


_FOOTPRINT_NAME_RE = re.compile(r"^[A-Za-z0-9_. +@#-]+$")
_INLINE_FOOTPRINT_PATH_KEYS = {
    "file",
    "filename",
    "library_path",
    "path",
    "pretty_path",
    "root",
    "uri",
    "url",
}
_MAX_INLINE_FOOTPRINTS = 64
_MAX_INLINE_FOOTPRINT_BYTES = 512_000
_MAX_INLINE_FOOTPRINT_TOTAL_BYTES = 4_000_000


def _safe_footprint_component(value: object, field: str) -> str:
    text = str(value or "").strip()
    if (
        not text
        or "/" in text
        or "\\" in text
        or ".." in text
        or not _FOOTPRINT_NAME_RE.match(text)
    ):
        raise ValueError(f"invalid footprint {field}: {text!r}")
    return text[:-7] if text.endswith(".pretty") else text


def _reject_inline_footprint_path_refs(entry: dict, source: str) -> None:
    path_keys = sorted(
        str(key)
        for key in entry
        if str(key).strip().lower() in _INLINE_FOOTPRINT_PATH_KEYS
    )
    if path_keys:
        raise ValueError(
            f"{source} does not accept filesystem paths ({', '.join(path_keys)}); "
            "submit KiCad .kicad_mod text as content/kicad_mod instead"
        )


def _inline_footprint_text(value: object, source: str, ref: str) -> str:
    if not isinstance(value, str):
        raise ValueError(
            f"{source} entry {ref} content must be KiCad .kicad_mod text"
        )
    size = len(value.encode("utf-8"))
    if size > _MAX_INLINE_FOOTPRINT_BYTES:
        raise ValueError(
            f"{source} entry {ref} is too large "
            f"({size} bytes; limit {_MAX_INLINE_FOOTPRINT_BYTES})"
        )
    return value


def _validate_inline_footprint_text(
    lib: str,
    name: str,
    content: str,
    source: str,
) -> None:
    ref = f"{lib}:{name}"
    if not content.strip():
        raise ValueError(f"{source} entry {ref} is empty")
    try:
        from simp_sexp import Sexp

        parsed = Sexp(content)
    except Exception as exc:
        raise ValueError(
            f"{source} entry {ref} is not parseable KiCad .kicad_mod text "
            f"({type(exc).__name__})"
        ) from None

    if not parsed or str(parsed[0]).strip('"') != "footprint":
        raise ValueError(
            f"{source} entry {ref} must start with a KiCad (footprint ...) "
            "S-expression"
        )
    declared_name = str(parsed[1]).strip('"') if len(parsed) > 1 else ""
    if declared_name != name:
        raise ValueError(
            f"{source} entry {ref} footprint name mismatch: content declares "
            f"{declared_name!r}"
        )


def _inline_footprint_items(raw, *, source: str) -> list[tuple[str, str, str, str]]:
    """Normalize submitted footprint payloads into (library, name, content, source)."""
    if not raw:
        return []
    items: list[tuple[str, str, str, str]] = []
    if isinstance(raw, dict):
        for key, content in raw.items():
            if isinstance(content, dict):
                _reject_inline_footprint_path_refs(content, source)
                lib = content.get("library") or content.get("lib")
                name = content.get("name") or content.get("footprint")
                text = content.get("content") or content.get("kicad_mod")
            else:
                if ":" not in str(key):
                    raise ValueError(
                        f"{source} dict keys must be 'Library:Footprint'"
                    )
                lib, name = str(key).split(":", 1)
                text = content
            safe_lib = _safe_footprint_component(lib, "library")
            safe_name = _safe_footprint_component(name, "name")
            text = _inline_footprint_text(text, source, f"{safe_lib}:{safe_name}")
            _validate_inline_footprint_text(safe_lib, safe_name, text, source)
            items.append((safe_lib, safe_name, text, source))
    elif isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                raise ValueError(f"{source} list entries must be dicts")
            _reject_inline_footprint_path_refs(entry, source)
            safe_lib = _safe_footprint_component(
                entry.get("library") or entry.get("lib"), "library"
            )
            safe_name = _safe_footprint_component(
                entry.get("name") or entry.get("footprint"), "name"
            )
            text = _inline_footprint_text(
                entry.get("content") or entry.get("kicad_mod"),
                source,
                f"{safe_lib}:{safe_name}",
            )
            _validate_inline_footprint_text(safe_lib, safe_name, text, source)
            items.append((safe_lib, safe_name, text, source))
    else:
        raise ValueError(f"{source} must be a dict or list")
    return items


def _write_inline_footprints(
    raw,
    out_dir: Path,
    *,
    extra_raw=None,
) -> tuple[str | None, dict]:
    """Write submitted custom footprints into a temporary KiCad library root."""
    submitted = _inline_footprint_items(raw, source="EDA_FOOTPRINTS")
    submitted.extend(
        _inline_footprint_items(extra_raw, source="custom_footprints")
    )
    items: list[tuple[str, str, str, str]] = []
    seen: dict[tuple[str, str], tuple[str, str]] = {}
    total_size = 0
    for lib, name, content, source in submitted:
        key = (lib, name)
        prior = seen.get(key)
        if prior is not None:
            prior_content, prior_source = prior
            if prior_content != content:
                raise ValueError(
                    f"duplicate custom footprint {lib}:{name} supplied by "
                    f"{prior_source} and {source} with different content"
                )
            continue
        seen[key] = (content, source)
        total_size += len(content.encode("utf-8"))
        items.append((lib, name, content, source))
    if len(items) > _MAX_INLINE_FOOTPRINTS:
        raise ValueError(
            f"custom footprint bundle has {len(items)} entries; "
            f"limit is {_MAX_INLINE_FOOTPRINTS}"
        )
    if total_size > _MAX_INLINE_FOOTPRINT_TOTAL_BYTES:
        raise ValueError(
            f"custom footprint bundle is too large "
            f"({total_size} bytes; limit {_MAX_INLINE_FOOTPRINT_TOTAL_BYTES})"
        )
    if not items:
        return None, {"count": 0}

    root = out_dir / "_inline_footprints"
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)
    refs: list[str] = []
    for lib, name, content, _source in items:
        lib_dir = root / f"{lib}.pretty"
        lib_dir.mkdir(parents=True, exist_ok=True)
        (lib_dir / f"{name}.kicad_mod").write_text(content, encoding="utf-8")
        refs.append(f"{lib}:{name}")
    return str(root), {"count": len(items), "footprints": refs}


def _normalize_pipeline_goal(value) -> str:
    text = str(value or "manufacturing").strip().lower().replace("-", "_")
    aliases = {
        "place": "placement_review",
        "placement": "placement_review",
        "placement_only": "placement_review",
        "review": "placement_review",
        "review_placement": "placement_review",
        "preview": "placement_review",
    }
    text = aliases.get(text, text)
    if text in {"manufacturing", "placement_review"}:
        return text
    return "manufacturing"


def _floorplan_get(data, *keys: str, default=None):
    if isinstance(data, dict):
        for key in keys:
            if key in data:
                return data.get(key)
        return default
    for key in keys:
        if hasattr(data, key):
            return getattr(data, key)
    return default


def _float_field(data, *keys: str, default: float | None = None) -> float | None:
    for key in keys:
        value = _floorplan_get(data, key)
        if value is not None:
            return float(value)
    return default


def _floorplan_refs(value) -> list[str]:
    if isinstance(value, str):
        refs = [part.strip() for part in value.split(",")]
    elif isinstance(value, (list, tuple, set)):
        refs = [str(part).strip() for part in value]
    else:
        refs = []
    return [ref for ref in refs if ref]


def _floorplan_items(value) -> list:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, (list, tuple)):
        return value
    if value is not None and not isinstance(value, (str, int, float, bool)):
        return [value]
    return []


def _floorplan_collect(floorplan: dict, *keys: str) -> list:
    items: list = []
    for key in keys:
        if key in floorplan:
            items.extend(_floorplan_items(floorplan.get(key)))
    return items


def _floorplan_axis(value) -> str | None:
    text = str(value or "").strip().lower()
    aliases = {
        "col": "x",
        "column": "x",
        "columns": "x",
        "vertical": "x",
        "row": "y",
        "rows": "y",
        "horizontal": "y",
    }
    text = aliases.get(text, text)
    if text in {"x", "y"}:
        return text
    return None


def _floorplan_numeric_pair(value) -> tuple[float, float] | None:
    if isinstance(value, dict):
        try:
            return (
                float(value.get("x_mm", value.get("x", value.get("width_mm")))),
                float(value.get("y_mm", value.get("y", value.get("height_mm")))),
            )
        except (TypeError, ValueError):
            return None
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        try:
            return float(value[0]), float(value[1])
        except (TypeError, ValueError):
            return None
    return None


def _floorplan_side(value) -> str | None:
    from skidl.layout.intent import normalize_assembly_side

    return normalize_assembly_side(value)


def _floorplan_side_counts(sides: dict[str, str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for side in sides.values():
        counts[side] = counts.get(side, 0) + 1
    return counts


def _floorplan_apply_side(
    sides: dict[str, str],
    warnings: list[str],
    ref: str,
    value,
) -> None:
    side = _floorplan_side(value)
    if side is None:
        if value not in (None, ""):
            warnings.append(f"ignored invalid assembly side for {ref}: {value}")
        return
    sides[ref] = side


def _floorplan_cutout_items(floorplan: dict) -> list[dict]:
    items: list = []
    for key, default_shape in (
        ("cutouts", None),
        ("apertures", None),
        ("slots", "slot"),
    ):
        for item in _floorplan_items(floorplan.get(key)):
            if isinstance(item, dict):
                normalized = dict(item)
                normalized.setdefault("_source", key)
                if default_shape is not None:
                    normalized.setdefault("shape", default_shape)
                items.append(normalized)
            elif item is not None:
                items.append(item)
    return items


def _floorplan_vertices(value) -> list[tuple[float, float]]:
    vertices = []
    if not isinstance(value, list):
        return vertices
    for point in value:
        if isinstance(point, dict):
            try:
                vertices.append((float(point["x"]), float(point["y"])))
            except (TypeError, ValueError, KeyError):
                return []
        elif isinstance(point, (list, tuple)) and len(point) >= 2:
            try:
                vertices.append((float(point[0]), float(point[1])))
            except (TypeError, ValueError):
                return []
    return vertices if len(vertices) >= 3 else []


def _floorplan_cutout(item: dict):
    from skidl.layout import BoardCutout

    if not isinstance(item, dict) and all(
        hasattr(item, attr) for attr in ("x_min", "y_min", "x_max", "y_max")
    ):
        return BoardCutout(
            x_min=float(getattr(item, "x_min")),
            y_min=float(getattr(item, "y_min")),
            x_max=float(getattr(item, "x_max")),
            y_max=float(getattr(item, "y_max")),
            shape=str(getattr(item, "shape", "rect") or "rect"),
            name=str(getattr(item, "name", "") or ""),
            vertices=list(getattr(item, "vertices", []) or []),
            radius_mm=getattr(item, "radius_mm", None),
        )

    shape = str(
        item.get("shape")
        or item.get("kind")
        or item.get("type")
        or "rect"
    ).strip().lower()
    if shape in {"rectangle", "rectangular", "box"}:
        shape = "rect"
    elif shape in {"round", "circular", "hole"}:
        shape = "circle"
    elif shape in {"polygonal", "poly"}:
        shape = "polygon"
    elif shape in {"rounded_slot", "slotted"}:
        shape = "slot"

    name = str(item.get("name") or item.get("id") or item.get("ref") or "").strip()
    vertices = _floorplan_vertices(item.get("vertices") or item.get("points"))
    if vertices:
        xs = [x for x, _ in vertices]
        ys = [y for _, y in vertices]
        return BoardCutout(
            x_min=min(xs),
            y_min=min(ys),
            x_max=max(xs),
            y_max=max(ys),
            shape="polygon",
            name=name,
            vertices=vertices,
        )

    radius = _float_field(item, "radius_mm", "r_mm", "radius")
    diameter = _float_field(item, "diameter_mm", "d_mm", "diameter")
    if radius is None and diameter is not None:
        radius = diameter / 2
    center = _floorplan_numeric_pair(
        item.get("center")
        or item.get("center_mm")
        or item.get("position")
        or item.get("position_mm")
    )
    cx = _float_field(item, "center_x_mm", "cx_mm", "x_mm", "x")
    cy = _float_field(item, "center_y_mm", "cy_mm", "y_mm", "y")
    if center is not None:
        cx, cy = center
    if shape == "circle" or radius is not None:
        if cx is None or cy is None or radius is None or radius <= 0:
            raise ValueError("circle cutout requires center and positive radius")
        return BoardCutout(
            x_min=cx - radius,
            y_min=cy - radius,
            x_max=cx + radius,
            y_max=cy + radius,
            shape="circle",
            name=name,
            radius_mm=radius,
        )

    start = _floorplan_numeric_pair(item.get("start") or item.get("start_mm"))
    end = _floorplan_numeric_pair(item.get("end") or item.get("end_mm"))
    width = _float_field(item, "width_mm", "w_mm", "width")
    height = _float_field(item, "height_mm", "h_mm", "height")
    if shape == "slot" and start is not None and end is not None:
        slot_w = width if width is not None else height
        if slot_w is None or slot_w <= 0:
            raise ValueError("slot cutout requires positive width_mm")
        half = slot_w / 2
        return BoardCutout(
            x_min=min(start[0], end[0]) - half,
            y_min=min(start[1], end[1]) - half,
            x_max=max(start[0], end[0]) + half,
            y_max=max(start[1], end[1]) + half,
            shape="slot",
            name=name,
        )

    if all(key in item for key in ("x_min", "y_min", "x_max", "y_max")):
        x_min = float(item["x_min"])
        y_min = float(item["y_min"])
        x_max = float(item["x_max"])
        y_max = float(item["y_max"])
    else:
        if width is None:
            width = _float_field(item, "w", "dx_mm", "dx")
        if height is None:
            height = _float_field(item, "h", "dy_mm", "dy")
        if cx is None or cy is None or width is None or height is None:
            raise ValueError("rect cutout requires bounds or center plus size")
        x_min = cx - width / 2
        y_min = cy - height / 2
        x_max = cx + width / 2
        y_max = cy + height / 2
    if x_max <= x_min or y_max <= y_min:
        raise ValueError("cutout bounds must have positive area")
    return BoardCutout(
        x_min=x_min,
        y_min=y_min,
        x_max=x_max,
        y_max=y_max,
        shape=shape if shape in {"rect", "slot", "polygon"} else "rect",
        name=name,
    )


def _floorplan_constraints(floorplan) -> tuple[object | None, dict]:
    """Build layout constraints from a submitted EDA_FLOORPLAN dict."""
    if floorplan is None:
        return None, {}
    if not isinstance(floorplan, dict):
        attrs = (
            "outline",
            "fixed",
            "fixed_positions",
            "edge_anchors",
            "keepouts",
            "cutouts",
            "zones",
            "align",
            "distribute",
        )
        if not any(hasattr(floorplan, attr) for attr in attrs):
            return None, {}
        floorplan = {
            attr: getattr(floorplan, attr)
            for attr in attrs
            if hasattr(floorplan, attr)
        }

    from skidl.layout import (
        AlignConstraint,
        AnchorZone,
        BoardOutline,
        BoardCutout,
        DistributeConstraint,
        EdgeAnchor,
        FixedPosition,
        KeepOut,
        LayoutConstraints,
    )

    fixed = []
    edge_anchors = []
    keepouts = []
    cutouts: list[BoardCutout] = []
    zones = []
    align = []
    distribute = []
    assembly_sides: dict[str, str] = {}
    edge_anchor_attrs = []
    warnings = []
    grid_count = 0
    grid_fixed_count = 0
    explicit_fixed_refs: set[str] = set()
    grid_refs: set[str] = set()

    for item in _floorplan_collect(floorplan, "fixed_positions", "fixed", "positions"):
        ref_value = _floorplan_get(item, "ref", "reference")
        if not ref_value:
            warnings.append("ignored fixed_position without ref")
            continue
        ref = str(ref_value)
        try:
            x_mm = _float_field(item, "x_mm", "x")
            y_mm = _float_field(item, "y_mm", "y")
            if x_mm is None or y_mm is None:
                pair = _floorplan_numeric_pair(
                    _floorplan_get(
                        item,
                        "position",
                        "position_mm",
                        "xy",
                        "center",
                        "center_mm",
                    )
                )
                if pair is not None:
                    x_mm, y_mm = pair
            if x_mm is None or y_mm is None:
                raise ValueError("missing x_mm/y_mm")
            fixed.append(
                FixedPosition(
                    ref=ref,
                    x_mm=x_mm,
                    y_mm=y_mm,
                    rot_deg=_float_field(item, "rotation_deg", "rot_deg", default=0.0) or 0.0,
                )
            )
        except (TypeError, ValueError, KeyError) as exc:
            warnings.append(f"ignored fixed_position for {ref}: {exc}")
            continue
        explicit_fixed_refs.add(ref)
        _floorplan_apply_side(
            assembly_sides,
            warnings,
            ref,
            _floorplan_get(item, "side", "assembly_side"),
        )

    for item in _floorplan_collect(
        floorplan,
        "edge_anchors",
        "edge_connectors",
        "edge_connections",
    ):
        ref_value = _floorplan_get(item, "ref", "reference")
        edge_value = _floorplan_get(item, "edge", "edge_preference")
        if not ref_value or not edge_value:
            warnings.append("ignored edge_anchor without ref/edge")
            continue
        ref = str(ref_value)
        try:
            edge = str(edge_value)
            offset = _float_field(item, "offset_mm")
            rot = _float_field(item, "rotation_deg", "rot_deg")
            edge_anchors.append(
                EdgeAnchor(
                    ref=ref,
                    edge=edge,
                    offset_mm=offset,
                    inset_mm=_float_field(item, "inset_mm", default=0.5) or 0.5,
                    rot_deg=rot,
                )
            )
            edge_anchor_attrs.append(
                {
                    "ref": ref,
                    "edge": edge,
                    "offset_mm": offset,
                    "rot_deg": rot,
                }
            )
        except (TypeError, ValueError) as exc:
            warnings.append(f"ignored edge_anchor for {ref}: {exc}")
            continue
        _floorplan_apply_side(
            assembly_sides,
            warnings,
            ref,
            _floorplan_get(item, "side", "assembly_side", "placement_side"),
        )

    for item in _floorplan_collect(floorplan, "keepouts", "no_place", "no_place_zones"):
        try:
            keepouts.append(
                KeepOut(
                    x_min=float(_floorplan_get(item, "x_min")),
                    y_min=float(_floorplan_get(item, "y_min")),
                    x_max=float(_floorplan_get(item, "x_max")),
                    y_max=float(_floorplan_get(item, "y_max")),
                    allowed_refs=_floorplan_refs(
                        _floorplan_get(item, "allowed_refs", default=[])
                    ),
                )
            )
        except (TypeError, ValueError, KeyError) as exc:
            warnings.append(f"ignored keepout: {exc}")

    for item in _floorplan_cutout_items(floorplan):
        try:
            cutouts.append(_floorplan_cutout(item))
        except (TypeError, ValueError, KeyError) as exc:
            warnings.append(f"ignored cutout: {exc}")

    for item in _floorplan_collect(floorplan, "zones", "anchor_zones"):
        try:
            zones.append(
                AnchorZone(
                    group_name=str(
                        _floorplan_get(item, "group_name", "name", default="zone")
                        or "zone"
                    ),
                    x_min=float(_floorplan_get(item, "x_min")),
                    y_min=float(_floorplan_get(item, "y_min")),
                    x_max=float(_floorplan_get(item, "x_max")),
                    y_max=float(_floorplan_get(item, "y_max")),
                    refs=_floorplan_refs(_floorplan_get(item, "refs", default=[])),
                )
            )
        except (TypeError, ValueError, KeyError) as exc:
            warnings.append(f"ignored anchor zone: {exc}")

    def _add_align(item, *, source: str) -> None:
        refs = _floorplan_refs(_floorplan_get(item, "refs"))
        axis = _floorplan_axis(_floorplan_get(item, "axis"))
        if len(refs) < 2 or axis is None:
            warnings.append(f"ignored {source} align constraint without refs/axis")
            return
        try:
            align.append(
                AlignConstraint(
                    refs=refs,
                    axis=axis,
                    value_mm=_float_field(item, "value_mm", "value"),
                )
            )
        except (TypeError, ValueError) as exc:
            warnings.append(f"ignored {source} align constraint: {exc}")

    def _add_distribute(item, *, source: str) -> None:
        refs = _floorplan_refs(_floorplan_get(item, "refs"))
        axis = _floorplan_axis(_floorplan_get(item, "axis"))
        if len(refs) < 2 or axis is None:
            warnings.append(f"ignored {source} distribute constraint without refs/axis")
            return
        try:
            distribute.append(
                DistributeConstraint(
                    refs=refs,
                    axis=axis,
                    start_mm=_float_field(item, "start_mm", "start"),
                    end_mm=_float_field(item, "end_mm", "end"),
                )
            )
        except (TypeError, ValueError) as exc:
            warnings.append(f"ignored {source} distribute constraint: {exc}")

    for item in _floorplan_collect(floorplan, "align", "align_constraints"):
        _add_align(item, source="floorplan")

    for item in _floorplan_collect(
        floorplan,
        "distribute",
        "distribute_constraints",
    ):
        _add_distribute(item, source="floorplan")

    def _grid_spacing(item: dict, axis: str) -> float | None:
        key_sets = {
            "x": ("dx_mm", "pitch_x_mm", "x_pitch_mm", "col_pitch_mm", "column_pitch_mm"),
            "y": ("dy_mm", "pitch_y_mm", "y_pitch_mm", "row_pitch_mm"),
        }
        spacing = _float_field(item, *key_sets[axis])
        if spacing is not None:
            return spacing
        pair = _floorplan_numeric_pair(
            _floorplan_get(item, "pitch_mm", "pitch")
        )
        if pair is not None:
            return pair[0] if axis == "x" else pair[1]
        scalar = _floorplan_get(item, "pitch_mm", "pitch")
        try:
            return float(scalar)
        except (TypeError, ValueError):
            return None

    def _grid_origin(item: dict) -> tuple[float | None, float | None]:
        pair = _floorplan_numeric_pair(
            _floorplan_get(item, "origin", "origin_mm", "position", "position_mm")
        )
        if pair is not None:
            return pair
        return (
            _float_field(item, "x_mm", "x0_mm", "origin_x_mm"),
            _float_field(item, "y_mm", "y0_mm", "origin_y_mm"),
        )

    for grid in _floorplan_collect(floorplan, "grids", "grid"):
        refs = _floorplan_refs(_floorplan_get(grid, "refs"))
        if not refs:
            warnings.append("ignored grid without refs")
            continue
        try:
            cols = int(_floorplan_get(grid, "cols", "columns", default=0) or 0)
            rows = int(_floorplan_get(grid, "rows", default=0) or 0)
        except (TypeError, ValueError) as exc:
            warnings.append(f"ignored grid with invalid rows/cols: {exc}")
            continue
        if rows <= 0 and cols <= 0:
            cols = len(refs)
            rows = 1
        elif cols <= 0:
            cols = max(1, (len(refs) + rows - 1) // rows)
        elif rows <= 0:
            rows = max(1, (len(refs) + cols - 1) // cols)
        grid_count += 1
        grid_refs.update(refs)
        x0, y0 = _grid_origin(grid)
        dx = _grid_spacing(grid, "x")
        dy = _grid_spacing(grid, "y")
        rot = _float_field(grid, "rotation_deg", "rot_deg", default=0.0) or 0.0
        side = _floorplan_get(grid, "side", "assembly_side")

        for row in range(rows):
            row_refs = refs[row * cols:(row + 1) * cols]
            if len(row_refs) > 1:
                y_value = y0 + row * dy if y0 is not None and dy is not None else None
                align.append(AlignConstraint(refs=list(row_refs), axis="y", value_mm=y_value))
                x_start = x0 if x0 is not None else None
                x_end = (
                    x0 + (len(row_refs) - 1) * dx
                    if x0 is not None and dx is not None
                    else None
                )
                distribute.append(
                    DistributeConstraint(
                        refs=list(row_refs),
                        axis="x",
                        start_mm=x_start,
                        end_mm=x_end,
                    )
                )

        for col in range(cols):
            col_refs = refs[col::cols][:rows]
            if len(col_refs) > 1:
                x_value = x0 + col * dx if x0 is not None and dx is not None else None
                align.append(AlignConstraint(refs=list(col_refs), axis="x", value_mm=x_value))
                y_start = y0 if y0 is not None else None
                y_end = (
                    y0 + (len(col_refs) - 1) * dy
                    if y0 is not None and dy is not None
                    else None
                )
                distribute.append(
                    DistributeConstraint(
                        refs=list(col_refs),
                        axis="y",
                        start_mm=y_start,
                        end_mm=y_end,
                    )
                )

        if x0 is None or y0 is None or dx is None or dy is None:
            warnings.append("grid kept as align/distribute intent only; missing origin or pitch")
            continue
        for idx, ref in enumerate(refs):
            if ref in explicit_fixed_refs:
                continue
            row = idx // cols
            col = idx % cols
            fixed.append(
                FixedPosition(
                    ref=ref,
                    x_mm=x0 + col * dx,
                    y_mm=y0 + row * dy,
                    rot_deg=rot,
                )
            )
            grid_fixed_count += 1
            _floorplan_apply_side(assembly_sides, warnings, ref, side)

    side_entries = floorplan.get("assembly_sides", floorplan.get("sides", {}))
    if isinstance(side_entries, dict):
        for ref, side in side_entries.items():
            ref_text = str(ref).strip()
            if ref_text:
                _floorplan_apply_side(assembly_sides, warnings, ref_text, side)
    elif isinstance(side_entries, list):
        for item in side_entries:
            if not isinstance(item, dict) or not item.get("ref"):
                warnings.append("ignored assembly side without ref")
                continue
            _floorplan_apply_side(
                assembly_sides,
                warnings,
                str(item["ref"]),
                item.get("side", item.get("assembly_side")),
            )
    elif side_entries:
        warnings.append("ignored assembly_sides that was not dict or list")

    def _outline_from_value(data) -> BoardOutline | None:
        if isinstance(data, BoardOutline):
            return data
        if isinstance(data, (list, tuple)) and len(data) >= 2:
            try:
                return BoardOutline(float(data[0]), float(data[1]))
            except (TypeError, ValueError):
                return None
        if not isinstance(data, dict):
            return None
        try:
            if all(key in data for key in ("x_min", "y_min", "x_max", "y_max")):
                x_min = float(data["x_min"])
                y_min = float(data["y_min"])
                x_max = float(data["x_max"])
                y_max = float(data["y_max"])
            elif all(key in data for key in ("width_mm", "height_mm")):
                x_min = float(data.get("x_min", data.get("x_mm", 0.0)) or 0.0)
                y_min = float(data.get("y_min", data.get("y_mm", 0.0)) or 0.0)
                x_max = x_min + float(data["width_mm"])
                y_max = y_min + float(data["height_mm"])
            elif isinstance(data.get("vertices"), list):
                vertices = [
                    (float(point[0]), float(point[1]))
                    for point in data["vertices"]
                    if isinstance(point, (list, tuple)) and len(point) >= 2
                ]
                if len(vertices) < 3:
                    return None
                return BoardOutline(
                    vertices=vertices,
                    corner_radius_mm=float(data.get("corner_radius_mm", 0.0) or 0.0),
                )
            else:
                return None
            if x_max <= x_min or y_max <= y_min:
                return None
            return BoardOutline(
                vertices=[
                    (x_min, y_min),
                    (x_max, y_min),
                    (x_max, y_max),
                    (x_min, y_max),
                ],
                corner_radius_mm=float(data.get("corner_radius_mm", 0.0) or 0.0),
            )
        except (TypeError, ValueError):
            return None

    explicit_outline = None
    outline_data = floorplan.get("outline") or floorplan.get("board_outline")
    if outline_data is None and all(key in floorplan for key in ("width_mm", "height_mm")):
        outline_data = floorplan
    if outline_data is not None:
        explicit_outline = _outline_from_value(outline_data)
        if explicit_outline is None:
            warnings.append("ignored invalid floorplan outline")

    def _keepout_band_outline() -> BoardOutline | None:
        if explicit_outline is not None or len(keepouts) < 2:
            return None
        x_min = min(keepout.x_min for keepout in keepouts)
        y_min = min(keepout.y_min for keepout in keepouts)
        x_max = max(keepout.x_max for keepout in keepouts)
        y_max = max(keepout.y_max for keepout in keepouts)
        if x_max <= x_min or y_max <= y_min:
            return None
        tol = 1e-6
        horizontal_bands = [
            keepout
            for keepout in keepouts
            if abs(keepout.x_min - x_min) <= tol and abs(keepout.x_max - x_max) <= tol
        ]
        vertical_bands = [
            keepout
            for keepout in keepouts
            if abs(keepout.y_min - y_min) <= tol and abs(keepout.y_max - y_max) <= tol
        ]
        if len(horizontal_bands) < 2 and len(vertical_bands) < 2:
            return None
        for item in fixed:
            if not (x_min - tol <= item.x_mm <= x_max + tol):
                return None
            if not (y_min - tol <= item.y_mm <= y_max + tol):
                return None
        return BoardOutline(
            vertices=[
                (x_min, y_min),
                (x_max, y_min),
                (x_max, y_max),
                (x_min, y_max),
            ]
        )

    def _mounting_hole_outline() -> BoardOutline | None:
        if explicit_outline is not None:
            return None
        hole_positions = [
            item
            for item in fixed
            if re.match(r"^(H|MH)\d+$", str(item.ref), re.I)
            or "mount" in str(item.ref).lower()
        ]
        if len(hole_positions) < 4:
            return None
        xs = sorted({round(float(item.x_mm), 3) for item in hole_positions})
        ys = sorted({round(float(item.y_mm), 3) for item in hole_positions})
        if len(xs) < 2 or len(ys) < 2:
            return None
        x_min, x_max = xs[0], xs[-1]
        y_min, y_max = ys[0], ys[-1]
        if x_min <= 0.0 or y_min <= 0.0 or x_max <= x_min or y_max <= y_min:
            return None

        tol = 1.0
        corners = {
            "tl": False,
            "tr": False,
            "bl": False,
            "br": False,
        }
        for item in hole_positions:
            x = float(item.x_mm)
            y = float(item.y_mm)
            if abs(x - x_min) <= tol and abs(y - y_min) <= tol:
                corners["tl"] = True
            elif abs(x - x_max) <= tol and abs(y - y_min) <= tol:
                corners["tr"] = True
            elif abs(x - x_min) <= tol and abs(y - y_max) <= tol:
                corners["bl"] = True
            elif abs(x - x_max) <= tol and abs(y - y_max) <= tol:
                corners["br"] = True
        if not all(corners.values()):
            return None

        width = x_min + x_max
        height = y_min + y_max
        if width <= x_max or height <= y_max:
            return None
        return BoardOutline(width, height)

    keepout_outline = _keepout_band_outline()
    mounting_outline = None if keepout_outline is not None else _mounting_hole_outline()
    inferred_outline = explicit_outline or keepout_outline or mounting_outline
    metadata = {
        "fixed_positions": len(fixed),
        "edge_anchors": len(edge_anchors),
        "keepouts": len(keepouts),
        "cutouts": len(cutouts),
    }
    fixed_refs = {item.ref for item in fixed}
    if fixed_refs:
        metadata["fixed_refs"] = sorted(fixed_refs)
    if explicit_fixed_refs:
        metadata["explicit_fixed_refs"] = sorted(explicit_fixed_refs)
    if zones:
        metadata["zones"] = len(zones)
    if align:
        metadata["align_constraints"] = len(align)
    if distribute:
        metadata["distribute_constraints"] = len(distribute)
    if grid_count:
        metadata["grids"] = grid_count
        metadata["grid_fixed_positions"] = grid_fixed_count
        metadata["grid_refs"] = sorted(grid_refs)
    if assembly_sides:
        metadata["assembly_sides"] = dict(sorted(assembly_sides.items()))
        metadata["assembly_side_counts"] = _floorplan_side_counts(assembly_sides)
    if edge_anchor_attrs:
        metadata["edge_anchor_refs"] = edge_anchor_attrs
    if cutouts:
        metadata["cutout_shapes"] = [
            cutout.to_dict() if hasattr(cutout, "to_dict") else {}
            for cutout in cutouts
        ]
    if explicit_outline is not None:
        metadata["outline"] = "explicit"
    elif keepout_outline is not None:
        metadata["outline"] = "keepout_bands"
    elif mounting_outline is not None:
        metadata["outline"] = "mounting_holes"
    if warnings:
        metadata["warnings"] = warnings
    constraints = LayoutConstraints(
        fixed=fixed,
        zones=zones,
        edge_anchors=edge_anchors,
        keepouts=keepouts,
        cutouts=cutouts,
        align=align,
        distribute=distribute,
        outline=inferred_outline,
    )
    return constraints, metadata


def _apply_floorplan_part_attributes(circuit, floorplan_meta: dict | None) -> None:
    """Expose parsed floorplan side/edge intent to layout intent inference."""
    if not floorplan_meta:
        return
    parts = {
        str(getattr(part, "ref", "") or ""): part
        for part in getattr(circuit, "parts", []) or []
    }
    for ref, side in (floorplan_meta.get("assembly_sides") or {}).items():
        part = parts.get(str(ref))
        if part is not None:
            setattr(part, "assembly_side", side)
    for anchor in floorplan_meta.get("edge_anchor_refs") or []:
        if not isinstance(anchor, dict):
            continue
        part = parts.get(str(anchor.get("ref") or ""))
        if part is None:
            continue
        setattr(part, "edge_preference", anchor.get("edge"))
        if anchor.get("offset_mm") is not None:
            setattr(part, "edge_offset_mm", anchor.get("offset_mm"))
        if anchor.get("rot_deg") is not None:
            setattr(part, "edge_rot_deg", anchor.get("rot_deg"))


def _exec_skidl(code: str):
    """Execute SKiDL Python code and return the populated default circuit."""
    circuit, _namespace = _exec_skidl_with_namespace(code)
    return circuit


def _exec_skidl_with_namespace(code: str):
    """Execute SKiDL Python code and return the circuit plus globals."""
    import builtins as _bi

    _configure_kicad_env()
    from skidl import KICAD9, set_default_tool

    _bi.default_circuit.reset()
    set_default_tool(KICAD9)

    namespace = {}
    exec("from skidl import *\nset_default_tool(KICAD9)", namespace)

    # Add easyeda2kicad converted libraries
    from skidl import lib_search_paths
    for d in _easyeda_sym_dirs():
        if d not in lib_search_paths.get("kicad9", []):
            lib_search_paths.setdefault("kicad9", []).append(d)

    cleaned = re.sub(
        r'(?:[\w.]*\.)?generate_(?:schematic|netlist|pcb)\s*\([^)]*\)',
        'pass',
        code,
    )
    cleaned = re.sub(
        r'set_default_tool\s*\([^)]*\)',
        'pass',
        cleaned,
    )
    cleaned = re.sub(
        r'^from\s+skidl\s+import\s+\*\s*$',
        '# from skidl import * stripped by worker',
        cleaned,
        flags=re.MULTILINE,
    )
    try:
        exec(cleaned, namespace)
    except SyntaxError:
        raise
    except Exception as exc:
        raise SkidlCodeExecutionError(exc, cleaned, namespace) from exc
    return _bi.default_circuit, namespace


def _circuit_to_spec_dict(circuit) -> dict:
    """Extract a minimal spec dict from a live SKiDL circuit for enrichment."""
    parts = []
    for p in circuit.parts:
        lib_name = ""
        if hasattr(p, "lib") and hasattr(p.lib, "filename"):
            lib_name = Path(p.lib.filename).stem
        parts.append({
            "ref": str(p.ref),
            "lib": lib_name,
            "part": str(p.name),
            "value": str(p.value) if p.value else "",
            "footprint": str(p.footprint) if p.footprint else "",
        })

    nets = []
    for n in circuit.nets:
        if n.name in ("NC",) or not n.name:
            continue
        pin_strs = []
        for pin in n.pins:
            part_ref = str(pin.part.ref) if pin.part else "?"
            pin_strs.append(f"{part_ref}.{pin.name}")
        is_power = is_power_net_name(str(n.name))
        nets.append({"name": n.name, "pins": pin_strs, "power": is_power})

    return {"board": {"name": "from_code"}, "parts": parts, "nets": nets}


def _inject_enrichment(circuit, original_spec: dict, enriched_spec: dict,
                       actions: list[dict]) -> list[dict]:
    """Add parts/nets from enrichment back into a live SKiDL circuit.

    Compares original vs enriched spec dicts. Any new parts get created as
    real SKiDL Part() objects and wired into the circuit's nets.
    Returns the actions list for logging.
    """
    if not actions:
        return actions

    from skidl import Part, Net, POWER

    orig_refs = {p["ref"] for p in original_spec.get("parts", [])}
    orig_nets = {n["name"] for n in original_spec.get("nets", [])}

    net_cache: dict[str, Net] = {}
    for n in circuit.nets:
        if n.name and n.name != "NC":
            net_cache[n.name] = n

    for part_dict in enriched_spec.get("parts", []):
        if part_dict["ref"] in orig_refs:
            continue

        lib = part_dict.get("lib", "Device")
        name = part_dict.get("part", "R")
        value = part_dict.get("value", "")
        fp = part_dict.get("footprint", "")

        try:
            kwargs = {}
            if value:
                kwargs["value"] = value
            if fp:
                kwargs["footprint"] = fp
            p = Part(lib, name, **kwargs)
            p.ref = part_dict["ref"]
        except Exception:
            continue

    for net_dict in enriched_spec.get("nets", []):
        net_name = net_dict["name"]
        if net_name not in net_cache:
            n = Net(net_name)
            if net_dict.get("power"):
                n.drive = POWER
            net_cache[net_name] = n

        net = net_cache[net_name]
        for pin_str in net_dict.get("pins", []):
            if "." not in pin_str:
                continue
            ref, pin_name = pin_str.rsplit(".", 1)
            if ref in orig_refs:
                continue
            for p in circuit.parts:
                if str(p.ref) == ref:
                    try:
                        pin = p[pin_name]
                        if pin not in net.pins:
                            pin += net
                    except Exception:
                        try:
                            pin_num = int(pin_name)
                            pin = p[pin_num]
                            if pin not in net.pins:
                                pin += net
                        except Exception:
                            pass
                    break

    return actions


def _available_part_pins(part) -> list[str]:
    pins: set[str] = set()

    def add(value) -> None:
        if value in (None, ""):
            return
        label = str(value)
        if label == "~" or re.fullmatch(r"p\d+", label):
            return
        pins.add(label)

    for pin in getattr(part, "pins", []) or []:
        for attr in ("name", "num"):
            add(getattr(pin, attr, None))
        aliases = getattr(pin, "aliases", None) or []
        for alias in aliases:
            add(alias)
    return sorted(pins)


def _infer_pin_lookup(error: SkidlCodeExecutionError) -> dict:
    """Infer a useful pin lookup diagnostic from the failed source line."""
    if not error.line_text:
        return {}
    lookups = re.findall(
        r"([A-Za-z_]\w*)\s*\[\s*(['\"])([^'\"]+)\2\s*\]",
        error.line_text,
    )
    for var_name, _quote, pin_name in lookups:
        obj = error.namespace.get(var_name)
        if obj is None or not hasattr(obj, "pins"):
            continue
        available = _available_part_pins(obj)
        ref = str(getattr(obj, "ref", var_name))
        part_name = str(getattr(obj, "name", "") or getattr(obj, "value", "") or "")
        subject = {
            "ref": ref,
            "part": part_name,
            "variable": var_name,
            "pin": pin_name,
            "available_pins": available[:80],
            "suggested_pins": _close(pin_name, available, 8),
        }
        family_hint = _connector_pin_family_hint(part_name, pin_name, available)
        if family_hint:
            subject["pin_family_hint"] = family_hint
        return subject
    return {}


def _infer_unit_pin_lookup(error: SkidlCodeExecutionError) -> dict:
    """Infer diagnostics for multi-unit access like opamp.uB[3]."""
    if not error.line_text:
        return {}
    lookups = re.findall(
        r"([A-Za-z_]\w*)\.([A-Za-z_]\w*)\s*\[\s*['\"]?([^'\"\]\s]+)['\"]?\s*\]",
        error.line_text,
    )
    for var_name, unit_name, pin_name in lookups:
        obj = error.namespace.get(var_name)
        unit = getattr(obj, unit_name, None) if obj is not None else None
        if unit is None or not hasattr(unit, "pins"):
            continue
        available = _available_part_pins(unit)
        ref = str(getattr(obj, "ref", var_name))
        part_name = str(getattr(obj, "name", "") or getattr(obj, "value", "") or "")
        subject = {
            "ref": ref,
            "part": part_name,
            "variable": var_name,
            "unit": unit_name,
            "pin": pin_name,
            "available_pins": available[:80],
            "suggested_pins": _close(pin_name, available, 8),
        }
        return subject
    return {}


def _close(token: str, pool: list[str], n: int = 6) -> list[str]:
    import difflib

    return difflib.get_close_matches(str(token), pool, n=n, cutoff=0.35)


def _connector_pin_family_hint(part_name: str, pin_name: str, available: list[str]) -> str:
    """Explain common mechanical-connector pin families when aliases mislead."""
    part_lower = part_name.lower()
    pin = str(pin_name).upper()
    pins_upper = {str(p).upper() for p in available}

    if part_lower in {"bme280", "bmp280", "bme680"}:
        if pin in {"SDA", "SCL"} and pin not in pins_upper:
            alias = "SDI" if pin == "SDA" else "SCK"
            return (
                f"{part_name} uses Bosch SPI-style pin names even in I2C mode: "
                f"use {alias} for I2C {pin}. For I2C, tie CSB high; SDO is "
                "the address-select pin, not SDA."
            )

    if "led_argb" in part_lower or "led_rgb" in part_lower:
        if pin in {"R", "G", "B"} and pin not in pins_upper:
            channel_pins = sorted(
                p for p in pins_upper
                if p.startswith(pin) and p in {f"{pin}A", f"{pin}K"}
            )
            if channel_pins:
                return (
                    f"This RGB LED symbol does not expose plain {pin!r}. "
                    f"Use {'/'.join(channel_pins)} for the {pin} channel "
                    "(A=anode, K=cathode), and wire the common anode/cathode "
                    "pin according to the selected LED symbol."
                )

    if "raspberrypi_pico" in part_lower and ("USB" in pin or pin in {"D+", "D-", "TP2", "TP3"}):
        return (
            "The Raspberry Pi Pico module symbol normally represents the "
            "complete module with onboard USB, and may not expose USB D+/D- "
            "test pads as SKiDL pins. For an external USB-C connector, use a "
            "raw RP2040 symbol/design; for a Pico-module board, omit external "
            "USB data wiring or use only the module's exposed power/GPIO pins."
        )

    if any(term in part_lower for term in ("relay", "ec2-", "g5v", "g6k", "tx2")):
        if pin not in pins_upper and available and all(re.fullmatch(r"\d+", str(p)) for p in available):
            return (
                "This relay symbol exposes numeric package pins only, not semantic "
                f"names like {pin!r}. Call search_kicad(part_name, detail=true) "
                "and wire the numeric coil/contact pins exactly; do not invent "
                "Coil+/Coil-/COM/NO/NC aliases unless the symbol lists them."
            )

    if "audiojack" not in part_lower and "audioplug" not in part_lower:
        return ""

    if pin in {"T", "R", "S"} and pin not in pins_upper:
        numbered = sorted(p for p in pins_upper if re.fullmatch(rf"{pin}\d+", p))
        normalled = sorted(
            p for p in pins_upper
            if re.fullmatch(rf"{pin}N\d*|{pin}\d*N", p)
        )
        if numbered or normalled:
            pieces = []
            if numbered:
                pieces.append(f"use {'/'.join(numbered[:4])} for actual {pin} contacts")
            if normalled:
                pieces.append(
                    f"{'/'.join(normalled[:4])} are switched/normalling contacts"
                )
            return (
                "This is a switched or dual audio jack symbol, so plain "
                f"{pin!r} is not a valid pin. " + "; ".join(pieces) + ". "
                "If the design does not need switching/normalling, choose a "
                "simpler AudioPlug/AudioJack symbol with T/R/S pins."
            )
    return ""


def _pin_suggestions_hint(subject: dict) -> str:
    suggestions = subject.get("suggested_pins") or []
    if suggestions:
        return " Close valid pin names: " + ", ".join(map(str, suggestions[:8])) + "."
    available = subject.get("available_pins") or []
    if available:
        return " Available pin names include: " + ", ".join(map(str, available[:12])) + "."
    return ""


def _multi_unit_pin_hint(unit_subject: dict) -> str:
    suggestions = unit_subject.get("suggested_pins") or []
    suffix = f" Close valid pins: {', '.join(suggestions[:5])}." if suggestions else ""
    available_hint = _pin_suggestions_hint(unit_subject)
    return (
        "This looks like a guessed multi-unit symbol pin access "
        f"({unit_subject['variable']}.{unit_subject['unit']}[...]). "
        "Multi-unit op-amp/comparator symbols expose only the pins listed by "
        "their selected unit. Do not reuse A-side package pin numbers on B/C/D "
        "units unless subject.available_pins lists them. Use "
        "search_kicad(part_name, detail=true) or subject.available_pins, then "
        "wire the exact pin on the exact unit before resubmitting; for TL07x "
        "style op-amps this often means `op.uB['+']`, `op.uB['-']`, and the "
        "unit's listed output pin."
        + available_hint
        + suffix
    )


def _symbol_part_suggestions(part_name: str, library: str = "", limit: int = 5) -> list[dict]:
    """Return exact Part() usages for a missing symbol part name."""
    try:
        from llm.kicad_index import search_symbols
    except Exception:
        return []

    queries = [part_name]
    if library:
        queries.append(f"{library} {part_name}")
    suggestions: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for query in queries:
        try:
            matches = search_symbols(query, limit=limit)
        except Exception:
            continue
        for sym in matches:
            key = (sym.lib, sym.name)
            if key in seen:
                continue
            seen.add(key)
            usage = (
                f'Part("{sym.lib}", "{sym.name}", footprint="{sym.footprint}")'
                if sym.footprint
                else f'Part("{sym.lib}", "{sym.name}", footprint="...")'
            )
            suggestions.append({
                "library": sym.lib,
                "part": sym.name,
                "description": sym.description,
                "usage": usage,
            })
            if len(suggestions) >= limit:
                return suggestions
    return suggestions


def _code_exception(
    message: str,
    hint: str = "",
    *,
    subject: dict | None = None,
) -> DesignException:
    return DesignException(
        id="e-code",
        code=ExcCode.CODE_EXEC_ERROR,
        severity=Severity.FATAL,
        message=message,
        subject=dict(subject or {}),
        candidates=[],
        retry_hint=hint or "Fix the error in your SKiDL code and resubmit.",
    )


def _missing_symbol_library_hint(lib: str, subject: dict) -> str:
    if lib.lower() == "testpoint":
        subject["suggested_usage"] = (
            'Part("Connector", "TestPoint", '
            'footprint="TestPoint:TestPoint_Pad_D1.5mm")'
        )
        return (
            "TestPoint is a KiCad footprint library, not the SKiDL symbol "
            "library. Use Connector:TestPoint for electrical probe pads, for "
            "example subject.suggested_usage, then wire its single pin to the "
            "net being probed."
        )

    if lib.lower() == "optodevice":
        subject["suggested_search"] = "search_kicad(\"6N138 optocoupler\", detail=true)"
        return (
            "KiCad optocoupler/opto-isolator symbols are usually in the "
            "Isolator symbol library, not OptoDevice. Call "
            "subject.suggested_search and copy the returned Part(...) usage "
            "before resubmitting."
        )

    return (
        "Use search_kicad(query, detail=true) for the intended part "
        "and copy a returned Part(...) usage. Do not use KiCad footprint "
        "library names or guessed symbol library names in Part(lib, name)."
    )


def _code_exception_from_syntax(error: SyntaxError) -> DesignException:
    subject = {}
    if error.lineno is not None:
        subject["line"] = error.lineno
    if error.text:
        subject["line_text"] = error.text.strip()
    if error.offset is not None:
        subject["column"] = error.offset
    hint = (
        "Fix the Python syntax and resubmit. SKiDL connections are usually "
        "`net += pin1, pin2` or `pin += net`; the left side of `+=` must be "
        "a named Net/Pin expression, not a function call or temporary value. "
        "There is no global `connect()` helper."
    )
    if "augmented assignment" not in str(error):
        hint = (
            "Fix the Python syntax and resubmit. For SKiDL wiring, use "
            "`net += pin1, pin2` or `pin += net`; there is no global "
            "`connect()` helper."
        )
    return _code_exception(f"SyntaxError: {error}", hint, subject=subject)


def _code_exception_from_exec(error: SkidlCodeExecutionError) -> DesignException:
    original = error.original
    original_text = f"{type(original).__name__}: {original}"
    subject = {
        "python_error": original_text,
        "traceback_tail": "".join(
            traceback.format_exception(type(original), original, original.__traceback__)
        )[-4000:],
    }
    if error.line is not None:
        subject["line"] = error.line
    if error.line_text:
        subject["line_text"] = error.line_text

    missing_lib = re.search(r"Can't open file:\s*([A-Za-z0-9_.+-]+)", str(original))
    if missing_lib:
        lib = missing_lib.group(1).rstrip(".")
        subject["missing_library"] = lib
        return _code_exception(
            f"symbol library {lib!r} is not available to SKiDL",
            _missing_symbol_library_hint(lib, subject),
            subject=subject,
        )

    missing_part = re.search(
        r"Unable to find part\s+(.+?)\s+in library\s+([A-Za-z0-9_.+-]+)",
        str(original),
    )
    if missing_part:
        part_name = missing_part.group(1).strip()
        lib = missing_part.group(2).rstrip(".")
        subject["missing_part"] = part_name
        subject["library"] = lib
        suggestions = _symbol_part_suggestions(part_name, lib)
        if suggestions:
            subject["suggested_parts"] = suggestions
            suggested = ", ".join(
                f"{s['library']}:{s['part']}" for s in suggestions[:3]
            )
            hint = (
                "Use one of subject.suggested_parts exactly, or call "
                "search_kicad(part_name, detail=true), then update the SKiDL "
                "code to use the exact returned library and part names before "
                f"resubmitting. Likely matches: {suggested}."
            )
        else:
            hint = (
                "Call search_kicad(part_name, detail=true) or search by function, "
                "then update the SKiDL code to use the exact returned library and "
                "part names before resubmitting."
            )
        if lib.startswith("MCU_ST_STM32") and part_name.upper().startswith("STM32"):
            subject["part_number_style"] = (
                "STM32 order codes often differ from KiCad package-family symbols"
            )
            hint += (
                " For STM32 order-code misses, search the exact part number with "
                "search_kicad(..., detail=true); KiCad may return a package-family "
                "symbol such as an ...Tx variant, while convert_lcsc() can preserve "
                "the exact stocked manufacturer order code."
            )
        return _code_exception(
            f"part {part_name!r} was not found in symbol library {lib!r}",
            hint,
            subject=subject,
        )

    pin_subject = _infer_pin_lookup(error)
    if "unsupported operand type(s) for +: 'Net' and 'Pin'" in str(original):
        if pin_subject:
            subject.update(pin_subject)
        return _code_exception(
            "invalid SKiDL net expression: '+' does not connect a Net and Pin",
            (
                "Create or reuse a named Net, then connect endpoints with "
                "`net += pin1, pin2`. Do not use `Net(...) + part['PIN']` "
                "inside a connection expression."
            ),
            subject=subject,
        )

    if "name 'connect' is not defined" in str(original):
        return _code_exception(
            original_text,
            (
                "SKiDL does not provide a global connect() helper. Connect "
                "endpoints by attaching them to a named net, e.g. "
                "`vcc += u1['VCC'], c1[1]` or `u1['GND'] += gnd`, then "
                "resubmit with submit_skidl_code()."
            ),
            subject=subject,
        )

    if "Can't use a non-zero index for a pin" in str(original):
        if pin_subject:
            subject.update(pin_subject)
        return _code_exception(
            original_text,
            (
                "A Part[...] lookup returned a single SKiDL Pin, then the code "
                "indexed that Pin again. Remove the extra [n] indexing and wire "
                "the pin directly, e.g. `net += jack['T1']`, not "
                "`jack['T1'][1]`. For audio jacks and switched connectors, "
                "use search_kicad(..., detail=true) and choose the exact "
                "T/R/S or T1/R1/S1 pin names the symbol exposes."
            ),
            subject=subject,
        )

    if pin_subject:
        subject.update(pin_subject)
        pin = pin_subject["pin"]
        ref = pin_subject["ref"]
        part = pin_subject.get("part") or "part"
        available = {str(p) for p in pin_subject.get("available_pins") or []}
        if str(pin) in available:
            return _code_exception(
                original_text,
                (
                    "The referenced pin exists, so this is a Python/SKiDL "
                    "expression error on subject.line rather than an unknown "
                    "pin. Inspect subject.line_text, edit the code, and "
                    "resubmit with submit_skidl_code()."
                ),
                subject=subject,
            )
        suggestions = pin_subject.get("suggested_pins") or []
        suffix = f"; close pins: {', '.join(suggestions[:5])}" if suggestions else ""
        family_hint = pin_subject.get("pin_family_hint")
        hint = (
            "Replace the pin name in the SKiDL code with one from "
            "subject.available_pins, or call search_kicad(part_name, "
            "detail=true) to inspect the symbol, then resubmit with "
            "submit_skidl_code()."
        )
        hint += _pin_suggestions_hint(subject)
        if family_hint:
            hint += f" {family_hint}"
        return _code_exception(
            f"pin {pin!r} not found on {ref} ({part}) while executing SKiDL code{suffix}",
            hint,
            subject=subject,
        )

    unit_subject = _infer_unit_pin_lookup(error)
    if unit_subject and (
        "NoneType" in str(original)
        or "No pins found" in str(original)
        or "not iterable" in str(original)
    ):
        subject.update(unit_subject)
        return _code_exception(
            f"pin {unit_subject['pin']!r} not found on "
            f"{unit_subject['ref']}.{unit_subject['unit']} "
            f"({unit_subject.get('part') or 'multi-unit symbol'})",
            _multi_unit_pin_hint(unit_subject),
            subject=subject,
        )

    return _code_exception(
        f"{type(original).__name__}: {original}",
        "Inspect subject.line and subject.line_text, edit the SKiDL code, then resubmit.",
        subject=subject,
    )


def _skidl_layout_intent_advisories(
    *,
    code: str,
    layout_result,
    floorplan_meta: dict | None,
    circuit,
) -> list[DesignException]:
    """Return code-shape advisories when layout fails for lack of intent."""

    validation = getattr(layout_result, "validation", None)
    score = getattr(layout_result, "score", None)
    overlap_count = len(getattr(validation, "overlaps", []) or [])
    hard_failure_count = (
        overlap_count
        + len(getattr(validation, "outline_violations", []) or [])
        + len(getattr(validation, "keepout_violations", []) or [])
        + len(getattr(validation, "missing_refs", []) or [])
    )
    if hard_failure_count == 0:
        return []
    congestion = float(getattr(score, "congestion_score", 0.0) or 0.0)
    part_count = len(getattr(circuit, "parts", []) or [])
    if part_count < 20 and overlap_count < 8 and congestion < 120.0:
        return []

    markers = {
        "subcircuits": "@subcircuit" in code,
        "floorplan": bool(floorplan_meta) or "EDA_FLOORPLAN" in code,
        "edge_preferences": "edge_preference" in code,
        "simulation_sources": "sim_source" in code,
    }
    missing = [name for name, present in markers.items() if not present]
    if not missing:
        return []

    return [
        DesignException(
            id="a-skidl-layout-intent",
            code=ExcCode.DESIGN_MISSING_FEATURE,
            severity=Severity.ADVISORY,
            message=(
                "Complex SKiDL submission lacks enough placement/power intent "
                "for the current layout failure"
            ),
            subject={
                "feature": "SKiDL layout intent",
                "missing": missing,
                "part_count": part_count,
                "overlap_count": overlap_count,
                "congestion_score": round(congestion, 1),
            },
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.ACCEPT_ADVISORY,
                    params={},
                    human_summary=(
                        "accept this layout-intent advisory for the current run"
                    ),
                    cost_hint="free",
                    confidence=0.7,
                )
            ],
            retry_hint=(
                "Before resubmitting a dense board, add @subcircuit blocks for "
                "functional groups such as power, MCU/module, sensors, and I/O; "
                "add EDA_FLOORPLAN or part.edge_preference for large modules, "
                "mounting holes, and board-edge connectors; keep decoupling "
                "parts in the same group as their IC; and declare external "
                "power assumptions with sim_source() when the board is powered "
                "from USB, JST, barrel, or another connector."
            ),
        )
    ]


def _floorplan_intent_preflight_exception(
    circuit,
    *,
    floorplan_meta: dict | None,
) -> DesignException | None:
    """Return a blocking preflight error for mechanically under-specified boards."""

    try:
        from skidl.layout.intent import classify_floorplan_intent_gap

        diagnosis = classify_floorplan_intent_gap(
            circuit,
            floorplan_meta=floorplan_meta,
        )
    except Exception:
        return None
    if not diagnosis.get("needs_floorplan"):
        return None

    module_refs = list(diagnosis.get("large_module_refs") or [])
    connector_refs = list(diagnosis.get("connector_refs") or [])
    mechanical_refs = list(diagnosis.get("mechanical_refs") or [])
    focus_refs = sorted(
        set(module_refs[:3] + connector_refs[:5] + mechanical_refs[:5])
    )
    return DesignException(
        id="e-floorplan-intent",
        code=ExcCode.DESIGN_MISSING_FEATURE,
        severity=Severity.ERROR,
        message=(
            "placement needs explicit floorplan/mechanical intent before "
            "running the layout engine"
        ),
        subject={
            "feature": "placement_floorplan_intent",
            "classification": "intent_insufficient_for_large_module_board",
            "reason": diagnosis.get("reason"),
            "confidence": diagnosis.get("confidence", 0.0),
            "large_module_refs": module_refs,
            "connector_refs": connector_refs,
            "mechanical_refs": mechanical_refs,
            "part_count": diagnosis.get("part_count", 0),
            "floorplan_intent": diagnosis.get("floorplan_intent", "none_or_weak"),
        },
        candidates=[
            Candidate(
                id="c1",
                action=ActionType.REGENERATE,
                params={
                    "required_intent": [
                        "EDA_FLOORPLAN.outline or outline_mm tied to the actual mechanical envelope",
                        "EDA_FLOORPLAN.edge_anchors for USB/JST/headers/card sockets and other user-facing connectors",
                        "EDA_FLOORPLAN.fixed_positions or zones for large modules and mounting-critical parts",
                        "part.edge_preference for connector refs when a full EDA_FLOORPLAN is not available",
                    ],
                    "focus_refs": focus_refs,
                },
                human_summary=(
                    "Add explicit floorplan constraints for the large module "
                    "and connector/mechanical refs, then regenerate"
                ),
                cost_hint="free",
                confidence=0.86,
            )
        ],
        retry_hint=(
            "Do not retry this board unchanged or treat a later placement crash "
            "as a generic engine failure. Add explicit mechanical intent first: "
            "set the real board outline; anchor edge connectors with "
            "EDA_FLOORPLAN['edge_anchors']; give the large module and any "
            "mounting-critical connectors fixed_positions or anchor zones; and "
            "use part.edge_preference for connector refs when only edge intent "
            "is known. Then resubmit the same circuit."
        ),
    )


def _run_skidl_code(envelope: dict) -> dict:
    """Execute SKiDL Python code and run the generation pipeline."""
    code = envelope.get("code", "")
    board_name = _safe_name(envelope.get("board_name", "board"))
    outline_mm = envelope.get("outline_mm")
    radius_context = (
        board_name,
        envelope.get("marketing_text", ""),
        envelope.get("design_intent", ""),
        code,
    )
    run_id = str(envelope.get("run_id") or uuid.uuid4().hex[:12])
    out_dir = Path(
        envelope.get("out_dir") or Path("artifacts") / "runs" / run_id
    ).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(out_dir)

    _configure_kicad_env()
    fp_dirs = [os.environ["KICAD9_FOOTPRINT_DIR"]]
    pipeline_goal = _normalize_pipeline_goal(envelope.get("pipeline_goal"))
    preview_mode = _placement_review_preview_mode(
        envelope.get("placement_preview_mode") or envelope.get("preview_mode")
    )

    fp_dirs.extend(_easyeda_fp_dirs())

    try:
        circuit, namespace = _exec_skidl_with_namespace(code)
    except SyntaxError as exc:
        return _json_result(
            run_id=run_id, ok=False, stage="exec",
            exceptions=[_code_exception_from_syntax(exc)],
            metrics=_metrics(),
        )
    except SkidlCodeExecutionError as exc:
        return _json_result(
            run_id=run_id, ok=False, stage="exec",
            exceptions=[_code_exception_from_exec(exc)],
            metrics=_metrics(),
        )
    except Exception as exc:
        return _json_result(
            run_id=run_id, ok=False, stage="exec",
            exceptions=[_code_exception(f"{type(exc).__name__}: {exc}")],
            metrics=_metrics(),
        )

    if not circuit.parts:
        return _json_result(
            run_id=run_id, ok=False, stage="exec",
            exceptions=[_code_exception(
                "Code produced no parts. Define parts with Part() and "
                "connect them with Net().",
            )],
            metrics=_metrics(),
        )

    try:
        inline_fp_root, inline_fp_meta = _write_inline_footprints(
            namespace.get("EDA_FOOTPRINTS"),
            out_dir,
            extra_raw=envelope.get("custom_footprints"),
        )
        if inline_fp_root:
            fp_dirs.insert(0, inline_fp_root)
    except ValueError as exc:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="footprint_bundle",
            exceptions=[_code_exception(str(exc))],
            metrics=_metrics(circuit=circuit, fp_dirs=fp_dirs),
        )

    # Server-side enrichment: add missing passives and functional blocks
    try:
        orig_spec = _circuit_to_spec_dict(circuit)
        marketing = envelope.get("marketing_text", "")
        working, block_actions = enrich_blocks(orig_spec, marketing)
        working, passive_actions = enrich_spec(working)
        all_enrich = block_actions + passive_actions
        if all_enrich:
            _inject_enrichment(circuit, orig_spec, working, all_enrich)
    except Exception:
        all_enrich = []

    review_spec = _circuit_to_spec_dict(circuit)
    review_exceptions = design_review_exceptions(
        review_spec,
        marketing_text=envelope.get("marketing_text", ""),
    )
    review_errors = [
        exc for exc in review_exceptions
        if exc.severity in (Severity.FATAL, Severity.ERROR)
    ]
    if review_errors:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="design_review",
            exceptions=review_exceptions,
            metrics=_metrics(circuit=circuit, fp_dirs=fp_dirs),
            summary=f"design review: {len(review_errors)} error(s)",
        )

    footprint_exception = _preflight_footprints(circuit, fp_dirs)
    if footprint_exception is not None:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="footprint_preflight",
            exceptions=[footprint_exception] + review_exceptions,
            metrics=_metrics(circuit=circuit, fp_dirs=fp_dirs),
            summary=footprint_exception.message,
        )

    schematic_path = out_dir / f"{board_name}.kicad_sch"
    pcb_path = out_dir / f"{board_name}.kicad_pcb"

    try:
        circuit.generate_schematic(
            filepath=str(out_dir),
            top_name=board_name,
            auto_stub=True,
            auto_stub_fanout=3,
            erc_max_iterations=8,
        )
    except Exception as exc:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="schematic_generation",
            exceptions=[
                crash_exception(
                    f"{type(exc).__name__}: {exc}",
                    stderr=traceback.format_exc(),
                    stage="schematic_generation",
                )
            ] + review_exceptions,
            outputs={"run_dir": str(out_dir)},
            metrics=_metrics(circuit=circuit, fp_dirs=fp_dirs),
            summary=(
                "schematic generation failed; preserve the SKiDL circuit and "
                "treat repeated TerminalClashException results as renderer feedback"
            ),
        )

    from skidl.layout import (
        LayoutConstraints, BoardOutline, plan_layout, write_kicad_pcb,
    )

    outline = (
        BoardOutline(
            *outline_mm,
            corner_radius_mm=_corner_radius_hint(
                envelope.get("corner_radius_mm"),
                outline_mm[0],
                outline_mm[1],
                *radius_context,
            ),
        )
        if outline_mm else None
    )
    floorplan_constraints, floorplan_meta = _floorplan_constraints(
        namespace.get("EDA_FLOORPLAN")
    )
    _apply_floorplan_part_attributes(circuit, floorplan_meta)
    floorplan_preflight = _floorplan_intent_preflight_exception(
        circuit,
        floorplan_meta=floorplan_meta,
    )
    if floorplan_preflight is not None:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="floorplan_preflight",
            exceptions=[floorplan_preflight] + review_exceptions,
            outputs={"run_dir": str(out_dir), "schematic": str(schematic_path)},
            metrics=_metrics(circuit=circuit, fp_dirs=fp_dirs),
            summary=floorplan_preflight.message,
        )
    if floorplan_constraints is None:
        constraints = LayoutConstraints(outline=outline)
    else:
        constraints = floorplan_constraints
        if outline is not None and floorplan_meta.get("outline") != "explicit":
            constraints.outline = outline
    auto_corner_radius_mm = (
        None
        if outline_mm
        else _auto_layout_corner_radius_hint(
            circuit,
            envelope.get("corner_radius_mm"),
            *radius_context,
        )
    )
    layout_result = plan_layout(
        circuit,
        fp_lib_dirs=fp_dirs,
        constraints=constraints,
        assembly_policy=envelope.get("assembly_policy"),
        corner_radius_mm=auto_corner_radius_mm,
    )
    if (
        outline_mm is None
        and auto_corner_radius_mm is None
        and layout_result.outline is not None
    ):
        layout_result.outline.corner_radius_mm = _corner_radius_hint(
            envelope.get("corner_radius_mm"),
            layout_result.outline.width_mm,
            layout_result.outline.height_mm,
            *radius_context,
        )

    try:
        write_kicad_pcb(
            layout_result.placed_parts, circuit, fp_dirs,
            str(pcb_path), outline=layout_result.outline,
            cutouts=getattr(layout_result, "cutouts", None),
        )
    except FileNotFoundError as exc:
        layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
        outputs = {"run_dir": str(out_dir), "schematic": str(schematic_path)}
        if pcb_path.exists():
            outputs["pcb"] = str(pcb_path)
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="layout_write",
            exceptions=[_footprint_missing_exception(exc, circuit)] + review_exceptions,
            outputs=outputs,
            layout=layout_dict,
            metrics=_metrics(layout_result, circuit, fp_dirs=fp_dirs),
            summary=layout_result.summary(),
        )

    layout_intent_advisories = _skidl_layout_intent_advisories(
        code=code,
        layout_result=layout_result,
        floorplan_meta=floorplan_meta,
        circuit=circuit,
    )
    all_exceptions = (
        layout_exceptions(layout_result)
        + layout_intent_advisories
        + review_exceptions
    )

    layout_errors = [
        e for e in all_exceptions
        if e.severity in (Severity.FATAL, Severity.ERROR)
    ]
    manufacturable = False
    mfg = {}
    if pipeline_goal == "placement_review":
        all_exceptions.append(_placement_review_only_exception(pipeline_goal))
    elif not layout_errors:
        route_timeout = max(30.0, float(envelope.get("route_timeout_s", 120)))
        route_exceptions = enrich_routing_failure_exceptions(
            _route_pcb(str(pcb_path), timeout_s=route_timeout),
            layout=layout_result,
        )
        all_exceptions.extend(route_exceptions)

        route_failed = any(
            e.code in (ExcCode.ROUTE_UNCONNECTED, ExcCode.ROUTE_TIMEOUT)
            for e in route_exceptions
        )
        route_skipped = any(
            e.code == ExcCode.ROUTE_UNAVAILABLE for e in route_exceptions
        )
        if not route_failed and not route_skipped:
            drc_exceptions = enrich_routing_failure_exceptions(
                _run_drc(str(pcb_path)),
                layout=layout_result,
            )
            all_exceptions.extend(drc_exceptions)
            drc_errors = [
                e for e in drc_exceptions
                if e.severity in (Severity.FATAL, Severity.ERROR)
            ]
            if not drc_errors:
                mfg = _generate_manufacturing_files(
                    str(pcb_path), circuit, layout_result, out_dir,
                )
                if _missing_manufacturing_outputs(mfg):
                    all_exceptions.append(_manufacturing_output_exception(mfg))
                else:
                    manufacturable = True

    previews = _generate_pipeline_previews(
        str(pcb_path),
        out_dir,
        layout_result,
        pipeline_goal=pipeline_goal,
        preview_mode=preview_mode,
    )

    outputs = {
        "run_dir": str(out_dir),
        "schematic": str(schematic_path),
        "pcb": str(pcb_path),
    }

    if mfg:
        outputs["manufacturing"] = mfg
    if previews.get("files"):
        outputs["previews"] = previews

    layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
    if floorplan_meta:
        layout_dict["floorplan"] = floorplan_meta
    if inline_fp_meta.get("count"):
        layout_dict["inline_footprints"] = inline_fp_meta
    metrics = _metrics(layout_result, circuit, fp_dirs=fp_dirs)
    metrics["manufacturable"] = manufacturable
    metrics["manufacturing_complete"] = manufacturable
    metrics["pipeline_goal"] = pipeline_goal
    metrics["preview_mode"] = (
        preview_mode if pipeline_goal == "placement_review" else "full"
    )
    all_exceptions = _drop_clean_manufacturing_advisories(
        all_exceptions,
        manufacturable=manufacturable,
    )

    return _json_result(
        run_id=run_id,
        ok=(
            layout_result.ok
            and (pipeline_goal == "placement_review" or manufacturable)
            and not any(
                e.severity in (Severity.FATAL, Severity.ERROR)
                for e in all_exceptions
            )
        ),
        stage="placement_review" if pipeline_goal == "placement_review" else "complete",
        exceptions=all_exceptions,
        outputs=outputs,
        layout=layout_dict,
        metrics=metrics,
        summary=layout_result.summary(),
    )


def _outline_for_spec(spec: CircuitSpec):
    from skidl.layout import BoardOutline

    if spec.board.outline_hint_mm:
        w, h = spec.board.outline_hint_mm
        return BoardOutline(
            w,
            h,
            corner_radius_mm=_corner_radius_hint(
                spec.board.corner_radius_mm,
                w,
                h,
                _spec_corner_context(spec),
            ),
        )
    return None


def run(envelope: dict) -> dict:
    if envelope.get("_mode") == "skidl_python":
        return _run_skidl_code(envelope)

    spec_dict = envelope.get("spec", envelope)
    run_id = str(envelope.get("run_id") or uuid.uuid4().hex[:12])
    out_dir = Path(envelope.get("out_dir") or Path("artifacts") / "runs" / run_id).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(out_dir)

    _configure_kicad_env()
    fp_dirs = [os.environ["KICAD9_FOOTPRINT_DIR"]]
    pipeline_goal = _normalize_pipeline_goal(envelope.get("pipeline_goal"))
    preview_mode = _placement_review_preview_mode(
        envelope.get("placement_preview_mode") or envelope.get("preview_mode")
    )

    fp_dirs.extend(_easyeda_fp_dirs())

    try:
        spec = CircuitSpec.model_validate(spec_dict)
    except ValidationError as exc:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="spec",
            exceptions=[spec_malformed_exception(str(exc))],
            metrics=_metrics(),
        )

    # Server-side enrichment: inject passives agents shouldn't need to know about
    working = spec.model_dump(mode="json")
    marketing = envelope.get("marketing_text", "")
    working, _block_actions = enrich_blocks(working, marketing)
    working, _passive_actions = enrich_spec(working)
    if _block_actions or _passive_actions:
        spec = CircuitSpec.model_validate(working)

    # Design review: structural checks (runs before translate so agents
    # get completeness feedback even when lib/pin matching fails)
    review_exceptions = design_review_exceptions(spec.model_dump(mode="json"))

    translated = translate(spec, fp_dirs=fp_dirs)
    if translated.exceptions:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="translate",
            spec=spec,
            exceptions=translated.exceptions + review_exceptions,
            metrics=_metrics(circuit=translated.circuit, fp_dirs=fp_dirs),
            summary="spec translation failed",
        )

    review_errors = [e for e in review_exceptions if e.severity in (Severity.FATAL, Severity.ERROR)]
    if review_errors:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="design_review",
            spec=spec,
            exceptions=review_exceptions,
            metrics=_metrics(circuit=translated.circuit, fp_dirs=fp_dirs),
            summary=f"design review: {len(review_errors)} error(s)",
        )

    circuit = translated.circuit
    board_name = _safe_name(spec.board.name)
    schematic_path = out_dir / f"{board_name}.kicad_sch"
    pcb_path = out_dir / f"{board_name}.kicad_pcb"

    try:
        circuit.generate_schematic(
            filepath=str(out_dir),
            top_name=board_name,
            auto_stub=True,
            auto_stub_fanout=3,
            erc_max_iterations=8,
        )
    except Exception as exc:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="schematic_generation",
            spec=spec,
            exceptions=[
                crash_exception(
                    f"{type(exc).__name__}: {exc}",
                    stderr=traceback.format_exc(),
                    stage="schematic_generation",
                )
            ] + review_exceptions,
            outputs={"run_dir": str(out_dir)},
            metrics=_metrics(circuit=circuit, fp_dirs=fp_dirs),
            summary=(
                "schematic generation failed; preserve the circuit and treat "
                "repeated TerminalClashException results as renderer feedback"
            ),
        )

    from skidl.layout import LayoutConstraints, plan_layout, write_kicad_pcb

    constraints = LayoutConstraints(
        outline=_outline_for_spec(spec),
        form_factor=spec.board.form_factor,
    )
    floorplan_preflight = _floorplan_intent_preflight_exception(
        circuit,
        floorplan_meta={},
    )
    if floorplan_preflight is not None:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="floorplan_preflight",
            spec=spec,
            exceptions=[floorplan_preflight] + review_exceptions,
            outputs={"run_dir": str(out_dir), "schematic": str(schematic_path)},
            metrics=_metrics(circuit=circuit, fp_dirs=fp_dirs),
            summary=floorplan_preflight.message,
        )
    auto_corner_radius_mm = (
        _auto_layout_corner_radius_hint(
            circuit,
            spec.board.corner_radius_mm,
            _spec_corner_context(spec),
        )
        if spec.board.outline_hint_mm is None and spec.board.form_factor is None
        else None
    )
    layout_result = plan_layout(
        circuit,
        fp_lib_dirs=fp_dirs,
        constraints=constraints,
        board_layers=spec.board.layers,
        assembly_policy=envelope.get("assembly_policy"),
        corner_radius_mm=auto_corner_radius_mm,
    )
    if (
        spec.board.outline_hint_mm is None
        and spec.board.form_factor is None
        and auto_corner_radius_mm is None
        and layout_result.outline is not None
    ):
        layout_result.outline.corner_radius_mm = _corner_radius_hint(
            spec.board.corner_radius_mm,
            layout_result.outline.width_mm,
            layout_result.outline.height_mm,
            _spec_corner_context(spec),
        )
    try:
        write_kicad_pcb(
            layout_result.placed_parts,
            circuit,
            fp_dirs,
            str(pcb_path),
            outline=layout_result.outline,
            cutouts=getattr(layout_result, "cutouts", None),
        )
    except FileNotFoundError as exc:
        layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
        outputs = {"run_dir": str(out_dir), "schematic": str(schematic_path)}
        if pcb_path.exists():
            outputs["pcb"] = str(pcb_path)
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="layout_write",
            spec=spec,
            exceptions=[_footprint_missing_exception(exc, circuit)] + review_exceptions,
            outputs=outputs,
            layout=layout_dict,
            metrics=_metrics(layout_result, circuit, fp_dirs=fp_dirs),
            summary=layout_result.summary(),
        )

    all_exceptions = layout_exceptions(layout_result) + review_exceptions

    # Routing stage: attempt Freerouting if available
    layout_errors = [e for e in all_exceptions if e.severity in (Severity.FATAL, Severity.ERROR)]
    manufacturable = False
    mfg = {}
    if pipeline_goal == "placement_review":
        all_exceptions.append(_placement_review_only_exception(pipeline_goal))
    elif not layout_errors:
        route_timeout = max(30.0, float(envelope.get("route_timeout_s", 120)))
        route_exceptions = enrich_routing_failure_exceptions(
            _route_pcb(str(pcb_path), timeout_s=route_timeout),
            layout=layout_result,
        )
        all_exceptions.extend(route_exceptions)

        # DRC stage: run after routing (or on unrouted board if routing unavailable)
        route_failed = any(
            e.code in (ExcCode.ROUTE_UNCONNECTED, ExcCode.ROUTE_TIMEOUT)
            for e in route_exceptions
        )
        route_skipped = any(e.code == ExcCode.ROUTE_UNAVAILABLE for e in route_exceptions)

        if not route_failed and not route_skipped:
            drc_exceptions = enrich_routing_failure_exceptions(
                _run_drc(str(pcb_path)),
                layout=layout_result,
            )
            all_exceptions.extend(drc_exceptions)
            drc_errors = [e for e in drc_exceptions
                          if e.severity in (Severity.FATAL, Severity.ERROR)]
            if not drc_errors:
                mfg = _generate_manufacturing_files(
                    str(pcb_path), circuit, layout_result, out_dir, spec=spec,
                )
                if _missing_manufacturing_outputs(mfg):
                    all_exceptions.append(_manufacturing_output_exception(mfg))
                else:
                    manufacturable = True

    previews = _generate_pipeline_previews(
        str(pcb_path),
        out_dir,
        layout_result,
        pipeline_goal=pipeline_goal,
        preview_mode=preview_mode,
    )

    outputs = {
        "run_dir": str(out_dir),
        "schematic": str(schematic_path),
        "pcb": str(pcb_path),
    }

    if mfg:
        outputs["manufacturing"] = mfg
    if previews.get("files"):
        outputs["previews"] = previews

    layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
    metrics = _metrics(layout_result, circuit, fp_dirs=fp_dirs)
    metrics["manufacturable"] = manufacturable
    metrics["manufacturing_complete"] = manufacturable
    metrics["pipeline_goal"] = pipeline_goal
    metrics["preview_mode"] = (
        preview_mode if pipeline_goal == "placement_review" else "full"
    )
    all_exceptions = _drop_clean_manufacturing_advisories(
        all_exceptions,
        manufacturable=manufacturable,
    )

    return _json_result(
        run_id=run_id,
        ok=(
            layout_result.ok
            and (pipeline_goal == "placement_review" or manufacturable)
            and not any(
                e.severity in (Severity.FATAL, Severity.ERROR)
                for e in all_exceptions
            )
        ),
        stage="placement_review" if pipeline_goal == "placement_review" else "complete",
        spec=spec,
        exceptions=all_exceptions,
        outputs=outputs,
        layout=layout_dict,
        metrics=metrics,
        summary=layout_result.summary(),
    )


def main() -> int:
    try:
        raw = sys.stdin.read()
        envelope = json.loads(raw) if raw.strip() else {}
        result = run(envelope)
    except Exception as exc:  # pragma: no cover - exercised through subprocess.
        result = _json_result(
            run_id="unknown",
            ok=False,
            stage="crash",
            exceptions=[
                crash_exception(
                    f"{type(exc).__name__}: {exc}",
                    stderr=traceback.format_exc(),
                    stage="engine_worker",
                )
            ],
            metrics=_metrics(),
        )
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
