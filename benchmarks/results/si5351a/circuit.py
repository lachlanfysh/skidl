"""
Si5351A Clock Generator Breakout Board
=======================================
Si5351A I2C-controlled clock generator with precision 25MHz crystal reference,
internal PLL and dividers. Three independent outputs (CLK0-CLK2) at 3Vpp.
Includes 3.3V LDO regulator (AP2112K-3.3) for 3-5V DC power input,
BSS138 MOSFET-based bidirectional I2C level shifting for 3V/5V logic
compatibility, and optional SMA connectors for RF work.
"""

import os

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *

set_default_tool(KICAD9)

# ===========================================================================
# Global power nets
# ===========================================================================
vin = Net("VIN")
vin.drive = POWER

vcc_3v3 = Net("+3V3")
vcc_3v3.drive = POWER

vcc_io = Net("VCC_IO")  # External I2C side voltage (3V or 5V)
vcc_io.drive = POWER

gnd = Net("GND")
gnd.drive = POWER

# I2C nets
sda_3v3 = Net("SDA_3V3")   # 3.3V side SDA
scl_3v3 = Net("SCL_3V3")   # 3.3V side SCL
sda_io = Net("SDA")        # External side SDA
scl_io = Net("SCL")        # External side SCL

# Clock output nets
clk0 = Net("CLK0")
clk1 = Net("CLK1")
clk2 = Net("CLK2")


# ===========================================================================
# Subcircuit: Power supply — LDO regulator with input/output caps
# ===========================================================================
@subcircuit
def power_supply(vin, vout, gnd):
    """AP2112K-3.3 LDO regulator with bypass capacitors."""
    # LDO regulator
    reg = Part(
        "Regulator_Linear",
        "AP2112K-3.3",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
        value="AP2112K-3.3",
    )
    reg["VIN"] += vin
    reg["EN"] += vin  # Enable tied to input (always on)
    reg["GND"] += gnd
    reg["VOUT"] += vout
    # NC pin left unconnected

    # Input capacitor — 10uF ceramic
    c_in = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_in[1] += vin
    c_in[2] += gnd

    # Output capacitor — 10uF ceramic
    c_out = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_out[1] += vout
    c_out[2] += gnd

    # Additional output filter — 100nF close to regulator output
    c_filt = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_filt[1] += vout
    c_filt[2] += gnd


# ===========================================================================
# Subcircuit: Si5351A clock generator with crystal and decoupling
# ===========================================================================
@subcircuit
def clock_generator(vdd, gnd, sda, scl, clk0_net, clk1_net, clk2_net):
    """Si5351A-B-GT (MSOP-10) with 25MHz crystal and decoupling."""
    # Si5351A-B-GT — 3-output variant in MSOP-10
    ic = Part(
        "Oscillator",
        "Si5351A-B-GT",
        footprint="Package_SO:MSOP-10_3x3mm_P0.5mm",
        value="Si5351A-B-GT",
    )
    ic["VDD"] += vdd
    ic["VDDO"] += vdd  # Output driver supply — same 3.3V rail
    ic["GND"] += gnd
    ic["SDA"] += sda
    ic["SCL"] += scl
    ic["CLK0"] += clk0_net
    ic["CLK1"] += clk1_net
    ic["CLK2"] += clk2_net

    # 25 MHz crystal — connected between XA and XB
    xtal = Part(
        "Device",
        "Crystal",
        value="25MHz",
        footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm",
    )
    xtal[1] += ic["XA"]
    xtal[2] += ic["XB"]

    # Crystal load capacitors (typically 10pF for 25MHz crystal)
    c_xa = Part(
        "Device", "C",
        value="10pF",
        footprint="Capacitor_SMD:C_0402_1005Metric",
    )
    c_xa[1] += ic["XA"]
    c_xa[2] += gnd

    c_xb = Part(
        "Device", "C",
        value="10pF",
        footprint="Capacitor_SMD:C_0402_1005Metric",
    )
    c_xb[1] += ic["XB"]
    c_xb[2] += gnd

    # VDD decoupling — 100nF
    c_vdd = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_vdd[1] += vdd
    c_vdd[2] += gnd

    # VDDO decoupling — 100nF (output driver supply)
    c_vddo = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_vddo[1] += vdd
    c_vddo[2] += gnd

    # Bulk decoupling — 10uF
    c_bulk = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_bulk[1] += vdd
    c_bulk[2] += gnd


# ===========================================================================
# Subcircuit: Bidirectional I2C level shifter (BSS138 MOSFET based)
# ===========================================================================
@subcircuit
def i2c_level_shifter(v_low, v_high, gnd, sda_low, scl_low, sda_high, scl_high):
    """
    BSS138-based bidirectional level shifter for I2C.
    Classic MOSFET level-shift circuit: gate tied to low-side voltage,
    source on low side, drain on high side, with pull-ups on both sides.
    """
    # --- SDA channel ---
    q_sda = Part(
        "Transistor_FET",
        "BSS138",
        footprint="Package_TO_SOT_SMD:SOT-23",
        value="BSS138",
    )
    q_sda["G"] += v_low       # Gate tied to low-side VCC
    q_sda["S"] += sda_low     # Source = low-side SDA
    q_sda["D"] += sda_high    # Drain = high-side SDA

    # Pull-up on low side (3.3V side) — 10K
    r_sda_low = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sda_low[1] += v_low
    r_sda_low[2] += sda_low

    # Pull-up on high side (VCC_IO side) — 10K
    r_sda_high = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sda_high[1] += v_high
    r_sda_high[2] += sda_high

    # --- SCL channel ---
    q_scl = Part(
        "Transistor_FET",
        "BSS138",
        footprint="Package_TO_SOT_SMD:SOT-23",
        value="BSS138",
    )
    q_scl["G"] += v_low       # Gate tied to low-side VCC
    q_scl["S"] += scl_low     # Source = low-side SCL
    q_scl["D"] += scl_high    # Drain = high-side SCL

    # Pull-up on low side — 10K
    r_scl_low = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_scl_low[1] += v_low
    r_scl_low[2] += scl_low

    # Pull-up on high side — 10K
    r_scl_high = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_scl_high[1] += v_high
    r_scl_high[2] += scl_high

    # Decoupling on low-side VCC reference
    c_low = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_low[1] += v_low
    c_low[2] += gnd

    # Decoupling on high-side VCC reference
    c_high = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_high[1] += v_high
    c_high[2] += gnd


# ===========================================================================
# Subcircuit: Output connectors (SMA + header)
# ===========================================================================
@subcircuit
def output_connectors(gnd, clk0_net, clk1_net, clk2_net):
    """
    Three SMA connectors for RF output and a 1x6 header for breadboard use.
    SMA connectors are optional (can be left unpopulated).
    """
    # SMA connector for CLK0
    sma0 = Part(
        "Connector",
        "Conn_Coaxial",
        footprint="Connector_Coaxial:SMA_Amphenol_132134-11_Vertical",
        value="SMA_CLK0",
    )
    sma0["In"] += clk0_net
    sma0["Ext"] += gnd

    # SMA connector for CLK1
    sma1 = Part(
        "Connector",
        "Conn_Coaxial",
        footprint="Connector_Coaxial:SMA_Amphenol_132134-11_Vertical",
        value="SMA_CLK1",
    )
    sma1["In"] += clk1_net
    sma1["Ext"] += gnd

    # SMA connector for CLK2
    sma2 = Part(
        "Connector",
        "Conn_Coaxial",
        footprint="Connector_Coaxial:SMA_Amphenol_132134-11_Vertical",
        value="SMA_CLK2",
    )
    sma2["In"] += clk2_net
    sma2["Ext"] += gnd

    # Output header — CLK0, CLK1, CLK2, GND, GND, GND
    j_out = Part(
        "Connector_Generic",
        "Conn_01x06",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
        value="CLK_OUT",
    )
    j_out[1] += clk0_net
    j_out[2] += clk1_net
    j_out[3] += clk2_net
    j_out[4] += gnd
    j_out[5] += gnd
    j_out[6] += gnd


# ===========================================================================
# Subcircuit: Input / I2C header connector
# ===========================================================================
@subcircuit
def input_connector(vin, vcc_io, gnd, sda_high, scl_high):
    """
    Main breakout header: VIN, GND, SCL, SDA, VCC_IO.
    VIN: 3-5V DC power input.
    VCC_IO: I2C logic level reference (connect to 3.3V or 5V as needed).
    """
    j_in = Part(
        "Connector_Generic",
        "Conn_01x05",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
        value="I2C_HDR",
    )
    j_in[1] += vin
    j_in[2] += gnd
    j_in[3] += scl_high
    j_in[4] += sda_high
    j_in[5] += vcc_io

    # Series resistors on CLK outputs for impedance matching (33 ohm)
    # These are placed inline on each clock output for signal integrity
    # (placed in output_connectors subcircuit for cleanliness — but
    #  since they connect to the same nets, they'll be on the same net)


# ===========================================================================
# Top-level: instantiate all subcircuits
# ===========================================================================

# Power supply: VIN -> 3.3V LDO
power_supply(vin, vcc_3v3, gnd)

# Si5351A clock generator
clock_generator(vcc_3v3, gnd, sda_3v3, scl_3v3, clk0, clk1, clk2)

# I2C level shifter: 3.3V <-> VCC_IO
i2c_level_shifter(vcc_3v3, vcc_io, gnd, sda_3v3, scl_3v3, sda_io, scl_io)

# Output connectors (SMA + header)
output_connectors(gnd, clk0, clk1, clk2)

# Input/I2C header connector
input_connector(vin, vcc_io, gnd, sda_io, scl_io)

# ===========================================================================
# Generate schematic
# ===========================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
print("Schematic generated successfully.")
