"""Tests for the MCP server pipeline layer."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from mcp_server.exception_mapper import (
    crash_exception,
    layout_exceptions,
    order_exceptions_for_agent,
    suppress_waived,
)
from mcp_server.engine_worker import (
    _code_exception_from_exec,
    _code_exception_from_syntax,
    _circuit_to_spec_dict,
    _drc_to_exceptions,
    _exec_skidl,
    _export_dsn_with_pcbnew,
    _footprint_missing_exception,
    _import_ses_with_pcbnew,
    _manufacturing_output_exception,
    _missing_manufacturing_outputs,
    _route_pcb,
)
from mcp_server.pipeline import (
    _enrich_code_exceptions,
    _infer_crash_stage,
    _manufacturing_metrics,
    run_pipeline,
    run_pipeline_code,
)
from mcp_server.runs import RunStore
from mcp_server import worker as worker_mod
from mcp_server.worker import _lcsc_refs_in_spec, _restore_lcsc_asset
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

    def test_agent_order_puts_errors_before_advisories(self):
        advisory = DesignException(
            id="e-high-congestion",
            code=ExcCode.HIGH_CONGESTION,
            severity=Severity.ADVISORY,
            message="layout congestion is high",
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.ACCEPT_ADVISORY,
                    params={},
                    human_summary="accept advisory",
                )
            ],
        )
        error = DesignException(
            id="e-route-timeout",
            code=ExcCode.ROUTE_TIMEOUT,
            severity=Severity.ERROR,
            message="routing timed out",
            candidates=[
                Candidate(
                    id="c1",
                    action=ActionType.REGENERATE,
                    params={"run_options": {"route_timeout_s": 240}},
                    human_summary="retry routing longer",
                )
            ],
        )

        ordered = order_exceptions_for_agent([advisory, error])

        assert [exc.code for exc in ordered] == [
            ExcCode.ROUTE_TIMEOUT,
            ExcCode.HIGH_CONGESTION,
        ]

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
        assert "@subcircuit" in exceptions[0].retry_hint
        assert "larger outline_mm" in exceptions[0].retry_hint


class TestHelpfulFailures:
    def test_lcsc_refs_are_detected_in_python_specs(self):
        spec = {
            "_mode": "skidl_python",
            "code": 'u1 = Part("C8734", "STM32F103C8T6"); u1.lcsc = "C8734"',
        }

        assert _lcsc_refs_in_spec(spec) == {"C8734"}

    def test_restore_lcsc_asset_writes_easyeda_cache(self, monkeypatch, tmp_path):
        cache = tmp_path / "easyeda_cache"
        monkeypatch.setattr(worker_mod, "EASYEDA_CACHE", cache)
        row = {
            "meta": {
                "lcsc": "C8734",
                "library": "C8734",
                "symbol": "STM32F103C8T6",
                "footprint": "C8734:LQFP-48_L7.0-W7.0-P0.50",
                "sym_file": str(cache / "C8734" / "C8734.kicad_sym"),
                "fp_dir": str(cache / "C8734" / "C8734.pretty"),
            },
            "sym_data": b"(kicad_symbol_lib)",
            "fp_data": b"(footprint)",
            "step_data": b"STEP",
        }

        assert _restore_lcsc_asset("C8734", row) is True
        assert (cache / "C8734" / "meta.json").exists()
        assert (cache / "C8734" / "C8734.kicad_sym").read_bytes() == b"(kicad_symbol_lib)"
        assert (
            cache / "C8734" / "C8734.pretty" / "LQFP-48_L7.0-W7.0-P0.50.kicad_mod"
        ).read_bytes() == b"(footprint)"
        assert (cache / "C8734" / "C8734.3dshapes" / "C8734.step").read_bytes() == b"STEP"

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

    def test_stderr_pin_context_adds_rgb_led_family_hint(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="ValueError: No pins found using LED_ARGB:D1[('R',)]",
        )
        code = "\n".join([
            "from skidl import *",
            "led = Part('Device', 'LED_ARGB')",
            "red = Net('RED'); red += led['R']",
        ])
        stderr = (
            "ERROR: No pins found using LED_ARGB:D1[('R',)] "
            "@ [/app/mcp_server/engine_worker.py:627=>/tmp/run/<string>:3]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert exc.subject["part"] == "LED_ARGB"
        assert exc.subject["pin"] == "R"
        assert exc.subject["pin_family_hint"]
        assert "RA/RK" in exc.retry_hint
        assert "RK/GK/BK" in exc.retry_hint

    def test_stderr_pin_context_overrides_wrong_line_inference(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="pin 'VCC' not found on U1 (ATtiny102-M)",
            subject={
                "ref": "U1",
                "pin": "VCC",
                "part": "ATtiny102-M",
                "variable": "mcu",
                "available_pins": ["VCC", "PA0"],
                "suggested_pins": ["VCC"],
            },
        )
        code = "\n".join([
            "from skidl import *",
            "vcc += mcu['VCC'], display['VCC'], battery['VOUT']",
        ])
        stderr = (
            "ERROR: No pins found using HY1602E:DS1[('VCC',)] "
            "@ [/app/mcp_server/engine_worker.py:906=>/tmp/run/<string>:2]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert exc.message == "pin 'VCC' not found on DS1 (HY1602E) while executing SKiDL code"
        assert exc.subject["ref"] == "DS1"
        assert exc.subject["part"] == "HY1602E"
        assert exc.subject["line"] == 2
        assert "available_pins" not in exc.subject
        assert "suggested_pins" not in exc.subject
        assert "variable" not in exc.subject

    def test_stderr_numeric_pin_context_overrides_wrong_line_inference(self):
        exc = DesignException(
            id="e-code",
            code=ExcCode.CODE_EXEC_ERROR,
            severity=Severity.FATAL,
            message="pin 'VCC' not found on DS1 (OLED-128O064D)",
            subject={
                "ref": "DS1",
                "pin": "VCC",
                "part": "OLED-128O064D",
                "variable": "display",
                "available_pins": ["GND", "VCC", "SCL", "SDA"],
                "suggested_pins": ["VCC"],
            },
        )
        code = "\n".join([
            "from skidl import *",
            "vcc += encoder[1], button[1], display['VCC'], buzzer[1], c1[1]",
        ])
        stderr = (
            "ERROR: No pins found using RotaryEncoder_Switch:SW1[(1,)] "
            "@ [/app/mcp_server/engine_worker.py:972=>/tmp/run/<string>:2]"
        )

        _enrich_code_exceptions([exc], stderr=stderr, code=code)

        assert (
            exc.message
            == "pin '1' not found on SW1 (RotaryEncoder_Switch) while executing SKiDL code"
        )
        assert exc.subject["ref"] == "SW1"
        assert exc.subject["pin"] == "1"
        assert exc.subject["part"] == "RotaryEncoder_Switch"
        assert exc.subject["line"] == 2
        assert "encoder[1]" in exc.subject["line_text"]
        assert "available_pins" not in exc.subject
        assert "suggested_pins" not in exc.subject
        assert "variable" not in exc.subject

    def test_missing_symbol_library_error_is_specific(self):
        class FakeExecError:
            original = FileNotFoundError("Can't open file: Connector_USB.\n")
            line = 7
            line_text = 'usb = Part("Connector_USB", "USB_C_Receptacle")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == "symbol library 'Connector_USB' is not available to SKiDL"
        assert exc.subject["missing_library"] == "Connector_USB"
        assert "search_kicad" in exc.retry_hint
        assert "guessed symbol library names" in exc.retry_hint

    def test_missing_testpoint_library_suggests_connector_symbol(self):
        class FakeExecError:
            original = FileNotFoundError("Can't open file: TestPoint.\n")
            line = 12
            line_text = 'tp = Part("TestPoint", "TestPoint_Pad_D1.5mm")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == "symbol library 'TestPoint' is not available to SKiDL"
        assert exc.subject["missing_library"] == "TestPoint"
        assert exc.subject["suggested_usage"].startswith('Part("Connector", "TestPoint"')
        assert "TestPoint is a KiCad footprint library" in exc.retry_hint
        assert "Connector:TestPoint" in exc.retry_hint

    def test_missing_optodevice_library_suggests_isolator_search(self):
        class FakeExecError:
            original = FileNotFoundError("Can't open file: OptoDevice.\n")
            line = 10
            line_text = 'opto = Part("OptoDevice", "6N138")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == "symbol library 'OptoDevice' is not available to SKiDL"
        assert exc.subject["suggested_search"] == 'search_kicad("6N138 optocoupler", detail=true)'
        assert "Isolator" in exc.retry_hint

    def test_missing_symbol_part_error_is_specific(self):
        class FakeExecError:
            original = ValueError("Unable to find part LCD_16x2 in library Display_Character.")
            line = 10
            line_text = 'display = Part("Display_Character", "LCD_16x2")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.message == (
            "part 'LCD_16x2' was not found in symbol library 'Display_Character'"
        )
        assert exc.subject["missing_part"] == "LCD_16x2"
        assert exc.subject["library"] == "Display_Character"
        assert "exact returned library and part names" in exc.retry_hint

    def test_missing_symbol_part_error_suggests_close_search_matches(self, monkeypatch):
        from llm import kicad_index
        from llm.kicad_index import SymbolEntry

        monkeypatch.setattr(
            kicad_index,
            "search_symbols",
            lambda query, limit=5: [
                SymbolEntry(
                    lib="Device",
                    name="R_Potentiometer",
                    description="Potentiometer",
                    pin_count=3,
                )
            ]
            if "Potentiometer" in query
            else [],
        )

        class FakeExecError:
            original = ValueError("Unable to find part Potentiometer in library Device.")
            line = 29
            line_text = 'pot1 = Part("Device", "Potentiometer")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["suggested_parts"][0]["library"] == "Device"
        assert exc.subject["suggested_parts"][0]["part"] == "R_Potentiometer"
        assert 'Part("Device", "R_Potentiometer"' in exc.subject["suggested_parts"][0]["usage"]
        assert "subject.suggested_parts" in exc.retry_hint

    def test_missing_stm32_order_code_mentions_package_family_symbols(self):
        class FakeExecError:
            original = ValueError(
                "Unable to find part STM32F103C8T6 in library MCU_ST_STM32F1."
            )
            line = 4
            line_text = 'u1 = Part("MCU_ST_STM32F1", "STM32F103C8T6")'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["part_number_style"].startswith("STM32 order codes")
        assert "package-family" in exc.retry_hint
        assert "convert_lcsc" in exc.retry_hint

    def test_switched_audio_jack_pin_error_explains_pin_family(self):
        jack = SimpleNamespace(
            ref="J1",
            name="AudioJack3_Dual_Ground_Switch",
            pins=[
                SimpleNamespace(name="~", num="G"),
                SimpleNamespace(name="~", num="S1"),
                SimpleNamespace(name="~", num="SN1"),
                SimpleNamespace(name="~", num="R1"),
                SimpleNamespace(name="~", num="RN1"),
                SimpleNamespace(name="~", num="T1"),
                SimpleNamespace(name="~", num="TN1"),
                SimpleNamespace(name="~", num="T2"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using J1['T']")
            line = 12
            line_text = "focus += jack['T']"
            namespace = {"jack": jack}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["pin"] == "T"
        assert "pin_family_hint" in exc.subject
        assert "Close valid pin names" in exc.retry_hint
        assert "plain 'T' is not a valid pin" in exc.subject["pin_family_hint"]
        assert "T1/T2" in exc.retry_hint
        assert "simpler AudioPlug/AudioJack" in exc.retry_hint

    def test_rgb_led_pin_error_explains_channel_pins(self):
        led = SimpleNamespace(
            ref="D1",
            name="LED_ARGB",
            pins=[
                SimpleNamespace(name="~", num="A"),
                SimpleNamespace(name="~", num="RK"),
                SimpleNamespace(name="~", num="GK"),
                SimpleNamespace(name="~", num="BK"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using D1['R']")
            line = 8
            line_text = "red += led['R']"
            namespace = {"led": led}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "R"
        assert "pin_family_hint" in exc.subject
        assert "RK" in exc.retry_hint
        assert "A=anode, K=cathode" in exc.retry_hint

    def test_pico_usb_pin_error_explains_module_boundary(self):
        pico = SimpleNamespace(
            ref="A1",
            name="RaspberryPi_Pico",
            pins=[
                SimpleNamespace(name="GPIO0", num="1"),
                SimpleNamespace(name="VBUS", num="40"),
                SimpleNamespace(name="GND", num="38"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using A1['USB_DP']")
            line = 10
            line_text = "usb_dp += pico['USB_DP']"
            namespace = {"pico": pico}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "USB_DP"
        assert "pin_family_hint" in exc.subject
        assert "complete module with onboard USB" in exc.retry_hint
        assert "raw RP2040" in exc.retry_hint

    def test_bme280_i2c_pin_error_explains_spi_aliases(self):
        bme = SimpleNamespace(
            ref="U1",
            name="BME280",
            pins=[
                SimpleNamespace(name="SDI", num="6"),
                SimpleNamespace(name="SCK", num="4"),
                SimpleNamespace(name="SDO", num="5"),
                SimpleNamespace(name="CSB", num="2"),
                SimpleNamespace(name="GND", num="1"),
                SimpleNamespace(name="VDD", num="8"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using U1['SDA']")
            line = 31
            line_text = 'sda += bme["SDA"]'
            namespace = {"bme": bme}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "SDA"
        assert "pin_family_hint" in exc.subject
        assert "SDI for I2C SDA" in exc.retry_hint
        assert "CSB high" in exc.retry_hint

    def test_numeric_relay_pin_error_warns_against_semantic_aliases(self):
        relay = SimpleNamespace(
            ref="K1",
            name="EC2-3NU",
            pins=[
                SimpleNamespace(name="~", num="1"),
                SimpleNamespace(name="~", num="3"),
                SimpleNamespace(name="~", num="4"),
                SimpleNamespace(name="~", num="5"),
                SimpleNamespace(name="~", num="8"),
                SimpleNamespace(name="~", num="9"),
                SimpleNamespace(name="~", num="10"),
                SimpleNamespace(name="~", num="12"),
            ],
        )

        class FakeExecError:
            original = ValueError("No pins found using K1['Coil+']")
            line = 47
            line_text = 'jack_signal += relay["Coil+"]'
            namespace = {"relay": relay}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.subject["pin"] == "Coil+"
        assert "pin_family_hint" in exc.subject
        assert "numeric package pins only" in exc.retry_hint
        assert "Coil+/Coil-/COM/NO/NC" in exc.retry_hint

    def test_missing_footprint_error_suggests_replacements(self, monkeypatch):
        from llm import kicad_index

        monkeypatch.setattr(
            kicad_index,
            "search_footprints",
            lambda query, limit=5: [
                "Connector_USB:USB_Micro-B_Molex-105017-0001",
                "Connector_USB:USB_Micro-B_Amphenol_10118193-0001LF_Horizontal",
            ]
            if "Micro" in query or "USB_B" in query or "USB" in query
            else [],
        )
        circuit = SimpleNamespace(
            parts=[
                SimpleNamespace(ref="J5", footprint="Connector_USB:USB_B_Micro"),
            ]
        )

        exc = _footprint_missing_exception(
            FileNotFoundError("INCOMPLETE PCB: 1/12 parts missing footprints: J5"),
            circuit,
        )

        assert exc.code == ExcCode.FOOTPRINT_MISSING
        assert exc.subject["suggested_footprints"]["J5"][0].startswith(
            "Connector_USB:USB_Micro-B"
        )
        assert exc.candidates[0].action == ActionType.REPLACE_FOOTPRINT
        assert exc.candidates[0].params == {
            "old": "Connector_USB:USB_B_Micro",
            "new": "Connector_USB:USB_Micro-B_Molex-105017-0001",
        }
        assert "subject.suggested_footprints" in exc.retry_hint

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

    def test_after_pcb_crash_maps_to_post_artifact_failure(self):
        exc = crash_exception(
            "worker exited with status -11",
            "segmentation fault after writing PCB",
            stage="after_pcb_write",
            artifact_keys=["board.kicad_pcb", "board.kicad_sch"],
        )

        assert exc.code == ExcCode.POST_ARTIFACT_FAILURE
        assert exc.severity == Severity.ERROR
        assert exc.subject["stage"] == "after_pcb_write"
        assert exc.subject["partial_artifacts"] == [
            "board.kicad_pcb",
            "board.kicad_sch",
        ]
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert "Fetch the run artifacts" in exc.retry_hint

    def test_post_artifact_failure_metrics_are_definitive(self):
        exc = DesignException(
            id="e-post",
            code=ExcCode.POST_ARTIFACT_FAILURE,
            severity=Severity.ERROR,
            message="post artifact failure",
        )

        metrics = _manufacturing_metrics({}, [exc], ok=False)

        assert metrics["manufacturable"] is False
        assert metrics["manufacturing_complete"] is False

    def test_valid_pin_in_bad_net_expression_is_not_reported_missing(self):
        class FakePin:
            def __init__(self, name, num):
                self.name = name
                self.num = num
                self.aliases = []

        class FakePart:
            ref = "U2"
            name = "ATmega328P-A"
            value = "ATmega328P-A"
            pins = [FakePin("PB4", "18"), FakePin("GND", "8")]

        class FakeExecError:
            original = TypeError(
                "unsupported operand type(s) for +: 'Net' and 'Pin'"
            )
            line = 80
            line_text = 'prog[1] += Net("MISO") + mcu["PB4"]'
            namespace = {"mcu": FakePart()}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert "does not connect a Net and Pin" in exc.message
        assert "pin 'PB4' not found" not in exc.message
        assert exc.subject["pin"] == "PB4"
        assert "net += pin1, pin2" in exc.retry_hint

    def test_syntax_error_reports_line_and_skidl_connection_hint(self):
        err = SyntaxError(
            "'function call' is an illegal expression for augmented assignment",
            ("<string>", 7, 1, 'make_net()[0] += usb["CC1"]\n'),
        )

        exc = _code_exception_from_syntax(err)

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["line"] == 7
        assert exc.subject["line_text"] == 'make_net()[0] += usb["CC1"]'
        assert "net += pin1, pin2" in exc.retry_hint
        assert "no global `connect()` helper" in exc.retry_hint

    def test_connect_name_error_gets_skidl_specific_hint(self):
        class FakeExecError:
            original = NameError("name 'connect' is not defined")
            line = 11
            line_text = 'connect(usb["CC1"], r_cc1[1])'
            namespace = {}

        exc = _code_exception_from_exec(FakeExecError())

        assert exc.code == ExcCode.CODE_EXEC_ERROR
        assert exc.subject["line"] == 11
        assert "does not provide a global connect() helper" in exc.retry_hint
        assert "vcc += u1['VCC'], c1[1]" in exc.retry_hint


class TestRoutingExceptions:
    def test_drc_unconnected_includes_examples_refs_and_retry_hint(self):
        report = {
            "unconnected_items": [
                {
                    "items": [
                        {"description": "Pad 1 [GND] of J1"},
                        {"description": "Track end [GND] near U2"},
                    ],
                    "pos": {"x": 10.0, "y": 12.5},
                },
                {
                    "items": [
                        {"description": "Pad 2 [SDA] of U3"},
                    ],
                },
            ],
            "violations": [],
        }

        exc = _drc_to_exceptions(report)[0]

        assert exc.code == ExcCode.DRC_UNCONNECTED
        assert exc.subject["nets"] == {"GND": 2, "SDA": 1}
        assert exc.subject["refs"] == ["J1", "U2", "U3"]
        assert exc.subject["examples"][0]["pos"] == {"x": 10.0, "y": 12.5}
        assert "not manufacturable" in exc.retry_hint
        assert "subject.examples" in exc.retry_hint

    def test_drc_short_hotspot_points_to_package_or_footprint(self):
        report = {
            "unconnected_items": [],
            "violations": [
                {
                    "type": "short",
                    "items": [
                        {"description": "Pad 8 [GND] of U2 on F.Cu"},
                        {"description": "Pad 9 [VCC] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "short",
                    "items": [
                        {"description": "Pad 8 [GND] of U2 on F.Cu"},
                        {"description": "Pad 10 [CLOCK] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "short",
                    "items": [
                        {"description": "Pad 8 [GND] of U2 on F.Cu"},
                        {"description": "Pad 11 [RESET] of U2 on F.Cu"},
                    ],
                },
            ],
        }

        exc = next(e for e in _drc_to_exceptions(report) if e.code == ExcCode.DRC_SHORT)

        assert exc.subject["refs"] == ["U2"]
        assert exc.subject["nets"]["GND"] == 3
        assert "mismatched symbol/footprint package" in exc.retry_hint
        assert "unrelated circuitry" in exc.retry_hint

    def test_drc_clearance_hotspot_points_to_package_or_footprint(self):
        report = {
            "unconnected_items": [],
            "violations": [
                {
                    "type": "clearance",
                    "items": [
                        {"description": "Pad 1 [VCC] of U2 on F.Cu"},
                        {"description": "Pad 2 [<no net>] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "clearance",
                    "items": [
                        {"description": "Pad 1 [VCC] of U2 on F.Cu"},
                        {"description": "Pad 3 [GND] of U2 on F.Cu"},
                    ],
                },
                {
                    "type": "clearance",
                    "items": [
                        {"description": "Pad 1 [VCC] of U2 on F.Cu"},
                        {"description": "Pad 4 [GPIO] of U2 on F.Cu"},
                    ],
                },
            ],
        }

        exc = next(e for e in _drc_to_exceptions(report) if e.code == ExcCode.DRC_CLEARANCE)

        assert exc.subject["refs"] == ["U2"]
        assert exc.subject["nets"] == {"VCC": 3, "GND": 1, "GPIO": 1}
        assert "too-tight package" in exc.retry_hint

    def test_route_timeout_suggests_router_budget_retry(self, monkeypatch, tmp_path):
        original_exists = Path.exists

        def fake_exists(path: Path) -> bool:
            if str(path) == "/opt/freerouting/freerouting-2.0.1.jar":
                return True
            return original_exists(path)

        def fake_run(cmd, **kwargs):
            if cmd[0] == sys.executable:
                (tmp_path / "timeout.dsn").write_text("(dsn)")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            assert kwargs["timeout"] == 120
            raise subprocess.TimeoutExpired(cmd, kwargs["timeout"])

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/java")
        monkeypatch.setattr(Path, "exists", fake_exists)
        monkeypatch.setattr("subprocess.run", fake_run)

        exceptions = _route_pcb(str(tmp_path / "timeout.kicad_pcb"), timeout_s=120)

        assert len(exceptions) == 1
        exc = exceptions[0]
        assert exc.code == ExcCode.ROUTE_TIMEOUT
        assert exc.candidates[0].action == ActionType.REGENERATE
        assert exc.candidates[0].params == {
            "run_options": {"route_timeout_s": 240.0},
        }
        assert "route_timeout_s=240" in exc.retry_hint

    def test_dsn_export_segfault_is_route_unavailable(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _export_dsn_with_pcbnew(
            str(tmp_path / "segfault.kicad_pcb"),
            str(tmp_path / "segfault.dsn"),
        )

        assert exc is not None
        assert exc.code == ExcCode.ROUTE_UNAVAILABLE
        assert exc.subject["stage"] == "dsn_export"
        assert exc.subject["returncode"] == -11
        assert "signal 11" in exc.message
        assert "routing/export tool failure" in exc.retry_hint

    def test_dsn_export_signal_retries_without_footprint_zones(self, monkeypatch, tmp_path):
        pcb_path = tmp_path / "zone-crash.kicad_pcb"
        dsn_path = tmp_path / "zone-crash.dsn"
        pcb_path.write_text(
            '(kicad_pcb\n'
            '  (version 20241229)\n'
            '  (footprint "USB_Test"\n'
            '    (uuid "11111111-1111-1111-1111-111111111111")\n'
            '    (zone\n'
            '      (net 0)\n'
            '      (net_name "")\n'
            '      (layer "F.Cu")\n'
            '      (uuid "22222222-2222-2222-2222-222222222222")\n'
            '      (polygon (pts (xy 0 0) (xy 1 0) (xy 1 1))))\n'
            '    (pad "1" smd rect (at 0 0) (size 1 1) (layers "F.Cu"))))\n'
        )
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if len(calls) == 1:
                return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")
            assert ".dsn_export_sanitized.kicad_pcb" in cmd[2]
            dsn_path.write_text("(dsn)")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _export_dsn_with_pcbnew(str(pcb_path), str(dsn_path))

        assert exc is None
        assert len(calls) == 2
        sanitized = tmp_path / "zone-crash.dsn_export_sanitized.kicad_pcb"
        assert sanitized.exists()
        assert "(zone" not in sanitized.read_text()

    def test_ses_import_segfault_is_route_unavailable(self, monkeypatch, tmp_path):
        def fake_run(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")

        monkeypatch.setattr("subprocess.run", fake_run)

        exc = _import_ses_with_pcbnew(
            str(tmp_path / "routed.kicad_pcb"),
            str(tmp_path / "routed.ses"),
        )

        assert exc is not None
        assert exc.code == ExcCode.ROUTE_UNAVAILABLE
        assert exc.subject["stage"] == "ses_import"
        assert exc.subject["returncode"] == -11
        assert "signal 11" in exc.message

    def test_route_import_segfault_does_not_crash_worker(self, monkeypatch, tmp_path):
        original_exists = Path.exists
        pcb_path = tmp_path / "import-crash.kicad_pcb"
        dsn_path = tmp_path / "import-crash.dsn"
        ses_path = tmp_path / "import-crash.ses"

        def fake_exists(path: Path) -> bool:
            if str(path) == "/opt/freerouting/freerouting-2.0.1.jar":
                return True
            return original_exists(path)

        def fake_run(cmd, **kwargs):
            if cmd[0] == sys.executable and "ExportSpecctraDSN" in cmd[2]:
                dsn_path.write_text("(dsn)")
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if cmd[0] == "/usr/bin/java":
                ses_path.write_text("(ses)")
                return subprocess.CompletedProcess(cmd, 0, "0 unrouted", "")
            if cmd[0] == sys.executable and "ImportSpecctraSES" in cmd[2]:
                return subprocess.CompletedProcess(cmd, -11, "", "segmentation fault")
            raise AssertionError(cmd)

        monkeypatch.setattr("shutil.which", lambda name: "/usr/bin/java")
        monkeypatch.setattr(Path, "exists", fake_exists)
        monkeypatch.setattr("subprocess.run", fake_run)

        exceptions = _route_pcb(str(pcb_path), timeout_s=120)

        assert len(exceptions) == 1
        exc = exceptions[0]
        assert exc.code == ExcCode.ROUTE_UNAVAILABLE
        assert exc.subject["stage"] == "ses_import"
        assert exc.subject["returncode"] == -11


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

    def test_python_mode_handles_mounting_holes_and_test_points(self, tmp_path):
        code = """
from skidl import *
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
j1 = Part("Connector_Generic", "Conn_01x02",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
r1 = Part("Device", "R", value="1K", footprint="Resistor_SMD:R_0603_1608Metric")
tp_vcc = Part("Connector", "TestPoint",
              footprint="TestPoint:TestPoint_Pad_D1.5mm")
tp_gnd = Part("Connector", "TestPoint",
              footprint="TestPoint:TestPoint_Pad_D1.5mm")
mh1 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
mh2 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
vcc += j1[1], r1[1], tp_vcc[1]
gnd += j1[2], r1[2], tp_gnd[1]
"""

        response = run_pipeline_code(
            code,
            board_name="mechanical-parts",
            outline_mm=[40.0, 25.0],
            out_dir=tmp_path,
            timeout_s=120,
        )

        assert response.stage != "engine_worker"
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
