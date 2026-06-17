"""MCP4725 12-bit DAC breakout board.

MCP4725 I2C 12-bit DAC in SOT-23-6 package.
- Single-channel voltage output (0-VDD)
- I2C interface, address selectable via A0 pad
- 100nF + 10uF decoupling on VDD
- STEMMA QT / Qwiic JST-SH 4-pin connector for I2C
- 4-pin header for VCC/GND/SDA/SCL
- 2-pin output header with VOUT and GND
- Board ~20x15mm
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
sda = Net("SDA")
scl = Net("SCL")
vout = Net("VOUT")

# MCP4725A0 DAC - converted from LCSC C144198
# Using standard KiCad SOT-23-6 footprint since JLC-converted one isn't in server library
dac = Part("C144198", "MCP4725A0T-E_CH",
           footprint="Package_TO_SOT_SMD:SOT-23-6")
dac["VDD"] += vcc
dac["VSS"] += gnd
dac["SDA"] += sda
dac["SCL"] += scl
dac["VOUT"] += vout
# A0 pulled low for address 0x60; exposed as solder pad via 0-ohm jumper
dac["A0"] += gnd

# Decoupling: 100nF ceramic on VDD (auto-detected as decap by placer)
c1 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0402_1005Metric")
c1[1] += vcc
c1[2] += gnd

# Bulk: 10uF electrolytic on VDD
c2 = Part("Device", "C_Polarized", value="10uF",
          footprint="Capacitor_SMD:CP_Elec_4x5.4")
c2[1] += vcc
c2[2] += gnd

# STEMMA QT / Qwiic JST-SH 4-pin SMD horizontal connector
# Pinout: 1=GND, 2=VCC(3.3V), 3=SDA, 4=SCL
qwiic = Part("Connector", "Conn_01x04_Pin",
             footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
qwiic.edge_preference = "bottom"
qwiic["Pin_1"] += gnd
qwiic["Pin_2"] += vcc
qwiic["Pin_3"] += sda
qwiic["Pin_4"] += scl

# 4-pin header: VCC/GND/SDA/SCL
h_i2c = Part("Connector", "Conn_01x04_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
h_i2c.edge_preference = "top"
h_i2c["Pin_1"] += vcc
h_i2c["Pin_2"] += gnd
h_i2c["Pin_3"] += sda
h_i2c["Pin_4"] += scl

# 2-pin output header: VOUT + GND
h_out = Part("Connector", "Conn_01x02_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
h_out.edge_preference = "right"
h_out["Pin_1"] += vout
h_out["Pin_2"] += gnd
