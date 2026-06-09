"""
Feather M0 Express (SAMD21) — CircuitPython Development Board

ATSAMD21G18A ARM Cortex M0+ at 48 MHz, 3.3V logic.
256KB Flash, 32KB RAM. Built-in USB bootloader (UF2).
2MB SPI Flash (GD25Q16 or W25Q32) for CircuitPython storage.
NeoPixel (WS2812B) status LED on PA06.
MCP73831 single-cell LiPo charger with JST-PH connector.
AP2112K-3.3 LDO regulator. Auto USB/battery power switching.
Battery voltage divider for monitoring.
Feather form factor (2.0" x 0.9") with 16+12 pin headers.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


# ==== Power nets ====
vbus = Net("VBUS")                       # USB 5V
vbat = Net("VBAT"); vbat.drive = POWER   # Battery voltage
vcc  = Net("VCC");  vcc.drive = POWER    # Switched power (USB or battery)
v3v3 = Net("+3V3"); v3v3.drive = POWER   # Regulated 3.3V
gnd  = Net("GND");  gnd.drive = POWER


# ==== Signal nets ====
usb_dp  = Net("USB_DP")
usb_dm  = Net("USB_DM")
xin     = Net("XIN")
xout    = Net("XOUT")
sda     = Net("SDA")
scl     = Net("SCL")
mosi    = Net("MOSI")
miso    = Net("MISO")
sck     = Net("SCK")
reset_n = Net("~{RESET}")
swdio   = Net("SWDIO")
swclk   = Net("SWCLK")
bat_div = Net("BAT_DIV")

# SPI Flash nets (shared SPI bus but dedicated CS)
flash_cs = Net("FLASH_CS")

# NeoPixel data
neo_data = Net("NEOPIXEL")


# ==============================================================
# Subcircuit: USB Micro-B connector with filtering
# ==============================================================
@subcircuit
def usb_input(vbus, gnd, dp, dm):
    """USB Micro-B connector with ferrite bead and bulk cap."""

    usb = Part(
        "Connector", "USB_B_Micro",
        value="USB_Micro_B",
        footprint="Connector_USB:USB_Micro-B_Molex_47346-0001",
    )
    usb["VBUS"]   += vbus
    usb["GND"]    += gnd
    usb["D+"]     += dp
    usb["D-"]     += dm
    usb["Shield"] += gnd

    # Ferrite bead on VBUS for noise filtering
    fb = Part(
        "Device", "L_Ferrite",
        value="600R@100MHz",
        footprint="Inductor_SMD:L_0805_2012Metric",
    )
    fb[1] += vbus
    fb[2] += vbus

    # Bulk decoupling on USB VBUS
    c_vbus = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_vbus[1] += vbus
    c_vbus[2] += gnd


# ==============================================================
# Subcircuit: MCP73831 LiPo battery charger
# ==============================================================
@subcircuit
def battery_charger(vbus, gnd, vbat):
    """MCP73831 single-cell LiPo charger with status LED."""

    chg = Part(
        "Battery_Management", "MCP73831-2-OT",
        value="MCP73831",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
    )
    chg["V_{DD}"]  += vbus
    chg["V_{SS}"]  += gnd
    chg["V_{BAT}"] += vbat

    # PROG resistor: 2K = 500mA charge current
    r_prog = Part(
        "Device", "R",
        value="2K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_prog[1] += chg["PROG"]
    r_prog[2] += gnd

    # Charge status LED (orange, active low from STAT pin)
    r_led = Part(
        "Device", "R",
        value="1K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    led_chg = Part(
        "Device", "LED",
        value="Orange",
        footprint="LED_SMD:LED_0603_1608Metric",
    )
    r_led[1] += vbus
    r_led[2] += led_chg["A"]
    led_chg["K"] += chg["STAT"]

    # 100nF decoupling on VDD
    c_chg = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_chg[1] += vbus
    c_chg[2] += gnd


# ==============================================================
# Subcircuit: Power switching and 3.3V LDO regulator
# ==============================================================
@subcircuit
def power_supply(vbus, vbat, gnd, vcc, v3v3):
    """
    Schottky diode OR for USB/battery auto-switching.
    AP2112K-3.3 LDO produces regulated 3.3V from VCC.
    """

    # Schottky diode: VBUS -> VCC
    d_usb = Part(
        "Device", "D_Schottky",
        value="MBR0520",
        footprint="Diode_SMD:D_SOD-323",
    )
    d_usb["A"] += vbus
    d_usb["K"] += vcc

    # Schottky diode: VBAT -> VCC
    d_bat = Part(
        "Device", "D_Schottky",
        value="MBR0520",
        footprint="Diode_SMD:D_SOD-323",
    )
    d_bat["A"] += vbat
    d_bat["K"] += vcc

    # AP2112K-3.3 LDO regulator
    reg = Part(
        "Regulator_Linear", "AP2112K-3.3",
        value="AP2112K-3.3",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
    )
    reg["VIN"]  += vcc
    reg["GND"]  += gnd
    reg["EN"]   += vcc   # Always enabled
    reg["VOUT"] += v3v3

    # Input capacitor on VCC
    c_in = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_in[1] += vcc
    c_in[2] += gnd

    # Output decoupling on 3.3V rail
    c_out_100n = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_out_100n[1] += v3v3
    c_out_100n[2] += gnd

    c_out_10u = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_out_10u[1] += v3v3
    c_out_10u[2] += gnd


# ==============================================================
# Subcircuit: Battery voltage monitor
# ==============================================================
@subcircuit
def battery_monitor(vbat, gnd, bat_div):
    """Resistor divider: VBAT -> 100K -> BAT_DIV -> 100K -> GND.
    Gives half of VBAT on analog input for battery monitoring."""

    r_top = Part(
        "Device", "R",
        value="100K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_bot = Part(
        "Device", "R",
        value="100K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_top[1] += vbat
    r_top[2] += bat_div
    r_bot[1] += bat_div
    r_bot[2] += gnd


# ==============================================================
# Subcircuit: 2MB SPI Flash (W25Q16 compatible)
# ==============================================================
@subcircuit
def spi_flash(v3v3, gnd, mosi, miso, sck, flash_cs):
    """2MB SPI Flash for CircuitPython file storage."""

    flash = Part(
        "Memory_Flash", "W25Q32JVSS",
        value="GD25Q16C",
        footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    )
    flash["VCC"]                       += v3v3
    flash["GND"]                       += gnd
    flash["DI/IO_{0}"]                 += mosi
    flash["DO/IO_{1}"]                 += miso
    flash["CLK"]                       += sck
    flash["~{CS}"]                     += flash_cs
    flash["~{WP}/IO_{2}"]             += v3v3   # Write protect disabled
    flash["~{HOLD}/~{RESET}/IO_{3}"]  += v3v3   # Hold disabled

    # 100nF decoupling
    c_flash = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_flash[1] += v3v3
    c_flash[2] += gnd


# ==============================================================
# Subcircuit: NeoPixel (WS2812B) status LED
# ==============================================================
@subcircuit
def neopixel_led(v3v3, gnd, data_in):
    """Single WS2812B NeoPixel LED with decoupling cap."""

    neo = Part(
        "LED", "WS2812B",
        value="WS2812B",
        footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
    )
    neo["VDD"]  += v3v3
    neo["VSS"]  += gnd
    neo["DIN"]  += data_in
    neo["DOUT"] += Net("NEO_DOUT")  # Unused, single LED chain

    # 100nF decoupling right next to NeoPixel
    c_neo = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_neo[1] += v3v3
    c_neo[2] += gnd


# ==============================================================
# Subcircuit: 32.768kHz crystal
# ==============================================================
@subcircuit
def crystal_32k(xin, xout, gnd):
    """32.768kHz crystal with load caps for SAMD21 RTC."""

    y1 = Part(
        "Device", "Crystal",
        value="32.768kHz",
        footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm",
    )
    y1[1] += xin
    y1[2] += xout

    # Load caps (12pF typical)
    c_x1 = Part(
        "Device", "C",
        value="12pF",
        footprint="Capacitor_SMD:C_0402_1005Metric",
    )
    c_x1[1] += xin
    c_x1[2] += gnd

    c_x2 = Part(
        "Device", "C",
        value="12pF",
        footprint="Capacitor_SMD:C_0402_1005Metric",
    )
    c_x2[1] += xout
    c_x2[2] += gnd


# ==============================================================
# Subcircuit: Reset circuit
# ==============================================================
@subcircuit
def reset_circuit(v3v3, gnd, reset_n):
    """Reset button with pull-up and filter cap."""

    # 10K pull-up on RESET
    r_rst = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_rst[1] += v3v3
    r_rst[2] += reset_n

    # Reset switch (modelled as 0-ohm placeholder)
    sw_rst = Part(
        "Device", "R",
        value="SW_RST",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    sw_rst[1] += reset_n
    sw_rst[2] += gnd

    # 100nF filter cap on reset line
    c_rst = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_rst[1] += reset_n
    c_rst[2] += gnd


# ==============================================================
# Subcircuit: User LED (on D13)
# ==============================================================
@subcircuit
def user_led(pin, gnd):
    """Red user LED on D13 with current-limiting resistor."""

    r_led = Part(
        "Device", "R",
        value="1K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    led = Part(
        "Device", "LED",
        value="Red",
        footprint="LED_SMD:LED_0603_1608Metric",
    )
    r_led[1] += pin
    r_led[2] += led["A"]
    led["K"] += gnd


# ==============================================================
# Subcircuit: SAMD21G18A MCU with decoupling
# ==============================================================
@subcircuit
def samd21_mcu(v3v3, gnd, usb_dp, usb_dm, xin, xout, reset_n,
               sda, scl, mosi, miso, sck, swdio, swclk, bat_div,
               flash_cs, neo_data, header_left, header_right):
    """ATSAMD21G18A-A with full decoupling and pin assignments."""

    mcu = Part(
        "MCU_Microchip_SAMD", "ATSAMD21G18A-A",
        value="ATSAMD21G18A",
        footprint="Package_QFP:TQFP-48_7x7mm_P0.5mm",
    )

    # Power pins
    mcu["VDDIO"]  += v3v3    # Pins 17, 36
    mcu["VDDIN"]  += v3v3    # Pin 44
    mcu["VDDANA"] += v3v3    # Pin 6
    mcu["GND"]    += gnd     # Pins 18, 35, 42
    mcu["GNDANA"] += gnd     # Pin 5

    # 1.2V core regulator output decoupling
    vddcore = Net("VDDCORE")
    mcu["VDDCORE"] += vddcore
    c_core = Part(
        "Device", "C",
        value="1uF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_core[1] += vddcore
    c_core[2] += gnd

    # VDDIO decoupling (100nF)
    c_vddio = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_vddio[1] += v3v3
    c_vddio[2] += gnd

    # VDDANA decoupling (100nF)
    c_vddana = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_vddana[1] += v3v3
    c_vddana[2] += gnd

    # VDDIN decoupling (100nF)
    c_vddin = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_vddin[1] += v3v3
    c_vddin[2] += gnd

    # USB data lines (PA24=D-, PA25=D+)
    mcu["PA24"] += usb_dm
    mcu["PA25"] += usb_dp

    # 32.768kHz crystal on PA00/PA01
    mcu["PA00"] += xin
    mcu["PA01"] += xout

    # Reset
    mcu["~{RESET}"] += reset_n

    # SWD debug
    mcu["PA30"] += swclk
    mcu["PA31"] += swdio

    # I2C (SERCOM3: PA22=SDA, PA23=SCL)
    mcu["PA22"] += sda
    mcu["PA23"] += scl

    # SPI bus (SERCOM4: PB10=MOSI, PA12=MISO, PB11=SCK)
    mcu["PB10"] += mosi
    mcu["PA12"] += miso
    mcu["PB11"] += sck

    # SPI Flash chip select on PA13
    mcu["PA13"] += flash_cs

    # NeoPixel data on PA06
    mcu["PA06"] += neo_data

    # Battery divider on PA07 (ADC AIN7)
    mcu["PA07"] += bat_div

    # Feather left header — analog pins
    mcu["PA02"] += header_left[0]    # A0
    mcu["PA03"] += header_left[1]    # AREF
    mcu["PB08"] += header_left[2]    # A1
    mcu["PB09"] += header_left[3]    # A2
    mcu["PA04"] += header_left[4]    # A3
    mcu["PA05"] += header_left[5]    # A4

    # Feather right header — digital pins
    mcu["PA17"] += header_right[0]   # D13 (onboard LED)
    mcu["PA19"] += header_right[1]   # D12
    mcu["PA16"] += header_right[2]   # D11
    mcu["PA18"] += header_right[3]   # D10
    mcu["PA20"] += header_right[4]   # D9
    mcu["PA15"] += header_right[5]   # D6
    mcu["PA14"] += header_right[6]   # D5

    # Serial (SERCOM0: PA10=TX, PA11=RX)
    mcu["PA10"] += header_right[7]   # TX
    mcu["PA11"] += header_right[8]   # RX

    # Spare digital
    mcu["PA21"] += header_right[9]   # D7

    # Unused pins tied to named no-connect nets
    mcu["PA08"] += Net("NC_PA08")
    mcu["PA09"] += Net("NC_PA09")
    mcu["PA27"] += Net("NC_PA27")
    mcu["PA28"] += Net("NC_PA28")
    mcu["PB02"] += Net("NC_PB02")
    mcu["PB03"] += Net("NC_PB03")
    mcu["PB22"] += Net("NC_PB22")
    mcu["PB23"] += Net("NC_PB23")


# ==============================================================
# Top-level instantiation
# ==============================================================

# Header nets
hl = [Net(f"HL_{i}") for i in range(6)]    # A0-A4 + AREF
hr = [Net(f"HR_{i}") for i in range(10)]   # D13 down to D7

# Instantiate all subcircuits
usb_input(vbus, gnd, usb_dp, usb_dm)
battery_charger(vbus, gnd, vbat)
power_supply(vbus, vbat, gnd, vcc, v3v3)
battery_monitor(vbat, gnd, bat_div)
crystal_32k(xin, xout, gnd)
reset_circuit(v3v3, gnd, reset_n)
spi_flash(v3v3, gnd, mosi, miso, sck, flash_cs)
neopixel_led(v3v3, gnd, neo_data)
samd21_mcu(v3v3, gnd, usb_dp, usb_dm, xin, xout, reset_n,
           sda, scl, mosi, miso, sck, swdio, swclk, bat_div,
           flash_cs, neo_data, hl, hr)
user_led(hr[0], gnd)  # D13 LED


# ==== JST battery connector ====
jst_bat = Part(
    "Connector_Generic", "Conn_01x02",
    value="JST_PH_2",
    footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
)
jst_bat[1] += vbat
jst_bat[2] += gnd


# ==== Feather left header (16 pins) ====
hdr_l = Part(
    "Connector_Generic", "Conn_01x16",
    value="Feather_Left",
    footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical",
)
hdr_l[1]  += reset_n
hdr_l[2]  += v3v3
hdr_l[3]  += v3v3        # AREF (tied to 3V3)
hdr_l[4]  += gnd
hdr_l[5]  += hl[0]       # A0
hdr_l[6]  += hl[1]       # AREF/A1
hdr_l[7]  += hl[2]       # A1
hdr_l[8]  += hl[3]       # A2
hdr_l[9]  += hl[4]       # A3
hdr_l[10] += hl[5]       # A4
hdr_l[11] += sck         # SCK
hdr_l[12] += mosi        # MOSI
hdr_l[13] += miso        # MISO
hdr_l[14] += hr[7]       # TX
hdr_l[15] += hr[8]       # RX
hdr_l[16] += neo_data    # DotStar Data (on Express)


# ==== Feather right header (12 pins) ====
hdr_r = Part(
    "Connector_Generic", "Conn_01x12",
    value="Feather_Right",
    footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical",
)
hdr_r[1]  += vbat
hdr_r[2]  += Net("EN")
hdr_r[3]  += vbus
hdr_r[4]  += hr[0]       # D13
hdr_r[5]  += hr[1]       # D12
hdr_r[6]  += hr[2]       # D11
hdr_r[7]  += hr[3]       # D10
hdr_r[8]  += hr[4]       # D9
hdr_r[9]  += hr[5]       # D6
hdr_r[10] += hr[6]       # D5
hdr_r[11] += sda         # SDA
hdr_r[12] += scl         # SCL


# ==== SWD debug header ====
swd_hdr = Part(
    "Connector_Generic", "Conn_01x04",
    value="SWD",
    footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
)
swd_hdr[1] += v3v3
swd_hdr[2] += swdio
swd_hdr[3] += swclk
swd_hdr[4] += gnd


# ---- Generate schematic ----
generate_schematic(auto_stub=True, auto_stub_fanout=3)
