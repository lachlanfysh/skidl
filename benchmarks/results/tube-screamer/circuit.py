"""
Tube Screamer overdrive guitar pedal — Ibanez TS808/TS9 clone
TL072 dual op-amp, true bypass DPDT footswitch, 9V center-negative
Target: Hammond 1590B enclosure (~100x56mm PCB)
All through-hole construction.
"""
from skidl import *

# Power rails
vbat = Net("VBAT"); vbat.drive = POWER
gnd  = Net("GND");  gnd.drive  = POWER
vref = Net("VREF")

# Signal flow
audio_in    = Net("AUDIO_IN")
audio_out   = Net("AUDIO_OUT")
circuit_in  = Net("CIRCUIT_IN")
circuit_out = Net("CIRCUIT_OUT")
drive_out   = Net("DRIVE_OUT")


@subcircuit
def power_supply(vbat, gnd, vref):
    """9V barrel jack, bulk filtering, VREF mid-rail bias at VBAT/2."""
    j_pwr = Part("Connector", "Barrel_Jack",
                  footprint="Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal")
    j_pwr.edge_preference = "top"
    j_pwr[1] += vbat   # sleeve = +9V (center-negative adapter)
    j_pwr[2] += gnd    # tip = GND

    c_bulk = Part("Device", "C_Polarized", value="47uF",
                   footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
    c_bypass = Part("Device", "C", value="100nF",
                     footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    vbat += c_bulk[1], c_bypass[1]
    gnd  += c_bulk[2], c_bypass[2]

    rb1 = Part("Device", "R", value="10K",
                footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    rb2 = Part("Device", "R", value="10K",
                footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    vbat += rb1[1]
    vref += rb1[2], rb2[1]
    gnd  += rb2[2]

    c_vref = Part("Device", "C_Polarized", value="10uF",
                   footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    vref += c_vref[1]
    gnd  += c_vref[2]


@subcircuit
def effect_engine(vbat, gnd, vref, sig_in, drive_out, level_out):
    """TL072 DIP-8: Unit A = clipping drive, Unit B = unity buffer. Tone + Level pots."""

    # TL072 dual op-amp
    op = Part("Amplifier_Operational", "TL072",
               footprint="Package_DIP:DIP-8_W7.62mm")
    vbat += op[8]   # V+
    gnd  += op[4]   # V-

    c_dec = Part("Device", "C", value="100nF",
                  footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    vbat += c_dec[1]
    gnd  += c_dec[2]

    # --- Unit A: inverting clipping stage ---
    # Non-inv (+) = VREF bias
    op[3] += vref

    # Input coupling cap + bias pull to VREF
    c_in = Part("Device", "C", value="22nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    r_in_bias = Part("Device", "R", value="10K",
                      footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    sig_biased = Net("SIG_BIASED")
    sig_in     += c_in[1]
    sig_biased += c_in[2], r_in_bias[1]
    vref       += r_in_bias[2]

    # Input gain resistor to inv (-)
    r_gain = Part("Device", "R", value="10K",
                   footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    opa_inv = Net("OPA_INV")
    sig_biased += r_gain[1]
    opa_inv    += r_gain[2], op[2]

    opa_out = Net("OPA_OUT")
    opa_out += op[1]

    # Feedback path: R_fb_min (4.7K) + C_fb (47nF) set minimum gain and LF rolloff
    r_fb_min = Part("Device", "R", value="4.7K",
                     footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_fb = Part("Device", "C", value="47nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    opa_out += r_fb_min[1]
    fb_node = Net("FB_NODE")
    fb_node += r_fb_min[2], c_fb[1]
    opa_inv += c_fb[2]

    # Drive pot (500K) in parallel with feedback: pins 1,3 are ends, pin 2 is wiper
    pot_drive = Part("Device", "R_Potentiometer", value="500K",
                      footprint="Potentiometer_THT:Potentiometer_Alps_RK09L_Single_Vertical")
    pot_drive.edge_preference = "top"
    opa_out += pot_drive[1]   # end 1
    opa_inv += pot_drive[3]   # end 3 (feedback variable gain)
    pot_drive[2] += NC        # wiper unused — TS808 uses only the ends for variable R

    # HF rolloff cap across feedback
    c_hf = Part("Device", "C", value="51pF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    opa_out += c_hf[1]
    opa_inv += c_hf[2]

    # Anti-parallel clipping diodes (1N4148 pair)
    d_clip1 = Part("Device", "D", value="1N4148",
                    footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal")
    d_clip2 = Part("Device", "D", value="1N4148",
                    footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal")
    opa_inv += d_clip1[1];  opa_out += d_clip1[2]   # D1: anode→inv, cathode→out
    opa_out += d_clip2[1];  opa_inv += d_clip2[2]   # D2: anode→out, cathode→inv

    drive_out += opa_out

    # --- Passive tone filter ---
    c_tone = Part("Device", "C", value="22nF",
                   footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    pot_tone = Part("Device", "R_Potentiometer", value="20K",
                     footprint="Potentiometer_THT:Potentiometer_Alps_RK09L_Single_Vertical")
    pot_tone.edge_preference = "top"
    tone_mid   = Net("TONE_MID")
    tone_wiper = Net("TONE_WIPER")
    drive_out  += c_tone[1]
    tone_mid   += c_tone[2], pot_tone[1]
    gnd        += pot_tone[3]
    tone_wiper += pot_tone[2]

    # Tone output coupling + VREF bias
    c_tone_out = Part("Device", "C", value="22nF",
                       footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    r_tone_bias = Part("Device", "R", value="10K",
                        footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    tone_buf_in = Net("TONE_BUF_IN")
    tone_wiper  += c_tone_out[1]
    tone_buf_in += c_tone_out[2], r_tone_bias[1]
    vref        += r_tone_bias[2]

    # --- Unit B: unity-gain buffer ---
    op[5] += tone_buf_in
    buf_out = Net("BUF_OUT")
    buf_out += op[7]
    op[6]  += buf_out

    # Level pot (100K output divider)
    pot_level = Part("Device", "R_Potentiometer", value="100K",
                      footprint="Potentiometer_THT:Potentiometer_Alps_RK09L_Single_Vertical")
    pot_level.edge_preference = "top"
    buf_out      += pot_level[1]
    level_wiper  = Net("LEVEL_WIPER")
    level_wiper  += pot_level[2]
    gnd          += pot_level[3]

    # Output coupling cap
    c_out = Part("Device", "C", value="100nF",
                  footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    level_wiper += c_out[1]
    level_out   += c_out[2]


@subcircuit
def io_true_bypass(vbat, gnd, audio_in, audio_out, circuit_in, circuit_out):
    """6.35mm jacks, DPDT footswitch true bypass, LED indicator."""
    j_in = Part("Connector_Audio", "AudioJack2",
                 footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_in.edge_preference = "left"
    gnd      += j_in["S"]
    audio_in += j_in["T"]

    j_out = Part("Connector_Audio", "AudioJack2",
                  footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_out.edge_preference = "right"
    gnd       += j_out["S"]
    audio_out += j_out["T"]

    # DPDT true-bypass
    # Pole1: B(2)=audio_in common, A(1)=circuit_in (ON), C(3)=audio_out (bypass OFF)
    # Pole2: B(5)=circuit_out common, A(4)=audio_out (effect ON to output jack)
    fsw = Part("Switch", "SW_DPDT_x2",
                footprint="Button_Switch_THT:SW_PUSH_E-Switch_FS5700DP_DPDT")
    fsw.edge_preference = "bottom"
    audio_in    += fsw[2]
    circuit_in  += fsw[1]
    audio_out   += fsw[3]
    circuit_out += fsw[5]
    audio_out   += fsw[4]

    # LED indicator: 4.7K + red LED, powered from VBAT
    r_led = Part("Device", "R", value="4.7K",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    led = Part("Device", "LED",
                footprint="LED_THT:LED_D5.0mm")
    vbat     += r_led[1]
    r_led[2] += led[1]
    gnd      += led[2]


# Instantiate
power_supply(vbat, gnd, vref)
effect_engine(vbat, gnd, vref, circuit_in, drive_out, circuit_out)
io_true_bypass(vbat, gnd, audio_in, audio_out, circuit_in, circuit_out)

# M3 mounting holes (4x corners)
for _i in range(4):
    Part("Mechanical", "MountingHole",
         footprint="MountingHole:MountingHole_3.2mm_M3")

# Floorplan: Hammond 1590B footprint (~112x60mm inside, PCB 100x56mm)
EDA_FLOORPLAN = {
    "outline": {"width_mm": 100, "height_mm": 56, "corner_radius_mm": 2},
    "edge_anchors": [
        {"ref": "J1",  "edge": "left"},
        {"ref": "J2",  "edge": "right"},
        {"ref": "J3",  "edge": "top"},
        {"ref": "SW1", "edge": "bottom"},
    ],
    "align": [
        {"refs": ["RV1", "RV2", "RV3"], "axis": "y"},
    ],
    "distribute": [
        {"refs": ["RV1", "RV2", "RV3"], "axis": "x"},
    ],
}
