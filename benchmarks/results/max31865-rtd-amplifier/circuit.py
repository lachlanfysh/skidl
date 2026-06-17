"""
MAX31865 RTD-to-digital converter breakout board.
SSOP-20 package, SPI interface for PT100/PT1000 RTDs.
3.3V logic, 430 ohm reference resistor for PT100.
2/3/4-wire RTD connection via screw terminals.
6-pin SPI header for host connection.

Uses LCSC C779509 (MAX31865AAP+T) via convert_lcsc since it is
not in the standard KiCad symbol library.
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# SPI / digital nets
spi_sclk = Net("SCLK")
spi_sdi  = Net("SDI")
spi_sdo  = Net("SDO")
spi_cs_n = Net("CS_N")
drdy_n   = Net("DRDY_N")

# Analog / RTD nets
refin_p   = Net("REFIN_P")
refin_n   = Net("REFIN_N")
isensor   = Net("ISENSOR")
force_p   = Net("FORCE_P")
force_n   = Net("FORCE_N")
rtdin_p   = Net("RTDIN_P")
rtdin_n   = Net("RTDIN_N")
bias_net  = Net("BIAS")

# MAX31865 SSOP-20 via convert_lcsc(lcsc='C779509')
u1 = Part("C779509", "MAX31865AAP+T",
          footprint="C779509:SSOP-20_L7.2-W5.3-P0.65-LS7.8-BL",
          value="MAX31865")
u1.ref = "U1"

# Power (analog + digital supplies share VCC on breakout)
vcc += u1["DVDD"], u1["VDD"]
gnd += u1["DGND"], u1["GND"], u1["GND2"]

# NC pin per known convention: tie to GND by pad number
gnd += u1["N.C."]

# SPI
spi_sclk += u1["SCLK"]
spi_sdi  += u1["SDI"]
spi_sdo  += u1["SDO"]
spi_cs_n += u1["~{CS}"]
drdy_n   += u1["~{DRDY}"]

# Analog RTD interface
bias_net += u1["BIAS"]
refin_p  += u1["REFIN+"]
refin_n  += u1["REFIN-"]
isensor  += u1["ISENSOR"]
force_p  += u1["FORCE+"]
force_n  += u1["FORCE-"]
rtdin_p  += u1["RTDIN+"]
rtdin_n  += u1["RTDIN-"]

# FORCE2: tie to RTDIN- for 3-wire RTD compensation (common breakout approach)
rtdin_n += u1["FORCE2"]

# 430 ohm reference resistor for PT100 (Rref = 430 * R0, R0=100Ω → Rref=430Ω)
# Connected between FORCE+ and REFIN-; REFIN+ = FORCE+ (top of Rref)
r_ref = Part("Device", "R", value="430R",
             footprint="Resistor_SMD:R_0805_2012Metric")
r_ref.ref = "R_REF"
r_ref.value = "430R"
force_p  += r_ref[1]   # High side: FORCE+ = REFIN+
refin_n  += r_ref[2]   # Low side: REFIN- = FORCE- via Rref

# REFIN+ connects to FORCE+ (top of reference resistor)
refin_p += force_p

# REFIN- connects to FORCE- (bottom of reference resistor)
refin_n += force_n

# BIAS decoupling: 100nF between BIAS and GND
c_bias = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
c_bias.ref = "C_BIAS"
bias_net += c_bias[1]
gnd      += c_bias[2]

# VCC decoupling: 100nF + 10uF bulk
c1 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0603_1608Metric")
c1.ref = "C1"
vcc += c1[1]
gnd += c1[2]

c2 = Part("Device", "C_Polarized", value="10uF",
          footprint="Capacitor_SMD:C_0805_2012Metric")
c2.ref = "C2"
vcc += c2[1]
gnd += c2[2]

# Second 100nF decoupling for DVDD domain
c3 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0603_1608Metric")
c3.ref = "C3"
vcc += c3[1]
gnd += c3[2]

# RTD input filter caps (noise rejection per datasheet fig. 10)
c_fp = Part("Device", "C", value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric")
c_fp.ref = "C_FP"
rtdin_p += c_fp[1]
gnd     += c_fp[2]

c_fn = Part("Device", "C", value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric")
c_fn.ref = "C_FN"
rtdin_n += c_fn[1]
gnd     += c_fn[2]

c_diff = Part("Device", "C", value="1nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
c_diff.ref = "C_DIFF"
rtdin_p += c_diff[1]
rtdin_n += c_diff[2]

# 4-pin screw terminal for 4-wire RTD
# Pin 1: FORCE+ (excitation out)
# Pin 2: RTDIN+ (sense+)
# Pin 3: RTDIN- (sense-)
# Pin 4: FORCE- (excitation return / REFIN-)
j_rtd4 = Part("Connector", "Screw_Terminal_01x04",
              footprint="TerminalBlock_Altech:Altech_AK300_1x04_P5.00mm_45-Degree",
              value="RTD_4WIRE")
j_rtd4.ref = "J_RTD4"
j_rtd4[1] += force_p
j_rtd4[2] += rtdin_p
j_rtd4[3] += rtdin_n
j_rtd4[4] += force_n

# 3-pin screw terminal for 3-wire RTD
# Pin 1: FORCE+ (excitation out)
# Pin 2: FORCE2/RTDIN- (3rd RTD wire, also sense-)
# Pin 3: RTDIN- (same as pin 2 for 3-wire mode)
j_rtd3 = Part("Connector", "Screw_Terminal_01x03",
              footprint="TerminalBlock_Altech:Altech_AK300_1x03_P5.00mm_45-Degree",
              value="RTD_3WIRE")
j_rtd3.ref = "J_RTD3"
j_rtd3[1] += force_p
j_rtd3[2] += isensor
j_rtd3[3] += rtdin_n

# 6-pin SPI header: VCC, GND, SCLK, SDI, SDO, CS_N
j_spi = Part("Connector", "Conn_01x06_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
             value="SPI_HEADER")
j_spi.ref = "J_SPI"
j_spi[1] += vcc
j_spi[2] += gnd
j_spi[3] += spi_sclk
j_spi[4] += spi_sdi
j_spi[5] += spi_sdo
j_spi[6] += spi_cs_n

# DRDY 2-pin header
j_drdy = Part("Connector", "Conn_01x02_Pin",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
              value="DRDY_HDR")
j_drdy.ref = "J_DRDY"
j_drdy[1] += drdy_n
j_drdy[2] += gnd

# Pull-up on DRDY_N
r_drdy = Part("Device", "R", value="10k",
              footprint="Resistor_SMD:R_0603_1608Metric")
r_drdy.ref = "R_DRDY"
vcc    += r_drdy[1]
drdy_n += r_drdy[2]

# Pull-up on CS_N (keeps device deselected when SPI bus idle)
r_cs = Part("Device", "R", value="10k",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_cs.ref = "R_CS"
vcc      += r_cs[1]
spi_cs_n += r_cs[2]

# SPI header and DRDY on left edge (host side)
j_spi.edge_preference = "left"
j_drdy.edge_preference = "left"

# RTD screw terminals: fix positions so they sit inboard on the right side.
# Altech AK300 at 5mm pitch: 4-pin block ~22mm wide, 3-pin block ~17mm wide.
# Place near the right edge but allow ~8mm clearance for silkscreen.
EDA_FLOORPLAN = {
    "outline": {"width_mm": 70.0, "height_mm": 48.0, "corner_radius_mm": 1.5},
    "fixed_positions": [
        {"ref": "J_RTD4", "x_mm": 50.0, "y_mm": 15.0, "rotation_deg": 0},
        {"ref": "J_RTD3", "x_mm": 50.0, "y_mm": 35.0, "rotation_deg": 0},
    ],
}
