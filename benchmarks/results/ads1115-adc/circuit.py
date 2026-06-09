"""
ADS1115 16-bit ADC Breakout Board
=================================
Based on the Adafruit ADS1115 breakout design.
Features:
- ADS1115 16-bit I2C ADC with PGA
- 4 analog input channels (AIN0-AIN3)
- I2C interface with pull-up resistors
- ADDR pin pull-down for default address (0x48)
- Alert/Ready output with pull-up
- Decoupling capacitors for power supply
- 10-pin breakout header for all signals
- Works with 3.3V and 5V microcontrollers
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


@subcircuit
def ads1115_adc(vdd, gnd, sda, scl, alert_rdy, ain0, ain1, ain2, ain3, addr_net):
    """ADS1115 ADC with decoupling and address configuration."""

    # ADS1115 16-bit ADC (TSSOP-10 / VSSOP-10)
    # Pin mapping from KiCad Analog_ADC library:
    #  1=ADDR, 2=ALERT/RDY, 3=GND, 4=AIN0, 5=AIN1,
    #  6=AIN2, 7=AIN3, 8=VDD, 9=SDA, 10=SCL
    u1 = Part(
        "Analog_ADC", "ADS1115IDGS",
        value="ADS1115",
        footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm",
    )

    # Power connections
    u1["VDD"] += vdd
    u1["GND"] += gnd

    # I2C bus connections
    u1["SDA"] += sda
    u1["SCL"] += scl

    # Alert/Ready output (active-low, open-drain)
    u1["ALERT/RDY"] += alert_rdy

    # Analog input channels
    u1["AIN0"] += ain0
    u1["AIN1"] += ain1
    u1["AIN2"] += ain2
    u1["AIN3"] += ain3

    # Address pin
    u1["ADDR"] += addr_net

    # --- Decoupling capacitors ---
    # Bulk decoupling: 10uF ceramic
    c1 = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c1[1] += vdd
    c1[2] += gnd

    # Local bypass: 100nF ceramic (auto-detected as decoupling cap)
    c2 = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c2[1] += vdd
    c2[2] += gnd


@subcircuit
def i2c_pullups(vdd, sda, scl):
    """I2C pull-up resistors (10K for compatibility with 3.3V and 5V)."""

    # SDA pull-up
    r_sda = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sda[1] += vdd
    r_sda[2] += sda

    # SCL pull-up
    r_scl = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_scl[1] += vdd
    r_scl[2] += scl


@subcircuit
def alert_pullup(vdd, alert_rdy):
    """Pull-up resistor for the ALERT/RDY open-drain output."""

    r_alert = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_alert[1] += vdd
    r_alert[2] += alert_rdy


@subcircuit
def addr_config(gnd, addr_net):
    """ADDR pin connected to GND via resistor for default I2C address 0x48."""

    r_addr = Part(
        "Device", "R",
        value="0R",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_addr[1] += addr_net
    r_addr[2] += gnd


# ========== Top-level nets ==========

# Power nets
vdd = Net("VDD")
vdd.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# I2C bus
sda = Net("SDA")
scl = Net("SCL")

# Alert/Ready output
alert_rdy = Net("ALERT_RDY")

# Analog inputs
ain0 = Net("AIN0")
ain1 = Net("AIN1")
ain2 = Net("AIN2")
ain3 = Net("AIN3")

# Address configuration net
addr_net = Net("ADDR")

# ========== Instantiate subcircuits ==========

ads1115_adc(vdd, gnd, sda, scl, alert_rdy, ain0, ain1, ain2, ain3, addr_net)
i2c_pullups(vdd, sda, scl)
alert_pullup(vdd, alert_rdy)
addr_config(gnd, addr_net)

# ========== Breakout header ==========
# 10-pin header: VDD, GND, SCL, SDA, ALERT/RDY, ADDR, AIN0, AIN1, AIN2, AIN3
j1 = Part(
    "Connector_Generic", "Conn_01x10",
    value="BREAKOUT",
    footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical",
)

j1[1] += vdd
j1[2] += gnd
j1[3] += scl
j1[4] += sda
j1[5] += alert_rdy
j1[6] += addr_net
j1[7] += ain0
j1[8] += ain1
j1[9] += ain2
j1[10] += ain3

# ========== Generate schematic ==========
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
