"""Tests for KiCad symbol/footprint search helpers."""

from __future__ import annotations

from llm import kicad_index
from llm.kicad_index import SymbolEntry


def _fake_symbols(monkeypatch):
    monkeypatch.setattr(
        kicad_index,
        "_index",
        {
            "Connector": [
                SymbolEntry(
                    lib="Connector",
                    name="DIN-3",
                    description="3-pin DIN connector",
                    keywords="din connector",
                    pin_count=3,
                ),
                SymbolEntry(
                    lib="Connector",
                    name="DIN-5",
                    description="5-pin DIN connector",
                    keywords="din midi connector",
                    pin_count=5,
                ),
                SymbolEntry(
                    lib="Connector",
                    name="USB_C_Receptacle_USB2.0_16P",
                    description="USB Type-C receptacle USB2.0",
                    keywords="usb universal serial bus type-C USB2.0",
                    pin_count=16,
                ),
                SymbolEntry(
                    lib="Connector",
                    name="USB_B_Micro",
                    description="USB Micro-B connector",
                    keywords="usb universal serial bus micro b",
                    pin_count=5,
                ),
            ],
            "Interface_USB": [
                SymbolEntry(
                    lib="Interface_USB",
                    name="FUSB302BMPX",
                    description="Programmable USB Type-C Controller",
                    keywords="usb type-c controller",
                    pin_count=15,
                ),
            ],
            "Connector_Audio": [
                SymbolEntry(
                    lib="Connector_Audio",
                    name="AudioPlug3",
                    description="Audio Jack, 3 Poles (Stereo / TRS)",
                    keywords="audio jack trs stereo",
                    pin_count=3,
                ),
                SymbolEntry(
                    lib="Connector_Audio",
                    name="AudioJack3_Dual_Ground_Switch",
                    description=(
                        "Audio Jack, Dual, 3 Poles (Stereo / TRS), "
                        "Grounded Sleeve, Switched Poles (Normalling)"
                    ),
                    keywords="audio jack trs stereo switched normalling",
                    pin_count=13,
                ),
            ],
            "Switch": [
                SymbolEntry(
                    lib="Switch",
                    name="SW_Push",
                    description="Push button switch, generic, two pins",
                    keywords="switch push button keyboard key",
                    pin_count=2,
                ),
                SymbolEntry(
                    lib="Switch",
                    name="SW_Reed",
                    description="Reed switch",
                    keywords="switch reed magnetic",
                    pin_count=2,
                ),
                SymbolEntry(
                    lib="Switch",
                    name="SW_E3_SA3216",
                    description="SPST tactile switch",
                    keywords="switch tactile push keyboard",
                    pin_count=2,
                ),
            ],
        },
    )


def _fake_footprints(monkeypatch):
    monkeypatch.setattr(
        kicad_index,
        "_footprint_index",
        {
            "Connector_Audio:Jack_3.5mm_PJ320D_Horizontal",
            "Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
            "TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-3-2-5.08_1x02_P5.08mm_Horizontal",
            "TerminalBlock:TerminalBlock_bornier-2_P5.08mm",
            "Potentiometer_THT:Potentiometer_Alps_RK09L_Double_Horizontal",
            "Potentiometer_THT:Potentiometer_Alps_RK09K_Single_Horizontal",
            "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal",
            "Connector_USB:USB_Micro-B_Molex-105017-0001",
            "Connector_DIN:DIN41612_R_2x32_Male_Vertical_THT",
            "Connector_DIN:DIN-5_180degree_Male_Horizontal_THT",
            "Resistor_SMD:R_0603_1608Metric",
        },
    )


def test_search_footprints_finds_35mm_right_angle_audio_jack(monkeypatch):
    _fake_footprints(monkeypatch)

    matches = kicad_index.search_footprints(
        "3.5mm TRS unswitched right angle through hole jack",
        limit=3,
    )

    assert matches[0] == "Connector_Audio:Jack_3.5mm_PJ320D_Horizontal"


def test_search_footprints_finds_screw_terminal_pitch(monkeypatch):
    _fake_footprints(monkeypatch)

    matches = kicad_index.search_footprints("screw terminal 2 pin 5.08mm", limit=3)

    assert matches[0].startswith("TerminalBlock_Phoenix:")
    assert "P5.08mm" in matches[0]


def test_search_footprints_finds_dual_pot_and_usb_c(monkeypatch):
    _fake_footprints(monkeypatch)

    pot_matches = kicad_index.search_footprints("dual gang potentiometer 10k", limit=3)
    usb_matches = kicad_index.search_footprints("USB_C_Receptacle USB2.0 16P", limit=3)

    assert pot_matches[0] == "Potentiometer_THT:Potentiometer_Alps_RK09L_Double_Horizontal"
    assert usb_matches[0] == "Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal"


def test_search_symbols_finds_usb_receptacles_despite_alias_punctuation(monkeypatch):
    _fake_symbols(monkeypatch)

    usb_c = kicad_index.search_symbols("USB-C connector", limit=3)
    micro_b = kicad_index.search_symbols("USB Micro-B connector", limit=3)

    assert usb_c[0].lib == "Connector"
    assert usb_c[0].name == "USB_C_Receptacle_USB2.0_16P"
    assert micro_b[0].lib == "Connector"
    assert micro_b[0].name == "USB_B_Micro"


def test_search_symbols_prefers_switched_audio_jack_when_requested(monkeypatch):
    _fake_symbols(monkeypatch)

    matches = kicad_index.search_symbols("switched 3.5mm TRS jack symbol", limit=3)

    assert matches[0].lib == "Connector_Audio"
    assert matches[0].name == "AudioJack3_Dual_Ground_Switch"


def test_search_symbols_prefers_din_symbol_for_midi_din(monkeypatch):
    _fake_symbols(monkeypatch)

    matches = kicad_index.search_symbols("5-pin DIN MIDI jack footprint", limit=3)

    assert matches[0].lib == "Connector"
    assert matches[0].name == "DIN-5"


def test_search_symbols_prefers_switch_library_for_keyboard_switch(monkeypatch):
    _fake_symbols(monkeypatch)

    matches = kicad_index.search_symbols("mechanical keyboard switch", limit=3)

    assert matches[0].lib == "Switch"
    assert matches[0].name == "SW_Push"


def test_search_footprints_filters_din41612_for_midi_din(monkeypatch):
    _fake_footprints(monkeypatch)

    matches = kicad_index.search_footprints("5-pin DIN MIDI jack footprint", limit=5)

    assert matches[0] == "Connector_DIN:DIN-5_180degree_Male_Horizontal_THT"
    assert all("DIN41612" not in match for match in matches)
    assert all("Connector_Audio" not in match for match in matches)
