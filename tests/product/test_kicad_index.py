"""Tests for KiCad symbol/footprint search helpers."""

from __future__ import annotations

from llm import kicad_index


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
