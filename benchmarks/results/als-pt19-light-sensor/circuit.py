"""
ALS-PT19 Analog Light Sensor Breakout
======================================
Wide-spectrum analog ambient light sensor with logarithmic response.
Circuit: NPN phototransistor + 10K load resistor voltage divider,
100nF decoupling cap, 3-pin header (VCC/GND/AOUT).

Generated via eda-mcp submit_skidl_code. Job succeeded first pass,
layout score 92.1/100, board 15.0x10.7mm.
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
aout = Net("AOUT")

# ALS-PT19 phototransistor (0603 SMD)
# Collector to VCC via load resistor, emitter to GND
# Analog output tapped at collector — voltage rises with increasing light
pt = Part("Device", "Q_Photo_NPN", footprint="Resistor_SMD:R_0603_1608Metric")
pt.ref = "Q1"
pt.value = "ALS-PT19"

# 10K load resistor forms voltage divider with phototransistor
r_load = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
r_load.ref = "R1"

# 100nF decoupling cap on VCC
c_dec = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
c_dec.ref = "C1"

# 3-pin header: pin1=VCC, pin2=GND, pin3=AOUT
j = Part("Connector", "Conn_01x03_Pin", footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
j.ref = "J1"

# Wiring
vcc += r_load[1], c_dec[1]
r_load[2] += pt["C"], aout
pt["E"] += gnd
gnd += c_dec[2]

j[1] += vcc
j[2] += gnd
j[3] += aout
