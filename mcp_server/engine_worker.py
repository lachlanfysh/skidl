"""Subprocess worker for the EDA generation engine.

Input is a JSON object on stdin. It may be a raw CircuitSpec or an envelope:
{"run_id": "...", "out_dir": "...", "spec": {...}}.
Output is exactly one JSON object on stdout.
"""

from __future__ import annotations

import json
import os
import re
import sys
import traceback
import uuid
from pathlib import Path
from resource import RUSAGE_SELF, getrusage

from pydantic import ValidationError

from mcp_server.exception_mapper import (
    crash_exception,
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


def _configure_kicad_env() -> None:
    """Set KiCad paths broadly so SKiDL stderr keeps the useful signal."""
    symbol_dir = os.environ.get("KICAD9_SYMBOL_DIR", DEFAULT_SYM_DIR)
    footprint_dir = os.environ.get("KICAD9_FOOTPRINT_DIR", DEFAULT_FP_DIR)
    for version in ("9", "8", "7", "6"):
        os.environ.setdefault(f"KICAD{version}_SYMBOL_DIR", symbol_dir)
        os.environ.setdefault(f"KICAD{version}_FOOTPRINT_DIR", footprint_dir)
    os.environ.setdefault("KICAD_SYMBOL_DIR", symbol_dir)
    os.environ.setdefault("KICAD_FOOTPRINT_DIR", footprint_dir)


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
            "or complete. Fetch the run artifacts for inspection. If the run "
            "also reports congestion, long power nets, outline issues, or DRC "
            "errors, revise board size, layer count, edge placement, or part "
            "choices before retrying; otherwise report this as a routing/export "
            "tool failure."
        ),
    )


def _subprocess_signal_message(returncode: int) -> str:
    if returncode < 0:
        return f"signal {-returncode}"
    return f"exit {returncode}"


def _run_pcbnew_child(script: str, *, timeout_s: float = 30.0):
    """Run native pcbnew work in a child so segfaults stay structured."""

    import subprocess as sp

    return sp.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=timeout_s,
    )


def _export_dsn_with_pcbnew(pcb_path: str, dsn_path: str) -> DesignException | None:
    import subprocess as sp

    script = f"""
import pcbnew
pcb_path = {json.dumps(pcb_path)}
dsn_path = {json.dumps(dsn_path)}
board = pcbnew.LoadBoard(pcb_path)
if not pcbnew.ExportSpecctraDSN(board, dsn_path):
    raise SystemExit(2)
"""
    try:
        result = _run_pcbnew_child(script)
    except sp.TimeoutExpired:
        return _route_tool_exception(
            id="e-route-dsn-timeout",
            message="DSN export timed out",
            stage="dsn_export",
            subject={"timeout_s": 30},
        )
    except OSError as exc:
        return _route_tool_exception(
            id="e-route-dsn-fail",
            message=f"DSN export could not start: {exc}",
            stage="dsn_export",
            subject={"error": str(exc)},
        )

    if result.returncode != 0:
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
            },
        )
    if not Path(dsn_path).exists():
        return _route_tool_exception(
            id="e-route-dsn-fail",
            message="DSN export completed but produced no DSN file",
            stage="dsn_export",
        )
    return None


def _import_ses_with_pcbnew(pcb_path: str, ses_path: str) -> DesignException | None:
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
        result = _run_pcbnew_child(script)
    except sp.TimeoutExpired:
        return _route_tool_exception(
            id="e-route-import-timeout",
            message="SES import timed out",
            stage="ses_import",
            subject={"timeout_s": 30},
        )
    except OSError as exc:
        return _route_tool_exception(
            id="e-route-import-fail",
            message=f"SES import could not start: {exc}",
            stage="ses_import",
            subject={"error": str(exc)},
        )

    if result.returncode != 0:
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
    jar_path = "/opt/freerouting/freerouting-2.0.1.jar"
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

    dsn_exception = _export_dsn_with_pcbnew(pcb_path, dsn_path)
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
                Candidate(id="c2", action=ActionType.SCALE_OUTLINE,
                          params={"area_factor": 1.3},
                          human_summary="Enlarge board 30% and retry routing",
                          confidence=0.6),
                Candidate(id="c3", action=ActionType.SET_LAYERS,
                          params={"layers": 4},
                          human_summary="Switch to 4-layer board for easier routing",
                          confidence=0.5),
            ],
            retry_hint=(
                f"First resubmit the same design with run_options.route_timeout_s="
                f"{next_timeout:.0f}. If timeout repeats, simplify placement, "
                "increase outline, or use a board stackup the engine supports."
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

    import_exception = _import_ses_with_pcbnew(pcb_path, ses_path)
    if import_exception:
        return [import_exception]

    exceptions = []
    if unrouted > 0:
        exceptions.append(DesignException(
            id="e-route-unconnected",
            code=ExcCode.ROUTE_UNCONNECTED,
            severity=Severity.ERROR,
            message=f"{unrouted} net(s) could not be routed",
            subject={"unrouted_count": unrouted},
            candidates=[
                Candidate(id="c1", action=ActionType.SCALE_OUTLINE,
                          params={"area_factor": 1.3},
                          human_summary=f"Enlarge board 30% ({unrouted} unrouted nets)",
                          confidence=0.6),
                Candidate(id="c2", action=ActionType.SET_LAYERS,
                          params={"layers": 4},
                          human_summary="Switch to 4-layer board",
                          confidence=0.5),
                Candidate(id="c3", action=ActionType.REGENERATE, params={},
                          human_summary="Retry placement (has randomness)",
                          confidence=0.3),
            ],
        ))

    return exceptions


def _run_drc(pcb_path: str) -> list:
    """Run kicad-cli DRC and parse the JSON report. Returns list of DesignException."""
    import shutil
    import subprocess as sp
    from schemas.exceptions import (
        ActionType, Candidate, DesignException, ExcCode, Severity,
    )

    kicad_cli = shutil.which("kicad-cli")
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


def _drc_to_exceptions(report: dict) -> list:
    """Map kicad-cli DRC JSON to DesignException objects."""
    from collections import Counter
    from schemas.exceptions import (
        ActionType, Candidate, DesignException, ExcCode, Severity,
    )

    exceptions = []
    violations = report.get("violations", [])
    unconnected = report.get("unconnected_items", [])

    if unconnected:
        nets = Counter()
        for item in unconnected:
            for sub in item.get("items", []):
                m = re.search(r'\[([^\]]+)\]', sub.get("description", ""))
                if m:
                    nets[m.group(1)] += 1
        if nets:
            top_nets = ", ".join(f"{n}({c})" for n, c in nets.most_common(5))
            exceptions.append(DesignException(
                id="e-drc-unconnected",
                code=ExcCode.DRC_UNCONNECTED,
                severity=Severity.ERROR,
                message=f"{sum(nets.values())} unconnected item(s): {top_nets}",
                subject={"nets": dict(nets), "count": sum(nets.values())},
                candidates=[
                    Candidate(id="c1", action=ActionType.SCALE_OUTLINE,
                              params={"area_factor": 1.2},
                              human_summary="Enlarge board 20% for routing space",
                              confidence=0.5),
                    Candidate(id="c2", action=ActionType.REGENERATE, params={},
                              human_summary="Retry with new placement",
                              confidence=0.3),
                ],
            ))

    clearance_count = 0
    courtyard_count = 0
    short_count = 0

    for v in violations:
        vtype = v.get("type", "").lower()
        if "clearance" in vtype:
            clearance_count += 1
        elif "courtyard" in vtype:
            courtyard_count += 1
        elif "short" in vtype:
            short_count += 1

    if clearance_count:
        exceptions.append(DesignException(
            id="e-drc-clearance",
            code=ExcCode.DRC_CLEARANCE,
            severity=Severity.ERROR,
            message=f"{clearance_count} clearance violation(s)",
            subject={"count": clearance_count},
            candidates=[
                Candidate(id="c1", action=ActionType.SCALE_OUTLINE,
                          params={"area_factor": 1.2},
                          human_summary="Enlarge board to reduce trace density",
                          confidence=0.5),
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
        exceptions.append(DesignException(
            id="e-drc-short",
            code=ExcCode.DRC_SHORT,
            severity=Severity.ERROR,
            message=f"{short_count} short circuit(s) detected",
            subject={"count": short_count},
            candidates=[
                Candidate(id="c1", action=ActionType.REGENERATE, params={},
                          human_summary="Regenerate placement and routing",
                          confidence=0.4),
            ],
        ))

    return exceptions


def _export_gerbers(pcb_path: str, out_dir: Path) -> dict:
    """Export Gerber + drill files via kicad-cli."""
    import shutil
    import subprocess as sp

    kicad_cli = shutil.which("kicad-cli")
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
    lower = raw.lower()
    if "usb" in lower and "micro" in lower:
        queries.append("USB Micro-B connector")
    if "usb" in lower and ("usb_c" in lower or "type_c" in lower or "receptacle" in lower):
        queries.append("USB_C_Receptacle USB2.0 16P")
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
            "subject.suggested_footprints when present; otherwise call "
            "search_kicad() for the affected part or footprint, update the "
            "SKiDL code, and resubmit with submit_skidl_code()."
        ),
    )


def _exec_skidl(code: str):
    """Execute SKiDL Python code and return the populated default circuit."""
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
    return _bi.default_circuit


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
        return {
            "ref": ref,
            "part": part_name,
            "variable": var_name,
            "pin": pin_name,
            "available_pins": available[:80],
            "suggested_pins": _close(pin_name, available, 8),
        }
    return {}


def _close(token: str, pool: list[str], n: int = 6) -> list[str]:
    import difflib

    return difflib.get_close_matches(str(token), pool, n=n, cutoff=0.35)


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
            (
                "Use search_kicad(query, detail=true) for the intended part "
                "and copy a returned Part(...) usage. Do not use KiCad footprint "
                "library names or guessed symbol library names in Part(lib, name)."
            ),
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
        return _code_exception(
            f"part {part_name!r} was not found in symbol library {lib!r}",
            (
                "Call search_kicad(part_name, detail=true) or search by function, "
                "then update the SKiDL code to use the exact returned library and "
                "part names before resubmitting."
            ),
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
        return _code_exception(
            f"pin {pin!r} not found on {ref} ({part}) while executing SKiDL code{suffix}",
            (
                "Replace the pin name in the SKiDL code with one from "
                "subject.available_pins, or call search_kicad(part_name, "
                "detail=true) to inspect the symbol, then resubmit with "
                "submit_skidl_code()."
            ),
            subject=subject,
        )

    return _code_exception(
        f"{type(original).__name__}: {original}",
        "Inspect subject.line and subject.line_text, edit the SKiDL code, then resubmit.",
        subject=subject,
    )


def _run_skidl_code(envelope: dict) -> dict:
    """Execute SKiDL Python code and run the generation pipeline."""
    code = envelope.get("code", "")
    board_name = _safe_name(envelope.get("board_name", "board"))
    outline_mm = envelope.get("outline_mm")
    run_id = str(envelope.get("run_id") or uuid.uuid4().hex[:12])
    out_dir = Path(
        envelope.get("out_dir") or Path("artifacts") / "runs" / run_id
    ).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    os.chdir(out_dir)

    _configure_kicad_env()
    fp_dirs = [os.environ["KICAD9_FOOTPRINT_DIR"]]

    fp_dirs.extend(_easyeda_fp_dirs())

    try:
        circuit = _exec_skidl(code)
    except SyntaxError as exc:
        return _json_result(
            run_id=run_id, ok=False, stage="exec",
            exceptions=[_code_exception(f"SyntaxError: {exc}")],
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

    schematic_path = out_dir / f"{board_name}.kicad_sch"
    pcb_path = out_dir / f"{board_name}.kicad_pcb"

    circuit.generate_schematic(
        filepath=str(out_dir),
        top_name=board_name,
        auto_stub=True,
        auto_stub_fanout=3,
        erc_max_iterations=8,
    )

    from skidl.layout import (
        LayoutConstraints, BoardOutline, plan_layout, write_kicad_pcb,
    )

    outline = BoardOutline(*outline_mm) if outline_mm else None
    constraints = LayoutConstraints(outline=outline)
    layout_result = plan_layout(
        circuit, fp_lib_dirs=fp_dirs, constraints=constraints,
    )

    try:
        write_kicad_pcb(
            layout_result.placed_parts, circuit, fp_dirs,
            str(pcb_path), outline=layout_result.outline,
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

    all_exceptions = layout_exceptions(layout_result) + review_exceptions

    layout_errors = [
        e for e in all_exceptions
        if e.severity in (Severity.FATAL, Severity.ERROR)
    ]
    manufacturable = False
    mfg = {}
    if not layout_errors:
        route_timeout = max(30.0, float(envelope.get("route_timeout_s", 120)))
        route_exceptions = _route_pcb(str(pcb_path), timeout_s=route_timeout)
        all_exceptions.extend(route_exceptions)

        route_failed = any(
            e.code in (ExcCode.ROUTE_UNCONNECTED, ExcCode.ROUTE_TIMEOUT)
            for e in route_exceptions
        )
        route_skipped = any(
            e.code == ExcCode.ROUTE_UNAVAILABLE for e in route_exceptions
        )
        if not route_failed and not route_skipped:
            drc_exceptions = _run_drc(str(pcb_path))
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

    outputs = {
        "run_dir": str(out_dir),
        "schematic": str(schematic_path),
        "pcb": str(pcb_path),
    }

    if mfg:
        outputs["manufacturing"] = mfg

    layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
    metrics = _metrics(layout_result, circuit, fp_dirs=fp_dirs)
    metrics["manufacturable"] = manufacturable
    metrics["manufacturing_complete"] = manufacturable

    return _json_result(
        run_id=run_id,
        ok=layout_result.ok and manufacturable and not any(
            e.severity in (Severity.FATAL, Severity.ERROR)
            for e in all_exceptions
        ),
        stage="complete",
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
        return BoardOutline(w, h)
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

    circuit.generate_schematic(
        filepath=str(out_dir),
        top_name=board_name,
        auto_stub=True,
        auto_stub_fanout=3,
        erc_max_iterations=8,
    )

    from skidl.layout import LayoutConstraints, plan_layout, write_kicad_pcb

    constraints = LayoutConstraints(
        outline=_outline_for_spec(spec),
        form_factor=spec.board.form_factor,
    )
    layout_result = plan_layout(
        circuit,
        fp_lib_dirs=fp_dirs,
        constraints=constraints,
        board_layers=spec.board.layers,
    )
    try:
        write_kicad_pcb(
            layout_result.placed_parts,
            circuit,
            fp_dirs,
            str(pcb_path),
            outline=layout_result.outline,
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
    if not layout_errors:
        route_timeout = max(30.0, float(envelope.get("route_timeout_s", 120)))
        route_exceptions = _route_pcb(str(pcb_path), timeout_s=route_timeout)
        all_exceptions.extend(route_exceptions)

        # DRC stage: run after routing (or on unrouted board if routing unavailable)
        route_failed = any(
            e.code in (ExcCode.ROUTE_UNCONNECTED, ExcCode.ROUTE_TIMEOUT)
            for e in route_exceptions
        )
        route_skipped = any(e.code == ExcCode.ROUTE_UNAVAILABLE for e in route_exceptions)

        if not route_failed and not route_skipped:
            drc_exceptions = _run_drc(str(pcb_path))
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

    outputs = {
        "run_dir": str(out_dir),
        "schematic": str(schematic_path),
        "pcb": str(pcb_path),
    }

    if mfg:
        outputs["manufacturing"] = mfg

    layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
    metrics = _metrics(layout_result, circuit, fp_dirs=fp_dirs)
    metrics["manufacturable"] = manufacturable
    metrics["manufacturing_complete"] = manufacturable

    return _json_result(
        run_id=run_id,
        ok=layout_result.ok and manufacturable and not any(
            e.severity in (Severity.FATAL, Severity.ERROR) for e in all_exceptions
        ),
        stage="complete",
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
