"""
MAX4466 Electret Microphone Amplifier - SKiDL Circuit
=====================================================
Low-noise microphone amplifier with integrated electret microphone.
Selectable 25x to 125x gain via trimmer pot.
Excellent PSRR, rail-to-rail output.

Circuit:
- MAX4466 low-noise op-amp (SOT-23-5)
- Electret microphone with bias resistor
- AC coupling from mic to amp input
- Mid-supply bias network (VCC/2) for single-supply operation
- Adjustable gain via trimmer pot (25x-125x)
- HF rolloff cap in feedback
- Power supply bypass capacitor
- 3-pin output header (GND, OUT, VCC)
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
# Power nets
# =============================================================================
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER


# =============================================================================
# Subcircuit: Microphone amplifier core
# =============================================================================
@subcircuit
def amp_core(vcc, gnd, audio_out):
    """MAX4466 amplifier with electret mic, bias, and gain network."""

    # Internal nets
    mic_node = Net("MIC_NODE")     # Between mic and coupling cap
    amp_inp = Net("AMP_INP")       # Op-amp non-inverting input
    amp_inn = Net("AMP_INN")       # Op-amp inverting input
    vbias = Net("VBIAS")           # Mid-supply bias voltage
    fb_mid = Net("FB_MID")         # Feedback network midpoint

    # -- MAX4466 op-amp (SOT-23-5) --
    # Datasheet pinout: 1=OUT, 2=V-, 3=IN+, 4=IN-, 5=V+
    u1 = Part(name="MAX4466", tool=SKIDL, dest=NETLIST,
              footprint="Package_TO_SOT_SMD:SOT-23-5",
              ref_prefix="U",
              pins=[
                  make_pin("1", "OUT",  Pin.types.OUTPUT),
                  make_pin("2", "V-",   Pin.types.PWRIN),
                  make_pin("3", "IN+",  Pin.types.INPUT),
                  make_pin("4", "IN-",  Pin.types.INPUT),
                  make_pin("5", "V+",   Pin.types.PWRIN),
              ])
    u1.lib = _FakeLib("Amplifier_Operational")
    add_skidl_draw_cmds(u1)
    u1["V+"]  += vcc
    u1["V-"]  += gnd
    u1["IN+"] += amp_inp
    u1["IN-"] += amp_inn
    u1["OUT"] += audio_out

    # -- Power supply bypass cap (100nF) --
    c_bypass = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bypass[1] += vcc
    c_bypass[2] += gnd

    # -- Electret microphone (2-pin capsule) --
    mic = Part(name="Electret_Mic", tool=SKIDL, dest=NETLIST,
               footprint="Sensor_Audio:CUI_CMC-4013-SMT",
               ref_prefix="MK",
               pins=[
                   make_pin("1", "OUT",  Pin.types.PASSIVE),
                   make_pin("2", "GND",  Pin.types.PASSIVE),
               ])
    mic.lib = _FakeLib("Sensor_Audio")
    add_skidl_draw_cmds(mic)
    mic["OUT"] += mic_node
    mic["GND"] += gnd

    # -- Mic bias resistor (10K from VCC to mic capsule) --
    r_bias = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_bias[1] += vcc
    r_bias[2] += mic_node

    # -- AC coupling cap from mic to amp non-inverting input (100nF) --
    c_couple = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_couple[1] += mic_node
    c_couple[2] += amp_inp

    # -- DC bias voltage divider (mid-supply VCC/2 reference) --
    r_bias1 = Part("Device", "R", value="100K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    r_bias2 = Part("Device", "R", value="100K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    r_bias1[1] += vcc
    r_bias1[2] += vbias
    r_bias2[1] += vbias
    r_bias2[2] += gnd

    # -- Bias bypass cap (1uF for stable reference) --
    c_bias = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bias[1] += vbias
    c_bias[2] += gnd

    # -- Bias to non-inverting input (100K) --
    r_inp_bias = Part("Device", "R", value="100K",
                      footprint="Resistor_SMD:R_0603_1608Metric")
    r_inp_bias[1] += vbias
    r_inp_bias[2] += amp_inp

    # -- Feedback resistor (100K from output to feedback midpoint) --
    r_fb = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_fb[1] += audio_out
    r_fb[2] += fb_mid

    # -- Gain-setting trimmer pot (1K) --
    # Adjusts gain from 25x to 125x
    rv_gain = Part("Device", "R_Potentiometer_Trim", value="1K",
                   footprint="Potentiometer_SMD:Potentiometer_Bourns_3214W_Vertical")
    rv_gain["1"] += fb_mid
    rv_gain["3"] += amp_inn
    rv_gain["2"] += amp_inn

    # -- Ground-side gain resistor (1K to bias) --
    r_gain = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_gain[1] += amp_inn
    r_gain[2] += vbias

    # -- Feedback HF rolloff cap (10pF) --
    c_fb = Part("Device", "C", value="10pF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_fb[1] += audio_out
    c_fb[2] += amp_inn


# =============================================================================
# Subcircuit: Output and power interface
# =============================================================================
@subcircuit
def output_interface(vcc, gnd, audio_out):
    """3-pin header for power and audio output."""

    # Output header: pin1=GND, pin2=OUT, pin3=VCC
    j_out = Part("Connector_Generic", "Conn_01x03",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                 ref_prefix="J")
    j_out[1] += gnd
    j_out[2] += audio_out
    j_out[3] += vcc

    # Output AC coupling cap (10uF electrolytic equivalent)
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += audio_out
    c_out[2] += Net("AC_OUT")


# =============================================================================
# Top-level connections
# =============================================================================
audio_out = Net("AUDIO_OUT")

amp_core(vcc, gnd, audio_out)
output_interface(vcc, gnd, audio_out)

# =============================================================================
# Generate schematic
# =============================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3)
