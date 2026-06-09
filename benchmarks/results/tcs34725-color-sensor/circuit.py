#!/usr/bin/env python3
"""TCS34725 Color Sensor Breakout Board

RGB and Clear light sensing color sensor with 3,800,000:1 dynamic range,
integrated IR blocking filter, adjustable integration time and gain.
I2C interface with interrupt output. Includes onboard LED driver for
illumination and voltage regulator for 3.3V/5V compatibility.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


class _FakeLib:
    """Minimal stand-in for Part.lib so schematic writer can build lib_id."""
    def __init__(self, filename):
        self.filename = filename


def skidl_part(name, footprint, pins_def):
    """Create a Part(tool=SKIDL) with draw_cmds and pin attributes set
    so the KiCad 9 schematic generator can process them.

    pins_def: list of dicts with keys: num, name, func, side
        side: 'L' (left), 'R' (right), 'U' (up/top), 'D' (down/bottom)
    """
    # Group pins by side for coordinate assignment
    sides = {"L": [], "R": [], "U": [], "D": []}
    for pd in pins_def:
        sides[pd.get("side", "L")].append(pd)

    # Calculate body size based on number of pins per side
    max_v = max(len(sides["L"]), len(sides["R"]), 1)
    max_h = max(len(sides["U"]), len(sides["D"]), 1)
    body_h = max(max_v * 2.54, 5.08)  # mm, minimum 5.08
    body_w = max(max_h * 2.54, 5.08)  # mm, minimum 5.08
    pin_len = 2.54  # mm

    # Pin orientation: the direction the pin stub points (away from body)
    orient_map = {"L": 0, "R": 180, "U": 90, "D": 270}

    # KiCad pin type strings
    func_to_kicad = {
        Pin.types.PWRIN: "power_in",
        Pin.types.PWROUT: "power_out",
        Pin.types.INPUT: "input",
        Pin.types.OUTPUT: "output",
        Pin.types.BIDIR: "bidirectional",
        Pin.types.TRISTATE: "tri_state",
        Pin.types.PASSIVE: "passive",
        Pin.types.UNSPEC: "unspecified",
        Pin.types.NOCONNECT: "no_connect",
    }

    pin_objects = []
    draw_cmds = {1: [], 0: []}

    # Body rectangle
    draw_cmds[1].append([
        "rectangle",
        ["start", -body_w / 2, -body_h / 2],
        ["end", body_w / 2, body_h / 2],
        ["stroke", ["width", 0], ["type", "default"]],
        ["fill", ["type", "background"]],
    ])

    for side, plist in sides.items():
        for i, pd in enumerate(plist):
            if side == "L":
                x = -body_w / 2 - pin_len
                y = -2.54 * (len(plist) - 1) / 2 + 2.54 * i
            elif side == "R":
                x = body_w / 2 + pin_len
                y = -2.54 * (len(plist) - 1) / 2 + 2.54 * i
            elif side == "U":
                x = -2.54 * (len(plist) - 1) / 2 + 2.54 * i
                y = -body_h / 2 - pin_len
            else:  # D
                x = -2.54 * (len(plist) - 1) / 2 + 2.54 * i
                y = body_h / 2 + pin_len

            rot = orient_map[side]

            pin_objects.append(Pin(
                num=str(pd["num"]),
                name=pd["name"],
                func=pd["func"],
                orientation=rot,
                x=x,
                y=y,
            ))

            # Pin draw command for bbox calculation and lib symbol output
            kicad_type = func_to_kicad.get(pd["func"], "unspecified")
            draw_cmds[1].append([
                "pin", kicad_type, "line",
                ["at", x, y, rot],
                ["length", pin_len],
                ["name", pd["name"],
                    ["effects", ["font", ["size", 1.27, 1.27]]]],
                ["number", str(pd["num"]),
                    ["effects", ["font", ["size", 1.27, 1.27]]]],
            ])

    part = Part(name=name, tool=SKIDL, dest=NETLIST,
                footprint=footprint, pins=pin_objects)
    part.draw_cmds = draw_cmds
    part.lib = _FakeLib(name + ".kicad_sym")
    return part


# ── Power nets ──────────────────────────────────────────────────────
VIN = Net("VIN")
VCC = Net("+3V3"); VCC.drive = POWER
GND = Net("GND"); GND.drive = POWER

# ── Signal nets ─────────────────────────────────────────────────────
SDA = Net("SDA")
SCL = Net("SCL")
INT = Net("INT")
LED_DRV = Net("LED_DRV")


# ── Voltage Regulator (3.3V LDO) ───────────────────────────────────
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 or similar SOT-23-5 LDO regulator."""
    reg = skidl_part("AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5", [
        {"num": 1, "name": "VIN",  "func": Pin.types.PWRIN,      "side": "L"},
        {"num": 3, "name": "EN",   "func": Pin.types.INPUT,      "side": "L"},
        {"num": 2, "name": "GND",  "func": Pin.types.PWRIN,      "side": "D"},
        {"num": 5, "name": "VOUT", "func": Pin.types.PWROUT,     "side": "R"},
        {"num": 4, "name": "NC",   "func": Pin.types.NOCONNECT,  "side": "R"},
    ])
    reg["VIN"] += vin
    reg["GND"] += gnd
    reg["EN"] += vin        # Always enabled
    reg["VOUT"] += vout

    # Input capacitor
    cin = Part("Device", "C", value="10uF",
               footprint="Capacitor_SMD:C_0805_2012Metric")
    cin[1] += vin
    cin[2] += gnd

    # Output capacitor
    cout = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    cout[1] += vout
    cout[2] += gnd

    # Decoupling cap on output
    cdec = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cdec[1] += vout
    cdec[2] += gnd


# ── TCS34725 Color Sensor ──────────────────────────────────────────
@subcircuit
def color_sensor(vcc, gnd, sda, scl, int_out, led_drv):
    """TCS34725 RGBC color sensor with I2C interface.

    DFN-6 package (2x2mm):
    Pin 1: VDD    Pin 2: GND
    Pin 3: SDA    Pin 4: SCL
    Pin 5: INT    Pin 6: LED (active-low)
    Exposed pad (pin 7): GND
    """
    sensor = skidl_part("TCS34725", "Package_DFN_QFN:DFN-6-1EP_2x2mm_P0.65mm_EP1x1.6mm", [
        {"num": 1, "name": "VDD", "func": Pin.types.PWRIN,   "side": "L"},
        {"num": 6, "name": "LED", "func": Pin.types.INPUT,   "side": "L"},
        {"num": 3, "name": "SDA", "func": Pin.types.BIDIR,   "side": "R"},
        {"num": 4, "name": "SCL", "func": Pin.types.INPUT,   "side": "R"},
        {"num": 5, "name": "INT", "func": Pin.types.OUTPUT,  "side": "R"},
        {"num": 2, "name": "GND", "func": Pin.types.PWRIN,   "side": "D"},
        {"num": 7, "name": "EP",  "func": Pin.types.PASSIVE, "side": "D"},
    ])
    sensor["VDD"] += vcc
    sensor["GND"] += gnd
    sensor["SDA"] += sda
    sensor["SCL"] += scl
    sensor["INT"] += int_out
    sensor["LED"] += led_drv
    sensor["EP"] += gnd

    # Decoupling cap for sensor VDD
    cdec = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cdec[1] += vcc
    cdec[2] += gnd

    # Bulk cap for sensor supply
    cbulk = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    cbulk[1] += vcc
    cbulk[2] += gnd


# ── I2C Pull-ups ───────────────────────────────────────────────────
@subcircuit
def i2c_pullups(vcc, gnd, sda, scl, int_out):
    """I2C pull-up resistors and INT pull-up."""
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vcc
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vcc
    r_scl[2] += scl

    # INT is open-drain, needs pull-up
    r_int = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_int[1] += vcc
    r_int[2] += int_out


# ── LED Driver ──────────────────────────────────────────────────────
@subcircuit
def led_driver(vcc, gnd, led_drv):
    """MOSFET-based LED driver for illumination LED.

    Uses an N-channel MOSFET to switch current through a white LED
    with current-limiting resistor. The TCS34725 LED pin drives the gate.
    """
    # N-channel MOSFET to drive the LED (SOT-23)
    q = skidl_part("BSS138", "Package_TO_SOT_SMD:SOT-23", [
        {"num": 1, "name": "G", "func": Pin.types.INPUT,   "side": "L"},
        {"num": 2, "name": "S", "func": Pin.types.PASSIVE, "side": "D"},
        {"num": 3, "name": "D", "func": Pin.types.PASSIVE, "side": "U"},
    ])
    q["G"] += led_drv
    q["S"] += gnd

    # Gate pull-down to keep LED off by default
    r_pd = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_pd[1] += led_drv
    r_pd[2] += gnd

    # White illumination LED
    led = Part("Device", "LED", value="WHITE",
               footprint="LED_SMD:LED_0603_1608Metric")
    led[1] += vcc      # Anode to VCC

    # Current-limiting resistor (20mA @ 3.3V, ~2V Vf -> ~65 ohm)
    r_led = Part("Device", "R", value="68R",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_led[1] += led[2]   # LED cathode
    r_led[2] += q["D"]   # MOSFET drain


# ── Breakout Header ────────────────────────────────────────────────
@subcircuit
def breakout_header(vin, gnd, sda, scl, int_out, led_drv):
    """6-pin breakout header: VIN, GND, SDA, SCL, INT, LED."""
    hdr = Part("Connector_Generic", "Conn_01x06",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
    hdr[1] += vin
    hdr[2] += gnd
    hdr[3] += sda
    hdr[4] += scl
    hdr[5] += int_out
    hdr[6] += led_drv


# ── Instantiate all subcircuits ─────────────────────────────────────
voltage_regulator(VIN, VCC, GND)
color_sensor(VCC, GND, SDA, SCL, INT, LED_DRV)
i2c_pullups(VCC, GND, SDA, SCL, INT)
led_driver(VCC, GND, LED_DRV)
breakout_header(VIN, GND, SDA, SCL, INT, LED_DRV)

# ── Generate schematic ─────────────────────────────────────────────
generate_schematic(auto_stub=True)
