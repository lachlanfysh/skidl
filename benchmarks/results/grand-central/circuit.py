"""
Grand Central M4 Express (SAMD51) - Arduino Mega form factor
Microchip ATSAMD51J20A: 120MHz Cortex M4 with FPU/DSP, 1MB flash, 256KB RAM
Uses 64-pin TQFP variant from KiCad library.
Dual DAC, dual ADC, 8x SERCOM, PWM, I2S, AES-256, TRNG.
8MB QSPI Flash, Micro SD card slot, NeoPixel, USB, 6-12V DC with switch.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# Power Nets
# ============================================================
vbus = Net("VBUS"); vbus.drive = POWER
vin_raw = Net("VIN"); vin_raw.drive = POWER
v5v = Net("+5V"); v5v.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Internal nets
vin_switched = Net("VIN_SW")
usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")

# QSPI bus
qspi_sck = Net("QSPI_SCK")
qspi_cs = Net("QSPI_CS")
qspi_d0 = Net("QSPI_D0")
qspi_d1 = Net("QSPI_D1")
qspi_d2 = Net("QSPI_D2")
qspi_d3 = Net("QSPI_D3")

# SD card SPI bus
sd_sck = Net("SD_SCK")
sd_mosi = Net("SD_MOSI")
sd_miso = Net("SD_MISO")
sd_cs = Net("SD_CS")

# NeoPixel data
neopixel_data = Net("NEOPIXEL")

# I2S
i2s_sck = Net("I2S_SCK")
i2s_ws = Net("I2S_WS")
i2s_sd = Net("I2S_SD")

# Reset
reset_n = Net("~{RESET}")

# SWD debug
swdio = Net("SWDIO")
swdclk = Net("SWDCLK")

# GPIO nets for headers (reduced from 70 for J variant)
gpio = [Net(f"GPIO{i}") for i in range(40)]

# Analog nets
aref = Net("AREF")
dac0_out = Net("DAC0")
dac1_out = Net("DAC1")

# VDDCORE output
vddcore = Net("VDDCORE")


# ============================================================
# Subcircuit: ATSAMD51J20A MCU (64-pin TQFP from KiCad lib)
# ============================================================
@subcircuit
def mcu_block(v3v3, gnd, usb_dp, usb_dm, reset_n,
              qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3,
              sd_sck, sd_mosi, sd_miso, sd_cs,
              neopixel_data, i2s_sck, i2s_ws, i2s_sd,
              swdio, swdclk, aref, dac0_out, dac1_out, vddcore, gpio):
    """ATSAMD51J20A in 64-pin TQFP with decoupling and crystals."""

    mcu = Part("MCU_Microchip_SAMD", "ATSAMD51J20A-A", value="ATSAMD51J20A",
               footprint="Package_QFP:LQFP-64_10x10mm_P0.5mm")

    # Power connections
    mcu["VDDANA"] += v3v3
    mcu["VDDIO"] += v3v3     # Multiple VDDIO pins
    mcu["VDDIOB"] += v3v3
    mcu["VSW"] += v3v3
    mcu["VDDCORE"] += vddcore
    mcu["GNDANA"] += gnd
    mcu["GND"] += gnd

    # Decoupling capacitors (one per VDD group)
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += v3v3
        c[2] += gnd

    # Bulk capacitors
    c_bulk1 = Part("Device", "C", value="10uF",
                   footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk1[1] += v3v3
    c_bulk1[2] += gnd

    c_bulk2 = Part("Device", "C", value="10uF",
                   footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk2[1] += v3v3
    c_bulk2[2] += gnd

    # VDDCORE decoupling (1.2V internal regulator output)
    c_vcore = Part("Device", "C", value="1uF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcore[1] += vddcore
    c_vcore[2] += gnd

    # USB (PA24=D-, PA25=D+)
    mcu["PA24"] += usb_dm
    mcu["PA25"] += usb_dp

    # Reset with pull-up
    mcu["~{RESET}"] += reset_n
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += v3v3
    r_rst[2] += reset_n

    # SWD (PA30=SWCLK, PA31=SWDIO)
    mcu["PA30"] += swdclk
    mcu["PA31"] += swdio

    # AREF (VDDANA pin provides analog reference, PA03 is VREFA)
    mcu["PA03"] += aref

    # QSPI flash connections (PA08-PA11 = D0-D3, PB10=SCK, PB11=CS)
    mcu["PA08"] += qspi_d0
    mcu["PA09"] += qspi_d1
    mcu["PA10"] += qspi_d2
    mcu["PA11"] += qspi_d3
    mcu["PB10"] += qspi_sck
    mcu["PB11"] += qspi_cs

    # SD card SPI (using SERCOM on PB12-PB15)
    mcu["PB12"] += sd_mosi
    mcu["PB13"] += sd_sck
    mcu["PB14"] += sd_miso
    mcu["PB15"] += sd_cs

    # NeoPixel (PB22)
    mcu["PB22"] += neopixel_data

    # I2S (PB16=SCK, PB17=WS, PA22=SD)
    mcu["PB16"] += i2s_sck
    mcu["PB17"] += i2s_ws
    mcu["PA22"] += i2s_sd

    # DAC outputs (PA02=DAC0, PA05=DAC1)
    mcu["PA02"] += dac0_out
    mcu["PA05"] += dac1_out

    # GPIO pin assignments to headers
    gpio_map = {
        "PA00": 0, "PA01": 1,
        "PA04": 2, "PA06": 3, "PA07": 4,
        "PA12": 5, "PA13": 6, "PA14": 7, "PA15": 8,
        "PA16": 9, "PA17": 10, "PA18": 11, "PA19": 12,
        "PA20": 13, "PA21": 14, "PA23": 15, "PA27": 16,
        "PB00": 17, "PB01": 18, "PB02": 19, "PB03": 20,
        "PB04": 21, "PB05": 22, "PB06": 23, "PB07": 24,
        "PB08": 25, "PB09": 26,
        "PB23": 27, "PB30": 28, "PB31": 29,
    }
    for pin_name, gpio_idx in gpio_map.items():
        mcu[pin_name] += gpio[gpio_idx]


# ============================================================
# Subcircuit: Power Supply (Barrel Jack + Switch + 5V + 3.3V regulators)
# ============================================================
@subcircuit
def power_supply(vin_raw, vin_switched, v5v, v3v3, vbus, gnd):
    """6-12V barrel jack, on/off switch, 5V buck, 3.3V LDO."""

    # Barrel jack connector
    j_dc = Part("Connector", "Barrel_Jack",
                footprint="Connector_BarrelJack:BarrelJack_CUI_PJ-063AH_Horizontal")
    j_dc[1] += vin_raw
    j_dc[2] += gnd

    # On/off slide switch (SPDT)
    sw_pwr = Part("Switch", "SW_SPDT",
                  footprint="Button_Switch_SMD:SW_DIP_SPSTx01_Slide_6.7x4.1mm_W8.61mm_P2.54mm_LowProfile")
    sw_pwr["A"] += vin_raw
    sw_pwr["B"] += gnd
    sw_pwr["C"] += vin_switched

    # Input protection - reverse polarity diode
    d_rev = Part("Device", "D_Schottky",
                 footprint="Diode_SMD:D_SMA")
    d_rev[1] += gnd
    d_rev[2] += vin_switched

    # Input filter cap
    c_vin = Part("Device", "C", value="47uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vin[1] += vin_switched
    c_vin[2] += gnd

    # 5V buck regulator (AP63200WU from KiCad library, equivalent to AP63205)
    vreg_5v = Part("Regulator_Switching", "AP63200WU", value="AP63205",
                   footprint="Package_TO_SOT_SMD:SOT-23-6")
    vreg_5v["IN"] += vin_switched
    vreg_5v["EN"] += vin_switched
    vreg_5v["GND"] += gnd

    # Buck inductor
    l_buck = Part("Device", "L", value="4.7uH",
                  footprint="Inductor_SMD:L_0805_2012Metric")
    sw_net = Net("BUCK_SW")
    vreg_5v["SW"] += sw_net
    l_buck[1] += sw_net
    l_buck[2] += v5v

    # Bootstrap cap
    c_bst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    bst_net = Net("BST")
    vreg_5v["BST"] += bst_net
    c_bst[1] += bst_net
    c_bst[2] += sw_net

    # Feedback resistor divider for 5V output
    r_fb_top = Part("Device", "R", value="100K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_fb_bot = Part("Device", "R", value="24.9K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    fb_net = Net("FB_5V")
    vreg_5v["FB"] += fb_net
    r_fb_top[1] += v5v
    r_fb_top[2] += fb_net
    r_fb_bot[1] += fb_net
    r_fb_bot[2] += gnd

    # Output capacitors for 5V
    c_5v_1 = Part("Device", "C", value="22uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_5v_1[1] += v5v
    c_5v_1[2] += gnd

    c_5v_2 = Part("Device", "C", value="22uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_5v_2[1] += v5v
    c_5v_2[2] += gnd

    # USB VBUS to 5V via Schottky diode (OR-ing)
    d_usb = Part("Device", "D_Schottky",
                 footprint="Diode_SMD:D_SMA")
    d_usb[1] += vbus
    d_usb[2] += v5v

    # 3.3V LDO regulator (AP2112K-3.3)
    vreg_3v3 = Part("Regulator_Linear", "AP2112K-3.3",
                    footprint="Package_TO_SOT_SMD:SOT-23-5")
    vreg_3v3["VIN"] += v5v
    vreg_3v3["EN"] += v5v
    vreg_3v3["GND"] += gnd
    vreg_3v3["VOUT"] += v3v3

    # 3.3V input decoupling
    c_3v3_in = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_3v3_in[1] += v5v
    c_3v3_in[2] += gnd

    # 3.3V output caps
    c_3v3_1 = Part("Device", "C", value="10uF",
                   footprint="Capacitor_SMD:C_0805_2012Metric")
    c_3v3_1[1] += v3v3
    c_3v3_1[2] += gnd

    c_3v3_2 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_3v3_2[1] += v3v3
    c_3v3_2[2] += gnd


# ============================================================
# Subcircuit: USB Interface
# ============================================================
@subcircuit
def usb_interface(vbus, gnd, usb_dp, usb_dm):
    """Micro USB connector with ESD protection."""

    j_usb = Part("Connector", "USB_B_Micro",
                 footprint="Connector_USB:USB_Micro-B_Molex-105017-0001")
    j_usb["VBUS"] += vbus
    j_usb["GND"] += gnd
    j_usb["D+"] += usb_dp
    j_usb["D-"] += usb_dm
    j_usb["Shield"] += gnd

    # ESD protection TVS diode
    tvs = Part("Device", "D_TVS",
               footprint="Diode_SMD:D_SMA")
    tvs[1] += usb_dp
    tvs[2] += gnd

    # VBUS filter cap
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd


# ============================================================
# Subcircuit: Main Crystal (12MHz) + RTC Crystal (32.768kHz)
# ============================================================
@subcircuit
def crystal_oscillators(v3v3, gnd, gpio):
    """12MHz main crystal and 32.768kHz RTC crystal.
    Connected to PA00/PA01 (XIN/XOUT) for main and PB**/PB** for 32k."""

    # Note: SAMD51 J variant has no dedicated XOSC pins.
    # Main clock typically uses internal DFLL48M with USB SOF reference.
    # 32.768kHz crystal on PA00 (XIN32) / PA01 (XOUT32) for RTC.
    y_rtc = Part("Device", "Crystal", value="32.768kHz",
                 footprint="Crystal:Crystal_SMD_2012-2Pin_2.0x1.2mm")
    xin32 = gpio[0]    # PA00 = XIN32
    xout32 = gpio[1]   # PA01 = XOUT32
    y_rtc[1] += xin32
    y_rtc[2] += xout32

    c_xin32 = Part("Device", "C", value="6.8pF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xin32[1] += xin32
    c_xin32[2] += gnd

    c_xout32 = Part("Device", "C", value="6.8pF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xout32[1] += xout32
    c_xout32[2] += gnd


# ============================================================
# Subcircuit: 8MB QSPI Flash
# ============================================================
@subcircuit
def qspi_flash(v3v3, gnd, qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3):
    """GD25Q64 8MB QSPI NOR flash."""

    flash = Part("Memory_Flash", "GD25QxxxEY", value="GD25Q64",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    flash["~{CS}"] += qspi_cs
    flash["SCLK"] += qspi_sck
    flash["SI/IO0"] += qspi_d0
    flash["SO/IO1"] += qspi_d1
    flash["~{WP}/IO2"] += qspi_d2
    flash["~{HOLD}/~{RESET}/IO3"] += qspi_d3
    flash["VCC"] += v3v3
    flash["VSS"] += gnd
    flash["PAD"] += gnd

    # Decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_flash[1] += v3v3
    c_flash[2] += gnd


# ============================================================
# Subcircuit: Micro SD Card Slot
# ============================================================
@subcircuit
def sd_card_slot(v3v3, gnd, sd_sck, sd_mosi, sd_miso, sd_cs):
    """Micro SD card connector in SPI mode."""

    sd = Part("Connector", "Micro_SD_Card",
              footprint="Connector_Card:microSD_HC_Hirose_DM3AT-SF-PEJM5")
    sd["VDD"] += v3v3
    sd["VSS"] += gnd
    sd["CLK"] += sd_sck
    sd["CMD"] += sd_mosi
    sd["DAT0"] += sd_miso
    sd["DAT3/CD"] += sd_cs
    sd["SHIELD"] += gnd

    # Pull-ups on unused data lines
    r_dat1 = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_dat1[1] += v3v3
    sd_dat1_net = Net("SD_DAT1")
    r_dat1[2] += sd_dat1_net
    sd["DAT1"] += sd_dat1_net

    r_dat2 = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_dat2[1] += v3v3
    sd_dat2_net = Net("SD_DAT2")
    r_dat2[2] += sd_dat2_net
    sd["DAT2"] += sd_dat2_net

    # Decoupling
    c_sd = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_sd[1] += v3v3
    c_sd[2] += gnd


# ============================================================
# Subcircuit: NeoPixel Status LED
# ============================================================
@subcircuit
def neopixel_led(v5v, gnd, neopixel_data):
    """Single WS2812B NeoPixel for status indication."""

    neo = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    neo["VDD"] += v5v
    neo["VSS"] += gnd
    neo["DIN"] += neopixel_data
    # DOUT left unconnected (single LED, no chain)

    # Decoupling
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_neo[1] += v5v
    c_neo[2] += gnd


# ============================================================
# Subcircuit: Reset Circuit
# ============================================================
@subcircuit
def reset_circuit(v3v3, gnd, reset_n):
    """Reset button with RC filter."""

    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_DIP_SPSTx01_Slide_6.7x4.1mm_W8.61mm_P2.54mm_LowProfile")
    sw_rst[1] += reset_n
    sw_rst[2] += gnd

    # RC filter for debounce
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_rst[1] += reset_n
    c_rst[2] += gnd


# ============================================================
# Subcircuit: SWD Debug Header
# ============================================================
@subcircuit
def swd_header(v3v3, gnd, swdio, swdclk, reset_n):
    """2x5 Cortex Debug connector (10-pin)."""

    j_swd = Part("Connector_Generic", "Conn_02x05_Odd_Even",
                 footprint="Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical")
    j_swd[1] += v3v3       # Pin 1: VTref
    j_swd[2] += swdio      # Pin 2: SWDIO
    j_swd[3] += gnd        # Pin 3: GND
    j_swd[4] += swdclk     # Pin 4: SWDCLK
    j_swd[5] += gnd        # Pin 5: GND
    j_swd[6] += gnd
    j_swd[7] += gnd
    j_swd[8] += gnd
    j_swd[9] += gnd
    j_swd[10] += reset_n   # Pin 10: nRESET


# ============================================================
# Subcircuit: Arduino Headers
# ============================================================
@subcircuit
def arduino_headers(v5v, v3v3, gnd, gpio, aref, dac0_out, dac1_out, reset_n, vin_raw):
    """Arduino Mega form factor pin headers."""

    # Digital header 1 (1x16): D0-D13, GND, AREF
    j_dig1 = Part("Connector_Generic", "Conn_01x16",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    for i in range(14):
        j_dig1[i+1] += gpio[i]
    j_dig1[15] += gnd
    j_dig1[16] += aref

    # Digital header 2 (1x16): D14-D29
    j_dig2 = Part("Connector_Generic", "Conn_01x16",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    for i in range(16):
        j_dig2[i+1] += gpio[14 + i]

    # Analog + power header (1x10): A0-A5, DAC0, DAC1, VIN, 5V
    j_analog = Part("Connector_Generic", "Conn_01x10",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
    for i in range(6):
        j_analog[i+1] += gpio[30 + i]  # Analog pins A0-A5
    j_analog[7] += dac0_out
    j_analog[8] += dac1_out
    j_analog[9] += vin_raw
    j_analog[10] += v5v

    # Power header (1x04): 5V, 3V3, GND, RESET
    j_pwr = Part("Connector_Generic", "Conn_01x04",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    j_pwr[1] += v5v
    j_pwr[2] += v3v3
    j_pwr[3] += gnd
    j_pwr[4] += reset_n

    # ICSP/SPI header (2x3)
    j_icsp = Part("Connector_Generic", "Conn_02x03_Odd_Even",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical")
    j_icsp[1] += gpio[26]   # MISO (PB09)
    j_icsp[2] += v5v
    j_icsp[3] += gpio[25]   # SCK (PB08)
    j_icsp[4] += gpio[24]   # MOSI (PB07)
    j_icsp[5] += reset_n
    j_icsp[6] += gnd


# ============================================================
# Subcircuit: Status LEDs
# ============================================================
@subcircuit
def status_leds(v3v3, gnd, gpio):
    """Power LED + user LED (on GPIO13 / D13)."""

    # Power LED (green)
    led_pwr = Part("Device", "LED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    led_pwr[1] += v3v3
    led_pwr[2] += r_pwr[1]
    r_pwr[2] += gnd

    # User LED on D13 (red)
    led_d13 = Part("Device", "LED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_d13 = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    led_d13[1] += gpio[13]
    led_d13[2] += r_d13[1]
    r_d13[2] += gnd


# ============================================================
# Subcircuit: Analog Reference
# ============================================================
@subcircuit
def analog_ref(v3v3, gnd, aref):
    """AREF decoupling and filter."""

    c_aref = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_aref[1] += aref
    c_aref[2] += gnd

    r_aref = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_aref[1] += v3v3
    r_aref[2] += aref


# ============================================================
# Instantiate all subcircuits
# ============================================================

mcu_block(v3v3, gnd, usb_dp, usb_dm, reset_n,
          qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3,
          sd_sck, sd_mosi, sd_miso, sd_cs,
          neopixel_data, i2s_sck, i2s_ws, i2s_sd,
          swdio, swdclk, aref, dac0_out, dac1_out, vddcore, gpio)

power_supply(vin_raw, vin_switched, v5v, v3v3, vbus, gnd)

usb_interface(vbus, gnd, usb_dp, usb_dm)

crystal_oscillators(v3v3, gnd, gpio)

qspi_flash(v3v3, gnd, qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3)

sd_card_slot(v3v3, gnd, sd_sck, sd_mosi, sd_miso, sd_cs)

neopixel_led(v5v, gnd, neopixel_data)

reset_circuit(v3v3, gnd, reset_n)

swd_header(v3v3, gnd, swdio, swdclk, reset_n)

arduino_headers(v5v, v3v3, gnd, gpio, aref, dac0_out, dac1_out, reset_n, vin_raw)

status_leds(v3v3, gnd, gpio)

analog_ref(v3v3, gnd, aref)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)

print("Grand Central M4 Express schematic generated successfully!")
print(f"Parts: {len(default_circuit.parts)}")
print(f"Nets: {len(default_circuit.nets)}")
