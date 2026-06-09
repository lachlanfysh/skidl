"""
HUZZAH32 ESP32 Feather - SKiDL Circuit Description

ESP32-based Feather using the official WROOM-32 module with dual-core ESP32,
4 MB SPI Flash, tuned antenna. WiFi and Bluetooth Classic/LE support.
Built-in CP2104 USB-to-Serial converter, automatic bootloader reset,
MCP73831 Lithium Ion/Polymer charger, AP2112K-3.3 LDO regulator.
All GPIOs brought out to Feather-compatible headers (12+16 pins).
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# Power nets
# ============================================================
vbus = Net("VBUS"); vbus.drive = POWER      # USB 5V
vbat = Net("VBAT"); vbat.drive = POWER      # Battery voltage
v3v3 = Net("+3V3"); v3v3.drive = POWER      # 3.3V regulated
gnd  = Net("GND");  gnd.drive  = POWER      # Ground

# ============================================================
# Signal nets
# ============================================================
usb_dp  = Net("USB_D+")
usb_dm  = Net("USB_D-")
uart_tx = Net("TXD0")
uart_rx = Net("RXD0")
dtr_n   = Net("~{DTR}")
rts_n   = Net("~{RTS}")
en_net  = Net("EN")
io0_net = Net("IO0")

# ESP32 GPIO nets
gpio2  = Net("IO2")
gpio4  = Net("IO4")
gpio5  = Net("IO5")
gpio12 = Net("IO12")
gpio13 = Net("IO13")
gpio14 = Net("IO14")
gpio15 = Net("IO15")
gpio16 = Net("IO16")
gpio17 = Net("IO17")
gpio18 = Net("IO18")
gpio19 = Net("IO19")
gpio21 = Net("IO21")
gpio22 = Net("IO22")
gpio23 = Net("IO23")
gpio25 = Net("IO25")
gpio26 = Net("IO26")
gpio27 = Net("IO27")
gpio32 = Net("IO32")
gpio33 = Net("IO33")
gpio34 = Net("IO34")
gpio35 = Net("IO35")
svp    = Net("SENSOR_VP")
svn    = Net("SENSOR_VN")

# Battery charger status
chg_stat = Net("CHG_STAT")


# ============================================================
# Subcircuit: USB-to-UART (CP2104)
# ============================================================
@subcircuit
def usb_uart_bridge(vbus, v3v3, gnd, usb_dp, usb_dm, uart_tx, uart_rx, dtr_n, rts_n):
    """CP2104 USB-to-UART bridge with auto-reset circuitry."""

    # USB Micro-B connector
    usb_conn = Part("Connector", "USB_B_Micro",
                    footprint="Connector_USB:USB_Micro-B_Amphenol_10118194_Horizontal")
    usb_conn["VBUS"] += vbus
    usb_conn["D+"]   += usb_dp
    usb_conn["D-"]   += usb_dm
    usb_conn["GND"]  += gnd
    usb_conn["Shield"] += gnd

    # CP2104 USB-to-UART bridge
    cp2104 = Part("Interface_USB", "CP2104",
                  footprint="Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm")
    cp2104["VDD"]     += vbus
    cp2104["REGIN"]   += vbus
    cp2104["VIO"]     += v3v3
    cp2104["D+"]      += usb_dp
    cp2104["D-"]      += usb_dm
    cp2104["TXD"]     += uart_rx   # CP2104 TXD -> ESP32 RXD
    cp2104["RXD"]     += uart_tx   # ESP32 TXD -> CP2104 RXD
    cp2104["~{DTR}"]  += dtr_n
    cp2104["~{RTS}"]  += rts_n
    cp2104["GND"]     += gnd

    # Decoupling caps for CP2104
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd[1] += vbus
    c_vdd[2] += gnd

    c_vio = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vio[1] += v3v3
    c_vio[2] += gnd

    # Auto-reset circuit: DTR+RTS -> EN and IO0 for bootloader entry
    # Uses two NPN transistors (BSS138 MOSFETs or 2N7002 on real board,
    # but functionally represented as NPN transistors)
    # DTR -> 10K -> EN (via transistor)
    # RTS -> 10K -> IO0 (via transistor)

    q1 = Part("Device", "Q_NPN", footprint="Package_TO_SOT_SMD:SOT-23")
    q2 = Part("Device", "Q_NPN", footprint="Package_TO_SOT_SMD:SOT-23")

    # Q1: RTS controls EN reset
    q1["E"] += gnd
    q1["C"] += en_net
    q1["B"] += rts_n

    # Q2: DTR controls IO0 boot mode
    q2["E"] += gnd
    q2["C"] += io0_net
    q2["B"] += dtr_n

    # Pull-up resistors for auto-reset
    r_en = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_en[1] += v3v3
    r_en[2] += en_net

    r_io0 = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_io0[1] += v3v3
    r_io0[2] += io0_net


# ============================================================
# Subcircuit: Power Management
# ============================================================
@subcircuit
def power_management(vbus, vbat, v3v3, gnd, chg_stat):
    """MCP73831 LiPo charger + AP2112K-3.3 LDO regulator."""

    # JST PH connector for LiPo battery
    j_bat = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    j_bat[1] += vbat
    j_bat[2] += gnd

    # MCP73831-2-OT LiPo charger
    mcp73831 = Part("Battery_Management", "MCP73831-2-OT",
                    footprint="Package_TO_SOT_SMD:SOT-23-5")
    mcp73831["V_{DD}"]  += vbus
    mcp73831["V_{BAT}"] += vbat
    mcp73831["V_{SS}"]  += gnd
    mcp73831["STAT"]    += chg_stat
    prog_net = Net("PROG")
    mcp73831["PROG"]    += prog_net

    # Charge current programming resistor (2K = ~500mA charge rate)
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_prog[1] += prog_net
    r_prog[2] += gnd

    # Charge status LED (active low)
    r_chg_led = Part("Device", "R", value="1K",
                     footprint="Resistor_SMD:R_0603_1608Metric")
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_chg_led[1] += vbus
    r_chg_led[2] += led_chg["A"]
    led_chg["K"] += chg_stat

    # Input bulk cap for charger
    c_vbus = Part("Device", "C", value="4.7uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd

    # Battery bulk cap
    c_bat = Part("Device", "C", value="4.7uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bat[1] += vbat
    c_bat[2] += gnd

    # Schottky diode for power-OR between USB and battery
    # When USB connected, VBUS powers the board; when not, battery does
    # Schottky diode: VBAT -> anode -> cathode -> VBUS rail for power-OR
    d_pwr = Part("Device", "D_Schottky", value="MBR120",
                 footprint="Diode_SMD:D_SOD-123")
    d_pwr["A"] += vbat   # anode from battery
    d_pwr["K"] += vbus   # cathode to VBUS rail

    # AP2112K-3.3V LDO regulator (600mA output)
    ap2112 = Part("Regulator_Linear", "AP2112K-3.3",
                  footprint="Package_TO_SOT_SMD:SOT-23-5")
    ap2112["VIN"]  += vbus
    ap2112["EN"]   += vbus    # Tied high to always enable
    ap2112["GND"]  += gnd
    ap2112["VOUT"] += v3v3

    # Decoupling caps for AP2112
    c_vin = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vin[1] += vbus
    c_vin[2] += gnd

    c_vout = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vout[1] += v3v3
    c_vout[2] += gnd

    # Output bulk cap
    c_out_bulk = Part("Device", "C", value="10uF",
                      footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out_bulk[1] += v3v3
    c_out_bulk[2] += gnd


# ============================================================
# Subcircuit: ESP32 WROOM-32 Module
# ============================================================
@subcircuit
def esp32_module(v3v3, gnd, en_net, io0_net, uart_tx, uart_rx):
    """ESP32-WROOM-32 module with decoupling and bootstrap."""

    esp32 = Part("RF_Module", "ESP32-WROOM-32",
                 footprint="RF_Module:ESP32-WROOM-32")
    esp32["VDD"]  += v3v3
    esp32["GND"]  += gnd
    esp32["EN"]   += en_net

    # UART0 (programming/debug)
    esp32["TXD0/IO1"]  += uart_tx
    esp32["RXD0/IO3"]  += uart_rx

    # Boot mode strapping pin
    esp32["IO0"]  += io0_net

    # GPIO breakout connections
    esp32["IO2"]   += gpio2
    esp32["IO4"]   += gpio4
    esp32["IO5"]   += gpio5
    esp32["IO12"]  += gpio12
    esp32["IO13"]  += gpio13
    esp32["IO14"]  += gpio14
    esp32["IO15"]  += gpio15
    esp32["IO16"]  += gpio16
    esp32["IO17"]  += gpio17
    esp32["IO18"]  += gpio18
    esp32["IO19"]  += gpio19
    esp32["IO21"]  += gpio21
    esp32["IO22"]  += gpio22
    esp32["IO23"]  += gpio23
    esp32["IO25"]  += gpio25
    esp32["IO26"]  += gpio26
    esp32["IO27"]  += gpio27
    esp32["IO32"]  += gpio32
    esp32["IO33"]  += gpio33
    esp32["IO34"]  += gpio34
    esp32["IO35"]  += gpio35
    esp32["SENSOR_VP"] += svp
    esp32["SENSOR_VN"] += svn

    # EN pull-up with filter cap (10K + 100nF)
    c_en = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_en[1] += en_net
    c_en[2] += gnd

    # Decoupling caps for ESP32
    c_esp1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_esp1[1] += v3v3
    c_esp1[2] += gnd

    c_esp2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_esp2[1] += v3v3
    c_esp2[2] += gnd


# ============================================================
# Subcircuit: Feather Headers and User Interface
# ============================================================
@subcircuit
def feather_headers(vbus, vbat, v3v3, gnd, en_net):
    """Feather-compatible headers: 16-pin and 12-pin."""

    # 16-pin header (left side of Feather)
    # Pinout: RST, 3V, NC, GND, A0(26), A1(25), A2(34), A3(39/SVN),
    #         A4(36/SVP), A5(4), SCK(5), MOSI(18), MISO(19), RX(16), TX(17), IO21
    hdr16 = Part("Connector_Generic", "Conn_01x16",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    hdr16[1]  += en_net     # RST / EN
    hdr16[2]  += v3v3       # 3V3
    hdr16[3]  += NC         # NC
    hdr16[4]  += gnd        # GND
    hdr16[5]  += gpio26     # A0 / DAC2
    hdr16[6]  += gpio25     # A1 / DAC1
    hdr16[7]  += gpio34     # A2 (input only)
    hdr16[8]  += svn        # A3 / SENSOR_VN (input only)
    hdr16[9]  += svp        # A4 / SENSOR_VP (input only)
    hdr16[10] += gpio4      # A5
    hdr16[11] += gpio5      # SCK
    hdr16[12] += gpio18     # MOSI
    hdr16[13] += gpio19     # MISO
    hdr16[14] += gpio16     # RX (UART2)
    hdr16[15] += gpio17     # TX (UART2)
    hdr16[16] += gpio21     # IO21 / SDA

    # 12-pin header (right side of Feather)
    # Pinout: BAT, EN, USB, 13, 12, 27, 33, 15, 32, 14, SCL(22), SDA(23)
    hdr12 = Part("Connector_Generic", "Conn_01x12",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    hdr12[1]  += vbat       # BAT
    hdr12[2]  += en_net     # EN
    hdr12[3]  += vbus       # USB (5V)
    hdr12[4]  += gpio13     # IO13
    hdr12[5]  += gpio12     # IO12
    hdr12[6]  += gpio27     # IO27
    hdr12[7]  += gpio33     # IO33
    hdr12[8]  += gpio15     # IO15
    hdr12[9]  += gpio32     # IO32
    hdr12[10] += gpio14     # IO14
    hdr12[11] += gpio22     # SCL
    hdr12[12] += gpio23     # SDA

    # Reset button
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1TS009xxxx-xxxx-xxxx_6x6x5mm")
    sw_rst[1] += en_net
    sw_rst[2] += gnd

    # General purpose LED on IO13
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    led_usr = Part("Device", "LED", value="RED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_led[1] += gpio13
    r_led[2] += led_usr["A"]
    led_usr["K"] += gnd


# ============================================================
# Instantiate all subcircuits
# ============================================================
usb_uart_bridge(vbus, v3v3, gnd, usb_dp, usb_dm, uart_tx, uart_rx, dtr_n, rts_n)
power_management(vbus, vbat, v3v3, gnd, chg_stat)
esp32_module(v3v3, gnd, en_net, io0_net, uart_tx, uart_rx)
feather_headers(vbus, vbat, v3v3, gnd, en_net)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True)
