from skidl import *

# Fuzz Face Guitar Pedal Clone
# Classic 2-transistor NPN fuzz circuit (2N3904)
# True bypass with 3PDT footswitch
# Fits in 1590B enclosure (PCB: 110mm x 55mm)
#
# MCP server: https://mcp-server-production-5d58.up.railway.app/mcp
# Final run: 97c916d91b0e (score 20/100, placement OK, routing attempted)

v9  = Net("V9");  v9.drive  = POWER
gnd = Net("GND"); gnd.drive = POWER

# === INPUT JACK (1/4" TS Mono, panel-mount vertical) ===
j_in = Part("Connector_Audio", "AudioJack2",
            footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical",
            value="IN", ref="J1")
j_in.edge_preference = "left"
net_jack_in_tip = Net("JACK_IN_TIP")
net_jack_in_tip += j_in["T"]
gnd             += j_in["S"]

# === OUTPUT JACK (1/4" TS Mono, panel-mount vertical) ===
j_out = Part("Connector_Audio", "AudioJack2",
             footprint="Connector_Audio:Jack_6.35mm_Neutrik_NJ2FD-V_Vertical",
             value="OUT", ref="J2")
j_out.edge_preference = "right"
net_jack_out_tip = Net("JACK_OUT_TIP")
net_jack_out_tip += j_out["T"]
gnd              += j_out["S"]

# === DC POWER JACK (9V center-negative) ===
# Note: Connector_BarrelJack:BarrelJack_Horizontal confirmed to exist on server
j_pwr = Part("Connector", "Barrel_Jack_Switch",
             footprint="Connector_BarrelJack:BarrelJack_Horizontal",
             value="9VDC", ref="J3")
gnd += j_pwr[1]   # center (negative)
v9  += j_pwr[2]   # sleeve (positive)
gnd += j_pwr[3]   # switch NC

# === 3PDT TRUE BYPASS SWITCH ===
# Modeled as 9-pin header (no 3PDT symbol in KiCad)
# Physical 3PDT: 3 poles, each DPDT (common + 2 throws)
# Conn_Generic gets ref "J4" automatically
sw = Part("Connector_Generic", "Conn_01x09",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Vertical",
          value="3PDT_BYPASS", ref="J4")

# Pole 1: Route input signal (effect or bypass)
effect_in   = Net("EFFECT_IN")
bypass_thru = Net("BYPASS_THRU")
net_jack_in_tip += sw[1]    # common 1 = input from jack
effect_in       += sw[2]    # throw 1a -> to effect circuit
bypass_thru     += sw[3]    # throw 1b -> direct to output

# Pole 2: Route output (effect output or bypass signal)
effect_out = Net("EFFECT_OUT")
effect_out       += sw[4]   # common 2 = from volume pot
net_jack_out_tip += sw[5]   # throw 2a -> output jack (effect mode)
bypass_thru      += sw[6]   # throw 2b -> output jack (bypass mode)

# Pole 3: LED switching
led_anode = Net("LED_ANODE")
v9        += sw[7]           # common 3 = V9
led_anode += sw[8]           # throw 3a -> LED (effect on)
gnd       += sw[9]           # throw 3b (bypass: LED off path)

# === INPUT COUPLING CAP (2.2uF electrolytic) ===
c_in = Part("Device", "C_Polarized",
            footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm",
            value="2.2uF", ref="C1")
fuzz_base = Net("FUZZ_BASE")
effect_in += c_in[1]   # + plate (from switch)
fuzz_base += c_in[2]   # - plate (to Q1 base)

# === Q1 - First NPN transistor (input/amplifier stage) ===
r_bias1 = Part("Device", "R", value="33K",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
               ref="R1")
r_emit1 = Part("Device", "R", value="470",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
               ref="R2")
r_load1 = Part("Device", "R", value="8.2K",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
               ref="R3")
q1 = Part("Transistor_BJT", "2N3904",
          footprint="Package_TO_SOT_THT:TO-92_Inline", ref="Q1")
q1_col = Net("Q1_COL")
v9        += r_bias1[1]
fuzz_base += r_bias1[2]    # 33K bias from V9 to base
fuzz_base += q1["B"]
q1["E"]   += r_emit1[1]   # 470 ohm emitter degeneration
gnd        += r_emit1[2]
v9         += r_load1[1]   # 8.2K collector load to V9
q1_col     += r_load1[2]
q1_col     += q1["C"]

# === FUZZ POTENTIOMETER (500K) ===
# Controls gain/saturation by varying signal fed to Q2
pot_fuzz = Part("Device", "R_Potentiometer", value="500K",
                footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
                ref="RV1")
fuzz_wiper = Net("FUZZ_WIPER")
q1_col     += pot_fuzz[1]   # CW (from Q1 collector)
fuzz_wiper += pot_fuzz[2]   # wiper
gnd        += pot_fuzz[3]   # CCW to GND

# === Q2 - Second NPN transistor (gain/output stage) ===
r_emit2 = Part("Device", "R", value="33",
               footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
               ref="R4")
q2 = Part("Transistor_BJT", "2N3904",
          footprint="Package_TO_SOT_THT:TO-92_Inline", ref="Q2")
q2_col = Net("Q2_COL")
fuzz_wiper += q2["B"]
q2["E"]   += r_emit2[1]    # 33 ohm emitter resistor
gnd        += r_emit2[2]
v9         += q2["C"]
q2_col     += q2["C"]

# === OUTPUT COUPLING CAP (2.2uF electrolytic) ===
c_out = Part("Device", "C_Polarized",
             footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm",
             value="2.2uF", ref="C2")
vol_in = Net("VOL_IN")
q2_col += c_out[1]
vol_in += c_out[2]

# === VOLUME POTENTIOMETER (500K log) ===
pot_vol = Part("Device", "R_Potentiometer", value="500K",
               footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical",
               ref="RV2")
vol_in     += pot_vol[1]    # CW (signal from Q2)
effect_out += pot_vol[2]    # wiper -> to bypass switch output
gnd        += pot_vol[3]    # CCW to GND

# === POWER SUPPLY FILTERING ===
c_bulk = Part("Device", "C_Polarized",
              footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm",
              value="47uF", ref="C3")
v9  += c_bulk[1]
gnd += c_bulk[2]

c_fil = Part("Device", "C",
             footprint="Capacitor_THT:C_Disc_D4.7mm_W2.5mm_P5.00mm",
             value="100nF", ref="C4")
v9  += c_fil[1]
gnd += c_fil[2]

# === LED STATUS INDICATOR ===
led = Part("Device", "LED",
           footprint="LED_THT:LED_D5.0mm",
           value="STATUS", ref="D1")
r_led = Part("Device", "R", value="4.7K",
             footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P7.62mm_Horizontal",
             ref="R5")
led_anode += r_led[1]
r_led[2]  += led["A"]
gnd       += led["K"]

# === BOARD OUTLINE AND PLACEMENT HINTS ===
# 110mm x 55mm - generous for 1590B enclosure (internal: ~110mm x 58mm)
# Audio jacks left/right via edge_preference, barrel jack and 3PDT auto-placed
EDA_FLOORPLAN = {
    "outline": {"width_mm": 110, "height_mm": 55, "corner_radius_mm": 0},
    "edge_anchors": [
        {"ref": "J1", "edge": "left"},
        {"ref": "J2", "edge": "right"},
    ],
}
