"""
Boss CE-2 chorus pedal clone — classic analog chorus guitar effect.

Signal path:
  Input jack -> TL072A (input buffer unity gain) -> LPF -> MN3007 BBD
  -> LPF -> Depth pot (wet blend) -> TL072B (output summing mixer)
  -> DPDT true bypass footswitch -> Output jack

MN3101 generates complementary clock signals (CP1/CP2) from an RC oscillator
whose frequency is controlled by the Rate pot. VGG_OUT from MN3101 pin 8
supplies the MN3007 VGG bias rail.

Board target: Hammond 1590B, PCB 100x56mm all through-hole.
"""

from skidl import *

# ---- Power rails ----
vbat = Net("VBAT");  vbat.drive = POWER   # +9V (tip = GND, sleeve = +9V for Boss standard)
gnd  = Net("GND");   gnd.drive  = POWER
vref = Net("VREF")                          # mid-rail bias ~4.5V for single-supply audio

# ---- Clock nets ----
cp1  = Net("CP1")
cp2  = Net("CP2")
vgg  = Net("VGG")     # MN3101 VGG_OUT -> MN3007 VGG pin

# ---- Audio signal nets ----
audio_in    = Net("AUDIO_IN")    # tip of input jack (post true-bypass pole)
audio_out   = Net("AUDIO_OUT")   # tip of output jack (post true-bypass pole)
circuit_in  = Net("CIRCUIT_IN")  # effect input (from bypass switch pole A)
buf_out     = Net("BUF_OUT")     # TL072A buffered output
bbd_in      = Net("BBD_IN")      # after LP filter, into MN3007 IN pin
bbd_out     = Net("BBD_OUT")     # MN3007 OUT2, after LP filter
depth_wip   = Net("DEPTH_WIP")   # depth pot wiper (wet level to mixer)
circuit_out = Net("CIRCUIT_OUT") # TL072B mixed output (effect output to bypass switch)


@subcircuit
def power_section(vbat, gnd, vref):
    """9V barrel jack, bulk filter cap, VREF mid-rail divider."""
    j_pwr = Part("Connector", "Barrel_Jack",
                 footprint="Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal")
    j_pwr.edge_preference = "top"
    j_pwr[1] += vbat
    j_pwr[2] += gnd

    c_bulk = Part("Device", "C_Polarized", value="100uF",
                  footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
    c_bypass = Part("Device", "C", value="100nF",
                    footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    vbat += c_bulk[1], c_bypass[1]
    gnd  += c_bulk[2], c_bypass[2]

    # VREF divider 47K / 47K -> 4.5V at VREF, bypassed to AC ground
    r_top = Part("Device", "R", value="47K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_bot = Part("Device", "R", value="47K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_ref = Part("Device", "C_Polarized", value="47uF",
                 footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    vbat += r_top[1]
    vref += r_top[2], r_bot[1], c_ref[1]
    gnd  += r_bot[2], c_ref[2]


@subcircuit
def clock_driver_section(vbat, gnd, vref, cp1, cp2, vgg):
    """MN3101 clock generator. Rate pot controls oscillator timing RC."""
    mn3101 = Part("Timer", "MN3101",
                  footprint="Package_DIP:DIP-8_W7.62mm")
    mn3101["VDD"] += vbat
    mn3101["GND"] += gnd
    mn3101["CP1"] += cp1
    mn3101["CP2"] += cp2
    mn3101["VGG_OUT"] += vgg

    # Rate potentiometer (50K): CCW=fast, CW=slow
    rv_rate = Part("Device", "R_Potentiometer", value="50K",
                   footprint="Potentiometer_THT:Potentiometer_Alps_RK09L_Single_Vertical")
    rv_rate.edge_preference = "top"
    vbat += rv_rate[3]    # CW end -> max resistance -> slower LFO
    gnd  += rv_rate[1]    # CCW end -> min resistance -> faster LFO

    # OX1 RC oscillator: pot wiper -> R_osc -> C_osc -> GND
    # oscillator node connects OX1, OX2, OX3 internally per datasheet
    r_osc = Part("Device", "R", value="10K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_osc = Part("Device", "C", value="10nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    osc_node = Net("OSC_NODE")
    rv_rate[2] += r_osc[1]       # wiper to series resistor
    r_osc[2]   += osc_node
    c_osc[1]   += osc_node
    c_osc[2]   += gnd
    mn3101["OX1"] += osc_node
    mn3101["OX2"] += osc_node
    mn3101["OX3"] += gnd

    # VGG filter cap
    c_vgg = Part("Device", "C_Polarized", value="10uF",
                 footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    vgg += c_vgg[1]
    gnd += c_vgg[2]

    # Power decoupling for MN3101
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    vbat += c_dec[1]
    gnd  += c_dec[2]


@subcircuit
def bbd_section(vbat, gnd, vref, cp1, cp2, vgg, circuit_in, buf_out, bbd_in, bbd_out, depth_wip, circuit_out):
    """TL072A input buffer + LP filter + MN3007 BBD + output LP filter + TL072B mixer."""

    # TL072 (dual op-amp): A = pins 1(out),2(-),3(+); B = pins 7(out),6(-),5(+)
    u_tl072 = Part("Amplifier_Operational", "TL072",
                   footprint="Package_DIP:DIP-8_W7.62mm")
    u_tl072["V+"] += vbat
    u_tl072["V-"] += gnd

    # TL072A: unity-gain input buffer, biased at VREF
    r_in = Part("Device", "R", value="1M",
                footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    in_node = Net("IN_NODE")
    circuit_in += c_in[1]         # input coupling cap (blocks guitar DC)
    c_in[2]    += in_node
    in_node    += r_in[1]
    r_in[2]    += vref             # bleed to VREF to set bias at half supply
    u_tl072[3] += in_node          # non-inverting +
    u_tl072[2] += buf_out          # inverting - feedback
    u_tl072[1] += buf_out          # output

    # TL072B: output summing mixer (inverting summer: dry + wet)
    r_dry = Part("Device", "R", value="22K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_wet = Part("Device", "R", value="22K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_fb  = Part("Device", "R", value="22K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_out = Part("Device", "C", value="100nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    mix_node = Net("MIX_NODE")
    mixer_out = Net("MIXER_OUT")   # named net to avoid N$x anonymous net
    buf_out  += r_dry[1]
    r_dry[2] += mix_node
    depth_wip += r_wet[1]          # depth pot wiper -> wet mixer input
    r_wet[2] += mix_node
    r_fb[1]  += mix_node           # feedback from output
    r_fb[2]  += mixer_out          # connect r_fb to output net explicitly
    u_tl072[5] += vref             # B non-inverting to VREF
    u_tl072[6] += mix_node         # B inverting = summing node
    u_tl072[7] += mixer_out        # B output -> named net
    # Output coupling cap (blocks DC bias from VREF)
    c_out[1]   += mixer_out
    c_out[2]   += circuit_out

    # MN3007 BBD
    mn3007 = Part("Audio", "MN3007",
                  footprint="Package_DIP:DIP-8_W7.62mm")
    mn3007["GND"] += gnd
    mn3007["VDD"] += vbat
    mn3007["VGG"] += vgg
    mn3007["CP1"] += cp1
    mn3007["CP2"] += cp2

    # Input LP filter: 22K + 5.6nF to limit BBD aliasing
    r_lpf_in = Part("Device", "R", value="22K",
                    footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_lpf_in = Part("Device", "C", value="5.6nF",
                    footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    buf_out   += r_lpf_in[1]
    r_lpf_in[2] += bbd_in
    bbd_in    += c_lpf_in[1]
    gnd       += c_lpf_in[2]
    mn3007["IN"] += bbd_in

    # BBD output: OUT2 through LP reconstruction filter to bbd_out
    # OUT1 tied to OUT2 raw node (both outputs feed the reconstruction filter in CE-2)
    bbd_out2_pre = Net("BBD_OUT2_PRE")
    mn3007["OUT2"] += bbd_out2_pre
    mn3007["OUT1"] += bbd_out2_pre  # tie both outputs together into LP filter

    # Output LP filter: 22K + 5.6nF to reconstruct audio from BBD outputs
    r_lpf_out = Part("Device", "R", value="22K",
                     footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    c_lpf_out = Part("Device", "C", value="5.6nF",
                     footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    bbd_out2_pre  += r_lpf_out[1]
    r_lpf_out[2]  += bbd_out
    bbd_out       += c_lpf_out[1]
    gnd           += c_lpf_out[2]

    # Single shared bypass cap for MN3007 and TL072 on this board section
    c_bypass_bbd = Part("Device", "C", value="100nF",
                        footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm")
    vbat += c_bypass_bbd[1]
    gnd  += c_bypass_bbd[2]


@subcircuit
def depth_section(gnd, bbd_out, depth_wip):
    """Depth pot (50K): blends wet BBD signal level into mixer."""
    rv_depth = Part("Device", "R_Potentiometer", value="50K",
                    footprint="Potentiometer_THT:Potentiometer_Alps_RK09L_Single_Vertical")
    rv_depth.edge_preference = "top"
    bbd_out  += rv_depth[3]   # CW = full wet
    gnd      += rv_depth[1]   # CCW = no wet
    depth_wip += rv_depth[2]  # wiper -> mixer R_wet input


@subcircuit
def io_section(vbat, gnd, audio_in, audio_out, circuit_in, circuit_out):
    """6.35mm input/output jacks, DPDT true bypass footswitch, LED indicator."""

    # Input jack
    j_in = Part("Connector_Audio", "AudioJack2",
                footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_in.edge_preference = "left"
    audio_in += j_in["T"]
    gnd      += j_in["S"]

    # Output jack
    j_out = Part("Connector_Audio", "AudioJack2",
                 footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_out.edge_preference = "right"
    audio_out += j_out["T"]
    gnd       += j_out["S"]

    # DPDT true bypass footswitch
    # Pole1 (pins 1,2,3): A(1)=circuit_in, B(2)=audio_in, C(3)=audio_out (bypass path)
    # Pole2 (pins 4,5,6): A(4)=audio_out, B(5)=circuit_out, C(6)=NC
    fsw = Part("Switch", "SW_DPDT_x2",
               footprint="Button_Switch_THT:SW_PUSH_E-Switch_FS5700DP_DPDT")
    fsw.edge_preference = "bottom"
    circuit_in  += fsw[1]    # effect send
    audio_in    += fsw[2]    # common (guitar signal)
    audio_out   += fsw[3]    # bypass throw (direct wire when off)
    audio_out   += fsw[4]    # effect return to output (when on)
    circuit_out += fsw[5]    # effect output common
    # fsw[6] left unconnected (second bypass throw, not needed for mono bypass)

    # LED indicator: on when effect is engaged
    r_led = Part("Device", "R", value="4.7K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    led = Part("Device", "LED",
               footprint="LED_THT:LED_D3.0mm")
    vbat     += r_led[1]
    r_led[2] += led["A"]
    gnd      += led["K"]


# ============================================================
# Instantiate all subcircuits
# ============================================================

power_section(vbat, gnd, vref)
clock_driver_section(vbat, gnd, vref, cp1, cp2, vgg)
depth_section(gnd, bbd_out, depth_wip)
bbd_section(vbat, gnd, vref, cp1, cp2, vgg, circuit_in, buf_out, bbd_in, bbd_out, depth_wip, circuit_out)
io_section(vbat, gnd, audio_in, audio_out, circuit_in, circuit_out)

# M3 mounting holes (4x corners for Hammond 1590B)
for _i in range(4):
    Part("Mechanical", "MountingHole",
         footprint="MountingHole:MountingHole_3.2mm_M3")

# EDA floorplan hint: Hammond 1590B, 112x62mm PCB with edge margin for DIP ICs
EDA_FLOORPLAN = {
    "outline": {"width_mm": 112, "height_mm": 62, "corner_radius_mm": 2},
    "edge_anchors": [
        {"ref": "J1",  "edge": "left"},
        {"ref": "J2",  "edge": "right"},
        {"ref": "J3",  "edge": "top"},    # barrel jack
        {"ref": "SW1", "edge": "bottom"},  # footswitch
    ],
    "align": [
        {"refs": ["RV1", "RV2"], "axis": "y"},   # Rate and Depth pots aligned horizontally
    ],
    "distribute": [
        {"refs": ["RV1", "RV2"], "axis": "x"},
    ],
    "keepout": [
        # 5mm keepout from all edges to keep DIP ICs clear of board outline
        {"region": "edge_margin", "margin_mm": 6},
    ],
}
