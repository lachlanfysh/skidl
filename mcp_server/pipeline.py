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


def run_pipeline(
    spec,
    out_dir,
    timeout_s: float = 300,
) -> DesignResponse:
    """Run translate -> schematic -> layout -> PCB in an isolated worker."""

    circuit_spec = spec if isinstance(spec, CircuitSpec) else CircuitSpec.model_validate(spec)
    run_id = uuid.uuid4().hex[:12]
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
    with session(circuit_spec.board.name, "engine_only", run_id=run_id) as record:
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

        record.geometry = extract_geometry(
            circuit_spec.model_dump(mode="json"),
            response.metrics,
        )
        record.cpu_time_s = float(response.metrics.get("cpu_time_s", 0.0) or 0.0)
        record.peak_rss_mb = float(response.metrics.get("peak_rss_mb", 0.0) or 0.0)
        record.layout_score = response.metrics.get("layout_score")
        record.total_hpwl_mm = response.metrics.get("total_hpwl_mm")
        record.congestion_score = response.metrics.get("congestion_score")
        record.exceptions_raised = [exc.code.value for exc in response.exceptions]
        record.status = response.status
        if response.exceptions:
            record.failure_reason = "; ".join(exc.message for exc in response.exceptions[:3])

    store.save(run_id, circuit_spec, response.exceptions, response)
    return response
