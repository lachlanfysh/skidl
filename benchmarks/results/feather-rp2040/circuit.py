"""
Feather RP2040 -- SKiDL circuit description.

Adafruit Feather RP2040 built around the Raspberry Pi RP2040 chip
(Dual Cortex-M0+ @ 133MHz). Features USB-C, 8MB QSPI flash (GD25Q64),
LiPo charging (MCP73831), AP2112K-3.3 LDO, NeoPixel status LED,
STEMMA QT / Qwiic I2C connector, 12MHz crystal, and standard Feather
headers (16-pin + 12-pin).
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# ── Power Nets ──────────────────────────────────────────────────
vbus = Net("VBUS"); vbus.drive = POWER        # USB 5V
vbat = Net("VBAT"); vbat.drive = POWER        # Battery / regulated 5V
v3v3 = Net("+3V3"); v3v3.drive = POWER        # 3.3V rail
gnd  = Net("GND");  gnd.drive  = POWER        # Ground

# ── Signal Nets ─────────────────────────────────────────────────
usb_dp   = Net("USB_DP")
usb_dm   = Net("USB_DM")
neopixel = Net("NEOPIXEL")
reset_n  = Net("RESET_N")
swdio    = Net("SWDIO")
swdclk   = Net("SWDCLK")
charge_stat = Net("CHG_STAT")

# QSPI flash bus
qspi_sclk = Net("QSPI_SCLK")
qspi_sd0  = Net("QSPI_SD0")
qspi_sd1  = Net("QSPI_SD1")
qspi_sd2  = Net("QSPI_SD2")
qspi_sd3  = Net("QSPI_SD3")
qspi_ss   = Net("QSPI_SS")

# I2C bus
sda = Net("SDA")
scl = Net("SCL")

# UART
uart_tx = Net("TX")
uart_rx = Net("RX")

# SPI (exposed on headers)
spi_sck  = Net("SPI_SCK")
spi_mosi = Net("SPI_MOSI")
spi_miso = Net("SPI_MISO")

# Analog inputs (ADC on RP2040)
a0 = Net("A0")
a1 = Net("A1")
a2 = Net("A2")
a3 = Net("A3")

# Digital pins
d4  = Net("D4")
d5  = Net("D5")
d6  = Net("D6")
d9  = Net("D9")
d10 = Net("D10")
d11 = Net("D11")
d12 = Net("D12")
d13 = Net("D13")
d24 = Net("D24")
d25 = Net("D25")


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: USB-C Input
# ═══════════════════════════════════════════════════════════════
@subcircuit
def usb_input(vbus, gnd, usb_dp, usb_dm):
    """USB-C connector with CC resistors for UFP detection."""

    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
               footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
               value="USB-C")
    usb["VBUS"]   += vbus
    usb["GND"]    += gnd
    usb["D+"]     += usb_dp
    usb["D-"]     += usb_dm
    usb["SHIELD"] += gnd
    usb["SBU1"]   += NC
    usb["SBU2"]   += NC

    # CC1/CC2 pull-down resistors (5.1K for UFP / device role)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["CC1"]
    r_cc1[2] += gnd

    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2[1] += usb["CC2"]
    r_cc2[2] += gnd

    # USB input bulk capacitor
    c_usb = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_usb[1] += vbus
    c_usb[2] += gnd


usb_input(vbus, gnd, usb_dp, usb_dm)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: LiPo Battery Charger (MCP73831)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def lipo_charger(vbus, vbat, gnd, charge_stat):
    """MCP73831 single-cell LiPo charger with status LED."""

    chg = Part("Battery_Management", "MCP73831-2-OT",
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               value="MCP73831")
    chg["V_{DD}"]  += vbus
    chg["V_{SS}"]  += gnd
    chg["V_{BAT}"] += vbat
    chg["STAT"]    += charge_stat

    # Charge current programming resistor (2K = ~500mA)
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    chg["PROG"] += r_prog[1]
    r_prog[2]   += gnd

    # Charge status LED (orange)
    led_chg = Part("Device", "LED", value="Orange",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_chg_led = Part("Device", "R", value="1K",
                     footprint="Resistor_SMD:R_0603_1608Metric")
    charge_stat += r_chg_led[1]
    r_chg_led[2] += led_chg[1]   # Cathode
    led_chg[2]   += vbus          # Anode — LED is active low from STAT

    # Battery connector (JST PH 2-pin)
    j_bat = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
                 value="BATT")
    j_bat[1] += vbat
    j_bat[2] += gnd

    # Schottky diode for USB/battery power ORing
    d_usb = Part("Device", "D_Schottky", value="MBR120",
                 footprint="Diode_SMD:D_SOD-123")
    d_usb[1] += vbat   # Cathode — to battery rail
    d_usb[2] += vbus   # Anode — from USB VBUS

    # Decoupling cap for charger
    c_chg = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_chg[1] += vbus
    c_chg[2] += gnd

    # Battery output bulk cap
    c_bat = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bat[1] += vbat
    c_bat[2] += gnd


lipo_charger(vbus, vbat, gnd, charge_stat)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: 3.3V Voltage Regulator (AP2112K-3.3)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def voltage_regulator(vbat, v3v3, gnd):
    """AP2112K-3.3 LDO regulator with bypass caps."""

    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               value="AP2112K-3.3")
    reg["VIN"]  += vbat
    reg["VOUT"] += v3v3
    reg["GND"]  += gnd
    reg["EN"]   += vbat  # Always enabled

    # Input decoupling
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vbat
    c_in[2] += gnd

    # Output decoupling
    c_out1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out1[1] += v3v3
    c_out1[2] += gnd

    c_out2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out2[1] += v3v3
    c_out2[2] += gnd


voltage_regulator(vbat, v3v3, gnd)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: RP2040 Microcontroller
# ═══════════════════════════════════════════════════════════════
@subcircuit
def mcu_rp2040(v3v3, gnd, usb_dp, usb_dm, reset_n, swdio, swdclk,
               qspi_sclk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3, qspi_ss,
               neopixel, sda, scl, uart_tx, uart_rx,
               spi_sck, spi_mosi, spi_miso,
               a0, a1, a2, a3,
               d4, d5, d6, d9, d10, d11, d12, d13, d24, d25):
    """RP2040 MCU with decoupling, crystal, and flash connections."""

    mcu = Part("MCU_RaspberryPi", "RP2040",
               footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm",
               value="RP2040")

    # ── Power connections ────────────────────────────────
    # IOVDD — 6 pins, all to 3.3V
    mcu["IOVDD"]    += v3v3

    # DVDD — internal core supply, connected via regulator output
    dvdd = Net("DVDD"); dvdd.drive = POWER
    mcu["DVDD"]     += dvdd

    # On-chip voltage regulator
    mcu["VREG_VIN"]  += v3v3     # Regulator input from 3.3V
    mcu["VREG_VOUT"] += dvdd     # Regulator output to DVDD

    # USB and ADC power
    mcu["USB_VDD"]   += v3v3
    mcu["ADC_AVDD"]  += v3v3

    # Ground (exposed pad)
    mcu["GND"]       += gnd

    # TESTEN — tie to ground
    mcu["TESTEN"]    += gnd

    # DVDD decoupling (one per DVDD pin)
    c_dvdd1 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dvdd1[1] += dvdd
    c_dvdd1[2] += gnd

    # VREG_VOUT inductor-less: 1uF cap
    c_vreg = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vreg[1] += dvdd
    c_vreg[2] += gnd

    # IOVDD decoupling caps (100nF per IOVDD group)
    for i in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += v3v3
        c[2] += gnd

    # USB_VDD decoupling
    c_usbv = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_usbv[1] += v3v3
    c_usbv[2] += gnd

    # ADC_AVDD decoupling
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_adc[1] += v3v3
    c_adc[2] += gnd

    # Bulk decoupling
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

    # ── USB ──────────────────────────────────────────────
    # RP2040 has internal USB series resistors — direct connection
    mcu["USB_DM"] += usb_dm
    mcu["USB_DP"] += usb_dp

    # ── 12MHz Crystal ────────────────────────────────────
    xin_net  = Net("XIN")
    xout_net = Net("XOUT")
    mcu["XIN"]  += xin_net
    mcu["XOUT"] += xout_net

    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xin_net
    xtal[2] += xout_net

    # Crystal load capacitors
    c_x1 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += xin_net
    c_x1[2] += gnd

    c_x2 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2[1] += xout_net
    c_x2[2] += gnd

    # ── Reset ────────────────────────────────────────────
    mcu["RUN"] += reset_n

    # Reset pull-up
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += v3v3
    r_rst[2] += reset_n

    # Reset button
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                  value="RESET")
    sw_rst[1] += reset_n
    sw_rst[2] += gnd

    # Bootloader button (active-low on GPIO for BOOTSEL on Feather RP2040)
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                   value="BOOTSEL")
    boot_net = Net("BOOTSEL")
    sw_boot[1] += boot_net
    sw_boot[2] += gnd

    # Pull-up for bootsel
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_boot[1] += v3v3
    r_boot[2] += boot_net

    # ── SWD Debug ────────────────────────────────────────
    mcu["SWCLK"] += swdclk
    mcu["SWDIO"] += swdio

    # ── QSPI Flash ───────────────────────────────────────
    mcu["QSPI_SCLK"]    += qspi_sclk
    mcu["QSPI_SD0"]     += qspi_sd0
    mcu["QSPI_SD1"]     += qspi_sd1
    mcu["QSPI_SD2"]     += qspi_sd2
    mcu["QSPI_SD3"]     += qspi_sd3
    mcu["~{QSPI_SS}"]   += qspi_ss

    # ── NeoPixel (GPIO16) ────────────────────────────────
    mcu["GPIO16"] += neopixel

    # ── I2C (GPIO2=SDA, GPIO3=SCL) ──────────────────────
    mcu["GPIO2"] += sda
    mcu["GPIO3"] += scl

    # ── UART (GPIO0=TX, GPIO1=RX) ───────────────────────
    mcu["GPIO0"] += uart_tx
    mcu["GPIO1"] += uart_rx

    # ── SPI (GPIO18=SCK, GPIO19=MOSI, GPIO20=MISO) ─────
    mcu["GPIO18"] += spi_sck
    mcu["GPIO19"] += spi_mosi
    mcu["GPIO20"] += spi_miso

    # ── Analog Inputs ────────────────────────────────────
    mcu["GPIO26/ADC0"] += a0
    mcu["GPIO27/ADC1"] += a1
    mcu["GPIO28/ADC2"] += a2
    mcu["GPIO29/ADC3"] += a3

    # ── Digital I/O ──────────────────────────────────────
    mcu["GPIO4"]  += d4
    mcu["GPIO5"]  += d5
    mcu["GPIO6"]  += d6
    mcu["GPIO7"]  += d9    # D9 mapped to GPIO7
    mcu["GPIO8"]  += d10   # D10 mapped to GPIO8
    mcu["GPIO9"]  += d11   # D11 mapped to GPIO9
    mcu["GPIO10"] += d12   # D12 mapped to GPIO10
    mcu["GPIO11"] += d13   # D13 mapped to GPIO11
    mcu["GPIO24"] += d24
    mcu["GPIO25"] += d25

    # ── User LED (D13 / GPIO11) ──────────────────────────
    led_d13 = Part("Device", "LED", value="Red",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_d13 = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    d13 += r_d13[1]
    r_d13[2] += led_d13[2]    # Resistor to Anode
    led_d13[1] += gnd          # Cathode to GND

    # ── Unused GPIOs — connect to avoid ERC warnings ─────
    mcu["GPIO12"] += NC
    mcu["GPIO13"] += NC
    mcu["GPIO14"] += NC
    mcu["GPIO15"] += NC
    mcu["GPIO17"] += NC
    mcu["GPIO21"] += NC
    mcu["GPIO22"] += NC
    mcu["GPIO23"] += NC


mcu_rp2040(v3v3, gnd, usb_dp, usb_dm, reset_n, swdio, swdclk,
           qspi_sclk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3, qspi_ss,
           neopixel, sda, scl, uart_tx, uart_rx,
           spi_sck, spi_mosi, spi_miso,
           a0, a1, a2, a3,
           d4, d5, d6, d9, d10, d11, d12, d13, d24, d25)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: QSPI Flash (GD25Q64 / W25Q64 — 8MB)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def qspi_flash(v3v3, gnd, qspi_sclk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3, qspi_ss):
    """8MB QSPI NOR flash for CircuitPython filesystem."""

    # Using W25Q32JVSS symbol (pin-compatible with GD25Q64)
    flash = Part("Memory_Flash", "W25Q32JVSS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 value="GD25Q64")
    flash["VCC"]                      += v3v3
    flash["GND"]                      += gnd
    flash["~{CS}"]                    += qspi_ss
    flash["CLK"]                      += qspi_sclk
    flash["DI/IO_{0}"]               += qspi_sd0
    flash["DO/IO_{1}"]               += qspi_sd1
    flash["~{WP}/IO_{2}"]            += qspi_sd2
    flash["~{HOLD}/~{RESET}/IO_{3}"] += qspi_sd3

    # Decoupling cap
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_flash[1] += v3v3
    c_flash[2] += gnd


qspi_flash(v3v3, gnd, qspi_sclk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3, qspi_ss)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: NeoPixel RGB LED
# ═══════════════════════════════════════════════════════════════
@subcircuit
def neopixel_led(v3v3, gnd, neopixel):
    """Single WS2812B addressable RGB LED."""

    neo = Part("LED", "SK6812",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
               value="NeoPixel")
    neo["VDD"]  += v3v3
    neo["VSS"]  += gnd
    neo["DIN"]  += neopixel
    neo["DOUT"] += NC       # Single LED, no chain

    # Decoupling cap
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_neo[1] += v3v3
    c_neo[2] += gnd


neopixel_led(v3v3, gnd, neopixel)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: I2C Pull-ups and STEMMA QT Connector
# ═══════════════════════════════════════════════════════════════
@subcircuit
def i2c_stemma(v3v3, gnd, sda, scl):
    """I2C bus pull-ups and STEMMA QT / Qwiic JST-SH connector."""

    # I2C pull-up resistors
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += v3v3
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += v3v3
    r_scl[2] += scl

    # STEMMA QT connector (JST SH 4-pin: GND, VCC, SDA, SCL)
    j_stemma = Part("Connector_Generic", "Conn_01x04",
                    footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
                    value="STEMMA_QT")
    j_stemma[1] += gnd
    j_stemma[2] += v3v3
    j_stemma[3] += sda
    j_stemma[4] += scl


i2c_stemma(v3v3, gnd, sda, scl)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Feather Headers
# ═══════════════════════════════════════════════════════════════
@subcircuit
def feather_headers(v3v3, vbat, vbus, gnd, reset_n,
                    a0, a1, a2, a3,
                    sda, scl, d4, d5, d6, d9,
                    d10, d11, d12, d13,
                    uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck,
                    d24, d25):
    """Standard Feather form-factor headers (16-pin + 12-pin)."""

    # Left header (16 pins) — matches Adafruit Feather pinout
    j_left = Part("Connector_Generic", "Conn_01x16",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical",
                  value="HEADER_L")
    j_left[1]  += reset_n
    j_left[2]  += v3v3
    j_left[3]  += NC        # AREF
    j_left[4]  += gnd
    j_left[5]  += a0
    j_left[6]  += a1
    j_left[7]  += a2
    j_left[8]  += a3
    j_left[9]  += d24
    j_left[10] += d25
    j_left[11] += sda
    j_left[12] += scl
    j_left[13] += d5
    j_left[14] += d6
    j_left[15] += d9
    j_left[16] += d10

    # Right header (12 pins)
    j_right = Part("Connector_Generic", "Conn_01x12",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical",
                   value="HEADER_R")
    j_right[1]  += vbat
    j_right[2]  += NC       # EN (enable)
    j_right[3]  += vbus
    j_right[4]  += d13
    j_right[5]  += d12
    j_right[6]  += d11
    j_right[7]  += d10
    j_right[8]  += d9
    j_right[9]  += d4
    j_right[10] += uart_rx
    j_right[11] += uart_tx
    j_right[12] += spi_miso


feather_headers(v3v3, vbat, vbus, gnd, reset_n,
                a0, a1, a2, a3,
                sda, scl, d4, d5, d6, d9,
                d10, d11, d12, d13,
                uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck,
                d24, d25)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: SWD Debug Header
# ═══════════════════════════════════════════════════════════════
@subcircuit
def swd_header(v3v3, gnd, swdio, swdclk, reset_n):
    """SWD debug header."""

    j_swd = Part("Connector_Generic", "Conn_01x05",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
                 value="SWD")
    j_swd[1] += v3v3
    j_swd[2] += gnd
    j_swd[3] += swdio
    j_swd[4] += swdclk
    j_swd[5] += reset_n


swd_header(v3v3, gnd, swdio, swdclk, reset_n)


# ═══════════════════════════════════════════════════════════════
#  Generate Schematic
# ═══════════════════════════════════════════════════════════════
generate_schematic(auto_stub=True)
