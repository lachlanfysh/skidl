"""
MXR Phase 90 analog phaser pedal clone.

4-stage JFET all-pass phase shifter using 2N3819 JFETs (sub for 2N5457).
TL072 dual op-amp for LFO and mixing/buffering.
True bypass via DPDT footswitch.
9V DC barrel jack power input with filtering.
Speed pot (1M) controls LFO rate.
2x 6.35mm Neutrik mono jacks (in/out).
LED indicator with current-limiting resistor.
~100x60mm PCB for Hammond 1590B enclosure.

TL072 pin map: 8=V+, 4=V-, 3=+A, 2=-A, 1=outA, 5=+B, 6=-B, 7=outB
"""

from skidl import *

# Power rails
VCC = Net("VCC"); VCC.drive = POWER
GND = Net("GND"); GND.drive = POWER
VBIAS = Net("VBIAS")

# Audio signal nets
IN_RAW = Net("IN_RAW")
IN_SIG = Net("IN_SIG")
OUT_SIG = Net("OUT_SIG")
OUT_RAW = Net("OUT_RAW")

# Phase shifter stage nets
STAGE1 = Net("STAGE1")
STAGE2 = Net("STAGE2")
STAGE3 = Net("STAGE3")
STAGE4 = Net("STAGE4")

# LFO nets
LFO_OUT = Net("LFO_OUT")
LFO_INT = Net("LFO_INT")
LFO_BUF = Net("LFO_BUF")


@subcircuit
def power_supply(vcc, gnd):
    """9V power input, bulk filtering, mid-rail bias."""
    pwr_jack = Part("Connector", "Barrel_Jack",
                    value="9VDC",
                    footprint="Connector_BarrelJack:BarrelJack_CUI_PJ-063AH_Horizontal")
    pwr_jack[1] += gnd
    pwr_jack[2] += vcc

    c_pwr1 = Part("Device", "C_Polarized", value="100uF",
                  footprint="Capacitor_THT:CP_Radial_D8.0mm_P3.50mm")
    c_pwr1[1] += vcc
    c_pwr1[2] += gnd

    c_pwr2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")
    c_pwr2[1] += vcc
    c_pwr2[2] += gnd


@subcircuit
def bias_divider(vcc, gnd, vbias):
    """Mid-rail bias divider: 2x 47k + 10uF."""
    r1 = Part("Device", "R", value="47k",
              footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r2 = Part("Device", "R", value="47k",
              footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c1 = Part("Device", "C_Polarized", value="10uF",
              footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    r1[1] += vcc
    r1[2] += vbias
    r2[1] += vbias
    r2[2] += gnd
    c1[1] += vbias
    c1[2] += gnd


@subcircuit
def lfo_block(vcc, gnd, vbias, lfo_int, lfo_buf, lfo_out):
    """LFO: TL072 op-amp A as integrator + speed pot."""
    u1 = Part("Amplifier_Operational", "TL072",
              footprint="Package_DIP:DIP-8_W7.62mm")
    u1[8] += vcc
    u1[4] += gnd

    # TL072 decoupling
    c_dec1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")
    c_dec2 = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    c_dec1[1] += vcc; c_dec1[2] += gnd
    c_dec2[1] += vcc; c_dec2[2] += gnd

    # Speed pot (1M)
    speed_pot = Part("Device", "R_Potentiometer", value="1M",
                     footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical")
    speed_pot[1] += vcc
    speed_pot[2] += lfo_int
    speed_pot[3] += gnd

    # U1A: LFO integrator
    r_lfo1 = Part("Device", "R", value="100k",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_lfo2 = Part("Device", "R", value="10k",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_lfo1 = Part("Device", "C_Polarized", value="4.7uF",
                  footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    r_lfo1[1] += lfo_buf
    r_lfo1[2] += u1[2]    # -input A
    c_lfo1[1] += u1[1]    # integrator cap: from output A
    c_lfo1[2] += u1[2]    # to -input A
    u1[3] += vbias         # +input A to mid-rail
    r_lfo2[1] += lfo_int
    r_lfo2[2] += lfo_buf
    u1[1] += lfo_out
    lfo_out += lfo_buf

    # U1B: audio mixer/output buffer
    r_mix1 = Part("Device", "R", value="47k",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_mix2 = Part("Device", "R", value="47k",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_mix_fb = Part("Device", "R", value="47k",
                    footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_in = Part("Device", "C", value="10nF",
                footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")
    c_ph = Part("Device", "C", value="10nF",
                footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")
    c_out = Part("Device", "C", value="10nF",
                 footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")

    c_in[1] += IN_SIG
    c_in[2] += r_mix1[1]
    r_mix1[2] += u1[6]     # -input B
    c_ph[1] += STAGE4
    c_ph[2] += r_mix2[1]
    r_mix2[2] += u1[6]
    r_mix_fb[1] += u1[6]
    r_mix_fb[2] += u1[7]   # output B
    u1[5] += vbias          # +input B to mid-rail
    c_out[1] += u1[7]
    c_out[2] += OUT_SIG


@subcircuit
def jfet_stage(sig_in, sig_out, lfo_buf, vcc, gnd):
    """Single JFET all-pass phase shift stage."""
    q = Part("Transistor_FET", "2N3819",
             footprint="Package_TO_SOT_THT:TO-92")
    r_d = Part("Device", "R", value="10k",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_s = Part("Device", "R", value="10k",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_c = Part("Device", "C", value="10nF",
               footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")
    r_g = Part("Device", "R", value="1M",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")

    q["G"] += lfo_buf
    q["G"] += r_g[2]
    r_g[1] += gnd
    q["D"] += r_d[1]
    r_d[2] += vcc
    q["S"] += r_s[1]
    r_s[2] += gnd
    c_c[1] += sig_in
    c_c[2] += sig_out
    q["D"] += sig_out


@subcircuit
def io_and_bypass(in_raw, in_sig, out_sig, out_raw, vcc, gnd):
    """Input/output jacks, true bypass DPDT footswitch, LED indicator."""
    # Input jack
    j_in = Part("Connector_Audio", "NJ2FD-V",
                value="IN",
                footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_in["T"] += in_raw
    j_in["S"] += gnd

    # Output jack
    j_out = Part("Connector_Audio", "NJ2FD-V",
                 value="OUT",
                 footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_out["T"] += out_raw
    j_out["S"] += gnd

    # True bypass DPDT footswitch
    # SW_Push_DPDT: 1=A1, 2=B1(common), 3=C1; 4=A2, 5=B2(common), 6=C2
    sw1 = Part("Switch", "SW_Push_DPDT",
               value="BYPASS",
               footprint="Button_Switch_THT:SW_PUSH_E-Switch_FS5700DP_DPDT")
    sw1[2] += in_raw      # Pole1 common: from input jack
    sw1[1] += out_raw     # Pole1 A: bypass direct to output
    sw1[3] += in_sig      # Pole1 C: to effect input
    sw1[5] += out_sig     # Pole2 common: from effect output
    sw1[4] += in_raw      # Pole2 A: (not used in bypass path)
    sw1[6] += out_raw     # Pole2 C: to output jack

    # LED indicator
    led1 = Part("Device", "LED", value="RED",
                footprint="LED_THT:LED_D3.0mm")
    r_led = Part("Device", "R", value="4k7",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_led[1] += vcc
    r_led[2] += led1[2]   # Anode
    led1[1] += gnd         # Cathode


# Instantiate subcircuits
power_supply(VCC, GND)
bias_divider(VCC, GND, VBIAS)
lfo_block(VCC, GND, VBIAS, LFO_INT, LFO_BUF, LFO_OUT)
jfet_stage(IN_SIG, STAGE1, LFO_BUF, VCC, GND)
jfet_stage(STAGE1, STAGE2, LFO_BUF, VCC, GND)
jfet_stage(STAGE2, STAGE3, LFO_BUF, VCC, GND)
jfet_stage(STAGE3, STAGE4, LFO_BUF, VCC, GND)
io_and_bypass(IN_RAW, IN_SIG, OUT_SIG, OUT_RAW, VCC, GND)
