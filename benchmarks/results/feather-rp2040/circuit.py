"""
Feather RP2040 -- SKiDL circuit description.

Adafruit Feather RP2040 built around the Raspberry Pi RP2040 chip
(Dual Cortex-M0+ @ 133MHz). Features USB-C, 8MB QSPI flash (W25Q64JV via LCSC C179171),
LiPo charging (MCP73831), AP2112K-3.3 LDO, WS2812B NeoPixel status LED,
STEMMA QT I2C connector, 12MHz crystal, SWD debug header, and standard Feather
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
vbat = Net("VBAT"); vbat.drive = POWER        # Battery / regulated 5V rail
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

    # ESD protection on VBUS (TVS diode)
    tvs = Part("Device", "D_Zener", value="5.1V_TVS",
               footprint="Diode_SMD:D_SOD-123")
    tvs[1] += gnd
    tvs[2] += vbus

    usb.edge_preference = "bottom"


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

    # Charge status LED (yellow/orange)
    led_chg = Part("Device", "LED", value="Orange",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_chg_led = Part("Device", "R", value="1K",
                     footprint="Resistor_SMD:R_0603_1608Metric")
    charge_stat += r_chg_led[1]
    r_chg_led[2] += led_chg["A"]   # Resistor to Anode
    led_chg["K"]  += gnd            # Cathode to GND (STAT is open-drain)

    # Battery connector (JST PH 2-pin vertical, standard Feather)
    j_bat = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
                 value="BATT")
    j_bat[1] += vbat
    j_bat[2] += gnd

    # Schottky diode for USB/battery power OR-ing
    d_usb = Part("Device", "D_Schottky", value="MBR120",
                 footprint="Diode_SMD:D_SOD-123")
    d_usb["A"] += vbus   # Anode from USB VBUS
    d_usb["K"] += vbat   # Cathode to battery/supply rail

    # Decoupling cap for charger VDD
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
    reg["NC"]   += NC

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
    # IOVDD — multiple pins, all to 3.3V
    mcu["IOVDD"]    += v3v3

    # DVDD — internal core supply, generated by on-chip regulator
    dvdd = Net("DVDD"); dvdd.drive = POWER
    mcu["DVDD"]     += dvdd

    # On-chip voltage regulator (use pin numbers: pin 44=VREG_VIN, pin 45=VREG_VOUT)
    mcu[44] += v3v3     # VREG_VIN — regulator input from 3.3V
    mcu[45] += dvdd     # VREG_VOUT — regulator output to DVDD

    # USB and ADC power (pin 48=USB_VDD, pin 43=ADC_AVDD)
    mcu[48] += v3v3   # USB_VDD
    mcu[43] += v3v3   # ADC_AVDD

    # Ground (exposed pad, pin 57)
    mcu["GND"]       += gnd

    # TESTEN — tie to ground (pin 19)
    mcu[19] += gnd  # TESTEN

    # DVDD decoupling (per RP2040 datasheet: 100nF per DVDD pin)
    c_dvdd1 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dvdd1[1] += dvdd
    c_dvdd1[2] += gnd

    c_dvdd2 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dvdd2[1] += dvdd
    c_dvdd2[2] += gnd

    # VREG_VOUT filter cap (1uF per datasheet)
    c_vreg = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vreg[1] += dvdd
    c_vreg[2] += gnd

    # DVDD bulk cap
    c_dvdd_bulk = Part("Device", "C", value="10uF",
                       footprint="Capacitor_SMD:C_0805_2012Metric")
    c_dvdd_bulk[1] += dvdd
    c_dvdd_bulk[2] += gnd

    # IOVDD decoupling caps (100nF per IOVDD group — 6 IOVDD pins)
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v3v3
        c[2] += gnd

    # USB_VDD decoupling
    c_usbv = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usbv[1] += v3v3
    c_usbv[2] += gnd

    # ADC_AVDD decoupling with ferrite bead filter
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_adc[1] += v3v3
    c_adc[2] += gnd

    # Bulk decoupling
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

    # ── USB (pin 46=USB_DM, pin 47=USB_DP) ──────────────
    # RP2040 has internal USB series resistors — direct connection
    mcu[46] += usb_dm   # USB_DM
    mcu[47] += usb_dp   # USB_DP

    # ── 12MHz Crystal (pin 20=XIN, pin 21=XOUT) ─────────
    xin_net  = Net("XIN")
    xout_net = Net("XOUT")
    mcu[20] += xin_net   # XIN
    mcu[21] += xout_net  # XOUT

    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xin_net
    xtal[2] += xout_net

    # Crystal load capacitors (15pF for 12MHz typical)
    c_x1 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += xin_net
    c_x1[2] += gnd

    c_x2 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2[1] += xout_net
    c_x2[2] += gnd

    # ── Reset (pin 26=RUN) ───────────────────────────────
    mcu[26] += reset_n  # RUN

    # Reset pull-up
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += v3v3
    r_rst[2] += reset_n

    # Reset button
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                  value="RESET")
    sw_rst[1] += reset_n
    sw_rst[2] += gnd

    # Bootloader button (BOOTSEL — GPIO via 1-wire on Feather RP2040)
    boot_net = Net("BOOTSEL")
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                   value="BOOTSEL")
    sw_boot[1] += boot_net
    sw_boot[2] += gnd

    # Pull-up for bootsel
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += v3v3
    r_boot[2] += boot_net
    # BOOTSEL is connected to QSPI_SS on Feather RP2040 (reads high = normal boot)
    boot_net += qspi_ss

    # ── SWD Debug (pin 24=SWCLK, pin 25=SWDIO) ──────────
    mcu[24] += swdclk   # SWCLK
    mcu[25] += swdio    # SWDIO

    # ── QSPI Flash (pin numbers per RP2040 datasheet) ──────────
    mcu[52] += qspi_sclk   # QSPI_SCLK
    mcu[53] += qspi_sd0    # QSPI_SD0
    mcu[55] += qspi_sd1    # QSPI_SD1
    mcu[54] += qspi_sd2    # QSPI_SD2
    mcu[51] += qspi_sd3    # QSPI_SD3
    mcu[56] += qspi_ss     # ~QSPI_SS

    # GPIO pin number map (from RP2040 datasheet):
    # pin 2=GPIO0, 3=GPIO1, 4=GPIO2, 5=GPIO3, 6=GPIO4, 7=GPIO5,
    # 8=GPIO6, 9=GPIO7, 11=GPIO8, 12=GPIO9, 13=GPIO10, 14=GPIO11,
    # 15=GPIO12, 16=GPIO13, 17=GPIO14, 18=GPIO15, 27=GPIO16, 28=GPIO17,
    # 29=GPIO18, 30=GPIO19, 31=GPIO20, 32=GPIO21, 34=GPIO22, 35=GPIO23,
    # 36=GPIO24, 37=GPIO25, 38=GPIO26/ADC0, 39=GPIO27/ADC1,
    # 40=GPIO28/ADC2, 41=GPIO29/ADC3

    # ── NeoPixel (GPIO16 = pin 27) ────────────────────────────────
    mcu[27] += neopixel

    # ── I2C (GPIO2=pin 4=SDA, GPIO3=pin 5=SCL) ──────────────────
    mcu[4] += sda    # GPIO2/SDA
    mcu[5] += scl    # GPIO3/SCL

    # ── UART (GPIO0=pin 2=TX, GPIO1=pin 3=RX) ───────────────────
    mcu[2] += uart_tx   # GPIO0/TX
    mcu[3] += uart_rx   # GPIO1/RX

    # ── SPI (GPIO18=pin 29=SCK, GPIO19=pin 30=MOSI, GPIO20=pin 31=MISO)
    mcu[29] += spi_sck    # GPIO18/SCK
    mcu[30] += spi_mosi   # GPIO19/MOSI
    mcu[31] += spi_miso   # GPIO20/MISO

    # ── Analog Inputs ────────────────────────────────────
    mcu[38] += a0    # GPIO26/ADC0
    mcu[39] += a1    # GPIO27/ADC1
    mcu[40] += a2    # GPIO28/ADC2
    mcu[41] += a3    # GPIO29/ADC3

    # ── Digital I/O ──────────────────────────────────────
    mcu[6]  += d4    # GPIO4
    mcu[7]  += d5    # GPIO5
    mcu[8]  += d6    # GPIO6
    mcu[9]  += d9    # GPIO7 → D9
    mcu[11] += d10   # GPIO8 → D10
    mcu[12] += d11   # GPIO9 → D11
    mcu[13] += d12   # GPIO10 → D12
    mcu[14] += d13   # GPIO11 → D13
    mcu[36] += d24   # GPIO24
    mcu[37] += d25   # GPIO25

    # ── User LED (D13 / GPIO11, active-high) ─────────────
    led_d13 = Part("Device", "LED", value="Red",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_d13 = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    d13 += r_d13[1]
    r_d13[2] += led_d13["A"]    # Resistor to Anode
    led_d13["K"] += gnd          # Cathode to GND

    # ── Unused GPIOs (by pin number) ──────────────────────
    mcu[15] += NC   # GPIO12
    mcu[16] += NC   # GPIO13
    mcu[17] += NC   # GPIO14
    mcu[18] += NC   # GPIO15
    mcu[28] += NC   # GPIO17
    mcu[32] += NC   # GPIO21
    mcu[34] += NC   # GPIO22
    mcu[35] += NC   # GPIO23


mcu_rp2040(v3v3, gnd, usb_dp, usb_dm, reset_n, swdio, swdclk,
           qspi_sclk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3, qspi_ss,
           neopixel, sda, scl, uart_tx, uart_rx,
           spi_sck, spi_mosi, spi_miso,
           a0, a1, a2, a3,
           d4, d5, d6, d9, d10, d11, d12, d13, d24, d25)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: QSPI Flash (W25Q64JV 8MB — LCSC C179171)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def qspi_flash(v3v3, gnd, qspi_sclk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3, qspi_ss):
    """8MB QSPI NOR flash (W25Q64JV — using W25Q32JVSS symbol, pin-compatible)."""

    # W25Q32JVSS is pin-compatible with W25Q64JV; override value to indicate actual part
    flash = Part("Memory_Flash", "W25Q32JVSS",
                 footprint="Package_SO:SOIC-8_5.3x5.3mm_P1.27mm",
                 value="W25Q64JV")
    flash["VCC"]                      += v3v3
    flash["GND"]                      += gnd
    flash["~{CS}"]                    += qspi_ss
    flash["CLK"]                      += qspi_sclk
    flash["DI/IO_{0}"]               += qspi_sd0
    flash["DO/IO_{1}"]               += qspi_sd1
    flash["~{WP}/IO_{2}"]            += qspi_sd2
    flash["~{HOLD}/~{RESET}/IO_{3}"] += qspi_sd3

    # Decoupling cap close to flash
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += v3v3
    c_flash[2] += gnd


qspi_flash(v3v3, gnd, qspi_sclk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3, qspi_ss)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: NeoPixel RGB LED (WS2812B)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def neopixel_led(v3v3, gnd, neopixel):
    """Single WS2812B addressable RGB LED."""

    neo = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
               value="WS2812B")
    neo["VDD"]  += v3v3
    neo["VSS"]  += gnd
    neo["DIN"]  += neopixel
    neo["DOUT"] += NC       # Single LED, no chain

    # Decoupling cap (mandatory for WS2812B stability)
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += v3v3
    c_neo[2] += gnd

    # Bulk cap for WS2812B
    c_neo_bulk = Part("Device", "C", value="10uF",
                      footprint="Capacitor_SMD:C_0805_2012Metric")
    c_neo_bulk[1] += v3v3
    c_neo_bulk[2] += gnd


neopixel_led(v3v3, gnd, neopixel)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: I2C Pull-ups and STEMMA QT Connector
# ═══════════════════════════════════════════════════════════════
@subcircuit
def i2c_stemma(v3v3, gnd, sda, scl):
    """I2C bus pull-ups and STEMMA QT / Qwiic JST-SH connector."""

    # I2C pull-up resistors
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v3v3
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
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

    j_stemma.edge_preference = "right"


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

    j_left.edge_preference = "left"

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

    j_right.edge_preference = "right"


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
    """SWD debug header — standard 5-pin 1.27mm."""

    j_swd = Part("Connector_Generic", "Conn_01x05",
                 footprint="Connector_PinHeader_1.27mm:PinHeader_1x05_P1.27mm_Vertical",
                 value="SWD")
    j_swd[1] += v3v3
    j_swd[2] += gnd
    j_swd[3] += swdio
    j_swd[4] += swdclk
    j_swd[5] += reset_n


swd_header(v3v3, gnd, swdio, swdclk, reset_n)


# ═══════════════════════════════════════════════════════════════
#  Board Floorplan — Feather standard 50.8x22.86mm
# ═══════════════════════════════════════════════════════════════
EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 50.8,
        "height_mm": 22.86,
        "corner_radius_mm": 1.5,
    },
    "edge_anchors": [
        {"ref": "J1", "edge": "bottom"},   # USB-C on bottom edge
    ],
}
