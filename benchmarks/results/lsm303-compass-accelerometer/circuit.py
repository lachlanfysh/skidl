"""
LSM303 Compass + Accelerometer Module
Triple-axis accelerometer and magnetometer compass on single chip.
I2C interface with 5V-safe logic and power, 3.3V regulator included.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ── Power Nets ──────────────────────────────────────────────────────
vin = Net("+5V"); vin.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ── I2C and Signal Nets ────────────────────────────────────────────
sda_5v = Net("SDA_5V")
scl_5v = Net("SCL_5V")
sda_3v3 = Net("SDA_3V3")
scl_3v3 = Net("SCL_3V3")
drdy = Net("DRDY")
int1 = Net("INT1")
int2 = Net("INT2")


@subcircuit
def voltage_regulator(vin_net, vout_net, gnd_net):
    """AP2112K-3.3 LDO: 5V input to 3.3V output."""
    u_reg = Part("Regulator_Linear", "AP2112K-3.3",
                 footprint="Package_TO_SOT_SMD:SOT-23-5")
    u_reg["VIN"] += vin_net
    u_reg["EN"] += vin_net       # Enable tied to VIN (always on)
    u_reg["GND"] += gnd_net
    u_reg["VOUT"] += vout_net

    # Input decoupling cap
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vin_net
    c_in[2] += gnd_net

    # Output decoupling cap
    c_out = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out[1] += vout_net
    c_out[2] += gnd_net


@subcircuit
def i2c_level_shifter(sda_lv, scl_lv, sda_hv, scl_hv, v_low, v_high, gnd_net):
    """BSS138 bidirectional I2C level shifter (3.3V <-> 5V)."""
    # SDA channel
    q_sda = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_sda["G"] += v_low       # Gate to low-voltage rail
    q_sda["S"] += sda_lv      # Source to 3.3V side
    q_sda["D"] += sda_hv      # Drain to 5V side

    # SDA pull-ups
    r_sda_lv = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda_lv[1] += v_low
    r_sda_lv[2] += sda_lv

    r_sda_hv = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda_hv[1] += v_high
    r_sda_hv[2] += sda_hv

    # SCL channel
    q_scl = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_scl["G"] += v_low
    q_scl["S"] += scl_lv
    q_scl["D"] += scl_hv

    # SCL pull-ups
    r_scl_lv = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl_lv[1] += v_low
    r_scl_lv[2] += scl_lv

    r_scl_hv = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl_hv[1] += v_high
    r_scl_hv[2] += scl_hv


@subcircuit
def lsm303_sensor(sda_net, scl_net, drdy_net, int1_net, int2_net, vdd_net, gnd_net):
    """LSM303DLHC triple-axis accelerometer + magnetometer."""
    u_sensor = Part("Sensor_Motion", "LSM303DLHC",
                    footprint="Package_LGA:LGA-14_3x5mm_P0.8mm_LayoutBorder1x6y")
    u_sensor["VDD"] += vdd_net
    u_sensor["VDDIO"] += vdd_net
    u_sensor["GND"] += gnd_net
    u_sensor["SDA"] += sda_net
    u_sensor["SCL"] += scl_net
    u_sensor["DRDY"] += drdy_net
    u_sensor["INT1"] += int1_net
    u_sensor["INT2"] += int2_net

    # SETC and SETP need external capacitors per datasheet
    setc_net = Net("SETC")
    setp_net = Net("SETP")
    u_sensor["SETC"] += setc_net
    u_sensor["SETP"] += setp_net

    c_setc = Part("Device", "C", value="220nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_setc[1] += setc_net
    c_setc[2] += gnd_net

    c_setp = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_setp[1] += setp_net
    c_setp[2] += gnd_net

    # C1 pin requires bypass capacitor per datasheet
    c1_net = Net("C1_BYPASS")
    u_sensor["C1"] += c1_net
    c_c1 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_c1[1] += c1_net
    c_c1[2] += gnd_net

    # VDD decoupling
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd[1] += vdd_net
    c_vdd[2] += gnd_net

    # VDDIO decoupling
    c_vddio = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vddio[1] += vdd_net
    c_vddio[2] += gnd_net


# ── Breakout Header (1x8) ──────────────────────────────────────────
header = Part("Connector_Generic", "Conn_01x08",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
header[1] += vin       # VIN (5V)
header[2] += gnd       # GND
header[3] += v3v3      # 3V3 output
header[4] += sda_5v    # SDA (5V level)
header[5] += scl_5v    # SCL (5V level)
header[6] += drdy      # Data ready
header[7] += int1      # Interrupt 1
header[8] += int2      # Interrupt 2


# ── Instantiate Subcircuits ─────────────────────────────────────────
voltage_regulator(vin, v3v3, gnd)
i2c_level_shifter(sda_3v3, scl_3v3, sda_5v, scl_5v, v3v3, vin, gnd)
lsm303_sensor(sda_3v3, scl_3v3, drdy, int1, int2, v3v3, gnd)


# ── Generate Schematic ──────────────────────────────────────────────
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
