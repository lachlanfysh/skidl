"""
BNO055 9-DOF Absolute Orientation Sensor Breakout
===================================================
Bosch BNO055 smart 9-DOF sensor with on-chip sensor fusion.
Features:
  - BNO055 IMU with accelerometer, magnetometer, gyroscope
  - AP2112K-3.3 LDO for 3.3V regulation from VIN (3.3-5V)
  - 32.768 kHz crystal for BNO055 clock
  - Two STEMMA QT / Qwiic JST SH 4-pin I2C connectors
  - I2C pull-up resistors
  - Address select jumper
  - Breakout header with VIN, 3V3, GND, SDA, SCL, INT, RST
  - Decoupling capacitors on all power rails
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ---- Power Nets ----
vin_net = Net("VIN")
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ---- I2C Bus Nets ----
sda = Net("SDA")
scl = Net("SCL")

# ---- Signal Nets ----
int_net = Net("INT")
rst_net = Net("nRESET")

# ============================================================
# Subcircuit: 3.3V LDO Regulator (AP2112K-3.3)
# ============================================================
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 LDO with input/output decoupling."""
    # LDO regulator
    u_reg = Part("Regulator_Linear", "AP2112K-3.3",
                 footprint="Package_TO_SOT_SMD:SOT-23-5",
                 value="AP2112K-3.3")
    u_reg["VIN"] += vin
    u_reg["VOUT"] += vout
    u_reg["GND"] += gnd
    u_reg["EN"] += vin  # Always enabled (tie EN to VIN)
    u_reg["NC"] += NC   # No connect pin

    # Input decoupling cap (10uF on VIN)
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output decoupling cap (10uF on 3V3)
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vout
    c_out[2] += gnd

    # Additional 100nF output filter
    c_out2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out2[1] += vout
    c_out2[2] += gnd

# ============================================================
# Subcircuit: BNO055 IMU Sensor
# ============================================================
@subcircuit
def bno055_sensor(vdd, vddio, gnd_net, sda_net, scl_net, int_pin, rst_pin):
    """BNO055 9-DOF IMU with crystal and decoupling."""

    # BNO055 IMU
    u_imu = Part("Sensor_Motion", "BNO055",
                 footprint="Package_LGA:LGA-28_5.2x3.8mm_P0.5mm",
                 value="BNO055")

    # Power connections
    u_imu["VDD"] += vdd
    u_imu["VDDIO"] += vddio
    u_imu["GND"] += gnd_net
    u_imu["GNDIO"] += gnd_net

    # I2C interface (COM0=SDA, COM1=SCL for I2C mode)
    # PS0=LOW, PS1=LOW selects I2C mode
    u_imu["COM0"] += sda_net     # SDA in I2C mode
    u_imu["COM1"] += scl_net     # SCL in I2C mode
    u_imu["COM2"] += gnd_net     # Unused in I2C mode, tied to GND
    u_imu["COM3"] += gnd_net     # I2C address select: GND=0x28, VDDIO=0x29

    # Protocol selection: PS0=LOW, PS1=LOW for I2C mode
    u_imu["PS0"] += gnd_net
    u_imu["PS1"] += gnd_net

    # Control signals
    u_imu["INT"] += int_pin
    u_imu["~{RESET}"] += rst_pin
    u_imu["~{BOOT_LOAD_PIN}"] += vddio  # Pull high for normal operation
    u_imu["BL_IND"] += NC  # Boot loader indicator - not connected

    # Unused pins
    u_imu["PIN1"] += NC
    u_imu["PIN7"] += NC
    u_imu["PIN8"] += NC
    u_imu["PIN12"] += NC
    u_imu["PIN13"] += NC
    u_imu["PIN15"] += NC
    u_imu["PIN16"] += NC
    u_imu["PIN21"] += NC
    u_imu["PIN22"] += NC
    u_imu["PIN23"] += NC
    u_imu["PIN24"] += NC

    # CAP pin: connect 100nF to GND per datasheet
    cap_net = Net("BNO_CAP")
    u_imu["CAP"] += cap_net
    c_cap = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_cap[1] += cap_net
    c_cap[2] += gnd_net

    # 32.768 kHz crystal for BNO055 internal oscillator
    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    u_imu["XIN32"] += xtal[1]
    u_imu["XOUT32"] += xtal[2]

    # Crystal load capacitors (12pF typical for 32.768kHz)
    xin_net = Net("XIN32")
    xout_net = Net("XOUT32")
    xtal[1] += xin_net
    xtal[2] += xout_net

    c_xin = Part("Device", "C", value="12pF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xin[1] += xin_net
    c_xin[2] += gnd_net

    c_xout = Part("Device", "C", value="12pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xout[1] += xout_net
    c_xout[2] += gnd_net

    # VDD decoupling cap (100nF)
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd[1] += vdd
    c_vdd[2] += gnd_net

    # VDDIO decoupling cap (100nF)
    c_vddio = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vddio[1] += vddio
    c_vddio[2] += gnd_net

# ============================================================
# Subcircuit: I2C Pull-ups and Signal Conditioning
# ============================================================
@subcircuit
def i2c_interface(vddio, gnd_net, sda_net, scl_net, int_pin, rst_pin):
    """I2C pull-ups and reset/interrupt pull-ups."""

    # I2C pull-up resistors (4.7K to VDDIO)
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vddio
    r_sda[2] += sda_net

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vddio
    r_scl[2] += scl_net

    # Reset pull-up (10K to VDDIO) - active low
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += vddio
    r_rst[2] += rst_pin

    # INT pull-up (10K to VDDIO) - optional but typical
    r_int = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_int[1] += vddio
    r_int[2] += int_pin

# ============================================================
# Subcircuit: Connectors
# ============================================================
@subcircuit
def connectors(vin, v3v3_net, gnd_net, sda_net, scl_net, int_pin, rst_pin):
    """Breakout header and two STEMMA QT / Qwiic JST SH connectors."""

    # Main breakout header: VIN, 3V3, GND, SDA, SCL, INT, RST
    j_hdr = Part("Connector_Generic", "Conn_01x07",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical",
                 value="Header")
    j_hdr[1] += vin
    j_hdr[2] += v3v3_net
    j_hdr[3] += gnd_net
    j_hdr[4] += sda_net
    j_hdr[5] += scl_net
    j_hdr[6] += int_pin
    j_hdr[7] += rst_pin

    # STEMMA QT / Qwiic connector 1 (JST SH 4-pin: GND, VCC, SDA, SCL)
    j_qt1 = Part("Connector_Generic", "Conn_01x04",
                 footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
                 value="STEMMA_QT")
    j_qt1[1] += gnd_net
    j_qt1[2] += v3v3_net
    j_qt1[3] += sda_net
    j_qt1[4] += scl_net

    # STEMMA QT / Qwiic connector 2 (daisy chain)
    j_qt2 = Part("Connector_Generic", "Conn_01x04",
                 footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
                 value="STEMMA_QT")
    j_qt2[1] += gnd_net
    j_qt2[2] += v3v3_net
    j_qt2[3] += sda_net
    j_qt2[4] += scl_net

# ============================================================
# Top-level: Instantiate subcircuits
# ============================================================

# Power regulation
voltage_regulator(vin_net, v3v3, gnd)

# BNO055 IMU sensor (VDD and VDDIO both from 3.3V)
bno055_sensor(v3v3, v3v3, gnd, sda, scl, int_net, rst_net)

# I2C interface (pull-ups, signal conditioning)
i2c_interface(v3v3, gnd, sda, scl, int_net, rst_net)

# Connectors (breakout header + STEMMA QT)
connectors(vin_net, v3v3, gnd, sda, scl, int_net, rst_net)

# Generate schematic
generate_schematic(auto_stub=True)
