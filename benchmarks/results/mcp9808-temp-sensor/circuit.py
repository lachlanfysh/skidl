#!/usr/bin/env python3
"""MCP9808 high-accuracy I2C digital temperature sensor breakout board.

Features:
- +/-0.25C accuracy from -40C to +125C
- I2C interface (configurable 7-bit address via A0/A1/A2)
- ALERT output (open-drain, active low)
- Shutdown mode via I2C register
- 8-pin MSOP package
- 100nF decoupling cap on VDD
- 10k pull-down resistors on A0/A1/A2 for address 0x18 (default)
- 10k I2C pull-up resistors on SDA/SCL
- Single 8-pin header: VCC, GND, SDA, SCL, ALERT, A0, A1, A2
"""

import os, sys
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ── Power nets ────────────────────────────────────────────────
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ── Signal nets ───────────────────────────────────────────────
sda   = Net("SDA")
scl   = Net("SCL")
alert = Net("ALERT")
a0    = Net("A0")
a1    = Net("A1")
a2    = Net("A2")


# ── Subcircuit: I2C pin header connector ─────────────────────
@subcircuit
def i2c_header(vcc, gnd, sda, scl, alert, a0, a1, a2):
    """8-pin 2.54mm pin header: VCC GND SDA SCL ALERT A0 A1 A2"""
    hdr = Part(
        "Connector", "Conn_01x08_Pin",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
    )
    hdr[1] += vcc
    hdr[2] += gnd
    hdr[3] += sda
    hdr[4] += scl
    hdr[5] += alert
    hdr[6] += a0
    hdr[7] += a1
    hdr[8] += a2


# ── Subcircuit: MCP9808 temperature sensor ────────────────────
@subcircuit
def mcp9808_sensor(vcc, gnd, sda, scl, alert, a0, a1, a2):
    """MCP9808 with decoupling cap, I2C pull-ups, address pull-downs."""

    # PWR_FLAGs tell KiCad ERC that VCC and GND are driven from the connector
    pwr_flag_vcc = Part("power", "PWR_FLAG")
    pwr_flag_vcc[1] += vcc
    pwr_flag_gnd = Part("power", "PWR_FLAG")
    pwr_flag_gnd[1] += gnd

    # MCP9808 in MSOP-8 package
    ic = Part(
        "Sensor_Temperature", "MCP9808_MSOP",
        footprint="Package_SO:MSOP-8_3x3mm_P0.65mm",
    )
    # Pin 1: SDA, Pin 2: SCL, Pin 3: Alert, Pin 4: GND
    # Pin 5: A2,  Pin 6: A1,  Pin 7: A0,   Pin 8: VDD
    ic[1] += sda
    ic[2] += scl
    ic[3] += alert
    ic[4] += gnd
    ic[5] += a2
    ic[6] += a1
    ic[7] += a0
    ic[8] += vcc

    # 100nF decoupling cap on VDD (detected by placer as decap)
    c_dec = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0402_1005Metric",
    )
    c_dec[1] += vcc
    c_dec[2] += gnd

    # 10k I2C pull-up resistors on SDA and SCL
    r_sda = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0402_1005Metric",
    )
    r_sda[1] += vcc
    r_sda[2] += sda

    r_scl = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0402_1005Metric",
    )
    r_scl[1] += vcc
    r_scl[2] += scl

    # 10k pull-down resistors on A0, A1, A2 → default address 0x18
    r_a0 = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0402_1005Metric",
    )
    r_a0[1] += a0
    r_a0[2] += gnd

    r_a1 = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0402_1005Metric",
    )
    r_a1[1] += a1
    r_a1[2] += gnd

    r_a2 = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0402_1005Metric",
    )
    r_a2[1] += a2
    r_a2[2] += gnd

    # 10k pull-up on ALERT (open-drain output)
    r_alert = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0402_1005Metric",
    )
    r_alert[1] += vcc
    r_alert[2] += alert


# ── Instantiate subcircuits ───────────────────────────────────
mcp9808_sensor(vcc, gnd, sda, scl, alert, a0, a1, a2)
i2c_header(vcc, gnd, sda, scl, alert, a0, a1, a2)

# ── Generate schematic ────────────────────────────────────────
generate_schematic(
    auto_stub=True,
    auto_stub_fanout=3,
    erc_max_iterations=8,
)
