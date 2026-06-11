"""Job worker for Railway deployment. Polls Postgres for queued jobs, runs engine."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import traceback
import uuid
from pathlib import Path

from mcp_server.db import DB
from mcp_server.pipeline import DesignResponse, run_pipeline
from mcp_server.policy import auto_corrections, correction_history_keys, decision_kind, normalize_policy
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import apply_candidate


async def worker_loop(db: DB, slot: int, worker_id: str) -> None:
    """Single worker slot: claim jobs, run engine, store results."""
    label = f"worker-{worker_id}[{slot}]"
    print(f"{label}: ready", flush=True)

    while True:
        job = await db.claim_job(f"{worker_id}-{slot}")
        if job is None:
            await asyncio.sleep(2)
            continue

        job_id = job["id"]
        print(f"{label}: claimed job {job_id}", flush=True)

        try:
            result = await asyncio.to_thread(_execute_job, job)
            status = "succeeded" if result["ok"] else "failed"
            if result.get("status") == "timeout":
                status = "timeout"
            await db.complete_job(job_id, status, result=result)

            if result.get("run_id"):
                artifacts = _collect_artifacts(result)
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

            print(f"{label}: job {job_id} → {status}", flush=True)
        except Exception as exc:
            tb = traceback.format_exc()
            print(f"{label}: job {job_id} crashed: {exc}\n{tb}", flush=True)
            await db.complete_job(job_id, "failed", error=str(exc))


def _execute_job(job: dict) -> dict:
    """Run the engine pipeline synchronously (called via to_thread)."""
    spec = CircuitSpec.model_validate(job["spec"])
    opts = job.get("options") or {}
    policy_dict = job.get("policy") or {}
    policy = normalize_policy(policy_dict)

    timeout_s = float(opts.get("timeout_s", 300))
    parent_job_id = job.get("parent_job_id")

    with tempfile.TemporaryDirectory(prefix="eda-run-") as tmpdir:
        base_kwargs = {
            "timeout_s": timeout_s,
            "mode": str(opts.get("mode", "engine_only")),
            "board_id": opts.get("board_id"),
            "record_telemetry": bool(opts.get("record_telemetry", True)),
            "record_fields": opts.get("record_fields"),
            "validation_mode": opts.get("validation_mode"),
            "model_tier": opts.get("model_tier"),
            "bom_match_score": opts.get("bom_match_score"),
            "netlist_match_score": opts.get("netlist_match_score"),
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

        for iteration in range(policy.max_internal_corrections):
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

        if response.exceptions:
            result["decision_required"] = True
            result["decision_kind"] = decision_kind(response.exceptions)

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
    """Collect generated file contents from tmpdir before cleanup."""
    artifacts = {}
    for ext in ("*.kicad_pcb", "*.kicad_sch"):
        for path in run_dir.rglob(ext):
            artifacts[path.name] = path.read_text(errors="replace")
    return artifacts


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
        pending = 0
        if db_ok:
            try:
                pending = await db.count_pending()
            except Exception:
                db_ok = False
        return JSONResponse({
            "status": "ok" if db_ok else "degraded",
            "worker_id": worker_id,
            "concurrency": concurrency,
            "db": db_ok,
            "pending_jobs": pending,
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
    print(f"Worker {worker_id} started, concurrency={concurrency}", flush=True)

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
