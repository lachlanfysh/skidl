"""Tests for Railway deployment: db, worker execution, HTTP server, auth, job lifecycle."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import tempfile
import zipfile
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import pytest_asyncio

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from mcp_server.db import DB
from mcp_server.pipeline import DesignResponse
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
    "board": {"name": "test-board", "outline_hint_mm": [30.0, 20.0]},
    "parts": [
        {
            "ref": "R1",
            "lib": None,
            "part": "R",
            "value": "10K",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "pins": [
                {"num": "1", "name": "A"},
                {"num": "2", "name": "B"},
            ],
        },
        {
            "ref": "R2",
            "lib": None,
            "part": "R",
            "value": "10K",
            "footprint": "Resistor_SMD:R_0603_1608Metric",
            "pins": [
                {"num": "1", "name": "A"},
                {"num": "2", "name": "B"},
            ],
        },
        {
            "ref": "J1",
            "lib": "Connector_Generic",
            "part": "Conn_01x03",
            "value": "IO",
            "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
        },
    ],
    "nets": [
        {"name": "VCC", "power": True, "pins": ["J1.1", "R1.A"]},
        {"name": "SIG", "pins": ["J1.2", "R1.B", "R2.A"]},
        {"name": "GND", "power": True, "pins": ["J1.3", "R2.B"]},
    ],
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
    async def test_run_feedback_round_trip(self, db):
        await db.save_run(
            "test-feedback-run",
            None,
            SIMPLE_SPEC,
            [],
            {"ok": True, "run_id": "test-feedback-run"},
            artifacts={"preview_2d_top.png": "base64-png"},
        )

        entry = await db.add_run_feedback(
            "test-feedback-run",
            artifact="preview_2d_top.png",
            feedback="Move the header to the bottom edge.",
            structured={"labels": ["placement"], "artifact_exists": True},
        )
        feedback = await db.list_run_feedback("test-feedback-run")

        assert entry["id"]
        assert feedback[0]["feedback"] == "Move the header to the bottom edge."
        assert feedback[0]["artifact"] == "preview_2d_top.png"
        assert feedback[0]["structured"]["labels"] == ["placement"]

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
    async def test_fail_stale_running_jobs(self, db):
        job_id = await db.create_job(SIMPLE_SPEC, {"timeout_s": 1})
        await db.claim_job("dead-worker")
        async with db.pool.acquire() as conn:
            await conn.execute(
                "UPDATE jobs SET started_at = NOW() - INTERVAL '10 minutes' WHERE id = $1",
                job_id,
            )

        counts = await db.job_status_counts(
            stale_grace_seconds=0,
            min_stale_seconds=30,
        )
        assert counts["stale_running"] >= 1

        failed = await db.fail_stale_running_jobs(
            stale_grace_seconds=0,
            min_stale_seconds=30,
        )

        assert failed == 1
        job = await db.get_job(job_id)
        assert job["status"] == "failed"
        assert "worker lost" in job["error"]

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


class TestWorkerOptionPassthrough:
    def test_circuit_spec_route_timeout_reaches_pipeline(self, monkeypatch):
        seen = {}

        def fake_run_pipeline(spec, out_dir, **kwargs):
            seen.update(kwargs)
            return DesignResponse(
                run_id="fake-run",
                ok=True,
                status="succeeded",
                stage="complete",
                metrics={
                    "manufacturable": True,
                    "manufacturing_complete": True,
                },
            )

        monkeypatch.setattr("mcp_server.worker.run_pipeline", fake_run_pipeline)

        job = {
            "spec": SIMPLE_SPEC,
            "options": {"timeout_s": 600, "route_timeout_s": 420},
            "policy": {},
        }
        result = _execute_job(job)

        assert result["run_id"] == "fake-run"
        assert seen["timeout_s"] == 600
        assert seen["route_timeout_s"] == 420

    def test_skidl_code_route_timeout_reaches_pipeline(self, monkeypatch):
        seen = {}

        def fake_run_pipeline_code(**kwargs):
            seen.update(kwargs)
            return DesignResponse(
                run_id="fake-code-run",
                ok=True,
                status="succeeded",
                stage="complete",
                metrics={
                    "manufacturable": True,
                    "manufacturing_complete": True,
                },
            )

        monkeypatch.setattr("mcp_server.worker.run_pipeline_code", fake_run_pipeline_code)

        job = {
            "spec": {
                "_mode": "skidl_python",
                "code": "from skidl import *",
                "board_name": "code-board",
                "outline_mm": [40.0, 25.0],
                "design_intent": "test board",
            },
            "options": {
                "timeout_s": 600,
                "route_timeout_s": 420,
                "board_id": "route-timeout-test",
                "assembly_policy": "double_sided",
                "pipeline_goal": "placement_review",
            },
            "policy": {},
        }
        result = _execute_job(job)

        assert result["run_id"] == "fake-code-run"
        assert seen["timeout_s"] == 600
        assert seen["route_timeout_s"] == 420
        assert seen["board_id"] == "route-timeout-test"
        assert seen["assembly_policy"] == "double_sided"
        assert seen["pipeline_goal"] == "placement_review"


@needs_kicad
class TestWorkerExecution:
    """Test _execute_job against the real engine."""

    def test_simple_spec_generates_layout_but_requires_router_for_success(self):
        """A tiny board generates artifacts, but success requires full fab gates."""
        job = {"spec": SIMPLE_SPEC, "options": {"timeout_s": 120}, "policy": {}}
        result = _execute_job(job)
        assert result["run_id"]
        assert result["status"] != "crashed"
        artifacts = result.get("_artifact_paths", {})
        assert any(k.endswith(".kicad_pcb") for k in artifacts)
        assert any(k.endswith(".kicad_sch") for k in artifacts)
        if result["ok"]:
            assert result["metrics"]["manufacturable"] is True
        else:
            assert result["metrics"].get("manufacturable") is not True

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

    def test_collects_preview_artifacts(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            png_bytes = b"\x89PNG\r\n\x1a\npreview"
            flat_png_bytes = b"\x89PNG\r\n\x1a\nflat-preview"
            (p / "preview_top.svg").write_text("<svg><text>pcb</text></svg>")
            (p / "preview_assembly.svg").write_text("<svg><text>assembly</text></svg>")
            (p / "preview_top.png").write_bytes(png_bytes)
            (p / "preview_2d_top.png").write_bytes(flat_png_bytes)

            arts = _find_artifacts(p)

            assert arts["preview_top.svg"] == "<svg><text>pcb</text></svg>"
            assert arts["preview_assembly.svg"] == "<svg><text>assembly</text></svg>"
            assert base64.b64decode(arts["preview_top.png"]) == png_bytes
            assert base64.b64decode(arts["preview_2d_top.png"]) == flat_png_bytes

    def test_empty_dir(self):
        with tempfile.TemporaryDirectory() as td:
            arts = _find_artifacts(Path(td))
            assert arts == {}

    def test_manufacturing_artifacts_build_order_zip(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td)
            (p / "board.kicad_pcb").write_text("(kicad_pcb content)")
            (p / "board.kicad_sch").write_text("(kicad_sch content)")
            png_bytes = b"\x89PNG\r\n\x1a\npreview"
            flat_png_bytes = b"\x89PNG\r\n\x1a\nflat-preview"
            (p / "preview_top.svg").write_text("<svg><text>pcb</text></svg>")
            (p / "preview_assembly.svg").write_text("<svg><text>assembly</text></svg>")
            (p / "preview_top.png").write_bytes(png_bytes)
            (p / "preview_2d_top.png").write_bytes(flat_png_bytes)
            (p / "bom.csv").write_text("Comment,Designator,Footprint,LCSC\nR,R1,0603,C1\n")
            (p / "cpl.csv").write_text("Designator,Mid X,Mid Y,Layer,Rotation\nR1,1,1,Top,0\n")
            gerbers = p / "gerbers"
            gerbers.mkdir()
            (gerbers / "board-F_Cu.gbr").write_text("G04 gerber*")
            (gerbers / "board.drl").write_text("M48 drill")

            arts = _find_artifacts(p)

            assert "bom.csv" in arts
            assert "cpl.csv" in arts
            assert "_board.zip" in arts

            with zipfile.ZipFile(BytesIO(base64.b64decode(arts["_board.zip"]))) as zf:
                assert zf.read("preview_top.svg").decode() == "<svg><text>pcb</text></svg>"
                assert zf.read("preview_assembly.svg").decode() == "<svg><text>assembly</text></svg>"
                assert zf.read("preview_top.png") == png_bytes
                assert zf.read("preview_2d_top.png") == flat_png_bytes


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

    @pytest.fixture(autouse=True)
    def reset_auth_test_state(self, client):
        import mcp_server.serve_http as mod

        mod._SIGNUP_ATTEMPTS.clear()
        mod._ADMIN_LOGIN_ATTEMPTS.clear()
        client.cookies.clear()

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

    def test_health_includes_build_metadata(self, client, monkeypatch):
        monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "abc123")
        monkeypatch.setenv("RAILWAY_GIT_BRANCH", "feat/test")
        monkeypatch.setenv("RAILWAY_GIT_REPO_NAME", "eda-mcp")
        monkeypatch.setenv("RAILWAY_GIT_REPO_OWNER", "lachlanfysh")
        monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "dep_123")
        monkeypatch.setenv("RAILWAY_SERVICE_ID", "svc_123")
        monkeypatch.setenv("RAILWAY_SERVICE_NAME", "mcp-server")
        monkeypatch.setenv("RAILWAY_ENVIRONMENT_NAME", "production")

        resp = client.get("/health")
        assert resp.status_code == 200
        build = resp.json()["build"]
        assert build["commit_sha"] == "abc123"
        assert build["branch"] == "feat/test"
        assert build["repo"] == "eda-mcp"
        assert build["repo_owner"] == "lachlanfysh"
        assert build["deployment_id"] == "dep_123"
        assert build["service_id"] == "svc_123"
        assert build["service_name"] == "mcp-server"
        assert build["environment_name"] == "production"

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

    def test_signup_page_is_public(self, client):
        resp = client.get("/signup")
        assert resp.status_code == 200
        assert "eda mcp" in resp.text
        assert "open beta" in resp.text
        assert "request beta access" in resp.text
        assert "KiCad generated PCB render" in resp.text

    def test_signup_submit_validates_email(self, client):
        resp = client.post(
            "/signup",
            data={
                "email": "not-an-email",
                "name": "Ada",
                "use_case": "Trying agentic PCB layout",
            },
        )
        assert resp.status_code == 400
        assert "Enter a valid email address" in resp.text

    def test_signup_submit_requires_storage(self, client):
        resp = client.post(
            "/signup",
            data={
                "email": "ada@example.com",
                "name": "Ada",
                "use_case": "Trying agentic PCB layout",
            },
        )
        assert resp.status_code == 503
        assert "Signup storage is not connected" in resp.text

    def test_signup_api_records_beta_request(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def create_beta_signup(self, **kwargs):
                self.last_signup = kwargs
                return {
                    "id": 1,
                    "email": kwargs["email"],
                    "name": kwargs["name"],
                    "organization": kwargs["organization"],
                    "use_case": kwargs["use_case"],
                    "source": kwargs["source"],
                    "status": "pending",
                    "created": True,
                    "created_at": "2026-06-14T00:00:00+00:00",
                    "updated_at": "2026-06-14T00:00:00+00:00",
                }

        fake = FakeDB()
        monkeypatch.setattr(mod, "db", fake)

        resp = client.post(
            "/api/beta-signup",
            json={
                "email": "Ada@Example.com",
                "name": "Ada",
                "organization": "Analytical Engines",
                "use_case": "Agentic PCB layout for small sensor boards",
                "source": "test",
            },
        )

        assert resp.status_code == 201
        assert resp.json()["ok"] is True
        assert resp.json()["signup"]["email"] == "Ada@Example.com"
        assert fake.last_signup["email"] == "Ada@Example.com"
        assert fake.last_signup["metadata"]["user_agent"]

    def test_signup_honeypot_returns_success_without_storing(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def create_beta_signup(self, **kwargs):
                raise AssertionError("honeypot signup should not be stored")

        monkeypatch.setattr(mod, "db", FakeDB())

        resp = client.post(
            "/api/beta-signup",
            json={
                "email": "bot@example.com",
                "use_case": "bot text",
                "website": "https://spam.example",
            },
        )

        assert resp.status_code == 201
        assert resp.json()["ok"] is True
        assert "bot_filtered" not in resp.text

    def test_signup_rate_limit_is_per_ip(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def create_beta_signup(self, **kwargs):
                return {
                    "id": 1,
                    "email": kwargs["email"],
                    "name": "",
                    "organization": "",
                    "use_case": kwargs["use_case"],
                    "source": "test",
                    "status": "pending",
                    "created": True,
                    "created_at": "2026-06-14T00:00:00+00:00",
                    "updated_at": "2026-06-14T00:00:00+00:00",
                }

        monkeypatch.setattr(mod, "db", FakeDB())
        monkeypatch.setenv("SIGNUP_RATE_LIMIT_PER_HOUR", "1")
        mod._SIGNUP_ATTEMPTS.clear()
        headers = {"X-Forwarded-For": "203.0.113.7"}

        first = client.post(
            "/api/beta-signup",
            json={"email": "one@example.com", "use_case": "first"},
            headers=headers,
        )
        second = client.post(
            "/api/beta-signup",
            json={"email": "two@example.com", "use_case": "second"},
            headers=headers,
        )

        assert first.status_code == 201
        assert second.status_code == 429

    def test_signup_rejects_oversized_body(self, client):
        resp = client.post(
            "/api/beta-signup",
            json={"email": "huge@example.com", "use_case": "x" * 25_000},
        )

        assert resp.status_code == 413

    def test_user_api_key_can_access_mcp_but_not_admin(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def authenticate_api_key(self, token_hash):
                assert token_hash == mod._hash_token("user-token")
                return {
                    "user": {
                        "id": 1,
                        "email": "ada@example.com",
                        "name": "Ada",
                        "organization": "",
                        "status": "active",
                    },
                    "api_key": {
                        "id": 1,
                        "user_id": 1,
                        "name": "open-beta",
                        "token_prefix": "abcd1234",
                        "status": "active",
                    },
                }

        monkeypatch.setattr(mod, "db", FakeDB())

        mcp_resp = client.get("/mcp", headers={"Authorization": "Bearer user-token"})
        assert mcp_resp.status_code != 401
        assert mcp_resp.status_code != 403

        admin_resp = client.get(
            "/beta-signups",
            headers={"Authorization": "Bearer user-token"},
        )
        assert admin_resp.status_code == 403

    def test_beta_signup_list_requires_auth(self, client):
        resp = client.get("/beta-signups")
        assert resp.status_code == 401

    def test_admin_browser_login_sets_cookie_and_opens_admin(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def list_beta_signups(self, limit=100):
                return []

        monkeypatch.setattr(mod, "db", FakeDB())

        unauth = client.get("/admin/beta-signups", follow_redirects=False)
        assert unauth.status_code == 303
        assert unauth.headers["location"] == "/admin/login"

        login = client.post(
            "/admin/login",
            data={"token": "test-token-123"},
            follow_redirects=False,
        )
        assert login.status_code == 303
        assert login.headers["location"] == "/admin/beta-signups"
        assert "HttpOnly" in login.headers["set-cookie"]
        assert "SameSite=strict" in login.headers["set-cookie"]

        admin = client.get("/admin/beta-signups")
        assert admin.status_code == 200
        assert admin.headers["cache-control"] == "no-store"
        assert admin.headers["x-frame-options"] == "DENY"
        assert "No beta requests yet" in admin.text

    def test_admin_login_rate_limit_blocks_repeated_attempts(self, client, monkeypatch):
        monkeypatch.setenv("ADMIN_LOGIN_RATE_LIMIT_PER_HOUR", "1")

        first = client.post("/admin/login", data={"token": "wrong"})
        second = client.post("/admin/login", data={"token": "wrong"})

        assert first.status_code == 401
        assert second.status_code == 429
        assert "Too many login attempts" in second.text

    def test_beta_signup_list_is_protected_by_bearer_token(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def list_beta_signups(self, limit=100):
                return [
                    {
                        "id": 1,
                        "email": "ada@example.com",
                        "name": "Ada",
                        "organization": "",
                        "use_case": "Agentic PCB layout",
                        "source": "test",
                        "status": "pending",
                        "created": False,
                        "created_at": "2026-06-14T00:00:00+00:00",
                        "updated_at": "2026-06-14T00:00:00+00:00",
                    }
                ]

        monkeypatch.setattr(mod, "db", FakeDB())

        resp = client.get(
            "/beta-signups",
            headers={"Authorization": "Bearer test-token-123"},
        )
        assert resp.status_code == 200
        assert resp.json()[0]["email"] == "ada@example.com"

    def test_admin_approve_beta_signup_mints_user_token(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def approve_beta_signup(self, signup_id, *, token_prefix, token_hash, key_name):
                assert signup_id == 7
                assert key_name == "open-beta"
                assert len(token_prefix) == 8
                assert len(token_hash) == 64
                self.token_prefix = token_prefix
                self.token_hash = token_hash
                return {
                    "signup": {
                        "id": 7,
                        "email": "ada@example.com",
                        "name": "Ada",
                        "organization": "Analytical Engines",
                        "use_case": "Agentic PCB layout",
                        "source": "test",
                        "status": "approved",
                        "created": False,
                        "created_at": "2026-06-14T00:00:00+00:00",
                        "updated_at": "2026-06-14T00:00:00+00:00",
                    },
                    "user": {
                        "id": 3,
                        "email": "ada@example.com",
                        "email_normalized": "ada@example.com",
                        "name": "Ada",
                        "organization": "Analytical Engines",
                        "status": "active",
                    },
                    "api_key": {
                        "id": 9,
                        "user_id": 3,
                        "name": "open-beta",
                        "token_prefix": token_prefix,
                        "status": "active",
                    },
                }

        fake = FakeDB()
        monkeypatch.setattr(mod, "db", fake)

        resp = client.post(
            "/api/beta-signups/7/approve",
            headers={"Authorization": "Bearer test-token-123"},
        )

        assert resp.status_code == 201
        approval = resp.json()["approval"]
        assert approval["token"].startswith(f"eda_live_{fake.token_prefix}_")
        assert mod._hash_token(approval["token"]) == fake.token_hash
        assert "token_hash" not in approval["api_key"]
        assert approval["email_sent"] is False

    def test_admin_approve_rejects_already_handled_signup(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def approve_beta_signup(self, *args, **kwargs):
                raise ValueError("beta signup 7 is approved")

        monkeypatch.setattr(mod, "db", FakeDB())

        resp = client.post(
            "/api/beta-signups/7/approve",
            headers={"Authorization": "Bearer test-token-123"},
        )

        assert resp.status_code == 409
        assert resp.json()["ok"] is False

    def test_admin_approve_page_requires_owner_token(self, client):
        resp = client.post("/admin/beta-signups/7/approve")
        assert resp.status_code == 401

    def test_cookie_admin_post_rejects_cross_origin(self, client, monkeypatch):
        import mcp_server.serve_http as mod

        class FakeDB:
            pool = object()

            async def approve_beta_signup(self, *args, **kwargs):
                raise AssertionError("cross-origin approval must not execute")

        monkeypatch.setattr(mod, "db", FakeDB())
        login = client.post(
            "/admin/login",
            data={"token": "test-token-123"},
            follow_redirects=False,
        )
        assert login.status_code == 303

        resp = client.post(
            "/admin/beta-signups/7/approve",
            headers={"Origin": "https://evil.example"},
        )
        assert resp.status_code == 403

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
        assert run["feedback"] == []

    @pytest.mark.asyncio
    async def test_submit_human_feedback_records_review_turn(self, db):
        from mcp_server.server_http import get_run, submit_human_feedback

        await db.save_run(
            "http-feedback-run",
            None,
            SIMPLE_SPEC,
            [],
            {"ok": True, "run_id": "http-feedback-run"},
            artifacts={"preview_2d_top.png": "base64-png"},
        )

        result = await submit_human_feedback(
            "http-feedback-run",
            "Mounting holes should be closer to the corners.",
            labels=["Placement", "mounting holes"],
            suggested_action="Move holes outward and preserve clearance.",
        )
        run = await get_run("http-feedback-run")

        assert result["status"] == "recorded"
        assert result["warning"] is None
        assert result["feedback"]["structured"]["labels"] == [
            "placement",
            "mounting-holes",
        ]
        assert run["feedback"][0]["feedback"].startswith("Mounting holes")

    @pytest.mark.asyncio
    async def test_submit_human_feedback_warns_for_missing_artifact(self, db):
        from mcp_server.server_http import submit_human_feedback

        await db.save_run("http-feedback-missing-artifact", None, SIMPLE_SPEC, [], {"ok": True})

        result = await submit_human_feedback(
            "http-feedback-missing-artifact",
            "I reviewed a screenshot from outside the artifact bundle.",
            target_artifact="external-screenshot.png",
        )

        assert result["status"] == "recorded"
        assert "not present" in result["warning"]

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
            assert data["pending_jobs"] == 0
            assert data["active_jobs"] == 0

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
            assert isinstance(data["queued_jobs"], int)
            assert isinstance(data["running_jobs"], int)
            assert isinstance(data["stale_running_jobs"], int)
            assert isinstance(data["active_jobs"], int)
        finally:
            await database.close()


class TestWorkerStaleRecovery:
    @pytest.mark.asyncio
    async def test_reap_stale_jobs_returns_marked_count(self):
        from mcp_server.worker import _reap_stale_jobs

        class FakeDB:
            async def fail_stale_running_jobs(self):
                return 2

        assert await _reap_stale_jobs(FakeDB(), "worker-test") == 2

    @pytest.mark.asyncio
    async def test_reap_stale_jobs_does_not_crash_worker_loop(self):
        from mcp_server.worker import _reap_stale_jobs

        class FakeDB:
            async def fail_stale_running_jobs(self):
                raise RuntimeError("database unavailable")

        assert await _reap_stale_jobs(FakeDB(), "worker-test") == 0


class TestWorkerLogging:
    def test_job_log_summary_is_actionable_without_source_code(self):
        from mcp_server.worker import _job_log_summary

        result = {
            "run_id": "run-1",
            "status": "failed",
            "ok": False,
            "stage": "exec",
            "decision_kind": "code_authoring_error",
            "spec": {
                "_mode": "skidl_python",
                "code": "SECRETISH_CODE_SHOULD_NOT_APPEAR",
            },
            "exceptions": [{
                "code": "CODE_EXEC_ERROR",
                "severity": "fatal",
                "message": "pin 'A0' not found on U1",
                "candidates": [],
            }],
        }

        summary = _job_log_summary(
            "job-1",
            "failed",
            result,
            {"board.kicad_sch": "(sch)", "board.kicad_pcb": "(pcb)"},
        )

        assert summary["job_id"] == "job-1"
        assert summary["run_id"] == "run-1"
        assert summary["stage"] == "exec"
        assert summary["decision_kind"] == "code_authoring_error"
        assert summary["exceptions"][0]["code"] == "CODE_EXEC_ERROR"
        assert summary["artifact_keys"] == ["board.kicad_pcb", "board.kicad_sch"]
        assert "spec" not in summary
        assert "SECRETISH_CODE_SHOULD_NOT_APPEAR" not in json.dumps(summary)


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
            "submit_skidl_code", "get_job", "get_run",
            "submit_human_feedback", "search_kicad", "convert_lcsc",
        }
        for tool in tools:
            assert tool.description and len(tool.description) > 100, (
                f"{tool.name} description too thin for agent use"
            )

    @pytest.mark.asyncio
    async def test_submit_skidl_code_teaches_the_loop(self):
        """An agent reading only submit_skidl_code must learn the product loop."""
        from mcp_server.server_http import mcp
        tools = {t.name: t for t in await mcp.list_tools()}
        desc = tools["submit_skidl_code"].description
        for needle in (
            "get_job", "job_id", "Part()", "Net()", "footprint",
            "Library:Name", "POWER", "100nF", "eda://guide/skidl",
            "timeout_s", "route_timeout_s", "assembly_policy", "pipeline_goal",
            "assembly_side", "edge_preference", "edge_rot_deg", "LCSC",
            "manufacturing", "placement_review", "EDA_FLOORPLAN",
            "custom_footprints", "EDA_FOOTPRINTS", "succeeded_with_warnings",
            "terminal status", "edge_preference alone is usually not enough",
        ):
            assert needle in desc, f"submit_skidl_code description missing {needle!r}"

    @pytest.mark.asyncio
    async def test_submit_skidl_code_stores_effective_assembly_policy(self, monkeypatch):
        from mcp_server import server_http

        seen = {}

        async def fake_create_job(spec, options, parent_job_id=None):
            seen["spec"] = spec
            seen["options"] = options
            seen["parent_job_id"] = parent_job_id
            return "job-default-policy"

        monkeypatch.setattr(server_http.db, "create_job", fake_create_job)

        result = await server_http.submit_skidl_code(
            "from skidl import *",
            board_name="policy-board",
        )

        assert result["job_id"] == "job-default-policy"
        assert seen["spec"]["assembly_policy"] == "single_sided"
        assert seen["spec"]["pipeline_goal"] == "manufacturing"
        assert seen["options"]["assembly_policy"] == "single_sided"
        assert seen["options"]["pipeline_goal"] == "manufacturing"

    @pytest.mark.asyncio
    async def test_submit_skidl_code_stores_custom_footprints(self, monkeypatch):
        from mcp_server import server_http

        seen = {}

        async def fake_create_job(spec, options, parent_job_id=None):
            seen["spec"] = spec
            return "job-custom-footprints"

        monkeypatch.setattr(server_http.db, "create_job", fake_create_job)

        footprint = '(footprint "Tiny_2Pad" (layer "F.Cu"))'
        result = await server_http.submit_skidl_code(
            "from skidl import *",
            board_name="custom-fp-board",
            custom_footprints={"MyLib:Tiny_2Pad": footprint},
        )

        assert result["job_id"] == "job-custom-footprints"
        assert seen["spec"]["custom_footprints"] == {"MyLib:Tiny_2Pad": footprint}

    @pytest.mark.asyncio
    async def test_submit_human_feedback_teaches_review_turn(self):
        from mcp_server.server_http import mcp
        tools = {t.name: t for t in await mcp.list_tools()}
        desc = tools["submit_human_feedback"].description

        for needle in (
            "preview_2d_top.png",
            "ask-human",
            "record",
            "revise SKiDL code",
            "does not automatically modify",
        ):
            assert needle in desc, f"submit_human_feedback description missing {needle!r}"

    @pytest.mark.asyncio
    async def test_search_kicad_detail_returns_pin_details_for_multiple_symbols(
        self,
        monkeypatch,
    ):
        from llm.kicad_index import PinInfo, SymbolDetail, SymbolEntry
        import llm.kicad_index as kicad_index
        import mcp_server.server_http as server_http

        monkeypatch.setattr(server_http, "_lcsc_variants", lambda query: [])
        monkeypatch.setattr(kicad_index, "search_footprints", lambda query, limit=5: [])
        monkeypatch.setattr(
            kicad_index,
            "search_symbols",
            lambda query, limit=8: [
                SymbolEntry(lib="MCU_Module", name="Arduino_Nano_RP2040_Connect"),
                SymbolEntry(lib="MCU_Module", name="RaspberryPi_Pico"),
            ],
        )

        def fake_detail(lib, name):
            pins = {
                "Arduino_Nano_RP2040_Connect": [PinInfo("17", "3V3", "power_out")],
                "RaspberryPi_Pico": [PinInfo("1", "GPIO0", "bidirectional")],
            }[name]
            return SymbolDetail(
                lib=lib,
                name=name,
                description="",
                keywords="",
                footprint="Module:Fake",
                pins=pins,
            )

        monkeypatch.setattr(kicad_index, "get_symbol_detail", fake_detail)

        result = await server_http.search_kicad("RP2040", detail=True)

        assert result["pin_detail"]["part"] == "MCU_Module:Arduino_Nano_RP2040_Connect"
        pico = next(
            detail
            for detail in result["pin_details"]
            if detail["part"] == "MCU_Module:RaspberryPi_Pico"
        )
        assert pico["pins"] == [
            {"num": "1", "name": "GPIO0", "type": "bidirectional"}
        ]

    @pytest.mark.asyncio
    async def test_search_kicad_adds_design_notes_for_keypads_and_modules(
        self,
        monkeypatch,
    ):
        from llm.kicad_index import SymbolEntry
        import llm.kicad_index as kicad_index
        import mcp_server.server_http as server_http

        monkeypatch.setattr(kicad_index, "search_footprints", lambda query, limit=5: [])
        monkeypatch.setattr(kicad_index, "get_symbol_detail", lambda lib, name: None)

        def fake_symbols(query, limit=8):
            if "keypad" in query.lower():
                return [SymbolEntry(lib="Switch", name="SW_Push")]
            return [SymbolEntry(lib="MCU_Module", name="NUCLEO64-F411RE")]

        monkeypatch.setattr(kicad_index, "search_symbols", fake_symbols)
        monkeypatch.setattr(
            server_http,
            "_lcsc_variants",
            lambda query: [{"lcsc": "C123", "mfr": "STM32F411RET6"}]
            if "stm32" in query.lower()
            else [],
        )

        keypad = await server_http.search_kicad("4x4 keypad matrix", detail=True)
        mcu = await server_http.search_kicad("STM32F411RET6", detail=True)

        assert "SW_Matrix_4x4" in keypad["design_notes"][0]
        assert "SW_Push" in keypad["hint"]
        assert "MCU_Module/NUCLEO/Pico" in mcu["design_notes"][0]
        assert "custom PCB around the chip" in mcu["hint"]

    def test_converted_lcsc_symbol_file_pin_details_are_parsed(self, tmp_path):
        import mcp_server.server_http as server_http

        sym_file = tmp_path / "C123.kicad_sym"
        sym_file.write_text(
            """
            (kicad_symbol_lib
              (version 20211014)
              (generator test)
              (symbol "C123"
                (pin power_in line
                  (at 0 0 0) (length 2.54)
                  (name "VDD") (number "1"))
                (pin bidirectional line
                  (at 0 2.54 0) (length 2.54)
                  (name "PA0") (number "2"))
              )
            )
            """
        )

        meta = server_http._augment_converted_meta({
            "library": "C123",
            "symbol": "C123",
            "footprint": "C123:C123_FP",
            "sym_file": str(sym_file),
        })

        assert meta["pin_detail"] == {
            "part": "C123:C123",
            "footprint": "C123:C123_FP",
            "pins": [
                {"num": "1", "name": "VDD", "type": "power_in"},
                {"num": "2", "name": "PA0", "type": "bidirectional"},
            ],
        }

    @pytest.mark.asyncio
    async def test_get_job_documents_statuses(self):
        from mcp_server.server_http import mcp
        tools = {t.name: t for t in await mcp.list_tools()}
        desc = tools["get_job"].description
        for status in (
            "queued", "running", "succeeded", "succeeded_with_warnings",
            "failed", "timeout", "crashed",
        ):
            assert status in desc
        assert "run_id" in desc and "exceptions" in desc
        assert "not a design result" in desc
        assert "edit the SKiDL code" in desc
        assert "apply_correction" not in desc
        assert "CircuitSpec" not in desc

    @pytest.mark.asyncio
    async def test_apply_correction_is_hidden_from_public_mcp_surface(self):
        from mcp_server.server_http import mcp
        tools = {t.name: t for t in await mcp.list_tools()}
        assert "apply_correction" not in tools

    @pytest.mark.asyncio
    async def test_resources_listed(self):
        from mcp_server.server_http import mcp
        resources = await mcp.list_resources()
        uris = {str(r.uri) for r in resources}
        assert uris == {
            "eda://guide/skidl",
            "eda://guide/workflow",
            "eda://guide/exceptions",
            "eda://guide/parts",
        }
        for r in resources:
            assert r.description, f"{r.uri} has no description"

    def test_parts_guide_teaches_ambiguous_connector_choices(self):
        from mcp_server.server_http import PARTS_GUIDE, SKIDL_GUIDE

        for needle in (
            "TRS",
            "switched",
            "right-angle",
            "through-hole",
            "panel",
            "USB-C",
            "screw terminals",
            "Mechanical:MountingHole",
            "Connector:TestPoint",
            "Device:TestPoint",
            "SW_Matrix_4x4",
            "Switch",
            "ROWx",
            "COLx",
        ):
            assert needle in PARTS_GUIDE
        assert "Custom MCU board vs dev board" in SKIDL_GUIDE
        assert "convert_lcsc()" in SKIDL_GUIDE
        assert "eda://guide/parts" in SKIDL_GUIDE
        assert 'pipeline_goal="placement_review"' in SKIDL_GUIDE
        assert "EDA_FLOORPLAN" in SKIDL_GUIDE
        assert "custom_footprints" in SKIDL_GUIDE
        assert "EDA_FOOTPRINTS" in SKIDL_GUIDE

    def test_circuit_spec_reference_is_legacy_not_public_resource(self):
        from mcp_server.server_http import CIRCUIT_SPEC_GUIDE
        assert "Legacy/Internal" in CIRCUIT_SPEC_GUIDE
        assert "submit_skidl_code()" in CIRCUIT_SPEC_GUIDE

    def test_submit_skidl_code_docstring_is_python_first(self):
        from mcp_server import server_http
        doc = server_http.submit_skidl_code.__doc__
        assert "from skidl import *" in doc
        assert "Part(" in doc and "Net(" in doc
        assert "CircuitSpec" not in doc

    @pytest.mark.asyncio
    async def test_public_guides_do_not_offer_legacy_json_workflow(self):
        from mcp_server.server_http import EXCEPTIONS_GUIDE, WORKFLOW_GUIDE, mcp

        public_text = "\n".join([
            mcp.instructions or "",
            WORKFLOW_GUIDE,
            EXCEPTIONS_GUIDE,
            *[
                t.description or ""
                for t in await mcp.list_tools()
            ],
        ])
        for forbidden in (
            "apply_correction",
            "submit_design",
            "CircuitSpec",
            "auto_apply",
            "legacy",
        ):
            assert forbidden not in public_text

    @needs_kicad
    def test_skidl_guide_example_parts_exist_in_kicad_libs(self):
        """Part()/footprint examples in the guide should name real KiCad libs."""
        import re
        from mcp_server.server_http import SKIDL_GUIDE

        fp_root = Path("/usr/share/kicad/footprints")
        sym_root = Path("/usr/share/kicad/symbols")
        examples = re.findall(
            r'Part\("([^"]+)",\s*"([^"]+)".*?footprint="([^"]+)"',
            SKIDL_GUIDE,
            re.DOTALL,
        )
        assert examples, "SKiDL guide has no Part() examples with footprints"

        for lib, part, footprint in examples:
            sym_file = sym_root / f"{lib}.kicad_sym"
            assert sym_file.exists(), f"missing symbol lib {lib}"
            assert f'"{part}"' in sym_file.read_text(), f"symbol {part} not in {lib}"
            fp_lib, fp_name = footprint.split(":")
            fp_file = fp_root / f"{fp_lib}.pretty" / f"{fp_name}.kicad_mod"
            assert fp_file.exists(), f"missing footprint {footprint}"

    def test_get_job_hint_for_python_mode_says_edit_and_resubmit(self):
        from mcp_server.server_http import _get_job_hint
        hint = _get_job_hint({
            "status": "failed",
            "spec": {"_mode": "skidl_python", "code": "from skidl import *"},
            "result": {
                "run_id": "run-python",
                "decision_required": True,
                "exceptions": [{"code": "SPEC_PIN_NOT_FOUND"}],
            },
        })
        assert "edit the SKiDL code" in hint
        assert "submit_skidl_code()" in hint
        assert "apply_correction" not in hint

    def test_get_job_hint_for_code_error_includes_line_context(self):
        from mcp_server.server_http import _get_job_hint
        hint = _get_job_hint({
            "status": "failed",
            "spec": {"_mode": "skidl_python", "code": "from skidl import *"},
            "result": {
                "run_id": "run-code",
                "decision_required": True,
                "exceptions": [{
                    "code": "CODE_EXEC_ERROR",
                    "subject": {
                        "line": 3,
                        "line_text": "ain0 += ads1115_part['A0']",
                        "suggested_pins": ["AIN0", "ADDR"],
                    },
                }],
            },
        })
        assert "line 3" in hint
        assert "ain0 += ads1115_part" in hint
        assert "suggested pins: AIN0, ADDR" in hint
        assert "submit_skidl_code()" in hint

    def test_skidl_guide_teaches_connection_syntax_not_connect_helper(self):
        from mcp_server.server_http import SKIDL_GUIDE

        assert "net += pin1, pin2" in SKIDL_GUIDE
        assert "pin += net" in SKIDL_GUIDE
        assert "Do not use a global `connect()` function" in SKIDL_GUIDE
        assert 'Net("X") + part["PIN"]' in SKIDL_GUIDE

    def test_get_job_hint_for_engine_crash_says_backend_failure(self):
        from mcp_server.server_http import _get_job_hint
        hint = _get_job_hint({
            "status": "failed",
            "spec": {"_mode": "skidl_python", "code": "from skidl import *"},
            "result": {
                "run_id": "run-crash",
                "decision_required": True,
                "exceptions": [{
                    "code": "ENGINE_CRASH",
                    "subject": {"partial_artifacts": ["board.kicad_pcb"]},
                }],
            },
        })
        assert "Backend engine failure" in hint
        assert "not circuit feedback" in hint
        assert "get_run('run-crash')" in hint

    def test_get_job_hint_for_post_artifact_failure_says_fetch_artifacts(self):
        from mcp_server.server_http import _get_job_hint
        hint = _get_job_hint({
            "status": "failed",
            "spec": {"_mode": "skidl_python", "code": "from skidl import *"},
            "result": {
                "run_id": "run-post-artifact",
                "decision_required": True,
                "exceptions": [{
                    "code": "POST_ARTIFACT_FAILURE",
                    "subject": {"partial_artifacts": ["board.kicad_pcb"]},
                }],
            },
        })
        assert "Manufacturing is incomplete" in hint
        assert "not call this board manufacturable or complete" in hint
        assert "get_run('run-post-artifact')" in hint
        assert "routing/export tool failure" in hint

    def test_get_job_hint_for_route_unavailable_is_hard_manufacturing_gate(self):
        from mcp_server.server_http import _get_job_hint
        hint = _get_job_hint({
            "status": "failed",
            "spec": {"_mode": "skidl_python", "code": "from skidl import *"},
            "result": {
                "run_id": "run-route",
                "decision_required": True,
                "exceptions": [{
                    "code": "ROUTE_UNAVAILABLE",
                    "subject": {"stage": "dsn_export", "returncode": -11},
                }],
            },
        })
        assert "Manufacturing is incomplete" in hint
        assert "not call this board manufacturable or complete" in hint
        assert "board size" in hint
        assert "routing/export tool failure" in hint

    def test_get_job_compacts_finished_result_for_agent(self):
        from mcp_server.server_http import _compact_job_result_for_agent

        result = {
            "run_id": "run-compact",
            "status": "failed",
            "ok": False,
            "summary": "dense final report\n" + ("details\n" * 1000),
            "spec": {"large": "omitted"},
            "_artifact_paths": {"board.kicad_pcb": "x" * 1000},
            "stderr": "native trace" * 100,
            "layout": {"ok": False, "score": {"score": 12.3}, "verbose": "x" * 5000},
            "exceptions": [
                {
                    "id": "e-route-timeout",
                    "code": "ROUTE_TIMEOUT",
                    "severity": "error",
                    "message": "routing timed out",
                    "subject": {
                        "stderr_tail": "x" * 5000,
                        "available_pins": [f"P{i}" for i in range(80)],
                    },
                    "retry_hint": "retry with route_timeout_s=300",
                    "candidates": [
                        {
                            "id": "c1",
                            "action": "regenerate",
                            "params": {"run_options": {"route_timeout_s": 300}},
                            "human_summary": "retry longer",
                            "source": "deterministic",
                        }
                    ],
                },
                *[
                    {
                        "id": f"e-advisory-{idx}",
                        "code": "LONG_POWER_NET",
                        "severity": "advisory",
                        "message": "power net is long",
                        "subject": {"warning": "y" * 2000},
                        "candidates": [],
                    }
                    for idx in range(12)
                ],
            ],
        }

        _compact_job_result_for_agent(result)

        assert "spec" not in result
        assert "stderr" not in result
        assert "_artifact_paths" not in result
        assert result["top_exception"]["code"] == "ROUTE_TIMEOUT"
        assert result["exception_codes"][0] == "ROUTE_TIMEOUT"
        assert result["exceptions_truncated"] == 8
        assert len(result["summary"]) < 1400
        assert len(json.dumps(result)) < 8000

    @pytest.mark.asyncio
    async def test_get_job_omits_top_level_spec_without_mutating_store(self, monkeypatch):
        from mcp_server import server_http

        stored_job = {
            "id": "job-compact",
            "status": "failed",
            "spec": {
                "_mode": "skidl_python",
                "code": "from skidl import *\n" + ("# big source\n" * 1000),
            },
            "options": {},
            "policy": {},
            "result": {
                "run_id": "run-compact",
                "status": "failed",
                "ok": False,
                "spec": {"also": "large"},
                "exceptions": [{
                    "code": "CODE_EXEC_ERROR",
                    "severity": "fatal",
                    "message": "code did not run",
                    "candidates": [],
                }],
            },
        }

        class FakeDB:
            async def get_job(self, job_id):
                assert job_id == "job-compact"
                return stored_job

        monkeypatch.setattr(server_http, "db", FakeDB())

        response = await server_http.get_job("job-compact")

        assert "spec" not in response
        assert "spec" not in response["result"]
        assert response["result"]["top_exception"]["code"] == "CODE_EXEC_ERROR"
        assert "submit_skidl_code()" in response["hint"]
        assert stored_job["spec"]["code"].startswith("from skidl import *")
        assert stored_job["result"]["spec"] == {"also": "large"}
        assert len(json.dumps(response)) < 5000

    @pytest.mark.asyncio
    async def test_apply_correction_rejects_skidl_python_runs(self, monkeypatch):
        from mcp_server import server_http

        class FakeDB:
            async def load_run(self, run_id):
                return {
                    "spec": {"_mode": "skidl_python", "code": "from skidl import *"},
                    "exceptions": [],
                    "response": {"run_id": run_id},
                }

        monkeypatch.setattr(server_http, "db", FakeDB())
        with pytest.raises(ValueError, match="submit_skidl_code"):
            await server_http.apply_correction("run-python", [])

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
        assert "submit_skidl_code" in instructions
        assert "CircuitSpec" not in instructions
        assert "JSON" not in instructions
        assert "eda://" in instructions
