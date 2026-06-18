"""
Optical Tremolo Guitar Pedal
- TL072 dual op-amp: U1A = triangle wave LFO, U1B = VCA buffer
- LED + LDR vactrol for optical amplitude modulation
- Rate pot (B500K) controls LFO frequency
- Depth pot (B100K) controls modulation depth
- 6.35mm mono jacks: input (switched) and output
- 3PDT bypass footswitch (9-pin connector with custom footprint)
- Status LED with current limiting resistor
- 9V DC barrel jack power entry with filter caps
- Board: 110x58mm (1590B enclosure PCB)

3PDT footswitch custom footprint (standard 3x3 lug grid, 4.8mm pitch):
  Row 1 (pole 1): pad 1=A1(common), 2=B1(throw1), 3=C1(throw2)
  Row 2 (pole 2): pad 4=A2(common), 5=B2(throw1), 6=C2(throw2)
  Row 3 (pole 3): pad 7=A3(common), 8=B3(throw1), 9=C3(throw2)
"""

from skidl import *

# -----------------------------------------------------------------------
# Power rails
# -----------------------------------------------------------------------
v9    = Net("V9");   v9.drive    = POWER
vcc   = Net("VCC");  vcc.drive   = POWER   # after polarity diode
vref  = Net("VREF"); vref.drive  = POWER   # virtual mid-rail 4.5V
gnd   = Net("GND");  gnd.drive   = POWER

# -----------------------------------------------------------------------
# Signal nets
# -----------------------------------------------------------------------
audio_in      = Net("AUDIO_IN")
lfo_out       = Net("LFO_OUT")
rate_wiper    = Net("RATE_WIPER")
depth_wiper   = Net("DEPTH_WIPER")
vca_in        = Net("VCA_IN")
led_drive     = Net("LED_DRIVE")
bypass_in     = Net("BYPASS_IN")
bypass_effect = Net("BYPASS_EFFECT")
status_anode  = Net("STATUS_ANODE")

# -----------------------------------------------------------------------
# Power entry: barrel jack + protection + filter
# -----------------------------------------------------------------------
@subcircuit
def power_entry(v9, vcc, gnd):
    pwr = Part("Connector", "Barrel_Jack_Switch",
               footprint="Connector_BarrelJack:BarrelJack_Wuerth_6941xx301002")
    pwr.description = "9V DC barrel jack 2.1mm"
    v9  += pwr[1]
    gnd += pwr[2]
    gnd += pwr[3]

    c_bulk = Part("Device", "C_Polarized", value="100uF",
                  footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
    c_bulk.description = "Power bulk filter"
    v9  += c_bulk[1]
    gnd += c_bulk[2]

    c_hf = Part("Device", "C", value="100nF",
                footprint="Capacitor_THT:C_Radial_D5.0mm_H5.0mm_P2.00mm")
    c_hf.description = "Power HF bypass"
    v9  += c_hf[1]
    gnd += c_hf[2]

    d_rev = Part("Device", "D", value="1N4001",
                 footprint="Diode_THT:D_DO-41_SOD81_P7.62mm_Horizontal")
    d_rev.description = "Reverse polarity protection diode"
    v9  += d_rev["A"]
    vcc += d_rev["K"]

    # Post-diode VCC bulk cap
    c_vcc = Part("Device", "C_Polarized", value="47uF",
                 footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    c_vcc.description = "VCC bulk filter cap"
    vcc += c_vcc[1]
    gnd += c_vcc[2]

power_entry(v9, vcc, gnd)

# -----------------------------------------------------------------------
# VREF divider: VCC/2 mid-rail for single-supply op-amp biasing
# -----------------------------------------------------------------------
@subcircuit
def vref_divider(vcc, vref, gnd):
    r_top = Part("Device", "R", value="10K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r_bot = Part("Device", "R", value="10K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    c_vref = Part("Device", "C_Polarized", value="47uF",
                  footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    c_vref.description = "VREF bypass cap"
    vcc  += r_top[1]
    vref += r_top[2], r_bot[1], c_vref[1]
    gnd  += r_bot[2], c_vref[2]

vref_divider(vcc, vref, gnd)

# -----------------------------------------------------------------------
# Audio jacks (6.35mm mono, separate subcircuit so placer can edge-anchor
# them independently from the pots)
# -----------------------------------------------------------------------
@subcircuit
def audio_jacks(bypass_in, bypass_effect, gnd):
    # Use simple unswitched AudioJack2 (T=tip, S=sleeve) with NJ2FD footprint
    # The NJ2FD-V_Vertical has only S and T pads — matches AudioJack2 exactly
    j_in = Part("Connector_Audio", "AudioJack2",
                footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_in.description = "Input 6.35mm jack"
    j_in.edge_preference = "left"
    bypass_in += j_in["T"]
    gnd       += j_in["S"]

    j_out = Part("Connector_Audio", "AudioJack2",
                 footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical")
    j_out.description = "Output 6.35mm jack"
    j_out.edge_preference = "right"
    bypass_effect += j_out["T"]
    gnd           += j_out["S"]

audio_jacks(bypass_in, bypass_effect, gnd)

# -----------------------------------------------------------------------
# Rate pot (separate subcircuit — placer will put it near its own signals)
# -----------------------------------------------------------------------
@subcircuit
def rate_pot(vcc, rate_wiper, gnd):
    pot = Part("Device", "R_Potentiometer", value="500K",
               footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical_CircularHoles")
    pot.description = "Rate pot B500K"
    vcc        += pot[1]
    rate_wiper += pot[2]
    gnd        += pot[3]

rate_pot(vcc, rate_wiper, gnd)

# -----------------------------------------------------------------------
# Depth pot (separate subcircuit)
# -----------------------------------------------------------------------
@subcircuit
def depth_pot(led_drive, depth_wiper, gnd):
    pot = Part("Device", "R_Potentiometer", value="100K",
               footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical_CircularHoles")
    pot.description = "Depth pot B100K"
    led_drive   += pot[1]
    depth_wiper += pot[2]
    gnd         += pot[3]

depth_pot(led_drive, depth_wiper, gnd)

# -----------------------------------------------------------------------
# 3PDT bypass footswitch (9-pin connector + custom footprint)
# True-bypass wiring:
#   Effect OFF: poles A→B (input passes through, LED off)
#   Effect ON:  poles A→C (signal to circuit, circuit to output, LED on)
# -----------------------------------------------------------------------
@subcircuit
def bypass_switch(bypass_in, audio_in, bypass_effect, status_anode, gnd, vcc):
    sw = Part("Connector_Generic", "Conn_01x09",
              footprint="Footswitch_Custom:Footswitch_3PDT_9Lug")
    sw.description = "3PDT bypass footswitch"
    # Pole 1: audio input routing
    bypass_in     += sw[1]   # A1 = common (always the input)
    bypass_effect += sw[2]   # B1 = bypass throw (direct in→out when off)
    audio_in      += sw[3]   # C1 = effect throw (send to circuit when on)
    # Pole 2: audio output routing
    bypass_effect += sw[4]   # A2 = common (to output jack)
    bypass_effect += sw[5]   # B2 = bypass throw
    bypass_effect += sw[6]   # C2 = effect throw (from circuit output)
    # Pole 3: status LED
    status_anode  += sw[7]   # A3 = common (to LED)
    gnd           += sw[8]   # B3 = off (LED dark in bypass)
    vcc           += sw[9]   # C3 = on (LED lit when effect active)

bypass_switch(bypass_in, audio_in, bypass_effect, status_anode, gnd, vcc)

# -----------------------------------------------------------------------
# Status LED with current-limiting resistor
# -----------------------------------------------------------------------
@subcircuit
def status_led(status_anode, gnd):
    r = Part("Device", "R", value="4.7K",
             footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r.description = "Status LED current limiter"
    led = Part("Device", "LED", value="RED",
               footprint="LED_THT:LED_D5.0mm")
    led.description = "Status indicator LED"
    status_anode += r[1]
    r[2]         += led["A"]
    gnd          += led["K"]

status_led(status_anode, gnd)

# -----------------------------------------------------------------------
# TL072 LFO core (U1A oscillator) — without pots, just the timing network
# TL072 pin map (DIP-8):
#   1=U1A_out, 2=U1A_inv, 3=U1A_noninv, 4=V-, 5=U1B_noninv, 6=U1B_inv, 7=U1B_out, 8=V+
# -----------------------------------------------------------------------
@subcircuit
def tl072_core(vcc, vref, gnd, rate_wiper, lfo_out, led_drive, audio_in, vca_in, depth_wiper):
    u1 = Part("Amplifier_Operational", "TL072",
              footprint="Package_DIP:DIP-8_W7.62mm")
    u1.description = "TL072 dual op-amp"
    vcc += u1["V+"]
    gnd += u1["V-"]

    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_THT:C_Radial_D5.0mm_H5.0mm_P2.00mm")
    c_dec.description = "TL072 supply decoupling"
    vcc += c_dec[1]
    gnd += c_dec[2]

    # U1A: relaxation oscillator
    r_min = Part("Device", "R", value="10K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r_min.description = "LFO rate minimum resistor"
    lfo_rc = Net("LFO_RC")
    rate_wiper += r_min[1]
    lfo_rc     += r_min[2]

    c_int = Part("Device", "C", value="100nF",
                 footprint="Capacitor_THT:C_Radial_D5.0mm_H5.0mm_P2.00mm")
    c_int.description = "LFO integrating capacitor"
    lfo_out += c_int[1]
    lfo_rc  += c_int[2]

    lfo_rc += u1[2]   # U1A "-"

    r_hyst = Part("Device", "R", value="100K",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r_hyst.description = "LFO hysteresis resistor"
    lfo_out += r_hyst[1]
    vref    += r_hyst[2]
    vref    += u1[3]   # U1A "+"
    lfo_out += u1[1]   # U1A output

    # LED drive series resistor
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r_led.description = "Vactrol LED series resistor"
    lfo_out   += r_led[1]
    led_drive += r_led[2]

    # U1B: unity-gain audio buffer (VCA)
    c_in = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    c_in.description = "Input AC coupling"
    audio_in += c_in[1]
    vca_in   += c_in[2]

    r_bias = Part("Device", "R", value="1M",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r_bias.description = "VCA input bias to VREF"
    vca_in += r_bias[1]
    vref   += r_bias[2]

    vca_in += u1[5]   # U1B "+"
    vca_out = Net("VCA_BUF_OUT")
    vca_out += u1[7]   # U1B output
    vca_out += u1[6]   # U1B "-" (unity gain)

    c_out = Part("Device", "C_Polarized", value="10uF",
                 footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    c_out.description = "Output AC coupling"
    vca_out     += c_out[1]
    depth_wiper += c_out[2]

tl072_core(vcc, vref, gnd, rate_wiper, lfo_out, led_drive, audio_in, vca_in, depth_wiper)

# -----------------------------------------------------------------------
# Vactrol: IR LED optically coupled to LDR (GL5528 or equiv)
# -----------------------------------------------------------------------
@subcircuit
def vactrol(vca_in, led_drive, gnd):
    vled = Part("Device", "LED", value="IR",
                footprint="LED_THT:LED_D5.0mm")
    vled.description = "Vactrol IR LED"
    led_drive += vled["A"]
    gnd       += vled["K"]

    ldr = Part("Sensor_Optical", "LDR03",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    ldr.description = "Vactrol LDR GL5528"
    vca_in += ldr[1]
    gnd    += ldr[2]

vactrol(vca_in, led_drive, gnd)

# -----------------------------------------------------------------------
# Output stage: depth wiper -> 100R -> bypass_effect
# -----------------------------------------------------------------------
@subcircuit
def output_stage(depth_wiper, bypass_effect, gnd):
    r = Part("Device", "R", value="100R",
             footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal")
    r.description = "Output series resistor"
    depth_wiper   += r[1]
    bypass_effect += r[2]

output_stage(depth_wiper, bypass_effect, gnd)

# -----------------------------------------------------------------------
# Floorplan: 110x58mm for 1590B interior
# Pots at top (user-accessible through enclosure lid holes)
# Jacks on sides (left=in, right=out)
# Power at top-right edge
# Footswitch keepout at bottom-centre (switch body hangs below PCB)
# Electronics cluster in centre
# -----------------------------------------------------------------------
EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 110,
        "height_mm": 58
    },
    "edge_anchors": [
        {
            "selector": {"description": "Input 6.35mm jack"},
            "edge": "left"
        },
        {
            "selector": {"description": "Output 6.35mm jack"},
            "edge": "right"
        },
        {
            "selector": {"description": "9V DC barrel jack 2.1mm"},
            "edge": "top"
        },
    ],
    "fixed_positions": [
        {
            "ref": "J4",
            "x_mm": 45,
            "y_mm": 46,
            "rotation_deg": 0
        }
    ],
    "align": [
        {"refs": ["RV1", "RV2"], "axis": "y"},
    ],
    "distribute": [
        {"refs": ["RV1", "RV2"], "axis": "x"},
    ],
}
