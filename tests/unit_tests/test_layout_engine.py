from __future__ import annotations

from skidl.layout.constraints import (
    BoardOutline,
    EdgeAnchor,
    FixedPosition,
    LayoutConstraints,
)
from skidl.layout.engine import LayoutResult, plan_layout


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

    assert names[:4] == [
        "baseline",
        "connector_edge_first",
        "power_first",
        "cluster_first",
    ]
    assert result.report.selected in names
    assert result.intent_plan is not None
    assert result.report.part_reasons["J1"]
    assert result.report.power_corridors
    assert j1.x_mm == 50.0
    assert j1.y_mm + h / 2 == outline.y_max
    assert j1.rot_deg == 180.0
