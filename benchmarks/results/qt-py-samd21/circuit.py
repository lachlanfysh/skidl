"""
QT Py SAMD21 — Seeeduino XIAO form factor (~17.5x21mm)
ATSAMD21E18A-AU TQFP-32, native USB-C, NeoPixel WS2812B, STEMMA QT I2C, AP2112K-3.3 LDO
Boot button. 11 GPIO castellated edge pads.

Parts sourced via eda-mcp MCP server search_kicad / convert_lcsc:
  MCU:      LCSC C618771 → ATSAMD21E18A-AU (TQFP-32)
  USB-C:    KiCad Connector:USB_C_Receptacle_USB2.0_16P + HCTL footprint
  JST-SH:   KiCad Connector_Generic:Conn_01x04 + JST-SH footprint
  LDO:      KiCad Regulator_Linear:AP2112K-3.3 (SOT-23-5)
  NeoPixel: KiCad LED:WS2812B (PLCC4 5x5mm)
  SW_Push:  KiCad Switch:SW_Push
"""

import os
os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from skidl import *
set_default_tool(KICAD9)


# =============================================================================
# Global nets
# =============================================================================
VBUS   = Net("VBUS");   VBUS.drive   = POWER
VCC3V3 = Net("+3V3");   VCC3V3.drive = POWER
GND    = Net("GND");    GND.drive    = POWER

USB_DP  = Net("USB_DP")
USB_DM  = Net("USB_DM")
USB_CC1 = Net("USB_CC1")
USB_CC2 = Net("USB_CC2")

SCL = Net("SCL")
SDA = Net("SDA")

NEOPIXEL_DIN = Net("NEOPIXEL_DIN")
RESET_N      = Net("RESET_N")
BOOT_N       = Net("BOOT_N")

# GPIO castellated pads
GPIO_A0  = Net("A0")
GPIO_A1  = Net("A1")
GPIO_A2  = Net("A2")
GPIO_A3  = Net("A3")
GPIO_A6  = Net("A6")
GPIO_A7  = Net("A7")
GPIO_A8  = Net("A8")
GPIO_A9  = Net("A9")
GPIO_A10 = Net("A10")


# =============================================================================
# USB-C connector + CC pull-downs
# KiCad native Connector:USB_C_Receptacle_USB2.0_16P
# Pin names: GND, VBUS, CC1, CC2, D-, D+, SBU1, SBU2, SHIELD
# Footprint HCTL_HC-TYPE-C-16P-01A pin numbers match native symbol numbers
# =============================================================================
@subcircuit
def usb_c_input(vbus, gnd, dp, dm, cc1, cc2):
    global VBUS, GND, USB_DP, USB_DM, USB_CC1, USB_CC2

    conn = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
                footprint="Connector_USB:USB_C_Receptacle_HCTL_HC-TYPE-C-16P-01A")
    conn["VBUS"]   += vbus
    conn["GND"]    += gnd
    conn["D+"]     += dp
    conn["D-"]     += dm
    conn["CC1"]    += cc1
    conn["CC2"]    += cc2
    conn["SBU1"]   += gnd
    conn["SBU2"]   += gnd
    conn["SHIELD"] += gnd

    # CC pull-down 5.1k for UFP/device role
    r1 = Part("Device", "R", value="5.1k",
              footprint="Resistor_SMD:R_0402_1005Metric")
    r1[1] += cc1; r1[2] += gnd

    r2 = Part("Device", "R", value="5.1k",
              footprint="Resistor_SMD:R_0402_1005Metric")
    r2[1] += cc2; r2[2] += gnd

    # 100nF VBUS decap at connector
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += vbus; c1[2] += gnd


# =============================================================================
# AP2112K-3.3 LDO — 5V → 3.3V
# Pins: VIN(1), GND(2), EN(3), NC(4), VOUT(5)
# =============================================================================
@subcircuit
def ldo_3v3(vin, vout, gnd):
    global VBUS, VCC3V3, GND

    ldo = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    ldo["VIN"]  += vin
    ldo["GND"]  += gnd
    ldo["EN"]   += vin   # always-on
    ldo["VOUT"] += vout

    # 1uF input cap
    cin = Part("Device", "C", value="1uF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cin[1] += vin; cin[2] += gnd

    # 1uF output cap
    cout = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cout[1] += vout; cout[2] += gnd

    # 100nF bypass
    cbyp = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cbyp[1] += vout; cbyp[2] += gnd


# =============================================================================
# ATSAMD21E18A-AU TQFP-32 MCU
# Pin map (from convert_lcsc C618771):
#   1=PA00, 2=PA01, 3=PA02, 4=PA03, 5=PA04, 6=PA05, 7=PA06, 8=PA07
#   9=VDDANA, 10=GND, 11=PA08, 12=PA09, 13=PA10, 14=PA11
#   15=PA14, 16=PA15, 17=PA16, 18=PA17, 19=PA18, 20=PA19
#   21=PA22, 22=PA23, 23=PA24, 24=PA25, 25=PA27, 26=~{RESET}
#   27=PA28, 28=GND, 29=VDDCORE, 30=VDDIN, 31=PA30, 32=PA31
#
# Functional assignments for QT Py:
#   PA24=USB_D+, PA25=USB_D-  (USB native)
#   PA22=SDA, PA23=SCL        (SERCOM3 I2C)
#   PA11=NeoPixel data out
#   PA07=BOOT button (active low)
#   PA30=SWCLK, PA31=SWDIO   (SWD debug)
#   PA02=A0, PA04=A1, PA10=A2, PA11 used for neopixel, others=GPIO
# =============================================================================
@subcircuit
def samd21_mcu(vcc, gnd, dp, dm, scl, sda, neopixel, reset_n, boot_n,
               a0, a1, a2, a3, a6, a7, a8, a9, a10):
    global VCC3V3, GND

    mcu = Part("C618771", "ATSAMD21E18A-AU",
               footprint="Package_QFP:TQFP-32_7x7mm_P0.8mm")

    # Power
    mcu["VDDANA"]  += vcc
    mcu["VDDCORE"] += vcc    # filtered internally via internal LDO; needs cap
    mcu["VDDIN"]   += vcc
    mcu["GND"]     += gnd

    # USB
    mcu["PA24"] += dp
    mcu["PA25"] += dm

    # I2C
    mcu["PA22"] += sda
    mcu["PA23"] += scl

    # NeoPixel
    mcu["PA11"] += neopixel

    # Boot button sense
    mcu["PA07"] += boot_n

    # Reset
    mcu["~{RESET}"] += reset_n

    # SWD pads exposed as test points (no dedicated header on this tiny board)
    swclk_tp = Part("Connector", "Conn_01x01_Pin",
                    footprint="TestPoint:TestPoint_Pad_1.0x1.0mm", value="SWCLK_TP")
    swdio_tp = Part("Connector", "Conn_01x01_Pin",
                    footprint="TestPoint:TestPoint_Pad_1.0x1.0mm", value="SWDIO_TP")
    mcu["PA30"] += swclk_tp[1]
    mcu["PA31"] += swdio_tp[1]

    # GPIO castellated edge
    mcu["PA02"] += a0
    mcu["PA04"] += a1
    mcu["PA10"] += a2
    mcu["PA08"] += a3
    mcu["PA06"] += a6
    mcu["PA09"] += a7
    mcu["PA14"] += a8
    mcu["PA15"] += a9
    mcu["PA16"] += a10

    # Remaining unused GPIO
    mcu["PA00"] += Net("NC_PA00")
    mcu["PA01"] += Net("NC_PA01")
    mcu["PA03"] += Net("NC_PA03")
    mcu["PA05"] += Net("NC_PA05")
    mcu["PA17"] += Net("NC_PA17")
    mcu["PA18"] += Net("NC_PA18")
    mcu["PA19"] += Net("NC_PA19")
    mcu["PA27"] += Net("NC_PA27")
    mcu["PA28"] += Net("NC_PA28")

    # Decoupling caps — one per VDD pin (100nF each)
    for _ in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vcc; c[2] += gnd

    # VDDCORE cap (1uF — feeds internal 1.2V LDO output)
    c_core = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_core[1] += vcc; c_core[2] += gnd

    # Reset pull-up
    r_rst = Part("Device", "R", value="10k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vcc; r_rst[2] += reset_n

    # Reset filter cap
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_rst[1] += reset_n; c_rst[2] += gnd


# =============================================================================
# WS2812B NeoPixel
# Pins: VDD(1), DOUT(2), VSS(3), DIN(4)
# =============================================================================
@subcircuit
def neopixel_block(vcc, gnd, mcu_data_out):
    global VCC3V3, GND

    neo_din = Net("NEO_DIN")

    # 300-ohm series resistor on DIN to protect against ringing
    r_din = Part("Device", "R", value="300",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_din[1] += mcu_data_out
    r_din[2] += neo_din

    led = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    led["VDD"]  += vcc
    led["VSS"]  += gnd
    led["DIN"]  += neo_din
    led["DOUT"] += Net("NC_NEOPIXEL_DOUT")

    # 100nF decap (required within 100mm of NeoPixel)
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += vcc; c[2] += gnd


# =============================================================================
# STEMMA QT — JST-SH 4-pin (LCSC C160390 BM04B-SRSS-TB)
# Adafruit STEMMA QT pinout: pin1=GND, pin2=VCC, pin3=SDA, pin4=SCL
# =============================================================================
@subcircuit
def stemma_qt_conn(vcc, gnd, sda, scl):
    global VCC3V3, GND, SDA, SCL

    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    conn[1] += gnd
    conn[2] += vcc
    conn[3] += sda
    conn[4] += scl

    # I2C pull-ups 4.7k
    r_sda = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc; r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl[1] += vcc; r_scl[2] += scl


# =============================================================================
# Boot button (BOOT_N pulled high, pulled low by button)
# =============================================================================
@subcircuit
def boot_button_block(boot_n, gnd):
    global BOOT_N, GND, VCC3V3

    sw = Part("Switch", "SW_Push",
              footprint="Button_Switch_SMD:SW_SPST_TL3342")
    sw[1] += boot_n
    sw[2] += gnd

    r = Part("Device", "R", value="10k",
             footprint="Resistor_SMD:R_0402_1005Metric")
    r[1] += VCC3V3
    r[2] += boot_n


# =============================================================================
# Castellated edge pads — single-pin connectors as placeholders
# =============================================================================
@subcircuit
def castellated_pads(vcc, gnd, a0, a1, a2, a3, a6, a7, a8, a9, a10):
    for label, net in [
        ("3V3",  vcc),
        ("GND",  gnd),
        ("A0",   a0),
        ("A1",   a1),
        ("A2",   a2),
        ("A3",   a3),
        ("A6",   a6),
        ("A7",   a7),
        ("A8",   a8),
        ("A9",   a9),
        ("A10",  a10),
    ]:
        pad = Part("Connector", "Conn_01x01_Pin",
                   footprint="TestPoint:TestPoint_Pad_1.0x1.0mm",
                   value=label)
        pad[1] += net


# =============================================================================
# Floorplan — QT Py SAMD21 is ~17.5mm wide x 21mm tall (Seeeduino XIAO form factor)
# USB-C at top, STEMMA QT on right side, castellated pads along left/right/bottom
# MCU centered, passives scattered around it
# =============================================================================
EDA_FLOORPLAN = {
    "outline": {"width_mm": 17.5, "height_mm": 21.0, "corner_radius_mm": 0.5},
    "fixed_positions": [
        # MCU slightly above center — leaves room for passives below and USB at top
        {"ref": "U2", "x_mm": 8.75, "y_mm": 9.0, "rotation_deg": 0},
    ],
    "edge_anchors": [
        # USB-C connector at top center (J1 = native KiCad USB_C_Receptacle)
        {"ref": "J1", "edge": "top"},
        # STEMMA QT connector on the right side (faces down on XIAO form factor)
        {"ref": "J2",   "edge": "bottom"},
        # Castellated pads: left edge (6 pads: 3V3, GND, A0, A1, A2, A3)
        {"ref": "J3",  "edge": "left"},
        {"ref": "J4",  "edge": "left"},
        {"ref": "J5",  "edge": "left"},
        {"ref": "J6",  "edge": "left"},
        {"ref": "J7",  "edge": "left"},
        {"ref": "J8",  "edge": "left"},
        # Castellated pads: right edge (5 pads: A6, A7, A8, A9, A10)
        {"ref": "J9",  "edge": "right"},
        {"ref": "J10", "edge": "right"},
        {"ref": "J11", "edge": "right"},
        {"ref": "J12", "edge": "right"},
        {"ref": "J13", "edge": "right"},
    ],
}


# =============================================================================
# Top-level assembly
# =============================================================================
usb_c_input(VBUS, GND, USB_DP, USB_DM, USB_CC1, USB_CC2)
ldo_3v3(VBUS, VCC3V3, GND)

samd21_mcu(
    VCC3V3, GND,
    USB_DP, USB_DM,
    SCL, SDA,
    NEOPIXEL_DIN, RESET_N, BOOT_N,
    GPIO_A0, GPIO_A1, GPIO_A2, GPIO_A3,
    GPIO_A6, GPIO_A7, GPIO_A8, GPIO_A9, GPIO_A10,
)

neopixel_block(VCC3V3, GND, NEOPIXEL_DIN)
stemma_qt_conn(VCC3V3, GND, SDA, SCL)
boot_button_block(BOOT_N, GND)
castellated_pads(VCC3V3, GND,
                 GPIO_A0, GPIO_A1, GPIO_A2, GPIO_A3,
                 GPIO_A6, GPIO_A7, GPIO_A8, GPIO_A9, GPIO_A10)

generate_schematic(auto_stub=False, erc_max_iterations=8)
