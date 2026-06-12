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


def _exec_skidl(code: str):
    """Execute SKiDL Python code and return the populated default circuit."""
    import builtins as _bi

    from skidl import KICAD9, set_default_tool

    _bi.default_circuit.reset()
    set_default_tool(KICAD9)

    namespace = {}
    exec("from skidl import *\nset_default_tool(KICAD9)", namespace)

    cleaned = re.sub(
        r'(?:[\w.]*\.)?generate_(?:schematic|netlist|pcb)\s*\([^)]*\)',
        'pass',
        code,
    )
    exec(cleaned, namespace)
    return _bi.default_circuit


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

    schematic_path = out_dir / f"{board_name}.kicad_sch"
    pcb_path = out_dir / f"{board_name}.kicad_pcb"

    circuit.generate_schematic(
        filepath=str(out_dir),
        top_name=board_name,
        auto_stub=True,
        auto_stub_fanout=3,
        erc_max_iterations=8,
    )

    from skidl.layout import LayoutConstraints, BoardOutline, plan_layout, write_kicad_pcb

    outline = BoardOutline(*outline_mm) if outline_mm else None
    constraints = LayoutConstraints(outline=outline)
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
