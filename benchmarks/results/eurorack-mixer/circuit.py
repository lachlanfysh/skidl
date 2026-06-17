"""
Eurorack 4-channel active audio mixer — 16HP panel.
4x 3.5mm Thonkiconn jacks (PJ398SM) with 100K level pots (Alpha 9mm).
TL072 dual op-amp: inverting summing amp + inverting unity buffer.
100K mixing resistors per channel.
Eurorack 2x5 shrouded IDC power header (+12V/-12V/GND).
16HP PCB: 81.25mm wide x 128.5mm tall. All through-hole.
"""

import os
os.environ.setdefault('KICAD9_SYMBOL_DIR', '/usr/share/kicad/symbols')

from skidl import *
set_default_tool(KICAD9)

# ─── Nets ───────────────────────────────────────────────────────────────────
vcc   = Net("+12V");  vcc.drive  = POWER
vneg  = Net("-12V");  vneg.drive = POWER
gnd   = Net("GND");   gnd.drive  = POWER

mix_node  = Net("MIX_NODE")
sum_out   = Net("SUM_OUT")
buf_inv   = Net("BUF_INV")
audio_out = Net("AUDIO_OUT")

ch_in    = [Net(f"CH{i}_IN")    for i in range(1, 5)]
ch_wiper = [Net(f"CH{i}_WIPER") for i in range(1, 5)]


# ─── Input Jacks (Thonkiconn PJ398SM) ────────────────────────────────────
j1 = Part("Connector_Audio", "AudioJack2_SwitchT",
          footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
          value="PJ398SM"); j1.ref = "J1"
j2 = Part("Connector_Audio", "AudioJack2_SwitchT",
          footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
          value="PJ398SM"); j2.ref = "J2"
j3 = Part("Connector_Audio", "AudioJack2_SwitchT",
          footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
          value="PJ398SM"); j3.ref = "J3"
j4 = Part("Connector_Audio", "AudioJack2_SwitchT",
          footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
          value="PJ398SM"); j4.ref = "J4"
j5 = Part("Connector_Audio", "AudioJack2_SwitchT",
          footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
          value="PJ398SM"); j5.ref = "J5"  # output

for i, jack in enumerate([j1, j2, j3, j4]):
    jack["T"]  += ch_in[i]
    jack["S"]  += gnd
    jack["TN"] += gnd   # silent when unplugged
j5["T"] += audio_out; j5["S"] += gnd; j5["TN"] += audio_out


# ─── Level Potentiometers (100K Alpha 9mm vertical) ──────────────────────
# R_Potentiometer_Trim: pin 1=A, 2=wiper, 3=B
rv1 = Part("Device", "R_Potentiometer_Trim",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
           value="100K"); rv1.ref = "RV1"
rv2 = Part("Device", "R_Potentiometer_Trim",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
           value="100K"); rv2.ref = "RV2"
rv3 = Part("Device", "R_Potentiometer_Trim",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
           value="100K"); rv3.ref = "RV3"
rv4 = Part("Device", "R_Potentiometer_Trim",
           footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
           value="100K"); rv4.ref = "RV4"

for i, pot in enumerate([rv1, rv2, rv3, rv4]):
    pot[1] += ch_in[i]     # end A = signal in
    pot[2] += ch_wiper[i]  # wiper = attenuated output
    pot[3] += gnd           # end B = GND


# ─── Mixing Resistors (100K per channel, wiper → virtual sum node) ────────
r1 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r1.ref = "R1"
r2 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r2.ref = "R2"
r3 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r3.ref = "R3"
r4 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r4.ref = "R4"

r1[1] += ch_wiper[0]; r1[2] += mix_node
r2[1] += ch_wiper[1]; r2[2] += mix_node
r3[1] += ch_wiper[2]; r3[2] += mix_node
r4[1] += ch_wiper[3]; r4[2] += mix_node


# ─── TL072 Dual Op-Amp ─────────────────────────────────────────────────────
u1 = Part("Amplifier_Operational", "TL072",
          footprint="Package_DIP:DIP-8_W7.62mm",
          value="TL072"); u1.ref = "U1"
u1[8] += vcc
u1[4] += vneg

# Op-amp A: inverting summing amp
a_plus = Net("A_PLUS")
u1[3] += a_plus
u1[2] += mix_node
u1[1] += sum_out

r5 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r5.ref = "R5"
r5[1] += a_plus; r5[2] += gnd

r6 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r6.ref = "R6"
r6[1] += mix_node; r6[2] += sum_out    # feedback

# Op-amp B: inverting unity buffer
b_plus = Net("B_PLUS")
u1[5] += b_plus
u1[6] += buf_inv
u1[7] += audio_out

r7 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r7.ref = "R7"
r7[1] += b_plus; r7[2] += gnd

r8 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r8.ref = "R8"
r8[1] += sum_out; r8[2] += buf_inv

r9 = Part("Device", "R", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal", value="100K"); r9.ref = "R9"
r9[1] += buf_inv; r9[2] += audio_out


# ─── Power Supply Decoupling ─────────────────────────────────────────────
# C_Polarized pins: 1 = anode (+), 2 = cathode (-)
c1 = Part("Device", "C_Polarized", footprint="Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm", value="10uF"); c1.ref = "C1"
c1[1] += vcc; c1[2] += gnd

c2 = Part("Device", "C_Polarized", footprint="Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm", value="10uF"); c2.ref = "C2"
c2[1] += gnd; c2[2] += vneg   # + to GND, - to -12V (correct for negative rail)

c3 = Part("Device", "C", footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm", value="100nF"); c3.ref = "C3"
c3[1] += vcc; c3[2] += gnd

c4 = Part("Device", "C", footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm", value="100nF"); c4.ref = "C4"
c4[1] += gnd; c4[2] += vneg


# ─── Eurorack Power Header (2x5 shrouded IDC) ───────────────────────────
pwr = Part("Connector_Generic", "Conn_02x05_Odd_Even",
           footprint="Connector_IDC:IDC-Header_2x05_P2.54mm_Vertical",
           value="PWR2x5"); pwr.ref = "J6"
pwr[1] += vneg; pwr[2] += vneg   # -12V (red stripe on Eurorack cable = pin 1)
pwr[3] += gnd;  pwr[4] += gnd    # GND
pwr[5] += gnd;  pwr[6] += gnd    # GND
pwr[7] += vcc;  pwr[8] += vcc    # +12V
pwr[9] += gnd;  pwr[10] += gnd   # +5V unused → GND


# ─── EDA Floorplan (Eurorack 16HP board) ─────────────────────────────────
# 16HP = 81.25mm wide, 128.5mm tall standard Eurorack PCB.
# Jacks at bottom (panel-mount row), pots in row above, opamp + passives above that.
# Power header at top-right rear of board.

EDA_FLOORPLAN = {
    "outline": {"width_mm": 81.25, "height_mm": 128.5},

    # Fixed grid for 4 input jacks at bottom (panel-mounted)
    # 4 jacks at 17mm pitch across 81.25mm board → 4×17 = 68mm, centred in 81.25mm
    "grid": {
        "refs": ["J1", "J2", "J3", "J4"],
        "rows": 1,
        "cols": 4,
        "x_mm": 8.625,
        "y_mm": 113.0,
        "dx_mm": 17.0,
        "dy_mm": 0,
        "soft": False,
        "side": "front",
    },

    # Fixed grid for 4 pots (row above jacks, 17mm pitch, same X centres as jacks)
    "fixed_positions": [
        {"ref": "RV1", "x_mm": 8.625,  "y_mm": 88.0, "rotation_deg": 0},
        {"ref": "RV2", "x_mm": 25.625, "y_mm": 88.0, "rotation_deg": 0},
        {"ref": "RV3", "x_mm": 42.625, "y_mm": 88.0, "rotation_deg": 0},
        {"ref": "RV4", "x_mm": 59.625, "y_mm": 88.0, "rotation_deg": 0},
        # Output jack at bottom right
        {"ref": "J5", "x_mm": 72.0,   "y_mm": 113.0, "rotation_deg": 0},
        # Power header at top of board
        {"ref": "J6", "x_mm": 55.0,   "y_mm": 15.0,  "rotation_deg": 0},
        # Op-amp at centre of board
        {"ref": "U1", "x_mm": 20.0,   "y_mm": 45.0,  "rotation_deg": 0},
    ],
}
