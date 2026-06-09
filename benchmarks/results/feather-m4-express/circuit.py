"""
Feather M4 Express SAMD51 — SKiDL circuit description.

Adafruit Feather M4 Express with ATSAMD51J19A-A (120MHz Cortex-M4F),
2MB SPI flash, USB Micro-B, LiPo charging, 3.3V regulation,
NeoPixel status LED, and standard Feather headers.
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
spi_sck  = Net("SPI_SCK")
spi_mosi = Net("SPI_MOSI")
spi_miso = Net("SPI_MISO")
flash_cs = Net("FLASH_CS")
neopixel = Net("NEOPIXEL")
reset_n  = Net("RESET_N")
swdio    = Net("SWDIO")
swdclk   = Net("SWDCLK")
charge_stat = Net("CHG_STAT")

# I2C bus
sda = Net("SDA")
scl = Net("SCL")

# UART
uart_tx = Net("TX")
uart_rx = Net("RX")

# Analog inputs
a0 = Net("A0")
a1 = Net("A1")
a2 = Net("A2")
a3 = Net("A3")
a4 = Net("A4")
a5 = Net("A5")

# Digital pins
d4  = Net("D4")
d5  = Net("D5")
d6  = Net("D6")
d9  = Net("D9")
d10 = Net("D10")
d11 = Net("D11")
d12 = Net("D12")
d13 = Net("D13")


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: USB and Power Input
# ═══════════════════════════════════════════════════════════════
@subcircuit
def usb_power(vbus, gnd, usb_dp, usb_dm):
    """USB Micro-B connector with ESD protection."""

    # USB Micro-B connector
    usb = Part("Connector", "USB_B_Micro",
               footprint="Connector_USB:USB_Micro-B_Molex-105017-0001",
               value="USB_Micro-B")
    usb["VBUS"]  += vbus
    usb["GND"]   += gnd
    usb["D+"]    += usb_dp
    usb["D-"]    += usb_dm
    usb["Shield"] += gnd
    usb["ID"]    += NC  # Not used on device side

    # USB input bulk capacitor
    c_usb = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_usb[1] += vbus
    c_usb[2] += gnd


usb_power(vbus, gnd, usb_dp, usb_dm)


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
    r_chg_led[2] += led_chg[1]  # K (cathode)
    led_chg[2]   += vbus         # A (anode) — LED is active low from STAT

    # Battery connector (JST PH 2-pin)
    j_bat = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
                 value="BATT")
    j_bat[1] += vbat
    j_bat[2] += gnd

    # Schottky diode for USB/battery power ORing
    d_usb = Part("Device", "D_Schottky", value="MBR120",
                 footprint="Diode_SMD:D_SOD-123")
    d_usb[1] += vbat   # K (cathode) — to battery rail
    d_usb[2] += vbus   # A (anode)   — from USB VBUS

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
#  SUBCIRCUIT: SAMD51 Microcontroller
# ═══════════════════════════════════════════════════════════════
@subcircuit
def mcu_samd51(v3v3, gnd, usb_dp, usb_dm, reset_n, swdio, swdclk,
               spi_sck, spi_mosi, spi_miso, flash_cs,
               neopixel, sda, scl, uart_tx, uart_rx,
               a0, a1, a2, a3, a4, a5,
               d4, d5, d6, d9, d10, d11, d12, d13):
    """ATSAMD51J19A-A MCU with decoupling and crystal."""

    mcu = Part("MCU_Microchip_SAMD", "ATSAMD51J19A-A",
               footprint="Package_QFP:TQFP-64_10x10mm_P0.5mm",
               value="ATSAMD51J19A")

    # ── Power connections ────────────────────────────────
    # Multiple VDDIO pins
    mcu["VDDIO"]  += v3v3    # Pins 34, 48, 56
    mcu["VDDIOB"] += v3v3    # Pin 21
    mcu["VDDANA"] += v3v3    # Pin 8
    mcu["VDDCORE"] += NC     # Internal core regulator output — leave NC or decoupled
    mcu["VSW"]    += NC      # Switching regulator — not used, left NC

    # Ground pins
    mcu["GND"]    += gnd     # Pins 22, 33, 47, 54, 65
    mcu["GNDANA"] += gnd     # Pin 7

    # Core voltage decoupling (VDDCORE output)
    vddcore = Net("VDDCORE")
    mcu["VDDCORE"] += vddcore
    c_core = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_core[1] += vddcore
    c_core[2] += gnd

    # VDDIO decoupling caps (one per VDDIO group)
    for i in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += v3v3
        c[2] += gnd

    # VDDANA decoupling
    c_ana = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_ana[1] += v3v3
    c_ana[2] += gnd

    # Bulk decoupling
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

    # ── USB ──────────────────────────────────────────────
    mcu["PA24"] += usb_dm   # USB D-
    mcu["PA25"] += usb_dp   # USB D+

    # ── Reset ────────────────────────────────────────────
    mcu["~{RESET}"] += reset_n

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

    # ── SWD Debug ────────────────────────────────────────
    mcu["PA30"] += swdclk
    mcu["PA31"] += swdio

    # ── 32.768kHz Crystal ────────────────────────────────
    xtal_net1 = Net("XIN32")
    xtal_net2 = Net("XOUT32")
    mcu["PA00"] += xtal_net1   # XIN32
    mcu["PA01"] += xtal_net2   # XOUT32

    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xtal_net1
    xtal[2] += xtal_net2

    # Crystal load capacitors
    c_x1 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += xtal_net1
    c_x1[2] += gnd

    c_x2 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2[1] += xtal_net2
    c_x2[2] += gnd

    # ── SPI Flash (QSPI) ────────────────────────────────
    mcu["PB10"] += spi_sck    # SERCOM4 SCK / QSPI SCK
    mcu["PB11"] += flash_cs   # QSPI CS
    mcu["PA08"] += spi_mosi   # QSPI DATA0
    mcu["PA09"] += spi_miso   # QSPI DATA1

    # ── NeoPixel ─────────────────────────────────────────
    mcu["PB22"] += neopixel   # NeoPixel data out

    # ── I2C ──────────────────────────────────────────────
    mcu["PA22"] += sda    # SERCOM3 SDA
    mcu["PA23"] += scl    # SERCOM3 SCL

    # ── UART ─────────────────────────────────────────────
    mcu["PB16"] += uart_tx   # SERCOM5 TX
    mcu["PB17"] += uart_rx   # SERCOM5 RX

    # ── Analog Inputs ────────────────────────────────────
    mcu["PA02"] += a0    # A0 / DAC
    mcu["PA05"] += a1    # A1
    mcu["PB08"] += a2    # A2
    mcu["PB09"] += a3    # A3
    mcu["PA04"] += a4    # A4
    mcu["PA06"] += a5    # A5

    # ── Digital I/O ──────────────────────────────────────
    mcu["PA14"] += d4     # D4
    mcu["PA15"] += d5     # D5
    mcu["PA18"] += d6     # D6
    mcu["PA19"] += d9     # D9
    mcu["PA20"] += d10    # D10
    mcu["PA21"] += d11    # D11
    mcu["PA03"] += d12    # D12 / AREF
    mcu["PA16"] += d13    # D13 / built-in LED

    # ── User LED (D13) ───────────────────────────────────
    # LED pin 1=K (cathode), pin 2=A (anode)
    # Current path: D13 -> R -> A(2) -> K(1) -> GND
    led_d13 = Part("Device", "LED", value="Red",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_d13 = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    d13 += r_d13[1]
    r_d13[2] += led_d13[2]   # Resistor to Anode
    led_d13[1] += gnd         # Cathode to GND

    # Unused MCU pins — connect to avoid ERC warnings
    mcu["PA07"]  += NC
    mcu["PA10"]  += NC
    mcu["PA11"]  += NC
    mcu["PA12"]  += NC
    mcu["PA13"]  += NC
    mcu["PA17"]  += NC
    mcu["PA27"]  += NC
    mcu["PB00"]  += NC
    mcu["PB01"]  += NC
    mcu["PB02"]  += NC
    mcu["PB04"]  += NC
    mcu["PB05"]  += NC
    mcu["PB06"]  += NC
    mcu["PB07"]  += NC
    mcu["PB12"]  += NC
    mcu["PB13"]  += NC
    mcu["PB14"]  += NC
    mcu["PB15"]  += NC
    mcu["PB23"]  += NC
    mcu["PB30"]  += NC
    mcu["PB31"]  += NC


mcu_samd51(v3v3, gnd, usb_dp, usb_dm, reset_n, swdio, swdclk,
           spi_sck, spi_mosi, spi_miso, flash_cs,
           neopixel, sda, scl, uart_tx, uart_rx,
           a0, a1, a2, a3, a4, a5,
           d4, d5, d6, d9, d10, d11, d12, d13)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: SPI Flash (GD25Q16 / W25Q16 — 2MB)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def spi_flash(v3v3, gnd, spi_sck, spi_mosi, spi_miso, flash_cs):
    """2MB SPI NOR flash for CircuitPython filesystem."""

    flash = Part("Memory_Flash", "W25Q32JVSS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 value="GD25Q16")
    flash["VCC"]                      += v3v3
    flash["GND"]                      += gnd
    flash["~{CS}"]                    += flash_cs
    flash["CLK"]                      += spi_sck
    flash["DI/IO_{0}"]               += spi_mosi
    flash["DO/IO_{1}"]               += spi_miso
    flash["~{WP}/IO_{2}"]            += v3v3    # Write protect disabled
    flash["~{HOLD}/~{RESET}/IO_{3}"] += v3v3    # Hold disabled

    # Decoupling cap
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_flash[1] += v3v3
    c_flash[2] += gnd


spi_flash(v3v3, gnd, spi_sck, spi_mosi, spi_miso, flash_cs)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: NeoPixel RGB LED
# ═══════════════════════════════════════════════════════════════
@subcircuit
def neopixel_led(v3v3, gnd, neopixel):
    """Single SK6812 / WS2812B addressable RGB LED."""

    neo = Part("LED", "SK6812",
               footprint="LED_SMD:LED_WS2812B-Mini_PLCC4_3.5x3.5mm",
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
#  SUBCIRCUIT: I2C Pull-ups
# ═══════════════════════════════════════════════════════════════
@subcircuit
def i2c_pullups(v3v3, sda, scl):
    """I2C bus pull-up resistors."""

    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += v3v3
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += v3v3
    r_scl[2] += scl


i2c_pullups(v3v3, sda, scl)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: Feather Headers
# ═══════════════════════════════════════════════════════════════
@subcircuit
def feather_headers(v3v3, vbat, gnd, reset_n,
                    a0, a1, a2, a3, a4, a5,
                    sda, scl, d4, d5, d6, d9,
                    d10, d11, d12, d13,
                    uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck):
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
    j_left[9]  += a4
    j_left[10] += a5
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


feather_headers(v3v3, vbat, gnd, reset_n,
                a0, a1, a2, a3, a4, a5,
                sda, scl, d4, d5, d6, d9,
                d10, d11, d12, d13,
                uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: SWD Debug Header
# ═══════════════════════════════════════════════════════════════
@subcircuit
def swd_header(v3v3, gnd, swdio, swdclk, reset_n):
    """SWD debug header (2x5 0.05" or 1x5 0.1")."""

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
