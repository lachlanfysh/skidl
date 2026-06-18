"""
REF5050 Precision 5.0V Voltage Reference Breakout
- REF5050AIDR: 0.05% initial accuracy, 3ppm/C drift, 5.5-36V input
- SOIC-8 package (C27804 LCSC via convert_lcsc)
- 10uF + 100nF input decoupling
- 10uF + 100nF output decoupling
- 100K trim potentiometer on TRIMNR pin
- Test points for VREF and GND
- 3-pin header VIN/VREF/GND
- Board ~15x12mm
"""
from skidl import *

set_default_tool(KICAD9)

# Power nets
vin = Net("VIN"); vin.drive = POWER
vref = Net("VREF"); vref.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
trim_net = Net("TRIM")

# REF5050AIDR - converted from LCSC C27804
# Pins: DNC(1), VIN(2), TEMP(3), GND(4), TRIMNR(5), VOUT(6), NC(7), DNC(8)
u1 = Part("C27804", "REF5050AIDR",
          footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
u1.lcsc = "C27804"

# Connect REF5050
vin += u1["VIN"]
gnd += u1["GND"]
vref += u1["VOUT"]
trim_net += u1["TRIMNR"]
# TEMP pin left floating (no connection needed for basic operation)
# DNC/NC pins left floating

# Input decoupling: 10uF electrolytic + 100nF ceramic
c_in_bulk = Part("Device", "C_Polarized",
                 value="10uF",
                 footprint="Capacitor_SMD:CP_Elec_4x5.4")
c_in_bulk[1] += vin
c_in_bulk[2] += gnd

c_in_bypass = Part("Device", "C",
                   value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
c_in_bypass[1] += vin
c_in_bypass[2] += gnd

# Output decoupling: 10uF electrolytic + 100nF ceramic
c_out_bulk = Part("Device", "C_Polarized",
                  value="10uF",
                  footprint="Capacitor_SMD:CP_Elec_4x5.4")
c_out_bulk[1] += vref
c_out_bulk[2] += gnd

c_out_bypass = Part("Device", "C",
                    value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
c_out_bypass[1] += vref
c_out_bypass[2] += gnd

# 100K trim potentiometer: pins 1 and 3 to VREF and GND, wiper (2) to TRIMNR
# REF5050 datasheet: TRIMNR pin connects to resistor divider for fine trim
rv1 = Part("Device", "R_Potentiometer_Trim",
           value="100K",
           footprint="Potentiometer_SMD:Potentiometer_Bourns_3214W_Vertical")
rv1["1"] += vref
rv1["2"] += trim_net
rv1["3"] += gnd

# Test points
tp_vref = Part("Connector", "TestPoint",
               footprint="TestPoint:TestPoint_THTPad_1.5x1.5mm_Drill0.7mm")
tp_vref["1"] += vref

tp_gnd = Part("Connector", "TestPoint",
              footprint="TestPoint:TestPoint_THTPad_1.5x1.5mm_Drill0.7mm")
tp_gnd["1"] += gnd

# 3-pin header: VIN / VREF / GND
j1 = Part("Connector_Generic", "Conn_01x03",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
j1["1"] += vin
j1["2"] += vref
j1["3"] += gnd
