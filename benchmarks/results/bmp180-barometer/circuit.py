"""
BMP180 Barometric Pressure Sensor Module
==========================================
Precision barometric pressure and temperature sensor with altitude measurement.
Features:
- BMP280 sensor IC (I2C mode, pin-compatible successor to BMP180)
- AMS1117-3.3 LDO: 5V input to 3.3V for sensor
- BSS138 MOSFET-based bidirectional I2C level shifter (5V <-> 3.3V)
- I2C pull-ups on both voltage domains
- Decoupling capacitors on power rails
- 5-pin header: VCC(5V), GND, SCL, SDA, 3V3

BMP280 pinout (Bosch LGA-8):
  Pin 1: GND
  Pin 2: CSB (chip select, tie to VDDIO for I2C mode)
  Pin 3: SDI/SDA (I2C data)
  Pin 4: SCK/SCL (I2C clock)
  Pin 5: SDO (I2C address select: GND=0x76, VDDIO=0x77)
  Pin 6: VDDIO (I/O voltage)
  Pin 7: GND
  Pin 8: VDD (supply)
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# Global power nets
# ============================================================
vcc_5v = Net("+5V")
vcc_5v.drive = POWER

vcc_3v3 = Net("+3V3")
vcc_3v3.drive = POWER

gnd = Net("GND")
gnd.drive = POWER

# I2C bus nets (5V side)
sda_5v = Net("SDA_5V")
scl_5v = Net("SCL_5V")

# I2C bus nets (3.3V side)
sda_3v3 = Net("SDA_3V3")
scl_3v3 = Net("SCL_3V3")


# ============================================================
# Subcircuit: Voltage Regulator (5V -> 3.3V)
# ============================================================
@subcircuit
def voltage_regulator(vin, vout, gnd_net):
    """AMS1117-3.3 LDO with input/output decoupling."""
    # AMS1117-3.3: pin 3=VI, pin 1=GND, pin 2=VO
    reg = Part("Regulator_Linear", "AMS1117-3.3",
               footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")

    reg["VI"] += vin
    reg["GND"] += gnd_net
    reg["VO"] += vout

    # Input decoupling cap (10uF)
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin
    c_in[2] += gnd_net

    # Output decoupling cap (10uF for LDO stability)
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vout
    c_out[2] += gnd_net

    # Additional 100nF output cap for high-frequency decoupling
    c_hf = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_hf[1] += vout
    c_hf[2] += gnd_net


# ============================================================
# Subcircuit: I2C Level Shifter (one channel)
# ============================================================
@subcircuit
def i2c_level_shift_channel(low_side, high_side, v_low, v_high, gnd_net):
    """
    BSS138 bidirectional level shifter for one I2C line.
    - Gate tied to low-voltage rail
    - Source on low-voltage side with pull-up to V_LOW
    - Drain on high-voltage side with pull-up to V_HIGH
    """
    # BSS138 N-MOSFET: G=gate, S=source, D=drain
    q = Part("Transistor_FET", "BSS138",
             footprint="Package_TO_SOT_SMD:SOT-23")
    q["G"] += v_low       # Gate to low-voltage rail
    q["S"] += low_side     # Source to 3.3V side
    q["D"] += high_side    # Drain to 5V side

    # Pull-up on low-voltage side (10K to 3.3V)
    r_low = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_low[1] += v_low
    r_low[2] += low_side

    # Pull-up on high-voltage side (10K to 5V)
    r_high = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_high[1] += v_high
    r_high[2] += high_side


# ============================================================
# Subcircuit: BMP280 Sensor (I2C mode)
# ============================================================
@subcircuit
def bmp_sensor(vdd, vddio, gnd_net, sda, scl):
    """
    BMP280 barometric pressure sensor in I2C mode.
    CSB tied to VDDIO (selects I2C interface).
    SDO tied to GND (I2C address 0x76).
    """
    bmp = Part("Sensor_Pressure", "BMP280",
               footprint="Package_LGA:Bosch_LGA-8_2x2.5mm_P0.65mm_ClockwisePinNumbering")

    bmp["VDD"] += vdd
    bmp["VDDIO"] += vddio
    bmp["GND"] += gnd_net
    bmp["SDI"] += sda
    bmp["SCK"] += scl

    # CSB tied to VDDIO for I2C mode
    bmp["CSB"] += vddio

    # SDO tied to GND for I2C address 0x76
    bmp["SDO"] += gnd_net

    # Decoupling cap for BMP280 VDD (100nF, required by datasheet)
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd[1] += vdd
    c_vdd[2] += gnd_net

    # Decoupling cap for VDDIO (100nF)
    c_vddio = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vddio[1] += vddio
    c_vddio[2] += gnd_net


# ============================================================
# Build the circuit
# ============================================================

# 1. Voltage regulator: 5V -> 3.3V
voltage_regulator(vcc_5v, vcc_3v3, gnd)

# 2. I2C level shifter: SDA channel
i2c_level_shift_channel(sda_3v3, sda_5v, vcc_3v3, vcc_5v, gnd)

# 3. I2C level shifter: SCL channel
i2c_level_shift_channel(scl_3v3, scl_5v, vcc_3v3, vcc_5v, gnd)

# 4. BMP280 sensor (VDD and VDDIO both from 3.3V rail)
bmp_sensor(vcc_3v3, vcc_3v3, gnd, sda_3v3, scl_3v3)

# 5. Input/output connector (5-pin header)
# Pin 1: VCC (5V), Pin 2: GND, Pin 3: SCL, Pin 4: SDA, Pin 5: 3V3 (optional output)
conn = Part("Connector_Generic", "Conn_01x05",
            footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical")
conn[1] += vcc_5v
conn[2] += gnd
conn[3] += scl_5v
conn[4] += sda_5v
conn[5] += vcc_3v3

# 6. Bulk input decoupling on 5V rail (near connector)
c_bulk = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
c_bulk[1] += vcc_5v
c_bulk[2] += gnd

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
