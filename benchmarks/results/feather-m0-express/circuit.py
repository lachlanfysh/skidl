"""
Adafruit Feather M0 Express
ATSAMD21G18A ARM Cortex M0+ 48MHz, 3.3V.
256KB flash, 32KB RAM. USB bootloader (UF2).
2MB SPI flash (GD25Q16) for CircuitPython.
NeoPixel WS2812B on PA06. MCP73831 LiPo charger.
AP2112K-3.3 LDO. Schottky diode USB/battery OR.
Feather form factor ~51x23mm, 2x 1x16 + 1x12 headers.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

EDA_FLOORPLAN = {
    "board_size_mm": [51, 23],
    "edge_anchors": [
        {"ref": "J_USB",  "edge": "left"},
        {"ref": "J_BAT",  "edge": "bottom"},
        {"ref": "J_SWD",  "edge": "right"},
    ],
}

# ==== Power nets ====
vbus = Net("VBUS")
vbat = Net("VBAT"); vbat.drive = POWER
vcc  = Net("VCC");  vcc.drive  = POWER   # USB/battery OR output
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd  = Net("GND");  gnd.drive  = POWER

# ==== Signal nets ====
usb_dp  = Net("USB_DP")
usb_dm  = Net("USB_DM")
xin     = Net("XIN32")
xout    = Net("XOUT32")
sda     = Net("SDA")
scl     = Net("SCL")
mosi    = Net("MOSI")
miso    = Net("MISO")
sck     = Net("SCK")
reset_n = Net("~{RESET}")
swdio   = Net("SWDIO")
swclk   = Net("SWCLK")
bat_div = Net("BAT_DIV")
flash_cs = Net("FLASH_CS")
neo_data = Net("NEOPIXEL")
chg_stat = Net("CHG_STAT")

# Feather header nets
a0 = Net("A0"); a1 = Net("A1"); a2 = Net("A2")
a3 = Net("A3"); a4 = Net("A4"); a5 = Net("A5")
d5  = Net("D5");  d6  = Net("D6");  d9  = Net("D9")
d10 = Net("D10"); d11 = Net("D11"); d12 = Net("D12")
d13 = Net("D13"); tx  = Net("TX");  rx  = Net("RX")


# ==============================================================
# USB Micro-B connector
# ==============================================================
@subcircuit
def usb_input():
    global vbus, gnd, usb_dp, usb_dm

    usb = Part("Connector", "USB_B_Micro", value="USB_Micro_B",
               footprint="Connector_USB:USB_Micro-B_Molex_47346-0001", ref="J_USB")
    usb.edge_preference = "left"
    vbus      += usb["VBUS"]
    gnd       += usb["GND"], usb["Shield"]
    usb_dp    += usb["D+"]
    usb_dm    += usb["D-"]
    gnd       += usb["ID"]

    c = Part("Device", "C", value="10uF",
             footprint="Capacitor_SMD:C_0805_2012Metric", ref="C_VBUS")
    vbus += c[1]; gnd += c[2]


# ==============================================================
# LiPo JST-PH 2-pin battery connector
# ==============================================================
@subcircuit
def lipo_connector():
    global vbat, gnd

    j = Part("Connector_Generic", "Conn_01x02", value="JST-PH-2",
             footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
             ref="J_BAT")
    j.edge_preference = "bottom"
    vbat += j[1]; gnd += j[2]


# ==============================================================
# MCP73831 LiPo charger (SOT-23-5)
# ==============================================================
@subcircuit
def lipo_charger():
    global vbus, vbat, gnd, chg_stat

    chg = Part("Battery_Management", "MCP73831-2-OT", value="MCP73831",
               footprint="Package_TO_SOT_SMD:SOT-23-5", ref="U_CHG")
    vbus     += chg["V_{DD}"]
    vbat     += chg["V_{BAT}"]
    gnd      += chg["V_{SS}"]
    chg_stat += chg["STAT"]

    # 2kΩ PROG sets ~500mA charge current
    r = Part("Device", "R", value="2k",
             footprint="Resistor_SMD:R_0603_1608Metric", ref="R_PROG")
    r[1] += chg["PROG"]; r[2] += gnd

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric", ref="C_CHG")
    vbus += c[1]; gnd += c[2]

    cbat = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric", ref="C_VBAT")
    vbat += cbat[1]; gnd += cbat[2]

    # CHG status LED
    rled = Part("Device", "R", value="1k",
                footprint="Resistor_SMD:R_0603_1608Metric", ref="R_CHG_LED")
    led = Part("Device", "LED", value="CHG_Orange",
               footprint="LED_SMD:LED_0603_1608Metric", ref="LED_CHG")
    vbus     += rled[1]
    rled[2]  += led["A"]
    led["K"] += chg_stat


# ==============================================================
# Power switching (Schottky OR) + AP2112K-3.3 LDO
# ==============================================================
@subcircuit
def power_supply():
    global vbus, vbat, vcc, v3v3, gnd

    d_usb = Part("Diode", "MBR0520", value="MBR0520",
                 footprint="Diode_SMD:D_SOD-123", ref="D_USB")
    d_usb["A"] += vbus; d_usb["K"] += vcc

    d_bat = Part("Diode", "MBR0520", value="MBR0520",
                 footprint="Diode_SMD:D_SOD-123", ref="D_BAT")
    d_bat["A"] += vbat; d_bat["K"] += vcc

    reg = Part("Regulator_Linear", "AP2112K-3.3", value="AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5", ref="U_LDO")
    reg["VIN"]  += vcc
    reg["GND"]  += gnd
    reg["EN"]   += vcc
    reg["VOUT"] += v3v3
    reg["NC"]   += gnd

    cin = Part("Device", "C", value="10uF",
               footprint="Capacitor_SMD:C_0805_2012Metric", ref="C_VCC")
    vcc += cin[1]; gnd += cin[2]

    cout = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric", ref="C_3V3")
    v3v3 += cout[1]; gnd += cout[2]

    cdec = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric", ref="C_3V3_DEC")
    v3v3 += cdec[1]; gnd += cdec[2]

    # ON LED
    ron = Part("Device", "R", value="1k",
               footprint="Resistor_SMD:R_0603_1608Metric", ref="R_ON")
    lon = Part("Device", "LED", value="ON_Green",
               footprint="LED_SMD:LED_0603_1608Metric", ref="LED_ON")
    v3v3    += ron[1]
    ron[2]  += lon["A"]
    lon["K"] += gnd


# ==============================================================
# Battery voltage divider for monitoring
# ==============================================================
@subcircuit
def battery_monitor():
    global vbat, gnd, bat_div

    rt = Part("Device", "R", value="100k",
              footprint="Resistor_SMD:R_0603_1608Metric", ref="R_BAT_T")
    rb = Part("Device", "R", value="100k",
              footprint="Resistor_SMD:R_0603_1608Metric", ref="R_BAT_B")
    vbat   += rt[1]; rt[2] += bat_div
    bat_div += rb[1]; rb[2] += gnd


# ==============================================================
# 2MB SPI Flash GD25Q16ETIGR (SOP-8, from LCSC C2904431)
# ==============================================================
@subcircuit
def spi_flash_mem():
    global v3v3, gnd, mosi, miso, sck, flash_cs

    # W25Q32JVSS compatible with GD25Q16C (same 8-pin SPI flash pinout)
    fl = Part("Memory_Flash", "W25Q32JVSS", value="GD25Q16",
              footprint="Package_SO:SOIC-8_5.3x5.3mm_P1.27mm", ref="U_FLASH")
    # Use pin numbers throughout to avoid SKiDL issues with ~{} and /{}
    # W25Q32JVSS pin map: 1=~CS, 2=DO/IO1, 3=~WP/IO2, 4=GND, 5=DI/IO0, 6=CLK, 7=~HOLD/IO3, 8=VCC
    fl[8]  += v3v3      # VCC
    fl[4]  += gnd       # GND
    fl[1]  += flash_cs  # ~{CS}
    fl[6]  += sck       # CLK
    fl[5]  += mosi      # DI/IO0
    fl[2]  += miso      # DO/IO1
    fl[3]  += v3v3      # ~{WP}/IO2 — WP disabled
    fl[7]  += v3v3      # ~{HOLD}/IO3 — HOLD disabled

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric", ref="C_FLASH")
    v3v3 += c[1]; gnd += c[2]

    # CS pull-up
    rcs = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0603_1608Metric", ref="R_FLASH_CS")
    v3v3 += rcs[1]; rcs[2] += flash_cs


# ==============================================================
# WS2812B NeoPixel status LED
# ==============================================================
@subcircuit
def neopixel_status():
    global v3v3, gnd, neo_data

    neo = Part("LED", "WS2812B", value="WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm", ref="D_NEO")
    v3v3     += neo["VDD"]
    gnd      += neo["VSS"]
    neo_data += neo["DIN"]
    neo["DOUT"] += Net("NEO_DOUT")

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric", ref="C_NEO")
    v3v3 += c[1]; gnd += c[2]


# ==============================================================
# 32.768kHz crystal for RTC
# ==============================================================
@subcircuit
def crystal_32k():
    global xin, xout, gnd

    y = Part("Device", "Crystal", value="32.768kHz",
             footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm", ref="Y1")
    xin  += y[1]; xout += y[2]

    c1 = Part("Device", "C", value="12pF",
              footprint="Capacitor_SMD:C_0402_1005Metric", ref="C_XTAL1")
    xin += c1[1]; gnd += c1[2]

    c2 = Part("Device", "C", value="12pF",
              footprint="Capacitor_SMD:C_0402_1005Metric", ref="C_XTAL2")
    xout += c2[1]; gnd += c2[2]


# ==============================================================
# Reset button + pull-up + filter cap
# ==============================================================
@subcircuit
def reset_circuit():
    global v3v3, gnd, reset_n

    r = Part("Device", "R", value="10k",
             footprint="Resistor_SMD:R_0603_1608Metric", ref="R_RST")
    v3v3 += r[1]; r[2] += reset_n

    sw = Part("Switch", "SW_Push", value="RESET",
              footprint="Button_Switch_SMD:SW_SPST_B3U-1000P", ref="SW_RST")
    sw[1] += reset_n; sw[2] += gnd

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric", ref="C_RST")
    reset_n += c[1]; gnd += c[2]


# ==============================================================
# D13 red user LED
# ==============================================================
@subcircuit
def user_led():
    global d13, gnd

    r = Part("Device", "R", value="1k",
             footprint="Resistor_SMD:R_0603_1608Metric", ref="R_LED")
    led = Part("Device", "LED", value="D13_Red",
               footprint="LED_SMD:LED_0603_1608Metric", ref="LED_D13")
    d13     += r[1]; r[2] += led["A"]
    led["K"] += gnd


# ==============================================================
# ATSAMD21G18A-A MCU (TQFP-48, KiCad native symbol)
# ==============================================================
@subcircuit
def samd21_mcu():
    global v3v3, gnd, usb_dp, usb_dm, xin, xout, reset_n
    global sda, scl, mosi, miso, sck, swdio, swclk
    global bat_div, flash_cs, neo_data
    global a0, a1, a2, a3, a4, a5, d5, d6, d9, d10, d11, d12, d13, tx, rx

    mcu = Part("MCU_Microchip_SAMD", "ATSAMD21G18A-A", value="ATSAMD21G18A",
               footprint="Package_QFP:TQFP-48_7x7mm_P0.5mm", ref="U_MCU")

    # Power
    mcu["VDDIO"]  += v3v3
    mcu["VDDIN"]  += v3v3
    mcu["VDDANA"] += v3v3
    mcu["GND"]    += gnd
    mcu["GNDANA"] += gnd

    # VDDCORE (internal 1.2V LDO output — needs 1uF cap)
    vddcore = Net("VDDCORE")
    mcu["VDDCORE"] += vddcore
    ccore = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0603_1608Metric", ref="C_CORE")
    vddcore += ccore[1]; gnd += ccore[2]

    # Decoupling caps
    for i, val in enumerate(["100nF", "100nF", "100nF", "100nF"]):
        c = Part("Device", "C", value=val,
                 footprint="Capacitor_SMD:C_0603_1608Metric", ref="C_MCU" + str(i+1))
        v3v3 += c[1]; gnd += c[2]

    # USB D+/D- (PA24=D-, PA25=D+)
    mcu["PA24"] += usb_dm
    mcu["PA25"] += usb_dp

    # 32.768kHz crystal PA00/PA01
    mcu["PA00"] += xin
    mcu["PA01"] += xout

    # Reset (pin 40 = ~{RESET} — use pin number for reliability)
    mcu[40] += reset_n

    # SWD: PA30=SWCLK, PA31=SWDIO
    mcu["PA30"] += swclk
    mcu["PA31"] += swdio

    # I2C SERCOM3: PA22=SDA, PA23=SCL
    mcu["PA22"] += sda
    mcu["PA23"] += scl

    # SPI (flash+header) SERCOM4: PB10=MOSI, PA12=MISO, PB11=SCK
    mcu["PB10"] += mosi
    mcu["PA12"] += miso
    mcu["PB11"] += sck
    mcu["PA13"] += flash_cs

    # NeoPixel data PA06
    mcu["PA06"] += neo_data

    # Battery divider ADC PA07
    mcu["PA07"] += bat_div

    # Analog header
    mcu["PA02"] += a0
    mcu["PB08"] += a1
    mcu["PB09"] += a2
    mcu["PA04"] += a3
    mcu["PA05"] += a4
    mcu["PB02"] += a5

    # Digital header
    mcu["PA17"] += d13   # D13/SCK
    mcu["PA19"] += d12   # D12/MISO
    mcu["PA16"] += d11   # D11/MOSI
    mcu["PA18"] += d10
    mcu["PA20"] += d9
    mcu["PA15"] += d6
    mcu["PA14"] += d5

    # UART SERCOM0
    mcu["PA10"] += tx
    mcu["PA11"] += rx

    # I2C pull-ups
    rsda = Part("Device", "R", value="4.7k",
                footprint="Resistor_SMD:R_0603_1608Metric", ref="R_SDA")
    rscl = Part("Device", "R", value="4.7k",
                footprint="Resistor_SMD:R_0603_1608Metric", ref="R_SCL")
    v3v3 += rsda[1]; rsda[2] += sda
    v3v3 += rscl[1]; rscl[2] += scl

    # PA03 = VREFA, connect to 3V3 as analog reference
    mcu["PA03"] += v3v3

    # Unused pads to GND
    for pin in ["PA08", "PA09", "PA21", "PA27", "PA28",
                "PB03", "PB22", "PB23"]:
        mcu[pin] += gnd


# ==============================================================
# SWD debug connector (2x5, 1.27mm)
# ==============================================================
@subcircuit
def swd_header():
    global v3v3, gnd, swdio, swclk, reset_n

    j = Part("Connector", "Conn_ARM_JTAG_SWD_10", value="SWD_2x5",
             footprint="Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical_SMD",
             ref="J_SWD")
    j.edge_preference = "right"
    # Use pin numbers to avoid SKiDL issues with '/' and '~{}' in pin names
    # Pin map: 1=VTref, 2=SWDIO/TMS, 3=GND, 4=SWCLK/TCK, 5=GND, 6=SWO/TDO,
    #          7=KEY, 8=NC/TDI, 9=GNDDetect, 10=~{RESET}
    v3v3    += j[1]   # VTref
    swdio   += j[2]   # SWDIO/TMS
    gnd     += j[3]   # GND
    swclk   += j[4]   # SWCLK/TCK
    gnd     += j[5]   # GND
    j[6]    += Net("SWO")  # SWO/TDO unused
    gnd     += j[7]   # KEY
    gnd     += j[8]   # NC/TDI
    gnd     += j[9]   # GNDDetect
    reset_n += j[10]  # ~{RESET}


# ==============================================================
# Feather left header (16 pins): power + analog
# ==============================================================
@subcircuit
def feather_left_header():
    global reset_n, v3v3, gnd, vbat, vbus, a0, a1, a2, a3, a4, a5, tx, rx

    h = Part("Connector_Generic", "Conn_01x16", value="Feather_Left",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical",
             ref="J_HDR_L")
    h[1]  += reset_n
    h[2]  += v3v3
    h[3]  += v3v3      # AREF tied to 3V3
    h[4]  += gnd
    h[5]  += a0
    h[6]  += a1
    h[7]  += a2
    h[8]  += a3
    h[9]  += a4
    h[10] += a5
    h[11] += sck
    h[12] += mosi
    h[13] += miso
    h[14] += tx
    h[15] += rx
    h[16] += Net("NEOPIXEL_HDR")   # Feather Express: extra NeoPixel data out


# ==============================================================
# Feather right header (12 pins): power + digital
# ==============================================================
@subcircuit
def feather_right_header():
    global vbat, vbus, v3v3, gnd, d5, d6, d9, d10, d11, d12, d13, sda, scl

    h = Part("Connector_Generic", "Conn_01x12", value="Feather_Right",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical",
             ref="J_HDR_R")
    h[1]  += vbat
    h[2]  += Net("EN")      # LDO enable exposed
    h[3]  += vbus
    h[4]  += d13
    h[5]  += d12
    h[6]  += d11
    h[7]  += d10
    h[8]  += d9
    h[9]  += d6
    h[10] += d5
    h[11] += sda
    h[12] += scl


# ==============================================================
# Top-level
# ==============================================================
usb_input()
lipo_connector()
lipo_charger()
power_supply()
battery_monitor()
spi_flash_mem()
neopixel_status()
crystal_32k()
reset_circuit()
samd21_mcu()
user_led()
swd_header()
feather_left_header()
feather_right_header()
