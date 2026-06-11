from __future__ import annotations

import pytest

from skidl.layout.constraints import (
    BoardOutline,
    EdgeAnchor,
    FixedPosition,
    LayoutConstraints,
)
from skidl.layout.engine import LayoutResult, _footprint_names, plan_layout
from skidl.layout.geometry import FootprintGeometry, PadGeometry


class _Net:
    def __init__(self, name):
        self.name = name
        self._pins = []

    def get_pins(self):
        return self._pins


class _Pin:
    def __init__(self, part, net):
        self.part = part
        self.net = net
        net._pins.append(self)


class _Part:
    def __init__(self, ref, value="", footprint="", name="", nets=None, pins=2):
        self.ref = ref
        self.value = value
        self.footprint = footprint
        self.name = name
        self.node = None
        self.pins = []
        for net in nets or []:
            self.pins.append(_Pin(self, net))
        while len(self.pins) < pins:
            self.pins.append(_Pin(self, _Net(f"{ref}_N{len(self.pins)}")))

    def __len__(self):
        return len(self.pins)


class _Circuit:
    def __init__(self, parts, nets):
        self.parts = parts
        self.nets = nets

    def get_nets(self):
        return self.nets


BBOXES = {
    "Package_QFP:MCU": (12.0, 12.0),
    "Capacitor:C_0805": (2.0, 1.25),
    "Connector:USB": (10.0, 5.0),
}


def _circuit():
    vbus = _Net("VBUS")
    vcc = _Net("3V3")
    gnd = _Net("GND")
    u1 = _Part("U1", name="MCU", footprint="Package_QFP:MCU", nets=[vcc, gnd], pins=2)
    c1 = _Part("C1", value="100nF", footprint="Capacitor:C_0805", nets=[vcc, gnd])
    j1 = _Part("J1", name="USB connector", footprint="Connector:USB", nets=[vbus, gnd])
    return _Circuit([u1, c1, j1], [vbus, vcc, gnd])


def test_footprint_names_accepts_foot_alias():
    part = _Part("R1", name="resistor")
    part.foot = "Device:R"
    circuit = _Circuit([part], [])

    assert _footprint_names(circuit) == {"Device:R"}


def test_plan_layout_derives_outline_scores_and_power_plan():
    result = plan_layout(
        _circuit(),
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(fixed=[FixedPosition("U1", 30.0, 30.0)]),
        board_layers=4,
    )

    assert isinstance(result, LayoutResult)
    assert result.outline is not None
    assert result.validation.placed_parts == 3
    assert result.validation.missing_refs == []
    assert result.score.power_net_count == 3
    assert result.power_plan.net("GND") is not None
    assert any(
        intent.strategy == "plane" for intent in result.power_plan.route_intents
    )


def test_plan_layout_reads_existing_board_outline(tmp_path):
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text(
        """
(kicad_pcb
  (gr_rect
    (start 10 20)
    (end 110 90)
    (layer "Edge.Cuts")
    (stroke (width 0.1))
  )
)
"""
    )

    result = plan_layout(
        _circuit(),
        fp_bboxes=BBOXES,
        existing_pcb_path=str(pcb),
    )

    assert result.outline is not None
    assert result.outline.x_min == 10.0
    assert result.outline.y_min == 20.0
    assert result.outline.x_max == 110.0
    assert result.outline.y_max == 90.0


def test_plan_layout_prefers_explicit_outline_over_existing_board(tmp_path):
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text(
        """
(kicad_pcb
  (gr_rect (start 10 20) (end 110 90) (layer "Edge.Cuts"))
)
"""
    )

    explicit = BoardOutline(50.0, 40.0)
    result = plan_layout(
        _circuit(),
        fp_bboxes=BBOXES,
        outline=explicit,
        existing_pcb_path=str(pcb),
    )

    assert result.outline is explicit
    assert result.outline.width_mm == 50.0
    assert result.outline.height_mm == 40.0


def test_plan_layout_returns_candidates_report_and_preserves_edge_anchors():
    outline = BoardOutline(100.0, 60.0)
    result = plan_layout(
        _circuit(),
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(
            outline=outline,
            edge_anchors=[EdgeAnchor("J1", "bottom", offset_mm=50.0, rot_deg=180.0)],
        ),
    )

    names = [candidate.name for candidate in result.candidates]
    j1 = next(placed for placed in result.placed_parts if placed.ref == "J1")
    _, h = BBOXES[j1.footprint]

    assert names[:5] == [
        "baseline",
        "connector_edge_first",
        "power_first",
        "power_topology_first",
        "cluster_first",
    ]
    assert result.report.selected in names
    assert result.intent_plan is not None
    assert result.report.part_reasons["J1"]
    assert result.report.power_corridors
    assert j1.x_mm == 50.0
    # courtyard bottom edge sits 0.5mm inside the board edge (default inset)
    assert j1.y_mm + h / 2 == pytest.approx(outline.y_max - 0.5)
    assert j1.rot_deg == 180.0


def test_plan_layout_reports_power_topology_chain():
    vbus = _Net("VBUS")
    vcc = _Net("VCC")
    gnd = _Net("GND")
    sig = _Net("SIG")
    j1 = _Part("J1", name="USB connector", footprint="Connector:USB", nets=[vbus, gnd])
    u2 = _Part(
        "U2",
        name="LDO regulator",
        footprint="Package_TO_SOT:SOT23",
        nets=[vbus, gnd, vcc],
        pins=3,
    )
    c1 = _Part("C1", value="100nF", footprint="Capacitor:C_0805", nets=[vcc, gnd])
    u1 = _Part("U1", name="MCU", footprint="Package_QFP:MCU", nets=[vcc, gnd, sig], pins=3)
    circuit = _Circuit([j1, u2, c1, u1], [vbus, vcc, gnd, sig])

    result = plan_layout(
        circuit,
        fp_bboxes={
            **BBOXES,
            "Package_TO_SOT:SOT23": (3.0, 3.0),
        },
        constraints=LayoutConstraints(outline=BoardOutline(100.0, 60.0)),
    )

    assert any(candidate.name == "power_topology_first" for candidate in result.candidates)
    assert any("VBUS: J1 -> U2 -> C1 -> U1" in chain for chain in result.report.power_topology)
    assert any(
        "power chain: VBUS from J1" in reason
        for reason in result.report.part_reasons["U2"]
    )


def test_plan_layout_refines_decaps_to_actual_parent_pads(monkeypatch):
    vdd = _Net("VDD")
    gnd = _Net("GND")
    sig = _Net("SIG")
    u1 = _Part(
        "U1",
        name="MCU",
        footprint="Package_QFP:MCU",
        nets=[vdd, gnd, sig],
        pins=3,
    )
    c1 = _Part("C1", value="100nF", footprint="Capacitor:C_0805", nets=[vdd, gnd])
    circuit = _Circuit([u1, c1], [vdd, gnd, sig])
    geometries = {
        "Package_QFP:MCU": FootprintGeometry(
            footprint="Package_QFP:MCU",
            pads=[
                PadGeometry("1", -4.0, -1.5, 0.6, 0.6),
                PadGeometry("2", -4.0, 1.5, 0.6, 0.6),
                PadGeometry("3", 4.0, 0.0, 0.6, 0.6),
            ],
            body_bounds=(-5.0, -5.0, 5.0, 5.0),
        ),
        "Capacitor:C_0805": FootprintGeometry(
            footprint="Capacitor:C_0805",
            pads=[
                PadGeometry("1", -0.6, 0.0, 0.4, 0.4),
                PadGeometry("2", 0.6, 0.0, 0.4, 0.4),
            ],
            body_bounds=(-1.0, -0.6, 1.0, 0.6),
        ),
    }
    monkeypatch.setattr(
        "skidl.layout.engine._resolve_geometries",
        lambda circuit, fp_lib_dirs: geometries,
    )

    result = plan_layout(
        circuit,
        fp_bboxes={
            "Package_QFP:MCU": (10.0, 10.0),
            "Capacitor:C_0805": (2.0, 1.2),
        },
        constraints=LayoutConstraints(
            fixed=[FixedPosition("U1", 20.0, 20.0)],
            outline=BoardOutline(60.0, 40.0),
        ),
    )
    placed = {part.ref: part for part in result.placed_parts}

    assert placed["C1"].x_mm < placed["U1"].x_mm
    assert placed["C1"].rot_deg == 90.0
    assert any(
        "actual U1 VDD/GND pads" in reason
        for reason in result.report.part_reasons["C1"]
    )
