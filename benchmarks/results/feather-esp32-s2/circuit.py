"""
Feather ESP32-S2 — Adafruit-compatible Feather format board
ESP32-S2-WROVER module with WiFi, native USB, 4MB Flash, 2MB PSRAM.
Single-core 240MHz. LiPo charger (MCP73831), AP2112K-3.3 LDO,
STEMMA QT connector, NeoPixel, Feather Wing headers.
Board: 55x25mm (generous for the WROVER module)
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# Global power nets
# ============================================================
VCC = Net("+3V3"); VCC.drive = POWER
VBUS = Net("VBUS"); VBUS.drive = POWER
VBAT = Net("VBAT"); VBAT.drive = POWER
GND = Net("GND"); GND.drive = POWER

# Internal nets
VSYS = Net("VSYS")
NEOPIXEL = Net("NEOPIXEL")
BOOT0 = Net("BOOT0")
RST_N = Net("RST_N")
USB_DP = Net("USB_DP")
USB_DM = Net("USB_DM")

# Feather wing pin buses
# FL12=SDA, FL13=SCL are shared with the I2C bus — use same net objects
feather_left_pins = [Net(f"FL{i}") for i in range(16)]
feather_right_pins = [Net(f"FR{i}") for i in range(10)]

# SDA/SCL ARE the feather header pins — avoid net merge by using the same net objects
SDA = feather_left_pins[12]
SDA.name = "SDA"
SCL = feather_left_pins[13]
SCL.name = "SCL"

# ============================================================
# USB-C Connector subcircuit
# ============================================================
@subcircuit
def usb_c_connector(vbus, gnd, dp, dm):
    """USB-C receptacle with CC resistors for UFP device mode."""
    global VCC
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
               footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11")

    # Power pins — multiple VBUS and GND pads
    usb["VBUS"] += vbus
    usb["GND"] += gnd
    usb["SHIELD"] += gnd

    # Data pins
    usb["D+"] += dp
    usb["D-"] += dm

    # SBU1/SBU2 — not used, tie to GND
    usb["SBU1"] += gnd
    usb["SBU2"] += gnd

    # CC pull-downs for UFP (device) — 5.1k to GND
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    usb["CC1"] += r_cc1[1]
    r_cc1[2] += gnd
    usb["CC2"] += r_cc2[1]
    r_cc2[2] += gnd

    # VBUS decoupling
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd


# ============================================================
# Battery charging subcircuit (MCP73831)
# ============================================================
@subcircuit
def battery_charger(vbus, vbat, gnd):
    """MCP73831-based single-cell LiPo charger, SOT-23-5."""
    chg = Part("Battery_Management", "MCP73831-2-OT",
               footprint="Package_TO_SOT_SMD:SOT-23-5")

    chg["V_{DD}"] += vbus
    chg["V_{SS}"] += gnd
    chg["V_{BAT}"] += vbat

    # PROG resistor sets charge current: 2K -> 500mA
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    chg["PROG"] += r_prog[1]
    r_prog[2] += gnd

    # Charge status LED (active low, orange)
    r_stat = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    chg["STAT"] += r_stat[1]
    r_stat[2] += led_chg[2]   # anode
    led_chg[1] += gnd          # cathode

    # Input decoupling
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vbus
    c_in[2] += gnd

    # Battery bulk cap
    c_bat = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bat[1] += vbat
    c_bat[2] += gnd


# ============================================================
# Power path: USB/battery -> VSYS via Schottky OR'ing
# ============================================================
@subcircuit
def power_path(vbus, vbat, vsys, gnd):
    """Schottky diode OR of USB and battery into VSYS rail."""
    d_usb = Part("Device", "D_Schottky", value="MBR0530",
                 footprint="Diode_SMD:D_SOD-123")
    d_bat = Part("Device", "D_Schottky", value="MBR0530",
                 footprint="Diode_SMD:D_SOD-123")

    # Schottky: pin 1=K (cathode), pin 2=A (anode)
    d_usb[2] += vbus
    d_usb[1] += vsys

    d_bat[2] += vbat
    d_bat[1] += vsys

    # Bulk cap on VSYS
    c_sys = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_sys[1] += vsys
    c_sys[2] += gnd


# ============================================================
# 3.3V LDO regulator (AP2112K-3.3)
# ============================================================
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 LDO, 600mA, SOT-23-5. NC pin tied to GND."""
    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")

    reg["VIN"] += vin
    reg["GND"] += gnd
    reg["EN"] += vin      # always enabled
    reg["VOUT"] += vout
    reg["NC"] += gnd      # NC pin tied to GND

    # Input cap
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output caps (10uF + 100nF)
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vout
    c_out[2] += gnd

    c_out2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out2[1] += vout
    c_out2[2] += gnd

    # Power LED — green, shows 3.3V rail is live
    led_pwr = Part("Device", "LED", value="GREEN",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_pwr[1] += vout
    r_pwr[2] += led_pwr[2]   # anode
    led_pwr[1] += gnd          # cathode


# ============================================================
# ESP32-S2-WROVER module
# ============================================================
@subcircuit
def esp32_s2_module(vcc, gnd, sda, scl, usb_dp, usb_dm,
                    neopixel_out, rst_n, boot0,
                    feather_left, feather_right):
    """ESP32-S2-WROVER with decoupling, reset/boot pull-ups, I2C pull-ups."""
    global GND
    esp = Part("RF_Module", "ESP32-S2-WROVER",
               footprint="RF_Module:ESP32-S2-WROVER")

    # Power — tie all GND pads
    esp["3V3"] += vcc
    esp["GND"] += gnd   # connects all GND pads (1, 26, 42, 43) by name

    # Enable/Reset with RC filter
    esp["EN"] += rst_n
    r_en = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    c_en = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    r_en[1] += vcc
    r_en[2] += rst_n
    c_en[1] += rst_n
    c_en[2] += gnd

    # Boot/IO0 pull-up
    esp["IO00"] += boot0
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += vcc
    r_boot[2] += boot0

    # Native USB
    esp["USB_D+"] += usb_dp
    esp["USB_D-"] += usb_dm

    # I2C pull-ups 4.7K — IO01=SDA, IO02=SCL are wired via feather_left[12/13]
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc; r_sda[2] += sda
    r_scl[1] += vcc; r_scl[2] += scl

    # NeoPixel data via series resistor (IO33)
    r_neo = Part("Device", "R", value="330",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    esp["IO33"] += r_neo[1]
    r_neo[2] += neopixel_out

    # Module decoupling
    c_dec1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dec2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_dec1[1] += vcc; c_dec1[2] += gnd
    c_dec2[1] += vcc; c_dec2[2] += gnd

    # ---- Feather left header (16 pins): A0-A5, SCK, MOSI, MISO, RX, TX, D4, SDA, SCL, D5, D6 ----
    # feather_left[12] IS sda, feather_left[13] IS scl (same net objects)
    esp["IO17"] += feather_left[0]   # A0
    esp["IO18"] += feather_left[1]   # A1
    esp["IO14"] += feather_left[2]   # A2
    esp["IO13"] += feather_left[3]   # A3
    esp["IO12"] += feather_left[4]   # A4
    esp["IO11"] += feather_left[5]   # A5
    esp["IO36"] += feather_left[6]   # SCK
    esp["IO35"] += feather_left[7]   # MOSI
    esp["IO37"] += feather_left[8]   # MISO
    esp["RXD0"] += feather_left[9]   # RX
    esp["TXD0"] += feather_left[10]  # TX
    esp["IO21"] += feather_left[11]  # D4
    esp["IO01"] += feather_left[12]  # SDA
    esp["IO02"] += feather_left[13]  # SCL
    esp["IO05"] += feather_left[14]  # D5
    esp["IO06"] += feather_left[15]  # D6

    # ---- Feather right header (10 GPIO pins): D13..D9, A10, A6-A9 ----
    esp["IO42"] += feather_right[0]  # D13 / LED
    esp["IO41"] += feather_right[1]  # D12
    esp["IO40"] += feather_right[2]  # D11
    esp["IO39"] += feather_right[3]  # D10
    esp["IO38"] += feather_right[4]  # D9
    esp["IO26"] += feather_right[5]  # A10
    esp["IO10"] += feather_right[6]  # A6
    esp["IO09"] += feather_right[7]  # A7
    esp["IO08"] += feather_right[8]  # A8
    esp["IO07"] += feather_right[9]  # A9


# ============================================================
# NeoPixel (WS2812B)
# ============================================================
@subcircuit
def neopixel_led(vcc, gnd, data_in):
    """Single WS2812B NeoPixel status LED."""
    neo = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")

    neo["VDD"] += vcc
    neo["VSS"] += gnd
    neo["DIN"] += data_in
    # DOUT not connected — stub will handle it

    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += vcc
    c_neo[2] += gnd


# ============================================================
# STEMMA QT connector (JST SH 4-pin)
# ============================================================
@subcircuit
def stemma_qt(vcc, gnd, sda, scl):
    """STEMMA QT / Qwiic horizontal JST SH 4-pin connector."""
    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")

    # STEMMA QT standard: 1=GND, 2=VCC, 3=SDA, 4=SCL
    conn[1] += gnd
    conn[2] += vcc
    conn[3] += sda
    conn[4] += scl


# ============================================================
# Tactile buttons (Reset + Boot)
# ============================================================
@subcircuit
def buttons(gnd, rst_n, boot0):
    """Reset (active low EN) and BOOT (GPIO0) tactile buttons."""
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_rst[1] += rst_n
    sw_rst[2] += gnd

    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_boot[1] += boot0
    sw_boot[2] += gnd


# ============================================================
# JST PH battery connector (2-pin)
# ============================================================
@subcircuit
def battery_connector(vbat, gnd):
    """2-pin JST PH connector for LiPoly battery."""
    batt = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    batt[1] += vbat
    batt[2] += gnd


# ============================================================
# Feather wing headers (2x 1x16)
# ============================================================
@subcircuit
def feather_headers(vcc, vbat, vbus, gnd, rst_n,
                    left_pins, right_pins):
    """Feather-standard wing headers: 1x16 left, 1x12 right."""
    hdr_left = Part("Connector_Generic", "Conn_01x16",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")

    hdr_right = Part("Connector_Generic", "Conn_01x12",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")

    for i in range(16):
        hdr_left[i+1] += left_pins[i]

    hdr_right[1] += vbat   # BAT
    hdr_right[2] += rst_n  # EN/RST
    for i in range(10):
        hdr_right[i+3] += right_pins[i]


# ============================================================
# Board run options — schematic + placement preview only, skip routing
# ============================================================
run_options = type('run_options', (), {})()
run_options.pipeline_goal = "placement_review"

# ============================================================
# Board floorplan
# ============================================================
EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 100,
        "height_mm": 60,
        "corner_radius_mm": 1.0,
    },
    # ESP32-S2-WROVER module footprint is large — bounds span ~48.5x47mm
    # center at (50, 38) keeps it well inside 100x60mm outline
    "fixed_positions": [
        {"ref": "U3", "x_mm": 50.0, "y_mm": 38.0, "rotation_deg": 0},
    ],
    # USB-C and battery on the left edge; STEMMA QT on right; headers along top/bottom
    "edge_anchors": [
        {"ref": "J2", "edge": "left"},   # USB-C
        {"ref": "J3", "edge": "left"},   # JST-PH battery
        {"ref": "J1", "edge": "right"},  # STEMMA QT
        {"ref": "J4", "edge": "top"},    # left header 1x16
        {"ref": "J5", "edge": "bottom"}, # right header 1x12
    ],
}

# ============================================================
# Top-level instantiation
# ============================================================

# 1. USB-C connector
usb_c_connector(VBUS, GND, USB_DP, USB_DM)

# 2. Battery charger
battery_charger(VBUS, VBAT, GND)

# 3. Battery connector
battery_connector(VBAT, GND)

# 4. Power path (Schottky OR)
power_path(VBUS, VBAT, VSYS, GND)

# 5. 3.3V LDO regulator
voltage_regulator(VSYS, VCC, GND)

# 6. ESP32-S2-WROVER module
esp32_s2_module(VCC, GND, SDA, SCL, USB_DP, USB_DM,
                NEOPIXEL, RST_N, BOOT0,
                feather_left_pins, feather_right_pins)

# 7. NeoPixel
neopixel_led(VCC, GND, NEOPIXEL)

# 8. STEMMA QT connector
stemma_qt(VCC, GND, SDA, SCL)

# 9. Buttons
buttons(GND, RST_N, BOOT0)

# 10. Feather headers
feather_headers(VCC, VBAT, VBUS, GND, RST_N,
                feather_left_pins, feather_right_pins)

# ============================================================
# Generate schematic + PCB
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
