"""
MAX31865 RTD Temperature Amplifier Board
Precision PT100 RTD-to-Digital converter with SPI interface.
Supports 2, 3, or 4-wire RTD configuration.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# Power nets
# ============================================================
VDD = Net("VDD"); VDD.drive = POWER
GND = Net("GND"); GND.drive = POWER

# ============================================================
# SPI nets
# ============================================================
spi_sclk = Net("SCLK")
spi_sdi  = Net("SDI")
spi_sdo  = Net("SDO")
spi_cs   = Net("~{CS}")
drdy     = Net("~{DRDY}")

# ============================================================
# RTD analog nets
# ============================================================
bias_net    = Net("BIAS")
refin_p     = Net("REFIN+")
refin_n     = Net("REFIN-")
isensor_net = Net("ISENSOR")
force_p     = Net("FORCE+")
force2_net  = Net("FORCE2")
rtdin_p     = Net("RTDIN+")
rtdin_n     = Net("RTDIN-")
force_n     = Net("FORCE-")

# ============================================================
# Subcircuit: MAX31865 core with decoupling
# ============================================================
@subcircuit
def max31865_core(vdd, gnd, sclk, sdi, sdo, cs, drdy,
                  bias, refin_pos, refin_neg, isensor,
                  force_pos, force2, rtdin_pos, rtdin_neg, force_neg):
    """MAX31865 RTD-to-Digital Converter with decoupling caps."""

    # MAX31865 IC
    u1 = Part("Sensor_Temperature", "MAX31865xAP",
              footprint="Package_SO:SSOP-20_5.3x7.2mm_P0.65mm",
              value="MAX31865")

    # Power connections
    u1["DVDD"]  += vdd
    u1["VDD"]   += vdd
    u1["DGND"]  += gnd
    u1["GND"]   += gnd

    # SPI interface
    u1["SCLK"]    += sclk
    u1["SDI"]     += sdi
    u1["SDO"]     += sdo
    u1["~{CS}"]   += cs
    u1["~{DRDY}"] += drdy

    # RTD analog interface
    u1["BIAS"]    += bias
    u1["REFIN+"]  += refin_pos
    u1["REFIN-"]  += refin_neg
    u1["ISENSOR"] += isensor
    u1["FORCE+"]  += force_pos
    u1["FORCE2"]  += force2
    u1["RTDIN+"]  += rtdin_pos
    u1["RTDIN-"]  += rtdin_neg
    u1["FORCE-"]  += force_neg

    # Decoupling cap on DVDD (digital supply)
    c_dvdd = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dvdd[1] += vdd
    c_dvdd[2] += gnd

    # Decoupling cap on VDD (analog supply)
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd[1] += vdd
    c_vdd[2] += gnd

    # Bulk capacitor on supply
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vdd
    c_bulk[2] += gnd

    # BIAS filter capacitor (recommended 0.1uF per datasheet)
    c_bias = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bias[1] += bias
    c_bias[2] += gnd


# ============================================================
# Subcircuit: RTD interface (reference resistor + filter + connector)
# ============================================================
@subcircuit
def rtd_interface(gnd, refin_pos, refin_neg, isensor,
                  force_pos, force2, rtdin_pos, rtdin_neg, force_neg):
    """
    RTD front-end for 2/3/4-wire PT100.
    Reference resistor: 4.3K (Rref for PT100, ratio ~4x R0).
    Filter caps on RTDIN+/- for noise rejection.
    4-pin terminal for RTD sensor connection.
    """

    # Reference resistor: 4.3K ohm, 0.1% precision
    # Connected between REFIN+ and REFIN-
    # Also carries the excitation current from BIAS to FORCE+
    r_ref = Part("Device", "R", value="4.3K",
                 footprint="Resistor_SMD:R_0805_2012Metric")
    r_ref[1] += refin_pos
    r_ref[2] += refin_neg

    # Connect BIAS to REFIN+ (excitation current path)
    # ISENSOR ties to REFIN+ as well (current sense)
    isensor += refin_pos

    # FORCE+ connects to high side of Rref (same as REFIN+)
    force_pos += refin_pos

    # REFIN- connects to FORCE- (low side reference)
    # In 4-wire config, FORCE- is the return path
    refin_neg += force_neg

    # Input filter capacitors for RTDIN+/- (noise filtering)
    c_filt_p = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_filt_p[1] += rtdin_pos
    c_filt_p[2] += gnd

    c_filt_n = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_filt_n[1] += rtdin_neg
    c_filt_n[2] += gnd

    # Differential filter cap across RTDIN+/-
    c_diff = Part("Device", "C", value="1nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_diff[1] += rtdin_pos
    c_diff[2] += rtdin_neg

    # RTD connector: 4-pin for 4-wire RTD
    # Pin 1: FORCE+ (excitation out)
    # Pin 2: RTDIN+ (sense+)
    # Pin 3: RTDIN- (sense-)
    # Pin 4: FORCE- (excitation return)
    j_rtd = Part("Connector_Generic", "Conn_01x04",
                 footprint="Connector_JST:JST_PH_B4B-PH-K_1x04_P2.00mm_Vertical",
                 value="RTD_4WIRE")
    j_rtd[1] += force_pos
    j_rtd[2] += rtdin_pos
    j_rtd[3] += rtdin_neg
    j_rtd[4] += force_neg

    # FORCE2 connects to RTDIN- for 3-wire compensation
    force2 += rtdin_neg


# ============================================================
# Subcircuit: SPI & Power connector
# ============================================================
@subcircuit
def spi_connector(vdd, gnd, sclk, sdi, sdo, cs, drdy):
    """SPI host header: VDD, GND, SCLK, SDI, SDO, CS, DRDY."""

    j_spi = Part("Connector_Generic", "Conn_01x07",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical",
                 value="SPI_HDR")
    j_spi[1] += vdd
    j_spi[2] += gnd
    j_spi[3] += sclk
    j_spi[4] += sdi
    j_spi[5] += sdo
    j_spi[6] += cs
    j_spi[7] += drdy

    # Pull-up resistor on ~CS (keep deselected when floating)
    r_cs = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_cs[1] += vdd
    r_cs[2] += cs


# ============================================================
# Instantiate subcircuits
# ============================================================
max31865_core(VDD, GND, spi_sclk, spi_sdi, spi_sdo, spi_cs, drdy,
              bias_net, refin_p, refin_n, isensor_net,
              force_p, force2_net, rtdin_p, rtdin_n, force_n)

rtd_interface(GND, refin_p, refin_n, isensor_net,
              force_p, force2_net, rtdin_p, rtdin_n, force_n)

spi_connector(VDD, GND, spi_sclk, spi_sdi, spi_sdo, spi_cs, drdy)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True)
