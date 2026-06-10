"""Tests for the MCP server pipeline layer."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from mcp_server.exception_mapper import layout_exceptions, suppress_waived
from mcp_server.pipeline import run_pipeline
from mcp_server.runs import RunStore
from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity


SYM_DIR = "/usr/share/kicad/symbols"
FP_DIR = "/usr/share/kicad/footprints"
needs_kicad = pytest.mark.skipif(
    not (os.path.isdir(SYM_DIR) and os.path.isdir(FP_DIR)),
    reason="KiCad 9 symbol/footprint libraries not installed",
)


def trivial_spec() -> dict:
    return {
        "board": {"name": "mcp-smoke", "outline_hint_mm": [30.0, 20.0]},
        "parts": [
            {
                "ref": "R1",
                "lib": None,
                "part": "R",
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
                "footprint": "Resistor_SMD:R_0603_1608Metric",
                "pins": [
                    {"num": "1", "name": "A"},
                    {"num": "2", "name": "B"},
                ],
            },
        ],
        "nets": [
            {"name": "SIG", "pins": ["R1.A", "R2.A"]},
            {"name": "GND", "power": True, "pins": ["R1.B", "R2.B"]},
        ],
    }


class TestRunStore:
    def test_round_trip(self, tmp_path):
        store = RunStore(tmp_path)
        spec = CircuitSpec.model_validate(trivial_spec())
        exc = DesignException(
            id="e1",
            code=ExcCode.HIGH_CONGESTION,
            severity=Severity.ADVISORY,
            message="high congestion",
            subject={"congestion_score": 42.0},
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.ACCEPT_ADVISORY,
                    human_summary="accept",
                )
            ],
        )
        response = {"run_id": "run-1", "ok": False}

        store.save("run-1", spec, [exc], response)

        assert store.load_spec("run-1") == spec
        assert store.load_exceptions("run-1")[0] == exc
        assert store.load_response("run-1") == response
        assert (tmp_path / "run-1" / "spec.json").exists()
        assert (tmp_path / "run-1" / "exceptions.json").exists()
        assert (tmp_path / "run-1" / "response.json").exists()


class TestExceptionMapper:
    def test_waived_advisory_suppressed(self):
        spec = CircuitSpec.model_validate(trivial_spec())
        exc = DesignException(
            id="e1",
            code=ExcCode.HIGH_CONGESTION,
            severity=Severity.ADVISORY,
            message="high congestion",
            subject={"net": "SIG"},
        )
        spec.waivers = [exc.waiver_key()]

        assert suppress_waived([exc], spec) == []

    def test_layout_validation_overlap_maps_to_candidate(self):
        class Validation:
            overlaps = [("R1", "R2")]
            outline_violations = []
            keepout_violations = []
            missing_refs = []

        class Score:
            congestion_score = 0.0
            warnings = []

        class Result:
            validation = Validation()
            score = Score()
            outline = None

        exceptions = layout_exceptions(Result())

        assert exceptions[0].code == ExcCode.LAYOUT_OVERLAP
        assert exceptions[0].candidates[0].action == ActionType.SCALE_OUTLINE


@needs_kicad
class TestWorkerAndPipeline:
    def test_engine_worker_echo_pipe_outputs_json(self, tmp_path):
        run_dir = tmp_path / "worker-run"
        envelope = {
            "run_id": "worker-run",
            "out_dir": str(run_dir),
            "spec": trivial_spec(),
        }
        proc = subprocess.run(
            [sys.executable, "-m", "mcp_server.engine_worker"],
            input=json.dumps(envelope),
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=120,
        )

        assert proc.returncode == 0, proc.stderr
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        assert payload["run_id"] == "worker-run"
        assert "metrics" in payload
        assert "outputs" in payload
        assert "layout" in payload
        assert payload["artifacts"].get("pcb", "").endswith(".kicad_pcb")

    def test_pipeline_smoke(self, tmp_path):
        response = run_pipeline(trivial_spec(), tmp_path, timeout_s=120)

        assert response.run_id
        assert response.stage in {"complete", "translate"}
        assert (tmp_path / response.run_id / "spec.json").exists()
        assert response.metrics["cpu_time_s"] >= 0.0
        if response.ok:
            assert os.path.exists(response.outputs["pcb"])
            assert os.path.exists(response.artifacts["pcb"])
