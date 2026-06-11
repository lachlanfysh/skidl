"""Tests for Railway deployment: db, worker execution, HTTP server, auth, job lifecycle."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from mcp_server.db import DB
from mcp_server.worker import _execute_job, _find_artifacts, worker_loop

DATABASE_URL = os.environ.get("TEST_DATABASE_URL")
needs_postgres = pytest.mark.skipif(
    not DATABASE_URL, reason="TEST_DATABASE_URL not set"
)
needs_kicad = pytest.mark.skipif(
    not os.path.isdir("/usr/share/kicad/symbols"),
    reason="KiCad symbol libraries not installed",
)


SIMPLE_SPEC = {
    "board": {"name": "test-board"},
    "parts": [
        {
            "ref": "R1",
            "lib": "Device",
            "part": "R",
            "value": "10K",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
        }
    ],
    "nets": [],
}

MULTI_PART_SPEC = {
    "board": {"name": "test-multi"},
    "parts": [
        {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115IDGS",
         "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm"},
        {"ref": "C1", "lib": "Device", "part": "C", "value": "100nF",
         "footprint": "Capacitor_SMD:C_0603_1608Metric"},
    ],
    "nets": [
        {"name": "VCC", "power": True, "pins": ["U1.VDD", "C1.1"]},
        {"name": "GND", "power": True, "pins": ["U1.GND", "C1.2"]},
    ],
}

BAD_SPEC = {
    "board": {"name": "test-bad"},
    "parts": [
        {"ref": "U1", "lib": "NoSuchLib", "part": "FakeChip",
         "footprint": "Package_DIP:DIP-8_W7.62mm"},
    ],
    "nets": [],
}


# ── DB layer ──────────────────────────────────────────────────────────


@needs_postgres
class TestDB:
    @pytest_asyncio.fixture
    async def db(self):
        d = DB()
        await d.connect(DATABASE_URL)
        yield d
        async with d.pool.acquire() as conn:
            await conn.execute("DELETE FROM telemetry")
            await conn.execute("DELETE FROM runs")
            await conn.execute("DELETE FROM jobs")
        await d.close()

    @pytest.mark.asyncio
    async def test_create_and_get_job(self, db):
        job_id = await db.create_job(SIMPLE_SPEC, {"timeout_s": 60})
        job = await db.get_job(job_id)
        assert job["status"] == "queued"
        assert job["spec"]["board"]["name"] == "test-board"
        assert job["options"]["timeout_s"] == 60

    @pytest.mark.asyncio
    async def test_claim_job(self, db):
        job_id = await db.create_job(SIMPLE_SPEC)
        claimed = await db.claim_job("test-worker")
        assert claimed is not None
        assert claimed["id"] == job_id
        job = await db.get_job(job_id)
        assert job["status"] == "running"
        assert job["started_at"] is not None
        assert job["worker_id"] == "test-worker"

    @pytest.mark.asyncio
    async def test_claim_skip_locked(self, db):
        """Two concurrent workers must get different jobs."""
        j1 = await db.create_job(SIMPLE_SPEC)
        j2 = await db.create_job(SIMPLE_SPEC)
        c1 = await db.claim_job("w1")
        c2 = await db.claim_job("w2")
        assert c1["id"] != c2["id"]
        assert {c1["id"], c2["id"]} == {j1, j2}

    @pytest.mark.asyncio
    async def test_claim_fifo_order(self, db):
        """Jobs claimed in creation order."""
        j1 = await db.create_job(SIMPLE_SPEC)
        j2 = await db.create_job(SIMPLE_SPEC)
        c = await db.claim_job("w")
        assert c["id"] == j1

    @pytest.mark.asyncio
    async def test_running_job_not_reclaimable(self, db):
        """A running job is skipped by the next claim."""
        j1 = await db.create_job(SIMPLE_SPEC)
        await db.claim_job("w1")
        c2 = await db.claim_job("w2")
        assert c2 is None

    @pytest.mark.asyncio
    async def test_complete_job(self, db):
        job_id = await db.create_job(SIMPLE_SPEC)
        await db.claim_job("test-worker")
        await db.complete_job(job_id, "succeeded", result={"ok": True, "run_id": "r1"})
        job = await db.get_job(job_id)
        assert job["status"] == "succeeded"
        assert job["result"]["ok"] is True
        assert job["finished_at"] is not None

    @pytest.mark.asyncio
    async def test_complete_job_with_error(self, db):
        job_id = await db.create_job(SIMPLE_SPEC)
        await db.claim_job("w")
        await db.complete_job(job_id, "failed", error="engine crashed: SIGSEGV")
        job = await db.get_job(job_id)
        assert job["status"] == "failed"
        assert "SIGSEGV" in job["error"]

    @pytest.mark.asyncio
    async def test_no_jobs_returns_none(self, db):
        claimed = await db.claim_job("test-worker")
        assert claimed is None

    @pytest.mark.asyncio
    async def test_get_missing_job_raises(self, db):
        with pytest.raises(KeyError):
            await db.get_job("nonexistent")

    @pytest.mark.asyncio
    async def test_parent_job_id(self, db):
        j1 = await db.create_job(SIMPLE_SPEC)
        j2 = await db.create_job(SIMPLE_SPEC, parent_job_id=j1)
        job = await db.get_job(j2)
        assert job["parent_job_id"] == j1

    @pytest.mark.asyncio
    async def test_save_and_load_run(self, db):
        await db.save_run(
            "test-run-001", None, SIMPLE_SPEC,
            [{"id": "e1", "code": "SPEC_BAD_FOOTPRINT", "message": "bad"}],
            {"ok": False, "status": "failed"},
            artifacts={"board.kicad_pcb": "(kicad_pcb ...)"},
        )
        run = await db.load_run("test-run-001")
        assert run["run_id"] == "test-run-001"
        assert run["spec"]["board"]["name"] == "test-board"
        assert len(run["exceptions"]) == 1
        assert run["artifacts"]["board.kicad_pcb"].startswith("(kicad_pcb")

    @pytest.mark.asyncio
    async def test_save_run_upsert(self, db):
        """save_run with same run_id updates rather than duplicates."""
        await db.save_run("test-upsert", None, SIMPLE_SPEC, [], {"ok": True}, {})
        await db.save_run("test-upsert", None, SIMPLE_SPEC, [{"id": "e1"}], {"ok": False}, {})
        run = await db.load_run("test-upsert")
        assert len(run["exceptions"]) == 1

    @pytest.mark.asyncio
    async def test_run_linked_to_job(self, db):
        job_id = await db.create_job(SIMPLE_SPEC)
        await db.save_run("test-linked", job_id, SIMPLE_SPEC, [], {"ok": True})
        run = await db.load_run("test-linked")
        assert run["job_id"] == job_id

    @pytest.mark.asyncio
    async def test_load_missing_run_raises(self, db):
        with pytest.raises(KeyError):
            await db.load_run("nonexistent-run-id")

    @pytest.mark.asyncio
    async def test_append_telemetry(self, db):
        await db.append_telemetry({
            "run_id": "test-tel-001",
            "board_id": "test-board",
            "mode": "engine_only",
            "status": "succeeded",
            "cpu_time_s": 5.2,
            "peak_rss_mb": 280.0,
            "geometry": {"component_count": 5},
        })
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM telemetry WHERE run_id = 'test-tel-001'"
            )
        assert row is not None
        assert float(row["cpu_time_s"]) == pytest.approx(5.2)
        geo = json.loads(row["geometry"])
        assert geo["component_count"] == 5

    @pytest.mark.asyncio
    async def test_count_pending(self, db):
        before = await db.count_pending()
        j1 = await db.create_job(SIMPLE_SPEC)
        assert await db.count_pending() == before + 1
        await db.claim_job("w")
        assert await db.count_pending() == before + 1  # running counts as pending
        await db.complete_job(j1, "succeeded")
        assert await db.count_pending() == before  # completed doesn't

    @pytest.mark.asyncio
    async def test_expire_old_jobs(self, db):
        job_id = await db.create_job(SIMPLE_SPEC)
        await db.claim_job("w")
        await db.complete_job(job_id, "succeeded")
        expired = await db.expire_old_jobs(hours=48)
        assert expired == 0
        # Force-age the job and re-expire
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET finished_at = NOW() - INTERVAL '72 hours' WHERE id = $1",
                job_id,
            )
        expired = await db.expire_old_jobs(hours=48)
        assert expired == 1


# ── Worker execution ──────────────────────────────────────────────────


@needs_kicad
class TestWorkerExecution:
    """Test _execute_job against the real engine."""

    def test_simple_spec_succeeds(self):
        """A single resistor should translate + generate schematic + layout."""
        job = {"spec": SIMPLE_SPEC, "options": {"timeout_s": 120}, "policy": {}}
        result = _execute_job(job)
        assert result["run_id"]
        assert result["status"] in ("succeeded", "succeeded_with_warnings")
        assert result["ok"] or result["status"] == "succeeded_with_warnings"

    def test_multi_part_spec(self):
        """ADC + decap with nets — exercises pin resolution + layout."""
        job = {"spec": MULTI_PART_SPEC, "options": {"timeout_s": 120}, "policy": {}}
        result = _execute_job(job)
        assert result["run_id"]
        # May have warnings (congestion, etc.) but should not crash
        assert result["status"] != "crashed"

    def test_bad_spec_produces_exceptions(self):
        """Unknown lib should fail with SPEC_UNKNOWN_LIB, not crash."""
        job = {"spec": BAD_SPEC, "options": {"timeout_s": 60}, "policy": {}}
        result = _execute_job(job)
        assert not result["ok"]
        exc_codes = [e["code"] for e in result.get("exceptions", [])]
        assert any("UNKNOWN" in c for c in exc_codes)

    def test_timeout_handled(self):
        """An impossibly short timeout should return timeout status, not crash."""
        job = {"spec": MULTI_PART_SPEC, "options": {"timeout_s": 0.1}, "policy": {}}
        result = _execute_job(job)
        assert result["status"] == "timeout"
        assert not result["ok"]

    def test_artifacts_collected(self):
        """Verify .kicad_pcb and .kicad_sch are captured before tmpdir cleanup."""
        job = {"spec": SIMPLE_SPEC, "options": {"timeout_s": 120}, "policy": {}}
        result = _execute_job(job)
        artifacts = result.get("_artifact_paths", {})
        if result["ok"]:
            assert any(k.endswith(".kicad_pcb") for k in artifacts), \
                f"No .kicad_pcb in artifacts: {list(artifacts.keys())}"
            assert any(k.endswith(".kicad_sch") for k in artifacts), \
                f"No .kicad_sch in artifacts: {list(artifacts.keys())}"
            for name, content in artifacts.items():
                assert len(content) > 100, f"{name} suspiciously small"

    def test_spec_included_in_result(self):
        """Result should carry the (possibly corrected) spec for Postgres storage."""
        job = {"spec": SIMPLE_SPEC, "options": {"timeout_s": 120}, "policy": {}}
        result = _execute_job(job)
        assert "spec" in result
        assert result["spec"]["board"]["name"] == "test-board"

    def test_options_passthrough(self):
        """Board ID and other options should flow through to the pipeline."""
        job = {
            "spec": SIMPLE_SPEC,
            "options": {"timeout_s": 120, "board_id": "custom-board-id"},
            "policy": {},
        }
        result = _execute_job(job)
        assert result["run_id"]


class TestFindArtifacts:
    """Test artifact collection from tmpdir."""

    def test_collects_kicad_files(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "board.kicad_pcb").write_text("(kicad_pcb content)")
            (p / "board.kicad_sch").write_text("(kicad_sch content)")
            (p / "engine_worker.log").write_text("log stuff")
            arts = _find_artifacts(p)
            assert "board.kicad_pcb" in arts
            assert "board.kicad_sch" in arts
            assert "engine_worker.log" not in arts

    def test_collects_nested(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            sub = p / "subdir"
            sub.mkdir()
            (sub / "deep.kicad_pcb").write_text("nested pcb")
            arts = _find_artifacts(p)
            assert "deep.kicad_pcb" in arts

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            arts = _find_artifacts(Path(td))
            assert arts == {}


# ── Worker loop integration ───────────────────────────────────────────


@needs_postgres
@needs_kicad
class TestWorkerLoop:
    """Test the full worker_loop claim→execute→store cycle against Postgres."""

    @pytest_asyncio.fixture
    async def db(self):
        d = DB()
        await d.connect(DATABASE_URL)
        yield d
        async with d.pool.acquire() as conn:
            await conn.execute("DELETE FROM telemetry")
            await conn.execute("DELETE FROM runs")
            await conn.execute("DELETE FROM jobs")
        await d.close()

    @pytest.mark.asyncio
    async def test_full_job_lifecycle(self, db):
        """Submit a job, let the worker pick it up, verify result in Postgres."""
        job_id = await db.create_job(SIMPLE_SPEC, {"timeout_s": 120})

        # Run one iteration of the worker loop (not the infinite loop)
        claimed = await db.claim_job("test-lifecycle")
        assert claimed is not None

        from mcp_server.worker import _execute_job, _collect_artifacts
        result = await asyncio.to_thread(_execute_job, claimed)
        status = "succeeded" if result["ok"] else "failed"
        if result.get("status") == "timeout":
            status = "timeout"
        await db.complete_job(job_id, status, result=result)

        if result.get("run_id"):
            artifacts = _collect_artifacts(result)
            await db.save_run(
                result["run_id"], job_id,
                result.get("spec", claimed["spec"]),
                result.get("exceptions", []),
                result,
                artifacts=artifacts,
            )

        # Verify job completed
        job = await db.get_job(job_id)
        assert job["status"] in ("succeeded", "failed", "timeout")
        assert job["finished_at"] is not None

        # Verify run stored
        if result.get("run_id"):
            run = await db.load_run(result["run_id"])
            assert run["job_id"] == job_id
            assert run["spec"]["board"]["name"] == "test-board"

    @pytest.mark.asyncio
    async def test_concurrent_workers_claim_different_jobs(self, db):
        """Two workers running concurrently should each get a unique job."""
        j1 = await db.create_job(SIMPLE_SPEC, {"timeout_s": 60})
        j2 = await db.create_job(SIMPLE_SPEC, {"timeout_s": 60})

        c1 = await db.claim_job("worker-0")
        c2 = await db.claim_job("worker-1")
        assert c1 is not None and c2 is not None
        assert c1["id"] != c2["id"]
        assert {c1["id"], c2["id"]} == {j1, j2}

    @pytest.mark.asyncio
    async def test_crashed_job_records_error(self, db):
        """If _execute_job raises, the worker loop should store the error."""
        # Use a spec that will cause _execute_job to fail during validation
        bad_spec = {"board": {"name": "x"}, "parts": "not-a-list", "nets": []}
        job_id = await db.create_job(bad_spec, {"timeout_s": 30})
        claimed = await db.claim_job("w")
        try:
            await asyncio.to_thread(_execute_job, claimed)
        except Exception as exc:
            await db.complete_job(job_id, "failed", error=str(exc))

        job = await db.get_job(job_id)
        assert job["status"] == "failed"
        assert job["error"]


# ── HTTP server + auth ────────────────────────────────────────────────


class TestAuthMiddleware:
    """Test bearer token auth without Postgres."""

    @pytest.fixture(scope="class")
    def client(self):
        from starlette.testclient import TestClient
        os.environ["EDA_AUTH_TOKEN"] = "test-token-123"
        # Re-read the env var since it's captured at module load
        import mcp_server.serve_http as mod
        mod.EDA_AUTH_TOKEN = "test-token-123"
        app = mod.create_app()
        # Context manager runs the lifespan (session manager + db wiring)
        with TestClient(app) as client:
            yield client

    def test_health_no_auth_required(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["auth_configured"] is True
        assert "status" in data

    def test_health_reports_db_status(self, client):
        """Health should report db: false when no DATABASE_URL."""
        resp = client.get("/health")
        data = resp.json()
        assert data["db"] is False
        assert data["status"] == "degraded"

    def test_401_without_token(self, client):
        resp = client.get("/mcp")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Invalid token"

    def test_401_wrong_token(self, client):
        resp = client.get("/mcp", headers={"Authorization": "Bearer wrong-token"})
        assert resp.status_code == 401

    def test_auth_passes_with_correct_token(self, client):
        resp = client.get("/mcp", headers={"Authorization": "Bearer test-token-123"})
        assert resp.status_code != 401

    def test_no_oauth_discovery_headers(self, client):
        """Server must NOT return WWW-Authenticate with OAuth metadata."""
        resp = client.get("/mcp")
        assert "www-authenticate" not in resp.headers
        body = json.dumps(resp.json()).lower()
        assert "oauth" not in body
        assert "openid" not in body

    def test_no_wellknown_oauth_endpoint(self, client):
        """No OAuth protected resource metadata endpoint should exist."""
        resp = client.get("/.well-known/oauth-protected-resource",
                         headers={"Authorization": "Bearer test-token-123"})
        assert resp.status_code == 404

    def test_500_when_token_not_configured(self):
        from starlette.testclient import TestClient
        import mcp_server.serve_http as mod
        mod.EDA_AUTH_TOKEN = ""
        app = mod.create_app()
        client = TestClient(app)
        resp = client.get("/mcp")
        assert resp.status_code == 500
        assert "not configured" in resp.json()["error"]
        mod.EDA_AUTH_TOKEN = "test-token-123"


@needs_postgres
class TestHTTPToolsIntegration:
    """Test MCP tools via the HTTP server against real Postgres."""

    @pytest_asyncio.fixture
    async def db(self):
        d = DB()
        await d.connect(DATABASE_URL)
        # Wire up the server_http module's db instance
        import mcp_server.server_http as smod
        smod.db.pool = d.pool
        yield d
        async with d.pool.acquire() as conn:
            await conn.execute("DELETE FROM telemetry")
            await conn.execute("DELETE FROM runs")
            await conn.execute("DELETE FROM jobs")
        smod.db.pool = None
        await d.close()

    @pytest.mark.asyncio
    async def test_submit_design_creates_job(self, db):
        from mcp_server.server_http import submit_design
        result = await submit_design(SIMPLE_SPEC)
        assert result["status"] == "queued"
        assert result["job_id"]
        job = await db.get_job(result["job_id"])
        assert job["status"] == "queued"

    @pytest.mark.asyncio
    async def test_submit_with_options(self, db):
        from mcp_server.server_http import submit_design
        result = await submit_design(
            SIMPLE_SPEC,
            run_options={"timeout_s": 600, "board_id": "my-board"},
        )
        job = await db.get_job(result["job_id"])
        assert job["options"]["timeout_s"] == 600
        assert job["options"]["board_id"] == "my-board"

    @pytest.mark.asyncio
    async def test_get_job_returns_status(self, db):
        from mcp_server.server_http import submit_design, get_job
        sub = await submit_design(SIMPLE_SPEC)
        job = await get_job(sub["job_id"])
        assert job["status"] == "queued"

    @pytest.mark.asyncio
    async def test_get_job_missing_raises(self, db):
        from mcp_server.server_http import get_job
        with pytest.raises(KeyError):
            await get_job("nonexistent")

    @pytest.mark.asyncio
    async def test_estimate_complexity_sync(self, db):
        from mcp_server.server_http import estimate_complexity
        result = await estimate_complexity(SIMPLE_SPEC)
        assert "complexity_tier" in result
        assert result["complexity_tier"] == "simple"

    @pytest.mark.asyncio
    async def test_get_run_after_save(self, db):
        from mcp_server.server_http import get_run
        await db.save_run("http-test-run", None, SIMPLE_SPEC, [], {"ok": True})
        run = await get_run("http-test-run")
        assert run["run_id"] == "http-test-run"

    @pytest.mark.asyncio
    async def test_apply_correction_creates_child_job(self, db):
        from mcp_server.server_http import apply_correction
        from schemas.exceptions import ActionType, ExcCode, Severity

        # Seed a run with a fixable exception
        exc = {
            "id": "e1",
            "code": ExcCode.SPEC_BAD_FOOTPRINT.value,
            "severity": Severity.ERROR.value,
            "message": "bad footprint",
            "subject": {"ref": "R1"},
            "candidates": [
                {
                    "id": "c1",
                    "action": ActionType.REPLACE_FOOTPRINT.value,
                    "params": {"old": "Resistor_SMD:R_0603_1608Metric", "new": "Resistor_SMD:R_0805_2012Metric"},
                    "confidence": 0.9,
                    "human_summary": "Use 0805 instead",
                },
            ],
        }
        parent_job_id = await db.create_job(SIMPLE_SPEC, {})
        await db.save_run(
            "correction-test", parent_job_id,
            SIMPLE_SPEC, [exc],
            {"ok": False, "run_id": "correction-test"},
        )

        result = await apply_correction("correction-test", [
            {"exception_id": "e1", "candidate_id": "c1"},
        ])
        assert result["status"] == "queued"
        assert result["job_id"]
        assert result["parent_run_id"] == "correction-test"

        child_job = await db.get_job(result["job_id"])
        assert child_job["status"] == "queued"
        corrected_spec = child_job["spec"]
        r1 = next(p for p in corrected_spec["parts"] if p["ref"] == "R1")
        assert r1["footprint"] == "Resistor_SMD:R_0805_2012Metric"

    @pytest.mark.asyncio
    async def test_apply_correction_bad_exception_id(self, db):
        await db.save_run("corr-bad-exc", None, SIMPLE_SPEC, [], {"ok": True})
        from mcp_server.server_http import apply_correction
        with pytest.raises(ValueError, match="unknown exception_id"):
            await apply_correction("corr-bad-exc", [
                {"exception_id": "bogus", "candidate_id": "c1"},
            ])

    @pytest.mark.asyncio
    async def test_submit_bad_spec_validates(self, db):
        from mcp_server.server_http import submit_design
        with pytest.raises(Exception):
            await submit_design({"not": "a valid spec"})


class TestWorkerHealth:
    """The worker must answer Railway's /health probe (it has no MCP surface)."""

    def test_health_endpoint_no_db(self):
        from starlette.testclient import TestClient
        from mcp_server.worker import health_app
        app = health_app(DB(), "w-test", 2)
        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            data = resp.json()
            assert data["worker_id"] == "w-test"
            assert data["concurrency"] == 2
            assert data["db"] is False
            assert data["status"] == "degraded"

    @needs_postgres
    @pytest.mark.asyncio
    async def test_health_endpoint_with_db(self):
        import httpx
        from mcp_server.worker import health_app
        database = DB()
        await database.connect(DATABASE_URL)
        try:
            app = health_app(database, "w-db", 2)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                resp = await client.get("/health")
            data = resp.json()
            assert data["status"] == "ok"
            assert data["db"] is True
            assert isinstance(data["pending_jobs"], int)
        finally:
            await database.close()


# ── Agent UX: tool descriptions and resources ─────────────────────────


class TestAgentUX:
    """The MCP surface must teach an agent the workflow without external docs.

    These run without Postgres or KiCad — they only inspect the MCP
    server's metadata and the guide content.
    """

    def _extract_json_blocks(self, markdown: str) -> list[dict]:
        """Pull every ```json fenced block out of a guide."""
        import re
        blocks = re.findall(r"```json\n(.*?)```", markdown, re.DOTALL)
        parsed = []
        for block in blocks:
            try:
                parsed.append(json.loads(block))
            except json.JSONDecodeError:
                continue  # fragments (e.g. single-part snippets) are fine
        return parsed

    @pytest.mark.asyncio
    async def test_all_tools_listed_with_descriptions(self):
        from mcp_server.server_http import mcp
        tools = await mcp.list_tools()
        names = {t.name for t in tools}
        assert names == {
            "submit_design", "get_job", "estimate_complexity",
            "apply_correction", "get_run",
        }
        for tool in tools:
            assert tool.description and len(tool.description) > 100, (
                f"{tool.name} description too thin for agent use"
            )

    @pytest.mark.asyncio
    async def test_submit_design_teaches_the_loop(self):
        """An agent reading only submit_design must learn: spec shape,
        polling, footprint format, power-net convention, and resources."""
        from mcp_server.server_http import mcp
        tools = {t.name: t for t in await mcp.list_tools()}
        desc = tools["submit_design"].description
        for needle in (
            "get_job", "job_id", "footprint", "Library:Name",
            "power", "REF.PIN", "100nF", "eda://", "timeout_s",
        ):
            assert needle in desc, f"submit_design description missing {needle!r}"

    @pytest.mark.asyncio
    async def test_get_job_documents_statuses(self):
        from mcp_server.server_http import mcp
        tools = {t.name: t for t in await mcp.list_tools()}
        desc = tools["get_job"].description
        for status in ("queued", "running", "succeeded", "failed", "timeout"):
            assert status in desc
        assert "run_id" in desc and "exceptions" in desc

    @pytest.mark.asyncio
    async def test_apply_correction_documents_selection(self):
        from mcp_server.server_http import mcp
        tools = {t.name: t for t in await mcp.list_tools()}
        desc = tools["apply_correction"].description
        assert "exception_id" in desc and "candidate_id" in desc
        assert "confidence" in desc

    @pytest.mark.asyncio
    async def test_resources_listed(self):
        from mcp_server.server_http import mcp
        resources = await mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert uris == {
            "eda://schema/circuit-spec",
            "eda://guide/circuit-spec",
            "eda://guide/workflow",
            "eda://guide/exceptions",
        }
        for r in resources:
            assert r.description, f"{r.uri} has no description"

    @pytest.mark.asyncio
    async def test_schema_resource_is_live_model_schema(self):
        from mcp_server.server_http import mcp
        from schemas.circuit_spec import CircuitSpec
        content = await mcp.read_resource("eda://schema/circuit-spec")
        schema = json.loads(list(content)[0].content)
        assert schema == CircuitSpec.model_json_schema()

    @pytest.mark.asyncio
    async def test_guide_examples_validate_against_schema(self):
        """Every complete spec example embedded in the guides must be a
        valid CircuitSpec — otherwise the docs teach agents broken input."""
        from mcp_server.server_http import CIRCUIT_SPEC_GUIDE
        from schemas.circuit_spec import CircuitSpec
        specs = [
            b for b in self._extract_json_blocks(CIRCUIT_SPEC_GUIDE)
            if isinstance(b, dict) and "board" in b and "parts" in b
        ]
        assert specs, "guide contains no complete spec example"
        for spec in specs:
            CircuitSpec.model_validate(spec)

    def test_submit_design_inline_example_validates(self):
        """The JSON example inside the submit_design docstring must parse
        and validate — agents copy it verbatim."""
        import re
        from mcp_server import server_http
        from schemas.circuit_spec import CircuitSpec
        doc = server_http.submit_design.__doc__
        m = re.search(r"^    (\{\n.*?\n    \})$", doc, re.DOTALL | re.MULTILINE)
        assert m, "no JSON example found in submit_design docstring"
        block = "\n".join(line[4:] if line.startswith("    ") else line
                          for line in m.group(1).split("\n"))
        spec = json.loads(block)
        CircuitSpec.model_validate(spec)

    @needs_kicad
    def test_guide_example_parts_exist_in_kicad_libs(self):
        """Symbols and footprints named in the worked example must exist in
        the KiCad libraries the worker image ships."""
        from mcp_server.server_http import CIRCUIT_SPEC_GUIDE
        specs = [
            b for b in self._extract_json_blocks(CIRCUIT_SPEC_GUIDE)
            if isinstance(b, dict) and "board" in b and "parts" in b
        ]
        fp_root = Path("/usr/share/kicad/footprints")
        sym_root = Path("/usr/share/kicad/symbols")
        for spec in specs:
            for part in spec["parts"]:
                if part.get("lib"):
                    sym_file = sym_root / f"{part['lib']}.kicad_sym"
                    assert sym_file.exists(), f"missing symbol lib {part['lib']}"
                    assert f'"{part["part"]}"' in sym_file.read_text(), (
                        f"symbol {part['part']} not in {part['lib']}"
                    )
                lib, name = part["footprint"].split(":")
                fp_file = fp_root / f"{lib}.pretty" / f"{name}.kicad_mod"
                assert fp_file.exists(), f"missing footprint {part['footprint']}"

    @pytest.mark.asyncio
    async def test_exceptions_guide_covers_all_codes_and_actions(self):
        """The reference must stay complete as the enums grow."""
        from mcp_server.server_http import EXCEPTIONS_GUIDE
        from schemas.exceptions import ActionType, ExcCode
        for code in ExcCode:
            assert f"`{code.value}`" in EXCEPTIONS_GUIDE, f"{code.value} undocumented"
        for action in ActionType:
            assert f"`{action.value}`" in EXCEPTIONS_GUIDE, f"{action.value} undocumented"

    @pytest.mark.asyncio
    async def test_server_instructions_point_to_resources(self):
        from mcp_server.server_http import mcp
        instructions = mcp.instructions
        assert instructions
        assert "submit_design" in instructions
        assert "eda://" in instructions
