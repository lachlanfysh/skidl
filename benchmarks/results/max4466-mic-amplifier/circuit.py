"""MAX4466 Electret Microphone Amplifier Breakout

Supply: 2.4V to 5.5V VCC.
Gain: Rf/Rin, where Rf=RV1 trimmer (0..100k), Rin=1k -> gain 0..100x adjustable.
"""
from skidl import *

vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
in_plus = Net("IN_PLUS")
inv_in  = Net("INV_IN")
amp_out = Net("AMP_OUT")
out_ac  = Net("AUDIO_OUT")

u1 = Part("C7456762", "MAX4466EXK+T",
          footprint="Package_TO_SOT_SMD:SOT-353_SC-70-5")
u1.lcsc = "C7456762"
mic = Part("Device", "Microphone_Condenser",
           footprint="Sensor_Audio:CUI_CMC-4013-SMT")
rv1 = Part("Device", "R_Potentiometer_Trim", value="100k",
           footprint="Potentiometer_SMD:Potentiometer_Bourns_3214W_Vertical")
r_in = Part("Device", "R", value="1k",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_bias = Part("Device", "R", value="2.2k",
              footprint="Resistor_SMD:R_0603_1608Metric")
r_div1 = Part("Device", "R", value="100k",
              footprint="Resistor_SMD:R_0603_1608Metric")
r_div2 = Part("Device", "R", value="100k",
              footprint="Resistor_SMD:R_0603_1608Metric")
c_in  = Part("Device", "C", value="1uF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
c_out = Part("Device", "C", value="1uF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
c_dec  = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
c_bulk = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
c_bias = Part("Device", "C", value="1uF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
j1 = Part("Connector", "Conn_01x03_Pin",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
j1.edge_preference = "right"

vcc += u1["VCC"], c_dec[1], c_bulk[1]
gnd += u1["GND"], c_dec[2], c_bulk[2], r_in[2], c_bias[2]
vcc += r_bias[1]
r_bias[2] += mic["+"]
mic["-"]  += gnd
mic["+"]  += c_in[1]
c_in[2]   += in_plus
vcc       += r_div1[1]
r_div1[2] += in_plus
in_plus   += r_div2[1], c_bias[1], u1["IN+"]
r_div2[2] += gnd
amp_out += u1["OUT"], rv1[3], c_out[1]
rv1[2]  += inv_in
rv1[1]  += gnd
inv_in  += u1["IN-"], r_in[1]
c_out[2] += out_ac
j1["Pin_1"] += vcc
j1["Pin_2"] += gnd
j1["Pin_3"] += out_ac
