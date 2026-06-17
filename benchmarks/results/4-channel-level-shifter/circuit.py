"""
4-Channel TXB0104 Bidirectional Level Shifter
TI TXB0104 4-bit bidirectional voltage-level translator breakout.
Translates between 1.2V-3.6V (VCCA) and 1.65V-5.5V (VCCB) logic levels.
Push-pull outputs, no external pull-ups needed. 4 bidirectional channels.
OE pin with 100K pull-down. 100nF decoupling on VCCA and VCCB.
2x 6-pin headers for A-side and B-side connections.

MCP run: job_id=49e5def6482b, run_id=1e4ccda223e6
Board: 33.4mm x 23.1mm (auto), layout score 87.1/100
"""

from skidl import *

# Power rails
vcca = Net("VCCA"); vcca.drive = POWER  # Low-voltage side (1.2V-3.6V)
vccb = Net("VCCB"); vccb.drive = POWER  # High-voltage side (1.65V-5.5V)
gnd = Net("GND"); gnd.drive = POWER

# TXB0104 4-bit bidirectional level translator (TSSOP-14)
# Symbol from LCSC C60708 via convert_lcsc, footprint from KiCad standard library
u1 = Part("C60708", "TXB0104PWR", footprint="Package_SO:TSSOP-14_4.4x5mm_P0.65mm")

# Power connections
u1["VCCA"] += vcca
u1["VCCB"] += vccb
u1["GND"] += gnd

# OE pin with 100K pull-down to GND (active-high, pulled low = disabled by default)
r_oe = Part("Device", "R", value="100K", footprint="Resistor_SMD:R_0402_1005Metric")
oe_net = Net("OE")
oe_net += u1["OE"]
r_oe[1] += oe_net
r_oe[2] += gnd

# Decoupling caps: 100nF on VCCA and VCCB
c_vcca = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric")
c_vcca[1] += vcca
c_vcca[2] += gnd

c_vccb = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric")
c_vccb[1] += vccb
c_vccb[2] += gnd

# A-side 6-pin header: GND, A1, A2, A3, A4, VCCA
hdr_a = Part("Connector", "Conn_01x06_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
             value="A_SIDE")

hdr_a["Pin_1"] += gnd
net_a1 = Net("A1"); hdr_a["Pin_2"] += net_a1
net_a2 = Net("A2"); hdr_a["Pin_3"] += net_a2
net_a3 = Net("A3"); hdr_a["Pin_4"] += net_a3
net_a4 = Net("A4"); hdr_a["Pin_5"] += net_a4
hdr_a["Pin_6"] += vcca

# B-side 6-pin header: GND, B1, B2, B3, B4, VCCB
hdr_b = Part("Connector", "Conn_01x06_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
             value="B_SIDE")

hdr_b["Pin_1"] += gnd
net_b1 = Net("B1"); hdr_b["Pin_2"] += net_b1
net_b2 = Net("B2"); hdr_b["Pin_3"] += net_b2
net_b3 = Net("B3"); hdr_b["Pin_4"] += net_b3
net_b4 = Net("B4"); hdr_b["Pin_5"] += net_b4
hdr_b["Pin_6"] += vccb

# Connect A-side signals to TXB0104 A ports
u1["A1"] += net_a1
u1["A2"] += net_a2
u1["A3"] += net_a3
u1["A4"] += net_a4

# Connect B-side signals to TXB0104 B ports
u1["B1"] += net_b1
u1["B2"] += net_b2
u1["B3"] += net_b3
u1["B4"] += net_b4
