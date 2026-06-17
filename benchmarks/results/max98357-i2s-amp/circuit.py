"""
MAX98357A I2S Class-D Mono Amp Breakout
========================================
All-in-one digital audio amp: I2S input -> DAC -> Class-D amplifier -> speaker.
Delivers 3.2W into 4 ohm (5V, 10% THD). VDD range 2.7V-5.5V.
Built-in filterless output, thermal and over-current protection.

Breakout design:
- USB-C (5V) power input with ferrite bead filtering
- 5-pin I2S header (VDD, GND, BCLK, LRCLK, DIN)
- Screw terminal speaker output
- 100nF x2 decoupling on VDD (auto-placed near IC)
- Gain select: 100k to VDD = 9dB (float=15dB, GND=12dB)
- SD_MODE: 100k pull-up to VDD = left channel, always-on

Part note: No KiCad symbol for MAX98357A exists in standard libs.
Symbol loaded from EasyEDA via convert_lcsc(lcsc="C910544").
Footprint substituted to KiCad standard TQFN-16-1EP_3x3mm (pin-compatible).
N.C. pads (5,6,12,13) tied to GND per TQFN best practice.
"""

from skidl import *

# Power rails
vbus = Net("VBUS"); vbus.drive = POWER
vdd = Net("VDD"); vdd.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2S signal nets
bclk = Net("BCLK")
lrclk = Net("LRCLK")
din = Net("DIN")

# Speaker output nets (differential, Class-D filterless)
outp = Net("OUTP")
outn = Net("OUTN")

# Gain/SD control nets
gain_slot = Net("GAIN_SLOT")
sd_mode = Net("SD_MODE")

# ---- U1: MAX98357A I2S Class-D Amplifier ----
# Symbol from EasyEDA (LCSC C910544). KiCad TQFN-16 3x3mm footprint.
u1 = Part("C910544", "MAX98357AETE+T",
          footprint="Package_DFN_QFN:TQFN-16-1EP_3x3mm_P0.5mm_EP1.6x1.6mm")

vdd += u1["VDD"]
gnd += u1["GND"], u1["EP"]
bclk += u1["BCLK"]
lrclk += u1["LRCLK"]
din += u1["DIN"]
outp += u1["OUTP"]
outn += u1["OUTN"]
gain_slot += u1["GAIN_SLOT"]
sd_mode += u1["~{SD_MODE}"]
# N.C. pads tied to GND (standard practice for unused TQFN pads)
gnd += u1[5], u1[6], u1[12], u1[13]

# ---- J1: USB-C power input (5V, up to 3A) ----
# 6P power-only symbol paired with matching 6P footprint (GCT USB4125)
j1 = Part("Connector", "USB_C_Receptacle_PowerOnly_6P",
          footprint="Connector_USB:USB_C_Receptacle_GCT_USB4125-xx-x_6P_TopMnt_Horizontal")
j1.edge_preference = "bottom"
vbus += j1["VBUS"]
gnd += j1["GND"], j1["SHIELD"]

# 5.1k CC pull-downs for USB-C 5V sink identification (no PD chip needed)
r_cc1 = Part("Device", "R", value="5.1K", footprint="Resistor_SMD:R_0402_1005Metric")
r_cc2 = Part("Device", "R", value="5.1K", footprint="Resistor_SMD:R_0402_1005Metric")
j1["CC1"] += r_cc1[1]; gnd += r_cc1[2]
j1["CC2"] += r_cc2[1]; gnd += r_cc2[2]

# ---- FB1: Ferrite bead for power supply filtering (VBUS -> VDD) ----
# 600R @ 100MHz, 0805 package for sufficient current rating (3A+)
fb1 = Part("Device", "FerriteBead", value="600R@100MHz", footprint="Resistor_SMD:R_0805_2012Metric")
vbus += fb1[1]
vdd += fb1[2]

# ---- Decoupling caps on VDD (auto-placed near U1 by layout engine) ----
c1 = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric")
c2 = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric")
vdd += c1[1], c2[1]
gnd += c1[2], c2[2]

# Bulk caps: VBUS input (10uF) and VDD rail (10uF)
c3 = Part("Device", "C_Polarized", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")
c4 = Part("Device", "C_Polarized", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")
vbus += c3[1]; gnd += c3[2]
vdd += c4[1]; gnd += c4[2]

# ---- J2: I2S input header (5-pin: VDD, GND, BCLK, LRCLK, DIN) ----
j2 = Part("Connector_Generic", "Conn_01x05",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical")
j2.edge_preference = "top"
vdd += j2[1]; gnd += j2[2]; bclk += j2[3]; lrclk += j2[4]; din += j2[5]

# ---- J3: Speaker output screw terminal (3.5mm pitch horizontal) ----
j3 = Part("Connector", "Screw_Terminal_01x02",
          footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_PT-1,5-2-3.5-H_1x02_P3.50mm_Horizontal")
j3.edge_preference = "top"
outp += j3[1]
outn += j3[2]

# ---- R_GAIN: Gain select (GAIN_SLOT to VDD via 100k = 9dB) ----
r_gain = Part("Device", "R", value="100K", footprint="Resistor_SMD:R_0402_1005Metric")
gain_slot += r_gain[1]; vdd += r_gain[2]

# ---- R_SD: SD_MODE pull-up (100k to VDD = left channel, always-on) ----
r_sd = Part("Device", "R", value="100K", footprint="Resistor_SMD:R_0402_1005Metric")
sd_mode += r_sd[1]; vdd += r_sd[2]

# ---- Mounting holes (M2) ----
mh1 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_2.2mm_M2")
mh2 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_2.2mm_M2")
mh3 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_2.2mm_M2")
mh4 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_2.2mm_M2")

EDA_FLOORPLAN = {
    "outline": {"width_mm": 50, "height_mm": 36, "corner_radius_mm": 1},
}
