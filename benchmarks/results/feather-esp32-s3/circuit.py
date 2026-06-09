"""
Feather ESP32-S3 — Adafruit-compatible Feather format board
ESP32-S3 module with WiFi/BLE, USB-C, LiPo charging, battery monitor,
STEMMA QT connector, NeoPixel, and full Feather Wing header compatibility.
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
VSYS = Net("VSYS")          # Post-Schottky USB or battery power
SDA = Net("SDA")
SCL = Net("SCL")
NEOPIXEL = Net("NEOPIXEL")
CHG_LED = Net("CHG_LED")
PWR_LED = Net("PWR_LED")
STEMMA_EN = Net("STEMMA_EN")
STEMMA_VCC = Net("STEMMA_VCC")
BOOT0 = Net("BOOT0")
RST_N = Net("RST_N")

# ============================================================
# USB-C Connector subcircuit
# ============================================================
@subcircuit
def usb_c_connector(vbus, gnd, dp, dm):
    """USB-C receptacle with CC resistors for device mode."""
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
               footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11")

    # Power pins (multiple VBUS and GND pins — connect all)
    usb["VBUS"] += vbus
    usb["GND"] += gnd
    usb["SHIELD"] += gnd

    # Data pins — USB 2.0 so D+/D- on both orientations
    usb["D+"] += dp
    usb["D-"] += dm

    # CC pull-downs for UFP (device) — 5.1k to GND
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    usb["CC1"] += r_cc1[1]
    r_cc1[2] += gnd
    usb["CC2"] += r_cc2[1]
    r_cc2[2] += gnd

    # SBU pins — not used, leave floating (NC internally)

    # ESD protection capacitors on VBUS
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd


# ============================================================
# Battery charging subcircuit (MCP73831)
# ============================================================
@subcircuit
def battery_charger(vbus, vbat, gnd, chg_status):
    """MCP73831-based single-cell LiPo charger."""
    # MCP73831-2-OT: pins 1=STAT, 2=VSS, 3=VBAT, 4=VDD, 5=PROG
    chg = Part("Battery_Management", "MCP73831-2-OT",
               footprint="Package_TO_SOT_SMD:SOT-23-5")

    chg["V_{DD}"] += vbus
    chg["V_{SS}"] += gnd
    chg["V_{BAT}"] += vbat

    # PROG resistor sets charge current: 2K = 500mA
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    chg["PROG"] += r_prog[1]
    r_prog[2] += gnd

    # Charge status LED (active low)
    r_stat = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    chg["STAT"] += r_stat[1]
    r_stat[2] += led_chg[2]   # LED anode
    led_chg[1] += gnd         # LED cathode to GND (STAT pulls low)

    # Input decoupling
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vbus
    c_in[2] += gnd

    # Battery decoupling (10uF tantalum/ceramic)
    c_bat = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bat[1] += vbat
    c_bat[2] += gnd


# ============================================================
# Power path: USB/battery → VSYS via Schottky OR'ing
# ============================================================
@subcircuit
def power_path(vbus, vbat, vsys, gnd):
    """Schottky diode OR of USB and battery into VSYS."""
    d_usb = Part("Device", "D_Schottky", value="MBR0530",
                 footprint="Diode_SMD:D_SOD-123")
    d_bat = Part("Device", "D_Schottky", value="MBR0530",
                 footprint="Diode_SMD:D_SOD-123")

    # Diode: pin 1=K (cathode), pin 2=A (anode)
    # VBUS → anode, VSYS ← cathode
    d_usb[2] += vbus
    d_usb[1] += vsys

    # VBAT → anode, VSYS ← cathode
    d_bat[2] += vbat
    d_bat[1] += vsys

    # Bulk capacitor on VSYS
    c_sys = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_sys[1] += vsys
    c_sys[2] += gnd


# ============================================================
# 3.3V LDO regulator (AP2112K-3.3)
# ============================================================
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 low-dropout 3.3V regulator."""
    # AP2112K-3.3: 1=VIN, 2=GND, 3=EN, 4=NC, 5=VOUT
    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")

    reg["VIN"] += vin
    reg["GND"] += gnd
    reg["EN"] += vin     # Always enabled (tied to input)
    reg["VOUT"] += vout

    # Input cap
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output cap (10uF recommended)
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vout
    c_out[2] += gnd

    # Additional 100nF output decoupling
    c_out2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out2[1] += vout
    c_out2[2] += gnd


# ============================================================
# LC709203F battery fuel gauge (SKIDL part — not in KiCad lib)
# ============================================================
@subcircuit
def battery_monitor(vbat, vcc, gnd, sda, scl):
    """LC709203F I2C battery fuel gauge."""
    # LC709203F battery fuel gauge in SOIC-8 package.
    # Using Conn_01x08 as proxy since KiCad doesn't have LC709203F symbol,
    # and tool=SKIDL parts lack draw_cmds needed by the schematic placer.
    # Pin mapping: 1=SDA, 2=ALARM, 3=NC, 4=GND, 5=TSENSE, 6=NC, 7=VCC, 8=SCL
    bm = Part("Connector_Generic", "Conn_01x08",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
              ref_prefix="U")

    bm.value = "LC709203F"
    bm[7] += vbat    # Pin 7 = VCC, powered from battery
    bm[4] += gnd     # Pin 4 = GND
    bm[1] += sda     # Pin 1 = SDA
    bm[8] += scl     # Pin 8 = SCL
    bm[2] += Net("BATT_ALARM")  # Pin 2 = ALARM (open-drain, unused)
    bm[3] += NC      # Pin 3 = NC
    bm[6] += NC      # Pin 6 = NC

    # Thermistor input — pull to GND with 10K (no external thermistor)
    r_therm = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0402_1005Metric")
    bm[5] += r_therm[1]   # Pin 5 = TSENSE
    r_therm[2] += gnd

    # Decoupling
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dec[1] += vbat
    c_dec[2] += gnd

    # I2C pull-ups (4.7K to 3.3V)
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc
    r_sda[2] += sda
    r_scl[1] += vcc
    r_scl[2] += scl


# ============================================================
# STEMMA QT connector with switchable power
# ============================================================
@subcircuit
def stemma_qt(vcc, gnd, sda, scl, en_pin):
    """STEMMA QT / Qwiic I2C connector with power switching for low power."""
    # JST SH 4-pin connector: GND, VCC, SDA, SCL
    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")

    # P-channel MOSFET power switch (AO3401A): G=1, S=2, D=3
    pfet = Part("Transistor_FET", "AO3401A",
                footprint="Package_TO_SOT_SMD:SOT-23")

    # Gate driven by ESP32 GPIO (active low — pull low to enable power)
    pfet["G"] += en_pin
    pfet["S"] += vcc         # Source = 3.3V rail
    pfet["D"] += Net("STEMMA_VCC_SW")   # Drain = switched STEMMA power

    # Gate pull-up to keep STEMMA off by default (deep sleep)
    r_gate = Part("Device", "R", value="100K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_gate[1] += vcc
    r_gate[2] += en_pin

    # Connector wiring: pin 1=GND, 2=VCC, 3=SDA, 4=SCL (STEMMA QT standard)
    conn[1] += gnd
    conn[2] += pfet["D"]     # Switched power
    conn[3] += sda
    conn[4] += scl

    # Bypass cap on switched rail
    c_stemma = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_stemma[1] += pfet["D"]
    c_stemma[2] += gnd


# ============================================================
# NeoPixel (WS2812B) subcircuit
# ============================================================
@subcircuit
def neopixel(vcc, gnd, data_in):
    """Single WS2812B NeoPixel for status indication."""
    neo = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")

    neo["VDD"] += vcc
    neo["VSS"] += gnd
    neo["DIN"] += data_in

    # Decoupling cap right at the LED
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += vcc
    c_neo[2] += gnd

    # Data series resistor (between MCU and DIN is best practice)
    # Already handled by the main data_in connection; the resistor is in main


# ============================================================
# ESP32-S3 module subcircuit
# ============================================================
@subcircuit
def esp32_s3_module(vcc, gnd, sda, scl, usb_dp, usb_dm,
                    neopixel_out, stemma_en, rst_n, boot0,
                    feather_left, feather_right):
    """ESP32-S3-WROOM-1 module with supporting passives."""
    esp = Part("RF_Module", "ESP32-S3-WROOM-1",
               footprint="RF_Module:ESP32-S3-WROOM-1")

    # Power
    esp["3V3"] += vcc
    esp["GND"] += gnd

    # Enable / Reset — active high with RC filter
    esp["EN"] += rst_n
    r_en = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    c_en = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    r_en[1] += vcc
    r_en[2] += rst_n
    c_en[1] += rst_n
    c_en[2] += gnd

    # Boot mode — IO0 active low enters bootloader
    esp["IO0"] += boot0
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += vcc
    r_boot[2] += boot0

    # Native USB
    esp["USB_D+"] += usb_dp
    esp["USB_D-"] += usb_dm

    # I2C
    esp["IO3"] += sda      # IO3 = default SDA on Feather ESP32-S3
    esp["IO4"] += scl      # IO4 = default SCL on Feather ESP32-S3

    # NeoPixel data output via series resistor
    # IO15 used for NeoPixel (internal to board, not on headers)
    r_neo = Part("Device", "R", value="330",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    esp["IO15"] += r_neo[1]
    r_neo[2] += neopixel_out

    # STEMMA QT enable GPIO (IO7 — internal, not on headers)
    esp["IO7"] += stemma_en

    # NeoPixel power control (IO46 — internal)
    # IO46 controls a FET for NeoPixel power (for deep sleep)
    esp["IO46"] += Net("NEO_PWR_EN")

    # Decoupling caps for the module
    c_dec1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dec2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_dec1[1] += vcc; c_dec1[2] += gnd
    c_dec2[1] += vcc; c_dec2[2] += gnd

    # ---- Feather Wing header pin assignments ----
    # Available WROOM-1 pins: IO0-IO14, IO15-IO18, IO21, IO35-IO42,
    # IO45-IO48, RXD0, TXD0. Used internally: IO0(boot), IO3(SDA),
    # IO4(SCL), IO7(STEMMA), IO15(NeoPixel), IO46(NeoPixel pwr)
    #
    # Left header (1x16): A0-A5, SCK, MOSI, MISO, RX, TX, D4, SDA, SCL, D5, D6
    esp["IO18"] += feather_left[0]   # A0
    esp["IO17"] += feather_left[1]   # A1
    esp["IO14"] += feather_left[2]   # A2
    esp["IO13"] += feather_left[3]   # A3
    esp["IO12"] += feather_left[4]   # A4
    esp["IO11"] += feather_left[5]   # A5
    esp["IO36"] += feather_left[6]   # SCK
    esp["IO35"] += feather_left[7]   # MOSI
    esp["IO37"] += feather_left[8]   # MISO
    esp["RXD0"] += feather_left[9]   # RX
    esp["TXD0"] += feather_left[10]  # TX
    esp["IO21"] += feather_left[11]  # D4/GPIO21
    esp["IO3"] += feather_left[12]   # SDA (shared with I2C bus)
    esp["IO4"] += feather_left[13]   # SCL (shared with I2C bus)
    esp["IO5"] += feather_left[14]   # D5
    esp["IO6"] += feather_left[15]   # D6

    # Right header: 10 GPIO pins (BAT and RST handled in feather_headers)
    esp["IO42"] += feather_right[0]  # D13 (LED)
    esp["IO41"] += feather_right[1]  # D12
    esp["IO40"] += feather_right[2]  # D11
    esp["IO39"] += feather_right[3]  # D10
    esp["IO38"] += feather_right[4]  # D9
    esp["IO48"] += feather_right[5]  # D25/A6
    esp["IO47"] += feather_right[6]  # D24/A7
    esp["IO8"]  += feather_right[7]  # A8
    esp["IO9"]  += feather_right[8]  # D24
    esp["IO10"] += feather_right[9]  # D25


# ============================================================
# User interface: buttons and power LED
# ============================================================
@subcircuit
def buttons_and_leds(vcc, gnd, rst_n, boot0, pwr_led_net):
    """Reset button, boot button, power LED."""
    # Reset button — pulls EN low
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_rst[1] += rst_n
    sw_rst[2] += gnd

    # Boot / DFU button — pulls IO0 low
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_boot[1] += boot0
    sw_boot[2] += gnd

    # Power LED (green, from 3.3V)
    led_pwr = Part("Device", "LED", value="GREEN",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_pwr[1] += vcc
    r_pwr[2] += led_pwr[2]   # Anode
    led_pwr[1] += gnd        # Cathode


# ============================================================
# Feather headers
# ============================================================
@subcircuit
def feather_headers(vcc, vbat, vbus, gnd, rst_n,
                    left_pins, right_pins):
    """Feather-standard pin headers — 1x16 left, 1x12 right."""
    # Left header: 16 pins
    hdr_left = Part("Connector_Generic", "Conn_01x16",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")

    # Right header: 12 pins
    hdr_right = Part("Connector_Generic", "Conn_01x12",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")

    # Connect left header GPIOs (16 pins, 0-indexed list)
    for i in range(16):
        hdr_left[i+1] += left_pins[i]

    # Right header — first 2 are power/special, then 10 GPIOs
    hdr_right[1] += vbat    # BAT (pin 1)
    hdr_right[2] += rst_n   # EN/RST (pin 2)
    # Pins 3-12: GPIOs (0-indexed list → right_pins[0..9])
    for i in range(10):
        hdr_right[i+3] += right_pins[i]


# ============================================================
# JST PH battery connector
# ============================================================
@subcircuit
def battery_connector(vbat, gnd):
    """2-pin JST PH connector for LiPoly battery."""
    batt = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    batt[1] += vbat
    batt[2] += gnd


# ============================================================
# Top-level instantiation
# ============================================================

# Internal signal nets for data
USB_DP = Net("USB_DP")
USB_DM = Net("USB_DM")

# Feather wing pin buses (directly connected nets)
feather_left_pins = [Net(f"FL{i}") for i in range(16)]
feather_right_pins = [Net(f"FR{i}") for i in range(10)]

# 1. USB-C connector
usb_c_connector(VBUS, GND, USB_DP, USB_DM)

# 2. Battery charger
battery_charger(VBUS, VBAT, GND, CHG_LED)

# 3. Battery connector
battery_connector(VBAT, GND)

# 4. Power path (Schottky OR)
power_path(VBUS, VBAT, VSYS, GND)

# 5. 3.3V regulator
voltage_regulator(VSYS, VCC, GND)

# 6. Battery fuel gauge
battery_monitor(VBAT, VCC, GND, SDA, SCL)

# 7. ESP32-S3 module
esp32_s3_module(VCC, GND, SDA, SCL, USB_DP, USB_DM,
                NEOPIXEL, STEMMA_EN, RST_N, BOOT0,
                feather_left_pins, feather_right_pins)

# 8. STEMMA QT connector
stemma_qt(VCC, GND, SDA, SCL, STEMMA_EN)

# 9. NeoPixel
neopixel(VCC, GND, NEOPIXEL)

# 10. Buttons and LEDs
buttons_and_leds(VCC, GND, RST_N, BOOT0, PWR_LED)

# 11. Feather headers
feather_headers(VCC, VBAT, VBUS, GND, RST_N,
                feather_left_pins, feather_right_pins)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
