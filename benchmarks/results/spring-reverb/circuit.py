"""
Eurorack Spring Reverb Module -- SKiDL circuit description.

8HP Eurorack module (100x50mm panel) featuring:
- Belton BTDR-2 (or equivalent) spring reverb brick via 2x 3-pin headers
- TL072 dual op-amp (U1) for drive amplifier (gain stage + impedance match)
- TL072 dual op-amp (U2) for recovery amplifier (low-noise pickup amp)
- Mix pot (dry/wet blend) Alpha 9mm B10K vertical
- Decay/feedback pot Alpha 9mm B100K vertical
- Thonkiconn 3.5mm jacks for I/O (AudioJack2_SwitchT)
- Eurorack 2x5 IDC power header (+12V, -12V)
- Power filtering: 10uF + 100nF on +12V and -12V rails
- Signal coupling capacitors throughout
- Board: 100x50mm (8HP Eurorack)
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# ── Power Nets ───────────────────────────────────────────────────
pwr_12v  = Net("+12V");  pwr_12v.drive  = POWER
pwr_n12v = Net("-12V");  pwr_n12v.drive = POWER
gnd      = Net("GND");   gnd.drive      = POWER

# ── Signal Nets ─────────────────────────────────────────────────
audio_in      = Net("AUDIO_IN")
audio_out     = Net("AUDIO_OUT")

spring_drive_p  = Net("SPRING_DRV_P")
spring_drive_n  = Net("SPRING_DRV_N")
spring_pickup_p = Net("SPRING_PICKUP_P")
spring_pickup_n = Net("SPRING_PICKUP_N")

recovered_wet = Net("RECOVERED_WET")
feedback_net  = Net("FEEDBACK")


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Eurorack Power Header + Filtering
# ═══════════════════════════════════════════════════════════════
@subcircuit
def eurorack_power(p12v, n12v, gnd_net):
    """Eurorack 2x5 IDC power header with bulk and decoupling filters."""

    pwr_hdr = Part("Connector_Generic", "Conn_02x05_Odd_Even",
                   footprint="Connector_IDC:IDC-Header_2x05_P2.54mm_Vertical",
                   value="PWR_EURO")

    # Eurorack bus pinout (Doepfer A-100):
    # odd col: 1,3,5,7=GND; 9=+12V
    # even col: 2,4,6=-12V; 8=+5V (NC/GND); 10=+12V
    pwr_hdr[1]  += gnd_net
    pwr_hdr[2]  += n12v
    pwr_hdr[3]  += gnd_net
    pwr_hdr[4]  += n12v
    pwr_hdr[5]  += gnd_net
    pwr_hdr[6]  += n12v
    pwr_hdr[7]  += gnd_net
    pwr_hdr[8]  += gnd_net  # +5V unused, tie to GND
    pwr_hdr[9]  += p12v
    pwr_hdr[10] += p12v

    pwr_hdr.edge_preference = "top"

    # +12V bulk filter
    c_12v_bulk = Part("Device", "C_Polarized",
                      footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm",
                      value="10uF")
    c_12v_bulk[1] += p12v
    c_12v_bulk[2] += gnd_net

    c_12v_byp = Part("Device", "C",
                     footprint="Capacitor_SMD:C_0805_2012Metric",
                     value="100nF")
    c_12v_byp[1] += p12v
    c_12v_byp[2] += gnd_net

    # -12V bulk filter (reversed polarity for electrolytic)
    c_n12v_bulk = Part("Device", "C_Polarized",
                       footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm",
                       value="10uF")
    c_n12v_bulk[1] += gnd_net
    c_n12v_bulk[2] += n12v

    c_n12v_byp = Part("Device", "C",
                      footprint="Capacitor_SMD:C_0805_2012Metric",
                      value="100nF")
    c_n12v_byp[1] += gnd_net
    c_n12v_byp[2] += n12v


eurorack_power(pwr_12v, pwr_n12v, gnd)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Audio I/O Jacks (Thonkiconn 3.5mm)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def audio_io(sig_in, sig_out, gnd_net):
    """Input and output Thonkiconn 3.5mm mono jacks."""

    # AudioJack2_SwitchT pin numbers: T=Tip, S=Sleeve, TN=Tip Normalling
    j_in = Part("Connector_Audio", "AudioJack2_SwitchT",
                footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
                value="IN")
    j_in["T"]  += sig_in
    j_in["S"]  += gnd_net
    j_in["TN"] += gnd_net  # Normalling pin: tie to GND (open when plugged in)

    j_out = Part("Connector_Audio", "AudioJack2_SwitchT",
                 footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
                 value="OUT")
    j_out["T"]  += sig_out
    j_out["S"]  += gnd_net
    j_out["TN"] += gnd_net

    j_in.edge_preference  = "left"
    j_out.edge_preference = "left"


audio_io(audio_in, audio_out, gnd)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Spring Reverb Brick Headers
# ═══════════════════════════════════════════════════════════════
@subcircuit
def spring_brick_headers(drv_p, drv_n, pickup_p, pickup_n, gnd_net):
    """3-pin headers for BTDR-2 spring reverb brick drive and pickup."""

    # Drive header: pin1=drive+, pin2=drive-, pin3=GND
    j_drive = Part("Connector_Generic", "Conn_01x03",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                   value="SPRING_DRIVE")
    j_drive[1] += drv_p
    j_drive[2] += drv_n
    j_drive[3] += gnd_net

    # Pickup header: pin1=pickup+, pin2=pickup-, pin3=GND
    j_pickup = Part("Connector_Generic", "Conn_01x03",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
                    value="SPRING_PICKUP")
    j_pickup[1] += pickup_p
    j_pickup[2] += pickup_n
    j_pickup[3] += gnd_net


spring_brick_headers(spring_drive_p, spring_drive_n,
                     spring_pickup_p, spring_pickup_n, gnd)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Drive Amplifier (TL072 U1)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def drive_amplifier(p12v, n12v, gnd_net, sig_in, drv_p, drv_n):
    """
    TL072 drive stage: U1A inverting ~6x gain, U1B unity follower.
    Signal-coupled input, AC-coupled output to spring drive+.
    """

    u1 = Part("Amplifier_Operational", "TL072",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
              value="TL072")

    # TL072 SOIC-8: 1=OUT_A, 2=IN-_A, 3=IN+_A, 4=V-, 5=IN+_B, 6=IN-_B, 7=OUT_B, 8=V+
    u1[8] += p12v
    u1[4] += n12v

    # Input coupling cap: AC couple sig_in to U1A
    c_in = Part("Device", "C",
                footprint="Capacitor_SMD:C_0805_2012Metric",
                value="1uF")
    in_ac = Net("DRV_IN_AC")
    c_in[1] += sig_in
    c_in[2] += in_ac

    # U1A: non-inverting input to GND (split supply → virtual GND = 0V)
    u1[3] += gnd_net

    # Gain resistors: inverting amp, Rf/Rin = 56K/10K ≈ 5.6x
    r_in_a = Part("Device", "R",
                  footprint="Resistor_SMD:R_0805_2012Metric",
                  value="10K")
    r_fb_a = Part("Device", "R",
                  footprint="Resistor_SMD:R_0805_2012Metric",
                  value="56K")

    r_in_a[1] += in_ac
    r_in_a[2] += u1[2]    # IN-_A

    u1[2]      += r_fb_a[1]
    r_fb_a[2]  += u1[1]   # OUT_A → feedback

    # Output coupling cap → spring drive+
    c_drv = Part("Device", "C",
                 footprint="Capacitor_SMD:C_0805_2012Metric",
                 value="1uF")
    c_drv[1] += u1[1]
    c_drv[2] += drv_p

    # Drive- termination: pull to GND via series resistor
    r_drv_n = Part("Device", "R",
                   footprint="Resistor_SMD:R_0805_2012Metric",
                   value="100R")
    r_drv_n[1] += drv_n
    r_drv_n[2] += gnd_net

    # U1B: unity follower, grounded input, keeps op-amp biased
    u1[5] += gnd_net
    u1[6] += u1[7]   # IN-_B = OUT_B

    # Decoupling for U1
    c_u1_p = Part("Device", "C",
                  footprint="Capacitor_SMD:C_0805_2012Metric",
                  value="100nF")
    c_u1_p[1] += p12v
    c_u1_p[2] += gnd_net

    c_u1_n = Part("Device", "C",
                  footprint="Capacitor_SMD:C_0805_2012Metric",
                  value="100nF")
    c_u1_n[1] += gnd_net
    c_u1_n[2] += n12v


drive_amplifier(pwr_12v, pwr_n12v, gnd,
                audio_in, spring_drive_p, spring_drive_n)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Recovery Amplifier (TL072 U2)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def recovery_amplifier(p12v, n12v, gnd_net, pickup_p, pickup_n, wet_out):
    """
    TL072 recovery stage: U2A non-inverting ~20x gain, U2B unity output buffer.
    AC-coupled pickup inputs, AC-coupled wet output.
    """

    u2 = Part("Amplifier_Operational", "TL072",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
              value="TL072")

    # TL072 SOIC-8: 1=OUT_A, 2=IN-_A, 3=IN+_A, 4=V-, 5=IN+_B, 6=IN-_B, 7=OUT_B, 8=V+
    u2[8] += p12v
    u2[4] += n12v

    # Input coupling caps
    c_p_in = Part("Device", "C",
                  footprint="Capacitor_SMD:C_0805_2012Metric",
                  value="100nF")
    pickup_ac_p = Net("PICKUP_AC_P")
    c_p_in[1] += pickup_p
    c_p_in[2] += pickup_ac_p

    c_n_in = Part("Device", "C",
                  footprint="Capacitor_SMD:C_0805_2012Metric",
                  value="100nF")
    pickup_ac_n = Net("PICKUP_AC_N")
    c_n_in[1] += pickup_n
    c_n_in[2] += pickup_ac_n

    # Termination resistor on pickup- side
    r_term = Part("Device", "R",
                  footprint="Resistor_SMD:R_0805_2012Metric",
                  value="100K")
    r_term[1] += pickup_ac_n
    r_term[2] += gnd_net

    # Bias resistor on pickup+ → non-inverting input
    r_bias = Part("Device", "R",
                  footprint="Resistor_SMD:R_0805_2012Metric",
                  value="100K")
    r_bias[1] += pickup_ac_p
    r_bias[2] += gnd_net
    u2[3] += pickup_ac_p  # IN+_A

    # Non-inverting gain: 1 + Rf/Rin = 1 + 82K/4.7K ≈ 18.4x
    r_in_rec = Part("Device", "R",
                    footprint="Resistor_SMD:R_0805_2012Metric",
                    value="4.7K")
    r_fb_rec = Part("Device", "R",
                    footprint="Resistor_SMD:R_0805_2012Metric",
                    value="82K")

    r_in_rec[1] += gnd_net
    r_in_rec[2] += u2[2]    # IN-_A

    u2[2]       += r_fb_rec[1]
    r_fb_rec[2] += u2[1]    # OUT_A → feedback

    # AC-couple U2A output to U2B
    wet_ac = Net("WET_AC")
    c_wet = Part("Device", "C",
                 footprint="Capacitor_SMD:C_0805_2012Metric",
                 value="1uF")
    c_wet[1] += u2[1]
    c_wet[2] += wet_ac

    # U2B: unity buffer
    u2[5] += wet_ac
    u2[6] += u2[7]   # IN-_B = OUT_B
    u2[7] += wet_out

    # Decoupling for U2
    c_u2_p = Part("Device", "C",
                  footprint="Capacitor_SMD:C_0805_2012Metric",
                  value="100nF")
    c_u2_p[1] += p12v
    c_u2_p[2] += gnd_net

    c_u2_n = Part("Device", "C",
                  footprint="Capacitor_SMD:C_0805_2012Metric",
                  value="100nF")
    c_u2_n[1] += gnd_net
    c_u2_n[2] += n12v


recovery_amplifier(pwr_12v, pwr_n12v, gnd,
                   spring_pickup_p, spring_pickup_n, recovered_wet)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Mix Potentiometer (Dry/Wet Blend, B10K)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def mix_pot_circuit(sig_in, wet_in, gnd_net, sig_out):
    """
    Passive dry/wet mixer using B10K Alpha 9mm pot.
    Pin 1 (CW) = wet, Pin 2 (Wiper) = blended out, Pin 3 (CCW) = dry.
    """

    pot = Part("Device", "R_Potentiometer",
               footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
               value="B10K")
    # R_Potentiometer pins: 1=CW, 2=Wiper, 3=CCW

    # Wet signal → isolation resistor → CW end
    r_wet = Part("Device", "R",
                 footprint="Resistor_SMD:R_0805_2012Metric",
                 value="10K")
    r_wet[1] += wet_in
    r_wet[2] += pot[1]   # CW

    # Dry signal coupling cap then isolation resistor → CCW end
    c_dry = Part("Device", "C",
                 footprint="Capacitor_SMD:C_0805_2012Metric",
                 value="100nF")
    dry_ac = Net("DRY_AC")
    c_dry[1] += sig_in
    c_dry[2] += dry_ac

    r_dry = Part("Device", "R",
                 footprint="Resistor_SMD:R_0805_2012Metric",
                 value="10K")
    r_dry[1] += dry_ac
    r_dry[2] += pot[3]   # CCW

    # Wiper → output coupling cap → audio out
    c_out = Part("Device", "C",
                 footprint="Capacitor_SMD:C_0805_2012Metric",
                 value="1uF")
    c_out[1] += pot[2]   # Wiper
    c_out[2] += sig_out


mix_pot_circuit(audio_in, recovered_wet, gnd, audio_out)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Decay/Feedback Potentiometer (B100K)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def decay_pot_circuit(wet_in, gnd_net, fb_out):
    """
    Decay/feedback pot: B100K Alpha 9mm vertical.
    Sets reverb level attenuation. CW=max wet, CCW=silence.
    """

    pot = Part("Device", "R_Potentiometer",
               footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
               value="B100K")
    # Pins: 1=CW, 2=Wiper, 3=CCW

    r_in = Part("Device", "R",
                footprint="Resistor_SMD:R_0805_2012Metric",
                value="10K")
    r_in[1] += wet_in
    r_in[2] += pot[1]   # CW

    pot[3] += gnd_net   # CCW = GND
    pot[2] += fb_out    # Wiper = feedback/decay output


decay_pot_circuit(recovered_wet, gnd, feedback_net)


# ═══════════════════════════════════════════════════════════════
#  Board Floorplan — 8HP Eurorack: 100x50mm
# ═══════════════════════════════════════════════════════════════
EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 100.0,
        "height_mm": 50.0,
        "corner_radius_mm": 0.0,
    },
    "edge_anchors": [
        {"ref": "J5", "edge": "top"},   # Eurorack power header on top edge
    ],
}
