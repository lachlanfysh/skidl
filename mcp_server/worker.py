"""Job worker for Railway deployment. Polls Postgres for queued jobs, runs engine."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import sys
import tempfile
import time
import traceback
import uuid
from pathlib import Path
from typing import Any

from mcp_server.db import DB
from mcp_server.pipeline import DesignResponse, run_pipeline, run_pipeline_code
from mcp_server.policy import auto_corrections, correction_history_keys, decision_kind, normalize_policy
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import apply_candidate


STALE_REAP_INTERVAL_S = float(os.environ.get("WORKER_STALE_REAP_INTERVAL_S", "15"))
WORKER_RUNTIME_GRACE_S = float(os.environ.get("WORKER_RUNTIME_GRACE_S", "15"))
EASYEDA_CACHE = Path(__file__).resolve().parent.parent / "corpus" / "jlc" / "easyeda_cache"
LCSC_RE = re.compile(r"\bC\d{2,}\b", re.IGNORECASE)


def _job_status_from_result(result: dict) -> str:
    pipeline_status = str(result.get("status") or "")
    if pipeline_status in {
        "succeeded",
        "succeeded_with_warnings",
        "failed_reviewable",
        "failed",
        "timeout",
        "crashed",
    }:
        return pipeline_status
    return "succeeded" if result["ok"] else "failed"


def _job_timeout_s(job: dict) -> float:
    """Return a safe wall-clock budget for one hosted job."""

    try:
        return max(0.0, float((job.get("options") or {}).get("timeout_s", 300)))
    except (TypeError, ValueError):
        return 300.0


def _worker_deadline_s(job: dict) -> float:
    """Slightly larger than engine timeout so cleanup can finish."""

    return _job_timeout_s(job) + max(0.0, WORKER_RUNTIME_GRACE_S)


def _job_pipeline_goal(job: dict) -> str:
    """Return the requested pipeline goal using the worker's accepted aliases."""

    raw = job.get("spec") or {}
    opts = job.get("options") or {}
    value = opts.get("pipeline_goal")
    if value is None and isinstance(raw, dict):
        value = raw.get("pipeline_goal")
    text = str(value or "manufacturing").strip().lower().replace("-", "_")
    aliases = {
        "place": "placement_review",
        "placement": "placement_review",
        "placement_only": "placement_review",
        "review": "placement_review",
        "review_placement": "placement_review",
        "preview": "placement_review",
    }
    normalized = aliases.get(text, text)
    if normalized not in {"manufacturing", "placement_review"}:
        return "manufacturing"
    return normalized


def _is_placement_review_job(job: dict) -> bool:
    return _job_pipeline_goal(job) == "placement_review"


def _exception_codes(result: dict[str, Any]) -> set[str]:
    codes: set[str] = set()
    for exc in result.get("exceptions") or []:
        if isinstance(exc, dict):
            code = exc.get("code")
        else:
            raw_code = getattr(exc, "code", None)
            code = getattr(raw_code, "value", raw_code)
        if code:
            codes.add(str(code))
    return codes


def _annotate_backend_failure_result(result: dict[str, Any]) -> None:
    """Make engine timeout/crash results unambiguously backend feedback."""

    codes = _exception_codes(result)
    if "ENGINE_TIMEOUT" in codes:
        result.setdefault("failure_kind", "engine_timeout")
        result.setdefault("engine_timeout", True)
    elif "ENGINE_CRASH" in codes:
        result.setdefault("failure_kind", "engine_crash")
        result.setdefault("worker_backend_failure", True)
    else:
        return

    result["decision_required"] = True
    result["decision_kind"] = "backend_failure"
    result["recommended_next_tool"] = "get_job"
    result.setdefault("visual_review_ready", False)
    result.setdefault("reviewable_failure", False)


def _prepare_job_for_execution(job: dict) -> dict:
    """Give every hosted execution a stable run_id before the worker starts."""

    prepared = dict(job)
    prepared["options"] = dict(job.get("options") or {})
    prepared["options"].setdefault("run_id", uuid.uuid4().hex[:12])
    return prepared


async def worker_loop(db: DB, slot: int, worker_id: str) -> None:
    """Single worker slot: claim jobs, run engine, store results."""
    label = f"worker-{worker_id}[{slot}]"
    print(f"{label}: ready", flush=True)
    last_stale_reap = 0.0

    while True:
        now = time.monotonic()
        if now - last_stale_reap >= STALE_REAP_INTERVAL_S:
            last_stale_reap = now
            await _reap_stale_jobs(db, label)

        job = await db.claim_job(f"{worker_id}-{slot}")
        if job is None:
            await asyncio.sleep(2)
            continue

        job_id = job["id"]
        print(f"{label}: claimed job {job_id}", flush=True)

        try:
            restored = await _restore_lcsc_assets_for_job(db, job["spec"], label)
            if restored:
                print(
                    f"{label}: restored {restored} converted LCSC asset(s) for job {job_id}",
                    flush=True,
                )
            job = _prepare_job_for_execution(job)
            result = await _execute_job_with_deadline(job, label)
            # Extract artifact file contents before storing — they bloat
            # the jobs.result column and can cause tool response truncation.
            artifacts = _collect_artifacts(result)
            _annotate_result_for_job_payload(result, artifacts)
            status = _job_status_from_result(result)
            await db.complete_job(job_id, status, result=result)

            if result.get("run_id"):
                await db.save_run(
                    result["run_id"],
                    job_id,
                    result.get("spec", job["spec"]),
                    result.get("exceptions", []),
                    result,
                    artifacts=artifacts,
                )

            if result.get("telemetry_record"):
                await db.append_telemetry(result["telemetry_record"])

            print(
                f"{label}: job_result "
                f"{json.dumps(_job_log_summary(job_id, status, result, artifacts), sort_keys=True)}",
                flush=True,
            )
        except Exception as exc:
            tb = traceback.format_exc()
            print(f"{label}: job {job_id} crashed: {exc}\n{tb}", flush=True)
            result = _worker_exception_result(job, job_id, label, exc, tb)
            await db.complete_job(job_id, "crashed", result=result, error=str(exc))


async def _reap_stale_jobs(db: DB, label: str) -> int:
    """Fail orphaned running jobs so queues recover without a deploy restart."""

    try:
        stale = await db.fail_stale_running_jobs()
    except Exception as exc:
        print(f"{label}: stale job reap failed: {exc}", flush=True)
        return 0
    if stale:
        print(f"{label}: marked {stale} stale running job(s) failed", flush=True)
    return stale


async def _execute_job_with_deadline(job: dict, worker_label: str) -> dict:
    """Run a job but force the DB-visible state to respect timeout_s."""

    try:
        return await asyncio.wait_for(
            asyncio.to_thread(_execute_job, job),
            timeout=_worker_deadline_s(job),
        )
    except asyncio.TimeoutError:
        timeout_s = _job_timeout_s(job)
        deadline_s = _worker_deadline_s(job)
        print(
            f"{worker_label}: job {job.get('id')} exceeded worker deadline "
            f"{deadline_s:.1f}s for requested timeout_s={timeout_s:.1f}",
            flush=True,
        )
        return _worker_timeout_result(job, str(job.get("id") or ""), worker_label)


def _lcsc_refs_in_spec(spec: Any) -> set[str]:
    """Return LCSC IDs mentioned anywhere in a job spec."""

    try:
        text = json.dumps(spec)
    except TypeError:
        text = str(spec)
    return {match.group(0).upper() for match in LCSC_RE.finditer(text)}


async def _restore_lcsc_assets_for_job(db: DB, spec: Any, label: str = "worker") -> int:
    """Restore EasyEDA-converted assets needed by this job from Postgres.

    convert_lcsc() can run in the HTTP process while the job runs in a worker
    process with a different local filesystem. The converted_parts table is the
    shared cache; this function makes the disk cache true again before SKiDL
    tries to load Part("C12345", ...).
    """

    restored = 0
    for lcsc in sorted(_lcsc_refs_in_spec(spec)):
        try:
            row = await db.fetchrow(
                "SELECT meta, sym_data, fp_data, step_data FROM converted_parts WHERE lcsc = $1",
                lcsc,
            )
        except Exception as exc:
            print(f"{label}: converted LCSC lookup failed for {lcsc}: {exc}", flush=True)
            continue
        if row is None:
            continue
        try:
            if _restore_lcsc_asset(lcsc, row):
                restored += 1
        except Exception as exc:
            print(f"{label}: converted LCSC restore failed for {lcsc}: {exc}", flush=True)
    return restored


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return row[key]


def _restore_lcsc_asset(lcsc: str, row: Any) -> bool:
    """Write one converted LCSC part from a DB row into easyeda_cache."""

    raw_meta = _row_get(row, "meta")
    if isinstance(raw_meta, str):
        meta = json.loads(raw_meta)
    else:
        meta = dict(raw_meta or {})
    if not meta:
        return False

    cache_dir = EASYEDA_CACHE / lcsc
    cache_dir.mkdir(parents=True, exist_ok=True)
    (cache_dir / "meta.json").write_text(json.dumps(meta, indent=2))

    sym_data = _row_get(row, "sym_data")
    if sym_data:
        sym_file = Path(meta.get("sym_file") or cache_dir / f"{lcsc}.kicad_sym")
        if not sym_file.is_absolute():
            sym_file = cache_dir / sym_file.name
        sym_file.parent.mkdir(parents=True, exist_ok=True)
        sym_file.write_bytes(bytes(sym_data))

    fp_data = _row_get(row, "fp_data")
    if fp_data:
        fp_dir = Path(meta.get("fp_dir") or cache_dir / f"{lcsc}.pretty")
        if not fp_dir.is_absolute():
            fp_dir = cache_dir / fp_dir.name
        fp_dir.mkdir(parents=True, exist_ok=True)
        fp_name = str(meta.get("footprint") or "").split(":")[-1] or lcsc
        (fp_dir / f"{fp_name}.kicad_mod").write_bytes(bytes(fp_data))

    step_data = _row_get(row, "step_data")
    if step_data:
        shapes_dir = cache_dir / f"{lcsc}.3dshapes"
        shapes_dir.mkdir(parents=True, exist_ok=True)
        (shapes_dir / f"{lcsc}.step").write_bytes(bytes(step_data))

    return True


def _job_log_summary(
    job_id: str,
    status: str,
    result: dict[str, Any],
    artifacts: dict[str, str],
) -> dict[str, Any]:
    """Small, non-secret summary for Railway logs."""
    exceptions = result.get("exceptions") or []
    exc_summaries: list[dict[str, Any]] = []
    for exc in exceptions[:5]:
        if not isinstance(exc, dict):
            continue
        exc_summaries.append({
            "code": exc.get("code"),
            "severity": exc.get("severity"),
            "message": str(exc.get("message") or "")[:240],
            "candidate_count": len(exc.get("candidates") or []),
        })
    return {
        "job_id": job_id,
        "run_id": result.get("run_id"),
        "status": status,
        "pipeline_status": result.get("status"),
        "ok": bool(result.get("ok")),
        "stage": result.get("stage"),
        "decision_kind": result.get("decision_kind"),
        "exception_count": len(exceptions),
        "exceptions": exc_summaries,
        "artifact_count": len(artifacts),
        "artifact_keys": sorted(artifacts)[:20],
    }


def _worker_exception_result(
    job: dict,
    job_id: str,
    worker_id: str,
    exc: Exception,
    traceback_text: str,
) -> dict[str, Any]:
    """Structured result for crashes outside the engine subprocess."""
    raw = job.get("spec") or {}
    mode = raw.get("_mode") if isinstance(raw, dict) else None
    timeout_s = (job.get("options") or {}).get("timeout_s", 300)
    traceback_tail = "\n".join(traceback_text.splitlines()[-40:])
    return {
        "run_id": None,
        "ok": False,
        "status": "crashed",
        "stage": "worker_exception",
        "decision_required": True,
        "decision_kind": "backend_failure",
        "recommended_next_tool": "get_job",
        "failure_kind": "worker_exception",
        "worker_backend_failure": True,
        "exceptions": [
            {
                "id": "e-worker-exception",
                "code": "ENGINE_CRASH",
                "severity": "fatal",
                "message": (
                    "worker crashed while handling the job; retry once unchanged"
                ),
                "subject": {
                    "stage": "worker_exception",
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "mode": mode or "circuit_spec",
                    "timeout_s": timeout_s,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "traceback_tail": traceback_tail,
                },
                "candidates": [
                    {
                        "id": "c1",
                        "action": "regenerate",
                        "params": {},
                        "human_summary": (
                            "retry unchanged; this is a backend worker failure, "
                            "not circuit feedback"
                        ),
                        "cost_hint": "cheap",
                        "confidence": 0.8,
                        "source": "deterministic",
                    }
                ],
                "retry_hint": (
                    "Retry once unchanged. If the crash repeats, report the "
                    "job_id and traceback tail instead of rewriting the circuit."
                ),
            }
        ],
        "summary": f"Worker crashed before returning an engine result: {exc}",
        "metrics": {"manufacturable": False, "manufacturing_complete": False},
        "visual_review_ready": False,
        "reviewable_failure": False,
    }


def _worker_timeout_result(job: dict, job_id: str, worker_id: str) -> dict[str, Any]:
    """Structured result when the Python worker wrapper outlives timeout_s."""

    raw = job.get("spec") or {}
    mode = raw.get("_mode") if isinstance(raw, dict) else None
    options = job.get("options") or {}
    timeout_s = _job_timeout_s(job)
    deadline_s = _worker_deadline_s(job)
    run_id = options.get("run_id")
    return {
        "run_id": run_id,
        "ok": False,
        "status": "timeout",
        "stage": "worker_runtime_timeout",
        "failure_kind": "worker_runtime_timeout",
        "worker_timeout": True,
        "decision_required": True,
        "decision_kind": "backend_failure",
        "recommended_next_tool": "get_job",
        "exceptions": [
            {
                "id": "e-worker-runtime-timeout",
                "code": "ENGINE_TIMEOUT",
                "severity": "fatal",
                "message": (
                    "hosted worker exceeded the job timeout envelope; retry once unchanged"
                ),
                "subject": {
                    "stage": "worker_runtime_timeout",
                    "job_id": job_id,
                    "worker_id": worker_id,
                    "mode": mode or "circuit_spec",
                    "timeout_s": timeout_s,
                    "worker_deadline_s": deadline_s,
                    "partial_artifacts": [],
                },
                "candidates": [
                    {
                        "id": "c1",
                        "action": "regenerate",
                        "params": {},
                        "human_summary": (
                            "retry unchanged; this is a hosted runtime timeout, "
                            "not circuit feedback"
                        ),
                        "cost_hint": "cheap",
                        "confidence": 0.8,
                        "source": "deterministic",
                    }
                ],
                "retry_hint": (
                    "Retry once unchanged. If the timeout repeats, report the "
                    "job_id as a hosted worker timeout instead of rewriting the circuit."
                ),
            }
        ],
        "summary": (
            f"Hosted worker exceeded timeout_s={timeout_s:.1f}s "
            f"(deadline {deadline_s:.1f}s) before returning an engine result."
        ),
        "metrics": {
            "manufacturable": False,
            "manufacturing_complete": False,
            "visual_review_ready": False,
            "product_layout_ok": False,
        },
        "visual_review_ready": False,
        "reviewable_failure": False,
    }


def _artifact_summary(artifacts: dict[str, str]) -> dict[str, Any]:
    """Small artifact manifest safe to keep in jobs.result."""

    keys = sorted(artifacts)
    previews = [
        key for key in keys
        if "preview" in key.lower() and key.lower().endswith((".png", ".svg"))
    ]
    kicad = [
        key for key in keys
        if key.lower().endswith((".kicad_pcb", ".kicad_sch", ".kicad_pro"))
    ]
    manufacturing = [
        key for key in keys
        if key in {"bom.csv", "cpl.csv", "_board.zip"}
        or key.lower().startswith("gerbers/")
    ]
    return {
        "available": bool(keys),
        "count": len(keys),
        "keys": keys[:50],
        "truncated": max(len(keys) - 50, 0),
        "preview_available": bool(previews),
        "preview_keys": previews[:10],
        "kicad_keys": kicad[:10],
        "manufacturing_keys": manufacturing[:10],
        "zip_available": "_board.zip" in artifacts,
    }


def _annotate_result_for_job_payload(
    result: dict[str, Any],
    artifacts: dict[str, str],
) -> None:
    """Expose reviewability and artifact availability without embedding files."""

    summary = _artifact_summary(artifacts)
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}
    quality = (
        result.get("layout_quality")
        if isinstance(result.get("layout_quality"), dict)
        else {}
    )
    gates = quality.get("gates") if isinstance(quality.get("gates"), dict) else {}
    visual_review_ready = bool(
        result.get("visual_review_ready")
        or metrics.get("visual_review_ready")
        or gates.get("visual_review_ready")
        or summary["preview_available"]
    )
    product_layout_known = (
        "product_layout_ok" in metrics
        or "product_layout_ok" in gates
    )
    product_layout_ok = bool(
        metrics.get("product_layout_ok", gates.get("product_layout_ok", False))
    )
    reviewable_failure = bool(
        result.get("reviewable_failure")
        or result.get("status") == "failed_reviewable"
        or (
            visual_review_ready
            and product_layout_known
            and not product_layout_ok
        )
    )
    result["artifact_summary"] = summary
    result["run_artifacts_available"] = bool(artifacts)
    result["visual_review_ready"] = visual_review_ready
    result["reviewable_failure"] = reviewable_failure
    if result.get("run_id") and artifacts:
        result["recommended_artifact_tool"] = "get_run"
    if reviewable_failure:
        result.setdefault(
            "review_note",
            (
                "failed but reviewable: call get_run(run_id) and inspect previews/artifacts "
                "before changing the design"
            ),
        )


def _execute_job(job: dict) -> dict:
    """Run the engine pipeline synchronously (called via to_thread)."""
    raw = job["spec"]
    if raw.get("_mode") == "skidl_python":
        return _execute_skidl_job(job)
    spec = CircuitSpec.model_validate(raw)
    opts = job.get("options") or {}
    policy_dict = job.get("policy") or {}
    policy = normalize_policy(policy_dict)

    timeout_s = float(opts.get("timeout_s", 300))
    route_timeout_s = float(opts.get("route_timeout_s", 120))
    parent_job_id = job.get("parent_job_id")

    with tempfile.TemporaryDirectory(prefix="eda-run-") as tmpdir:
        base_kwargs = {
            "timeout_s": timeout_s,
            "route_timeout_s": route_timeout_s,
            "mode": str(opts.get("mode", "engine_only")),
            "board_id": opts.get("board_id"),
            "record_telemetry": bool(opts.get("record_telemetry", True)),
            "record_fields": opts.get("record_fields"),
            "validation_mode": opts.get("validation_mode"),
            "model_tier": opts.get("model_tier"),
            "bom_match_score": opts.get("bom_match_score"),
            "netlist_match_score": opts.get("netlist_match_score"),
            "pipeline_goal": opts.get("pipeline_goal"),
            "placement_preview_mode": opts.get("placement_preview_mode")
            or opts.get("preview_mode"),
        }

        response = run_pipeline(
            spec,
            tmpdir,
            run_id=opts.get("run_id"),
            correction_iterations=int(opts.get("correction_iterations", 0) or 0),
            corrections_applied=list(opts.get("corrections_applied") or []),
            llm_stages=opts.get("llm_stages"),
            parent_run_id=opts.get("parent_run_id"),
            **base_kwargs,
        )

        corrections: list[dict] = []
        correction_strings: list[str] = []
        history: set[str] = set()
        skip_internal_corrections = _is_placement_review_job(job)

        correction_limit = (
            0 if skip_internal_corrections else policy.max_internal_corrections
        )
        for iteration in range(correction_limit):
            if not response.exceptions:
                break
            kind = decision_kind(response.exceptions)
            if kind in set(policy.stop_for):
                break
            choices = auto_corrections(response.exceptions, policy, history)
            if not choices:
                break
            history.update(correction_history_keys(response.exceptions, choices))
            spec, applied, telemetry_actions = _apply_choices(spec, response.exceptions, choices)
            corrections.extend(applied)
            correction_strings.extend(telemetry_actions)
            response = run_pipeline(
                spec,
                tmpdir,
                correction_iterations=int(opts.get("correction_iterations", 0) or 0) + iteration + 1,
                corrections_applied=list(opts.get("corrections_applied") or []) + correction_strings,
                llm_stages=opts.get("llm_stages"),
                parent_run_id=response.run_id,
                **base_kwargs,
            )

        result = response.model_dump(mode="json")
        result["spec"] = spec.model_dump(mode="json")
        result["corrections_applied"] = corrections
        if skip_internal_corrections and policy.max_internal_corrections:
            result.setdefault("metrics", {})
            result["metrics"]["internal_corrections_skipped"] = True
            result["metrics"]["internal_corrections_skip_reason"] = (
                "placement_review returns the first reviewable placement "
                "instead of spending the job timeout on hidden retries"
            )

        if response.exceptions:
            result["decision_required"] = True
            result["decision_kind"] = decision_kind(response.exceptions)
        _annotate_backend_failure_result(result)

        result["_artifact_paths"] = _find_artifacts(Path(tmpdir))
        return result


def _execute_skidl_job(job: dict) -> dict:
    """Run SKiDL Python code through the pipeline (called via to_thread)."""
    raw = job["spec"]
    opts = job.get("options") or {}
    timeout_s = float(opts.get("timeout_s", 300))
    route_timeout_s = float(opts.get("route_timeout_s", 120))
    assembly_policy = opts.get("assembly_policy") or raw.get("assembly_policy")
    pipeline_goal = opts.get("pipeline_goal") or raw.get("pipeline_goal")

    with tempfile.TemporaryDirectory(prefix="eda-run-") as tmpdir:
        response = run_pipeline_code(
            code=raw["code"],
            board_name=raw.get("board_name", "board"),
            outline_mm=raw.get("outline_mm"),
            corner_radius_mm=raw.get("corner_radius_mm"),
            out_dir=tmpdir,
            timeout_s=timeout_s,
            route_timeout_s=route_timeout_s,
            run_id=opts.get("run_id"),
            board_id=opts.get("board_id"),
            design_intent=raw.get("design_intent") or raw.get("marketing_text"),
            assembly_policy=assembly_policy,
            pipeline_goal=pipeline_goal,
            placement_preview_mode=opts.get("placement_preview_mode")
            or opts.get("preview_mode"),
            custom_footprints=raw.get("custom_footprints"),
        )

        result = response.model_dump(mode="json")
        result["spec"] = raw

        if response.exceptions:
            result["decision_required"] = True
            result["decision_kind"] = decision_kind(response.exceptions)
        _annotate_backend_failure_result(result)

        result["_artifact_paths"] = _find_artifacts(Path(tmpdir))
        return result


def _apply_choices(spec, exceptions, choices):
    """Apply correction choices to spec. Mirrors server.py._apply_choices."""
    from schemas.corrections import apply_candidate as _apply
    from schemas.exceptions import DesignException

    by_exc = {exc.id: exc for exc in exceptions}
    updated = spec
    applied: list[dict] = []
    telemetry_actions: list[str] = []

    for choice in choices:
        exc_id = choice.get("exception_id")
        cand_id = choice.get("candidate_id")
        exc = by_exc.get(exc_id)
        if exc is None:
            continue
        cand = next((c for c in exc.candidates if c.id == cand_id), None)
        if cand is None:
            continue
        updated = _apply(updated, exc, cand)
        applied.append({
            "exception_id": exc.id,
            "candidate_id": cand.id,
            "code": exc.code.value,
            "action": cand.action.value,
            "human_summary": cand.human_summary,
        })
        telemetry_actions.append(f"{exc.code.value}:{cand.action.value}:{cand.id}")
    return updated, applied, telemetry_actions


def _find_artifacts(run_dir: Path) -> dict[str, str]:
    """Collect generated file contents from tmpdir before cleanup.

    If the board uses converted LCSC parts (easyeda2kicad), bundles
    those libraries into a self-contained zip alongside the PCB/schematic.
    Also collects manufacturing files (Gerbers, drills, BOM, CPL) when present.
    """
    artifacts = {}
    for ext in ("*.kicad_pcb", "*.kicad_sch", "*.svg"):
        for path in run_dir.rglob(ext):
            artifacts[path.name] = path.read_text(errors="replace")

    for path in run_dir.rglob("*.png"):
        artifacts[path.name] = base64.b64encode(path.read_bytes()).decode("ascii")

    # BOM and CPL CSVs
    for csv_name in ("bom.csv", "cpl.csv"):
        csv_path = run_dir / csv_name
        if csv_path.exists():
            artifacts[csv_name] = csv_path.read_text(errors="replace")

    # Gerber + drill files
    gerber_dir = run_dir / "gerbers"
    gerber_files: dict[str, str] = {}
    if gerber_dir.is_dir():
        for gf in gerber_dir.iterdir():
            if gf.suffix in (".gbr", ".drl", ".gbrjob"):
                gerber_files[gf.name] = gf.read_text(errors="replace")

    if not artifacts:
        return artifacts

    # Check if any converted LCSC libraries are referenced
    easyeda_cache = Path(__file__).resolve().parent.parent / "corpus" / "jlc" / "easyeda_cache"
    used_libs: set[str] = set()
    for name, content in artifacts.items():
        if name.lower().endswith(".png"):
            continue
        for lcsc_dir in (easyeda_cache.iterdir() if easyeda_cache.is_dir() else []):
            if lcsc_dir.name in content:
                used_libs.add(lcsc_dir.name)

    # Build zip when: custom libs, multiple schematics, or manufacturing files
    sch_count = sum(1 for k in artifacts if k.endswith(".kicad_sch"))
    has_mfg = bool(gerber_files) or "bom.csv" in artifacts
    if used_libs or sch_count > 1 or has_mfg:
        artifacts["_board.zip"] = _build_zip(
            artifacts, used_libs, easyeda_cache, gerber_files,
        )

    return artifacts


def _build_zip(
    artifacts: dict[str, str],
    used_libs: set[str],
    easyeda_cache: Path,
    gerber_files: dict[str, str] | None = None,
) -> str:
    """Build a self-contained zip with board files, custom libs, manufacturing output, and kicad_pro."""
    import zipfile
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Board files
        for name, content in artifacts.items():
            if name.startswith("_"):
                continue
            if name.lower().endswith(".png"):
                zf.writestr(name, base64.b64decode(content.encode("ascii")))
            else:
                zf.writestr(name, content)

        # Manufacturing files (Gerbers + drills)
        for name, content in (gerber_files or {}).items():
            zf.writestr(f"gerbers/{name}", content)

        # Custom libraries from easyeda2kicad
        lib_entries = []
        fp_entries = []
        for lib_name in sorted(used_libs):
            lib_dir = easyeda_cache / lib_name

            # Symbol library
            sym_file = lib_dir / f"{lib_name}.kicad_sym"
            if sym_file.exists():
                zf.writestr(f"libs/{sym_file.name}", sym_file.read_text(errors="replace"))
                lib_entries.append(lib_name)

            # Footprint library (.pretty dir)
            pretty_dir = lib_dir / f"{lib_name}.pretty"
            if pretty_dir.is_dir():
                for mod in pretty_dir.glob("*.kicad_mod"):
                    zf.writestr(f"libs/{lib_name}.pretty/{mod.name}", mod.read_text(errors="replace"))
                fp_entries.append(lib_name)

            # 3D models
            shapes_dir = lib_dir / f"{lib_name}.3dshapes"
            if shapes_dir.is_dir():
                for model in shapes_dir.iterdir():
                    if model.suffix in (".step", ".wrl"):
                        zf.writestr(f"libs/{lib_name}.3dshapes/{model.name}", model.read_bytes())

        # Generate project files referencing local libs
        board_name = next(
            (n.rsplit(".", 1)[0] for n in artifacts if n.endswith(".kicad_pcb")),
            "board",
        )
        pro, sym_table, fp_table = _kicad_project_files(board_name, lib_entries, fp_entries)
        zf.writestr(f"{board_name}.kicad_pro", pro)
        if sym_table:
            zf.writestr("sym-lib-table", sym_table)
        if fp_table:
            zf.writestr("fp-lib-table", fp_table)

    return base64.b64encode(buf.getvalue()).decode("ascii")


def _kicad_project_files(
    board_name: str, sym_libs: list[str], fp_libs: list[str],
) -> tuple[str, str, str]:
    """Generate .kicad_pro, sym-lib-table, and fp-lib-table for custom libs."""
    import json

    pro = {
        "meta": {"filename": f"{board_name}.kicad_pro", "version": 1},
        "project": {
            "meta": {"filename": f"{board_name}.kicad_pro", "version": 1},
            "libraries": {
                "pinned_symbol_libs": list(sym_libs),
                "pinned_footprint_libs": list(fp_libs),
            },
        },
    }

    sym_table = "(sym_lib_table\n"
    for lib in sym_libs:
        sym_table += (
            f'  (lib (name "{lib}")(type "KiCad")'
            f'(uri "${{KIPRJMOD}}/libs/{lib}.kicad_sym")'
            f'(options "")(descr "LCSC {lib}"))\n'
        )
    sym_table += ")\n"

    fp_table = "(fp_lib_table\n"
    for lib in fp_libs:
        fp_table += (
            f'  (lib (name "{lib}")(type "KiCad")'
            f'(uri "${{KIPRJMOD}}/libs/{lib}.pretty")'
            f'(options "")(descr "LCSC {lib}"))\n'
        )
    fp_table += ")\n"

    return json.dumps(pro, indent=2), sym_table, fp_table


def _collect_artifacts(result: dict) -> dict:
    """Extract artifact file contents from the result's tmpdir paths."""
    paths = result.pop("_artifact_paths", {})
    return paths


def health_app(db: DB, worker_id: str, concurrency: int):
    """Tiny HTTP app so Railway's healthcheck (and ops) can see the worker."""
    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Route

    async def health(request):
        db_ok = db.pool is not None
        counts = {
            "pending": 0,
            "queued": 0,
            "running": 0,
            "stale_running": 0,
            "active": 0,
        }
        if db_ok:
            try:
                counts = await db.job_status_counts()
            except Exception:
                db_ok = False
        return JSONResponse({
            "status": "ok" if db_ok else "degraded",
            "worker_id": worker_id,
            "concurrency": concurrency,
            "db": db_ok,
            "pending_jobs": counts["pending"],
            "queued_jobs": counts["queued"],
            "running_jobs": counts["running"],
            "stale_running_jobs": counts["stale_running"],
            "active_jobs": counts["active"],
        })

    return Starlette(routes=[Route("/health", health)])


async def main() -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    concurrency = int(os.environ.get("WORKER_CONCURRENCY", "2"))
    worker_id = uuid.uuid4().hex[:8]

    db = DB()
    await db.connect(database_url)
    stale = await db.fail_stale_running_jobs()
    print(f"Worker {worker_id} started, concurrency={concurrency}", flush=True)
    if stale:
        print(f"Marked {stale} stale running job(s) failed", flush=True)

    import uvicorn
    port = int(os.environ.get("PORT", 8001))
    server = uvicorn.Server(uvicorn.Config(
        health_app(db, worker_id, concurrency),
        host="0.0.0.0", port=port, log_level="warning",
    ))

    try:
        tasks = [
            asyncio.create_task(worker_loop(db, i, worker_id))
            for i in range(concurrency)
        ]
        tasks.append(asyncio.create_task(server.serve()))
        await asyncio.gather(*tasks)
    finally:
        await db.close()


if __name__ == "__main__":
    asyncio.run(main())
