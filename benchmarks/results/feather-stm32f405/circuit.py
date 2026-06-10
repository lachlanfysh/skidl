"""
Feather STM32F405 Express -- SKiDL circuit description.

Adafruit Feather STM32F405 Express: 168MHz STM32F405RGT6 Cortex-M4F with FPU,
1MB internal flash, 192KB RAM, 2MB SPI flash (GD25Q16), USB-C connector,
MicroSD card socket, NeoPixel status LED, LiPo charger, 3.3V LDO,
8MHz HSE crystal, 32.768kHz LSE crystal, and standard Feather headers.
First USB-C Feather design.
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
neopixel_data = Net("NEOPIXEL")
reset_n  = Net("RESET_N")
swdio    = Net("SWDIO")
swdclk   = Net("SWDCLK")
charge_stat = Net("CHG_STAT")
boot0    = Net("BOOT0")

# SD card SPI signals
sd_sck  = Net("SD_SCK")
sd_mosi = Net("SD_MOSI")
sd_miso = Net("SD_MISO")
sd_cs   = Net("SD_CS")

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
#  SUBCIRCUIT: USB-C Connector and Power Input
# ═══════════════════════════════════════════════════════════════
@subcircuit
def usb_input(vbus, gnd, usb_dp, usb_dm):
    """USB-C connector with CC resistors for UFP (device) role."""

    # USB-C receptacle (USB 2.0 14-pin variant)
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_14P",
               footprint="Connector_USB:USB_C_Receptacle_GCT_USB4085",
               value="USB-C")
    usb["VBUS"]   += vbus
    usb["GND"]    += gnd
    usb["D+"]     += usb_dp
    usb["D-"]     += usb_dm
    usb["SHIELD"] += gnd
    usb["CC1"]    += NC
    usb["CC2"]    += NC

    # CC1 pull-down resistor (5.1K for UFP / device)
    cc1_net = Net("CC1")
    usb["CC1"]  += cc1_net
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += cc1_net
    r_cc1[2] += gnd

    # CC2 pull-down resistor (5.1K for UFP / device)
    cc2_net = Net("CC2")
    usb["CC2"]  += cc2_net
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2[1] += cc2_net
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
    r_chg_led[2] += led_chg[1]   # cathode
    led_chg[2]   += vbus          # anode -- LED is active low from STAT

    # Battery connector (JST PH 2-pin)
    j_bat = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
                 value="BATT")
    j_bat[1] += vbat
    j_bat[2] += gnd

    # Schottky diode for USB/battery power ORing
    d_usb = Part("Device", "D_Schottky", value="MBR120",
                 footprint="Diode_SMD:D_SOD-123")
    d_usb[1] += vbat   # cathode -- to battery rail
    d_usb[2] += vbus   # anode -- from USB VBUS

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
#  SUBCIRCUIT: STM32F405RGT6 Microcontroller
# ═══════════════════════════════════════════════════════════════
@subcircuit
def mcu_stm32f405(v3v3, gnd, usb_dp, usb_dm, reset_n, swdio, swdclk,
                  boot0, spi_sck, spi_mosi, spi_miso, flash_cs,
                  sd_sck, sd_mosi, sd_miso, sd_cs,
                  neopixel_data, sda, scl, uart_tx, uart_rx,
                  a0, a1, a2, a3, a4, a5,
                  d4, d5, d6, d9, d10, d11, d12, d13):
    """STM32F405RGT6 MCU with decoupling, crystals, and GPIO mapping."""

    mcu = Part("MCU_ST_STM32F4", "STM32F405RGTx",
               footprint="Package_QFP:LQFP-64_10x10mm_P0.5mm",
               value="STM32F405RGT6")

    # ── Power connections ────────────────────────────────
    mcu["VDD"]   += v3v3     # Multiple VDD pins (all tied together)
    mcu["VDDA"]  += v3v3     # Analog VDD
    mcu["VBAT"]  += v3v3     # Battery backup -- tie to 3V3
    mcu["VSS"]   += gnd      # Ground pins
    mcu["VSSA"]  += gnd      # Analog ground

    # VCAP pins -- internal core regulator output, need 2.2uF caps
    vcap1 = Net("VCAP1")
    vcap2 = Net("VCAP2")
    mcu["VCAP_1"] += vcap1
    mcu["VCAP_2"] += vcap2

    c_vcap1 = Part("Device", "C", value="2.2uF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcap1[1] += vcap1
    c_vcap1[2] += gnd

    c_vcap2 = Part("Device", "C", value="2.2uF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcap2[1] += vcap2
    c_vcap2[2] += gnd

    # VDD decoupling caps (one per VDD group -- 4 VDD pins)
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += v3v3
        c[2] += gnd

    # VDDA decoupling
    c_ana = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_ana[1] += v3v3
    c_ana[2] += gnd

    # VDDA ferrite bead + cap for analog supply
    c_ana_bulk = Part("Device", "C", value="1uF",
                      footprint="Capacitor_SMD:C_0603_1608Metric")
    c_ana_bulk[1] += v3v3
    c_ana_bulk[2] += gnd

    # Bulk decoupling
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

    # ── USB ──────────────────────────────────────────────
    mcu["PA11"] += usb_dm   # USB OTG FS D-
    mcu["PA12"] += usb_dp   # USB OTG FS D+

    # ── Reset ────────────────────────────────────────────
    mcu["NRST"] += reset_n

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

    # Reset filter cap
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_rst[1] += reset_n
    c_rst[2] += gnd

    # ── BOOT0 pin ────────────────────────────────────────
    mcu["BOOT0"] += boot0

    # BOOT0 pull-down (default boot from flash)
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_boot[1] += boot0
    r_boot[2] += gnd

    # ── SWD Debug ────────────────────────────────────────
    mcu["PA13"] += swdio    # SWDIO
    mcu["PA14"] += swdclk   # SWCLK

    # ── 8MHz HSE Crystal ─────────────────────────────────
    hse_in  = Net("HSE_IN")
    hse_out = Net("HSE_OUT")
    mcu["PH0"] += hse_in    # OSC_IN
    mcu["PH1"] += hse_out   # OSC_OUT

    xtal_hse = Part("Device", "Crystal", value="8MHz",
                    footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm")
    xtal_hse[1] += hse_in
    xtal_hse[2] += hse_out

    # HSE load capacitors
    c_hse1 = Part("Device", "C", value="20pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_hse1[1] += hse_in
    c_hse1[2] += gnd

    c_hse2 = Part("Device", "C", value="20pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_hse2[1] += hse_out
    c_hse2[2] += gnd

    # ── 32.768kHz LSE Crystal ────────────────────────────
    lse_in  = Net("LSE_IN")
    lse_out = Net("LSE_OUT")
    mcu["PC14"] += lse_in    # OSC32_IN
    mcu["PC15"] += lse_out   # OSC32_OUT

    xtal_lse = Part("Device", "Crystal", value="32.768kHz",
                    footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal_lse[1] += lse_in
    xtal_lse[2] += lse_out

    # LSE load capacitors
    c_lse1 = Part("Device", "C", value="6.8pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lse1[1] += lse_in
    c_lse1[2] += gnd

    c_lse2 = Part("Device", "C", value="6.8pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lse2[1] += lse_out
    c_lse2[2] += gnd

    # ── SPI Flash (QSPI bus) ────────────────────────────
    mcu["PB3"]  += spi_sck    # SPI1_SCK
    mcu["PB4"]  += spi_miso   # SPI1_MISO
    mcu["PB5"]  += spi_mosi   # SPI1_MOSI
    mcu["PA15"] += flash_cs   # SPI flash CS

    # ── SD Card (SPI mode on SPI2) ───────────────────────
    mcu["PB13"] += sd_sck     # SPI2_SCK
    mcu["PB14"] += sd_miso    # SPI2_MISO
    mcu["PB15"] += sd_mosi    # SPI2_MOSI
    mcu["PB12"] += sd_cs      # SD card CS

    # ── NeoPixel ─────────────────────────────────────────
    mcu["PC0"] += neopixel_data   # NeoPixel data

    # ── I2C ──────────────────────────────────────────────
    mcu["PB6"] += scl     # I2C1_SCL
    mcu["PB7"] += sda     # I2C1_SDA

    # ── UART ─────────────────────────────────────────────
    mcu["PB10"] += uart_tx    # USART3_TX
    mcu["PB11"] += uart_rx    # USART3_RX

    # ── Analog Inputs ────────────────────────────────────
    mcu["PA0"] += a0     # A0 / ADC1_IN0
    mcu["PA1"] += a1     # A1 / ADC1_IN1
    mcu["PA2"] += a2     # A2 / ADC1_IN2
    mcu["PA3"] += a3     # A3 / ADC1_IN3
    mcu["PA4"] += a4     # A4 / DAC1
    mcu["PA5"] += a5     # A5 / DAC2

    # ── Digital I/O ──────────────────────────────────────
    mcu["PB1"]  += d4     # D4
    mcu["PC7"]  += d5     # D5 (PWM)
    mcu["PC6"]  += d6     # D6 (PWM)
    mcu["PB8"]  += d9     # D9 (PWM)
    mcu["PB9"]  += d10    # D10 (PWM)
    mcu["PC3"]  += d11    # D11
    mcu["PC2"]  += d12    # D12
    mcu["PC1"]  += d13    # D13 / built-in LED

    # ── User LED (D13) ───────────────────────────────────
    led_d13 = Part("Device", "LED", value="Red",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_d13 = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    d13 += r_d13[1]
    r_d13[2] += led_d13[2]    # Resistor to anode
    led_d13[1] += gnd          # Cathode to GND

    # ── Unused MCU pins ──────────────────────────────────
    mcu["PA6"]  += NC
    mcu["PA7"]  += NC
    mcu["PA8"]  += NC
    mcu["PA9"]  += NC
    mcu["PA10"] += NC
    mcu["PB0"]  += NC
    mcu["PB2"]  += NC
    mcu["PC4"]  += NC
    mcu["PC5"]  += NC
    mcu["PC8"]  += NC
    mcu["PC9"]  += NC
    mcu["PC10"] += NC
    mcu["PC11"] += NC
    mcu["PC12"] += NC
    mcu["PC13"] += NC
    mcu["PD2"]  += NC


mcu_stm32f405(v3v3, gnd, usb_dp, usb_dm, reset_n, swdio, swdclk,
              boot0, spi_sck, spi_mosi, spi_miso, flash_cs,
              sd_sck, sd_mosi, sd_miso, sd_cs,
              neopixel_data, sda, scl, uart_tx, uart_rx,
              a0, a1, a2, a3, a4, a5,
              d4, d5, d6, d9, d10, d11, d12, d13)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: SPI Flash (GD25Q16 -- 2MB)
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
#  SUBCIRCUIT: MicroSD Card Socket
# ═══════════════════════════════════════════════════════════════
@subcircuit
def sd_card(v3v3, gnd, sd_sck, sd_mosi, sd_miso, sd_cs):
    """MicroSD card socket in SPI mode."""

    sd = Part("Connector", "Micro_SD_Card",
              footprint="Connector_Card:microSD_HC_Molex_104031-0811",
              value="MicroSD")
    sd["CLK"]     += sd_sck      # Pin 5
    sd["CMD"]     += sd_mosi     # Pin 3 (MOSI in SPI mode)
    sd["DAT0"]    += sd_miso     # Pin 7 (MISO in SPI mode)
    sd["DAT3/CD"] += sd_cs       # Pin 2 (CS in SPI mode)
    sd["VDD"]     += v3v3        # Pin 4
    sd["VSS"]     += gnd         # Pin 6
    sd["DAT1"]    += NC          # Not used in SPI mode
    sd["DAT2"]    += NC          # Not used in SPI mode
    sd["SHIELD"]  += gnd

    # SD card decoupling
    c_sd = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_sd[1] += v3v3
    c_sd[2] += gnd

    # SD card bulk cap
    c_sd_bulk = Part("Device", "C", value="10uF",
                     footprint="Capacitor_SMD:C_0805_2012Metric")
    c_sd_bulk[1] += v3v3
    c_sd_bulk[2] += gnd


sd_card(v3v3, gnd, sd_sck, sd_mosi, sd_miso, sd_cs)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: NeoPixel RGB LED
# ═══════════════════════════════════════════════════════════════
@subcircuit
def neopixel_led(v3v3, gnd, neopixel_data):
    """Single SK6812 addressable RGB LED."""

    neo = Part("LED", "SK6812",
               footprint="LED_SMD:LED_WS2812B-Mini_PLCC4_3.5x3.5mm",
               value="NeoPixel")
    neo["VDD"]  += v3v3
    neo["VSS"]  += gnd
    neo["DIN"]  += neopixel_data
    neo["DOUT"] += NC       # Single LED, no chain

    # Decoupling cap
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_neo[1] += v3v3
    c_neo[2] += gnd


neopixel_led(v3v3, gnd, neopixel_data)


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
def feather_headers(v3v3, vbat, gnd, reset_n, vbus,
                    a0, a1, a2, a3, a4, a5,
                    sda, scl, d4, d5, d6, d9,
                    d10, d11, d12, d13,
                    uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck):
    """Standard Feather form-factor headers (16-pin + 12-pin)."""

    # Left header (16 pins) -- matches Adafruit Feather pinout
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


feather_headers(v3v3, vbat, gnd, reset_n, vbus,
                a0, a1, a2, a3, a4, a5,
                sda, scl, d4, d5, d6, d9,
                d10, d11, d12, d13,
                uart_tx, uart_rx, spi_mosi, spi_miso, spi_sck)


# ═══════════════════════════════════════════════════════════════
#  SUBCIRCUIT: SWD Debug Header
# ═══════════════════════════════════════════════════════════════
@subcircuit
def swd_header(v3v3, gnd, swdio, swdclk, reset_n):
    """SWD debug header (1x5 0.1")."""

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
