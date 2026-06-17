"""BME680 Air Quality Sensor Breakout Board

Bosch BME680 environmental sensor with temperature, humidity, barometric
pressure, and VOC gas sensing. SPI and I2C interfaces. 3.3V and 5V compatible
with onboard AP2112K-3.3 LDO regulator and TXB0104D bidirectional level shifter.
"""

from skidl import *

# ── Power nets ──────────────────────────────────────────────────────
vin = Net("VIN"); vin.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ── Signal nets ─────────────────────────────────────────────────────
# High-voltage side (host/connector side)
sda_hi = Net("SDA_HI")
scl_hi = Net("SCL_HI")
sdo_hi = Net("SDO_HI")
csb_hi = Net("CSB_HI")

# Low-voltage side (3.3V, BME680 side)
sda_lo = Net("SDA_LO")
scl_lo = Net("SCL_LO")
sdo_lo = Net("SDO_LO")
csb_lo = Net("CSB_LO")


# ── Subcircuit: Voltage Regulator ───────────────────────────────────
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 LDO: 600mA, 3.8-6V input, 3.3V output, SOT-23-5."""
    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"]  += vin
    reg["GND"]  += gnd
    reg["EN"]   += vin   # Always enabled
    reg["VOUT"] += vout
    reg[4]      += gnd   # NC pin tied to GND by pin number

    # Input decoupling
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output decoupling (bulk + HF)
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vout
    c_bulk[2] += gnd

    c_hf = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_hf[1] += vout
    c_hf[2] += gnd


# ── Subcircuit: Level Shifter ───────────────────────────────────────
@subcircuit
def level_shifter(vcca, vccb, gnd, a1, a2, a3, a4, b1, b2, b3, b4):
    """TXB0104D 4-ch bidirectional level translator. A=3.3V, B=VIN."""
    xlat = Part("Logic_LevelTranslator", "TXB0104D",
                footprint="Package_SO:SOIC-14_3.9x8.7mm_P1.27mm")
    xlat["VCCA"] += vcca
    xlat["VCCB"] += vccb
    xlat["GND"]  += gnd
    xlat["OE"]   += vcca   # Always enabled
    xlat["A1"]   += a1
    xlat["A2"]   += a2
    xlat["A3"]   += a3
    xlat["A4"]   += a4
    xlat["B1"]   += b1
    xlat["B2"]   += b2
    xlat["B3"]   += b3
    xlat["B4"]   += b4
    # NC pins 6 and 9 tied to GND by number
    xlat[6]      += gnd
    xlat[9]      += gnd

    # VCCA decoupling
    c_a = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c_a[1] += vcca
    c_a[2] += gnd

    # VCCB decoupling
    c_b = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c_b[1] += vccb
    c_b[2] += gnd


# ── Subcircuit: BME680 Sensor ──────────────────────────────────────
@subcircuit
def bme680_sensor(vdd, vddio, gnd, sdi, sck, sdo, csb):
    """BME680 4-in-1 environmental sensor (LGA-8, 3x3mm)."""
    sensor = Part("Sensor", "BME680",
                  footprint="Package_LGA:Bosch_LGA-8_3x3mm_P0.8mm_ClockwisePinNumbering")
    sensor["VDD"]   += vdd
    sensor["VDDIO"] += vddio
    sensor["GND"]   += gnd   # Connects both GND pins (1 and 7)
    sensor["SDI"]   += sdi
    sensor["SCK"]   += sck
    sensor["SDO"]   += sdo
    sensor["CSB"]   += csb

    # VDD and VDDIO decoupling (100nF each, placed near sensor)
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vdd[1] += vdd
    c_vdd[2] += gnd

    c_vddio = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vddio[1] += vddio
    c_vddio[2] += gnd


# ── Subcircuit: I2C Pull-ups ────────────────────────────────────────
@subcircuit
def pullups(vdd, sda, scl, csb):
    """I2C pull-ups (4.7K on SDA/SCL) and CSB pull-up for I2C mode."""
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vdd
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vdd
    r_scl[2] += scl

    r_csb = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_csb[1] += vdd
    r_csb[2] += csb


# ── Breakout Header ────────────────────────────────────────────────
# 6-pin 2.54mm pitch header: VIN, GND, SCL, SDA, SDO, CSB
header = Part("Connector_Generic", "Conn_01x06",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
header.edge_preference = "bottom"
header[1] += vin
header[2] += gnd
header[3] += scl_hi
header[4] += sda_hi
header[5] += sdo_hi
header[6] += csb_hi


# ── Instantiate subcircuits ─────────────────────────────────────────
voltage_regulator(vin, v3v3, gnd)
level_shifter(v3v3, vin, gnd,
              sda_lo, scl_lo, sdo_lo, csb_lo,
              sda_hi, scl_hi, sdo_hi, csb_hi)
bme680_sensor(v3v3, v3v3, gnd, sda_lo, scl_lo, sdo_lo, csb_lo)
pullups(v3v3, sda_lo, scl_lo, csb_lo)
