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
from typing import Any

from mcp_server.db import DB
from mcp_server.pipeline import DesignResponse, run_pipeline, run_pipeline_code
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
            # Extract artifact file contents before storing — they bloat
            # the jobs.result column and can cause tool response truncation.
            artifacts = _collect_artifacts(result)
            status = "succeeded" if result["ok"] else "failed"
            if result.get("status") == "timeout":
                status = "timeout"
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
            await db.complete_job(job_id, "failed", error=str(exc))


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


def _execute_skidl_job(job: dict) -> dict:
    """Run SKiDL Python code through the pipeline (called via to_thread)."""
    raw = job["spec"]
    opts = job.get("options") or {}
    timeout_s = float(opts.get("timeout_s", 300))
    route_timeout_s = float(opts.get("route_timeout_s", 120))

    with tempfile.TemporaryDirectory(prefix="eda-run-") as tmpdir:
        response = run_pipeline_code(
            code=raw["code"],
            board_name=raw.get("board_name", "board"),
            outline_mm=raw.get("outline_mm"),
            out_dir=tmpdir,
            timeout_s=timeout_s,
            route_timeout_s=route_timeout_s,
            board_id=opts.get("board_id"),
            design_intent=raw.get("design_intent") or raw.get("marketing_text"),
        )

        result = response.model_dump(mode="json")
        result["spec"] = raw

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
    """Collect generated file contents from tmpdir before cleanup.

    If the board uses converted LCSC parts (easyeda2kicad), bundles
    those libraries into a self-contained zip alongside the PCB/schematic.
    Also collects manufacturing files (Gerbers, drills, BOM, CPL) when present.
    """
    artifacts = {}
    for ext in ("*.kicad_pcb", "*.kicad_sch"):
        for path in run_dir.rglob(ext):
            artifacts[path.name] = path.read_text(errors="replace")

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
    for content in artifacts.values():
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
    import base64
    import zipfile
    from io import BytesIO

    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # Board files
        for name, content in artifacts.items():
            if name.startswith("_"):
                continue
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
