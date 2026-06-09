"""
Feather RP2040 - SKiDL Circuit Description
Adafruit Feather RP2040 based on Raspberry Pi RP2040 in Feather form factor.

Key components:
- RP2040 dual-core Cortex-M0+ MCU (QFN-56)
- W25Q16JVSS 16Mbit QSPI Flash
- AP2112K-3.3 LDO regulator (USB 5V -> 3.3V)
- MCP73831 LiPo battery charger
- USB-C connector
- WS2812B NeoPixel LED
- User LED (GPIO13)
- Reset & Bootsel buttons
- Feather header pins (12+16)
- 12MHz crystal for RP2040
- Battery management with Schottky diode and PMOS power switching
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# =============================================================================
# Power nets
# =============================================================================
vbus = Net("VBUS"); vbus.drive = POWER
vbat = Net("VBAT"); vbat.drive = POWER
vsys = Net("VSYS"); vsys.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Signal nets used across subcircuits
usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")
run_net = Net("RUN")
qspi_ss_net = Net("QSPI_SS")

# GPIO signal nets (shared between MCU and headers/UI)
gpio0 = Net("TX")
gpio1 = Net("RX")
gpio2 = Net("SDA")
gpio3 = Net("SCL")
gpio4 = Net("D4")
gpio5 = Net("D5")
gpio6 = Net("D6")
gpio7 = Net("D7")
gpio8 = Net("D8")
gpio9 = Net("D9")
gpio10 = Net("D10")
gpio11 = Net("D11")
gpio12 = Net("D12")
gpio13 = Net("D13")
gpio14 = Net("D14")
gpio15 = Net("D15")
gpio16 = Net("NEOPIXEL")
gpio17 = Net("NEOPIXEL_PWR")
gpio18 = Net("SCK")
gpio19 = Net("MOSI")
gpio20 = Net("MISO")
gpio21 = Net("D21")
gpio22 = Net("D22")
gpio23 = Net("D23")
gpio24 = Net("D24")
gpio25 = Net("D25")
gpio26 = Net("A0")
gpio27 = Net("A1")
gpio28 = Net("A2")
gpio29 = Net("A3")
swclk = Net("SWCLK")
swdio = Net("SWDIO")

# =============================================================================
# USB-C Connector subcircuit
# =============================================================================
@subcircuit
def usb_c_connector(vbus_net, dp_net, dm_net, gnd_net):
    """USB-C connector with CC resistors for device mode."""
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P", dest=NETLIST,
               footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11")

    # Power
    usb["VBUS"] += vbus_net
    usb["GND"] += gnd_net
    usb["SHIELD"] += gnd_net

    # Data lines
    usb["D+"] += dp_net
    usb["D-"] += dm_net

    # CC resistors for UFP (device) identification - 5.1k to GND
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    usb["CC1"] += r_cc1[1]
    r_cc1[2] += gnd_net
    usb["CC2"] += r_cc2[1]
    r_cc2[2] += gnd_net

    # SBU pins not connected
    sbu1_nc = Net("SBU1_NC")
    sbu2_nc = Net("SBU2_NC")
    usb["SBU1"] += sbu1_nc
    usb["SBU2"] += sbu2_nc

usb_c_connector(vbus, usb_dp, usb_dm, gnd)

# =============================================================================
# Power management subcircuit
# =============================================================================
@subcircuit
def power_management(vbus_net, vbat_net, vsys_net, v3v3_net, gnd_net):
    """
    Battery charging (MCP73831) and power path management.
    PMOS switch selects between USB and battery power.
    AP2112K-3.3 regulates VSYS to 3.3V.
    """
    # --- MCP73831 LiPo charger ---
    charger = Part("Battery_Management", "MCP73831-2-OT", value="MCP73831",
                   footprint="Package_TO_SOT_SMD:SOT-23-5")
    charger["V_{DD}"] += vbus_net
    charger["V_{SS}"] += gnd_net
    charger["V_{BAT}"] += vbat_net

    # PROG resistor sets charge current: 2K = 500mA
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    charger["PROG"] += r_prog[1]
    r_prog[2] += gnd_net

    # Charge status LED (active low)
    chg_stat = Net("CHG_STAT")
    charger["STAT"] += chg_stat
    r_chg_led = Part("Device", "R", value="1K",
                     footprint="Resistor_SMD:R_0402_1005Metric")
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    chg_stat += r_chg_led[1]
    r_chg_led[2] += led_chg["A"]
    led_chg["K"] += gnd_net

    # Battery input cap
    c_bat = Part("Device", "C", value="4.7uF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bat[1] += vbat_net
    c_bat[2] += gnd_net

    # --- Power path: Schottky diode OR from VBUS, PMOS switch from VBAT ---
    # Schottky diode: VBUS -> VSYS
    d_usb = Part("Device", "D_Schottky", value="MBR120",
                 footprint="Diode_SMD:D_SOD-123")
    d_usb["A"] += vbus_net
    d_usb["K"] += vsys_net

    # PMOS: Source=VBAT, Drain=VSYS, Gate=VBUS
    q_pwr = Part("Transistor_FET", "AO3401A", value="AO3401A",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_pwr["S"] += vbat_net
    q_pwr["D"] += vsys_net
    q_pwr["G"] += vbus_net

    # Gate pulldown resistor
    r_gate = Part("Device", "R", value="100K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_gate[1] += vbus_net
    r_gate[2] += gnd_net

    # VSYS bulk cap
    c_vsys = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vsys[1] += vsys_net
    c_vsys[2] += gnd_net

    # --- AP2112K-3.3 LDO: VSYS -> 3.3V ---
    reg = Part("Regulator_Linear", "AP2112K-3.3", value="AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"] += vsys_net
    reg["GND"] += gnd_net
    reg["EN"] += vsys_net  # Always enabled
    reg["VOUT"] += v3v3_net

    # Input cap
    c_reg_in = Part("Device", "C", value="1uF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_reg_in[1] += vsys_net
    c_reg_in[2] += gnd_net

    # Output cap
    c_reg_out = Part("Device", "C", value="1uF",
                     footprint="Capacitor_SMD:C_0402_1005Metric")
    c_reg_out[1] += v3v3_net
    c_reg_out[2] += gnd_net

power_management(vbus, vbat, vsys, v3v3, gnd)

# =============================================================================
# RP2040 MCU subcircuit
# =============================================================================
@subcircuit
def rp2040_mcu(v3v3_net, gnd_net, usb_dp_net, usb_dm_net,
               run_n, qspi_ss_n,
               g0, g1, g2, g3, g4, g5, g6, g7, g8, g9,
               g10, g11, g12, g13, g14, g15, g16, g17,
               g18, g19, g20, g21, g22, g23, g24, g25,
               g26, g27, g28, g29, swclk_n, swdio_n):
    """RP2040 microcontroller with crystal, decoupling, and flash."""

    mcu = Part("MCU_RaspberryPi", "RP2040", value="RP2040",
               footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm")

    # --- Power connections ---
    mcu["IOVDD"] += v3v3_net
    mcu["USB_VDD"] += v3v3_net
    mcu["VREG_VIN"] += v3v3_net

    # ADC_AVDD with RC filter
    adc_avdd_net = Net("ADC_AVDD")
    mcu["ADC_AVDD"] += adc_avdd_net
    r_adc = Part("Device", "R", value="0R",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    r_adc[1] += v3v3_net
    r_adc[2] += adc_avdd_net
    c_adc[1] += adc_avdd_net
    c_adc[2] += gnd_net

    # Internal 1.1V regulator output -> DVDD
    vreg_out = Net("VREG_1V1")
    mcu["VREG_VOUT"] += vreg_out
    mcu["DVDD"] += vreg_out

    # VREG output cap
    c_vreg = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vreg[1] += vreg_out
    c_vreg[2] += gnd_net

    # GND
    mcu["GND"] += gnd_net

    # TESTEN -> GND
    mcu["TESTEN"] += gnd_net

    # --- Decoupling caps (100nF each) ---
    # 6x for IOVDD + 1x for DVDD + 1x for USB_VDD = 8 decoupling caps
    for _i in range(8):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v3v3_net
        c[2] += gnd_net

    # --- 12MHz Crystal ---
    xin_net = Net("XIN")
    xout_net = Net("XOUT")
    mcu["XIN"] += xin_net
    mcu["XOUT"] += xout_net

    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xin_net
    xtal[2] += xout_net

    # Crystal load caps
    c_x1 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += xin_net; c_x1[2] += gnd_net
    c_x2[1] += xout_net; c_x2[2] += gnd_net

    # --- USB data ---
    mcu["USB_DP"] += usb_dp_net
    mcu["USB_DM"] += usb_dm_net

    # --- QSPI Flash (W25Q16JVSS) ---
    flash = Part("Memory_Flash", "W25Q16JVSS", value="W25Q16JVSS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")

    qspi_sclk = Net("QSPI_SCLK")
    qspi_sd0 = Net("QSPI_SD0")
    qspi_sd1 = Net("QSPI_SD1")
    qspi_sd2 = Net("QSPI_SD2")
    qspi_sd3 = Net("QSPI_SD3")

    mcu["QSPI_SCLK"] += qspi_sclk
    mcu["QSPI_SD0"] += qspi_sd0
    mcu["QSPI_SD1"] += qspi_sd1
    mcu["QSPI_SD2"] += qspi_sd2
    mcu["QSPI_SD3"] += qspi_sd3
    mcu["~{QSPI_SS}"] += qspi_ss_n

    flash["CLK"] += qspi_sclk
    flash["DI/IO_{0}"] += qspi_sd0
    flash["DO/IO_{1}"] += qspi_sd1
    flash["~{WP}/IO_{2}"] += qspi_sd2
    flash["~{HOLD}/~{RESET}/IO_{3}"] += qspi_sd3
    flash["~{CS}"] += qspi_ss_n
    flash["VCC"] += v3v3_net
    flash["GND"] += gnd_net

    # Flash decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += v3v3_net
    c_flash[2] += gnd_net

    # --- RUN pin with pullup ---
    mcu["RUN"] += run_n
    r_run = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_run[1] += v3v3_net
    r_run[2] += run_n

    # --- SWD debug ---
    mcu["SWCLK"] += swclk_n
    mcu["SWDIO"] += swdio_n

    # --- GPIO connections ---
    mcu["GPIO0"] += g0
    mcu["GPIO1"] += g1
    mcu["GPIO2"] += g2
    mcu["GPIO3"] += g3
    mcu["GPIO4"] += g4
    mcu["GPIO5"] += g5
    mcu["GPIO6"] += g6
    mcu["GPIO7"] += g7
    mcu["GPIO8"] += g8
    mcu["GPIO9"] += g9
    mcu["GPIO10"] += g10
    mcu["GPIO11"] += g11
    mcu["GPIO12"] += g12
    mcu["GPIO13"] += g13
    mcu["GPIO14"] += g14
    mcu["GPIO15"] += g15
    mcu["GPIO16"] += g16
    mcu["GPIO17"] += g17
    mcu["GPIO18"] += g18
    mcu["GPIO19"] += g19
    mcu["GPIO20"] += g20
    mcu["GPIO21"] += g21
    mcu["GPIO22"] += g22
    mcu["GPIO23"] += g23
    mcu["GPIO24"] += g24
    mcu["GPIO25"] += g25
    mcu["GPIO26/ADC0"] += g26
    mcu["GPIO27/ADC1"] += g27
    mcu["GPIO28/ADC2"] += g28
    mcu["GPIO29/ADC3"] += g29

rp2040_mcu(v3v3, gnd, usb_dp, usb_dm, run_net, qspi_ss_net,
           gpio0, gpio1, gpio2, gpio3, gpio4, gpio5, gpio6, gpio7, gpio8, gpio9,
           gpio10, gpio11, gpio12, gpio13, gpio14, gpio15, gpio16, gpio17,
           gpio18, gpio19, gpio20, gpio21, gpio22, gpio23, gpio24, gpio25,
           gpio26, gpio27, gpio28, gpio29, swclk, swdio)

# =============================================================================
# User interface subcircuit (LEDs, buttons, NeoPixel)
# =============================================================================
@subcircuit
def user_interface(v3v3_net, gnd_net, neo_din, led13_net, run_n, qspi_ss_n):
    """NeoPixel, user LED, reset button, bootsel button."""

    # --- WS2812B NeoPixel ---
    neopixel = Part("LED", "WS2812B", value="WS2812B",
                    footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    neopixel["VDD"] += v3v3_net
    neopixel["VSS"] += gnd_net
    neopixel["DIN"] += neo_din

    # NeoPixel DOUT left unconnected (single pixel)
    neo_dout = Net("NEO_DOUT")
    neopixel["DOUT"] += neo_dout

    # NeoPixel decoupling
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += v3v3_net
    c_neo[2] += gnd_net

    # --- User LED on GPIO13 ---
    r_led = Part("Device", "R", value="470R",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led_user = Part("Device", "LED", value="RED",
                    footprint="LED_SMD:LED_0603_1608Metric")
    led13_net += r_led[1]
    r_led[2] += led_user["A"]
    led_user["K"] += gnd_net

    # --- Reset button (active low, pulls RUN to GND) ---
    sw_reset = Part("Switch", "SW_Push", value="RESET",
                    footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_reset[1] += run_n
    sw_reset[2] += gnd_net

    # --- BOOTSEL button (pulls QSPI_SS low) ---
    sw_boot = Part("Switch", "SW_Push", value="BOOTSEL",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_boot[1] += qspi_ss_n
    sw_boot[2] += gnd_net

user_interface(v3v3, gnd, gpio16, gpio13, run_net, qspi_ss_net)

# =============================================================================
# Feather headers subcircuit
# =============================================================================
@subcircuit
def feather_headers(v3v3_net, gnd_net, vbus_net, vbat_net,
                    run_n,
                    g0, g1, g2, g3, g6, g7, g8, g9,
                    g10, g11, g12, g13,
                    g18, g19, g20,
                    g24, g25, g26, g27, g28, g29):
    """
    Feather form factor headers:
    - 16-pin left header: RST, 3V3, AREF, GND, A0-A3, D24, D25, SCK, MOSI, MISO, RX, TX, D4
    - 12-pin right header: BAT, EN, USB, D13-D9, D6-D5, SCL, SDA
    """
    # Left header (16 pins)
    hdr_left = Part("Connector_Generic", "Conn_01x16",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    aref = Net("AREF")
    hdr_left[1] += run_n
    hdr_left[2] += v3v3_net
    hdr_left[3] += aref
    hdr_left[4] += gnd_net
    hdr_left[5] += g26      # A0
    hdr_left[6] += g27      # A1
    hdr_left[7] += g28      # A2
    hdr_left[8] += g29      # A3
    hdr_left[9] += g24      # D24
    hdr_left[10] += g25     # D25
    hdr_left[11] += g18     # SCK
    hdr_left[12] += g19     # MOSI
    hdr_left[13] += g20     # MISO
    hdr_left[14] += g1      # RX
    hdr_left[15] += g0      # TX
    hdr_left[16] += g6      # D4/D6

    # Right header (12 pins)
    hdr_right = Part("Connector_Generic", "Conn_01x12",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    en_net = Net("EN")
    hdr_right[1] += vbat_net
    hdr_right[2] += en_net
    hdr_right[3] += vbus_net
    hdr_right[4] += g13     # D13
    hdr_right[5] += g12     # D12
    hdr_right[6] += g11     # D11
    hdr_right[7] += g10     # D10
    hdr_right[8] += g9      # D9
    hdr_right[9] += g8      # D6/D8
    hdr_right[10] += g7     # D5/D7
    hdr_right[11] += g3     # SCL
    hdr_right[12] += g2     # SDA

    # --- JST PH battery connector ---
    jst_bat = Part("Connector_Generic", "Conn_01x02",
                   footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    jst_bat[1] += vbat_net
    jst_bat[2] += gnd_net

feather_headers(v3v3, gnd, vbus, vbat,
                run_net,
                gpio0, gpio1, gpio2, gpio3, gpio6, gpio7, gpio8, gpio9,
                gpio10, gpio11, gpio12, gpio13,
                gpio18, gpio19, gpio20,
                gpio24, gpio25, gpio26, gpio27, gpio28, gpio29)

# =============================================================================
# SWD debug header
# =============================================================================
@subcircuit
def swd_header(v3v3_net, gnd_net, swclk_n, swdio_n):
    """SWD debug header."""
    swd_hdr = Part("Connector_Generic", "Conn_01x04",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    swd_hdr[1] += v3v3_net
    swd_hdr[2] += swclk_n
    swd_hdr[3] += swdio_n
    swd_hdr[4] += gnd_net

swd_header(v3v3, gnd, swclk, swdio)

# =============================================================================
# STEMMA QT / Qwiic I2C connector (JST SH 4-pin)
# =============================================================================
@subcircuit
def stemma_qt(v3v3_net, gnd_net, sda_net, scl_net):
    """STEMMA QT / Qwiic I2C connector."""
    qt = Part("Connector_Generic", "Conn_01x04",
              footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    qt[1] += gnd_net
    qt[2] += sda_net
    qt[3] += scl_net
    qt[4] += v3v3_net

stemma_qt(v3v3, gnd, gpio2, gpio3)

# =============================================================================
# Generate schematic
# =============================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=16)
