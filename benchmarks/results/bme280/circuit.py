"""
BME280 Temperature/Humidity/Pressure Sensor Breakout Board

Bosch BME280 environmental sensor breakout with:
- BME280 sensor (I2C/SPI interface)
- AP2112K-3.3 LDO regulator for 3.3V from 5V input
- I2C pull-up resistors (10K on SDA, SCL)
- Decoupling capacitors on VDD, VDDIO, and regulator
- 6-pin header: VIN, GND, SCL, SDA, SDO (addr select), CS
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


@subcircuit
def power_supply(vin, gnd, vdd_3v3):
    """3.3V LDO regulator with input and output decoupling caps."""

    # AP2112K-3.3 LDO regulator (SOT-23-5)
    reg = Part(
        "Regulator_Linear", "AP2112K-3.3",
        value="AP2112K-3.3",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
    )
    reg["VIN"] += vin
    reg["GND"] += gnd
    reg["EN"] += vin       # Enable tied to VIN (always on)
    reg["VOUT"] += vdd_3v3

    # Input decoupling cap (1uF ceramic on VIN)
    c_in = Part(
        "Device", "C",
        value="1uF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_in[1] += vin
    c_in[2] += gnd

    # Output decoupling cap (1uF ceramic on 3.3V output)
    c_out = Part(
        "Device", "C",
        value="1uF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_out[1] += vdd_3v3
    c_out[2] += gnd


@subcircuit
def bme280_sensor(vdd, gnd, sda, scl, sdo, csb):
    """BME280 sensor with decoupling cap."""

    # BME280 environmental sensor (LGA-8)
    bme = Part(
        "Sensor", "BME280",
        value="BME280",
        footprint="Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering",
    )
    bme["VDD"] += vdd
    bme["VDDIO"] += vdd     # VDDIO same as VDD (3.3V logic)
    bme["GND"] += gnd       # Both GND pins (1 and 7) auto-connected
    bme["SDI"] += sda       # I2C SDA / SPI SDI
    bme["SCK"] += scl       # I2C SCL / SPI SCK
    bme["SDO"] += sdo       # I2C address select / SPI SDO
    bme["CSB"] += csb       # SPI chip select (high = I2C mode)

    # 100nF decoupling cap on VDD
    c_vdd = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_vdd[1] += vdd
    c_vdd[2] += gnd


@subcircuit
def i2c_pullups(vdd, sda, scl):
    """10K pull-up resistors for I2C bus."""

    # SDA pull-up
    r_sda = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sda[1] += vdd
    r_sda[2] += sda

    # SCL pull-up
    r_scl = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_scl[1] += vdd
    r_scl[2] += scl


# ---- Top-level nets ----
vin = Net("VIN")
gnd = Net("GND");    gnd.drive = POWER
vdd = Net("+3V3");   vdd.drive = POWER

sda = Net("SDA")
scl = Net("SCL")
sdo = Net("SDO")
csb = Net("CSB")

# ---- Instantiate subcircuits ----
power_supply(vin, gnd, vdd)
bme280_sensor(vdd, gnd, sda, scl, sdo, csb)
i2c_pullups(vdd, sda, scl)

# ---- CSB pull-up to VDD (I2C mode default) ----
r_csb = Part(
    "Device", "R",
    value="10K",
    footprint="Resistor_SMD:R_0603_1608Metric",
)
r_csb[1] += vdd
r_csb[2] += csb

# ---- 6-pin header: VIN, GND, SCL, SDA, SDO, CS ----
hdr = Part(
    "Connector_Generic", "Conn_01x06",
    value="Header_1x06",
    footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
)
hdr[1] += vin     # VIN (5V or 3.3V input)
hdr[2] += gnd     # GND
hdr[3] += scl     # SCL
hdr[4] += sda     # SDA
hdr[5] += sdo     # SDO / I2C address select
hdr[6] += csb     # CS / SPI chip select

# ---- Generate schematic ----
generate_schematic(auto_stub=True, auto_stub_fanout=3)
