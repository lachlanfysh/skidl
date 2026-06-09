"""
INA219 High Side DC Current Sensor Breakout
============================================
INA219B chip measures both high side voltage and DC current draw over I2C
with 1% precision. Handles high side current measuring up to +26VDC, even
though powered with 3 or 5V. Reports high side voltage for tracking battery
life or solar panels. Precision amplifier measures voltage across 0.1 ohm
1% sense resistor. Internal 12-bit ADC with +/-3.2A range gives 0.8mA
resolution. At minimum div8 gain, max current is +/-400mA with 0.1mA resolution.

Based on the Adafruit INA219 breakout board design.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ---------------------------------------------------------------
# Power nets
# ---------------------------------------------------------------
vcc = Net("VCC")
vcc.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# Signal nets
sda = Net("SDA")
scl = Net("SCL")
vin_plus = Net("VIN+")
vin_minus = Net("VIN-")

# ---------------------------------------------------------------
# INA219B Current/Power Monitor (SOIC-8)
# ---------------------------------------------------------------
# Pins: 1=A1, 2=A0, 3=SDA, 4=SCL, 5=VS, 6=GND, 7=IN-, 8=IN+
u1 = Part("Sensor_Energy", "INA219BxD",
          footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
          value="INA219BxD")

# Power connections
u1["VS"] += vcc
u1["GND"] += gnd

# I2C connections
u1["SDA"] += sda
u1["SCL"] += scl

# Address pins - tie to GND for default address 0x40
u1["A0"] += gnd
u1["A1"] += gnd

# Current sense inputs
u1["IN+"] += vin_plus
u1["IN-"] += vin_minus

# ---------------------------------------------------------------
# Decoupling capacitor for INA219 (100nF)
# ---------------------------------------------------------------
c1 = Part("Device", "C",
          footprint="Capacitor_SMD:C_0603_1608Metric",
          value="100nF")
c1[1] += vcc
c1[2] += gnd

# ---------------------------------------------------------------
# Bulk capacitor (10uF) for power supply stability
# ---------------------------------------------------------------
c2 = Part("Device", "C",
          footprint="Capacitor_SMD:C_0805_2012Metric",
          value="10uF")
c2[1] += vcc
c2[2] += gnd

# ---------------------------------------------------------------
# Shunt resistor - 0.1 ohm 1% current sense resistor
# ---------------------------------------------------------------
# High power resistor (2512 footprint for better power handling)
r_shunt = Part("Device", "R",
               footprint="Resistor_SMD:R_2512_6332Metric",
               value="0.1")
r_shunt[1] += vin_plus
r_shunt[2] += vin_minus

# ---------------------------------------------------------------
# I2C pull-up resistors (10K each)
# ---------------------------------------------------------------
r_sda = Part("Device", "R",
             footprint="Resistor_SMD:R_0603_1608Metric",
             value="10K")
r_sda[1] += vcc
r_sda[2] += sda

r_scl = Part("Device", "R",
             footprint="Resistor_SMD:R_0603_1608Metric",
             value="10K")
r_scl[1] += vcc
r_scl[2] += scl

# ---------------------------------------------------------------
# Breakout header - 6-pin connector
# Pins: VCC, GND, SCL, SDA, VIN+, VIN-
# ---------------------------------------------------------------
j1 = Part("Connector_Generic", "Conn_01x06",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
          value="Breakout_Header")
j1[1] += vcc
j1[2] += gnd
j1[3] += scl
j1[4] += sda
j1[5] += vin_plus
j1[6] += vin_minus

# ---------------------------------------------------------------
# Generate schematic
# ---------------------------------------------------------------
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
print("SUCCESS: Schematic generated.")
