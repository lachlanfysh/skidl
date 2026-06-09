"""BME680 Air Quality Sensor Breakout Board

All-in-one environmental sensor from Bosch with temperature, humidity,
barometric pressure, and VOC gas sensing. Features:
- BME680 sensor with SPI and I2C interfaces
- AP2112K-3.3 LDO regulator (accepts 3.3-5V input)
- TXB0104 bidirectional level shifter for I2C/SPI lines
- Decoupling capacitors on all power rails
- 6-pin breakout header (VIN, GND, SCL, SDA, SDO, CSB)
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ── Power nets ──────────────────────────────────────────────────────
vin = Net("VIN"); vin.drive = POWER        # Input voltage (3.3-5V)
v3v3 = Net("+3V3"); v3v3.drive = POWER     # Regulated 3.3V
gnd = Net("GND"); gnd.drive = POWER        # Ground

# ── Signal nets ─────────────────────────────────────────────────────
sda_hi = Net("SDA_HI")     # I2C data, high-voltage side
scl_hi = Net("SCL_HI")     # I2C clock, high-voltage side
sdo_hi = Net("SDO_HI")     # SPI MISO / I2C addr select, high-voltage side
csb_hi = Net("CSB_HI")     # SPI chip select, high-voltage side

sda_lo = Net("SDA_LO")     # I2C data, 3.3V side (to BME680)
scl_lo = Net("SCL_LO")     # I2C clock, 3.3V side
sdo_lo = Net("SDO_LO")     # SDO, 3.3V side
csb_lo = Net("CSB_LO")     # CSB, 3.3V side


# ── Subcircuit: Voltage Regulator ───────────────────────────────────
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 LDO regulator with input/output decoupling."""
    # AP2112K-3.3: Pin1=VIN, Pin2=GND, Pin3=EN, Pin4=NC, Pin5=VOUT
    reg = Part("Regulator_Linear", "AP2112K-3.3", value="AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg[1] += vin       # VIN
    reg[2] += gnd       # GND
    reg[3] += vin       # EN tied to VIN (always enabled)
    # Pin 4 is NC
    reg[5] += vout      # VOUT

    # Input decoupling capacitor
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output decoupling capacitor
    c_out = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out[1] += vout
    c_out[2] += gnd

    # Additional output bulk cap for stability
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vout
    c_bulk[2] += gnd


# ── Subcircuit: Level Shifter ───────────────────────────────────────
@subcircuit
def level_shifter(vcca, vccb, gnd, a1, a2, a3, a4, b1, b2, b3, b4):
    """TXB0104D 4-channel bidirectional level shifter.

    A-side = low voltage (3.3V sensor side)
    B-side = high voltage (host side, could be 5V or 3.3V)
    """
    # TXB0104D: 14-pin SOIC
    # Pin1=VCCA, Pin2=A1, Pin3=A2, Pin4=A3, Pin5=A4, Pin6=NC
    # Pin7=GND, Pin8=OE, Pin9=NC, Pin10=B4, Pin11=B3, Pin12=B2, Pin13=B1, Pin14=VCCB
    xlat = Part("Logic_LevelTranslator", "TXB0104D", value="TXB0104D",
                footprint="Package_SO:SOIC-14_3.9x8.7mm_P1.27mm")
    xlat["VCCA"] += vcca    # Low-voltage supply (3.3V)
    xlat["VCCB"] += vccb    # High-voltage supply (VIN)
    xlat["GND"]  += gnd
    xlat["OE"]   += vcca    # Output enable tied to VCCA (always on)
    xlat["A1"]   += a1      # SDA low side
    xlat["A2"]   += a2      # SCL low side
    xlat["A3"]   += a3      # SDO low side
    xlat["A4"]   += a4      # CSB low side
    xlat["B1"]   += b1      # SDA high side
    xlat["B2"]   += b2      # SCL high side
    xlat["B3"]   += b3      # SDO high side
    xlat["B4"]   += b4      # CSB high side

    # Decoupling cap on VCCA
    c_a = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c_a[1] += vcca
    c_a[2] += gnd

    # Decoupling cap on VCCB
    c_b = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c_b[1] += vccb
    c_b[2] += gnd


# ── Subcircuit: BME680 Sensor ──────────────────────────────────────
@subcircuit
def bme680_sensor(vdd, vddio, gnd, sdi, sck, sdo, csb):
    """BME680 environmental sensor with decoupling.

    Pins: VDD(8), VDDIO(6), GND(1,7), SDI(3), SCK(4), SDO(5), CSB(2)
    SDI = I2C SDA / SPI MOSI
    SCK = I2C SCL / SPI SCLK
    SDO = SPI MISO / I2C address select (GND=0x76, VDDIO=0x77)
    CSB = Chip select (pull high for I2C mode)
    """
    sensor = Part("Sensor", "BME680", value="BME680",
                  footprint="Package_LGA:Bosch_LGA-8_3x3mm_P0.8mm_ClockwisePinNumbering")
    sensor["VDD"]   += vdd
    sensor["VDDIO"] += vddio
    sensor["GND"]   += gnd     # Both GND pins (1 and 7) connected
    sensor["SDI"]   += sdi
    sensor["SCK"]   += sck
    sensor["SDO"]   += sdo
    sensor["CSB"]   += csb

    # VDD decoupling
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd[1] += vdd
    c_vdd[2] += gnd

    # VDDIO decoupling
    c_vddio = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vddio[1] += vddio
    c_vddio[2] += gnd


# ── Subcircuit: I2C Pull-ups and CSB Pull-up ───────────────────────
@subcircuit
def pullups(vdd, sda, scl, csb):
    """I2C pull-up resistors (10K) and CSB pull-up for I2C mode."""
    # SDA pull-up on 3.3V side (sensor side)
    r_sda = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vdd
    r_sda[2] += sda

    # SCL pull-up on 3.3V side
    r_scl = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vdd
    r_scl[2] += scl

    # CSB pull-up to VDDIO (selects I2C mode when high)
    r_csb = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_csb[1] += vdd
    r_csb[2] += csb


# ── Breakout Header ────────────────────────────────────────────────
# 6-pin header: VIN, GND, SCL, SDA, SDO, CSB (high-voltage side)
header = Part("Connector_Generic", "Conn_01x06",
              value="Conn_01x06",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
header[1] += vin        # VIN
header[2] += gnd        # GND
header[3] += scl_hi     # SCL (host side)
header[4] += sda_hi     # SDA (host side)
header[5] += sdo_hi     # SDO (host side)
header[6] += csb_hi     # CSB (host side)


# ── Instantiate subcircuits ─────────────────────────────────────────
voltage_regulator(vin, v3v3, gnd)
level_shifter(v3v3, vin, gnd,
              sda_lo, scl_lo, sdo_lo, csb_lo,
              sda_hi, scl_hi, sdo_hi, csb_hi)
bme680_sensor(v3v3, v3v3, gnd, sda_lo, scl_lo, sdo_lo, csb_lo)
pullups(v3v3, sda_lo, scl_lo, csb_lo)


# ── Generate schematic ──────────────────────────────────────────────
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
