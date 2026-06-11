"""Async MCP tools for Railway-hosted HTTP server.

Provides submit/poll/fetch pattern instead of synchronous blocking tools.
The existing server.py stays for local stdio development.
"""

from __future__ import annotations

import asyncio
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.db import DB
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.estimator import estimate_complexity as _estimate_complexity
from schemas.exceptions import DesignException

mcp = FastMCP(
    "eda-mcp",
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
db = DB()


@mcp.tool()
async def submit_design(
    input_spec: dict,
    run_options: dict | None = None,
    policy: dict | None = None,
) -> dict:
    """Submit a design job. Returns job_id immediately. Poll with get_job().

    The engine runs asynchronously on a worker. Call get_job(job_id) to check
    status and retrieve results when complete.
    """
    spec = CircuitSpec.model_validate(input_spec)
    job_id = await db.create_job(
        spec.model_dump(mode="json"),
        run_options,
        policy,
    )
    return {"job_id": job_id, "status": "queued"}


@mcp.tool()
async def get_job(job_id: str) -> dict:
    """Poll job status. Returns full result when status is succeeded/failed/timeout."""
    return await db.get_job(job_id)


@mcp.tool()
async def estimate_complexity(input_spec: dict) -> dict:
    """Pre-run complexity estimate. Predicts decisions, cost, success probability.

    Call before submit_design() to gauge how much work a board will need.
    Fast (<2s), no side effects, no cost.
    """
    spec = CircuitSpec.model_validate(input_spec)
    result = await asyncio.to_thread(
        lambda: _estimate_complexity(spec).model_dump(mode="json")
    )
    return result


@mcp.tool()
async def apply_correction(run_id: str, corrections: list[dict]) -> dict:
    """Apply corrections from a previous run and submit a new design job.

    Loads the spec and exceptions from the previous run, applies the selected
    candidates, and enqueues a new job with the corrected spec.
    """
    run_data = await db.load_run(run_id)
    spec = CircuitSpec.model_validate(run_data["spec"])
    exceptions = [DesignException.model_validate(e) for e in run_data["exceptions"]]
    by_exc = {exc.id: exc for exc in exceptions}

    for correction in corrections:
        exc_id = correction.get("exception_id")
        cand_id = correction.get("candidate_id")
        if exc_id not in by_exc:
            raise ValueError(f"unknown exception_id {exc_id!r} for run {run_id}")
        exc = by_exc[exc_id]
        cand = next((c for c in exc.candidates if c.id == cand_id), None)
        if cand is None:
            raise ValueError(f"unknown candidate_id {cand_id!r} for exception {exc_id!r}")
        spec = apply_candidate(spec, exc, cand)

    prev_response = run_data.get("response") or {}
    parent_run_id = prev_response.get("run_id", run_id)

    job_id = await db.create_job(
        spec.model_dump(mode="json"),
        {"parent_run_id": parent_run_id},
        parent_job_id=run_data.get("job_id"),
    )
    return {"job_id": job_id, "status": "queued", "parent_run_id": parent_run_id}


@mcp.tool()
async def get_run(run_id: str) -> dict:
    """Fetch full run data: spec, exceptions, response, and artifacts."""
    return await db.load_run(run_id)
