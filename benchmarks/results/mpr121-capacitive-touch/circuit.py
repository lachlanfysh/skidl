#!/usr/bin/env python3
"""MPR121 12-Channel Capacitive Touch Sensor Breakout

Features:
- MPR121QR2 capacitive touch controller (QFN-20, LCSC C91322 via convert_lcsc)
- 12 independent touch pad inputs on split headers (2x Conn_01x06)
- I2C interface with bidirectional BSS138 level shifting for 3-5V host
- TLV70033 3.3V LDO regulator (MPR121 is a 3V-only chip)
- Selectable I2C address via ADDR pin (0R to GND = 0x5A default)
- IRQ output with active-low LED indicator
- Input voltage: 3-5V via HOST_I2C header
"""

import os
os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from skidl import *
set_default_tool(KICAD9)

# Power rails
vin = Net("VIN"); vin.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C nets (split by voltage domain)
sda_3v3 = Net("SDA_3V3")
scl_3v3 = Net("SCL_3V3")
sda_host = Net("SDA")
scl_host = Net("SCL")
irq_net = Net("IRQ")

# 12 electrode sense nets
ele_nets = [Net(f"ELE{i}") for i in range(12)]

# ==============================================================
# 3.3V LDO Regulator (TLV70033, SOT-23-5)
# Pins: IN(1), GND(2), EN(3), NC(4), OUT(5)
# EN tied to VIN to keep always-on
# ==============================================================
reg = Part("Regulator_Linear", "TLV70033_SOT23-5",
           footprint="Package_TO_SOT_SMD:SOT-23-5")
reg["IN"] += vin
reg["GND"] += gnd
reg["OUT"] += v3v3
reg["EN"] += vin
reg["NC"] += NC()

cin = Part("Device", "C", value="1uF",
           footprint="Capacitor_SMD:C_0603_1608Metric")
cin[1] += vin; cin[2] += gnd

cout = Part("Device", "C", value="1uF",
            footprint="Capacitor_SMD:C_0603_1608Metric")
cout[1] += v3v3; cout[2] += gnd

cbulk = Part("Device", "C", value="10uF",
             footprint="Capacitor_SMD:C_0805_2012Metric")
cbulk[1] += v3v3; cbulk[2] += gnd

# ==============================================================
# MPR121QR2 touch controller (LCSC C91322, QFN-20 3x3mm 0.4mm pitch)
# Pins from convert_lcsc:
#   ~{IRQ}(1), SCL(2), SDA(3), ADDR(4), VREG(5), VSS(6),
#   REXT(7), ELE0(8)..ELE11(19), VDD(20)
# Footprint: UQFN-20 3x3mm 0.4mm pitch (standard KiCad, closest match)
# ==============================================================
ic = Part("C91322", "MPR121QR2",
          footprint="Package_DFN_QFN:UQFN-20_3x3mm_P0.4mm")

ic["VDD"] += v3v3
ic["VSS"] += gnd
ic["~{IRQ}"] += irq_net
ic["SCL"] += scl_3v3
ic["SDA"] += sda_3v3

# ADDR to GND via 0R = I2C address 0x5A (default)
r_addr = Part("Device", "R", value="0R",
              footprint="Resistor_SMD:R_0603_1608Metric")
r_addr[1] += ic["ADDR"]
r_addr[2] += gnd

# VREG: internal regulator output bypass cap (10nF per datasheet)
cvreg = Part("Device", "C", value="10nF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
cvreg[1] += ic["VREG"]
cvreg[2] += gnd

# REXT: 200k to GND sets electrode charge current per datasheet
r_rext = Part("Device", "R", value="200K",
              footprint="Resistor_SMD:R_0603_1608Metric")
r_rext[1] += ic["REXT"]
r_rext[2] += gnd

# VDD 100nF decoupling
cdec = Part("Device", "C", value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric")
cdec[1] += v3v3
cdec[2] += gnd

# Electrode connections ELE0-ELE11
for i in range(12):
    ic[f"ELE{i}"] += ele_nets[i]

# ==============================================================
# I2C Bidirectional Level Shifter (2x BSS138 N-MOSFET)
# Classic open-drain circuit: Gate=3V3, Source=low side, Drain=high side
# Works bidirectionally for both SDA and SCL
# ==============================================================
q_sda = Part("Transistor_FET", "BSS138",
             footprint="Package_TO_SOT_SMD:SOT-23")
q_sda["G"] += v3v3
q_sda["S"] += sda_3v3
q_sda["D"] += sda_host

r_sda_lo = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
r_sda_lo[1] += v3v3; r_sda_lo[2] += sda_3v3

r_sda_hi = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
r_sda_hi[1] += vin; r_sda_hi[2] += sda_host

q_scl = Part("Transistor_FET", "BSS138",
             footprint="Package_TO_SOT_SMD:SOT-23")
q_scl["G"] += v3v3
q_scl["S"] += scl_3v3
q_scl["D"] += scl_host

r_scl_lo = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
r_scl_lo[1] += v3v3; r_scl_lo[2] += scl_3v3

r_scl_hi = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
r_scl_hi[1] += vin; r_scl_hi[2] += scl_host

# ==============================================================
# IRQ Pull-up + LED indicator
# MPR121 IRQ is active-low open drain
# LED lights when touch detected (IRQ asserted low)
# LED Device:LED pin 1=K (cathode), pin 2=A (anode)
# ==============================================================
r_irq = Part("Device", "R", value="100K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_irq[1] += v3v3
r_irq[2] += irq_net

r_led = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
led = Part("Device", "LED", value="RED",
           footprint="LED_SMD:LED_0603_1608Metric")

r_led[1] += v3v3
r_led[2] += led["A"]   # Anode through current-limit R to 3V3
led["K"] += irq_net    # Cathode to IRQ: LED lights when IRQ low

# ==============================================================
# Connectors
# Host I2C header on left edge (5-pin: VIN, GND, SDA, SCL, IRQ)
# Electrode headers split 2x6 on right edge for reduced congestion
# ==============================================================
j_host = Part("Connector", "Conn_01x05_Pin",
              value="HOST_I2C",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical")
j_host["Pin_1"] += vin
j_host["Pin_2"] += gnd
j_host["Pin_3"] += sda_host
j_host["Pin_4"] += scl_host
j_host["Pin_5"] += irq_net
j_host.edge_preference = "left"

j_ele_a = Part("Connector", "Conn_01x06_Pin",
               value="ELE0-5",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
for i in range(6):
    j_ele_a[f"Pin_{i+1}"] += ele_nets[i]
j_ele_a.edge_preference = "right"

j_ele_b = Part("Connector", "Conn_01x06_Pin",
               value="ELE6-11",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
for i in range(6):
    j_ele_b[f"Pin_{i+1}"] += ele_nets[i + 6]
j_ele_b.edge_preference = "right"

# ==============================================================
# Generate schematic
# ==============================================================
generate_schematic(auto_stub=True)
