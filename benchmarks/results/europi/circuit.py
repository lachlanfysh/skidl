"""
EuroPi Eurorack Module - SKiDL circuit
Raspberry Pi Pico-based Eurorack synthesizer module with:
- 6 CV outputs (0-10V): 4 via MCP4728 DAC + 2 via PWM-filtered outputs
- 2 analog input knobs (10K pots)
- 2 digital push buttons
- 1 analog CV input (0-10V, resistor divider to Pico ADC)
- 1 digital input
- Eurorack 2x5 IDC power header (+12V/-12V/+5V/GND)
- TL074 quad op-amp: non-inverting gain 4.9x (0-2.048V -> ~10V)
- Through-hole construction, 100mm x 128.5mm PCB
"""

from skidl import *

# Power rails
v12 = Net("+12V"); v12.drive = POWER
vm12 = Net("-12V"); vm12.drive = POWER
v5 = Net("+5V"); v5.drive = POWER
v33 = Net("3V3"); v33.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

C_DISC = "Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm"
C_RAD_6 = "Capacitor_THT:CP_Radial_D6.3mm_P2.50mm"
C_RAD_5 = "Capacitor_THT:CP_Radial_D5.0mm_P2.50mm"
R_AXIAL = "Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal"
JACK_FP = "Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles"

# Eurorack 2x5 IDC power header (Doepfer A-100 standard)
pwr_hdr = Part("Connector_Generic", "Conn_02x05_Odd_Even",
               value="Eurorack_Power",
               footprint="Connector_IDC:IDC-Header_2x05_P2.54mm_Vertical")
# 1,2=-12V; 3,4,5,6=GND; 7=+5V; 8,9,10=+12V
vm12 += pwr_hdr[1], pwr_hdr[2]
gnd += pwr_hdr[3], pwr_hdr[4], pwr_hdr[5], pwr_hdr[6]
v5 += pwr_hdr[7]
v12 += pwr_hdr[8], pwr_hdr[9], pwr_hdr[10]
pwr_hdr.edge_preference = "bottom"

# Power supply filtering
c_bulk_12p = Part("Device", "C_Polarized", value="100uF", footprint=C_RAD_6)
c_bulk_12m = Part("Device", "C_Polarized", value="100uF", footprint=C_RAD_6)
v12 += c_bulk_12p[1]; gnd += c_bulk_12p[2]
vm12 += c_bulk_12m[2]; gnd += c_bulk_12m[1]

c_byp_12p = Part("Device", "C", value="100nF", footprint=C_DISC)
c_byp_12m = Part("Device", "C", value="100nF", footprint=C_DISC)
v12 += c_byp_12p[1]; gnd += c_byp_12p[2]
vm12 += c_byp_12m[2]; gnd += c_byp_12m[1]

c_33_bulk = Part("Device", "C_Polarized", value="10uF", footprint=C_RAD_5)
c_33_byp = Part("Device", "C", value="100nF", footprint=C_DISC)
v33 += c_33_bulk[1], c_33_byp[1]
gnd += c_33_bulk[2], c_33_byp[2]

c_v5_bulk = Part("Device", "C_Polarized", value="10uF", footprint=C_RAD_5)
c_v5_byp = Part("Device", "C", value="100nF", footprint=C_DISC)
v5 += c_v5_bulk[1], c_v5_byp[1]
gnd += c_v5_bulk[2], c_v5_byp[2]

# Raspberry Pi Pico (through-hole)
pico = Part("MCU_Module", "RaspberryPi_Pico",
            footprint="Module:RaspberryPi_Pico_Common_THT")
v5 += pico["VSYS"]      # Pico onboard regulator; feed from Eurorack +5V
v33 += pico["3V3"]      # 3.3V from Pico's internal regulator
gnd += pico["GND"]

# I2C bus for MCP4728 DAC (GPIO4=SDA, GPIO5=SCL -> I2C0)
i2c_sda = Net("I2C_SDA")
i2c_scl = Net("I2C_SCL")
i2c_sda += pico["GPIO4"]
i2c_scl += pico["GPIO5"]

r_sda = Part("Device", "R", value="4.7k", footprint=R_AXIAL)
r_scl = Part("Device", "R", value="4.7k", footprint=R_AXIAL)
v33 += r_sda[1]; i2c_sda += r_sda[2]
v33 += r_scl[1]; i2c_scl += r_scl[2]

# PWM outputs for CV5 & CV6 (software low-pass filtered)
pwm5 = Net("PWM5")
pwm6 = Net("PWM6")
pwm5 += pico["GPIO14"]
pwm6 += pico["GPIO15"]

# ADC inputs
cv_in_adc = Net("CV_IN_ADC")
knob1_adc = Net("KNOB1_ADC")
knob2_adc = Net("KNOB2_ADC")
cv_in_adc += pico["GPIO26_ADC0"]
knob1_adc += pico["GPIO27_ADC1"]
knob2_adc += pico["GPIO28_ADC2"]

# Button and digital input pins
btn1_pin = Net("BTN1")
btn2_pin = Net("BTN2")
din_pin = Net("DIN")
btn1_pin += pico["GPIO2"]
btn2_pin += pico["GPIO3"]
din_pin += pico["GPIO6"]

# DAC control
ldac_net = Net("LDAC")
dac_rdy = Net("DAC_RDY")
ldac_net += pico["GPIO7"]
dac_rdy += pico["GPIO8"]

# MCP4728 Quad 12-bit I2C DAC
dac = Part("Analog_DAC", "MCP4728",
           footprint="Package_SO:MSOP-10_3x3mm_P0.5mm")
v33 += dac["VDD"]
gnd += dac["VSS"]
i2c_sda += dac["SDA"]
i2c_scl += dac["SCL"]
ldac_net += dac["~{LDAC}"]
dac_rdy += dac["RDY/~{BSY}"]

c_dac = Part("Device", "C", value="100nF", footprint=C_DISC)
v33 += c_dac[1]; gnd += c_dac[2]

dac_a = Net("DAC_A")
dac_b = Net("DAC_B")
dac_c = Net("DAC_C")
dac_d = Net("DAC_D")
dac_a += dac["VOUTA"]
dac_b += dac["VOUTB"]
dac_c += dac["VOUTC"]
dac_d += dac["VOUTD"]

# TL074 quad op-amp (DIP-14): non-inverting config, gain = 1 + Rf/Rg = 1 + 39k/10k = 4.9
# Maps DAC output (0-2.048V internal ref) to CV (0-10.03V)
opamp = Part("Amplifier_Operational", "TL074",
             footprint="Package_DIP:DIP-14_W7.62mm")
v12 += opamp["V+"]
vm12 += opamp["V-"]

c_op_vp = Part("Device", "C", value="100nF", footprint=C_DISC)
c_op_vm = Part("Device", "C", value="100nF", footprint=C_DISC)
v12 += c_op_vp[1]; gnd += c_op_vp[2]
vm12 += c_op_vm[2]; gnd += c_op_vm[1]

cv1 = Net("CV1")
cv2 = Net("CV2")
cv3 = Net("CV3")
cv4 = Net("CV4")
cv5 = Net("CV5")
cv6 = Net("CV6")

# Op-amp section A: + = pin 3, - = pin 2, out = pin 1
# Non-inverting: Rf from output to -, Rg from - to GND
inv_a = Net("INV_A")
dac_a += opamp[3]
inv_a += opamp[2]
cv1 += opamp[1]
r_f_a = Part("Device", "R", value="39k", footprint=R_AXIAL)
r_g_a = Part("Device", "R", value="10k", footprint=R_AXIAL)
cv1 += r_f_a[1]; inv_a += r_f_a[2]
inv_a += r_g_a[1]; gnd += r_g_a[2]

# Op-amp section B: + = pin 5, - = pin 6, out = pin 7
inv_b = Net("INV_B")
dac_b += opamp[5]
inv_b += opamp[6]
cv2 += opamp[7]
r_f_b = Part("Device", "R", value="39k", footprint=R_AXIAL)
r_g_b = Part("Device", "R", value="10k", footprint=R_AXIAL)
cv2 += r_f_b[1]; inv_b += r_f_b[2]
inv_b += r_g_b[1]; gnd += r_g_b[2]

# Op-amp section C: + = pin 10, - = pin 9, out = pin 8
inv_c = Net("INV_C")
dac_c += opamp[10]
inv_c += opamp[9]
cv3 += opamp[8]
r_f_c = Part("Device", "R", value="39k", footprint=R_AXIAL)
r_g_c = Part("Device", "R", value="10k", footprint=R_AXIAL)
cv3 += r_f_c[1]; inv_c += r_f_c[2]
inv_c += r_g_c[1]; gnd += r_g_c[2]

# Op-amp section D: + = pin 12, - = pin 13, out = pin 14
inv_d = Net("INV_D")
dac_d += opamp[12]
inv_d += opamp[13]
cv4 += opamp[14]
r_f_d = Part("Device", "R", value="39k", footprint=R_AXIAL)
r_g_d = Part("Device", "R", value="10k", footprint=R_AXIAL)
cv4 += r_f_d[1]; inv_d += r_f_d[2]
inv_d += r_g_d[1]; gnd += r_g_d[2]

# PWM RC filters for CV5 & CV6 (R=10k, C=100nF, fc~159Hz)
r_pwm5 = Part("Device", "R", value="10k", footprint=R_AXIAL)
c_pwm5 = Part("Device", "C", value="100nF", footprint=C_DISC)
pwm5_filt = Net("PWM5_FILT")
pwm5 += r_pwm5[1]; pwm5_filt += r_pwm5[2]
pwm5_filt += c_pwm5[1]; gnd += c_pwm5[2]
cv5 += pwm5_filt

r_pwm6 = Part("Device", "R", value="10k", footprint=R_AXIAL)
c_pwm6 = Part("Device", "C", value="100nF", footprint=C_DISC)
pwm6_filt = Net("PWM6_FILT")
pwm6 += r_pwm6[1]; pwm6_filt += r_pwm6[2]
pwm6_filt += c_pwm6[1]; gnd += c_pwm6[2]
cv6 += pwm6_filt

# 6 CV Output Jacks (Thonkiconn PJ398SM vertical, panel-facing top edge)
for i, cv_net in enumerate([cv1, cv2, cv3, cv4, cv5, cv6], 1):
    j = Part("Connector_Audio", "AudioJack2_Ground",
             value=f"CV_OUT_{i}", footprint=JACK_FP)
    cv_net += j["T"]
    gnd += j["S"]
    j.edge_preference = "top"

# Analog CV input jack (0-10V -> voltage divider -> Pico ADC 0-3.3V)
j_cvin = Part("Connector_Audio", "AudioJack2_Ground",
              value="CV_IN", footprint=JACK_FP)
cv_in_raw = Net("CV_IN_RAW")
cv_in_raw += j_cvin["T"]
gnd += j_cvin["S"]
j_cvin.edge_preference = "top"

# 68k + 22k divider: 10V * 22/(68+22) = 2.44V max (safe for 3.3V ADC)
r_div_top = Part("Device", "R", value="68k", footprint=R_AXIAL)
r_div_bot = Part("Device", "R", value="22k", footprint=R_AXIAL)
cv_in_raw += r_div_top[1]; cv_in_adc += r_div_top[2]
cv_in_adc += r_div_bot[1]; gnd += r_div_bot[2]
c_cvin = Part("Device", "C", value="100nF", footprint=C_DISC)
cv_in_adc += c_cvin[1]; gnd += c_cvin[2]

# Digital input jack
j_din = Part("Connector_Audio", "AudioJack2_Ground",
             value="DIN", footprint=JACK_FP)
din_raw = Net("DIN_RAW")
din_raw += j_din["T"]
gnd += j_din["S"]
j_din.edge_preference = "top"
r_din_prot = Part("Device", "R", value="1k", footprint=R_AXIAL)
din_raw += r_din_prot[1]; din_pin += r_din_prot[2]

# 2 knob potentiometers (Alpha RD901F 10K linear, vertical)
POT_FP = "Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical"
for i, adc_net in enumerate([knob1_adc, knob2_adc], 1):
    pot = Part("Device", "R_Potentiometer", value="10k", footprint=POT_FP)
    v33 += pot[1]
    adc_net += pot[2]
    gnd += pot[3]
    pot.edge_preference = "top"

# 2 push buttons (momentary SPST)
BTN_FP = "Button_Switch_THT:SW_Tactile_SPST_Angled_PTS645Vx58-2LFS"
for i, btn_net in enumerate([btn1_pin, btn2_pin], 1):
    sw = Part("Switch", "SW_Push", value=f"BTN{i}", footprint=BTN_FP)
    btn_net += sw[1]
    gnd += sw[2]
    sw.edge_preference = "top"

# External pull-up resistors for buttons (Pico has internal but adds margin)
r_btn1 = Part("Device", "R", value="10k", footprint=R_AXIAL)
r_btn2 = Part("Device", "R", value="10k", footprint=R_AXIAL)
v33 += r_btn1[1]; btn1_pin += r_btn1[2]
v33 += r_btn2[1]; btn2_pin += r_btn2[2]

# Board floorplan: 100mm wide to fit 9 jacks at 10mm pitch + pots/buttons
# Pico (A1) footprint is ~21.5mm x 53.9mm; at y=50mm it spans y=47-101mm (clear of keepouts)
EDA_FLOORPLAN = {
    "outline": {"width_mm": 100.0, "height_mm": 128.5, "corner_radius_mm": 2.0},
    "fixed_positions": [
        {"ref": "A1", "x_mm": 50.0, "y_mm": 50.0, "rotation_deg": 0},
    ],
    "edge_anchors": [
        {"ref": "J1", "edge": "top"},
        {"ref": "J2", "edge": "top"},
        {"ref": "J3", "edge": "top"},
        {"ref": "J4", "edge": "top"},
        {"ref": "J5", "edge": "top"},
        {"ref": "J6", "edge": "top"},
        {"ref": "J7", "edge": "top"},
        {"ref": "J8", "edge": "top"},
        {"ref": "J9", "edge": "top"},
        {"ref": "RV1", "edge": "top"},
        {"ref": "RV2", "edge": "top"},
        {"ref": "SW1", "edge": "top"},
        {"ref": "SW2", "edge": "top"},
        {"ref": "J10", "edge": "bottom"},
    ],
    "keepouts": [
        {"x_min": 0, "y_min": 0, "x_max": 100.0, "y_max": 4.0},
        {"x_min": 0, "y_min": 124.5, "x_max": 100.0, "y_max": 128.5},
    ],
}
