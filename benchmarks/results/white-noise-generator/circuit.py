"""
Standalone White Noise Generator
- Reverse-biased BC547 transistor noise source (collector-base junction)
- TL072 dual op-amp: Unit A = noise amplification (gain ~100x), Unit B = pink noise unity buffer
- Pink noise filter: 3-stage passive RC (-3dB/octave): 100K/100nF, 10K/10nF, 1K/1nF
- B10K Alpha 9mm output level potentiometer
- SPDT toggle switch for White/Pink output selection
- Power: 9V via 2-pin power header
- Thonkiconn PJ398SM 3.5mm output jack (Eurorack compatible)
- Power LED with series resistor
- Board: ~60x50mm

Pipeline: submitted via eda-mcp (submit_skidl_code), run_id=32534836e25e, job_id=a1349c5457d2
Status: failed_reviewable (manufacturing artifacts produced; layout score 45.8/100)
Bugs/friction logged separately in docstring below.
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Signal nets
noise_raw     = Net("NOISE_RAW")
noise_coupled = Net("NOISE_COUPLED")
amp_inv       = Net("AMP_INV")
noise_amp_out = Net("NOISE_AMP_OUT")
pink_stage1   = Net("PINK_STAGE1")
pink_stage2   = Net("PINK_STAGE2")
pink_stage3   = Net("PINK_STAGE3")
pink_buf_out  = Net("PINK_BUF_OUT")
sw_common     = Net("SW_COMMON")
vol_out       = Net("VOL_OUT")
vol_in        = Net("VOL_IN")
led_anode     = Net("LED_ANODE")

# --- 9V power input ---
j_pwr = Part("Connector_Generic", "Conn_01x02",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
j_pwr.ref = "J1"; j_pwr.value = "9V_IN"
j_pwr[1] += vcc
j_pwr[2] += gnd

# --- Power supply filtering ---
c1 = Part("Device", "C_Polarized",
          footprint="Capacitor_THT:C_Radial_D8.0mm_H11.5mm_P3.50mm")
c1.ref = "C1"; c1.value = "100uF"
c1[1] += vcc; c1[2] += gnd

c2 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c2.ref = "C2"; c2.value = "100nF"
c2[1] += vcc; c2[2] += gnd

# --- Noise source: BC547 reverse-biased CB junction ---
# BC547: pin 1=C, 2=B, 3=E
# Reverse bias: E to VCC (high), B via 10K to GND, C = noise output
q1 = Part("Transistor_BJT", "BC547",
          footprint="Package_TO_SOT_THT:TO-92_Inline")
q1.ref = "Q1"; q1.value = "BC547"

r1 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r1.ref = "R1"; r1.value = "10K"

q1["E"] += vcc
q1["B"] += r1[1]
r1[2] += gnd
q1["C"] += noise_raw

# --- DC blocking cap ---
c3 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c3.ref = "C3"; c3.value = "100nF"
c3[1] += noise_raw; c3[2] += noise_coupled

# --- Input resistor into inverting amp ---
r2 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r2.ref = "R2"; r2.value = "10K"
r2[1] += noise_coupled; r2[2] += amp_inv

# --- TL072 dual op-amp (DIP-8) ---
# Pin 1=outA, 2=-A(inv), 3=+A(noninv), 4=V-, 5=+B(noninv), 6=-B(inv), 7=outB, 8=V+
u1 = Part("Amplifier_Operational", "TL072",
          footprint="Package_DIP:DIP-8_W7.62mm")
u1.ref = "U1"; u1.value = "TL072"
u1["V+"] += vcc
u1["V-"] += gnd
# Unit A: inverting noise amplifier
u1[3] += gnd
u1[2] += amp_inv
u1[1] += noise_amp_out

# Feedback resistor (gain = 1M/10K = 100x)
r3 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r3.ref = "R3"; r3.value = "1M"
r3[1] += amp_inv; r3[2] += noise_amp_out

# Unit B: unity-gain buffer for pink noise
u1[5] += pink_stage3
u1[6] += pink_buf_out
u1[7] += pink_buf_out

# TL072 bypass cap
c4 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c4.ref = "C4"; c4.value = "100nF"
c4[1] += vcc; c4[2] += gnd

# --- Pink noise filter: 3-stage RC (-3dB/octave) ---
# Stage 1: 100K/100nF (fc ~16Hz)
r4 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r4.ref = "R4"; r4.value = "100K"
c5 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c5.ref = "C5"; c5.value = "100nF"
r4[1] += noise_amp_out; r4[2] += pink_stage1
c5[1] += pink_stage1;   c5[2] += gnd

# Stage 2: 10K/10nF (fc ~1.6kHz)
r5 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r5.ref = "R5"; r5.value = "10K"
c6 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c6.ref = "C6"; c6.value = "10nF"
r5[1] += pink_stage1; r5[2] += pink_stage2
c6[1] += pink_stage2; c6[2] += gnd

# Stage 3: 1K/1nF (fc ~160kHz)
r6 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r6.ref = "R6"; r6.value = "1K"
c7 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c7.ref = "C7"; c7.value = "1nF"
r6[1] += pink_stage2; r6[2] += pink_stage3
c7[1] += pink_stage3; c7[2] += gnd

# --- SPDT switch: White/Pink selector ---
# SW_SPDT: pin 1=A, 2=B (common), 3=C
sw1 = Part("Switch", "SW_SPDT",
           footprint="Button_Switch_THT:SW_E-Switch_EG1271_SPDT")
sw1.ref = "SW1"; sw1.value = "WHITE/PINK"
sw1[2] += sw_common
sw1[1] += noise_amp_out
sw1[3] += pink_buf_out

# --- Volume pot B10K Alpha 9mm ---
# R_Potentiometer: 1=CW, 2=wiper, 3=CCW
rv1 = Part("Device", "R_Potentiometer",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical_CircularHoles")
rv1.ref = "RV1"; rv1.value = "B10K"
rv1[1] += sw_common
rv1[2] += vol_out
rv1[3] += gnd

# --- Output coupling cap ---
c8 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c8.ref = "C8"; c8.value = "100nF"
c8[1] += vol_out; c8[2] += vol_in

# --- Thonkiconn PJ398SM 3.5mm output jack ---
# AudioJack2_SwitchT: T=tip, TN=tip-normalled, S=switch
j_out = Part("Connector_Audio", "AudioJack2_SwitchT",
             footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles")
j_out.ref = "J2"; j_out.value = "OUT"
j_out["T"] += vol_in
j_out["S"] += gnd
j_out["TN"] += vol_in

# --- Power LED ---
r7 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r7.ref = "R7"; r7.value = "10K"
r7[1] += vcc; r7[2] += led_anode

d1 = Part("Device", "LED",
          footprint="LED_THT:LED_D3.0mm")
d1.ref = "D1"; d1.value = "LED_GREEN"
d1["A"] += led_anode
d1["K"] += gnd

EDA_FLOORPLAN = {
    "outline": {"width_mm": 60, "height_mm": 50, "corner_radius_mm": 1.5},
}
