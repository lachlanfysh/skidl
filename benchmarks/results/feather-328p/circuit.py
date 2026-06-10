"""
Feather 328P Classic Arduino - SKiDL Circuit
ATmega328P @ 3.3V/8MHz with CP2104 USB-serial, MCP73831 battery charger,
AP2112K-3.3 voltage regulator, Feather form factor.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# ======================================================================
# Power nets
# ======================================================================
vbus = Net("VBUS"); vbus.drive = POWER
vbat = Net("VBAT"); vbat.drive = POWER
vcc = Net("+3V3"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ======================================================================
# USB Input Subcircuit
# ======================================================================
@subcircuit
def usb_input(vbus, gnd, dp, dm):
    """USB Micro-B connector with ESD protection."""
    usb_conn = Part("Connector", "USB_B_Micro",
                    footprint="Connector_USB:USB_Micro-B_Molex_47346-0001",
                    value="USB_Micro-B")
    usb_conn["VBUS"] += vbus
    usb_conn["GND"] += gnd
    usb_conn["D+"] += dp
    usb_conn["D-"] += dm
    usb_conn["ID"] += gnd    # ID pin tied to ground for device mode
    usb_conn["Shield"] += gnd

# ======================================================================
# USB-Serial Converter (CP2104)
# ======================================================================
@subcircuit
def usb_serial(vbus, vcc, gnd, dp, dm, txd, rxd, dtr):
    """CP2104 USB-to-UART bridge."""
    cp = Part("Interface_USB", "CP2104",
              footprint="Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.65x2.65mm",
              value="CP2104")

    cp["VBUS"] += vbus
    cp["REGIN"] += vbus
    cp["VDD"] += vbus
    cp["VIO"] += vcc
    cp["GND"] += gnd
    # Exposed pad (pin 25) is GND
    cp[25] += gnd

    cp["D+"] += dp
    cp["D-"] += dm

    cp["TXD"] += txd
    cp["RXD"] += rxd
    cp["~{DTR}"] += dtr

    # Unused pins
    cp["~{RST}"] += vcc
    cp["~{RI}"] += vcc
    cp["~{DCD}"] += vcc
    cp["~{DSR}"] += vcc
    cp["~{CTS}"] += vcc

    # Tie off unused output pins individually (outputs cannot share a net)
    nc_suspend = Net("NC_SUSPEND")
    nc_suspend.drive = POWER
    cp["SUSPEND"] += nc_suspend

    nc_nsuspend = Net("NC_nSUSPEND")
    nc_nsuspend.drive = POWER
    cp["~{SUSPEND}"] += nc_nsuspend

    nc_rts = Net("NC_nRTS")
    nc_rts.drive = POWER
    cp["~{RTS}"] += nc_rts

    # Tie off unused bidirectional GPIO individually
    nc_gpio0 = Net("NC_GPIO0")
    nc_gpio0.drive = POWER
    cp["TXT/GPIO.0"] += nc_gpio0

    nc_gpio1 = Net("NC_GPIO1")
    nc_gpio1.drive = POWER
    cp["RXT/GPIO.1"] += nc_gpio1

    nc_gpio2 = Net("NC_GPIO2")
    nc_gpio2.drive = POWER
    cp["RS485/GPIO.2"] += nc_gpio2

    nc_gpio3 = Net("NC_GPIO3")
    nc_gpio3.drive = POWER
    cp["GPIO.3"] += nc_gpio3

    # VPP - leave unconnected or tie to VIO
    cp["VPP"] += vcc

    # Decoupling caps for CP2104
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vbus[1] += vbus; c_vbus[2] += gnd

    c_vio = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vio[1] += vcc; c_vio[2] += gnd

    c_vdd = Part("Device", "C", value="4.7uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vdd[1] += vbus; c_vdd[2] += gnd


# ======================================================================
# Battery Charger (MCP73831)
# ======================================================================
@subcircuit
def battery_charger(vbus, vbat, gnd):
    """MCP73831 single-cell LiPo charger with 500mA charge current."""
    chrg = Part("Battery_Management", "MCP73831-2-OT",
                footprint="Package_TO_SOT_SMD:SOT-23-5",
                value="MCP73831")

    chrg["V_{DD}"] += vbus
    chrg["V_{SS}"] += gnd
    chrg["V_{BAT}"] += vbat

    # PROG resistor sets charge current: 2K = 500mA
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    chrg["PROG"] += r_prog[1]
    r_prog[2] += gnd

    # STAT LED (charge indicator)
    r_stat = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    led_chrg = Part("Device", "LED", value="ORANGE",
                    footprint="LED_SMD:LED_0603_1608Metric")
    chrg["STAT"] += r_stat[1]
    r_stat[2] += led_chrg[1]
    led_chrg[2] += gnd

    # Battery connector
    jst_bat = Part("Connector_Generic", "Conn_01x02",
                   footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
                   value="JST-PH_BAT")
    jst_bat[1] += vbat
    jst_bat[2] += gnd

    # Bypass cap on VBAT
    c_bat = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bat[1] += vbat; c_bat[2] += gnd


# ======================================================================
# Power Supply (AP2112K-3.3 LDO)
# ======================================================================
@subcircuit
def power_supply(vbus, vbat, vcc, gnd):
    """AP2112K-3.3 LDO regulator. Input from VBUS/VBAT via Schottky diode OR."""
    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               value="AP2112K-3.3")

    reg["VIN"] += vbat
    reg["GND"] += gnd
    reg["EN"] += vbat  # Always enabled
    reg["VOUT"] += vcc

    # Input cap
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vbat; c_in[2] += gnd

    # Output cap
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vcc; c_out[2] += gnd

    # Schottky diode from VBUS to VBAT for power OR-ing
    d_or = Part("Device", "D_Schottky", value="MBR0520",
                footprint="Diode_SMD:D_SOD-123")
    d_or[1] += vbat   # cathode (K) to VBAT side
    d_or[2] += vbus   # anode (A) from VBUS


# ======================================================================
# ATmega328P MCU Block
# ======================================================================
@subcircuit
def mcu_block(vcc, gnd, txd, rxd, dtr, sck, miso, mosi, ss,
              sda, scl, a0, a1, a2, a3, a4, a5):
    """ATmega328P-AU running at 8MHz / 3.3V."""
    mcu = Part("MCU_Microchip_ATmega", "ATmega328P-A",
               footprint="Package_QFP:TQFP-32_7x7mm_P0.8mm",
               value="ATmega328P-AU")

    # Power
    mcu["VCC"] += vcc
    mcu["AVCC"] += vcc
    mcu["GND"] += gnd
    mcu["AREF"] += vcc  # AREF tied to VCC via cap

    # Crystal - 8MHz
    xtal = Part("Device", "Crystal", value="8MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["XTAL1/PB6"]
    xtal[2] += mcu["XTAL2/PB7"]

    # Crystal load caps
    c_x1 = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2 = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += mcu["XTAL1/PB6"]; c_x1[2] += gnd
    c_x2[1] += mcu["XTAL2/PB7"]; c_x2[2] += gnd

    # Decoupling caps
    c_vcc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcc[1] += vcc; c_vcc[2] += gnd

    c_avcc = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_avcc[1] += vcc; c_avcc[2] += gnd

    # AREF cap
    c_aref = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_aref[1] += mcu["AREF"]; c_aref[2] += gnd

    # UART (connected to CP2104)
    mcu["PD0"] += rxd   # RXD
    mcu["PD1"] += txd   # TXD

    # SPI
    mcu["PB5"] += sck   # SCK
    mcu["PB4"] += miso  # MISO
    mcu["PB3"] += mosi  # MOSI
    mcu["PB2"] += ss    # SS

    # I2C
    mcu["PC4"] += sda   # SDA
    mcu["PC5"] += scl   # SCL

    # Analog pins
    mcu["PC0"] += a0
    mcu["PC1"] += a1
    mcu["PC2"] += a2
    mcu["PC3"] += a3
    mcu["ADC6"] += a4
    mcu["ADC7"] += a5

    # Digital pins exposed to headers
    mcu["PD2"] += Net("D2")
    mcu["PD3"] += Net("D3")
    mcu["PD4"] += Net("D4")
    mcu["PD5"] += Net("D5")
    mcu["PD6"] += Net("D6")
    mcu["PD7"] += Net("D7")
    mcu["PB0"] += Net("D8")
    mcu["PB1"] += Net("D9")

    # Reset with pull-up and capacitor (DTR auto-reset)
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    mcu["~{RESET}/PC6"] += r_rst[1]
    r_rst[2] += vcc
    mcu["~{RESET}/PC6"] += c_rst[1]
    c_rst[2] += dtr  # DTR for auto-reset from CP2104

    # User LED on D13/PB5 (shared with SCK)
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    led_d13 = Part("Device", "LED", value="RED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_led[1] += sck
    r_led[2] += led_d13[1]
    led_d13[2] += gnd


# ======================================================================
# Reset Button
# ======================================================================
@subcircuit
def reset_button(reset_net, gnd):
    """Tactile reset button."""
    sw = Part("Switch", "SW_Push",
              footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
              value="RESET")
    sw[1] += reset_net
    sw[2] += gnd


# ======================================================================
# Feather Edge Headers
# ======================================================================
@subcircuit
def feather_headers(vcc, gnd, vbat, a0, a1, a2, a3, a4, a5,
                    sck, miso, mosi, rxd, txd, sda, scl):
    """Feather-standard pin headers (16-pin + 12-pin)."""
    # Left header (16 pins)
    hdr_l = Part("Connector_Generic", "Conn_01x16",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical",
                 value="FEATHER_L")
    hdr_l[1] += Net("~{RST_HDR}")   # RST
    hdr_l[2] += vcc                   # 3V3
    hdr_l[3] += Net("AREF_HDR")      # AREF
    hdr_l[4] += gnd                   # GND
    hdr_l[5] += a0                    # A0
    hdr_l[6] += a1                    # A1
    hdr_l[7] += a2                    # A2
    hdr_l[8] += a3                    # A3
    hdr_l[9] += a4                    # A4
    hdr_l[10] += a5                   # A5
    hdr_l[11] += sck                  # SCK
    hdr_l[12] += mosi                 # MOSI
    hdr_l[13] += miso                 # MISO
    hdr_l[14] += rxd                  # RX
    hdr_l[15] += txd                  # TX
    hdr_l[16] += Net("D4_HDR")        # D4

    # Right header (12 pins)
    hdr_r = Part("Connector_Generic", "Conn_01x12",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical",
                 value="FEATHER_R")
    hdr_r[1] += vbat                  # BAT
    hdr_r[2] += Net("EN")             # EN
    hdr_r[3] += Net("VBUS_HDR")       # USB
    hdr_r[4] += Net("D13_HDR")        # D13
    hdr_r[5] += Net("D12_HDR")        # D12
    hdr_r[6] += Net("D11_HDR")        # D11
    hdr_r[7] += Net("D10_HDR")        # D10
    hdr_r[8] += Net("D9_HDR")         # D9
    hdr_r[9] += Net("D6_HDR")         # D6
    hdr_r[10] += Net("D5_HDR")        # D5
    hdr_r[11] += scl                  # SCL
    hdr_r[12] += sda                  # SDA


# ======================================================================
# Main Circuit Assembly
# ======================================================================

# Signal nets
dp = Net("USB_DP")
dm = Net("USB_DM")
txd = Net("TXD")
rxd = Net("RXD")
dtr = Net("DTR")
sck = Net("SCK")
miso = Net("MISO")
mosi = Net("MOSI")
ss = Net("SS")
sda = Net("SDA")
scl = Net("SCL")
a0 = Net("A0")
a1 = Net("A1")
a2 = Net("A2")
a3 = Net("A3")
a4 = Net("A4")
a5 = Net("A5")

# Instantiate subcircuits
usb_input(vbus, gnd, dp, dm)
usb_serial(vbus, vcc, gnd, dp, dm, txd, rxd, dtr)
battery_charger(vbus, vbat, gnd)
power_supply(vbus, vbat, vcc, gnd)
mcu_block(vcc, gnd, txd, rxd, dtr, sck, miso, mosi, ss,
          sda, scl, a0, a1, a2, a3, a4, a5)
reset_button(Net("~{RESET}/PC6"), gnd)
feather_headers(vcc, gnd, vbat, a0, a1, a2, a3, a4, a5,
                sck, miso, mosi, rxd, txd, sda, scl)

# Generate schematic
generate_schematic(auto_stub=True, auto_stub_fanout=3)
