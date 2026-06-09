"""
KB2040 RP2040 Keyboard Controller
==================================
Pro Micro-shaped RP2040 board for custom keyboard builds.
- RP2040 MCU with 8MB QSPI Flash
- Built-in NeoPixel (WS2812B)
- STEMMA QT / Qwiic I2C port (JST SH 4-pin)
- USB-C connector
- Castellated pads (2x 1x13 headers) for direct PCB mounting
- 20+ GPIO pins support up to 100-key matrices
- AP2112K-3.3 voltage regulator
- 12MHz crystal for RP2040
- CircuitPython ready
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
vbus = Net("VBUS"); vbus.drive = POWER      # USB 5V input
vcc  = Net("+3V3"); vcc.drive = POWER       # 3.3V regulated
gnd  = Net("GND");  gnd.drive = POWER       # Ground

# ============================================================
# Subcircuit: USB-C Connector (simplified to 5-pin representation)
# ============================================================
@subcircuit
def usb_connector(vbus, gnd, dp, dm):
    """USB-C receptacle for USB 2.0 data and power, modelled as 5-pin connector."""
    usb = Part("Connector_Generic", "Conn_01x05", value="USB_C",
               footprint="Connector_USB:USB_C_Receptacle_GCT_USB4085")
    # Pin 1=VBUS, 2=DM, 3=DP, 4=CC, 5=GND
    usb["Pin_1"] += vbus
    usb["Pin_2"] += dm
    usb["Pin_3"] += dp
    cc = Net("CC")
    usb["Pin_4"] += cc
    usb["Pin_5"] += gnd

    # CC resistors: 5.1K to GND for device mode (UFP)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += cc; r_cc1[2] += gnd

    # ESD protection capacitor on VBUS
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vbus[1] += vbus; c_vbus[2] += gnd


# ============================================================
# Subcircuit: Voltage Regulator (3.3V from USB 5V)
# ============================================================
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 LDO regulator with input/output caps."""
    reg = Part("Regulator_Linear", "AP2112K-3.3", value="AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"]  += vin
    reg["GND"]  += gnd
    reg["EN"]   += vin   # Always enabled
    reg["VOUT"] += vout

    # Input capacitor
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vin; c_in[2] += gnd

    # Output capacitor
    c_out = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out[1] += vout; c_out[2] += gnd


# ============================================================
# Subcircuit: RP2040 MCU with crystal, decoupling, flash
# ============================================================
@subcircuit
def rp2040_system(vcc, gnd, usb_dp, usb_dm, gpio_nets, sda_net, scl_net, neopixel_data):
    """
    RP2040 microcontroller with:
    - 12MHz crystal
    - Decoupling capacitors for all power pins
    - 8MB QSPI flash (W25Q128JVS)
    - VREG 1.1V internal regulator output cap
    """
    # --- RP2040 MCU ---
    mcu = Part("MCU_RaspberryPi", "RP2040", value="RP2040",
               footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm")

    # Power connections: IOVDD pins (1, 10, 22, 33, 42, 49)
    mcu["IOVDD"] += vcc

    # DVDD pins (23, 50) - digital core voltage from internal regulator
    dvdd = Net("DVDD"); dvdd.drive = POWER
    mcu["DVDD"] += dvdd

    # USB VDD gets 3.3V
    mcu["USB_VDD"] += vcc
    c_usb_vdd = Part("Device", "C", value="100nF",
                      footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usb_vdd[1] += vcc; c_usb_vdd[2] += gnd

    # ADC_AVDD - analog reference with RC filter
    adc_avdd = Net("ADC_AVDD"); adc_avdd.drive = POWER
    mcu["ADC_AVDD"] += adc_avdd
    r_adc = Part("Device", "R", value="200",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    r_adc[1] += vcc; r_adc[2] += adc_avdd
    c_adc[1] += adc_avdd; c_adc[2] += gnd

    # VREG - internal 1.1V regulator
    mcu["VREG_VIN"] += vcc
    vreg_vout = Net("VREG_VOUT")
    mcu["VREG_VOUT"] += vreg_vout
    vreg_vout += dvdd

    # VREG output capacitor (1uF required)
    c_vreg = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vreg[1] += vreg_vout; c_vreg[2] += gnd

    # GND
    mcu["GND"] += gnd

    # TESTEN - tie to GND
    mcu["TESTEN"] += gnd

    # RUN pin - pull up to 3.3V with 10K for normal operation
    r_run = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_run[1] += vcc; r_run[2] += mcu["RUN"]

    # SWCLK/SWDIO for debug
    swclk = Net("SWCLK")
    swdio = Net("SWDIO")
    mcu["SWCLK"] += swclk
    mcu["SWDIO"] += swdio

    # --- Decoupling capacitors ---
    # 6x IOVDD + 1x USB_VDD (already above) = 6 more
    for i in range(6):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vcc; c[2] += gnd

    # 2x DVDD decoupling
    for i in range(2):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += dvdd; c[2] += gnd

    # --- 12MHz Crystal ---
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["XIN"]
    xtal[2] += mcu["XOUT"]

    # Crystal load capacitors (15pF)
    c_xin = Part("Device", "C", value="15pF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xout = Part("Device", "C", value="15pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xin[1] += mcu["XIN"];  c_xin[2] += gnd
    c_xout[1] += mcu["XOUT"]; c_xout[2] += gnd

    # --- USB Data ---
    mcu["USB_DP"] += usb_dp
    mcu["USB_DM"] += usb_dm

    # --- GPIO Assignments ---
    # Map 22 GPIOs to external nets
    gpio_pin_names = [
        "GPIO0", "GPIO1", "GPIO2", "GPIO3", "GPIO4",
        "GPIO5", "GPIO6", "GPIO7", "GPIO8", "GPIO9",
        "GPIO10", "GPIO14", "GPIO15", "GPIO16", "GPIO17",
        "GPIO18", "GPIO19", "GPIO20",
        "GPIO26/ADC0", "GPIO27/ADC1", "GPIO28/ADC2", "GPIO29/ADC3",
    ]
    for i, pin_name in enumerate(gpio_pin_names):
        if i < len(gpio_nets):
            mcu[pin_name] += gpio_nets[i]

    # GPIO12 = NeoPixel data out
    mcu["GPIO12"] += neopixel_data

    # GPIO22 = SDA, GPIO23 = SCL (I2C1 for STEMMA QT)
    mcu["GPIO22"] += sda_net
    mcu["GPIO23"] += scl_net

    # Unused GPIOs: GPIO11, GPIO13, GPIO21, GPIO24, GPIO25

    # --- QSPI Flash: W25Q128JVS (8MB / 64Mbit) ---
    flash = Part("Memory_Flash", "W25Q128JVS*", value="W25Q128JVS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")

    flash["VCC"]                       += vcc
    flash["GND"]                       += gnd
    flash["~{CS}"]                     += mcu["~{QSPI_SS}"]
    flash["CLK"]                       += mcu["QSPI_SCLK"]
    flash["DI/IO_{0}"]                += mcu["QSPI_SD0"]
    flash["DO/IO_{1}"]                += mcu["QSPI_SD1"]
    flash["~{WP}/IO_{2}"]            += mcu["QSPI_SD2"]
    flash["~{HOLD}/~{RESET}/IO_{3}"] += mcu["QSPI_SD3"]

    # Flash decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += vcc; c_flash[2] += gnd


# ============================================================
# Subcircuit: NeoPixel (WS2812B)
# ============================================================
@subcircuit
def neopixel(vcc, gnd, din):
    """Built-in NeoPixel LED with bypass cap."""
    led = Part("LED", "WS2812B", value="WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    led["VDD"] += vcc
    led["VSS"] += gnd
    led["DIN"] += din
    # DOUT left unconnected (single LED, no chain)

    # Bypass cap
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += vcc; c_neo[2] += gnd


# ============================================================
# Subcircuit: STEMMA QT / Qwiic I2C Port
# ============================================================
@subcircuit
def stemma_qt(vcc, gnd, sda, scl):
    """JST SH 4-pin connector for STEMMA QT / Qwiic I2C."""
    j_qt = Part("Connector_Generic", "Conn_01x04", value="STEMMA_QT",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    j_qt["Pin_1"] += gnd
    j_qt["Pin_2"] += vcc
    j_qt["Pin_3"] += sda
    j_qt["Pin_4"] += scl

    # I2C pull-up resistors (4.7K)
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc; r_sda[2] += sda
    r_scl[1] += vcc; r_scl[2] += scl


# ============================================================
# Subcircuit: Castellated Pad Headers (Pro Micro form factor)
# ============================================================
@subcircuit
def castellated_headers(vcc, gnd, vbus, gpio_nets):
    """
    Two 1x13 castellated pad headers (left + right side),
    Pro Micro compatible pinout.
    """
    # Left header: 13 pins
    j_left = Part("Connector_Generic", "Conn_01x13", value="HDR_LEFT",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x13_P2.54mm_Vertical")
    # Right header: 13 pins
    j_right = Part("Connector_Generic", "Conn_01x13", value="HDR_RIGHT",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x13_P2.54mm_Vertical")

    # Left header pinout:
    # 1=D0/TX, 2=D1/RX, 3=GND, 4=GND, 5=D2, 6=D3, 7=D4, 8=D5,
    # 9=D6, 10=D7, 11=D8, 12=D9, 13=D10
    j_left["Pin_1"]  += gpio_nets[0]
    j_left["Pin_2"]  += gpio_nets[1]
    j_left["Pin_3"]  += gnd
    j_left["Pin_4"]  += gnd
    j_left["Pin_5"]  += gpio_nets[2]
    j_left["Pin_6"]  += gpio_nets[3]
    j_left["Pin_7"]  += gpio_nets[4]
    j_left["Pin_8"]  += gpio_nets[5]
    j_left["Pin_9"]  += gpio_nets[6]
    j_left["Pin_10"] += gpio_nets[7]
    j_left["Pin_11"] += gpio_nets[8]
    j_left["Pin_12"] += gpio_nets[9]
    j_left["Pin_13"] += gpio_nets[10]

    # Right header pinout:
    # 1=RAW(VBUS), 2=GND, 3=RST, 4=VCC(3.3V), 5=A3, 6=A2, 7=A1, 8=A0,
    # 9=D21, 10=D20, 11=D19, 12=D18, 13=MISO
    rst_hdr = Net("RST_HDR")
    j_right["Pin_1"]  += vbus
    j_right["Pin_2"]  += gnd
    j_right["Pin_3"]  += rst_hdr
    j_right["Pin_4"]  += vcc
    j_right["Pin_5"]  += gpio_nets[21]  # A3 (GPIO29/ADC3)
    j_right["Pin_6"]  += gpio_nets[20]  # A2 (GPIO28/ADC2)
    j_right["Pin_7"]  += gpio_nets[19]  # A1 (GPIO27/ADC1)
    j_right["Pin_8"]  += gpio_nets[18]  # A0 (GPIO26/ADC0)
    j_right["Pin_9"]  += gpio_nets[17]  # D21 (GPIO20)
    j_right["Pin_10"] += gpio_nets[16]  # D20 (GPIO19)
    j_right["Pin_11"] += gpio_nets[15]  # D19 (GPIO18)
    j_right["Pin_12"] += gpio_nets[14]  # D18 (GPIO17)
    j_right["Pin_13"] += gpio_nets[13]  # MISO (GPIO16)


# ============================================================
# Subcircuit: Boot/Reset Buttons
# ============================================================
@subcircuit
def buttons(gnd, boot_net, reset_net):
    """Boot select and reset buttons."""
    # Use generic 2-pin connectors with button footprints
    sw_boot = Part("Connector_Generic", "Conn_01x02", value="BOOT",
                   footprint="Button_Switch_SMD:SW_SPST_PTS645")
    sw_boot["Pin_1"] += boot_net
    sw_boot["Pin_2"] += gnd

    sw_reset = Part("Connector_Generic", "Conn_01x02", value="RESET",
                    footprint="Button_Switch_SMD:SW_SPST_PTS645")
    sw_reset["Pin_1"] += reset_net
    sw_reset["Pin_2"] += gnd


# ============================================================
# Top-level circuit assembly
# ============================================================

# Signal nets
usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")
neopixel_data = Net("NEOPIXEL")
sda = Net("SDA")
scl = Net("SCL")

# GPIO nets (22 total)
gpio_nets = [Net(f"GPIO{i}") for i in range(22)]

# BOOT pin
boot_net = Net("BOOT")

# Instantiate subcircuits
usb_connector(vbus, gnd, usb_dp, usb_dm)
voltage_regulator(vbus, vcc, gnd)
rp2040_system(vcc, gnd, usb_dp, usb_dm, gpio_nets, sda, scl, neopixel_data)
neopixel(vcc, gnd, neopixel_data)
stemma_qt(vcc, gnd, sda, scl)
castellated_headers(vcc, gnd, vbus, gpio_nets)
buttons(gnd, boot_net, Net("RST_HDR"))

# Generate schematic
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
