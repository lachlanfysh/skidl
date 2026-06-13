from __future__ import annotations

from skidl.layout.constraints import BoardOutline
from skidl.layout.scoring import LayoutScore, score_placement
from skidl.layout.writer import PlacedPart


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
        self._nets = nets

    def get_nets(self):
        return self._nets


BBOXES = {
    "Connector:Header": (10.0, 4.0),
    "Package_QFP:MCU": (12.0, 12.0),
    "Capacitor:C_0805": (2.0, 1.25),
}


def test_score_connector_warns_when_far_from_edge():
    connector = _Part("J1", name="Header", footprint="Connector:Header")
    circuit = _Circuit([connector], [])
    placed = [PlacedPart("J1", 50.0, 50.0, 0.0, "Connector:Header")]

    score = score_placement(placed, circuit, BBOXES, outline=BoardOutline(100.0, 100.0))

    assert isinstance(score, LayoutScore)
    assert score.warning_count == 1
    assert "connector" in score.warnings[0]
    assert score.score < 100.0


def test_score_decoupling_cap_warns_when_far_from_parent():
    vcc = _Net("VCC")
    gnd = _Net("GND")
    sig = _Net("SIG")
    ic = _Part("U1", name="MCU", footprint="Package_QFP:MCU", nets=[vcc, gnd, sig], pins=3)
    cap = _Part("C1", value="100nF", footprint="Capacitor:C_0805", nets=[vcc, gnd])
    circuit = _Circuit([ic, cap], [vcc, gnd, sig])
    placed = [
        PlacedPart("U1", 10.0, 10.0, 0.0, "Package_QFP:MCU"),
        PlacedPart("C1", 30.0, 10.0, 0.0, "Capacitor:C_0805"),
    ]

    score = score_placement(placed, circuit, BBOXES)

    assert any("decoupling cap" in warning for warning in score.warnings)
    assert score.role_counts["decoupling_cap"] == 1
    assert score.role_counts["ic"] == 1
    assert score.power_net_count == 2


def test_score_decoupling_cap_ignores_panel_controls_as_parents():
    vcc = _Net("VCC")
    gnd = _Net("GND")
    sig = _Net("SIG")
    switch = _Part(
        "SW1",
        name="RotaryEncoder_Switch",
        footprint="Connector:Header",
        nets=[vcc, gnd, sig],
        pins=5,
    )
    cap = _Part("C1", value="100nF", footprint="Capacitor:C_0805", nets=[vcc, gnd])
    circuit = _Circuit([switch, cap], [vcc, gnd, sig])
    placed = [
        PlacedPart("SW1", 10.0, 10.0, 0.0, "Connector:Header"),
        PlacedPart("C1", 30.0, 10.0, 0.0, "Capacitor:C_0805"),
    ]

    score = score_placement(placed, circuit, BBOXES)

    assert score.role_counts["control"] == 1
    assert not any("decoupling cap" in warning for warning in score.warnings)


def test_score_counts_hard_validation_failures():
    circuit = _Circuit([], [])
    placed = [
        PlacedPart("R1", 0.0, 0.0, 0.0, "Unknown:Part"),
        PlacedPart("R2", 0.5, 0.0, 0.0, "Unknown:Part"),
    ]

    score = score_placement(placed, circuit, {}, outline=BoardOutline(1.0, 1.0))

    assert score.overlap_count == 1
    assert score.outline_violation_count == 2
    assert score.ok is False


def test_score_includes_power_plan_warnings():
    vbus = _Net("VBUS")
    vcc = _Net("VCC")
    gnd = _Net("GND")
    regulator = _Part(
        "U2",
        name="LDO regulator",
        footprint="Package_TO_SOT:SOT23",
        nets=[vbus, gnd, vcc],
        pins=3,
    )
    circuit = _Circuit([regulator], [vbus, gnd, vcc])
    placed = [PlacedPart("U2", 50.0, 50.0, 0.0, "Package_QFP:MCU")]

    score = score_placement(placed, circuit, BBOXES, board_layers=4)

    assert score.power_net_count == 3
    assert any(
        "regulator has no decoupling cap" in warning for warning in score.warnings
    )


def test_four_layer_board_scores_long_high_current_path_better_than_two_layer():
    vbus = _Net("VBUS")
    gnd = _Net("GND")
    j1 = _Part("J1", name="USB connector", footprint="Connector:Header", nets=[vbus, gnd])
    u1 = _Part("U1", name="load", footprint="Package_QFP:MCU", nets=[vbus, gnd], pins=4)
    circuit = _Circuit([j1, u1], [vbus, gnd])
    placed = [
        PlacedPart("J1", 0.0, 0.0, 0.0, "Connector:Header"),
        PlacedPart("U1", 100.0, 0.0, 0.0, "Package_QFP:MCU"),
    ]

    score_2_layer = score_placement(placed, circuit, BBOXES, board_layers=2)
    score_4_layer = score_placement(placed, circuit, BBOXES, board_layers=4)

    assert score_4_layer.score > score_2_layer.score
    assert score_4_layer.power_corridor_count >= 1
