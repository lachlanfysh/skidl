"""Async Postgres layer for the Railway-hosted MCP server."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import asyncpg


class DB:
    """asyncpg pool + CRUD for jobs, runs, and telemetry."""

    def __init__(self) -> None:
        self.pool: asyncpg.Pool | None = None

    async def connect(self, database_url: str) -> None:
        self.pool = await asyncpg.create_pool(database_url, min_size=2, max_size=10)
        await self._ensure_tables()

    async def close(self) -> None:
        if self.pool:
            await self.pool.close()

    async def _ensure_tables(self) -> None:
        schema_path = Path(__file__).resolve().parent.parent / "deploy" / "schema.sql"
        schema_sql = schema_path.read_text()
        async with self.pool.acquire() as conn:
            await conn.execute(schema_sql)

    # ── Jobs ──────────────────────────────────────────────────────────

    async def create_job(
        self,
        spec: dict,
        options: dict | None = None,
        policy: dict | None = None,
        parent_job_id: str | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex[:12]
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO jobs (id, spec, options, policy, parent_job_id)
                   VALUES ($1, $2, $3, $4, $5)""",
                job_id,
                json.dumps(spec),
                json.dumps(options or {}),
                json.dumps(policy or {}),
                parent_job_id,
            )
        return job_id

    async def claim_job(self, worker_id: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE jobs SET status = 'running', started_at = NOW(), worker_id = $1
                   WHERE id = (
                       SELECT id FROM jobs
                       WHERE status = 'queued'
                       ORDER BY created_at
                       FOR UPDATE SKIP LOCKED
                       LIMIT 1
                   )
                   RETURNING id, spec, options, policy, parent_job_id""",
                worker_id,
            )
        if row is None:
            return None
        return {
            "id": row["id"],
            "spec": json.loads(row["spec"]),
            "options": json.loads(row["options"]),
            "policy": json.loads(row["policy"]),
            "parent_job_id": row["parent_job_id"],
        }

    async def complete_job(
        self,
        job_id: str,
        status: str,
        result: dict | None = None,
        error: str | None = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """UPDATE jobs
                   SET status = $2, result = $3, error = $4, finished_at = NOW()
                   WHERE id = $1""",
                job_id,
                status,
                json.dumps(result) if result else None,
                error,
            )

    async def get_job(self, job_id: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM jobs WHERE id = $1", job_id)
        if row is None:
            raise KeyError(f"job {job_id!r} not found")
        return _job_row_to_dict(row)

    async def count_pending(self) -> int:
        async with self.pool.acquire() as conn:
            return await conn.fetchval(
                "SELECT COUNT(*) FROM jobs WHERE status IN ('queued', 'running')"
            )

    # ── Runs ──────────────────────────────────────────────────────────

    async def save_run(
        self,
        run_id: str,
        job_id: str | None,
        spec: dict,
        exceptions: list[dict],
        response: dict,
        artifacts: dict | None = None,
    ) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO runs (run_id, job_id, spec, exceptions, response, artifacts)
                   VALUES ($1, $2, $3, $4, $5, $6)
                   ON CONFLICT (run_id) DO UPDATE
                   SET spec = $3, exceptions = $4, response = $5, artifacts = $6""",
                run_id,
                job_id,
                json.dumps(spec),
                json.dumps(exceptions),
                json.dumps(response),
                json.dumps(artifacts or {}),
            )

    async def load_run(self, run_id: str) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT run_id, job_id, spec, exceptions, response, artifacts FROM runs WHERE run_id = $1",
                run_id,
            )
            if row is None:
                row = await conn.fetchrow(
                    "SELECT run_id, job_id, spec, exceptions, response, artifacts FROM runs WHERE job_id = $1 ORDER BY created_at DESC LIMIT 1",
                    run_id,
                )
        if row is None:
            raise KeyError(f"run {run_id!r} not found")
        return {
            "run_id": row["run_id"],
            "job_id": row["job_id"],
            "spec": json.loads(row["spec"]),
            "exceptions": json.loads(row["exceptions"]),
            "response": json.loads(row["response"]),
            "artifacts": json.loads(row["artifacts"]) if row["artifacts"] else {},
        }

    # ── Telemetry ─────────────────────────────────────────────────────

    async def append_telemetry(self, record: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO telemetry
                   (run_id, board_id, mode, status, geometry,
                    cpu_time_s, peak_rss_mb, layout_score, total_hpwl_mm,
                    congestion_score, exceptions_raised, record)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)""",
                record.get("run_id"),
                record.get("board_id"),
                record.get("mode"),
                record.get("status"),
                json.dumps(record.get("geometry")) if record.get("geometry") else None,
                record.get("cpu_time_s"),
                record.get("peak_rss_mb"),
                record.get("layout_score"),
                record.get("total_hpwl_mm"),
                record.get("congestion_score"),
                json.dumps(record.get("exceptions_raised", [])),
                json.dumps(record),
            )

    # ── Housekeeping ──────────────────────────────────────────────────

    async def expire_old_jobs(self, hours: int = 48) -> int:
        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """DELETE FROM jobs
                   WHERE finished_at < NOW() - make_interval(hours => $1)
                   AND status NOT IN ('queued', 'running')""",
                hours,
            )
            return int(result.split()[-1])


def _job_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for key in ("id", "status", "parent_job_id", "error", "worker_id"):
        d[key] = row[key]
    for key in ("spec", "options", "policy", "result"):
        val = row[key]
        d[key] = json.loads(val) if val else None
    for key in ("created_at", "started_at", "finished_at"):
        ts = row[key]
        d[key] = ts.isoformat() if ts else None
    return d
