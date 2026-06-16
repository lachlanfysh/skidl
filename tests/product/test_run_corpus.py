"""Tests for the Phase 3b corpus runner."""

from __future__ import annotations

import asyncio
import json

from corpus import run_corpus
from mcp_server.pipeline import DesignResponse
from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity
from telemetry.store import read_records, session


def trivial_spec(name: str = "board-a") -> dict:
    return {
        "board": {"name": name, "outline_hint_mm": [25.0, 20.0]},
        "parts": [
            {
                "ref": "U1",
                "lib": None,
                "part": None,
                "value": "CUSTOM",
                "footprint": "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                "pins": [
                    {"num": "1", "name": "VCC", "func": "power_in"},
                    {"num": "2", "name": "GND", "func": "power_in"},
                    {"num": "3", "name": "IO", "func": "bidirectional"},
                ],
            },
            {
                "ref": "C1",
                "lib": None,
                "part": None,
                "value": "100nF",
                "footprint": "Capacitor_SMD:C_0603_1608Metric",
                "pins": [
                    {"num": "1", "name": "1", "func": "passive"},
                    {"num": "2", "name": "2", "func": "passive"},
                ],
            },
        ],
        "nets": [
            {"name": "VCC", "power": True, "pins": ["U1.VCC", "C1.1"]},
            {"name": "GND", "power": True, "pins": ["U1.GND", "C1.2"]},
            {"name": "IO", "pins": ["U1.IO"]},
        ],
    }


def write_manifest(path, rows):
    with open(path, "w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")


def write_spec(path, name):
    path.write_text(json.dumps(trivial_spec(name)) + "\n")


def manifest_row(board_id, spec_path):
    return {
        "board_id": board_id,
        "tier": 1,
        "source": "test",
        "difficulty_axis": "digital",
        "nl_source": "cached",
        "description": "test board",
        "validation_mode": "internal",
        "spec_path": str(spec_path),
    }


def config(tmp_path, manifest, telemetry):
    return run_corpus.RunnerConfig(
        manifest=manifest,
        artifacts=tmp_path / "artifacts",
        telemetry=telemetry,
        spend_log=tmp_path / "spend.jsonl",
        pid_file=tmp_path / "runner.pid",
        mode="engine_only",
        limit=None,
        timeout_s=30,
        concurrency=1,
        max_runtime_hours=1,
        no_mcp=True,
        force=False,
    )


def test_checkpoint_selection_skips_terminal_board(tmp_path):
    spec_a = tmp_path / "a.json"
    spec_b = tmp_path / "b.json"
    write_spec(spec_a, "a")
    write_spec(spec_b, "b")
    manifest = tmp_path / "manifest.jsonl"
    telemetry = tmp_path / "runs.jsonl"
    write_manifest(manifest, [manifest_row("a", spec_a), manifest_row("b", spec_b)])

    with session("a", "engine_only", path=telemetry) as record:
        record.status = "succeeded"

    cfg = config(tmp_path, manifest, telemetry)
    rows = run_corpus.select_rows(run_corpus.load_manifest(manifest), cfg)

    assert [row["board_id"] for row in rows] == ["b"]


def test_engine_only_runner_records_board_level_telemetry(tmp_path, monkeypatch):
    spec_a = tmp_path / "a.json"
    spec_b = tmp_path / "b.json"
    write_spec(spec_a, "a")
    write_spec(spec_b, "b")
    manifest = tmp_path / "manifest.jsonl"
    telemetry = tmp_path / "runs.jsonl"
    write_manifest(manifest, [manifest_row("a", spec_a), manifest_row("b", spec_b)])
    calls = []

    def fake_run_pipeline(spec, out_dir, timeout_s=300, **kwargs):
        circuit_spec = CircuitSpec.model_validate(spec)
        calls.append((circuit_spec.board.name, kwargs))
        return DesignResponse(
            run_id=f"run-{circuit_spec.board.name}",
            ok=True,
            status="succeeded",
            stage="complete",
            metrics={
                "cpu_time_s": 0.1,
                "peak_rss_mb": 12.0,
                "layout_score": 0.9,
                "pad_count": 8,
                "board_area_mm2": 500.0,
            },
        )

    monkeypatch.setattr(run_corpus, "run_pipeline", fake_run_pipeline)

    cfg = config(tmp_path, manifest, telemetry)
    results = asyncio.run(run_corpus.run_manifest(cfg))

    assert [result.status for result in results] == ["succeeded", "succeeded"]
    assert len(calls) == 2
    assert all(call[1]["record_telemetry"] is False for call in calls)
    records = read_records(telemetry)
    assert [record.board_id for record in records] == ["a", "b"]
    assert all(record.mode == "engine_only" for record in records)
    assert all(record.geometry.component_count == 2 for record in records)
    assert all(record.total_tokens == 0 for record in records)


def test_five_board_product_pack_writes_artifact_contract(tmp_path, monkeypatch):
    telemetry = tmp_path / "runs.jsonl"
    calls = []

    def fake_run_pipeline(spec, out_dir, timeout_s=300, **kwargs):
        circuit_spec = CircuitSpec.model_validate(spec)
        board_id = kwargs["board_id"]
        run_id = f"run-{board_id}"
        run_dir = tmp_path / "artifacts" / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "board.kicad_pcb").write_text("(kicad_pcb)\n", encoding="utf-8")
        (run_dir / "preview_top.svg").write_text("<svg></svg>\n", encoding="utf-8")
        calls.append((board_id, circuit_spec.board.name, kwargs))
        return DesignResponse(
            run_id=run_id,
            ok=True,
            status="succeeded",
            stage="complete",
            outputs={
                "run_dir": str(run_dir),
                "pcb": str(run_dir / "board.kicad_pcb"),
                "previews": {"files": ["preview_top.svg"]},
            },
            artifacts={
                "run_dir": str(run_dir),
                "pcb": str(run_dir / "board.kicad_pcb"),
                "previews": {"files": ["preview_top.svg"]},
            },
            layout={
                "ok": True,
                "outline": {"width_mm": 55.0, "height_mm": 40.0},
                "placed_parts": [
                    {"ref": f"U{i}", "x_mm": 8.0 + i * 5.0, "y_mm": 10.0 + i}
                    for i in range(1, 6)
                ],
                "validation": {"ok": True},
            },
            metrics={
                "manufacturable": True,
                "manufacturing_complete": True,
                "congestion_score": 90.0,
                "board_area_mm2": 2200.0,
            },
        )

    monkeypatch.setattr(run_corpus, "run_pipeline", fake_run_pipeline)
    cfg = run_corpus.RunnerConfig(
        artifacts=tmp_path / "artifacts",
        telemetry=telemetry,
        spend_log=tmp_path / "spend.jsonl",
        pid_file=tmp_path / "runner.pid",
        mode="engine_only",
        concurrency=1,
        max_runtime_hours=1,
        no_mcp=True,
        force=True,
        product_pack=run_corpus.PRODUCT_PACK_NAME,
    )

    results = asyncio.run(run_corpus.run_manifest(cfg))

    assert [result.board_id for result in results] == list(run_corpus.PRODUCT_PACK_BOARDS)
    assert len(calls) == 5
    assert all(call[2]["validation_mode"] == "internal" for call in calls)
    for result in results:
        run_dir = tmp_path / "artifacts" / result.run_id
        response_json = run_dir / "response.json"
        quality_json = run_dir / "layout_quality.json"
        assert response_json.exists()
        assert quality_json.exists()
        quality = json.loads(quality_json.read_text(encoding="utf-8"))
        assert quality["gates"]["schematic_ok"] is True
        assert quality["gates"]["manufacturable"] is True
        assert quality["gates"]["visual_review_ready"] is True
        assert quality["gates"]["product_layout_ok"] is False
        assert {
            "LOW_PART_SPREAD",
            "OVERSIZED_BOARD_OUTLINE",
            "UNUSED_OUTLINE_REGION",
        }.issubset(set(result.quality_summary["issue_classes"]))
        assert result.quality_summary["response_json"] == str(response_json)
        assert result.quality_summary["layout_quality"] == str(quality_json)
        assert result.quality_summary["preview_paths"]["preview_top.svg"] == str(
            run_dir / "preview_top.svg"
        )

    report = json.loads(
        (tmp_path / "artifacts" / run_corpus.PRODUCT_PACK_REPORT).read_text(encoding="utf-8")
    )
    assert report["pack"] == "five-board"
    assert report["board_count"] == 5
    assert report["gate_failures"]["product_layout_ok"] == list(run_corpus.PRODUCT_PACK_BOARDS)
    for issue_class in (
        "LOW_PART_SPREAD",
        "OVERSIZED_BOARD_OUTLINE",
        "UNUSED_OUTLINE_REGION",
    ):
        assert report["issue_classes"][issue_class] == list(run_corpus.PRODUCT_PACK_BOARDS)


def test_deterministic_c1_choice_applies_candidate():
    spec = CircuitSpec.model_validate(trivial_spec())
    exc = DesignException(
        id="e1",
        code=ExcCode.ERC_PIN_NOT_CONNECTED,
        severity=Severity.ERROR,
        message="IO is intentionally left open",
        subject={"net": "IO"},
        candidates=[
            Candidate(
                id="c1",
                action=ActionType.STUB_NET,
                params={"net": "IO"},
                human_summary="Stub the IO net.",
            )
        ],
    )

    choices = run_corpus.deterministic_choices([exc])
    updated, applied = run_corpus.apply_choices(spec, [exc], choices)

    assert choices == [{"exception_id": "e1", "candidate_id": "c1"}]
    assert next(net for net in updated.nets if net.name == "IO").stub is True
    assert applied == ["ERC_PIN_NOT_CONNECTED:stub_net:c1"]


def test_worker_startup_failure_is_not_silent_success(tmp_path, monkeypatch):
    spec_a = tmp_path / "a.json"
    write_spec(spec_a, "a")
    manifest = tmp_path / "manifest.jsonl"
    telemetry = tmp_path / "runs.jsonl"
    write_manifest(manifest, [manifest_row("a", spec_a)])

    class BrokenClient:
        async def __aenter__(self):
            raise RuntimeError("client cannot start")

        async def __aexit__(self, exc_type, exc, tb):
            return None

    monkeypatch.setattr(run_corpus, "_client_for", lambda cfg: BrokenClient())

    cfg = config(tmp_path, manifest, telemetry)
    results = asyncio.run(run_corpus.run_manifest(cfg))

    assert len(results) == 1
    assert results[0].status == "crashed"
    assert "client cannot start" in results[0].failure_reason


def test_board_timeout_covers_spec_translation(tmp_path, monkeypatch):
    manifest = tmp_path / "manifest.jsonl"
    telemetry = tmp_path / "runs.jsonl"
    row = manifest_row("a", tmp_path / "missing.json")
    write_manifest(manifest, [row])

    async def slow_spec(*args, **kwargs):
        await asyncio.sleep(1.0)
        return CircuitSpec.model_validate(trivial_spec("a")), []

    monkeypatch.setattr(run_corpus, "nl_to_input_spec", slow_spec)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    cfg = config(tmp_path, manifest, telemetry)
    cfg.mode = "internal"
    cfg.timeout_s = 0.01
    result = asyncio.run(
        run_corpus.run_board(
            row,
            cfg,
            run_corpus.DirectDesignClient(),
            run_corpus.SpendTracker(1.0),
        )
    )

    assert result.status == "timeout"
    assert "timeout" in result.failure_reason
