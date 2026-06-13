"""Tests for the MCP server pipeline layer."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from mcp_server.exception_mapper import crash_exception, layout_exceptions, suppress_waived
from mcp_server.engine_worker import (
    _circuit_to_spec_dict,
    _exec_skidl,
    _manufacturing_output_exception,
    _missing_manufacturing_outputs,
)
from mcp_server.pipeline import (
    _enrich_code_exceptions,
    _infer_crash_stage,
    run_pipeline,
    run_pipeline_code,
)
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


class TestHelpfulFailures:
    def test_code_exec_error_gets_pin_and_line_context(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="TypeError: 'NoneType' object is not iterable",
        )
        code = "\n".join([
            "from skidl import *",
            "ads1115_part = Part('Analog_ADC', 'ADS1115IDGS')",
            "ain0 = Net('AIN0'); ain0 += ads1115_part['A0']",
        ])
        stderr = (
            "ERROR: No pins found using ADS1115IDGS:U1[('A0',)] "
            "@ [/app/mcp_server/engine_worker.py:627=>/tmp/run/<string>:3]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert "pin 'A0' not found on U1" in exc.message
        assert exc.subject["ref"] == "U1"
        assert exc.subject["pin"] == "A0"
        assert exc.subject["line"] == 3
        assert "ads1115_part['A0']" in exc.subject["line_text"]
        assert "search_kicad" in exc.retry_hint

    def test_crash_stage_infers_partial_artifacts(self, tmp_path):
        (tmp_path / "amp.kicad_sch").write_text("(schematic)")
        (tmp_path / "amp.kicad_pcb").write_text("(pcb)")

        assert _infer_crash_stage({}, tmp_path) == "after_pcb_write"

    def test_manufacturing_gate_requires_all_fab_outputs(self):
        mfg = {"gerbers": True, "gerber_files": ["board-F_Cu.gbr", "board.drl"]}

        missing = _missing_manufacturing_outputs(mfg)
        exc = _manufacturing_output_exception(mfg)

        assert missing == ["bom.csv", "cpl.csv"]
        assert exc.code == ExcCode.MANUFACTURING_OUTPUT_FAILURE
        assert exc.severity == Severity.ERROR
        assert exc.subject["missing_outputs"] == missing

    def test_terminal_clash_crash_maps_to_schematic_routing_failure(self):
        stderr = "\n".join([
            "Traceback (most recent call last):",
            "  File \"/app/src/skidl/schematics/route.py\", line 495, in add_terminal",
            "    raise TerminalClashException",
            "skidl.schematics.route.TerminalClashException",
        ])

        exc = crash_exception("TerminalClashException: ", stderr, stage="engine_worker")

        assert exc.code == ExcCode.SCH_ROUTING_FAILURE
        assert exc.severity == Severity.FATAL
        assert exc.subject["stage"] == "schematic_routing"
        assert exc.subject["exception"] == "TerminalClashException"
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert "schematic renderer limitation" in exc.retry_hint


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


@needs_kicad
class TestSkidlPythonDesignReview:
    def test_python_mode_blocks_missing_connector(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
r1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
vcc += r1[1]
gnd += r1[2]
"""

        response = run_pipeline_code(
            code,
            board_name="missing-connector",
            outline_mm=[25.0, 20.0],
            out_dir=tmp_path,
            timeout_s=120,
        )

        assert response.stage == "design_review"
        assert not response.ok
        assert any(exc.code == ExcCode.DESIGN_NO_CONNECTOR for exc in response.exceptions)

    def test_python_mode_uses_design_intent_for_missing_feature(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
vcc += j1[1]
gnd += j1[2]
"""

        response = run_pipeline_code(
            code,
            board_name="missing-spi",
            outline_mm=[25.0, 20.0],
            out_dir=tmp_path,
            timeout_s=120,
            design_intent="SPI sensor breakout",
        )

        assert any(
            exc.code == ExcCode.DESIGN_MISSING_FEATURE
            and exc.subject.get("feature") == "SPI interface"
            for exc in response.exceptions
        )

    def test_python_mode_missing_pcb_footprint_is_structured_error(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
r1 = Part("Device", "R", value="10K", footprint="NoSuchLib:NoSuchFootprint")
vcc += j1[1], r1[1]
gnd += j1[2], r1[2]
"""

        response = run_pipeline_code(
            code,
            board_name="missing-footprint",
            outline_mm=[25.0, 20.0],
            out_dir=tmp_path,
            timeout_s=120,
        )

        assert response.stage == "layout_write"
        assert not response.ok
        assert any(exc.code == ExcCode.FOOTPRINT_MISSING for exc in response.exceptions)
        assert not any(exc.code == ExcCode.ENGINE_CRASH for exc in response.exceptions)

    def test_python_extraction_does_not_mark_signal_nets_as_power(self):
        code = """
from skidl import *
vcc = Net("3V3"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
sda = Net("SDA")
r1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
j1 = Part("Connector_Generic", "Conn_01x03",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
vcc += r1[1], j1[1]
sda += r1[2], j1[2]
gnd += j1[3]
"""

        spec = _circuit_to_spec_dict(_exec_skidl(code))
        power_by_name = {net["name"]: net["power"] for net in spec["nets"]}

        assert power_by_name["3V3"] is True
        assert power_by_name["GND"] is True
        assert power_by_name["SDA"] is False
