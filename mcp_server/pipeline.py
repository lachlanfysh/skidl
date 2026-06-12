"""Transport-agnostic pipeline runner for the EDA MCP server."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_server.exception_mapper import (
    crash_exception,
    payload_exceptions,
    timeout_exception,
)
from mcp_server.runs import RunStore
from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import DesignException, Severity
from telemetry.features import extract_geometry
from telemetry.models import LLMStage
from telemetry.store import session


class DesignResponse(BaseModel):
    run_id: str
    ok: bool = False
    status: str = "failed"
    stage: str = ""
    exceptions: list[DesignException] = Field(default_factory=list)
    outputs: dict = Field(default_factory=dict)
    artifacts: dict = Field(default_factory=dict)
    layout: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    summary: str = ""
    stderr: str = ""
    policy: dict = Field(default_factory=dict)
    decision_required: bool = False
    decision_kind: str = ""
    recommended_next_tool: str = ""
    corrections_applied: list[dict] = Field(default_factory=list)


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _env() -> dict:
    env = os.environ.copy()
    root = str(_repo_root())
    src = str(_repo_root() / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [p for p in (root, src, existing) if p]
    )
    env.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")
    env.setdefault("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
    return env


def _status(ok: bool, exceptions: list[DesignException], timed_out: bool = False) -> str:
    if timed_out:
        return "timeout"
    if any(exc.code.value == "ENGINE_CRASH" for exc in exceptions):
        return "crashed"
    if any(exc.code.value == "ENGINE_TIMEOUT" for exc in exceptions):
        return "timeout"
    if any(exc.severity == Severity.FATAL for exc in exceptions):
        return "failed"
    if any(exc.severity == Severity.ERROR for exc in exceptions):
        return "failed"
    if exceptions:
        return "succeeded_with_warnings"
    return "succeeded" if ok else "failed"


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _validation_mode(value: object) -> str:
    if value in {"internal", "reference", "none"}:
        return str(value)
    return "none"


def _record_fields(
    record_fields: dict | None,
    *,
    validation_mode: str | None,
    model_tier: str | None,
    correction_iterations: int,
    corrections_applied: list[str] | None,
    llm_stages: list[dict | LLMStage] | None,
    parent_run_id: str | None,
    bom_match_score: float | None,
    netlist_match_score: float | None,
) -> dict:
    source = record_fields or {}
    fields: dict = {
        "tier": int(source.get("tier", 0) or 0),
        "source": str(source.get("source", "") or ""),
        "difficulty_axis": str(source.get("difficulty_axis", "") or ""),
        "nl_source": str(source.get("nl_source", "") or ""),
        "validation_mode": _validation_mode(validation_mode or source.get("validation_mode")),
        "correction_iterations": int(correction_iterations or 0),
        "corrections_applied": list(corrections_applied or []),
        "llm_stages": [
            stage if isinstance(stage, LLMStage) else LLMStage.model_validate(stage)
            for stage in (llm_stages or [])
        ],
    }
    if model_tier is not None:
        fields["model_tier"] = str(model_tier)
    if parent_run_id is not None:
        fields["parent_run_id"] = str(parent_run_id)
    if bom_match_score is not None:
        fields["bom_match_score"] = float(bom_match_score)
    if netlist_match_score is not None:
        fields["netlist_match_score"] = float(netlist_match_score)
    return fields


def _populate_record(record, circuit_spec: CircuitSpec, response: DesignResponse) -> None:
    record.geometry = extract_geometry(
        circuit_spec.model_dump(mode="json"),
        response.metrics,
    )
    record.cpu_time_s = float(response.metrics.get("cpu_time_s", 0.0) or 0.0)
    record.peak_rss_mb = float(response.metrics.get("peak_rss_mb", 0.0) or 0.0)
    record.layout_score = response.metrics.get("layout_score")
    record.total_hpwl_mm = response.metrics.get("total_hpwl_mm")
    record.congestion_score = response.metrics.get("congestion_score")
    record.candidates_scored = int(response.metrics.get("candidates_scored", 0) or 0)
    record.erc_iterations = int(response.metrics.get("erc_iterations", 0) or 0)
    record.schematic_retries = int(response.metrics.get("schematic_retries", 0) or 0)
    record.exceptions_raised = [exc.code.value for exc in response.exceptions]
    record.status = response.status
    if response.exceptions:
        record.failure_reason = "; ".join(exc.message for exc in response.exceptions[:3])


def run_pipeline(
    spec,
    out_dir,
    timeout_s: float = 300,
    *,
    mode: str = "engine_only",
    run_id: str | None = None,
    board_id: str | None = None,
    telemetry_path: str | Path | None = None,
    record_telemetry: bool = True,
    record_fields: dict | None = None,
    validation_mode: str | None = None,
    model_tier: str | None = None,
    correction_iterations: int = 0,
    corrections_applied: list[str] | None = None,
    llm_stages: list[dict | LLMStage] | None = None,
    parent_run_id: str | None = None,
    bom_match_score: float | None = None,
    netlist_match_score: float | None = None,
) -> DesignResponse:
    """Run translate -> schematic -> layout -> PCB in an isolated worker."""

    circuit_spec = spec if isinstance(spec, CircuitSpec) else CircuitSpec.model_validate(spec)
    run_id = run_id or uuid.uuid4().hex[:12]
    store = RunStore(out_dir)
    run_dir = store.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    envelope = {
        "run_id": run_id,
        "out_dir": str(run_dir),
        "spec": circuit_spec.model_dump(mode="json"),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.engine_worker"],
        cwd=str(_repo_root()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=_env(),
    )

    timed_out = False
    payload: dict = {}
    stderr = ""

    def _execute_worker() -> DesignResponse:
        nonlocal timed_out, payload, stderr
        try:
            stdout, stderr = proc.communicate(
                json.dumps(envelope),
                timeout=timeout_s,
            )
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill_process_group(proc)
            stdout, stderr = proc.communicate()
            exceptions = [timeout_exception(timeout_s)]
            response = DesignResponse(
                run_id=run_id,
                ok=False,
                status="timeout",
                stage="timeout",
                exceptions=exceptions,
                stderr=stderr[-4000:],
            )
        else:
            lines = [line for line in stdout.splitlines() if line.strip()]
            try:
                payload = json.loads(lines[-1]) if lines else {}
            except (json.JSONDecodeError, IndexError) as exc:
                payload = {}
                exceptions = [
                    crash_exception(
                        f"worker returned invalid JSON: {type(exc).__name__}: {exc}",
                        stderr=stderr,
                    )
                ]
            else:
                exceptions = payload_exceptions(payload, circuit_spec)
                if proc.returncode not in (0, None) and not exceptions:
                    exceptions = [
                        crash_exception(
                            f"worker exited with status {proc.returncode}",
                            stderr=stderr,
                        )
                    ]
            ok = bool(payload.get("ok", False)) and not any(
                exc.severity in (Severity.FATAL, Severity.ERROR)
                for exc in exceptions
            )
            response = DesignResponse(
                run_id=run_id,
                ok=ok,
                status=str(payload.get("status") or _status(ok, exceptions, timed_out=timed_out)),
                stage=str(payload.get("stage", "")),
                exceptions=exceptions,
                outputs=dict(payload.get("outputs", payload.get("artifacts", {}))),
                artifacts=dict(payload.get("artifacts", {})),
                layout=dict(payload.get("layout", {})),
                metrics=dict(payload.get("metrics", {})),
                summary=str(payload.get("summary", "")),
                stderr=stderr[-4000:],
            )
        return response

    if record_telemetry:
        fields = _record_fields(
            record_fields,
            validation_mode=validation_mode,
            model_tier=model_tier,
            correction_iterations=correction_iterations,
            corrections_applied=corrections_applied,
            llm_stages=llm_stages,
            parent_run_id=parent_run_id,
            bom_match_score=bom_match_score,
            netlist_match_score=netlist_match_score,
        )
        record_board_id = board_id or (record_fields or {}).get("board_id") or circuit_spec.board.name
        with session(record_board_id, mode, run_id=run_id, path=telemetry_path, **fields) as record:
            response = _execute_worker()
            _populate_record(record, circuit_spec, response)
    else:
        response = _execute_worker()

    store.save(run_id, circuit_spec, response.exceptions, response)
    return response


def run_pipeline_code(
    code: str,
    board_name: str,
    outline_mm: list[float] | None,
    out_dir,
    timeout_s: float = 300,
    *,
    run_id: str | None = None,
    board_id: str | None = None,
) -> DesignResponse:
    """Run SKiDL Python code through the engine pipeline in an isolated worker."""

    run_id = run_id or uuid.uuid4().hex[:12]
    store = RunStore(out_dir)
    run_dir = store.run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    envelope = {
        "_mode": "skidl_python",
        "run_id": run_id,
        "out_dir": str(run_dir),
        "code": code,
        "board_name": board_name,
        "outline_mm": outline_mm,
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "mcp_server.engine_worker"],
        cwd=str(_repo_root()),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
        env=_env(),
    )

    timed_out = False
    try:
        stdout, stderr = proc.communicate(
            json.dumps(envelope), timeout=timeout_s,
        )
    except subprocess.TimeoutExpired:
        timed_out = True
        _kill_process_group(proc)
        stdout, stderr = proc.communicate()
        response = DesignResponse(
            run_id=run_id, ok=False, status="timeout", stage="timeout",
            exceptions=[timeout_exception(timeout_s)],
            stderr=stderr[-4000:],
        )
        store.save(
            run_id,
            {"_mode": "skidl_python", "code": code, "board_name": board_name},
            response.exceptions, response,
        )
        return response

    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except (json.JSONDecodeError, IndexError) as exc:
        payload = {}
        exceptions = [
            crash_exception(
                f"worker returned invalid JSON: {type(exc).__name__}: {exc}",
                stderr=stderr,
            )
        ]
    else:
        exceptions = [
            exc if isinstance(exc, DesignException)
            else DesignException.model_validate(exc)
            for exc in payload.get("exceptions", [])
        ]
        if proc.returncode not in (0, None) and not exceptions:
            exceptions = [
                crash_exception(
                    f"worker exited with status {proc.returncode}",
                    stderr=stderr,
                )
            ]

    ok = bool(payload.get("ok", False)) and not any(
        exc.severity in (Severity.FATAL, Severity.ERROR) for exc in exceptions
    )
    response = DesignResponse(
        run_id=run_id,
        ok=ok,
        status=str(
            payload.get("status")
            or _status(ok, exceptions, timed_out=timed_out)
        ),
        stage=str(payload.get("stage", "")),
        exceptions=exceptions,
        outputs=dict(payload.get("outputs", payload.get("artifacts", {}))),
        artifacts=dict(payload.get("artifacts", {})),
        layout=dict(payload.get("layout", {})),
        metrics=dict(payload.get("metrics", {})),
        summary=str(payload.get("summary", "")),
        stderr=stderr[-4000:],
    )

    store.save(
        run_id,
        {"_mode": "skidl_python", "code": code, "board_name": board_name},
        response.exceptions, response,
    )
    return response
