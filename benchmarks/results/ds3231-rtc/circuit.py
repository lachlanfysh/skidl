"""
DS3231 Precision Real-Time Clock Module
========================================
Extremely accurate RTC with integrated temperature-compensated crystal oscillator.
Features:
- DS3231M TCXO RTC IC (SOIC-16W)
- CR2032 coin cell battery backup
- I2C interface with pull-up resistors
- 32KHz output with pull-up
- INT/SQW alarm/square wave output with pull-up
- Power LED indicator
- Decoupling capacitors for clean power
- I2C and alarm header connectors
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


# ============================================================
# Power nets
# ============================================================
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
vbat = Net("VBAT"); vbat.drive = POWER

# Signal nets
sda = Net("SDA")
scl = Net("SCL")
sqw_int = Net("SQW_INT")
out_32k = Net("OUT_32K")
rst_n = Net("RST_N")


# ============================================================
# DS3231M RTC IC subcircuit
# ============================================================
@subcircuit
def rtc_core(vcc, gnd, vbat, sda, scl, sqw_int, out_32k, rst_n):
    """DS3231M RTC with decoupling caps."""

    # DS3231M RTC IC
    u1 = Part("Timer_RTC", "DS3231M",
              footprint="Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm",
              value="DS3231M")

    # Connect power pins
    u1["VCC"] += vcc
    u1["VBAT"] += vbat
    u1["GND"] += gnd

    # Connect I2C
    u1["SDA"] += sda
    u1["SCL"] += scl

    # Connect outputs
    u1["~{INT}/SQW"] += sqw_int
    u1["32KHZ"] += out_32k
    u1["~{RST}"] += rst_n

    # VCC decoupling: 100nF ceramic
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += vcc
    c1[2] += gnd

    # VBAT decoupling: 100nF ceramic
    c2 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c2[1] += vbat
    c2[2] += gnd


# ============================================================
# I2C pull-ups and signal conditioning subcircuit
# ============================================================
@subcircuit
def i2c_pullups(vcc, gnd, sda, scl, sqw_int, out_32k, rst_n):
    """I2C pull-up resistors and output pull-ups."""

    # SDA pull-up: 4.7K
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vcc
    r_sda[2] += sda

    # SCL pull-up: 4.7K
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vcc
    r_scl[2] += scl

    # INT/SQW pull-up: 4.7K (open-collector output)
    r_int = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_int[1] += vcc
    r_int[2] += sqw_int

    # 32KHz output pull-up: 4.7K (open-collector output)
    r_32k = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_32k[1] += vcc
    r_32k[2] += out_32k

    # RST pull-up: 10K
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += vcc
    r_rst[2] += rst_n


# ============================================================
# Battery backup subcircuit
# ============================================================
@subcircuit
def battery_backup(vbat, gnd):
    """CR2032 coin cell for battery backup."""

    bat = Part("Device", "Battery", value="CR2032",
               footprint="Battery:BatteryHolder_Keystone_3002_1x2032")
    bat["+"] += vbat
    bat["-"] += gnd


# ============================================================
# Power indicator LED subcircuit
# ============================================================
@subcircuit
def power_led(vcc, gnd):
    """Power indicator LED with current limiting resistor."""

    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    led = Part("Device", "LED", value="Green",
               footprint="LED_SMD:LED_0603_1608Metric")

    vcc & r_led & led & gnd


# ============================================================
# Connectors subcircuit
# ============================================================
@subcircuit
def connectors(vcc, gnd, sda, scl, sqw_int, out_32k):
    """I2C header and auxiliary output header."""

    # I2C header: VCC, GND, SDA, SCL (4-pin)
    j_i2c = Part("Connector_Generic", "Conn_01x04",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                 value="I2C")
    j_i2c[1] += vcc
    j_i2c[2] += gnd
    j_i2c[3] += sda
    j_i2c[4] += scl

    # Alarm/32KHz output header: SQW/INT, 32KHz, GND (3-pin)
    j_aux = Part("Connector_Generic", "Conn_01x03",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                 value="AUX_OUT")
    j_aux[1] += sqw_int
    j_aux[2] += out_32k
    j_aux[3] += gnd


# ============================================================
# Instantiate all subcircuits
# ============================================================
rtc_core(vcc, gnd, vbat, sda, scl, sqw_int, out_32k, rst_n)
i2c_pullups(vcc, gnd, sda, scl, sqw_int, out_32k, rst_n)
battery_backup(vbat, gnd)
power_led(vcc, gnd)
connectors(vcc, gnd, sda, scl, sqw_int, out_32k)

# Generate output
generate_schematic(auto_stub=True)
