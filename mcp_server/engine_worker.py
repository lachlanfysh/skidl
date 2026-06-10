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
from schemas.exceptions import Severity
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


def _outline_for_spec(spec: CircuitSpec):
    from skidl.layout import BoardOutline

    if spec.board.outline_hint_mm:
        w, h = spec.board.outline_hint_mm
        return BoardOutline(w, h)
    return None


def run(envelope: dict) -> dict:
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

    translated = translate(spec, fp_dirs=fp_dirs)
    if translated.exceptions:
        return _json_result(
            run_id=run_id,
            ok=False,
            stage="translate",
            spec=spec,
            exceptions=translated.exceptions,
            metrics=_metrics(circuit=translated.circuit, fp_dirs=fp_dirs),
            summary="spec translation failed",
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

    exceptions = layout_exceptions(layout_result)
    outputs = {
        "run_dir": str(out_dir),
        "schematic": str(schematic_path),
        "pcb": str(pcb_path),
    }
    layout_dict = layout_result.to_dict() if hasattr(layout_result, "to_dict") else {}
    return _json_result(
        run_id=run_id,
        ok=layout_result.ok,
        stage="complete",
        spec=spec,
        exceptions=exceptions,
        outputs=outputs,
        layout=layout_dict,
        metrics=_metrics(layout_result, circuit, fp_dirs=fp_dirs),
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
