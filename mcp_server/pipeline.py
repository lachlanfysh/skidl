"""Transport-agnostic pipeline runner for the EDA MCP server."""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from pydantic import BaseModel, Field

from mcp_server.exception_mapper import (
    crash_exception,
    order_exceptions_for_agent,
    payload_exceptions,
    product_layout_exception,
    timeout_exception,
)
from mcp_server.layout_quality import build_layout_quality, write_layout_quality
from mcp_server.runs import RunStore
from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import DesignException, ExcCode, Severity
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
    layout_quality: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    summary: str = ""
    stderr: str = ""
    policy: dict = Field(default_factory=dict)
    decision_required: bool = False
    decision_kind: str = ""
    recommended_next_tool: str = ""
    corrections_applied: list[dict] = Field(default_factory=list)
    visual_review_ready: bool = False
    reviewable_failure: bool = False


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _first_existing_dir(*paths: str) -> str:
    for path in paths:
        if path and os.path.isdir(path):
            return path
    return next((path for path in paths if path), "")


def _kicad_library_dir(env: dict, name: str, linux_default: str, mac_subdir: str) -> str:
    configured = env.get(name, "")
    return _first_existing_dir(
        configured,
        linux_default,
        f"/Applications/KiCad/KiCad.app/Contents/SharedSupport/{mac_subdir}",
        configured or linux_default,
    )


def _env() -> dict:
    env = os.environ.copy()
    root = str(_repo_root())
    src = str(_repo_root() / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join(
        [p for p in (root, src, existing) if p]
    )
    env["KICAD9_SYMBOL_DIR"] = _kicad_library_dir(
        env, "KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols", "symbols"
    )
    env["KICAD9_FOOTPRINT_DIR"] = _kicad_library_dir(
        env, "KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints", "footprints"
    )
    for version in ("8", "7", "6"):
        env.setdefault(f"KICAD{version}_SYMBOL_DIR", env["KICAD9_SYMBOL_DIR"])
        env.setdefault(f"KICAD{version}_FOOTPRINT_DIR", env["KICAD9_FOOTPRINT_DIR"])
    env.setdefault("KICAD_SYMBOL_DIR", env["KICAD9_SYMBOL_DIR"])
    env.setdefault("KICAD_FOOTPRINT_DIR", env["KICAD9_FOOTPRINT_DIR"])
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
    if any(exc.code == ExcCode.PRODUCT_LAYOUT_FAILED for exc in exceptions):
        return "failed_reviewable"
    if any(exc.severity == Severity.ERROR for exc in exceptions):
        return "failed"
    if exceptions:
        return "succeeded_with_warnings"
    return "succeeded" if ok else "failed"


def _manufacturing_metrics(
    metrics: dict | None,
    exceptions: list[DesignException],
    ok: bool,
) -> dict:
    """Ensure manufacturing status is explicit on every response."""
    normalized = dict(metrics or {})
    has_error = any(
        exc.severity in (Severity.FATAL, Severity.ERROR)
        for exc in exceptions
    )
    normalized.setdefault("manufacturable", bool(ok and not has_error))
    normalized.setdefault(
        "manufacturing_complete",
        bool(normalized["manufacturable"] and not has_error),
    )
    return normalized


def _attach_layout_quality(response: DesignResponse, run_dir: Path) -> DesignResponse:
    """Attach and persist product-layout quality gates for a response."""
    quality = build_layout_quality(
        run_id=response.run_id,
        status=response.status,
        stage=response.stage,
        ok=response.ok,
        exceptions=response.exceptions,
        layout=response.layout,
        metrics=response.metrics,
        artifacts=response.artifacts or response.outputs,
    )
    gates = quality.get("gates", {}) if isinstance(quality.get("gates"), dict) else {}
    review = quality.get("review", {}) if isinstance(quality.get("review"), dict) else {}
    failed_reviewable = bool(
        gates.get("visual_review_ready")
        and not gates.get("product_layout_ok")
    )
    if failed_reviewable and not any(
        exc.code == ExcCode.PRODUCT_LAYOUT_FAILED for exc in response.exceptions
    ):
        response.exceptions = order_exceptions_for_agent(
            [*response.exceptions, product_layout_exception(quality)]
        )
    if failed_reviewable:
        response.ok = False
        response.status = str(review.get("state") or "failed_reviewable")
        quality["ok"] = False
        quality["status"] = response.status
    quality_path = write_layout_quality(run_dir, quality)
    response.layout_quality = quality
    response.outputs = dict(response.outputs or {})
    response.artifacts = dict(response.artifacts or {})
    response.outputs["layout_quality"] = str(quality_path)
    response.artifacts["layout_quality"] = str(quality_path)
    response.metrics = dict(response.metrics or {})
    response.metrics["quality_gates"] = dict(quality.get("gates", {}))
    response.metrics["product_layout_ok"] = bool(
        quality.get("gates", {}).get("product_layout_ok")
    )
    response.metrics["visual_review_ready"] = bool(
        quality.get("gates", {}).get("visual_review_ready")
    )
    response.metrics["review_state"] = str(
        quality.get("review", {}).get("state") or ""
    )
    response.visual_review_ready = bool(response.metrics["visual_review_ready"])
    response.reviewable_failure = bool(failed_reviewable)
    return response


def _kill_process_group(proc: subprocess.Popen) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _partial_artifact_names(run_dir: Path) -> list[str]:
    """Return artifact names that already exist after a worker crash."""
    names: list[str] = []
    for pattern in ("*.kicad_sch", "*.kicad_pcb", "bom.csv", "cpl.csv"):
        names.extend(path.name for path in run_dir.rglob(pattern))
    gerbers = run_dir / "gerbers"
    if gerbers.is_dir():
        names.extend(f"gerbers/{path.name}" for path in gerbers.iterdir() if path.is_file())
    return sorted(set(names))


def _infer_crash_stage(payload: dict, run_dir: Path) -> str:
    """Best-effort stage for crashes that happen before a JSON result is emitted."""
    stage = str(payload.get("stage") or "")
    if stage:
        return stage
    artifacts = set(_partial_artifact_names(run_dir))
    if any(name.endswith(".kicad_pcb") for name in artifacts):
        return "after_pcb_write"
    if any(name.endswith(".kicad_sch") for name in artifacts):
        return "after_schematic"
    return "worker_crash"


_STRING_LINE_RE = re.compile(r"<string>:(?P<line>\d+)")
_PIN_ERROR_RE = re.compile(
    r"No pins found using\s+(?P<part>[^:\s]+):(?P<ref>[A-Za-z_]\w*)"
    r"\[\((?:'(?P<pin_quoted>[^']+)'|\"(?P<pin_dquoted>[^\"]+)\"|"
    r"(?P<pin_raw>[^,\)\]]+))(?:,)?\)\]"
)
_UNIT_PIN_ERROR_RE = re.compile(
    r"No pins found using\s+(?P<part>[^:\s]+):"
    r"(?P<ref>[A-Za-z_]\w*)\.(?P<unit>[A-Za-z_]\w*)"
    r"\[\((?:'(?P<pin_quoted>[^']+)'|\"(?P<pin_dquoted>[^\"]+)\"|"
    r"(?P<pin_raw>[^,\)\]]+))(?:,)?\)\]"
)


def _code_line(code: str, line: int | None) -> str:
    if not line:
        return ""
    lines = code.splitlines()
    if 1 <= line <= len(lines):
        return lines[line - 1].strip()
    return ""


def _stderr_pin_family_hint(part: str, pin: str) -> str:
    """Explain common symbol pin-name families from stderr-only context."""
    part_lower = str(part).lower()
    pin_upper = str(pin).upper()

    if "led_argb" in part_lower or "led_rgb" in part_lower:
        if pin_upper in {"R", "G", "B"}:
            return (
                f"This RGB LED symbol does not expose plain {pin_upper!r}. "
                f"Use {pin_upper}A/{pin_upper}K for the {pin_upper} channel "
                "(A=anode, K=cathode), or for common-anode symbols such as "
                "LED_ARGB use the common A pin plus RK/GK/BK cathode pins."
            )

    if "raspberrypi_pico" in part_lower and (
        "USB" in pin_upper or pin_upper in {"D+", "D-", "TP2", "TP3"}
    ):
        return (
            "The Raspberry Pi Pico module symbol normally represents the "
            "complete module with onboard USB, and may not expose USB D+/D- "
            "test pads as SKiDL pins. For an external USB-C connector, use a "
            "raw RP2040 symbol/design; for a Pico-module board, omit external "
            "USB data wiring or use only the module's exposed power/GPIO pins."
        )

    if ("audiojack" in part_lower or "audioplug" in part_lower) and pin_upper in {
        "T",
        "R",
        "S",
    }:
        return (
            f"Plain {pin_upper!r} is often not a valid pin on switched audio "
            "jack symbols. Inspect subject.available_pins or "
            "search_kicad(detail=true); switched jacks commonly expose pins "
            "like T1/R1/S1 and normalled contacts like TN/RN/SN."
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


def _enrich_code_exceptions(
    exceptions: list[DesignException],
    *,
    stderr: str,
    code: str,
) -> list[DesignException]:
    """Attach actionable context to SKiDL Python execution exceptions."""
    if not exceptions:
        return exceptions
    stderr_tail = stderr[-4000:] if stderr else ""
    unit_pin_match = _UNIT_PIN_ERROR_RE.search(stderr_tail)
    pin_match = _PIN_ERROR_RE.search(stderr_tail)
    line_match = _STRING_LINE_RE.search(stderr_tail)
    line = int(line_match.group("line")) if line_match else None

    for exc in exceptions:
        if exc.code != ExcCode.CODE_EXEC_ERROR:
            continue
        subject = dict(exc.subject or {})
        if stderr_tail:
            subject.setdefault("stderr_tail", stderr_tail)
        if line is not None:
            subject.setdefault("line", line)
            subject.setdefault("line_text", _code_line(code, line))
        if unit_pin_match:
            pin = (
                unit_pin_match.group("pin_quoted")
                or unit_pin_match.group("pin_dquoted")
                or unit_pin_match.group("pin_raw")
                or ""
            ).strip()
            ref = unit_pin_match.group("ref")
            unit = unit_pin_match.group("unit")
            part = unit_pin_match.group("part")
            subject["ref"] = ref
            subject["unit"] = unit
            subject["pin"] = pin
            subject["part"] = part
            subject["multi_unit_pin_access"] = True
            exc.message = (
                f"pin {pin!r} not found on {ref}.{unit} ({part}) while "
                "executing SKiDL code"
            )
            exc.retry_hint = (
                "This is a multi-unit symbol pin lookup. Do not reuse A-side "
                "package pin numbers on B/C/D units. Use "
                "search_kicad(part_name, detail=true) or subject.available_pins "
                "to inspect that exact unit, then wire the listed pins before "
                "resubmitting with submit_skidl_code(); for TL07x-style op-amps "
                "this often means the unit-local '+', '-', and listed output pin."
            )
            exc.retry_hint += _pin_suggestions_hint(subject)
            exc.subject = subject
            continue
        if pin_match:
            pin = (
                pin_match.group("pin_quoted")
                or pin_match.group("pin_dquoted")
                or pin_match.group("pin_raw")
                or ""
            ).strip()
            ref = pin_match.group("ref")
            part = pin_match.group("part")
            mismatch = any(
                subject.get(key) and subject.get(key) != value
                for key, value in (("ref", ref), ("pin", pin), ("part", part))
            )
            subject["ref"] = ref
            subject["pin"] = pin
            subject["part"] = part
            if mismatch:
                subject.pop("available_pins", None)
                subject.pop("suggested_pins", None)
                subject.pop("variable", None)
            exc.message = (
                f"pin {pin!r} not found on {ref} ({part}) while executing "
                "SKiDL code"
            )
            exc.retry_hint = (
                "Edit the SKiDL code to use a valid symbol pin name. Call "
                "search_kicad(part_name, detail=true) if you need the pin list, "
                "then resubmit with submit_skidl_code()."
            )
            exc.retry_hint += _pin_suggestions_hint(subject)
            family_hint = subject.get("pin_family_hint") or _stderr_pin_family_hint(
                part, pin
            )
            if family_hint:
                subject["pin_family_hint"] = family_hint
                exc.retry_hint += f" {family_hint}"
        exc.subject = subject
    return exceptions


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
    route_timeout_s: float = 120,
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
    pipeline_goal: str | None = None,
    placement_preview_mode: str | None = None,
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
        "route_timeout_s": route_timeout_s,
        "pipeline_goal": pipeline_goal or "manufacturing",
        "placement_preview_mode": placement_preview_mode,
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
            artifact_keys = _partial_artifact_names(run_dir)
            exceptions = [
                timeout_exception(
                    timeout_s,
                    artifact_keys=artifact_keys,
                    stderr=stderr,
                )
            ]
            response = DesignResponse(
                run_id=run_id,
                ok=False,
                status="timeout",
                stage="timeout",
                exceptions=exceptions,
                outputs={"partial_artifacts": artifact_keys},
                artifacts={"partial_artifacts": artifact_keys},
                metrics=_manufacturing_metrics({}, exceptions, False),
                stderr=stderr[-4000:],
            )
        else:
            lines = [line for line in stdout.splitlines() if line.strip()]
            try:
                payload = json.loads(lines[-1]) if lines else {}
            except (json.JSONDecodeError, IndexError) as exc:
                payload = {}
                artifact_keys = _partial_artifact_names(run_dir)
                exceptions = [
                    crash_exception(
                        f"worker returned invalid JSON: {type(exc).__name__}: {exc}",
                        stderr=stderr,
                        stage=_infer_crash_stage(payload, run_dir),
                        artifact_keys=artifact_keys,
                    )
                ]
            else:
                exceptions = payload_exceptions(payload, circuit_spec)
                if proc.returncode not in (0, None) and not exceptions:
                    artifact_keys = _partial_artifact_names(run_dir)
                    exceptions = [
                        crash_exception(
                            f"worker exited with status {proc.returncode}",
                            stderr=stderr,
                            stage=_infer_crash_stage(payload, run_dir),
                            artifact_keys=artifact_keys,
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
                metrics=_manufacturing_metrics(payload.get("metrics", {}), exceptions, ok),
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
            response = _attach_layout_quality(response, run_dir)
            _populate_record(record, circuit_spec, response)
    else:
        response = _execute_worker()
        response = _attach_layout_quality(response, run_dir)

    store.save(run_id, circuit_spec, response.exceptions, response)
    return response


def run_pipeline_code(
    code: str,
    board_name: str,
    outline_mm: list[float] | None,
    out_dir,
    timeout_s: float = 300,
    route_timeout_s: float = 120,
    *,
    run_id: str | None = None,
    board_id: str | None = None,
    design_intent: str | None = None,
    corner_radius_mm: float | None = None,
    assembly_policy: str | None = None,
    pipeline_goal: str | None = None,
    placement_preview_mode: str | None = None,
    custom_footprints: dict | list | None = None,
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
        "corner_radius_mm": corner_radius_mm,
        "assembly_policy": assembly_policy,
        "pipeline_goal": pipeline_goal or "manufacturing",
        "placement_preview_mode": placement_preview_mode,
        "custom_footprints": custom_footprints,
        "marketing_text": design_intent or "",
        "route_timeout_s": route_timeout_s,
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
        artifact_keys = _partial_artifact_names(run_dir)
        exceptions = [
            timeout_exception(
                timeout_s,
                artifact_keys=artifact_keys,
                stderr=stderr,
            )
        ]
        response = DesignResponse(
            run_id=run_id, ok=False, status="timeout", stage="timeout",
            exceptions=exceptions,
            outputs={"partial_artifacts": artifact_keys},
            artifacts={"partial_artifacts": artifact_keys},
            metrics=_manufacturing_metrics({}, exceptions, False),
            stderr=stderr[-4000:],
        )
        response = _attach_layout_quality(response, run_dir)
        store.save(
            run_id,
            {
                "_mode": "skidl_python",
                "code": code,
                "board_name": board_name,
                "design_intent": design_intent or "",
                "pipeline_goal": pipeline_goal or "manufacturing",
                "placement_preview_mode": placement_preview_mode,
                "custom_footprints": custom_footprints,
            },
            response.exceptions, response,
        )
        return response

    lines = [line for line in stdout.splitlines() if line.strip()]
    try:
        payload = json.loads(lines[-1]) if lines else {}
    except (json.JSONDecodeError, IndexError) as exc:
        payload = {}
        artifact_keys = _partial_artifact_names(run_dir)
        exceptions = [
            crash_exception(
                f"worker returned invalid JSON: {type(exc).__name__}: {exc}",
                stderr=stderr,
                stage=_infer_crash_stage(payload, run_dir),
                artifact_keys=artifact_keys,
            )
        ]
    else:
        exceptions = [
            exc if isinstance(exc, DesignException)
            else DesignException.model_validate(exc)
            for exc in payload.get("exceptions", [])
        ]
        exceptions = _enrich_code_exceptions(
            exceptions,
            stderr=stderr,
            code=code,
        )
        exceptions = order_exceptions_for_agent(exceptions)
        if proc.returncode not in (0, None) and not exceptions:
            artifact_keys = _partial_artifact_names(run_dir)
            exceptions = [
                crash_exception(
                    f"worker exited with status {proc.returncode}",
                    stderr=stderr,
                    stage=_infer_crash_stage(payload, run_dir),
                    artifact_keys=artifact_keys,
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
        metrics=_manufacturing_metrics(payload.get("metrics", {}), exceptions, ok),
        summary=str(payload.get("summary", "")),
        stderr=stderr[-4000:],
    )
    response = _attach_layout_quality(response, run_dir)

    store.save(
        run_id,
        {
            "_mode": "skidl_python",
            "code": code,
            "board_name": board_name,
            "design_intent": design_intent or "",
            "pipeline_goal": pipeline_goal or "manufacturing",
            "placement_preview_mode": placement_preview_mode,
            "custom_footprints": custom_footprints,
        },
        response.exceptions, response,
    )
    return response
