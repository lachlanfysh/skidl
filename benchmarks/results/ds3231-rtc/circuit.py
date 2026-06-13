"""
DS3231 RTC Breakout Board — MCP Server Design
===============================================
Extremely accurate I2C RTC with integrated TCXO.
Battery-backed by CR2032 coin cell.

MCP server run: job fb6e63a9b681 / run 71915ecab428
Board: 67.5 x 52.5mm, layout score 57.9/100, 0 overlaps
Remaining issue: 1 DRC clearance violation (Freerouting non-determinism)

Key note: use VCOIN (not VBAT) to avoid server auto-enriching a lipo
charger (MCP73831 block) for the coin cell rail. VBAT triggers lipo
charger enrichment with VBUS net, causing 23-part congestion.
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
vcoin = Net("VCOIN"); vcoin.drive = POWER  # CR2032 coin cell - avoid VBAT name

# Signal nets
sda = Net("SDA")
scl = Net("SCL")
sqw = Net("SQW")
clk32 = Net("32KHZ")


@subcircuit
def rtc_block(vcc, vcoin, gnd, sda, scl, sqw, clk32):
    """DS3231MZ RTC IC (SOIC-8) with decoupling caps."""
    u1 = Part("Timer_RTC", "DS3231MZ",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    u1["VCC"] += vcc
    u1["VBAT"] += vcoin
    u1["GND"] += gnd
    u1["SDA"] += sda
    u1["SCL"] += scl
    u1["~{INT}/SQW"] += sqw
    u1["32KHZ"] += clk32
    u1["~{RST}"] += vcc  # RST pulled high — not exposed externally

    # VCC decoupling
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += vcc; c1[2] += gnd

    # VCC bulk cap
    c2 = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    c2[1] += vcc; c2[2] += gnd

    # VCOIN decoupling
    c3 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c3[1] += vcoin; c3[2] += gnd


@subcircuit
def battery_block(vcoin, gnd):
    """CR2032 coin cell holder (Keystone 3034, through-hole, 20mm)."""
    bt1 = Part("Device", "Battery_Cell",
               footprint="Battery:BatteryHolder_Keystone_3034_1x20mm")
    bt1["+"] += vcoin
    bt1["-"] += gnd


@subcircuit
def i2c_connector(vcc, gnd, sda, scl):
    """4-pin I2C header: VCC, GND, SDA, SCL with 4.7K pull-ups."""
    j1 = Part("Connector_Generic", "Conn_01x04",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    j1[1] += vcc; j1[2] += gnd; j1[3] += sda; j1[4] += scl

    r1 = Part("Device", "R", value="4.7K",
              footprint="Resistor_SMD:R_0603_1608Metric")
    r1[1] += vcc; r1[2] += sda

    r2 = Part("Device", "R", value="4.7K",
              footprint="Resistor_SMD:R_0603_1608Metric")
    r2[1] += vcc; r2[2] += scl


@subcircuit
def output_connector(vcc, gnd, sqw, clk32):
    """4-pin output header: VCC, GND, SQW/INT, 32KHz.
    SQW is open-drain — pulled high by 10K resistor.
    """
    j2 = Part("Connector_Generic", "Conn_01x04",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    j2[1] += vcc; j2[2] += gnd; j2[3] += sqw; j2[4] += clk32

    r3 = Part("Device", "R", value="10K",
              footprint="Resistor_SMD:R_0603_1608Metric")
    r3[1] += vcc; r3[2] += sqw


# Instantiate all blocks
rtc_block(vcc, vcoin, gnd, sda, scl, sqw, clk32)
battery_block(vcoin, gnd)
i2c_connector(vcc, gnd, sda, scl)
output_connector(vcc, gnd, sqw, clk32)
