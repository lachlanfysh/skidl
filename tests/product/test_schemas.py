"""Acceptance tests for the circuit spec, translator, and corrections."""

import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.enrichment import design_review_exceptions, enrich_blocks
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity
from schemas.translator import translate

SYM_DIR = os.environ["KICAD9_SYMBOL_DIR"]
needs_kicad = pytest.mark.skipif(
    not os.path.isdir(SYM_DIR), reason="KiCad symbol libraries not installed"
)


def mk_spec(parts, nets, board=None):
    return CircuitSpec.model_validate(
        {"board": board or {"name": "test-board"}, "parts": parts, "nets": nets}
    )


GOOD_PARTS = [
    {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115IDGS",
     "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm", "group": "adc"},
    {"ref": "C1", "lib": "Device", "part": "C", "value": "100nF",
     "footprint": "Capacitor_SMD:C_0603_1608Metric", "group": "adc"},
    {"ref": "U2", "lib": None, "part": "CUSTOM_SENSOR",
     "footprint": "Package_TO_SOT_SMD:SOT-23",
     "pins": [{"num": "1", "name": "VCC", "func": "power_in"},
              {"num": "2", "name": "GND", "func": "power_in"},
              {"num": "3", "name": "OUT", "func": "output"}]},
]
GOOD_NETS = [
    {"name": "VCC", "power": True, "pins": ["U1.VDD", "C1.1", "U2.VCC"]},
    {"name": "GND", "power": True, "pins": ["U1.GND", "C1.2", "U2.GND"]},
    {"name": "AIN0", "pins": ["U1.AIN0", "U2.OUT"]},
]


def vbat_spec_dict(parts=None, nets=None):
    return {
        "board": {"name": "vbat-board"},
        "parts": parts or [
            {
                "ref": "J1",
                "lib": "Connector_Generic",
                "part": "Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            }
        ],
        "nets": nets or [
            {"name": "VBAT", "power": True, "pins": ["J1.1"]},
            {"name": "GND", "power": True, "pins": ["J1.2"]},
        ],
    }


class TestSpecValidation:
    def test_duplicate_refs_rejected(self):
        with pytest.raises(ValueError, match="duplicate part refs"):
            mk_spec([{"ref": "U1", "lib": "Device", "part": "R", "footprint": "a:b"},
                     {"ref": "U1", "lib": "Device", "part": "C", "footprint": "a:b"}], [])

    def test_lib_part_requires_no_pins(self):
        with pytest.raises(ValueError, match="only allowed for custom"):
            mk_spec([{"ref": "U1", "lib": "Device", "part": "R", "footprint": "a:b",
                      "pins": [{"num": "1", "name": "X"}]}], [])

    def test_custom_part_requires_pins(self):
        with pytest.raises(ValueError, match="require 'pins'"):
            mk_spec([{"ref": "U1", "footprint": "a:b"}], [])

    def test_bad_pin_ref_format(self):
        with pytest.raises(ValueError, match="REF.PIN"):
            mk_spec([{"ref": "U1", "lib": "Device", "part": "R", "footprint": "a:b"}],
                    [{"name": "N", "pins": ["U1"]}])


class TestEnrichmentIntent:
    def test_vbat_net_alone_does_not_inject_lipo_charger(self):
        enriched, actions = enrich_blocks(vbat_spec_dict(), marketing_text="")

        assert not any(p.get("value") == "MCP73831" for p in enriched["parts"])
        assert not any(a["rule"] == "lipo_charger" for a in actions)

    def test_lipo_marketing_still_injects_charger(self):
        enriched, actions = enrich_blocks(
            vbat_spec_dict(),
            marketing_text="Rechargeable LiPo battery charger input",
        )

        assert any(p.get("value") == "MCP73831" for p in enriched["parts"])
        assert any(a["rule"] == "BLOCK:lipo_charger" for a in actions)

    def test_negated_lipo_marketing_does_not_inject_charger(self):
        enriched, actions = enrich_blocks(
            vbat_spec_dict(),
            marketing_text="VBAT battery input with no battery charger on this board",
        )

        assert not any(p.get("value") == "MCP73831" for p in enriched["parts"])
        assert not any(a["rule"] == "BLOCK:lipo_charger" for a in actions)

    def test_external_3v3_header_does_not_inject_regulator(self):
        spec = {
            "board": {"name": "external-3v3"},
            "parts": [
                {
                    "ref": "J1",
                    "lib": "Connector_Generic",
                    "part": "Conn_01x04",
                    "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                },
                {
                    "ref": "U1",
                    "lib": None,
                    "part": "CUSTOM_SENSOR",
                    "footprint": "Package_TO_SOT_SMD:SOT-23",
                    "pins": [
                        {"num": "1", "name": "VDD"},
                        {"num": "2", "name": "GND"},
                        {"num": "3", "name": "SDA"},
                    ],
                },
            ],
            "nets": [
                {"name": "3V3", "power": True, "pins": ["J1.1", "U1.VDD"]},
                {"name": "GND", "power": True, "pins": ["J1.2", "U1.GND"]},
                {"name": "SDA", "pins": ["J1.3", "U1.SDA"]},
            ],
        }

        enriched, actions = enrich_blocks(
            spec,
            marketing_text="BME280-style sensor breakout powered from external 3.3V header, no regulator",
        )

        assert not any(p.get("value") == "AP2112K-3.3" for p in enriched["parts"])
        assert not any(a["rule"] == "BLOCK:ldo_3v3" for a in actions)

    def test_vbus_to_3v3_marketing_can_inject_regulator(self):
        spec = {
            "board": {"name": "regulated-3v3"},
            "parts": [
                {
                    "ref": "J1",
                    "lib": "Connector_Generic",
                    "part": "Conn_01x03",
                    "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                }
            ],
            "nets": [
                {"name": "VBUS", "power": True, "pins": ["J1.1"]},
                {"name": "GND", "power": True, "pins": ["J1.2"]},
                {"name": "+3V3", "power": True, "pins": ["J1.3"]},
            ],
        }

        enriched, actions = enrich_blocks(
            spec,
            marketing_text="Add a 3.3V regulator from USB VBUS",
        )

        assert any(p.get("value") == "AP2112K-3.3" for p in enriched["parts"])
        assert any(a["rule"] == "BLOCK:ldo_3v3" for a in actions)

    def test_pulldown_does_not_match_ldo_keyword(self):
        exceptions = design_review_exceptions(
            vbat_spec_dict(),
            marketing_text="USB-C CC pulldown resistors only",
        )

        assert not [
            e for e in exceptions
            if e.subject.get("feature") == "Voltage regulator"
        ]

    def test_ambiguous_vbat_becomes_advisory(self):
        exceptions = design_review_exceptions(vbat_spec_dict())

        vbat = [e for e in exceptions if e.subject.get("net") == "VBAT"]
        assert vbat
        assert vbat[0].severity == Severity.ADVISORY
        assert "consider adding a charger" in vbat[0].message

    def test_coin_cell_vbat_does_not_get_charger_advisory(self):
        spec = vbat_spec_dict(parts=[
            {
                "ref": "BT1",
                "lib": "Device",
                "part": "Battery_Cell",
                "value": "CR2032",
                "footprint": "Battery:BatteryHolder_Keystone_1060_1x2032",
            },
            {
                "ref": "J1",
                "lib": "Connector_Generic",
                "part": "Conn_01x02",
                "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
            },
        ], nets=[
            {"name": "VBAT", "power": True, "pins": ["BT1.1", "J1.1"]},
            {"name": "GND", "power": True, "pins": ["BT1.2", "J1.2"]},
        ])

        exceptions = design_review_exceptions(spec)
        assert not [
            e for e in exceptions
            if e.subject.get("feature") == "battery_source_intent"
        ]


@needs_kicad
class TestTranslator:
    def test_happy_path(self):
        res = translate(mk_spec(GOOD_PARTS, GOOD_NETS))
        assert res.ok, [e.message for e in res.exceptions]
        assert sorted(p.ref for p in res.circuit.parts) == ["C1", "U1", "U2"]
        vcc = next(n for n in res.circuit.nets if n.name == "VCC")
        # U1.VDD resolved to the symbol's real pin number
        assert sorted(f"{p.part.ref}.{p.num}" for p in vcc.pins) == ["C1.1", "U1.8", "U2.1"]

    def test_unknown_lib(self):
        res = translate(mk_spec(
            [{"ref": "U1", "lib": "NoSuchLib", "part": "X",
              "footprint": "Package_TO_SOT_SMD:SOT-23"}], []))
        assert not res.ok
        exc = res.exceptions[0]
        assert exc.code == ExcCode.SPEC_UNKNOWN_LIB
        assert exc.candidates and all(c.action == ActionType.REPLACE_LIB for c in exc.candidates)

    def test_unknown_part_suggests_variants(self):
        res = translate(mk_spec(
            [{"ref": "U1", "lib": "Sensor_Temperature", "part": "MCP9808",
              "footprint": "Package_TO_SOT_SMD:SOT-23"}], []))
        exc = res.exceptions[0]
        assert exc.code == ExcCode.SPEC_UNKNOWN_PART
        suggestions = [c.params.get("new") for c in exc.candidates]
        assert "MCP9808_MSOP" in suggestions

    def test_unknown_pin_lists_real_pins(self):
        res = translate(mk_spec(
            [{"ref": "U1", "lib": "MCU_RaspberryPi", "part": "RP2040",
              "footprint": "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm"}],
            [{"name": "VCC", "power": True, "pins": ["U1.VDD"]}]))
        exc = res.exceptions[0]
        assert exc.code == ExcCode.SPEC_UNKNOWN_PIN
        suggestions = [c.params.get("new") for c in exc.candidates]
        assert "IOVDD" in suggestions
        assert "available_pins" in exc.subject

    def test_bad_footprint(self):
        res = translate(mk_spec(
            [{"ref": "R1", "lib": "Device", "part": "R",
              "footprint": "NoSuchLib:TotallyFakeFootprint_XYZ"}], []))
        exc = res.exceptions[0]
        assert exc.code == ExcCode.SPEC_BAD_FOOTPRINT

    def test_unknown_net_ref(self):
        res = translate(mk_spec(
            [{"ref": "U1", "lib": "Device", "part": "R",
              "footprint": "Resistor_SMD:R_0603_1608Metric"}],
            [{"name": "N1", "pins": ["U9.1", "U1.1"]}]))
        assert res.exceptions[0].code == ExcCode.SPEC_MALFORMED

    def test_correction_loop_round_trip(self):
        spec = mk_spec(
            [{"ref": "U1", "lib": "MCU_RaspberryPi", "part": "RP2040",
              "footprint": "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm"}],
            [{"name": "VCC", "power": True, "pins": ["U1.VDD"]}])
        r1 = translate(spec)
        exc = r1.exceptions[0]
        pick = next(c for c in exc.candidates if c.params.get("new") == "IOVDD")
        spec2 = apply_candidate(spec, exc, pick)
        assert spec2.nets[0].pins == ["U1.IOVDD"]
        assert spec.nets[0].pins == ["U1.VDD"]  # purity
        assert translate(spec2).ok


class TestCorrections:
    def _spec(self):
        return mk_spec(
            [{"ref": "U1", "lib": "Device", "part": "R", "footprint": "a:b"},
             {"ref": "U2", "lib": "Device", "part": "C", "footprint": "a:b"}],
            [{"name": "N1", "pins": ["U1.1", "U2.1"]},
             {"name": "N2", "pins": ["U2.2"]}])

    def _exc(self):
        return DesignException(id="e1", code=ExcCode.LAYOUT_OVERLAP,
                               severity=Severity.ERROR, message="m",
                               subject={"pair": ["U1", "U2"]})

    def test_remove_part_cleans_nets(self):
        cand = Candidate(id="c1", action=ActionType.REMOVE_PART,
                         params={"ref": "U2"}, human_summary="")
        out = apply_candidate(self._spec(), self._exc(), cand)
        assert [p.ref for p in out.parts] == ["U1"]
        assert len(out.nets) == 1 and out.nets[0].pins == ["U1.1"]

    def test_scale_outline(self):
        spec = self._spec()
        spec.board.outline_hint_mm = (50.0, 25.0)
        cand = Candidate(id="c1", action=ActionType.SCALE_OUTLINE,
                         params={"area_factor": 1.44}, human_summary="")
        out = apply_candidate(spec, self._exc(), cand)
        assert out.board.outline_hint_mm == (60.0, 30.0)

    def test_negative_corner_radius_rejected(self):
        with pytest.raises(ValueError, match="corner_radius_mm"):
            CircuitSpec.model_validate(
                {"board": {"name": "b", "corner_radius_mm": -1.0}, "parts": [], "nets": []}
            )

    def test_accept_advisory_sets_waiver(self):
        cand = Candidate(id="c1", action=ActionType.ACCEPT_ADVISORY,
                         params={}, human_summary="")
        out = apply_candidate(self._spec(), self._exc(), cand)
        assert out.waivers == ["LAYOUT_OVERLAP:U1+U2"]

    def test_bad_params_raise(self):
        cand = Candidate(id="c1", action=ActionType.REMOVE_PART,
                         params={"ref": "U99"}, human_summary="")
        with pytest.raises(CorrectionError):
            apply_candidate(self._spec(), self._exc(), cand)

    def test_stub_net(self):
        cand = Candidate(id="c1", action=ActionType.STUB_NET,
                         params={"net": "N1"}, human_summary="")
        out = apply_candidate(self._spec(), self._exc(), cand)
        assert out.nets[0].stub is True


@needs_kicad
class TestSymbolAliases:
    """Verify _SYMBOL_ALIASES resolve deprecated/misplaced symbol names."""

    def test_r_pot_alias(self):
        res = translate(mk_spec(
            [{"ref": "RV1", "lib": "Device", "part": "R_POT",
              "footprint": "Potentiometer_THT:Potentiometer_Alps_RK09K_Single_Vertical"}],
            [{"name": "SIG", "pins": ["RV1.1"]},
             {"name": "W", "pins": ["RV1.2"]},
             {"name": "GND", "pins": ["RV1.3"]}]))
        assert res.ok, [e.message for e in res.exceptions]

    def test_r_pot_trim_alias(self):
        res = translate(mk_spec(
            [{"ref": "RV1", "lib": "Device", "part": "R_POT_TRIM",
              "footprint": "Potentiometer_SMD:Potentiometer_Bourns_3214W_SMD"}],
            []))
        assert res.ok, [e.message for e in res.exceptions]

    def test_transistor_pinout_alias(self):
        res = translate(mk_spec(
            [{"ref": "Q1", "lib": "Device", "part": "Q_NPN_BEC",
              "footprint": "Package_TO_SOT_THT:TO-92_Inline"}],
            []))
        assert res.ok, [e.message for e in res.exceptions]

    def test_audio_jack_in_connector_alias(self):
        res = translate(mk_spec(
            [{"ref": "J1", "lib": "Connector", "part": "AudioJack2_SwitchT",
              "footprint": "Connector_Audio:Jack_3.5mm_Ledino_PJ-321_Horizontal"}],
            [{"name": "SIG", "pins": ["J1.T"]},
             {"name": "GND", "pins": ["J1.S"]}]))
        assert res.ok, [e.message for e in res.exceptions]


@needs_kicad
class TestCrossLibrarySearch:
    """Pass 3 should find parts in wrong libraries via cross-library search."""

    def test_lm386_in_wrong_lib(self):
        res = translate(mk_spec(
            [{"ref": "U1", "lib": "Amplifier_Operational", "part": "LM386",
              "footprint": "Package_DIP:DIP-8_W7.62mm"}],
            []))
        assert not res.ok
        exc = res.exceptions[0]
        assert exc.code == ExcCode.SPEC_UNKNOWN_PART
        cross_lib_cands = [c for c in exc.candidates
                           if c.action == ActionType.REPLACE_LIB]
        assert any("Amplifier_Audio" in (c.params.get("new", "") or "")
                    for c in cross_lib_cands)


@needs_kicad
class TestFootprintCandidateRanking:
    """Cross-library footprint candidates must rank by name similarity, not
    library discovery order. Found by the Llama UX probe: requesting
    Package_QFN:QFN-16... offered TQFP-176 (24x24mm!) as c1 because
    Package_QFP is string-closer to Package_QFN than Package_DFN_QFN is.
    """

    BME280_FP = "Package_QFN:QFN-16-1EP_3x3mm_P0.5mm"

    def _exc(self):
        res = translate(mk_spec(
            [{"ref": "U1", "lib": "Device", "part": "C", "value": "1uF",
              "footprint": self.BME280_FP}], []))
        return next(e for e in res.exceptions
                    if e.code == ExcCode.SPEC_BAD_FOOTPRINT)

    def test_same_name_in_sibling_lib_ranks_first(self):
        exc = self._exc()
        fps = [c.params["new"] for c in exc.candidates
               if c.action == ActionType.REPLACE_FOOTPRINT
               and c.source == "deterministic"]
        assert fps, "no deterministic candidates offered"
        assert fps[0].startswith("Package_DFN_QFN:QFN-16-1EP_3x3mm_P0.5mm"), (
            f"best candidate should be the same footprint under its real "
            f"library, got {fps[0]}"
        )

    def test_dissimilar_candidates_below_auto_apply_confidence(self):
        """A 176-pin QFP is not a 0.9-confidence substitute for a 16-pin QFN.
        Anything that isn't a close name match must sit below the documented
        0.8 auto-apply threshold."""
        exc = self._exc()
        for c in exc.candidates:
            if c.action != ActionType.REPLACE_FOOTPRINT or c.source != "deterministic":
                continue
            name = c.params["new"].split(":")[1]
            if not name.startswith("QFN-16"):
                assert c.confidence < 0.8, (
                    f"{c.params['new']} (confidence {c.confidence}) is a "
                    f"dissimilar substitute ranked as auto-applicable"
                )


class TestFormFactorValidation:
    """form_factor must fail fast at spec validation, not deep in the engine.
    Found by the Llama UX probe: form_factor='compact' was accepted silently."""

    def test_unknown_form_factor_rejected(self):
        with pytest.raises(ValueError, match="form_factor"):
            mk_spec([], [], board={"name": "b", "form_factor": "compact"})

    def test_known_form_factors_accepted(self):
        for ff in ("feather", "qt_py", "metro", "metro_mini",
                   "trinket", "itsybitsy", "shield_uno"):
            spec = mk_spec([], [], board={"name": "b", "form_factor": ff})
            assert spec.board.form_factor == ff

    def test_error_message_lists_valid_options(self):
        try:
            mk_spec([], [], board={"name": "b", "form_factor": "bogus"})
        except ValueError as exc:
            assert "feather" in str(exc)
        else:
            pytest.fail("bogus form_factor accepted")
