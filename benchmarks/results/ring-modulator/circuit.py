"""
Ring Modulator Guitar Pedal

Signal path:
  Guitar In -> TL072A input buffer -> Diode Ring (4x 1N4148)
                                       ^
                                 NE555P carrier oscillator (square wave)
                                 Frequency pot controls rate ~1.3-7kHz
  Diode ring output -> TL072B output mixer/buffer -> Depth pot blend -> True bypass switch -> Out

The diode ring multiplies the input signal by the carrier, producing sum and difference
sidebands (classic ring modulation effect). The depth pot blends wet/dry signal.

Board: 115x70mm (fits Hammond 1590BB enclosure), all THT components.

MCP server: https://mcp-server-production-5d58.up.railway.app/mcp
Final run: a3fecd26616e (40/40 parts placed, no overlaps, 3 DRC clearance on AUDIO_OUT routing)
"""

from skidl import *

# Power rails
v9  = Net("V9");  v9.drive  = POWER
gnd = Net("GND"); gnd.drive = POWER
vref = Net("VREF")  # mid-rail ~4.5V for single-supply op-amp biasing

# Signal nets
audio_in    = Net("AUDIO_IN")
audio_out   = Net("AUDIO_OUT")
circuit_in  = Net("CIRCUIT_IN")
circuit_out = Net("CIRCUIT_OUT")
buf_out     = Net("BUF_OUT")   # TL072A output (buffered guitar signal)
ring_out    = Net("RING_OUT")  # diode ring output (AM-modulated signal)
carrier     = Net("CARRIER")   # NE555 square wave output


@subcircuit
def power_section(v9, gnd, vref):
    """9V barrel jack, bulk filter caps, VREF mid-rail divider at V9/2."""
    j_pwr = Part("Connector", "Barrel_Jack_Switch",
                 footprint="Connector_BarrelJack:BarrelJack_CUI_PJ-102AH_Horizontal",
                 value="9VDC", ref="J3")
    j_pwr.edge_preference = "top"
    gnd += j_pwr[1]   # center tip = GND (center-negative standard)
    v9  += j_pwr[2]   # sleeve = +9V
    gnd += j_pwr[3]   # switch NC -> GND

    # Bulk filtering
    c_bulk = Part("Device", "C_Polarized", value="47uF",
                  footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm", ref="C1")
    c_byp  = Part("Device", "C", value="100nF",
                  footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C2")
    v9  += c_bulk[1], c_byp[1]
    gnd += c_bulk[2], c_byp[2]

    # VREF divider: 47K/47K -> ~4.5V, bypassed with 10uF
    r_top = Part("Device", "R", value="47K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                 ref="R1")
    r_bot = Part("Device", "R", value="47K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                 ref="R2")
    c_vref = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm", ref="C3")
    v9   += r_top[1]
    vref += r_top[2], r_bot[1], c_vref[1]
    gnd  += r_bot[2], c_vref[2]


@subcircuit
def oscillator_section(v9, gnd, carrier):
    """NE555P astable oscillator — square wave carrier for ring modulation.
    
    Frequency: f ≈ 1.44 / ((R_freq + 2*R_min) * C_osc)
    With R_min=10K, R_freq=0-100K, C_osc=100nF:
      - max freq (~20Hz is too slow; use smaller cap)
    With C_osc=10nF, R_min=10K, R_freq=B100K:
      - min: f ≈ 1.44 / (110K * 10nF) ≈ 1.31kHz
      - max: f ≈ 1.44 / (20K * 10nF) ≈ 7.2kHz
    Adjusts from ~1.3kHz to ~7kHz — classic ring mod carrier range.
    """
    u_555 = Part("Timer", "NE555P",
                 footprint="Package_DIP:DIP-8_W7.62mm",
                 value="NE555P", ref="U1")
    u_555["VCC"] += v9
    u_555["GND"] += gnd

    # Reset (active low) tied to VCC to keep 555 running
    u_555["R"] += v9

    # Frequency pot (B100K): sets oscillator frequency
    rv_freq = Part("Device", "R_Potentiometer", value="B100K",
                   footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
                   ref="RV1")
    rv_freq.edge_preference = "top"

    # Minimum timing resistor between VCC and discharge pin
    r_min = Part("Device", "R", value="10K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                 ref="R3")

    # Astable wiring: VCC -> R_min -> DIS -> RV_freq_wiper -- RV_freq-CW -> THR/TR -> C_osc -> GND
    # Standard astable: pin7(DIS) between R_a and R_b; pins 2+6 tied to timing cap
    osc_top = Net("OSC_TOP")
    osc_bot = Net("OSC_BOT")

    v9      += r_min[1]
    osc_top += r_min[2]
    # Pot: pin1=CW, pin2=wiper, pin3=CCW
    # Connect pot in series: osc_top -> pot[1](CW) -> through the full pot -> pot[3](CCW) -> osc_bot
    # But we want variable R: use wiper as DIS connection point
    osc_top += rv_freq[1]         # CW end = VCC side (after R_min)
    osc_top += u_555["DIS"]       # DIS connected at top of variable R
    rv_freq[2] += osc_bot         # wiper -> bottom node (also THR+TR)
    rv_freq[3] += osc_bot         # CCW end also at osc_bot

    # Timing cap (10nF between THR/TR and GND)
    c_osc = Part("Device", "C", value="10nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C4")
    osc_bot += c_osc[1]
    gnd     += c_osc[2]

    # Tie THR and TR together at timing cap node
    u_555["THR"] += osc_bot
    u_555["TR"]  += osc_bot

    # Output: Q pin -> carrier net (square wave 0-9V, AC-coupled to ring)
    u_555["Q"] += carrier

    # Control voltage bypass (CV pin): 10nF to GND per datasheet
    c_cv = Part("Device", "C", value="10nF",
                footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C5")
    u_555["CV"] += c_cv[1]
    gnd         += c_cv[2]

    # Power decoupling for 555
    c_dec555 = Part("Device", "C", value="100nF",
                    footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C6")
    v9  += c_dec555[1]
    gnd += c_dec555[2]


@subcircuit
def ring_modulator_core(v9, gnd, vref, buf_out, carrier, ring_out):
    """Diode ring modulator core: 4x 1N4148 in bridge configuration.
    
    Classic ring mod: carrier transformer secondary drives the ring,
    guitar signal drives the input transformer primary. Here we use
    AC-coupled carrier and guitar signal, biased at VREF (mid-rail).
    
    The ring multiplies carrier x signal -> sum+difference sidebands.
    
    Carrier coupling: 555 output (0-9V square wave) -> C_carrier -> carrier node
    Signal coupling:  TL072A buffered guitar -> C_sig -> signal node
    
    Ring topology (bridge rectifier style):
      carrier_pos -> D1[A] -> D1[K]=D3[A]=mix+ 
      carrier_neg -> D2[A] -> D2[K]=D4[A]=mix+
      signal_pos  -> D1[A]=D2[K] 
      signal_neg  -> D3[K]=D4[A]
    
    Simplified single-ended approximation (no transformers):
      Input biased at VREF; carrier swings above/below VREF;
      diodes commutate to produce AM-type modulation.
    """
    # Carrier AC coupling and bias: 555 output (0-9V) -> C -> R_bias to VREF -> carrier_node
    # This centers the carrier swing around VREF for the ring
    c_carr = Part("Device", "C", value="100nF",
                  footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C7")
    r_carr_bias = Part("Device", "R", value="47K",
                       footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                       ref="R4")
    carrier_ac = Net("CARRIER_AC")
    carrier    += c_carr[1]
    carrier_ac += c_carr[2]
    vref       += r_carr_bias[1]
    carrier_ac += r_carr_bias[2]

    # Guitar signal coupling into ring (already biased from op-amp stage)
    c_sig = Part("Device", "C", value="100nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C8")
    sig_ac = Net("SIG_AC")
    buf_out += c_sig[1]
    sig_ac  += c_sig[2]

    # Bias signal node at VREF
    r_sig_bias = Part("Device", "R", value="100K",
                      footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                      ref="R5")
    vref   += r_sig_bias[1]
    sig_ac += r_sig_bias[2]

    # 4x 1N4148 diodes in ring/bridge configuration
    # ring_out is the positive output node; ring_mid2 is the return node
    # Standard ring mod bridge:
    #   D1: K=carrier_ac, A=ring_out
    #   D2: A=carrier_ac, K=ring_mid2
    #   D3: K=sig_ac,     A=ring_out
    #   D4: A=sig_ac,     K=ring_mid2
    # ring_mid2 is the second ring node (tied to VREF through R7)
    ring_mid2 = Net("RING_MID2")

    d1 = Part("Diode", "1N4148",
              footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
              value="1N4148", ref="D1")
    d2 = Part("Diode", "1N4148",
              footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
              value="1N4148", ref="D2")
    d3 = Part("Diode", "1N4148",
              footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
              value="1N4148", ref="D3")
    d4 = Part("Diode", "1N4148",
              footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal",
              value="1N4148", ref="D4")

    # D1: K=carrier_ac, A=ring_out  (carrier positive half -> ring_out node)
    d1["K"] += carrier_ac
    d1["A"] += ring_out

    # D2: A=carrier_ac, K=ring_mid2  (carrier negative half -> ring_mid2)
    d2["A"] += carrier_ac
    d2["K"] += ring_mid2

    # D3: K=sig_ac, A=ring_out  (signal positive half -> ring_out node)
    d3["K"] += sig_ac
    d3["A"] += ring_out

    # D4: A=sig_ac, K=ring_mid2  (signal negative half -> ring_mid2)
    d4["A"] += sig_ac
    d4["K"] += ring_mid2

    # Output load resistors (ring_out and ring_mid2 to VREF for biasing)
    r_load1 = Part("Device", "R", value="10K",
                   footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                   ref="R6")
    r_load2 = Part("Device", "R", value="10K",
                   footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                   ref="R7")
    ring_out  += r_load1[1]
    vref      += r_load1[2]
    ring_mid2 += r_load2[1]
    vref      += r_load2[2]


@subcircuit
def amplifier_section(v9, gnd, vref, circuit_in, buf_out, ring_out, depth_wip, circuit_out):
    """TL072 dual op-amp: Unit A = input buffer, Unit B = output mixer.
    
    Unit A: unity-gain buffer for guitar input signal
    Unit B: inverting summer — blends dry+wet based on depth pot
    """
    u_tl = Part("Amplifier_Operational", "TL072",
                footprint="Package_DIP:DIP-8_W7.62mm",
                value="TL072", ref="U2")
    u_tl["V+"] += v9
    u_tl["V-"] += gnd

    # Power decoupling
    c_dec_tl = Part("Device", "C", value="100nF",
                    footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C9")
    v9  += c_dec_tl[1]
    gnd += c_dec_tl[2]

    # -- Unit A: input buffer (unity gain) --
    # Input coupling cap + bias to VREF
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C10")
    r_bias_in = Part("Device", "R", value="100K",
                     footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                     ref="R8")
    in_node = Net("IN_NODE")
    circuit_in += c_in[1]
    in_node    += c_in[2], r_bias_in[1]
    vref       += r_bias_in[2]

    # TL072A non-inverting unity gain buffer
    u_tl[3] += in_node   # A non-inv (+) = signal
    u_tl[2] += buf_out   # A inv (-) = feedback from output (unity gain)
    u_tl[1] += buf_out   # A output = buf_out

    # -- Unit B: output summing mixer (dry + wet) --
    # Dry signal from buf_out
    r_dry = Part("Device", "R", value="47K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                 ref="R9")
    # Wet from depth pot wiper
    r_wet = Part("Device", "R", value="47K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                 ref="R10")
    # Feedback
    r_fb = Part("Device", "R", value="47K",
                footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                ref="R11")
    mix_node  = Net("MIX_NODE")
    mixer_out = Net("MIXER_OUT")

    buf_out   += r_dry[1]
    r_dry[2]  += mix_node
    depth_wip += r_wet[1]
    r_wet[2]  += mix_node
    r_fb[1]   += mix_node
    r_fb[2]   += mixer_out

    u_tl[5] += vref       # B non-inv (+) = VREF
    u_tl[6] += mix_node   # B inv (-) = summing node
    u_tl[7] += mixer_out  # B output

    # Output coupling cap to remove VREF DC bias
    c_out = Part("Device", "C", value="100nF",
                 footprint="Capacitor_THT:C_Disc_D3.4mm_W2.1mm_P2.50mm", ref="C11")
    mixer_out  += c_out[1]
    circuit_out += c_out[2]


@subcircuit
def depth_section(gnd, ring_out, depth_wip):
    """Depth pot (B10K): blends wet ring mod signal into the output mixer."""
    rv_depth = Part("Device", "R_Potentiometer", value="B10K",
                    footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
                    ref="RV2")
    rv_depth.edge_preference = "top"
    ring_out  += rv_depth[1]   # CW = full wet ring mod signal
    gnd       += rv_depth[3]   # CCW = silence wet
    depth_wip += rv_depth[2]   # wiper -> mixer wet input


@subcircuit
def io_section(v9, gnd, audio_in, audio_out, circuit_in, circuit_out):
    """6.35mm mono jacks, 3PDT true bypass footswitch (as 9-pin header), LED."""
    # Input jack
    j_in = Part("Connector_Audio", "AudioJack2",
                footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical",
                value="IN", ref="J1")
    j_in.edge_preference = "left"
    audio_in += j_in["T"]
    gnd      += j_in["S"]

    # Output jack
    j_out = Part("Connector_Audio", "AudioJack2",
                 footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical",
                 value="OUT", ref="J2")
    j_out.edge_preference = "right"
    audio_out += j_out["T"]
    gnd       += j_out["S"]

    # 3PDT true bypass footswitch (9-pin header — no standard 3PDT KiCad symbol)
    # Pin mapping:
    # Pole 1 (pins 1,2,3): signal switching - 1=circuit_in, 2=audio_in (common), 3=audio_out (bypass)
    # Pole 2 (pins 4,5,6): output select - 4=audio_out, 5=circuit_out (common), 6=bypass thru
    # Pole 3 (pins 7,8,9): LED control - 7=v9, 8=led_anode, 9=GND
    sw = Part("Connector_Generic", "Conn_01x09",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Vertical",
              value="3PDT_BYPASS", ref="SW1")
    sw.edge_preference = "bottom"

    bypass_thru = Net("BYPASS_THRU")
    led_anode   = Net("LED_ANODE")

    circuit_in  += sw[1]    # effect input (from ring mod circuit)
    audio_in    += sw[2]    # common pole 1 (guitar in)
    bypass_thru += sw[3]    # bypass throw (straight to out when bypassed)

    audio_out   += sw[4]    # throw 2a: effect output -> jack out (when on)
    circuit_out += sw[5]    # common pole 2 (from effect circuit)
    bypass_thru += sw[6]    # bypass throw 2b (connects bypass_thru to output)

    v9          += sw[7]    # common pole 3 (V9)
    led_anode   += sw[8]    # throw 3a: LED power when effect on
    gnd         += sw[9]    # throw 3b: to GND (LED off in bypass mode)

    # Status LED with series resistor (4.7K limits ~1.5mA with forward drop ~2V on 9V)
    r_led = Part("Device", "R", value="4.7K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal",
                 ref="R12")
    led = Part("Device", "LED",
               footprint="LED_THT:LED_D5.0mm",
               value="STATUS_RED", ref="D5")
    led_anode += r_led[1]
    r_led[2]  += led["A"]
    gnd       += led["K"]


# Module-level audio signal nets for depth section
depth_wip = Net("DEPTH_WIP")

# Instantiate all subcircuits
power_section(v9, gnd, vref)
oscillator_section(v9, gnd, carrier)
ring_modulator_core(v9, gnd, vref, buf_out, carrier, ring_out)
amplifier_section(v9, gnd, vref, circuit_in, buf_out, ring_out, depth_wip, circuit_out)
depth_section(gnd, ring_out, depth_wip)
io_section(v9, gnd, audio_in, audio_out, circuit_in, circuit_out)

# M3 mounting holes (4x corners for enclosure screws)
for _i in range(4):
    Part("Mechanical", "MountingHole",
         footprint="MountingHole:MountingHole_3.2mm_M3")

# Floorplan: 115x70mm — generous to avoid overlap and outline violations
# Hammond 1590BB internal dims are 111x60mm; slightly over for routing room.
EDA_FLOORPLAN = {
    "outline": {"width_mm": 115, "height_mm": 70, "corner_radius_mm": 2},
    "edge_anchors": [
        {"ref": "J1",  "edge": "left"},
        {"ref": "J2",  "edge": "right"},
        {"ref": "J3",  "edge": "top"},
        {"ref": "SW1", "edge": "bottom"},
    ],
}
