import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# ADS1115 16-bit I2C ADC Breakout (Adafruit-style)
# ============================================================
# Features:
# - ADS1115 precision 16-bit ADC with PGA
# - I2C interface with pull-ups
# - 4 analog input channels (A0-A3)
# - ALERT/RDY output
# - ADDR pin with default GND connection
# - Stemma QT (JST SH) connectors for I2C daisy-chain
# - Standard breakout header
# ============================================================

# Power nets
vdd = Net("VDD"); vdd.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Signal nets
sda = Net("SDA")
scl = Net("SCL")
alert_rdy = Net("ALERT_RDY")
addr = Net("ADDR")
ain0 = Net("AIN0")
ain1 = Net("AIN1")
ain2 = Net("AIN2")
ain3 = Net("AIN3")

# ----------------------------------------------------------
# ADS1115 ADC
# ----------------------------------------------------------
@subcircuit
def ads1115_adc(vdd, gnd, sda, scl, alert_rdy, addr, ain0, ain1, ain2, ain3):
    """ADS1115 16-bit ADC with decoupling."""
    # ADS1115 IC
    # Pins: 1=ADDR, 2=ALERT/RDY, 3=GND, 4=AIN0, 5=AIN1,
    #        6=AIN2, 7=AIN3, 8=VDD, 9=SDA, 10=SCL
    u1 = Part("Analog_ADC", "ADS1115IDGS",
              footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm")
    u1["VDD"] += vdd
    u1["GND"] += gnd
    u1["SDA"] += sda
    u1["SCL"] += scl
    u1["ALERT/RDY"] += alert_rdy
    u1["ADDR"] += addr
    u1["AIN0"] += ain0
    u1["AIN1"] += ain1
    u1["AIN2"] += ain2
    u1["AIN3"] += ain3

    # Decoupling: 100nF ceramic close to VDD
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += vdd
    c1[2] += gnd

    # Bulk decoupling: 10uF
    c2 = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    c2[1] += vdd
    c2[2] += gnd

# ----------------------------------------------------------
# I2C Pull-ups and ADDR Config
# ----------------------------------------------------------
@subcircuit
def addr_config(vdd, gnd, sda, scl, alert_rdy, addr):
    """I2C pull-ups and ADDR pin configuration."""
    # 10K pull-up on SDA
    r_sda = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vdd
    r_sda[2] += sda

    # 10K pull-up on SCL
    r_scl = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vdd
    r_scl[2] += scl

    # 10K pull-up on ALERT/RDY (active low, open drain)
    r_alert = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    r_alert[1] += vdd
    r_alert[2] += alert_rdy

    # ADDR to GND via 0R (default I2C address 0x48)
    r_addr = Part("Device", "R", value="0R",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_addr[1] += addr
    r_addr[2] += gnd

# ----------------------------------------------------------
# Breakout Header
# ----------------------------------------------------------
@subcircuit
def breakout_header(vdd, gnd, sda, scl, alert_rdy, addr, ain0, ain1, ain2, ain3):
    """10-pin breakout header for breadboard use."""
    j1 = Part("Connector_Generic", "Conn_01x10",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
    j1[1] += vdd       # VDD
    j1[2] += gnd       # GND
    j1[3] += scl       # SCL
    j1[4] += sda       # SDA
    j1[5] += addr      # ADDR
    j1[6] += alert_rdy # ALERT/RDY
    j1[7] += ain0      # A0
    j1[8] += ain1      # A1
    j1[9] += ain2      # A2
    j1[10] += ain3     # A3

# ----------------------------------------------------------
# Stemma QT / JST SH Connectors
# ----------------------------------------------------------
@subcircuit
def stemma_qt(vdd, gnd, sda, scl):
    """Two JST SH 4-pin Stemma QT connectors for I2C daisy-chain."""
    # Stemma QT connector 1
    j_qt1 = Part("Connector_Generic", "Conn_01x04",
                  footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    j_qt1[1] += gnd
    j_qt1[2] += vdd
    j_qt1[3] += sda
    j_qt1[4] += scl

    # Stemma QT connector 2
    j_qt2 = Part("Connector_Generic", "Conn_01x04",
                  footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    j_qt2[1] += gnd
    j_qt2[2] += vdd
    j_qt2[3] += sda
    j_qt2[4] += scl

# ----------------------------------------------------------
# Instantiate all subcircuits
# ----------------------------------------------------------
ads1115_adc(vdd, gnd, sda, scl, alert_rdy, addr, ain0, ain1, ain2, ain3)
addr_config(vdd, gnd, sda, scl, alert_rdy, addr)
breakout_header(vdd, gnd, sda, scl, alert_rdy, addr, ain0, ain1, ain2, ain3)
stemma_qt(vdd, gnd, sda, scl)

# Generate schematic
generate_schematic(auto_stub=True)
