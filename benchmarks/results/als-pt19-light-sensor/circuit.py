"""
ALS-PT19 Analog Light Sensor Breakout
======================================
Wide-spectrum analog ambient light sensor with logarithmic response.
Uses the ALS-PT19 NPN phototransistor in a 1206 reverse-mount SMD package.

Circuit:
- ALS-PT19 phototransistor (collector to VCC via load resistor, emitter to GND)
- 10K load resistor sets the operating point (output = collector voltage)
- 100nF decoupling capacitor on VCC
- 100nF filter capacitor on analog output for noise rejection
- 3-pin header: VCC, GND, AOUT for MCU ADC connection
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from collections import defaultdict
from skidl import *
set_default_tool(KICAD9)


# =============================================================================
# Helpers for tool=SKIDL parts (schematic generation support)
# =============================================================================
def make_pin(num, name, func, orientation="R"):
    """Create a Pin with schematic-required attributes."""
    return Pin(num=num, name=name, func=func,
               x=0, y=0, orientation=orientation, length=100, rotation=0)


def add_skidl_draw_cmds(part):
    """Add rectangle + pin draw_cmds to a tool=SKIDL Part for schematic gen."""
    pins = list(part.pins)
    n = len(pins)
    if n == 0:
        return

    spacing = 2.54
    pin_len = 2.54

    left_pins = pins[:n // 2]
    right_pins = pins[n // 2:]

    max_side = max(len(left_pins), len(right_pins), 1)
    body_h = max(max_side * spacing, spacing * 2)
    body_w = max(spacing * 4, spacing * 2)

    rect_cmd = [
        "rectangle",
        ["start", -body_w / 2, -body_h / 2],
        ["end", body_w / 2, body_h / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]

    pin_cmds = []
    for i, pin in enumerate(left_pins):
        y = -body_h / 2 + spacing * (i + 0.5)
        x = -body_w / 2 - pin_len
        pin.x = x
        pin.y = y
        pin.orientation = "R"
        pin.rotation = 0
        pin_cmds.append([
            "pin", "passive", "line",
            ["at", x, y, 0],
            ["length", pin_len],
            ["name", pin.name, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num), ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    for i, pin in enumerate(right_pins):
        y = -body_h / 2 + spacing * (i + 0.5)
        x = body_w / 2 + pin_len
        pin.x = x
        pin.y = y
        pin.orientation = "L"
        pin.rotation = 180
        pin_cmds.append([
            "pin", "passive", "line",
            ["at", x, y, 180],
            ["length", pin_len],
            ["name", pin.name, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num), ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    part.draw_cmds = defaultdict(list)
    part.draw_cmds[0] = [rect_cmd]
    part.draw_cmds[1] = pin_cmds + [rect_cmd]


class _FakeLib:
    """Minimal lib stub so sexp_schematic can write lib_id."""
    def __init__(self, name="skidl"):
        self.filename = name


# =============================================================================
# Subcircuit: ALS-PT19 sensor with analog output
# =============================================================================
@subcircuit
def als_pt19_sensor(vcc, gnd, aout):
    """ALS-PT19 ambient light sensor with analog output."""

    # ALS-PT19 NPN phototransistor — 2-pin (collector, emitter)
    # 1206 reverse-mount SMD package
    q1 = Part(
        name="ALS-PT19",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="Q",
        footprint="LED_SMD:LED_1206_3216Metric",
        pins=[
            make_pin("1", "C", Pin.types.PASSIVE),
            make_pin("2", "E", Pin.types.PASSIVE),
        ],
    )

    # 10K load resistor: VCC -> R_LOAD -> Collector
    # Output voltage = VCC - I_photo * R_LOAD
    # 10K gives good dynamic range for indoor/outdoor ambient light
    r_load = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
    r_load[1] += vcc
    r_load[2] += q1["C"]     # Collector
    r_load[2] += aout         # Analog output at collector node

    # Emitter to ground
    q1["E"] += gnd

    # 100nF decoupling capacitor on VCC supply
    c_dec = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vcc
    c_dec[2] += gnd

    # 100nF filter capacitor on analog output for noise rejection
    c_filt = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    c_filt[1] += aout
    c_filt[2] += gnd


# =============================================================================
# Top-level nets
# =============================================================================
vcc = Net("VCC")
vcc.drive = POWER

gnd = Net("GND")
gnd.drive = POWER

aout = Net("AOUT")

# Instantiate the sensor subcircuit
als_pt19_sensor(vcc, gnd, aout)

# 3-pin output header: VCC, GND, AOUT (for connection to MCU ADC)
j1 = Part(
    "Connector_Generic",
    "Conn_01x03",
    footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
)
j1[1] += vcc
j1[2] += gnd
j1[3] += aout

# =============================================================================
# Add schematic draw commands to SKIDL parts
# =============================================================================
_fake_lib = _FakeLib()

for part in default_circuit.parts:
    if not hasattr(part, "draw_cmds") or not part.draw_cmds:
        add_skidl_draw_cmds(part)
    if not hasattr(part, "lib") or part.lib is None:
        try:
            _ = part.lib.filename
        except (AttributeError, TypeError):
            part.lib = _fake_lib

generate_schematic(auto_stub=True)
