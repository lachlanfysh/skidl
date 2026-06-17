"""
Eurorack White Noise Generator
- Reverse-biased 2N3904 NPN transistor as noise source
- TL071 single op-amp inverting amplifier stage (gain ~10x)
- 100K linear pot for output level control
- Thonkiconn 3.5mm mono jack output (PJ398SM)
- LED activity indicator
- Eurorack 2x5 shrouded IDC power header (+12V/-12V/GND)
- All through-hole, 4HP panel (~20mm wide)
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# --- Power nets ---
vp12 = Net("+12V"); vp12.drive = POWER
vm12 = Net("-12V"); vm12.drive = POWER
gnd  = Net("GND");  gnd.drive = POWER

# --- Signal nets ---
noise_raw  = Net("NOISE_RAW")   # emitter of reverse-biased Q1
noise_in   = Net("NOISE_IN")    # after coupling cap, before R_in
opamp_inv  = Net("OPAMP_INV")   # TL071 inverting (-) input node
noise_amp  = Net("NOISE_AMP")   # TL071 output
amp_out_dc = Net("AMP_OUT_DC")  # after output coupling cap
noise_out  = Net("NOISE_OUT")   # pot wiper (final audio output)
led_a      = Net("LED_A")       # LED anode

# --- Eurorack 2x5 IDC power header ---
# Standard Eurorack: row1 (odd)=+12V,+12V,+12V,-12V,GND; row2 (even)=+12V,+12V,-12V,-12V,GND
pwr = Part("Connector_Generic", "Conn_02x05_Odd_Even",
           footprint="Connector_IDC:IDC-Header_2x05_P2.54mm_Latch_Vertical")
pwr.ref = "J1"; pwr.value = "EURORACK_PWR"
pwr[1] += vp12;  pwr[2] += vp12
pwr[3] += vp12;  pwr[4] += vp12
pwr[5] += vp12;  pwr[6] += vm12
pwr[7] += vm12;  pwr[8] += vm12
pwr[9] += gnd;   pwr[10] += gnd

# --- Power supply filtering ---
# +12V: 10uF electrolytic + 100nF ceramic
c1 = Part("Device", "C_Polarized",
          footprint="Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm")
c1.ref = "C1"; c1.value = "10uF"
c1[1] += vp12; c1[2] += gnd

c2 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c2.ref = "C2"; c2.value = "100nF"
c2[1] += vp12; c2[2] += gnd

# -12V: 10uF electrolytic (polarity reversed) + 100nF ceramic
c3 = Part("Device", "C_Polarized",
          footprint="Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm")
c3.ref = "C3"; c3.value = "10uF"
c3[1] += gnd; c3[2] += vm12   # + to GND, - to -12V

c4 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c4.ref = "C4"; c4.value = "100nF"
c4[1] += gnd; c4[2] += vm12

# --- Noise source: reverse-biased 2N3904 ---
# Reverse-bias the B-E junction: collector to +12V, base via R to GND, emitter = noise out
# 2N3904 in TO-92: pin1=E, pin2=B, pin3=C
q1 = Part("Transistor_BJT", "2N3904",
          footprint="Package_TO_SOT_THT:TO-92_Inline")
q1.ref = "Q1"; q1.value = "2N3904"

r1 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r1.ref = "R1"; r1.value = "10K"

q1["C"] += vp12        # collector to +12V
r1[1] += q1["B"]       # base via 10K to GND (reverse bias)
r1[2] += gnd
q1["E"] += noise_raw   # emitter = noise output

# --- Signal conditioning ---
# C5: coupling cap to strip DC from noise signal
c5 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c5.ref = "C5"; c5.value = "100nF"
c5[1] += noise_raw; c5[2] += noise_in

# R2: input resistor into inverting stage (gain = R_fb/R_in = 100K/10K = 10x)
r2 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r2.ref = "R2"; r2.value = "10K"
r2[1] += noise_in; r2[2] += opamp_inv

# --- TL071 inverting amplifier ---
# TL071 pins: -(inv), +(non-inv), V+, V-, ~(output), NULL(offset), NC
u1 = Part("Amplifier_Operational", "TL071",
          footprint="Package_DIP:DIP-8_W7.62mm")
u1.ref = "U1"; u1.value = "TL071"
u1["-"] += opamp_inv
u1["+"] += gnd          # non-inv to GND
u1["V+"] += vp12
u1["V-"] += vm12
u1["~"] += noise_amp    # output
u1["NC"] += NC          # pin 8: no connect
u1[1] += NC             # pin 1: OFFSET NULL (float)
u1[5] += NC             # pin 5: OFFSET NULL (float)

# R3: feedback resistor (100K sets gain ~10x with 10K input resistor)
r3 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r3.ref = "R3"; r3.value = "100K"
r3[1] += opamp_inv; r3[2] += noise_amp

# C6: output coupling cap (DC block before volume pot)
c6 = Part("Device", "C",
          footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm")
c6.ref = "C6"; c6.value = "100nF"
c6[1] += noise_amp; c6[2] += amp_out_dc

# --- Output level pot (100K linear, Alpha 9mm) ---
# R_Potentiometer: pin1=CW, pin2=wiper, pin3=CCW
rv1 = Part("Device", "R_Potentiometer",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical_CircularHoles")
rv1.ref = "RV1"; rv1.value = "100K"
rv1[1] += amp_out_dc   # CW = full signal
rv1[3] += gnd          # CCW = ground
rv1[2] += noise_out    # wiper = output

# --- Output jack: Thonkiconn PJ398SM ---
# AudioJack2_SwitchT: pads S, T, TN - matches WQP-PJ398SM footprint exactly
# The sleeve is grounded via the mounting hardware (no separate G pad on this footprint)
j2 = Part("Connector_Audio", "AudioJack2_SwitchT",
          footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles")
j2.ref = "J2"; j2.value = "OUT"
j2["T"] += noise_out   # tip = audio output
j2["S"] += gnd         # switch: connect to GND (sleeve reference, normalled when unplugged)
j2["TN"] += noise_out  # normalled tip (connected to output when nothing plugged in)

# --- LED activity indicator ---
# R4: current limiting resistor from +12V (~10K for low-current LED at ~1mA)
r4 = Part("Device", "R",
          footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
r4.ref = "R4"; r4.value = "10K"
r4[1] += vp12; r4[2] += led_a

d1 = Part("Device", "LED",
          footprint="LED_THT:LED_D3.0mm")
d1.ref = "D1"; d1.value = "LED_RED"
d1[2] += led_a   # pin2 = A (anode)
d1[1] += gnd     # pin1 = K (cathode)

# --- Generate schematic ---
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)

# --- PCB Layout ---
from skidl.layout import (
    extract_groups, place_parts, write_kicad_pcb, validate,
    LayoutConstraints, BoardOutline, load_footprint_bboxes,
)

ckt = default_circuit
fp_names = {str(p.footprint) for p in ckt.parts if getattr(p, "footprint", None)}
fp_lib_dirs = ["/usr/share/kicad/footprints"]
fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)

# 4HP Eurorack: 20.32mm wide, ~100mm tall PCB
constraints = LayoutConstraints(outline=BoardOutline(20.0, 100.0))
placed = place_parts(extract_groups(ckt), constraints, fp_bboxes)

result = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(result.summary())

write_kicad_pcb(placed, ckt, fp_lib_dirs,
                "/home/lachlan/Projects/skidl/benchmarks/results/eurorack-noise/board.kicad_pcb",
                outline=constraints.outline)
print("Done.")
