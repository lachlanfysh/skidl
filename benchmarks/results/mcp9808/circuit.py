"""
MCP9808 High Accuracy I2C Temperature Sensor Breakout
=====================================================
I2C digital temperature sensor with +/-0.25C accuracy over -40C to +125C,
+0.0625C precision. 3 address pins (A0-A2) with pull-down resistors allow
up to 8 devices on a single I2C bus. 2.7V to 5.5V logic.

Breakout board with:
- MCP9808 sensor in MSOP-8 package
- 100nF decoupling cap on VDD
- 10K pull-up resistors on SDA and SCL
- 10K pull-down resistors on A0, A1, A2 address pins
- Alert output pin (active-low, open-drain)
- 6-pin header for VDD, GND, SDA, SCL, Alert, and spare GND
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


@subcircuit
def mcp9808_breakout(vdd, gnd, sda, scl, alert):
    """MCP9808 temperature sensor with supporting passives."""

    # MCP9808 in MSOP-8 package from Sensor_Temperature library
    # Pin names: SDA, SCL, A0, A1, A2, V_{DD}, GND, Alert
    u1 = Part(
        "Sensor_Temperature",
        "MCP9808_MSOP",
        footprint="Package_SO:MSOP-8_3x3mm_P0.65mm",
    )

    # Connect power (note: KiCad uses V_{DD} with subscript notation)
    u1["V_{DD}"] += vdd
    u1["GND"] += gnd

    # Connect I2C
    u1["SDA"] += sda
    u1["SCL"] += scl

    # Connect alert output
    u1["Alert"] += alert

    # 100nF decoupling capacitor on VDD
    c1 = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += vdd
    c1[2] += gnd

    # I2C pull-up resistors (10K)
    r_sda = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vdd
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vdd
    r_scl[2] += scl

    # Address pin pull-down resistors (10K) - default address 0x18
    r_a0 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
    r_a0[1] += u1["A0"]
    r_a0[2] += gnd

    r_a1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
    r_a1[1] += u1["A1"]
    r_a1[2] += gnd

    r_a2 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
    r_a2[1] += u1["A2"]
    r_a2[2] += gnd


# Create power nets
vdd = Net("VDD")
vdd.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# Create signal nets
sda = Net("SDA")
scl = Net("SCL")
alert = Net("ALERT")

# Instantiate the breakout circuit
mcp9808_breakout(vdd, gnd, sda, scl, alert)

# 6-pin breakout header: VDD, GND, SDA, SCL, ALERT, GND
j1 = Part(
    "Connector_Generic",
    "Conn_01x06",
    footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
)
j1[1] += vdd
j1[2] += gnd
j1[3] += sda
j1[4] += scl
j1[5] += alert
j1[6] += gnd

# Generate schematic and netlist
generate_schematic(auto_stub=True)

print("MCP9808 breakout board circuit generated successfully.")
print(f"Parts: {len(default_circuit.parts)}")
print(f"Nets: {len(default_circuit.nets)}")
