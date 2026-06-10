"""Tests for the telemetry module: models, store, sessions, features."""

import json
import multiprocessing

import pytest

from telemetry.features import extract_geometry
from telemetry.models import GeometryFeatures, LLMStage, RunRecord
from telemetry.store import atomic_append, read_records, session


def mk_record(run_id="abc123def456", **overrides) -> RunRecord:
    fields = dict(
        run_id=run_id,
        parent_run_id="000000000000",
        board_id="feather-rp2040",
        git_sha="deadbee",
        started_at="2026-06-10T01:00:00+00:00",
        finished_at="2026-06-10T01:02:30+00:00",
        tier=2,
        source="adafruit-benchmark",
        difficulty_axis="pin_count",
        nl_source="marketing_page",
        mode="internal",
        model_tier="mid",
        geometry=GeometryFeatures(
            component_count=12, net_count=18, pin_count=64, pad_count=80,
            layer_count=2, board_area_mm2=1150.0, pad_density_per_cm2=6.9565,
        ),
        correction_iterations=2,
        candidates_scored=5,
        erc_iterations=3,
        schematic_retries=1,
        exceptions_raised=["ERC_PIN_NOT_DRIVEN", "LAYOUT_OVERLAP"],
        corrections_applied=["stub_net", "scale_outline"],
        llm_stages=[
            LLMStage(stage="design_nl_to_input", model="mid-1", tokens_in=1200,
                     tokens_out=800, latency_s=4.2, cost_usd=0.012),
            LLMStage(stage="review_internal", model="mid-1", tokens_in=3000,
                     tokens_out=400, latency_s=6.1, cost_usd=0.021),
        ],
        cpu_time_s=41.5,
        peak_rss_mb=312.0,
        status="succeeded_with_warnings",
        validation_mode="reference",
        layout_score=0.84,
        total_hpwl_mm=412.7,
        congestion_score=0.31,
        bom_match_score=0.95,
        netlist_match_score=0.88,
        failure_reason=None,
    )
    fields.update(overrides)
    return RunRecord(**fields)


class TestRoundTrip:
    def test_run_record_json_line_round_trip(self):
        rec = mk_record()
        rec.finalize()
        line = rec.model_dump_json()
        assert "\n" not in line  # must be a single JSONL line
        back = RunRecord.model_validate_json(line)
        assert back == rec
        assert back.model_dump() == rec.model_dump()


class TestSession:
    def test_clean_exit_writes_finalized_record(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        with session("board-x", "internal", path=path, tier=3) as rec:
            rec.llm_stages.append(
                LLMStage(stage="design_nl_to_input", model="m", tokens_in=100,
                         tokens_out=50, latency_s=1.0, cost_usd=0.010)
            )
            rec.llm_stages.append(
                LLMStage(stage="review_internal", model="m", tokens_in=200,
                         tokens_out=25, latency_s=2.0, cost_usd=0.005)
            )
        records = read_records(path)
        assert len(records) == 1
        got = records[0]
        assert got.board_id == "board-x"
        assert got.mode == "internal"
        assert got.tier == 3
        assert got.total_cost_usd == pytest.approx(0.015)
        assert got.total_tokens == 375
        assert got.status == "succeeded"
        assert got.finished_at is not None
        assert got.wall_time_s >= 0.0
        assert len(got.run_id) == 12

    def test_crash_writes_record_and_reraises(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        with pytest.raises(ValueError, match="engine exploded"):
            with session("board-y", "engine_only", path=path) as rec:
                rec.erc_iterations = 4
                raise ValueError("engine exploded")
        records = read_records(path)
        assert len(records) == 1
        got = records[0]
        assert got.status == "crashed"
        assert got.failure_reason == "ValueError: engine exploded"
        assert got.erc_iterations == 4  # body mutations before the crash persist
        assert got.finished_at is not None

    def test_crash_respects_explicit_status(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        with pytest.raises(RuntimeError):
            with session("board-z", "internal", path=path) as rec:
                rec.status = "timeout"
                raise RuntimeError("watchdog fired")
        got = read_records(path)[0]
        assert got.status == "timeout"  # not overwritten with "crashed"

    def test_explicit_run_id_and_env_default_path(self, tmp_path, monkeypatch):
        monkeypatch.setenv("SKIDL_TELEMETRY_DIR", str(tmp_path))
        with session("board-env", "external", run_id="run-0042") as rec:
            assert rec.run_id == "run-0042"
        records = read_records(tmp_path / "runs.jsonl")
        assert [r.run_id for r in records] == ["run-0042"]


def _append_worker(path_str: str, worker_idx: int, n: int) -> None:
    """Top-level so it pickles under any multiprocessing start method."""
    from telemetry.store import atomic_append  # re-import for spawn safety
    for i in range(n):
        rec = RunRecord(
            run_id=f"w{worker_idx}-r{i:03d}",
            board_id=f"board-{worker_idx}",
            mode="internal",
            started_at="2026-06-10T01:00:00+00:00",
        )
        atomic_append(path_str, rec.model_dump_json())


class TestConcurrentAppend:
    def test_three_processes_no_interleaving(self, tmp_path):
        path = tmp_path / "runs.jsonl"
        n_workers, n_each = 3, 20
        procs = [
            multiprocessing.Process(target=_append_worker, args=(str(path), w, n_each))
            for w in range(n_workers)
        ]
        for p in procs:
            p.start()
        for p in procs:
            p.join(timeout=60)
            assert p.exitcode == 0
        records = read_records(path)
        assert len(records) == n_workers * n_each
        run_ids = {r.run_id for r in records}
        assert len(run_ids) == n_workers * n_each  # all distinct, none mangled
        for w in range(n_workers):
            assert sum(1 for r in records if r.board_id == f"board-{w}") == n_each


class TestTolerantReader:
    def test_garbage_lines_skipped_with_warning(self, tmp_path, capsys):
        path = tmp_path / "runs.jsonl"
        good1 = mk_record(run_id="good00000001").model_dump_json()
        good2 = mk_record(run_id="good00000002").model_dump_json()
        atomic_append(path, good1)
        atomic_append(path, "{this is not json at all")
        atomic_append(path, json.dumps({"run_id": "missing-required-fields"}))
        atomic_append(path, "")  # blank line is silently ignored
        atomic_append(path, good2)
        records = read_records(path)
        assert [r.run_id for r in records] == ["good00000001", "good00000002"]
        err = capsys.readouterr().err
        assert err.count("skipping unparsable line") == 2

    def test_missing_file_returns_empty(self, tmp_path):
        assert read_records(tmp_path / "nope.jsonl") == []


class TestFeatures:
    def test_extract_geometry(self):
        spec = {
            "board": {"name": "b", "layers": 4},
            "parts": [{"ref": "U1"}, {"ref": "C1"}, {"ref": "R1"}],
            "nets": [
                {"name": "VCC", "pins": ["U1.VDD", "C1.1"]},
                {"name": "GND", "pins": ["U1.GND", "C1.2", "R1.2"]},
            ],
        }
        metrics = {"pad_count": 50, "board_area_mm2": 2000.0}
        geo = extract_geometry(spec, metrics)
        assert geo.component_count == 3
        assert geo.net_count == 2
        assert geo.pin_count == 5
        assert geo.pad_count == 50
        assert geo.layer_count == 4
        assert geo.board_area_mm2 == 2000.0
        assert geo.pad_density_per_cm2 == pytest.approx(50 / 20.0)

    def test_extract_geometry_guards_div0_and_missing_keys(self):
        geo = extract_geometry({}, {})
        assert geo == GeometryFeatures()  # everything defaults to 0
        geo = extract_geometry({"parts": [{"ref": "U1"}]}, {"pad_count": 10})
        assert geo.pad_density_per_cm2 == 0.0  # no area -> no density, no crash
