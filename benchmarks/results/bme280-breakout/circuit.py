#!/usr/bin/env python3
"""
BME280 Environmental Sensor Breakout Board

Bosch BME280: temperature (+/-1C), humidity (+/-3%), barometric pressure (+/-1 hPa).
Supports both I2C and SPI. Small breakout board with 0.1" (2.54mm) header pins
for easy breadboard use.

Interface header pinout (J1, 6-pin, 2.54mm):
  1: VCC  (3.3V supply)
  2: GND
  3: SCL/SCK  (I2C clock / SPI clock)
  4: SDA/SDI  (I2C data / SPI MOSI)
  5: SDO/MISO (I2C addr select / SPI MISO)
  6: CS       (SPI chip select, tie to VCC for I2C)

I2C address selection:
  SDO = GND  → 0x76
  SDO = VCC  → 0x77
  (2-pin jumper J2 connects SDO to either GND or VCC)
"""

import os
import sys

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


# ── Subcircuit: BME280 sensor core ───────────────────────────────────────────
@subcircuit
def bme280_sensor(vcc, gnd, scl, sda, sdo, cs):
    """BME280 LGA-8 sensor with decoupling caps."""

    # BME280 - Bosch LGA-8 2.5x2.5mm package
    # Pin 1: GND, Pin 2: CSB, Pin 3: SDI, Pin 4: SCK
    # Pin 5: SDO, Pin 6: VDDIO, Pin 7: GND, Pin 8: VDD
    bme = Part(
        "Sensor", "BME280",
        footprint="Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering"
    )
    bme[1] += gnd    # GND
    bme[2] += cs     # CSB - chip select / I2C-SPI select
    bme[3] += sda    # SDI - SPI MOSI / I2C SDA
    bme[4] += scl    # SCK - SPI/I2C clock
    bme[5] += sdo    # SDO - SPI MISO / I2C address select
    bme[6] += vcc    # VDDIO - IO voltage
    bme[7] += gnd    # GND (pad 7 is also GND)
    bme[8] += vcc    # VDD - core supply

    # 100nF decoupling cap on VDD (required per datasheet, placed close to sensor)
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += vcc
    c1[2] += gnd

    # 100nF decoupling cap on VDDIO
    c2 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2[1] += vcc
    c2[2] += gnd

    # Additional 1uF bulk cap for stability
    c3 = Part("Device", "C", value="1uF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c3[1] += vcc
    c3[2] += gnd


# ── Subcircuit: I2C pull-up resistors ────────────────────────────────────────
@subcircuit
def i2c_pullups(vcc, scl, sda):
    """4.7k pull-up resistors for I2C SDA and SCL lines."""

    r_scl = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl[1] += vcc
    r_scl[2] += scl

    r_sda = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc
    r_sda[2] += sda


# ── Subcircuit: Interface header + address select jumper ─────────────────────
@subcircuit
def interface_header(vcc, gnd, scl, sda, sdo, cs):
    """
    6-pin 2.54mm header for breadboard connection.
    2-pin jumper for I2C address selection (SDO to GND or VCC).
    """

    # Main 6-pin interface header (2.54mm pitch, breadboard compatible)
    # Pinout: VCC, GND, SCL, SDA, SDO, CS
    j1 = Part("Connector_Generic", "Conn_01x06",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
    j1[1] += vcc   # VCC (3.3V)
    j1[2] += gnd   # GND
    j1[3] += scl   # SCL / SCK
    j1[4] += sda   # SDA / SDI
    j1[5] += sdo   # SDO / MISO (also I2C addr)
    j1[6] += cs    # CS  / CSB  (tie to VCC for I2C mode)

    # 2-pin jumper for address select: SDO to GND (addr 0x76) or VCC (addr 0x77)
    # Use a 3-pin header with centre = SDO, pin1 = GND, pin3 = VCC
    j2 = Part("Connector_Generic", "Conn_01x03",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
    j2[1] += gnd   # GND side  (addr 0x76 when jumpered to centre)
    j2[2] += sdo   # SDO centre
    j2[3] += vcc   # VCC side  (addr 0x77 when jumpered to centre)

    # CS pull-up: 10k to VCC (default I2C mode, CSB=high disables SPI)
    r_cs = Part("Device", "R", value="10k",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_cs[1] += vcc
    r_cs[2] += cs


# ── Top level ─────────────────────────────────────────────────────────────────
# Power nets
vcc = Net("VCC")
vcc.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# Signal nets
scl = Net("SCL")
sda = Net("SDA")
sdo = Net("SDO")
cs  = Net("CS")

# Instantiate subcircuits
bme280_sensor(vcc, gnd, scl, sda, sdo, cs)
i2c_pullups(vcc, scl, sda)
interface_header(vcc, gnd, scl, sda, sdo, cs)

# Generate schematic
generate_schematic(
    auto_stub=True,
    auto_stub_fanout=3,
    erc_max_iterations=8,
)
