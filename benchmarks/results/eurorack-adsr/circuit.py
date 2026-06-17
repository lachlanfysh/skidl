"""
Eurorack ADSR Envelope Generator
CD4066 quad analog switch + TL074 quad op-amp design.
8HP panel, through-hole construction, +/-12V Eurorack power.
"""

import os
os.environ.setdefault('KICAD9_SYMBOL_DIR', '/usr/share/kicad/symbols')

from skidl import *

set_default_tool(KICAD9)

# ---------------------------------------------------------------------------
# Power nets
# ---------------------------------------------------------------------------
pwr_pos = Net('+12V')
pwr_neg = Net('-12V')
gnd = Net('GND')

pwr_pos.drive = POWER
pwr_neg.drive = POWER
gnd.drive = POWER

# ---------------------------------------------------------------------------
# Eurorack 2x5 shrouded power header
# Standard Eurorack IDC 2x5 pinout (odd/even numbering):
#   1=-12V, 2=-12V, 3=GND, 4=GND, 5=GND, 6=GND, 7=NC(+5V), 8=+12V, 9=+12V, 10=+12V
# ---------------------------------------------------------------------------
@subcircuit
def power_header(pwr_pos, pwr_neg, gnd):
    pwr = Part('Connector_Generic', 'Conn_02x05_Odd_Even',
               footprint='Connector_IDC:IDC-Header_2x05_P2.54mm_Vertical',
               value='Eurorack Power')
    pwr['Pin_1'] += pwr_neg
    pwr['Pin_2'] += pwr_neg
    pwr['Pin_3'] += gnd
    pwr['Pin_4'] += gnd
    pwr['Pin_5'] += gnd
    pwr['Pin_6'] += gnd
    pwr['Pin_8'] += pwr_pos
    pwr['Pin_9'] += pwr_pos
    pwr['Pin_10'] += pwr_pos

    # Pin_7 is +5V on Eurorack bus — not used, left unconnected

    # Bulk decoupling: 100nF + 10uF per rail
    c_pos_s = Part('Device', 'C', value='100nF',
                   footprint='Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm')
    c_pos_s[1] += pwr_pos
    c_pos_s[2] += gnd

    c_neg_s = Part('Device', 'C', value='100nF',
                   footprint='Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm')
    c_neg_s[1] += gnd
    c_neg_s[2] += pwr_neg

    c_pos_e = Part('Device', 'C', value='10uF',
                   footprint='Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm')
    c_pos_e[1] += pwr_pos
    c_pos_e[2] += gnd

    c_neg_e = Part('Device', 'C', value='10uF',
                   footprint='Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm')
    c_neg_e[1] += gnd
    c_neg_e[2] += pwr_neg


# ---------------------------------------------------------------------------
# Jacks (AudioJack2_SwitchT: S=sleeve, T=tip, TN=switch-NC contact)
# Thonkiconn PJ301M maps to PJ320D footprint (closest available)
# TN connected to GND so unpatched = gate low
# ---------------------------------------------------------------------------
@subcircuit
def jacks(gate_in, env_out, gnd):
    j_gate = Part('Connector_Audio', 'AudioJack2_SwitchT',
                  footprint='Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles',
                  value='GATE IN')
    j_gate['S'] += gnd
    j_gate['TN'] += gnd
    j_gate['T'] += gate_in

    j_env = Part('Connector_Audio', 'AudioJack2_SwitchT',
                 footprint='Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles',
                 value='ENV OUT')
    j_env['S'] += gnd
    j_env['TN'] += gnd
    j_env['T'] += env_out


# ---------------------------------------------------------------------------
# Potentiometers (Device/R_Potentiometer: pin1=CCW, pin2=wiper, pin3=CW)
# Alpha 9mm vertical pots (RD901F — no horizontal footprint in std library)
# ---------------------------------------------------------------------------
@subcircuit
def pots(pwr_pos, gnd, wiper_a, wiper_d, wiper_s, wiper_r):
    pot_a = Part('Device', 'R_Potentiometer',
                 footprint='Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical',
                 value='1M Log')
    pot_a[1] += pwr_pos
    pot_a[3] += gnd
    pot_a[2] += wiper_a

    pot_d = Part('Device', 'R_Potentiometer',
                 footprint='Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical',
                 value='1M Log')
    pot_d[1] += pwr_pos
    pot_d[3] += gnd
    pot_d[2] += wiper_d

    pot_s = Part('Device', 'R_Potentiometer',
                 footprint='Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical',
                 value='100K Lin')
    pot_s[1] += pwr_pos
    pot_s[3] += gnd
    pot_s[2] += wiper_s

    pot_r = Part('Device', 'R_Potentiometer',
                 footprint='Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical',
                 value='1M Log')
    pot_r[1] += pwr_pos
    pot_r[3] += gnd
    pot_r[2] += wiper_r


# ---------------------------------------------------------------------------
# CD4066 quad bilateral switch
# Pinout (DIP-14):
#   Switch A: pin1(I/O), pin2(I/O), pin13(ctrl)
#   Switch B: pin3(I/O), pin4(I/O), pin5(ctrl)
#   Switch C: pin8(I/O), pin9(I/O), pin6(ctrl)
#   Switch D: pin10(I/O), pin11(I/O), pin12(ctrl)
#   VDD=14, VSS=7
#
# ADSR function:
#   SW-A: connects VCC to env cap node (attack charging path)
#   SW-B: connects decay wiper to env cap (decay discharging path)
#   SW-C: connects release wiper to env cap (release discharging)
#   SW-D: disconnects decay when sustain is reached
# Gate high enables SW-A (attack) and SW-B (decay) simultaneously.
# Comparator from TL074 drives SW-C/D for sustain/release transitions.
# ---------------------------------------------------------------------------
@subcircuit
def analog_switch(pwr_pos, gnd, gate_in,
                  atk_src, atk_cap,
                  dec_src, dec_cap,
                  rel_src, rel_cap,
                  sus_ctrl, rel_ctrl):
    sw = Part('4xxx', '4066',
              footprint='Package_DIP:DIP-14_W7.62mm',
              value='CD4066')
    sw[14] += pwr_pos
    sw[7] += gnd

    # SW-A: attack charge path (gate → charge cap from +12V via R)
    sw[1] += atk_src    # I/O A side 1 — from +12V via attack resistor
    sw[2] += atk_cap    # I/O A side 2 — to envelope cap
    sw[13] += gate_in   # ctrl A — gate high = attack active

    # SW-B: decay path (gate high, but comparator disables when at sustain)
    sw[4] += dec_src    # I/O B side 1 — from decay wiper
    sw[3] += dec_cap    # I/O B side 2 — to envelope cap
    sw[5] += gate_in    # ctrl B — gate high = decay active

    # SW-C: release path (gate goes low, comparator enables release)
    sw[8] += rel_src    # I/O C side 1 — from release wiper
    sw[9] += rel_cap    # I/O C side 2 — to envelope cap
    sw[6] += rel_ctrl   # ctrl C — release comparator output

    # SW-D: sustain gate (cuts off decay when sustain level reached)
    sw[10] += dec_src   # I/O D side 1 — feeds sustain level to compare
    sw[11] += sus_ctrl  # I/O D side 2 — to comparator inv input
    sw[12] += gate_in   # ctrl D — gate high = sustain compare active


# ---------------------------------------------------------------------------
# TL074 quad op-amp
# Op-amp A: attack integrator buffer
# Op-amp B: decay/sustain comparator
# Op-amp C: release comparator
# Op-amp D: output unity-gain buffer
#
# TL074 pin map:
#   A: out=1, in-=2, in+=3
#   B: out=7, in-=6, in+=5
#   C: out=8, in-=9, in+=10
#   D: out=14, in-=13, in+=12
#   V+=4, V-=11
# ---------------------------------------------------------------------------
@subcircuit
def opamp_stages(pwr_pos, pwr_neg, gnd,
                 wiper_a, wiper_d, wiper_s, wiper_r,
                 env_cap, env_out,
                 gate_in, rel_ctrl, sus_ctrl):
    opa = Part('Amplifier_Operational', 'TL074',
               footprint='Package_DIP:DIP-14_W7.62mm',
               value='TL074')
    opa[4] += pwr_pos
    opa[11] += pwr_neg

    # Decoupling on op-amp supply pins
    c_op_p = Part('Device', 'C', value='100nF',
                  footprint='Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm')
    c_op_p[1] += pwr_pos
    c_op_p[2] += gnd

    c_op_n = Part('Device', 'C', value='100nF',
                  footprint='Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm')
    c_op_n[1] += gnd
    c_op_n[2] += pwr_neg

    # Timing capacitor: charged via attack switch, discharged via decay/release
    c_timing = Part('Device', 'C', value='10uF',
                    footprint='Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm')
    c_timing[1] += env_cap
    c_timing[2] += gnd

    # Op-amp A: voltage follower buffering the envelope capacitor
    opa[3] += env_cap   # in+ = envelope cap
    opa[2] += opa[1]    # in- = output (unity gain)
    # opa[1] = env_buffered (internal net via feedback)
    env_buf = Net('ENV_BUF')
    opa[1] += env_buf

    # Attack resistor (wiper_a → env_cap via SW-A in analog_switch)
    r_atk = Part('Device', 'R', value='100K',
                 footprint='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal')
    r_atk[1] += pwr_pos
    r_atk[2] += wiper_a   # wiper_a goes to SW-A input, wiper sets total R

    # Op-amp B: decay/sustain comparator
    # in+ = envelope buffer, in- = sustain wiper
    # output drives CD4066 sustain/decay logic
    opa[5] += env_buf     # in+ = buffered envelope
    opa[6] += wiper_s     # in- = sustain pot wiper
    opa[7] += sus_ctrl    # out = sustain control to SW-D

    # Op-amp C: release / gate-low comparator
    # in+ = small positive ref (biased ~0.1V), in- = gate_in
    # when gate goes low, output goes high → enables SW-C (release)
    r_ref_h = Part('Device', 'R', value='100K',
                   footprint='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal')
    r_ref_h[1] += pwr_pos
    r_ref_h[2] += Net('GATE_REF')

    r_ref_l = Part('Device', 'R', value='10K',
                   footprint='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal')
    r_ref_l[1] += Net('GATE_REF')
    r_ref_l[2] += gnd

    opa[10] += Net('GATE_REF')   # in+ = ~1V reference (small bias)
    opa[9] += gate_in            # in- = gate signal
    opa[8] += rel_ctrl           # out = release control to SW-C

    # Op-amp D: output unity-gain buffer
    opa[12] += env_buf    # in+ = buffered envelope from op-amp A
    opa[13] += env_out    # in- = output (unity feedback)
    opa[14] += env_out    # out = final output

    # Decay resistor
    r_dec = Part('Device', 'R', value='100K',
                 footprint='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal')
    r_dec[1] += gnd
    r_dec[2] += wiper_d    # wiper_d → SW-B → env_cap (pulls cap to GND = decay)

    # Release resistor
    r_rel = Part('Device', 'R', value='100K',
                 footprint='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal')
    r_rel[1] += gnd
    r_rel[2] += wiper_r    # wiper_r → SW-C → env_cap (release)


# ---------------------------------------------------------------------------
# LED output indicator (green, 3mm)
# ---------------------------------------------------------------------------
@subcircuit
def led_indicator(env_out, gnd):
    r_led = Part('Device', 'R', value='10K',
                 footprint='Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal')
    led = Part('Device', 'LED',
               footprint='LED_THT:LED_D3.0mm',
               value='Green LED')
    r_led[1] += env_out
    r_led[2] += led['A']
    led['K'] += gnd


# ---------------------------------------------------------------------------
# Internal nets
# ---------------------------------------------------------------------------
gate_in  = Net('GATE_IN')
env_out  = Net('ENV_OUT')
env_cap  = Net('ENV_CAP')
wiper_a  = Net('WIPER_A')
wiper_d  = Net('WIPER_D')
wiper_s  = Net('WIPER_S')
wiper_r  = Net('WIPER_R')
rel_ctrl = Net('REL_CTRL')
sus_ctrl = Net('SUS_CTRL')
atk_src  = Net('ATK_SRC')
atk_cap  = env_cap   # attack charges directly to cap
dec_src  = wiper_d
dec_cap  = env_cap
rel_src  = wiper_r
rel_cap  = env_cap

# ---------------------------------------------------------------------------
# Instantiate all subcircuits
# ---------------------------------------------------------------------------
power_header(pwr_pos, pwr_neg, gnd)
jacks(gate_in, env_out, gnd)
pots(pwr_pos, gnd, wiper_a, wiper_d, wiper_s, wiper_r)
analog_switch(pwr_pos, gnd, gate_in,
              atk_src, atk_cap,
              dec_src, dec_cap,
              rel_src, rel_cap,
              sus_ctrl, rel_ctrl)
opamp_stages(pwr_pos, pwr_neg, gnd,
             wiper_a, wiper_d, wiper_s, wiper_r,
             env_cap, env_out,
             gate_in, rel_ctrl, sus_ctrl)
led_indicator(env_out, gnd)

# ---------------------------------------------------------------------------
# Generate schematic
# ---------------------------------------------------------------------------
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
