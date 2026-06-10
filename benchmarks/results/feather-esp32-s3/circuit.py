"""
Feather ESP32-S3 -- SKiDL circuit description
ESP32-S3 in Feather format with dual 240 MHz cores, WiFi/BLE, native USB,
8 MB flash, USB-C charging, LC709203 battery monitor, STEMMA QT connector,
and deep sleep support. Compatible with 50+ Feather Wings.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"
import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

def _init_skidl_pins(part):
    """Set default schematic attributes on SKIDL-defined part pins and
    synthesize draw_cmds so the schematic generator can compute bounding boxes.

    Library-loaded parts get orientation/x/y/length/rotation from the .kicad_sym
    file and draw_cmds from the symbol definition. Part(tool=SKIDL) pins lack
    these, causing NaN in the placement engine.
    """
    spacing_mm = 2.54
    pin_length_mm = 2.54
    n = len(part.pins)

    left_count = (n + 1) // 2
    right_count = n - left_count

    body_h = max(left_count, right_count, 1) * spacing_mm
    body_w = max(5.08, body_h * 0.6)

    draw_cmds = []
    for idx, pin in enumerate(part.pins):
        if idx < left_count:
            row = idx
            pin.x = -(body_w / 2 + pin_length_mm)
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "R"
            pin.rotation = 0
        else:
            row = idx - left_count
            pin.x = body_w / 2 + pin_length_mm
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "L"
            pin.rotation = 180

        pin.length = pin_length_mm

        pin_cmd = [
            "pin", pin.func if isinstance(pin.func, str) else "passive", "line",
            ["at", pin.x, pin.y, int(pin.rotation)],
            ["length", pin_length_mm],
            ["name", pin.name,
                ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num),
                ["effects", ["font", ["size", 1.27, 1.27]]]],
        ]
        draw_cmds.append(pin_cmd)

    rect_cmd = [
        "rectangle",
        ["start", -body_w / 2, -body_h / 2 - spacing_mm / 2],
        ["end", body_w / 2, body_h / 2 + spacing_mm / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]
    draw_cmds.append(rect_cmd)

    part.draw_cmds = {1: draw_cmds, 0: draw_cmds}

    if not hasattr(part, "lib") or part.lib is None:
        class _MockLib:
            def __init__(self, name):
                self.filename = name
        part.lib = _MockLib("skidl_custom")

# ---------------------------------------------------------------
# Global power nets
# ---------------------------------------------------------------
vbus = Net("VBUS"); vbus.drive = POWER
vbat = Net("VBAT"); vbat.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd  = Net("GND");  gnd.drive  = POWER

# Internal rails
usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")
sda    = Net("SDA")
scl    = Net("SCL")
en     = Net("EN")
neopixel_data = Net("NEOPIXEL")

# ---------------------------------------------------------------
# USB Input  (USB-C connector + ESD protection + CC resistors)
# ---------------------------------------------------------------
@subcircuit
def usb_input(vbus, gnd, dp, dm):
    """USB-C receptacle with CC pull-down resistors for UFP (device) role."""
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
               footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11")

    # VBUS pins
    usb["A4"]  += vbus
    usb["A9"]  += vbus
    usb["B4"]  += vbus
    usb["B9"]  += vbus

    # GND pins
    usb["A1"]  += gnd
    usb["A12"] += gnd
    usb["B1"]  += gnd
    usb["B12"] += gnd
    usb["S1"]  += gnd  # shield

    # Data lines
    usb["A6"]  += dp
    usb["B6"]  += dp
    usb["A7"]  += dm
    usb["B7"]  += dm

    # CC pull-downs (5.1k for UFP device detection)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["A5"]  # CC1
    r_cc1[2] += gnd
    r_cc2[1] += usb["B5"]  # CC2
    r_cc2[2] += gnd

    # SBU pins - not connected
    usb["A8"] += Net("SBU1_NC")
    usb["B8"] += Net("SBU2_NC")

usb_input(vbus, gnd, usb_dp, usb_dm)

# ---------------------------------------------------------------
# Battery Charger (MCP73831 LiPo charger)
# ---------------------------------------------------------------
@subcircuit
def battery_charger(vbus, vbat, gnd):
    """MCP73831 single-cell LiPo charger with charge status LED."""
    chrg = Part("Battery_Management", "MCP73831-2-OT",
                footprint="Package_TO_SOT_SMD:SOT-23-5")
    chrg["V_{DD}"]  += vbus
    chrg["V_{SS}"]  += gnd
    chrg["V_{BAT}"] += vbat

    # Program charging current: 500mA -> R = 1000/I = 2K
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_prog[1] += chrg["PROG"]
    r_prog[2] += gnd

    # Charge status LED (active low)
    chrg_led = Part("Device", "LED", value="ORANGE",
                    footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_led[1] += vbus
    r_led[2] += chrg_led[1]
    chrg_led[2] += chrg["STAT"]

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

battery_charger(vbus, vbat, gnd)

# ---------------------------------------------------------------
# Battery connector (JST PH 2-pin)
# ---------------------------------------------------------------
@subcircuit
def battery_connector(vbat, gnd):
    """JST PH 2-pin connector for LiPo battery."""
    jst = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    jst[1] += vbat
    jst[2] += gnd

battery_connector(vbat, gnd)

# ---------------------------------------------------------------
# Battery Monitor (LC709203F)
# ---------------------------------------------------------------
@subcircuit
def battery_monitor(vbat, gnd, sda, scl):
    """LC709203F battery fuel gauge -- I2C voltage and SoC reporting."""
    bm = Part("Battery_Management", "LC709203FQH-01TWG",
              footprint="Package_DFN_QFN:DFN-8-1EP_2x2mm_P0.5mm_EP0.9x1.6mm")
    bm["V_{DD}"]  += vbat
    bm["V_{SS}"]  += gnd
    bm["EP"]      += gnd
    bm["SDA"]     += sda
    bm["SCL"]     += scl

    # TEST pin - to GND per datasheet
    bm["TEST"]    += gnd

    # Thermistor pins: TSW to VBAT, TSENSE to thermistor divider
    bm["T_{SW}"]    += vbat
    # NTC thermistor (10K) for temperature sensing
    r_ntc = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_ntc[1] += bm["T_{SENSE}"]
    r_ntc[2] += gnd

    # Alarm pin -- open drain, pull up to VBAT
    r_alarm = Part("Device", "R", value="100K",
                   footprint="Resistor_SMD:R_0402_1005Metric")
    r_alarm[1] += vbat
    r_alarm[2] += bm["~{ALARMB}"]

    # Decoupling
    c_bm = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_bm[1] += vbat
    c_bm[2] += gnd

battery_monitor(vbat, gnd, sda, scl)

# ---------------------------------------------------------------
# 3.3V Regulator (AP2112K-3.3)
# ---------------------------------------------------------------
@subcircuit
def power_regulator(vbat, v3v3, gnd, en_net):
    """AP2112K-3.3 LDO -- 600mA, low quiescent current."""
    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"]  += vbat
    reg["GND"]  += gnd
    reg["EN"]   += en_net
    reg["VOUT"] += v3v3

    # Enable pull-up to keep regulator on by default
    r_en = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_en[1] += vbat
    r_en[2] += en_net

    # Input capacitor
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vbat
    c_in[2] += gnd

    # Output capacitor
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += v3v3
    c_out[2] += gnd

    # Decoupling on output
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dec[1] += v3v3
    c_dec[2] += gnd

power_regulator(vbat, v3v3, gnd, en)

# ---------------------------------------------------------------
# ESP32-S3 MCU (using SKIDL tool for custom pin mapping)
# ---------------------------------------------------------------
@subcircuit
def esp32_s3_mcu(v3v3, gnd, dp, dm, sda_net, scl_net, en_net, neopixel):
    """ESP32-S3-WROOM-1 module with native USB and extensive GPIO."""
    esp = Part(name="ESP32-S3-WROOM-1", tool=SKIDL, dest=NETLIST,
               footprint="RF_Module:ESP32-S3-WROOM-1",
               pins=[
                   Pin(num="1",  name="GND",    func=Pin.types.PWRIN),
                   Pin(num="2",  name="3V3",    func=Pin.types.PWRIN),
                   Pin(num="3",  name="EN",     func=Pin.types.INPUT),
                   Pin(num="4",  name="IO4",    func=Pin.types.BIDIR),
                   Pin(num="5",  name="IO5",    func=Pin.types.BIDIR),
                   Pin(num="6",  name="IO6",    func=Pin.types.BIDIR),
                   Pin(num="7",  name="IO7",    func=Pin.types.BIDIR),
                   Pin(num="8",  name="IO15",   func=Pin.types.BIDIR),
                   Pin(num="9",  name="IO16",   func=Pin.types.BIDIR),
                   Pin(num="10", name="IO17",   func=Pin.types.BIDIR),
                   Pin(num="11", name="IO18",   func=Pin.types.BIDIR),
                   Pin(num="12", name="IO8",    func=Pin.types.BIDIR),
                   Pin(num="13", name="IO19",   func=Pin.types.BIDIR),  # USB_D-
                   Pin(num="14", name="IO20",   func=Pin.types.BIDIR),  # USB_D+
                   Pin(num="15", name="IO3",    func=Pin.types.BIDIR),
                   Pin(num="16", name="IO46",   func=Pin.types.BIDIR),
                   Pin(num="17", name="IO9",    func=Pin.types.BIDIR),
                   Pin(num="18", name="IO10",   func=Pin.types.BIDIR),
                   Pin(num="19", name="IO11",   func=Pin.types.BIDIR),
                   Pin(num="20", name="IO12",   func=Pin.types.BIDIR),
                   Pin(num="21", name="IO13",   func=Pin.types.BIDIR),
                   Pin(num="22", name="IO14",   func=Pin.types.BIDIR),
                   Pin(num="23", name="IO21",   func=Pin.types.BIDIR),
                   Pin(num="24", name="IO47",   func=Pin.types.BIDIR),
                   Pin(num="25", name="IO48",   func=Pin.types.BIDIR),
                   Pin(num="26", name="IO45",   func=Pin.types.BIDIR),
                   Pin(num="27", name="IO0",    func=Pin.types.BIDIR),
                   Pin(num="28", name="IO35",   func=Pin.types.BIDIR),
                   Pin(num="29", name="IO36",   func=Pin.types.BIDIR),
                   Pin(num="30", name="IO37",   func=Pin.types.BIDIR),
                   Pin(num="31", name="IO38",   func=Pin.types.BIDIR),
                   Pin(num="32", name="IO39",   func=Pin.types.BIDIR),
                   Pin(num="33", name="IO40",   func=Pin.types.BIDIR),
                   Pin(num="34", name="IO41",   func=Pin.types.BIDIR),
                   Pin(num="35", name="IO42",   func=Pin.types.BIDIR),
                   Pin(num="36", name="RXD0",   func=Pin.types.INPUT),
                   Pin(num="37", name="TXD0",   func=Pin.types.OUTPUT),
                   Pin(num="38", name="IO2",    func=Pin.types.BIDIR),
                   Pin(num="39", name="IO1",    func=Pin.types.BIDIR),
                   Pin(num="40", name="GND2",   func=Pin.types.PWRIN),
                   Pin(num="41", name="EPAD",   func=Pin.types.PWRIN),
               ])

    # Power
    esp["3V3"]  += v3v3
    esp["GND"]  += gnd
    esp["GND2"] += gnd
    esp["EPAD"] += gnd

    # Enable (with RC delay for reliable boot)
    esp["EN"]   += en_net
    c_en = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_en[1] += en_net
    c_en[2] += gnd

    # Native USB
    esp["IO19"] += dm   # USB D-
    esp["IO20"] += dp   # USB D+

    # I2C (for battery monitor + STEMMA QT)
    esp["IO3"]  += sda_net
    esp["IO4"]  += scl_net

    # I2C pull-ups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v3v3; r_sda[2] += sda_net
    r_scl[1] += v3v3; r_scl[2] += scl_net

    # NeoPixel data on IO38
    esp["IO38"] += neopixel

    # Boot mode (IO0): pull-up for normal boot
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += v3v3
    r_boot[2] += esp["IO0"]

    # Decoupling capacitors
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += v3v3; c1[2] += gnd

    c2 = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    c2[1] += v3v3; c2[2] += gnd

esp32_s3_mcu(v3v3, gnd, usb_dp, usb_dm, sda, scl, en, neopixel_data)

# ---------------------------------------------------------------
# NeoPixel Status LED (WS2812B)
# ---------------------------------------------------------------
@subcircuit
def neopixel_led(v3v3, gnd, data_in):
    """Single WS2812B NeoPixel for status indication."""
    neo = Part(name="WS2812B", tool=SKIDL, dest=NETLIST,
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
               pins=[
                   Pin(num="1", name="VDD",  func=Pin.types.PWRIN),
                   Pin(num="2", name="DOUT", func=Pin.types.OUTPUT),
                   Pin(num="3", name="VSS",  func=Pin.types.PWRIN),
                   Pin(num="4", name="DIN",  func=Pin.types.INPUT),
               ])
    neo["VDD"] += v3v3
    neo["VSS"] += gnd
    neo["DIN"] += data_in
    neo["DOUT"] += Net("NEO_DOUT_NC")  # no chain, single LED

    # Bypass capacitor close to NeoPixel
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += v3v3
    c_neo[2] += gnd

neopixel_led(v3v3, gnd, neopixel_data)

# ---------------------------------------------------------------
# STEMMA QT / Qwiic I2C Connector (with switchable power)
# ---------------------------------------------------------------
@subcircuit
def stemma_qt(v3v3, gnd, sda_net, scl_net):
    """STEMMA QT (JST SH 4-pin) I2C connector with switchable power via P-MOSFET."""
    qt = Part("Connector_Generic", "Conn_01x04",
              footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")

    # STEMMA QT pinout: GND, VCC, SDA, SCL
    qt[1] += gnd
    qt[3] += sda_net
    qt[4] += scl_net

    # Switchable 3.3V power via P-channel MOSFET for low-power mode
    qt_pwr = Net("QT_3V3")
    pfet = Part("Transistor_FET", "AO3401A",
                footprint="Package_TO_SOT_SMD:SOT-23")
    pfet["S"] += v3v3       # source to 3.3V supply
    pfet["D"] += qt_pwr     # drain to STEMMA QT VCC
    qt[2] += qt_pwr

    # Gate control (active low enable, pull low to enable by default)
    qt_en = Net("QT_EN")
    pfet["G"] += qt_en
    r_gate = Part("Device", "R", value="100K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_gate[1] += qt_en
    r_gate[2] += gnd   # pull low = ON by default

stemma_qt(v3v3, gnd, sda, scl)

# ---------------------------------------------------------------
# Reset Button
# ---------------------------------------------------------------
@subcircuit
def reset_button(en_net, gnd):
    """Tactile push button for reset (active low)."""
    sw = Part(name="SW_RESET", tool=SKIDL, dest=NETLIST,
              footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
              pins=[
                  Pin(num="1", name="A", func=Pin.types.PASSIVE),
                  Pin(num="2", name="B", func=Pin.types.PASSIVE),
              ])
    sw["A"] += en_net
    sw["B"] += gnd

reset_button(en, gnd)

# ---------------------------------------------------------------
# User LED
# ---------------------------------------------------------------
@subcircuit
def user_led(v3v3, gnd):
    """General-purpose user LED on IO13."""
    led = Part("Device", "LED", value="RED",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led_net = Net("LED_IO13")
    r_led[1] += led_net
    r_led[2] += led[1]
    led[2] += gnd

user_led(v3v3, gnd)

# ---------------------------------------------------------------
# Feather Headers (1x16 + 1x12)
# ---------------------------------------------------------------
@subcircuit
def feather_headers(vbus, vbat, v3v3, gnd, en_net, sda_net, scl_net):
    """Standard Feather header pinout for Wing compatibility."""
    # Left header (16 pins)
    hdr_l = Part("Connector_Generic", "Conn_01x16",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    hdr_l[1]  += Net("RST_HDR")
    hdr_l[2]  += v3v3
    hdr_l[3]  += Net("AREF")
    hdr_l[4]  += gnd
    hdr_l[5]  += Net("A0")
    hdr_l[6]  += Net("A1")
    hdr_l[7]  += Net("A2")
    hdr_l[8]  += Net("A3")
    hdr_l[9]  += Net("A4")
    hdr_l[10] += Net("A5")
    hdr_l[11] += scl_net
    hdr_l[12] += sda_net
    hdr_l[13] += Net("GPIO_D13")
    hdr_l[14] += Net("GPIO_D12")
    hdr_l[15] += Net("GPIO_D11")
    hdr_l[16] += Net("GPIO_D10")

    # Right header (12 pins)
    hdr_r = Part("Connector_Generic", "Conn_01x12",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    hdr_r[1]  += vbat
    hdr_r[2]  += en_net
    hdr_r[3]  += vbus
    hdr_r[4]  += Net("GPIO_D5")
    hdr_r[5]  += Net("GPIO_D6")
    hdr_r[6]  += Net("GPIO_D9")
    hdr_r[7]  += Net("GPIO_D10_R")
    hdr_r[8]  += Net("GPIO_D11_R")
    hdr_r[9]  += Net("GPIO_D12_R")
    hdr_r[10] += Net("GPIO_D13_R")
    hdr_r[11] += Net("TX")
    hdr_r[12] += Net("RX")

feather_headers(vbus, vbat, v3v3, gnd, en, sda, scl)

# ---------------------------------------------------------------
# SPI Flash (8 MB external flash -- W25Q64)
# ---------------------------------------------------------------
@subcircuit
def spi_flash(v3v3, gnd):
    """W25Q64 8MB SPI flash for CircuitPython filesystem / OTA."""
    flash = Part(name="W25Q64JV", tool=SKIDL, dest=NETLIST,
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 pins=[
                     Pin(num="1", name="CS",   func=Pin.types.INPUT),
                     Pin(num="2", name="DO",   func=Pin.types.OUTPUT),
                     Pin(num="3", name="WP",   func=Pin.types.INPUT),
                     Pin(num="4", name="GND",  func=Pin.types.PWRIN),
                     Pin(num="5", name="DI",   func=Pin.types.INPUT),
                     Pin(num="6", name="CLK",  func=Pin.types.INPUT),
                     Pin(num="7", name="HOLD", func=Pin.types.INPUT),
                     Pin(num="8", name="VCC",  func=Pin.types.PWRIN),
                 ])
    flash["VCC"]  += v3v3
    flash["GND"]  += gnd

    # SPI connections
    flash_cs  = Net("FLASH_CS")
    flash_clk = Net("FLASH_CLK")
    flash_di  = Net("FLASH_MOSI")
    flash_do  = Net("FLASH_MISO")

    flash["CS"]   += flash_cs
    flash["CLK"]  += flash_clk
    flash["DI"]   += flash_di
    flash["DO"]   += flash_do

    # WP and HOLD pulled high (inactive)
    r_wp = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_hold = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_wp[1]   += v3v3; r_wp[2]   += flash["WP"]
    r_hold[1] += v3v3; r_hold[2] += flash["HOLD"]

    # Decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += v3v3
    c_flash[2] += gnd

spi_flash(v3v3, gnd)

# ---------------------------------------------------------------
# 32.768 kHz Crystal for RTC
# ---------------------------------------------------------------
@subcircuit
def rtc_crystal(v3v3, gnd):
    """32.768 kHz crystal for ESP32-S3 internal RTC."""
    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal_p = Net("XTAL32K_P")
    xtal_n = Net("XTAL32K_N")
    xtal[1] += xtal_p
    xtal[2] += xtal_n

    # Load capacitors
    c1 = Part("Device", "C", value="6.8pF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2 = Part("Device", "C", value="6.8pF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += xtal_p; c1[2] += gnd
    c2[1] += xtal_n; c2[2] += gnd

rtc_crystal(v3v3, gnd)

# ---------------------------------------------------------------
# Initialize SKIDL-tool parts for schematic generation
# ---------------------------------------------------------------
for p in default_circuit.parts:
    if getattr(p, "tool", None) == SKIDL:
        _init_skidl_pins(p)

# ---------------------------------------------------------------
# Generate schematic
# ---------------------------------------------------------------
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
