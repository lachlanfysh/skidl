"""
Eurorack VCO - Simple analog voltage-controlled oscillator
Core: AS3340 VCO chip (CEM3340 clone)
Features:
  - 1V/oct CV input with TL072 op-amp buffer
  - Coarse and fine tune pots
  - Sawtooth, triangle, pulse waveform outputs (Thonkiconn PJ398SM jacks)
  - Pulse width CV input and pot
  - Eurorack 2x5 IDC power header (+12V/-12V/GND)
  - 8HP panel width (40.3mm)
  - SMD passives, through-hole pots and jacks
"""

from skidl import *

# ============================================================
# Power rails — use + prefix so layout engine recognises them
# ============================================================
p12 = Net("+12V"); p12.drive = POWER   # +12V Eurorack rail
n12 = Net("-12V"); n12.drive = POWER   # -12V Eurorack rail
gnd = Net("GND");  gnd.drive = POWER

# ============================================================
# Eurorack 2x5 IDC power header (Doepfer A-100 standard)
# Odd_Even pin numbering:
#   Pin 1=-12V, Pin2=GND, Pin3=-12V, Pin4=GND,
#   Pin5=+5V(tie to GND), Pin6=GND, Pin7=GND,
#   Pin8=+12V, Pin9=GND, Pin10=+12V
# ============================================================
pwr = Part("Connector_Generic", "Conn_02x05_Odd_Even",
           footprint="Connector_IDC:IDC-Header_2x05_P2.54mm_Latch_Vertical",
           value="Eurorack_Power",
           ref="J1")
n12 += pwr["Pin_1"], pwr["Pin_3"]
gnd += pwr["Pin_2"], pwr["Pin_4"], pwr["Pin_5"], pwr["Pin_6"], pwr["Pin_7"], pwr["Pin_9"]
p12 += pwr["Pin_8"], pwr["Pin_10"]

# Eurorack power entry: 10uF bulk + 100nF decoupling per rail
@subcircuit
def power_filter(rail, gnd_net):
    c_bulk = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_fast = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    if rail.drive == POWER:
        c_bulk[1] += rail; c_bulk[2] += gnd_net
        c_fast[1] += rail; c_fast[2] += gnd_net
    else:
        c_bulk[1] += gnd_net; c_bulk[2] += rail
        c_fast[1] += gnd_net; c_fast[2] += rail

power_filter(p12, gnd)
power_filter(n12, gnd)

# ============================================================
# AS3340 VCO chip (DIP-16)
# Pins: 1=SCALE1, 2=SCALE2, 3=VEE(-12V), 4=VP(pulse out),
#       5=VPWM(PW mod in), 6=VHSI, 7=VHFT, 8=VSO(saw out),
#       9=VSSI, 10=VTO(tri out), 11=CAP, 12=GND,
#       13=VLFI, 14=VS(CV sum), 15=VFCI(CV in), 16=VCC(+12V)
# ============================================================
vco = Part("Audio", "AS3340",
           footprint="Package_DIP:DIP-16_W7.62mm",
           value="AS3340",
           ref="U1")

p12 += vco["VCC"]
n12 += vco["VEE"]
gnd += vco["GND"]

cv_sum    = Net("CV_SUM")
saw_out   = Net("SAW_OUT")
tri_out   = Net("TRI_OUT")
pulse_out = Net("PULSE_OUT")
pw_cv_net = Net("PW_CV")

vco["VFCI"] += cv_sum
vco["VSO"]  += saw_out
vco["VTO"]  += tri_out
vco["VP"]   += pulse_out
vco["VPWM"] += pw_cv_net

# Timing capacitor (2.2nF for audio range, connects CAP to GND)
c_time = Part("Device", "C", value="2.2nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
vco["CAP"] += c_time[1]
gnd += c_time[2]

# 1V/oct SCALE resistors (33K, pulls SCALE1/SCALE2 to GND)
for scale_pin in ("SCALE1", "SCALE2"):
    r = Part("Device", "R", value="33K",
             footprint="Resistor_SMD:R_0603_1608Metric")
    vco[scale_pin] += r[1]
    gnd += r[2]

# Unused input pins pulled to GND via 100K
for pin_name in ("VS", "VHSI", "VHFT", "VSSI", "VLFI"):
    r = Part("Device", "R", value="100K",
             footprint="Resistor_SMD:R_0603_1608Metric")
    vco[pin_name] += r[1]
    gnd += r[2]

# VCO per-rail decoupling (100nF)
for rail, g in ((p12, gnd), (gnd, n12)):
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
    c[1] += rail; c[2] += g

# ============================================================
# TL072 dual op-amp (SOIC-8)
# Using pin numbers to avoid SKiDL unit-name ambiguity:
#   Pin 1=Unit-A out, 2=Unit-A -, 3=Unit-A +, 4=V-
#   Pin 5=Unit-B +, 6=Unit-B -, 7=Unit-B out, 8=V+
# ============================================================
tl = Part("Amplifier_Operational", "TL072",
          footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
          value="TL072",
          ref="U2")

p12 += tl[8]   # V+
n12 += tl[4]   # V-

# TL072 per-rail decoupling
c_tl_p = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
c_tl_p[1] += p12; c_tl_p[2] += gnd

c_tl_n = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
c_tl_n[1] += gnd; c_tl_n[2] += n12

# Unit A: CV buffer — unity gain non-inverting
# + (pin 3) = CV_IN_NODE; - (pin 2) = CV_BUFFERED (feedback); out (pin 1) = CV_BUFFERED
cv_in  = Net("CV_IN_NODE")
cv_buf = Net("CV_BUFFERED")
tl[3] += cv_in   # Unit A non-inv
tl[2] += cv_buf  # Unit A inv (feedback for unity gain)
tl[1] += cv_buf  # Unit A out

# CV buffered output → VCO via summing resistor
r_cv_sum = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
r_cv_sum[1] += cv_buf
r_cv_sum[2] += cv_sum

# Unit B: PW inverting summer
# + (pin 5) = GND; - (pin 6) = PW_NODE; out (pin 7) = PW_CV
pw_node = Net("PW_NODE")
tl[5] += gnd
tl[6] += pw_node
tl[7] += pw_cv_net

r_pw_in = Part("Device", "R", value="100K",
               footprint="Resistor_SMD:R_0603_1608Metric")
r_pw_fb = Part("Device", "R", value="100K",
               footprint="Resistor_SMD:R_0603_1608Metric")
r_pw_in[1] += pw_node; r_pw_in[2] += gnd
r_pw_fb[1] += pw_node; r_pw_fb[2] += pw_cv_net

# ============================================================
# Panel controls and jacks (Thonkiconn PJ398SM, front-facing)
# ============================================================

# 1V/oct CV input jack (AudioJack2_SwitchT matches PJ398SM's T/TN/S pads)
j_cv = Part("Connector_Audio", "AudioJack2_SwitchT",
            footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
            value="CV_IN", ref="J2")
cv_jack = Net("CV_JACK")
j_cv["T"] += cv_jack
j_cv["S"] += gnd
j_cv["TN"] += gnd   # normalling contact — tie to GND (inactive normalling)
j_cv.assembly_side = "front"

r_cv_prot = Part("Device", "R", value="100K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
r_cv_prot[1] += cv_jack
r_cv_prot[2] += cv_in

# PW CV input jack
j_pw_in = Part("Connector_Audio", "AudioJack2_SwitchT",
               footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
               value="PW_CV_IN", ref="J3")
pw_jack = Net("PW_JACK")
j_pw_in["T"] += pw_jack
j_pw_in["S"] += gnd
j_pw_in["TN"] += gnd
j_pw_in.assembly_side = "front"

r_pw_jack = Part("Device", "R", value="100K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
r_pw_jack[1] += pw_jack
r_pw_jack[2] += pw_node

# Sawtooth output jack
j_saw = Part("Connector_Audio", "AudioJack2_SwitchT",
             footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
             value="SAW_OUT", ref="J4")
saw_jack = Net("SAW_JACK")
j_saw["T"] += saw_jack
j_saw["S"] += gnd
j_saw["TN"] += gnd
j_saw.assembly_side = "front"

r_saw = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_saw[1] += saw_out; r_saw[2] += saw_jack

# Triangle output jack
j_tri = Part("Connector_Audio", "AudioJack2_SwitchT",
             footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
             value="TRI_OUT", ref="J5")
tri_jack = Net("TRI_JACK")
j_tri["T"] += tri_jack
j_tri["S"] += gnd
j_tri["TN"] += gnd
j_tri.assembly_side = "front"

r_tri = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_tri[1] += tri_out; r_tri[2] += tri_jack

# Pulse output jack
j_pls = Part("Connector_Audio", "AudioJack2_SwitchT",
             footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
             value="PULSE_OUT", ref="J6")
pls_jack = Net("PULSE_JACK")
j_pls["T"] += pls_jack
j_pls["S"] += gnd
j_pls["TN"] += gnd
j_pls.assembly_side = "front"

r_pls = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_pls[1] += pulse_out; r_pls[2] += pls_jack

# Coarse tune pot (100K Alpha 9mm through-hole, rail-to-rail)
rv1 = Part("Device", "R_Potentiometer",
           value="100K",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
           ref="RV1")
coarse_wip = Net("COARSE_WIPER")
rv1[1] += p12; rv1[2] += coarse_wip; rv1[3] += n12
rv1.assembly_side = "front"

r_coarse = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
r_coarse[1] += coarse_wip; r_coarse[2] += cv_sum

# Fine tune pot (10K through-hole)
rv2 = Part("Device", "R_Potentiometer",
           value="10K",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
           ref="RV2")
fine_wip = Net("FINE_WIPER")
rv2[1] += p12; rv2[2] += fine_wip; rv2[3] += n12
rv2.assembly_side = "front"

r_fine = Part("Device", "R", value="1M",
              footprint="Resistor_SMD:R_0603_1608Metric")
r_fine[1] += fine_wip; r_fine[2] += cv_sum

# Pulse width pot (100K through-hole)
rv3 = Part("Device", "R_Potentiometer",
           value="100K",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
           ref="RV3")
pw_wip = Net("PW_WIPER")
rv3[1] += p12; rv3[2] += pw_wip; rv3[3] += n12
rv3.assembly_side = "front"

r_pw_pot = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
r_pw_pot[1] += pw_wip; r_pw_pot[2] += pw_node

# ============================================================
# Floorplan: 8HP Eurorack PCB (40.3mm wide, 110mm tall)
#
# Alpha RD901F pot footprint is ~13.8mm wide with origin 1.175mm
# from left edge. Two pots side-by-side need ≥13.8mm pitch.
# Three 13.8mm pots = 41.4mm > 40.3mm board, so use 2-row layout:
#   Row A (y=54): Coarse (x=8), Fine (x=27.5) — 19.5mm pitch, no overlap
#   Row B (y=72): PW (x=17.5) — centred
#
# IDC 2x5 header: footprint extends ~16mm below and ~8mm above origin.
# At y=86, bottom edge = 86+16 = 102 < 110 — fits.
# ============================================================
EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 40.3,
        "height_mm": 110.0,
        "corner_radius_mm": 0,
    },
    "fixed_positions": [
        # Output jacks — row 1 (SAW, TRI, PULSE), 12mm pitch
        {"ref": "J4", "x_mm": 8.0,  "y_mm": 14.0, "rotation_deg": 0},
        {"ref": "J5", "x_mm": 20.0, "y_mm": 14.0, "rotation_deg": 0},
        {"ref": "J6", "x_mm": 32.0, "y_mm": 14.0, "rotation_deg": 0},
        # Input jacks — row 2 (CV, PW CV)
        {"ref": "J2", "x_mm": 11.0, "y_mm": 30.0, "rotation_deg": 0},
        {"ref": "J3", "x_mm": 29.0, "y_mm": 30.0, "rotation_deg": 0},
        # Pots row A: Coarse and Fine (19.5mm pitch to avoid overlap)
        {"ref": "RV1", "x_mm": 8.0,  "y_mm": 52.0, "rotation_deg": 0},
        {"ref": "RV2", "x_mm": 27.5, "y_mm": 52.0, "rotation_deg": 0},
        # Pot row B: PW centred
        {"ref": "RV3", "x_mm": 17.5, "y_mm": 68.0, "rotation_deg": 0},
        # ICs and power header: let placer decide — do NOT fix these
        # to avoid impossible conflicts on narrow 40.3mm board
    ],
    "assembly_sides": {
        "J2": "front", "J3": "front",
        "J4": "front", "J5": "front", "J6": "front",
        "RV1": "front", "RV2": "front", "RV3": "front",
    },
}
