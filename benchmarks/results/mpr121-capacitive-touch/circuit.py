#!/usr/bin/env python3
"""MPR121 12-Channel Capacitive Touch Sensor Board

Features:
- MPR121 capacitive touch controller (QFN-20)
- 12 independent touch pad inputs on header
- I2C interface with level shifting (BSS138) for 3-5V host compatibility
- MCP1700 3.3V LDO regulator for the MPR121 (3V-only chip)
- Selectable I2C address via ADDR pin (4 addresses: 0x5A-0x5D)
- IRQ output with indicator LED
- Input voltage: 3-5V via VIN header
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ==============================================================
# Power nets
# ==============================================================
vin = Net("VIN")          # 3-5V input
vcc = Net("VCC")          # Same as VIN, host-side logic level
v3v3 = Net("+3V3")        # 3.3V regulated for MPR121
gnd = Net("GND")

vin.drive = POWER
vcc.drive = POWER
v3v3.drive = POWER
gnd.drive = POWER


# ==============================================================
# Subcircuit: 3.3V Voltage Regulator (MCP1700-3302E/TT)
# ==============================================================
@subcircuit
def power_regulation(vin, vout, gnd):
    """MCP1700 3.3V LDO with input/output caps."""
    reg = Part("Regulator_Linear", "MCP1700x-300xxTT",
               value="MCP1700-3.3V",
               footprint="Package_TO_SOT_SMD:SOT-23")
    reg["VI"] += vin
    reg["VO"] += vout
    reg["GND"] += gnd

    # Input capacitor - 1uF ceramic
    cin = Part("Device", "C", value="1uF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    cin[1] += vin
    cin[2] += gnd

    # Output capacitor - 1uF ceramic
    cout = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cout[1] += vout
    cout[2] += gnd

    # Bulk output cap - 10uF
    cbulk = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    cbulk[1] += vout
    cbulk[2] += gnd


# ==============================================================
# Subcircuit: MPR121 Touch Controller
# ==============================================================
@subcircuit
def mpr121_touch(v3v3, gnd, sda, scl, irq, electrodes):
    """MPR121 12-channel capacitive touch sensor with decoupling.

    MPR121 QFN-20 pinout (from datasheet):
    Pin 1:  ELE0        Pin 11: ELE7
    Pin 2:  ELE1        Pin 12: ELE8
    Pin 3:  ELE2        Pin 13: ELE9
    Pin 4:  ELE3        Pin 14: ELE10
    Pin 5:  ELE4        Pin 15: ELE11
    Pin 6:  ELE5        Pin 16: IRQ (active low, open drain)
    Pin 7:  ELE6        Pin 17: SDA
    Pin 8:  VSS         Pin 18: SCL
    Pin 9:  ADDR        Pin 19: VDD
    Pin 10: VREG        Pin 20: VSS (exposed pad)
    """
    # Build pins with orientation and position attributes needed by schematic gen.
    # QFN-20: left side pins go down (orientation "R" = pin stub points right),
    # right side pins go up (orientation "L" = pin stub points left),
    # top pins (orientation "D"), bottom pins (orientation "U").
    def _pin(num, name, func, orientation="R", x=0, y=0):
        p = Pin(num=num, name=name, func=func)
        p.orientation = orientation
        p.x = x
        p.y = y
        return p

    # Pin layout for QFN-20:
    # Left side (pins 1-5): electrode pins, orientation "R" (stub points right into IC)
    # Bottom side (pins 6-9): more electrodes + VSS + ADDR
    # Right side (pins 10-15): VREG + more electrodes
    # Top side (pins 16-19): IRQ, SDA, SCL, VDD
    # Pin 21 (EP): exposed pad, center
    #
    # All coordinates in mm (library units). The schematic gen converts to mils.
    pin_defs = [
        ("1",  "ELE0",  Pin.types.BIDIR,    "R",  -5.08, -5.08),
        ("2",  "ELE1",  Pin.types.BIDIR,    "R",  -5.08, -2.54),
        ("3",  "ELE2",  Pin.types.BIDIR,    "R",  -5.08,  0.00),
        ("4",  "ELE3",  Pin.types.BIDIR,    "R",  -5.08,  2.54),
        ("5",  "ELE4",  Pin.types.BIDIR,    "R",  -5.08,  5.08),
        ("6",  "ELE5",  Pin.types.BIDIR,    "U",  -2.54,  7.62),
        ("7",  "ELE6",  Pin.types.BIDIR,    "U",   0.00,  7.62),
        ("8",  "VSS",   Pin.types.PWRIN,    "U",   2.54,  7.62),
        ("9",  "ADDR",  Pin.types.INPUT,    "U",   5.08,  7.62),
        ("10", "VREG",  Pin.types.PWROUT,   "L",   7.62,  5.08),
        ("11", "ELE7",  Pin.types.BIDIR,    "L",   7.62,  2.54),
        ("12", "ELE8",  Pin.types.BIDIR,    "L",   7.62,  0.00),
        ("13", "ELE9",  Pin.types.BIDIR,    "L",   7.62, -2.54),
        ("14", "ELE10", Pin.types.BIDIR,    "L",   7.62, -5.08),
        ("15", "ELE11", Pin.types.BIDIR,    "D",   5.08, -7.62),
        ("16", "IRQ",   Pin.types.OPENCOLL, "D",   2.54, -7.62),
        ("17", "SDA",   Pin.types.BIDIR,    "D",   0.00, -7.62),
        ("18", "SCL",   Pin.types.INPUT,    "D",  -2.54, -7.62),
        ("19", "VDD",   Pin.types.PWRIN,    "D",  -5.08, -7.62),
        ("21", "EP",    Pin.types.PWRIN,    "U",   0.00,  0.00),
    ]

    pins = []
    pin_draw_cmds = []
    orient_to_deg = {"R": 0, "U": 90, "L": 180, "D": 270}
    for num, name, func, orient, x, y in pin_defs:
        p = _pin(num, name, func, orient, x, y)
        pins.append(p)
        # Build a pin draw_cmd in KiCad s-expression list format
        pin_draw_cmds.append([
            "pin", "passive", "line",
            ["at", x, y, orient_to_deg[orient]],
            ["length", 2.54],
            ["name", name, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", num, ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    ic = Part(name="MPR121QR2", tool=SKIDL, dest=NETLIST,
              footprint="Package_DFN_QFN:QFN-20-1EP_3x3mm_P0.45mm_EP1.6x1.6mm",
              pins=pins)

    # Add draw_cmds so calc_symbol_bbox can compute a proper bounding box.
    # Rectangle body of the IC symbol (in mm, matching pin coordinates).
    body_rect = [
        "rectangle",
        ["start", -3.81, -6.35],
        ["end",    6.35,  6.35],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "background"]],
    ]
    ic.draw_cmds = {
        1: pin_draw_cmds + [body_rect],
        0: [body_rect],
    }

    # SKIDL-tool parts need a lib attribute for schematic generation.
    class _FakeLib:
        def __init__(self, name):
            self.filename = name
    ic.lib = _FakeLib("Sensor_Touch")

    # Power connections
    ic["VDD"] += v3v3
    ic["VSS"] += gnd
    ic["EP"] += gnd

    # Decoupling cap on VDD - 100nF
    cdec = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cdec[1] += v3v3
    cdec[2] += gnd

    # VREG internal regulator bypass cap - 10nF
    cvreg = Part("Device", "C", value="10nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    cvreg[1] += ic["VREG"]
    cvreg[2] += gnd

    # I2C connections
    ic["SDA"] += sda
    ic["SCL"] += scl

    # IRQ output (active low, open drain)
    ic["IRQ"] += irq

    # ADDR pin to GND = address 0x5A (default)
    # Connect via a resistor to allow optional address change
    r_addr = Part("Device", "R", value="0R",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_addr[1] += ic["ADDR"]
    r_addr[2] += gnd

    # Electrode connections (12 channels)
    for i in range(12):
        ele_name = f"ELE{i}"
        ic[ele_name] += electrodes[i]


# ==============================================================
# Subcircuit: I2C Level Shifter (BSS138 bidirectional)
# ==============================================================
@subcircuit
def i2c_level_shifter(v_low, v_high, gnd, low_sda, low_scl, high_sda, high_scl):
    """Bidirectional I2C level shifter using BSS138 MOSFETs.

    Classic application note circuit: 2x BSS138 with pull-ups on both sides.
    Low side = 3.3V (MPR121 side), High side = VCC (host side, 3-5V).
    """
    # SDA level shifter
    q_sda = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_sda["G"] += v_low       # Gate to low-side voltage
    q_sda["S"] += low_sda     # Source to low-side SDA
    q_sda["D"] += high_sda    # Drain to high-side SDA

    # SDA pull-ups
    r_sda_low = Part("Device", "R", value="10K",
                     footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda_low[1] += v_low
    r_sda_low[2] += low_sda

    r_sda_high = Part("Device", "R", value="10K",
                      footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda_high[1] += v_high
    r_sda_high[2] += high_sda

    # SCL level shifter
    q_scl = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_scl["G"] += v_low       # Gate to low-side voltage
    q_scl["S"] += low_scl     # Source to low-side SCL
    q_scl["D"] += high_scl    # Drain to high-side SCL

    # SCL pull-ups
    r_scl_low = Part("Device", "R", value="10K",
                     footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl_low[1] += v_low
    r_scl_low[2] += low_scl

    r_scl_high = Part("Device", "R", value="10K",
                      footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl_high[1] += v_high
    r_scl_high[2] += high_scl


# ==============================================================
# Subcircuit: IRQ Output with Indicator LED
# ==============================================================
@subcircuit
def irq_indicator(v3v3, gnd, irq_in, irq_out):
    """IRQ line with pull-up and indicator LED.

    LED lights when IRQ is asserted (low) since MPR121 IRQ is active-low open drain.
    """
    # Pull-up resistor on IRQ (3.3V side)
    r_irq = Part("Device", "R", value="100K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_irq[1] += v3v3
    r_irq[2] += irq_in

    # LED + current limiting resistor (LED on when IRQ low)
    # LED anode to 3.3V through resistor, cathode to IRQ line
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    led = Part("Device", "LED", value="RED",
               footprint="LED_SMD:LED_0603_1608Metric")

    r_led[1] += v3v3
    r_led[2] += led[1]   # LED anode
    led[2] += irq_in     # LED cathode to IRQ (lights when IRQ is low)

    # Pass IRQ through to output header
    irq_out += irq_in


# ==============================================================
# Top-level: Build the full circuit
# ==============================================================

# Internal nets
sda_3v3 = Net("SDA_3V3")    # Low-side (3.3V) SDA
scl_3v3 = Net("SCL_3V3")    # Low-side (3.3V) SCL
sda_5v = Net("SDA")         # High-side (VCC) SDA
scl_5v = Net("SCL")         # High-side (VCC) SCL
irq_net = Net("IRQ")        # IRQ from MPR121
irq_out = Net("IRQ_OUT")    # IRQ to header

# Electrode nets
ele_nets = [Net(f"ELE{i}") for i in range(12)]

# VIN = VCC (input power is the host-side logic rail)
vin += vcc

# 1. Power regulation: VIN -> 3.3V
power_regulation(vin, v3v3, gnd)

# 2. MPR121 touch controller
mpr121_touch(v3v3, gnd, sda_3v3, scl_3v3, irq_net, ele_nets)

# 3. I2C level shifter
i2c_level_shifter(v3v3, vcc, gnd, sda_3v3, scl_3v3, sda_5v, scl_5v)

# 4. IRQ indicator
irq_indicator(v3v3, gnd, irq_net, irq_out)

# ==============================================================
# Connectors
# ==============================================================

# Host I2C + Power header (1x6): VIN, GND, SDA, SCL, IRQ, NC
j_host = Part("Connector_Generic", "Conn_01x06",
              value="HOST",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
j_host[1] += vin
j_host[2] += gnd
j_host[3] += sda_5v
j_host[4] += scl_5v
j_host[5] += irq_out
j_host[6] += NC()      # Reserved / NC

# Electrode header (1x12): ELE0-ELE11
j_electrodes = Part("Connector_Generic", "Conn_01x12",
                     value="ELECTRODES",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
for i in range(12):
    j_electrodes[i + 1] += ele_nets[i]


# ==============================================================
# Generate schematic
# ==============================================================
generate_schematic(auto_stub=True)
