from __future__ import annotations

import pytest

from skidl.layout.constraints import (
    BoardOutline,
    DistributeConstraint,
    EdgeAnchor,
    FixedPosition,
    LayoutConstraints,
)
from skidl.layout.engine import LayoutResult, _footprint_names, plan_layout
from skidl.layout.geometry import FootprintGeometry, PadGeometry
from skidl.layout.placer import derive_outline


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
    "MountingHole:M2": (4.4, 4.4),
    "Connector:PinHeader_1x04": (2.54, 10.16),
    "Connector:PinHeader_1x06": (2.54, 15.24),
    "Connector_Audio:Thonkiconn_PJ398SM": (8.0, 8.0),
    "Connector_Audio:Jack_3.5mm_PJ320D_Horizontal": (14.0, 10.0),
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


def test_plan_layout_auto_outline_stays_near_placed_envelope():
    result = plan_layout(
        _circuit(),
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(fixed=[FixedPosition("U1", 30.0, 30.0)]),
    )

    assert result.outline is not None
    envelope = derive_outline(result.placed_parts, BBOXES)
    outline_area = result.outline.width_mm * result.outline.height_mm
    envelope_area = envelope.width_mm * envelope.height_mm

    assert outline_area <= envelope_area * 1.35 + 0.001


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


def test_plan_layout_keeps_inferred_pin_header_on_auto_outline_edge():
    vcc = _Net("3V3")
    gnd = _Net("GND")
    sig = _Net("SDA")
    u1 = _Part(
        "U1",
        name="sensor IC",
        footprint="Package_QFP:MCU",
        nets=[vcc, gnd, sig],
        pins=3,
    )
    j1 = _Part(
        "J1",
        name="pin header",
        footprint="Connector:PinHeader_1x06",
        nets=[vcc, gnd, sig],
        pins=6,
    )
    circuit = _Circuit([u1, j1], [vcc, gnd, sig])

    result = plan_layout(circuit, fp_bboxes=BBOXES)

    placed = {part.ref: part for part in result.placed_parts}
    j1 = placed["J1"]
    width, height = BBOXES[j1.footprint]
    if j1.rot_deg % 180 == 90:
        width, height = height, width
    bounds = (
        j1.x_mm - width / 2,
        j1.y_mm - height / 2,
        j1.x_mm + width / 2,
        j1.y_mm + height / 2,
    )

    assert result.outline is not None
    assert result.report.selected != "baseline"
    assert j1.x_mm == pytest.approx(
        (result.outline.x_min + result.outline.x_max) / 2
    )
    assert bounds[3] == pytest.approx(result.outline.y_max - 0.5)
    assert width > height
    assert not any(
        warning.startswith("J1: violates") or "J1: connector row" in warning
        for warning in result.score.warnings
    )


def test_plan_layout_places_two_generic_headers_on_opposing_edges():
    vcc = _Net("VCC")
    gnd = _Net("GND")
    sig1 = _Net("SIG1")
    sig2 = _Net("SIG2")
    j1 = _Part(
        "J1",
        name="pin header",
        footprint="Connector:PinHeader_1x06",
        nets=[vcc, gnd, sig1, sig2],
        pins=6,
    )
    j2 = _Part(
        "J2",
        name="pin header",
        footprint="Connector:PinHeader_1x06",
        nets=[vcc, gnd, sig1, sig2],
        pins=6,
    )
    r1 = _Part("R1", value="10K", footprint="Capacitor:C_0805", nets=[sig1, sig2])
    holes = [
        _Part(f"H{idx}", name="MountingHole", footprint="MountingHole:M2", nets=[], pins=0)
        for idx in range(1, 5)
    ]
    circuit = _Circuit([j1, j2, r1, *holes], [vcc, gnd, sig1, sig2])

    result = plan_layout(circuit, fp_bboxes=BBOXES)

    anchors = {
        anchor.ref: anchor.edge
        for anchor in result.intent_plan.edge_anchors
    }
    placed = {part.ref: part for part in result.placed_parts}
    width, height = BBOXES["Connector:PinHeader_1x06"]

    assert anchors["J1"] == "left"
    assert anchors["J2"] == "right"
    assert placed["J1"].x_mm - width / 2 == pytest.approx(result.outline.x_min + 0.5)
    assert placed["J2"].x_mm + width / 2 == pytest.approx(result.outline.x_max - 0.5)
    assert placed["J1"].rot_deg == 0.0
    assert placed["J2"].rot_deg == 0.0
    assert height > width
    assert result.validation.overlaps == []

    x_mid = (result.outline.x_min + result.outline.x_max) / 2
    y_mid = (result.outline.y_min + result.outline.y_max) / 2
    hole_quadrants = {
        (placed[ref].x_mm < x_mid, placed[ref].y_mm < y_mid)
        for ref in ("H1", "H2", "H3", "H4")
    }
    assert hole_quadrants == {
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    }
    for ref in ("H1", "H2", "H3", "H4"):
        x_edge_distance = min(
            placed[ref].x_mm - result.outline.x_min,
            result.outline.x_max - placed[ref].x_mm,
        )
        y_edge_distance = min(
            placed[ref].y_mm - result.outline.y_min,
            result.outline.y_max - placed[ref].y_mm,
        )
        assert x_edge_distance <= result.outline.width_mm * 0.25
        assert y_edge_distance <= result.outline.height_mm * 0.25


def test_plan_layout_grids_passives_between_opposing_headers():
    vcc = _Net("VCC")
    gnd = _Net("GND")
    sig1 = _Net("SIG1")
    sig2 = _Net("SIG2")
    sig3 = _Net("SIG3")
    sig4 = _Net("SIG4")
    j1 = _Part(
        "J1",
        name="pin header",
        footprint="Connector:PinHeader_1x06",
        nets=[vcc, gnd, sig1, sig2, sig3, sig4],
        pins=6,
    )
    j2 = _Part(
        "J2",
        name="pin header",
        footprint="Connector:PinHeader_1x06",
        nets=[vcc, gnd, sig1, sig2, sig3, sig4],
        pins=6,
    )
    passives = [
        _Part(f"R{idx}", value="10K", footprint="Capacitor:C_0805", nets=[sig1, sig2])
        for idx in range(1, 5)
    ] + [
        _Part(f"C{idx}", value="100nF", footprint="Capacitor:C_0805", nets=[vcc, gnd])
        for idx in range(1, 3)
    ]
    circuit = _Circuit([j1, j2, *passives], [vcc, gnd, sig1, sig2, sig3, sig4])

    result = plan_layout(circuit, fp_bboxes=BBOXES)

    placed = {part.ref: part for part in result.placed_parts}
    passive_refs = {part.ref for part in passives}
    passive_xs = sorted(round(placed[ref].x_mm, 1) for ref in passive_refs)
    passive_ys = sorted(round(placed[ref].y_mm, 1) for ref in passive_refs)
    unique_xs = sorted(set(passive_xs))
    unique_ys = sorted(set(passive_ys))

    assert result.validation.overlaps == []
    assert len(unique_xs) >= 2
    assert len(unique_ys) >= 3
    assert min(unique_xs) > placed["J1"].x_mm
    assert max(unique_xs) < placed["J2"].x_mm
    assert abs(placed["C1"].y_mm - placed["C2"].y_mm) <= 3.0
    for left_ref, right_ref in (("R1", "R2"), ("R3", "R4")):
        assert abs(placed[left_ref].x_mm - placed[right_ref].x_mm) <= 3.5
        assert abs(placed[left_ref].y_mm - placed[right_ref].y_mm) <= 3.0
    assert any(
        "passives arranged on an even grid" in reason
        for reason in result.report.reasons
    )


def test_plan_layout_infers_mounting_holes_to_corners():
    outline = BoardOutline(60.0, 40.0)
    circuit = _circuit()
    h1 = _Part("H1", name="MountingHole", footprint="MountingHole:M2", nets=[], pins=0)
    h2 = _Part("H2", name="MountingHole", footprint="MountingHole:M2", nets=[], pins=0)
    circuit.parts.extend([h1, h2])

    result = plan_layout(
        circuit,
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(outline=outline),
    )

    placed = {part.ref: part for part in result.placed_parts}
    assert placed["H1"].x_mm == pytest.approx(3.2)
    assert placed["H1"].y_mm == pytest.approx(3.2)
    assert placed["H2"].x_mm == pytest.approx(56.8)
    assert placed["H2"].y_mm == pytest.approx(3.2)
    assert "locked by fixed-position constraint" in result.report.part_reasons["H1"]


def test_plan_layout_does_not_edge_anchor_oled_daughterboard_header():
    outline = BoardOutline(60.0, 40.0)
    vcc = _Net("3V3")
    gnd = _Net("GND")
    sda = _Net("OLED_SDA")
    scl = _Net("OLED_SCL")
    u1 = _Part("U1", name="MCU", footprint="Package_QFP:MCU", nets=[vcc, gnd, sda, scl], pins=4)
    j1 = _Part(
        "J1",
        name="OLED daughterboard header",
        footprint="Connector:PinHeader_1x04",
        nets=[vcc, gnd, sda, scl],
        pins=4,
    )
    circuit = _Circuit([u1, j1], [vcc, gnd, sda, scl])

    result = plan_layout(
        circuit,
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(outline=outline),
    )

    assert all(anchor.ref != "J1" for anchor in result.intent_plan.edge_anchors)
    assert any(
        intent.kind == "internal_connector"
        for intent in result.intent_plan.intents_for("J1")
    )


def test_plan_layout_aligns_panel_jacks_without_edge_anchoring():
    outline = BoardOutline(80.0, 40.0)
    gnd = _Net("GND")
    sig1 = _Net("IN_1")
    sig2 = _Net("IN_2")
    sig3 = _Net("OUT_1")
    j1 = _Part(
        "J1",
        name="Thonkiconn PJ398SM input jack",
        footprint="Connector_Audio:Thonkiconn_PJ398SM",
        nets=[sig1, gnd],
    )
    j2 = _Part(
        "J2",
        name="Thonkiconn PJ398SM input jack",
        footprint="Connector_Audio:Thonkiconn_PJ398SM",
        nets=[sig2, gnd],
    )
    j3 = _Part(
        "J3",
        name="Thonkiconn PJ398SM output jack",
        footprint="Connector_Audio:Thonkiconn_PJ398SM",
        nets=[sig3, gnd],
    )
    circuit = _Circuit([j1, j2, j3], [gnd, sig1, sig2, sig3])

    result = plan_layout(
        circuit,
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(outline=outline),
    )

    assert all(anchor.ref not in {"J1", "J2", "J3"} for anchor in result.intent_plan.edge_anchors)
    placed = {part.ref: part for part in result.placed_parts}
    ys = [placed[ref].y_mm for ref in ("J1", "J2", "J3")]
    xs = [placed[ref].x_mm for ref in ("J1", "J2", "J3")]
    assert max(ys) - min(ys) <= 1.0
    assert max(xs) - min(xs) >= 30.0


def test_plan_layout_keeps_horizontal_audio_jack_row_on_edge():
    outline = BoardOutline(75.0, 100.0)
    vcc = _Net("VCC")
    gnd = _Net("GND")
    signal_nets = [_Net(f"OUT{idx}") for idx in range(1, 7)]
    u1 = _Part(
        "U1",
        name="MCU",
        footprint="Package_QFP:MCU",
        nets=[vcc, gnd, *signal_nets],
        pins=8,
    )
    jacks = [
        _Part(
            f"J{idx}",
            name="horizontal 3.5mm trigger output jack",
            footprint="Connector_Audio:Jack_3.5mm_PJ320D_Horizontal",
            nets=[signal_nets[idx - 1], gnd],
            pins=2,
        )
        for idx in range(1, 7)
    ]
    passives = [
        _Part(
            f"R{idx}",
            value="220",
            footprint="Capacitor:C_0805",
            nets=[signal_nets[idx - 1]],
        )
        for idx in range(1, 7)
    ]
    circuit = _Circuit([u1, *jacks, *passives], [vcc, gnd, *signal_nets])

    result = plan_layout(
        circuit,
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(outline=outline),
    )

    anchors = {anchor.ref: anchor.edge for anchor in result.intent_plan.edge_anchors}
    placed = {part.ref: part for part in result.placed_parts}
    jack_width, jack_height = BBOXES["Connector_Audio:Jack_3.5mm_PJ320D_Horizontal"]
    rotated_width = jack_height

    for ref in {f"J{idx}" for idx in range(1, 7)}:
        assert anchors[ref] == "right"
        jack = placed[ref]
        assert jack.rot_deg == 90.0
        assert jack.x_mm + rotated_width / 2 == pytest.approx(outline.x_max - 0.5)
        assert not any(warning.startswith(f"{ref}: violates right-edge") for warning in result.score.warnings)


def test_soft_constraints_do_not_move_edge_anchored_connectors():
    outline = BoardOutline(75.0, 100.0)
    gnd = _Net("GND")
    j1 = _Part(
        "J1",
        name="horizontal 3.5mm trigger output jack",
        footprint="Connector_Audio:Jack_3.5mm_PJ320D_Horizontal",
        nets=[gnd],
        pins=2,
    )
    j2 = _Part(
        "J2",
        name="horizontal 3.5mm trigger output jack",
        footprint="Connector_Audio:Jack_3.5mm_PJ320D_Horizontal",
        nets=[gnd],
        pins=2,
    )
    circuit = _Circuit([j1, j2], [gnd])

    result = plan_layout(
        circuit,
        fp_bboxes=BBOXES,
        constraints=LayoutConstraints(
            outline=outline,
            edge_anchors=[
                EdgeAnchor("J1", "right", offset_mm=25.0),
                EdgeAnchor("J2", "right", offset_mm=75.0),
            ],
            distribute=[
                DistributeConstraint(
                    refs=["J1", "J2"],
                    axis="x",
                    start_mm=10.0,
                    end_mm=20.0,
                ),
            ],
        ),
    )

    placed = {part.ref: part for part in result.placed_parts}
    jack_width, jack_height = BBOXES["Connector_Audio:Jack_3.5mm_PJ320D_Horizontal"]
    rotated_width = jack_height

    assert placed["J1"].x_mm + rotated_width / 2 == pytest.approx(outline.x_max - 0.5)
    assert placed["J2"].x_mm + rotated_width / 2 == pytest.approx(outline.x_max - 0.5)


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
    assert placed["C1"].rot_deg % 180 == 90.0
    assert any(
        "actual U1 VDD/GND pads" in reason
        for reason in result.report.part_reasons["C1"]
    )
