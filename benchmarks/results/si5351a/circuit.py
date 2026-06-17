"""
Si5351A I2C clock generator breakout board.
- Si5351A-B-GM QFN-20: 3 independent outputs, 8kHz-150MHz
- 25MHz crystal reference (SMD 3225-4pin)
- 3.3V LDO from 5V VIN (AMS1117-3.3)
- I2C interface with 4.7k pull-ups
- CLK0/CLK1/CLK2 available on 2.54mm pin headers
"""

from skidl import *

# Power rails
vcc3v3 = Net("3V3"); vcc3v3.drive = POWER
vcc5v = Net("VIN"); vcc5v.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C nets
i2c_sda = Net("SDA")
i2c_scl = Net("SCL")

# Clock output nets
clk0 = Net("CLK0")
clk1 = Net("CLK1")
clk2 = Net("CLK2")

# Crystal nets
xa = Net("XA")
xb = Net("XB")


@subcircuit
def si5351a_block(vcc3v3, gnd, sda, scl, clk0, clk1, clk2, xa, xb):
    """Si5351A clock generator IC with decoupling."""
    u1 = Part("Oscillator", "Si5351A-B-GM",
              footprint="Package_DFN_QFN:QFN-20-1EP_4x4mm_P0.5mm_EP2.7x2.7mm",
              value="Si5351A")

    vcc3v3 += u1["VDD"], u1["VDDOA"], u1["VDDOB"], u1["VDDOC"], u1["VDDOD"]
    gnd += u1["GND"]
    u1["XA"] += xa
    u1["XB"] += xb
    u1["SDA"] += sda
    u1["SCL"] += scl
    u1["A0"] += gnd        # I2C addr 0x60
    u1["OEB"] += gnd       # Enable all outputs
    u1["SSEN"] += gnd      # No spread spectrum
    u1["CLK0"] += clk0
    u1["CLK1"] += clk1
    u1["CLK2"] += clk2
    # Unused outputs tied to GND (ERC warnings expected per design brief)
    gnd += u1["CLK3"], u1["CLK4"], u1["CLK5"], u1["CLK6"], u1["CLK7"]

    # VDD core decoupling (0603 for easier routing)
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += vcc3v3; c1[2] += gnd

    # Shared output supply decoupling (2 caps for VDDOA-D cluster)
    c2 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c2[1] += vcc3v3; c2[2] += gnd

    c3 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c3[1] += vcc3v3; c3[2] += gnd

    # 3V3 bulk
    cb = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    cb[1] += vcc3v3; cb[2] += gnd


@subcircuit
def crystal_block(gnd, xa, xb):
    """25MHz crystal with load capacitors."""
    y1 = Part("Device", "Crystal_GND23",
              footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
              value="25MHz")
    y1[1] += xa
    y1[4] += xb
    y1[2] += gnd
    y1[3] += gnd

    c_xa = Part("Device", "C", value="18pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xb = Part("Device", "C", value="18pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xa[1] += xa; c_xa[2] += gnd
    c_xb[1] += xb; c_xb[2] += gnd


@subcircuit
def power_block(vin, vcc3v3, gnd):
    """AMS1117-3.3 LDO with input/output capacitors."""
    u2 = Part("Regulator_Linear", "AMS1117-3.3",
              footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2",
              value="AMS1117-3.3")
    vin += u2["VI"]
    gnd += u2["GND"]
    vcc3v3 += u2["VO"]

    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin; c_in[2] += gnd

    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vcc3v3; c_out[2] += gnd


@subcircuit
def connector_block(vin, vcc3v3, gnd, sda, scl, clk0, clk1, clk2):
    """Headers: power/I2C on left, clock outputs on right."""
    # I2C pull-ups to 3V3
    r_sda = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vcc3v3; r_sda[2] += sda
    r_scl[1] += vcc3v3; r_scl[2] += scl

    # Power + I2C header: VIN, GND, SDA, SCL
    j1 = Part("Connector", "Conn_01x04_Pin",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
              value="PWR+I2C")
    j1.edge_preference = "left"
    j1[1] += vin
    j1[2] += gnd
    j1[3] += sda
    j1[4] += scl

    # CLK output headers (signal + GND per output)
    j2 = Part("Connector", "Conn_01x02_Pin",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
              value="CLK0")
    j2.edge_preference = "right"
    j2[1] += clk0; j2[2] += gnd

    j3 = Part("Connector", "Conn_01x02_Pin",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
              value="CLK1")
    j3.edge_preference = "right"
    j3[1] += clk1; j3[2] += gnd

    j4 = Part("Connector", "Conn_01x02_Pin",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
              value="CLK2")
    j4.edge_preference = "right"
    j4[1] += clk2; j4[2] += gnd


# Instantiate blocks
si5351a_block(vcc3v3, gnd, i2c_sda, i2c_scl, clk0, clk1, clk2, xa, xb)
crystal_block(gnd, xa, xb)
power_block(vcc5v, vcc3v3, gnd)
connector_block(vcc5v, vcc3v3, gnd, i2c_sda, i2c_scl, clk0, clk1, clk2)

EDA_FLOORPLAN = {
    "outline": {"width_mm": 75, "height_mm": 55, "corner_radius_mm": 2.0},
}
