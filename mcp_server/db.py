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

    # ── Generic helpers ────────────────────────────────────────────────

    async def fetchrow(self, query: str, *args) -> Any:
        async with self.pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def execute(self, query: str, *args) -> str:
        async with self.pool.acquire() as conn:
            return await conn.execute(query, *args)

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

    async def job_status_counts(
        self,
        *,
        stale_grace_seconds: float = 120.0,
        min_stale_seconds: float = 1800.0,
    ) -> dict[str, int]:
        """Return queue counters, including orphaned running jobs."""

        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    COUNT(*) FILTER (WHERE status = 'queued') AS queued,
                    COUNT(*) FILTER (WHERE status = 'running') AS running,
                    COUNT(*) FILTER (
                        WHERE status = 'running'
                        AND started_at IS NOT NULL
                        AND started_at < NOW() - make_interval(
                            secs => GREATEST(
                                COALESCE((options->>'timeout_s')::double precision, 300.0)
                                    + $1::double precision,
                                $2::double precision
                            )
                        )
                    ) AS stale_running
                FROM jobs
                """,
                float(stale_grace_seconds),
                float(min_stale_seconds),
            )
        queued = int(row["queued"] or 0)
        running = int(row["running"] or 0)
        stale = int(row["stale_running"] or 0)
        return {
            "queued": queued,
            "running": running,
            "stale_running": stale,
            "pending": queued + running,
            "active": queued + max(running - stale, 0),
        }

    async def fail_stale_running_jobs(
        self,
        *,
        stale_grace_seconds: float = 120.0,
        min_stale_seconds: float = 1800.0,
    ) -> int:
        """Mark orphaned running jobs failed after deploys/worker loss."""

        async with self.pool.acquire() as conn:
            result = await conn.execute(
                """
                UPDATE jobs
                SET status = 'crashed',
                    result = COALESCE(
                        result,
                        jsonb_build_object(
                            'run_id', NULL,
                            'ok', false,
                            'status', 'crashed',
                            'stage', 'worker_lost',
                            'decision_required', true,
                            'decision_kind', 'backend_failure',
                            'recommended_next_tool', 'submit_skidl_code',
                            'exceptions', jsonb_build_array(
                                jsonb_build_object(
                                    'id', 'e-worker-lost',
                                    'code', 'ENGINE_CRASH',
                                    'severity', 'fatal',
                                    'message',
                                        'worker lost while job was running; retry once unchanged',
                                    'subject', jsonb_build_object(
                                        'stage', 'worker_lost',
                                        'job_id', id,
                                        'worker_id', worker_id,
                                        'started_at', started_at,
                                        'timeout_s',
                                            COALESCE(
                                                (options->>'timeout_s')::double precision,
                                                300.0
                                            )
                                    ),
                                    'candidates', jsonb_build_array(
                                        jsonb_build_object(
                                            'id', 'c1',
                                            'action', 'regenerate',
                                            'params', '{}'::jsonb,
                                            'human_summary',
                                                'retry unchanged; this is a service/backend failure, not circuit feedback',
                                            'cost_hint', 'cheap',
                                            'confidence', 0.8,
                                            'source', 'deterministic'
                                        )
                                    ),
                                    'retry_hint',
                                        'Retry once unchanged. If it repeats, report a backend worker-loss issue instead of rewriting the circuit.'
                                )
                            )
                        )
                    ),
                    error = COALESCE(
                        error,
                        'worker lost while job was running; resubmit the design'
                    ),
                    finished_at = NOW()
                WHERE status = 'running'
                  AND started_at IS NOT NULL
                  AND started_at < NOW() - make_interval(
                      secs => GREATEST(
                          COALESCE((options->>'timeout_s')::double precision, 300.0)
                              + $1::double precision,
                          $2::double precision
                      )
                  )
                """,
                float(stale_grace_seconds),
                float(min_stale_seconds),
            )
        return int(result.split()[-1])

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

    async def add_run_feedback(
        self,
        run_id: str,
        *,
        feedback: str,
        artifact: str = "",
        source: str = "human_via_agent",
        structured: dict | None = None,
    ) -> dict:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO run_feedback
                   (run_id, artifact, source, feedback, structured)
                   VALUES ($1, $2, $3, $4, $5)
                   RETURNING id, run_id, artifact, source, feedback, structured, created_at""",
                run_id,
                artifact.strip(),
                source.strip() or "human_via_agent",
                feedback.strip(),
                json.dumps(structured or {}),
            )
        return _feedback_row_to_dict(row)

    async def list_run_feedback(self, run_id: str, limit: int = 20) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, run_id, artifact, source, feedback, structured, created_at
                   FROM run_feedback
                   WHERE run_id = $1
                   ORDER BY created_at DESC
                   LIMIT $2""",
                run_id,
                limit,
            )
        return [_feedback_row_to_dict(row) for row in rows]

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

    async def append_estimate(self, board_id: str, estimate: dict) -> None:
        async with self.pool.acquire() as conn:
            await conn.execute(
                """INSERT INTO telemetry
                   (board_id, mode, status, record)
                   VALUES ($1, 'estimate', $2, $3)""",
                board_id,
                "has_issues" if estimate.get("spec_issues") else "clean",
                json.dumps(estimate),
            )

    async def recent_estimates(self, limit: int = 50) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT board_id, status, record, created_at
                   FROM telemetry
                   WHERE mode = 'estimate'
                   ORDER BY created_at DESC
                   LIMIT $1""",
                limit,
            )
        return [
            {
                "board_id": r["board_id"],
                "status": r["status"],
                "spec_issues": json.loads(r["record"]).get("spec_issues", []),
                "warnings": json.loads(r["record"]).get("warnings", []),
                "tier": json.loads(r["record"]).get("complexity_tier"),
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            }
            for r in rows
        ]

    # ── Beta signups ─────────────────────────────────────────────────

    async def create_beta_signup(
        self,
        *,
        email: str,
        name: str = "",
        organization: str = "",
        use_case: str = "",
        source: str = "",
        metadata: dict | None = None,
    ) -> dict:
        email_normalized = email.strip().lower()
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO beta_signups
                   (email, email_normalized, name, organization, use_case, source, metadata)
                   VALUES ($1, $2, $3, $4, $5, $6, $7)
                   ON CONFLICT (email_normalized) DO UPDATE
                   SET email = EXCLUDED.email,
                       name = EXCLUDED.name,
                       organization = EXCLUDED.organization,
                       use_case = EXCLUDED.use_case,
                       source = EXCLUDED.source,
                       metadata = beta_signups.metadata || EXCLUDED.metadata,
                       updated_at = NOW()
                   RETURNING id, email, name, organization, use_case, source, status,
                             created_at, updated_at, (xmax = 0) AS created""",
                email.strip(),
                email_normalized,
                name.strip(),
                organization.strip(),
                use_case.strip(),
                source.strip(),
                json.dumps(metadata or {}),
            )
        return _beta_signup_row_to_dict(row)

    async def list_beta_signups(self, limit: int = 100) -> list[dict]:
        async with self.pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, email, name, organization, use_case, source, status,
                          created_at, updated_at, false AS created
                   FROM beta_signups
                   ORDER BY created_at DESC
                   LIMIT $1""",
                limit,
            )
        return [_beta_signup_row_to_dict(row) for row in rows]

    async def approve_beta_signup(
        self,
        signup_id: int,
        *,
        token_prefix: str,
        token_hash: str,
        key_name: str = "default",
    ) -> dict:
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                signup = await conn.fetchrow(
                    """SELECT id, email, email_normalized, name, organization,
                              use_case, source, status, created_at, updated_at,
                              false AS created
                       FROM beta_signups
                       WHERE id = $1
                       FOR UPDATE""",
                    signup_id,
                )
                if signup is None:
                    raise KeyError(f"beta signup {signup_id!r} not found")
                if signup["status"] != "pending":
                    raise ValueError(f"beta signup {signup_id!r} is {signup['status']}")

                user = await conn.fetchrow(
                    """INSERT INTO users (email, email_normalized, name, organization)
                       VALUES ($1, $2, $3, $4)
                       ON CONFLICT (email_normalized) DO UPDATE
                       SET email = EXCLUDED.email,
                           name = EXCLUDED.name,
                           organization = EXCLUDED.organization,
                           status = 'active',
                           updated_at = NOW()
                       RETURNING id, email, email_normalized, name, organization,
                                 status, created_at, updated_at""",
                    signup["email"],
                    signup["email_normalized"],
                    signup["name"] or "",
                    signup["organization"] or "",
                )
                key = await conn.fetchrow(
                    """INSERT INTO api_keys
                       (user_id, name, token_prefix, token_hash)
                       VALUES ($1, $2, $3, $4)
                       RETURNING id, user_id, name, token_prefix, status, created_at,
                                 last_used_at""",
                    user["id"],
                    key_name,
                    token_prefix,
                    token_hash,
                )
                updated_signup = await conn.fetchrow(
                    """UPDATE beta_signups
                       SET status = 'approved', updated_at = NOW()
                       WHERE id = $1
                       RETURNING id, email, name, organization, use_case, source,
                                 status, created_at, updated_at, false AS created""",
                    signup_id,
                )

        return {
            "signup": _beta_signup_row_to_dict(updated_signup),
            "user": _user_row_to_dict(user),
            "api_key": _api_key_row_to_dict(key),
        }

    async def authenticate_api_key(self, token_hash: str) -> dict | None:
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE api_keys
                   SET last_used_at = NOW()
                   WHERE token_hash = $1 AND status = 'active'
                   RETURNING id, user_id, name, token_prefix, status, created_at,
                             last_used_at""",
                token_hash,
            )
            if row is None:
                return None
            user = await conn.fetchrow(
                """SELECT id, email, email_normalized, name, organization, status,
                          created_at, updated_at
                   FROM users
                   WHERE id = $1 AND status = 'active'""",
                row["user_id"],
            )
        if user is None:
            return None
        return {
            "api_key": _api_key_row_to_dict(row),
            "user": _user_row_to_dict(user),
        }

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


def _beta_signup_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "name": row["name"] or "",
        "organization": row["organization"] or "",
        "use_case": row["use_case"] or "",
        "source": row["source"] or "",
        "status": row["status"],
        "created": bool(row["created"]),
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _feedback_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "artifact": row["artifact"] or "",
        "source": row["source"],
        "feedback": row["feedback"],
        "structured": json.loads(row["structured"]) if row["structured"] else {},
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }


def _user_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "email": row["email"],
        "email_normalized": row["email_normalized"],
        "name": row["name"] or "",
        "organization": row["organization"] or "",
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
    }


def _api_key_row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    return {
        "id": row["id"],
        "user_id": row["user_id"],
        "name": row["name"],
        "token_prefix": row["token_prefix"],
        "status": row["status"],
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
        "last_used_at": row["last_used_at"].isoformat() if row["last_used_at"] else None,
    }
