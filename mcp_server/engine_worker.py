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
    spec_malformed_exception,
    suppress_waived,
)
from schemas.circuit_spec import CircuitSpec
from schemas.enrichment import (
    design_review_exceptions,
    enrich as enrich_spec,
    enrich_blocks,
)
from schemas.exceptions import DesignException, ExcCode, Severity
from schemas.translator import DEFAULT_FP_DIR, DEFAULT_SYM_DIR, translate


EASYEDA_CACHE_DIR = os.path.join(
    os.path.dirname(__file__), "..", "corpus", "jlc", "easyeda_cache"
)


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
    enrichment_actions=None,
) -> dict:
    exceptions = list(exceptions or [])
    if spec is not None:
        exceptions = suppress_waived(exceptions, spec)
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

    enrich_list = []
    if enrichment_actions:
        for a in enrichment_actions:
            d = a.to_dict() if hasattr(a, "to_dict") else dict(a)
            enrich_list.append(d)

    result = {
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

    if enrich_list:
        result["enrichment"] = {
            "count": len(enrich_list),
            "parts_added": sum(len(a.get("parts_added", [])) for a in enrich_list),
            "summary": _enrichment_summary(enrich_list),
            "actions": enrich_list,
        }

    return result


def _enrichment_summary(actions: list[dict]) -> str:
    """Human-readable summary of what the pipeline added to the design."""
    silent = [a for a in actions if a.get("category") == "silent"]
    loud = [a for a in actions if a.get("category") == "loud"]

    lines = []
    if silent:
        parts = []
        for a in silent:
            parts.extend(a.get("parts_added", []))
        if parts:
            lines.append(
                f"Auto-added {len(parts)} passive(s) your design was missing: "
                + ", ".join(parts)
                + ". These are standard best-practice components (decoupling caps, "
                "pull-up/pull-down resistors) that prevent noise, signal integrity, "
                "and reliability issues."
            )
    if loud:
        for a in loud:
            added = a.get("parts_added", [])
            desc = a.get("description", a.get("rule", ""))
            if added:
                lines.append(
                    f"Added functional block: {desc} "
                    f"({', '.join(added)}). "
                    "Flagged for review — verify this matches your intent."
                )
            else:
                lines.append(f"Applied: {desc}")

    if not lines:
        return "No enrichment needed — your design included all required support components."

    return " ".join(lines)


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
            severity=Severity.ADVISORY,
            message="Routing unavailable: Freerouting JAR or Java not found — board is unrouted but placement is valid",
            subject={},
            candidates=[],
        )]

    dsn_path = str(Path(pcb_path).with_suffix(".dsn"))
    ses_path = str(Path(pcb_path).with_suffix(".ses"))

    try:
        import pcbnew
        board = pcbnew.LoadBoard(pcb_path)
        if not pcbnew.ExportSpecctraDSN(board, dsn_path):
            return [DesignException(
                id="e-route-dsn-fail",
                code=ExcCode.ROUTE_UNAVAILABLE,
                severity=Severity.ERROR,
                message="DSN export failed — board outline may be malformed",
                subject={},
                candidates=[],
            )]
    except Exception as exc:
        return [DesignException(
            id="e-route-dsn-fail",
            code=ExcCode.ROUTE_UNAVAILABLE,
            severity=Severity.ERROR,
            message=f"DSN export error: {exc}",
            subject={},
            candidates=[],
        )]

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
        return [DesignException(
            id="e-route-timeout",
            code=ExcCode.ROUTE_TIMEOUT,
            severity=Severity.ERROR,
            message=f"Freerouting exceeded {timeout_s:.0f}s timeout",
            subject={"timeout_s": timeout_s},
            candidates=[
                Candidate(id="c1", action=ActionType.SCALE_OUTLINE,
                          params={"area_factor": 1.3},
                          human_summary="Enlarge board 30% and retry routing",
                          confidence=0.6),
                Candidate(id="c2", action=ActionType.SET_LAYERS,
                          params={"layers": 4},
                          human_summary="Switch to 4-layer board for easier routing",
                          confidence=0.5),
            ],
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

    try:
        board = pcbnew.LoadBoard(pcb_path)
        pcbnew.ImportSpecctraSES(board, ses_path)
        pcbnew.SaveBoard(pcb_path, board)
    except Exception as exc:
        return [DesignException(
            id="e-route-import-fail",
            code=ExcCode.ROUTE_UNAVAILABLE,
            severity=Severity.ERROR,
            message=f"SES import error: {exc}",
            subject={},
            candidates=[],
        )]

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
            severity=Severity.ADVISORY,
            message="kicad-cli not found — DRC skipped",
            subject={},
            candidates=[],
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
            severity=Severity.ADVISORY,
            message="kicad-cli DRC timed out",
            subject={},
            candidates=[],
        )]

    if not Path(drc_json).exists():
        return [DesignException(
            id="e-drc-no-report",
            code=ExcCode.DRC_TOOL_FAILURE,
            severity=Severity.ADVISORY,
            message=f"DRC produced no report (exit {result.returncode})",
            subject={"stderr_tail": result.stderr[-1000:]},
            candidates=[],
        )]

    try:
        with open(drc_json) as f:
            report = json.load(f)
    except (json.JSONDecodeError, OSError) as exc:
        return [DesignException(
            id="e-drc-parse-fail",
            code=ExcCode.DRC_TOOL_FAILURE,
            severity=Severity.ADVISORY,
            message=f"Failed to parse DRC report: {exc}",
            subject={},
            candidates=[],
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


def _export_gerbers(pcb_path: str, out_dir: Path) -> bool:
    """Export Gerber + drill files via kicad-cli. Returns True on success."""
    import shutil
    import subprocess as sp

    kicad_cli = shutil.which("kicad-cli")
    if not kicad_cli:
        return False

    gerber_dir = out_dir / "gerbers"
    gerber_dir.mkdir(exist_ok=True)

    try:
        sp.run(
            [kicad_cli, "pcb", "export", "gerbers",
             "-o", str(gerber_dir) + "/", pcb_path],
            capture_output=True, timeout=30, text=True,
        )
        sp.run(
            [kicad_cli, "pcb", "export", "drill",
             "-o", str(gerber_dir) + "/", pcb_path],
            capture_output=True, timeout=30, text=True,
        )
    except (sp.TimeoutExpired, OSError):
        return False

    return any(gerber_dir.iterdir())


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

    if _export_gerbers(pcb_path, out_dir):
        mfg["gerbers"] = True

    bom_path = _generate_bom(circuit, spec=spec, out_dir=out_dir)
    if bom_path:
        mfg["bom"] = str(bom_path)

    cpl_path = _generate_cpl(layout_result.placed_parts, out_dir)
    if cpl_path:
        mfg["cpl"] = str(cpl_path)

    return mfg


def _exec_skidl(code: str):
    """Execute SKiDL Python code and return the populated default circuit."""
    import builtins as _bi

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
        '',
        cleaned,
        flags=re.MULTILINE,
    )
    exec(cleaned, namespace)
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
        is_power = n.drive is not None and n.drive >= 1
        nets.append({"name": n.name, "pins": pin_strs, "power": is_power})

    return {"board": {"name": "from_code"}, "parts": parts, "nets": nets}


def _normalize_pin_name(name: str) -> str:
    """Strip KiCad pin name decorations for fuzzy matching."""
    return re.sub(r"[{}_~]", "", name).strip()


def _find_pin(part, pin_name):
    """Find a pin by name with fuzzy matching for KiCad decorated names."""
    # Exact match first
    try:
        return part[pin_name]
    except Exception:
        pass
    # Integer pin number
    try:
        return part[int(pin_name)]
    except Exception:
        pass
    # Fuzzy: strip decorations from both sides and compare
    normalized = _normalize_pin_name(pin_name).upper()
    for pin in part.pins:
        if _normalize_pin_name(pin.name).upper() == normalized:
            return pin
    return None


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
                    pin = _find_pin(p, pin_name)
                    if pin is not None and pin not in net.pins:
                        pin += net
                    break

    return actions


def _code_exception(message: str, hint: str = "") -> DesignException:
    return DesignException(
        id="e-code",
        code=ExcCode.CODE_EXEC_ERROR,
        severity=Severity.FATAL,
        message=message,
        subject={},
        candidates=[],
        retry_hint=hint or "Fix the error in your SKiDL code and resubmit.",
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

    os.environ.setdefault("KICAD9_SYMBOL_DIR", DEFAULT_SYM_DIR)
    os.environ.setdefault("KICAD9_FOOTPRINT_DIR", DEFAULT_FP_DIR)
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

    schematic_path = out_dir / f"{board_name}.kicad_sch"
    pcb_path = out_dir / f"{board_name}.kicad_pcb"

    # Validate footprints before schematic/layout to fail fast with a clear
    # error instead of a SIGSEGV in the placer.
    from skidl.layout.writer import validate_footprints
    fp_names = {str(p.footprint) for p in circuit.parts
                if getattr(p, "footprint", None)}
    _valid_fps, missing_fps = validate_footprints(fp_names, fp_dirs)
    if missing_fps:
        missing_list = ", ".join(sorted(missing_fps)[:5])
        hint = (
            "These footprint libraries are not available on the server. "
            "Use search_kicad() to find footprints from installed libraries. "
            "Common substitutions: Package_DFN_QFN → Package_SO (e.g. SOIC-16), "
            "use detail=True to see available footprints for each part."
        )
        return _json_result(
            run_id=run_id, ok=False, stage="footprint_check",
            exceptions=[_code_exception(
                f"Missing footprint(s): {missing_list}",
                hint=hint,
            )],
            metrics=_metrics(),
            enrichment_actions=all_enrich,
        )

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

    for scale in (1.5, 2.0):
        layout_errs = layout_exceptions(layout_result)
        has_placement_error = any(
            e.code in (ExcCode.LAYOUT_OVERLAP, ExcCode.LAYOUT_OUTLINE_VIOLATION)
            for e in layout_errs
        )
        if not has_placement_error:
            break
        expanded = BoardOutline(
            layout_result.outline.width_mm * scale,
            layout_result.outline.height_mm * scale,
        )
        constraints = LayoutConstraints(outline=expanded)
        layout_result = plan_layout(
            circuit, fp_lib_dirs=fp_dirs, constraints=constraints,
        )

    write_kicad_pcb(
        layout_result.placed_parts, circuit, fp_dirs,
        str(pcb_path), outline=layout_result.outline,
    )

    all_exceptions = layout_exceptions(layout_result)

    layout_errors = [
        e for e in all_exceptions
        if e.severity in (Severity.FATAL, Severity.ERROR)
    ]
    manufacturable = False
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
            manufacturable = not drc_errors

    outputs = {
        "run_dir": str(out_dir),
        "schematic": str(schematic_path),
        "pcb": str(pcb_path),
    }

    if manufacturable:
        mfg = _generate_manufacturing_files(
            str(pcb_path), circuit, layout_result, out_dir,
        )
        outputs["manufacturing"] = mfg

    layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
    metrics = _metrics(layout_result, circuit, fp_dirs=fp_dirs)
    metrics["manufacturable"] = manufacturable

    return _json_result(
        run_id=run_id,
        ok=layout_result.ok and not any(
            e.severity in (Severity.FATAL, Severity.ERROR)
            for e in all_exceptions
        ),
        stage="complete",
        exceptions=all_exceptions,
        outputs=outputs,
        layout=layout_dict,
        metrics=metrics,
        summary=layout_result.summary(),
        enrichment_actions=all_enrich,
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

    os.environ.setdefault("KICAD9_SYMBOL_DIR", DEFAULT_SYM_DIR)
    os.environ.setdefault("KICAD9_FOOTPRINT_DIR", DEFAULT_FP_DIR)
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
    all_enrich_json = _block_actions + _passive_actions
    if all_enrich_json:
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
    write_kicad_pcb(
        layout_result.placed_parts,
        circuit,
        fp_dirs,
        str(pcb_path),
        outline=layout_result.outline,
    )

    all_exceptions = layout_exceptions(layout_result) + review_exceptions

    # Routing stage: attempt Freerouting if available
    layout_errors = [e for e in all_exceptions if e.severity in (Severity.FATAL, Severity.ERROR)]
    manufacturable = False
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
            manufacturable = not drc_errors

    outputs = {
        "run_dir": str(out_dir),
        "schematic": str(schematic_path),
        "pcb": str(pcb_path),
    }

    if manufacturable:
        mfg = _generate_manufacturing_files(
            str(pcb_path), circuit, layout_result, out_dir, spec=spec,
        )
        outputs["manufacturing"] = mfg

    layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
    metrics = _metrics(layout_result, circuit, fp_dirs=fp_dirs)
    metrics["manufacturable"] = manufacturable

    return _json_result(
        run_id=run_id,
        ok=layout_result.ok and not any(
            e.severity in (Severity.FATAL, Severity.ERROR) for e in all_exceptions
        ),
        stage="complete",
        spec=spec,
        exceptions=all_exceptions,
        outputs=outputs,
        layout=layout_dict,
        metrics=metrics,
        summary=layout_result.summary(),
        enrichment_actions=all_enrich_json,
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
                )
            ],
            metrics=_metrics(),
        )
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
