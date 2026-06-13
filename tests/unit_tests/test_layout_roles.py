from __future__ import annotations

from skidl.layout.roles import classify_part, classify_parts, has_power_and_ground


class _Net:
    def __init__(self, name):
        self.name = name


class _Pin:
    def __init__(self, part, net):
        self.part = part
        self.net = net


class _Part:
    def __init__(
        self,
        ref,
        value="",
        footprint="",
        name="",
        description="",
        nets=None,
        pins=2,
    ):
        self.ref = ref
        self.value = value
        self.footprint = footprint
        self.name = name
        self.description = description
        self.pins = [_Pin(self, _Net(net)) for net in (nets or [])]
        while len(self.pins) < pins:
            self.pins.append(_Pin(self, _Net(f"N{len(self.pins)}")))

    def __len__(self):
        return len(self.pins)


class _Circuit:
    def __init__(self, parts):
        self.parts = parts


def test_classify_decoupling_cap():
    part = _Part(
        "C1",
        value="100nF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
        nets=["VCC", "GND"],
    )
    role = classify_part(part)
    assert role.role == "decoupling_cap"
    assert role.confidence > 0.9


def test_classify_connector_from_ref_and_text():
    part = _Part("J1", name="USB_C_Receptacle", footprint="Connector_USB:USB_C")
    assert classify_part(part).role == "connector"


def test_classify_panel_controls_not_as_ics():
    switch = _Part("SW1", name="RotaryEncoder_Switch", pins=5)
    pot = _Part("RV1", name="Potentiometer", pins=3)

    assert classify_part(switch).role == "control"
    assert classify_part(pot).role == "control"


def test_classify_crystal_and_regulator():
    crystal = _Part("Y1", value="16MHz", footprint="Crystal:Crystal_SMD")
    regulator = _Part(
        "U2",
        name="AP2112 regulator",
        nets=["VIN", "GND", "VOUT"],
        pins=3,
    )
    assert classify_part(crystal).role == "crystal"
    assert classify_part(regulator).role == "regulator"


def test_has_power_and_ground():
    part = _Part("U1", nets=["VCC", "GND", "SIG"], pins=3)
    assert has_power_and_ground(part) is True


def test_has_power_and_ground_understands_voltage_style_names():
    part = _Part("U1", nets=["3V3", "GND", "SIG"], pins=3)
    assert has_power_and_ground(part) is True


def test_classify_parts_by_ref():
    parts = [_Part("J1", name="Header"), _Part("R1", value="10k")]
    roles = classify_parts(_Circuit(parts))
    assert roles["J1"].role == "connector"
    assert roles["R1"].role == "signal_passive"
