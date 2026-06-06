from __future__ import annotations

from skidl.layout.constraints import BoardOutline
from skidl.layout.intent import infer_placement_intents


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
    def __init__(self, ref, value="", foot="", name="", nets=None, pins=2):
        self.ref = ref
        self.value = value
        self.foot = foot
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


def _kinds(plan, ref):
    return {intent.kind for intent in plan.intents_for(ref)}


def test_infers_edge_connector_power_and_debug_intent():
    vbus = _Net("VBUS")
    gnd = _Net("GND")
    usb = _Part("J1", name="USB connector", foot="Connector:USB_C", nets=[vbus, gnd], pins=16)
    debug = _Part("J2", name="SWD debug header", foot="Connector:TagConnect", nets=[gnd], pins=6)
    circuit = _Circuit([usb, debug], [vbus, gnd])

    plan = infer_placement_intents(circuit, outline=BoardOutline(80.0, 50.0))

    assert {"edge_connector", "power_input"}.issubset(_kinds(plan, "J1"))
    assert {"edge_connector", "test_debug"}.issubset(_kinds(plan, "J2"))
    usb_anchor = next(anchor for anchor in plan.edge_anchors if anchor.ref == "J1")
    debug_anchor = next(anchor for anchor in plan.edge_anchors if anchor.ref == "J2")
    assert usb_anchor.edge == "bottom"
    assert usb_anchor.offset_mm == 40.0
    assert debug_anchor.edge == "right"


def test_infers_mux_and_repeated_channel_intent():
    ch0 = _Net("CH0_SIG")
    ch1 = _Net("CH1_SIG")
    ch2 = _Net("CH2_SIG")
    ch3 = _Net("CH3_SIG")
    mux = _Part(
        "U1",
        name="analog mux",
        foot="Package_QFN:MUX",
        nets=[ch0, ch1, ch2, ch3],
        pins=4,
    )
    sensors = [
        _Part("U2", name="sensor", foot="Sensor:S", nets=[ch0], pins=3),
        _Part("U3", name="sensor", foot="Sensor:S", nets=[ch1], pins=3),
    ]
    circuit = _Circuit([mux, *sensors], [ch0, ch1, ch2, ch3])

    plan = infer_placement_intents(circuit)

    assert "mux_bank_controller" in _kinds(plan, "U1")
    assert len(plan.repeated_channels) == 1
    assert plan.repeated_channels[0].channel_numbers == [0, 1, 2, 3]
    assert plan.repeated_channels[0].refs_by_channel[0] == ["U1", "U2"]
    assert plan.repeated_channels[0].refs_by_channel[1] == ["U1", "U3"]
    assert {"U1", "U2", "U3"}.issubset(set(plan.repeated_channels[0].refs))
