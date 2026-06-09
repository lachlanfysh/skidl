"""
MacroPad RP2040 - 3x4 Macropad with RP2040 MCU

Features:
- Raspberry Pi RP2040 dual-core Cortex-M0+ MCU
- 8 MB W25Q64JV QSPI flash
- 12 Cherry MX key switches (individually wired to GPIO, no matrix)
- 12 WS2812B NeoPixel RGB LEDs (one per key)
- Rotary encoder with push switch (20 detents)
- 128x64 SH1106 OLED display on hardware SPI
- PAM8301 Class D amplifier with 8mm speaker
- USB-C connector for power/data
- STEMMA QT / JST SH I2C connector
- AP2112K-3.3 LDO for 3.3V rail
- 12 MHz crystal for RP2040
"""

import os

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *

set_default_tool(KICAD9)

# ==============================================================================
# Power nets
# ==============================================================================
vbus = Net("VBUS")
vbus.drive = POWER
vcc = Net("+3V3")
vcc.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# ==============================================================================
# USB-C Connector
# ==============================================================================
@subcircuit
def usb_connector(vbus, gnd, dp, dm):
    """USB-C receptacle for power and data."""
    usb = Part(
        "Connector",
        "USB_C_Receptacle_USB2.0_16P",
        footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
    )
    usb["VBUS"] += vbus
    usb["GND"] += gnd
    usb["D+"] += dp
    usb["D-"] += dm
    usb["SHIELD"] += gnd

    # CC pull-down resistors for UFP (device) - 5.1k to GND
    r_cc1 = Part(
        "Device", "R", value="5.1K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    r_cc2 = Part(
        "Device", "R", value="5.1K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    usb["CC1"] += r_cc1[1]
    r_cc1[2] += gnd
    usb["CC2"] += r_cc2[1]
    r_cc2[2] += gnd

    # SBU pins not connected
    usb["SBU1"] += NC
    usb["SBU2"] += NC


# ==============================================================================
# Voltage Regulator - AP2112K-3.3
# ==============================================================================
@subcircuit
def voltage_regulator(vin, vout, gnd):
    """3.3V LDO regulator with decoupling caps."""
    reg = Part(
        "Regulator_Linear",
        "AP2112K-3.3",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
    )
    reg["VIN"] += vin
    reg["GND"] += gnd
    reg["EN"] += vin  # Always enabled
    reg["VOUT"] += vout
    # NC pin left unconnected
    reg["NC"] += NC

    # Input decoupling cap
    c_in = Part(
        "Device", "C", value="1uF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    c_in[1] += vin
    c_in[2] += gnd

    # Output decoupling cap
    c_out = Part(
        "Device", "C", value="1uF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    c_out[1] += vout
    c_out[2] += gnd


# ==============================================================================
# RP2040 MCU
# ==============================================================================
@subcircuit
def rp2040_mcu(vcc, gnd, vbus, usb_dp, usb_dm, gpio_nets):
    """RP2040 MCU with crystal, flash, and decoupling."""
    mcu = Part(
        "MCU_RaspberryPi",
        "RP2040",
        footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm",
    )

    # Power connections
    mcu["DVDD"] += vcc
    mcu["IOVDD"] += vcc
    mcu["ADC_AVDD"] += vcc
    mcu["USB_VDD"] += vcc
    mcu["VREG_VIN"] += vcc

    # VREG_VOUT: add a 1uF cap on the internal regulator output (required by datasheet)
    # Use a separate net to avoid power-output-to-power-output ERC conflict
    vreg_out = Net("VREG_OUT")
    mcu["VREG_VOUT"] += vreg_out
    c_vreg = Part(
        "Device", "C", value="1uF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    c_vreg[1] += vreg_out
    c_vreg[2] += gnd
    mcu["GND"] += gnd
    mcu["TESTEN"] += gnd  # Must be tied to GND

    # USB
    mcu["USB_DP"] += usb_dp
    mcu["USB_DM"] += usb_dm

    # 12 MHz crystal
    xtal = Part(
        "Device",
        "Crystal",
        value="12MHz",
        footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm",
    )
    xtal[1] += mcu["XIN"]
    xtal[2] += mcu["XOUT"]

    # Crystal load caps (18pF typical for 12MHz)
    c_x1 = Part(
        "Device", "C", value="18pF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    c_x2 = Part(
        "Device", "C", value="18pF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    c_x1[1] += mcu["XIN"]
    c_x1[2] += gnd
    c_x2[1] += mcu["XOUT"]
    c_x2[2] += gnd

    # RUN (reset) - pull-up to 3.3V
    r_run = Part(
        "Device", "R", value="10K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    r_run[1] += vcc
    r_run[2] += mcu["RUN"]

    # SWD debug (exposed on test points but no connector needed)
    mcu["SWCLK"] += NC
    mcu["SWDIO"] += NC

    # QSPI Flash - W25Q64JVS (8 MB = 64 Mbit)
    flash = Part(
        "Memory_Flash",
        "W25Q128JVS",
        value="W25Q64JVS",
        footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    )
    flash["VCC"] += vcc
    flash["GND"] += gnd
    flash["CLK"] += mcu["QSPI_SCLK"]
    flash["DI/IO_{0}"] += mcu["QSPI_SD0"]
    flash["DO/IO_{1}"] += mcu["QSPI_SD1"]
    flash["~{WP}/IO_{2}"] += mcu["QSPI_SD2"]
    flash["~{HOLD}/~{RESET}/IO_{3}"] += mcu["QSPI_SD3"]
    flash["~{CS}"] += mcu["~{QSPI_SS}"]

    # Flash decoupling
    c_flash = Part(
        "Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    c_flash[1] += vcc
    c_flash[2] += gnd

    # MCU decoupling caps (one per power pin cluster)
    for i in range(6):
        c = Part(
            "Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric"
        )
        c[1] += vcc
        c[2] += gnd

    # Bulk cap
    c_bulk = Part(
        "Device", "C", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric"
    )
    c_bulk[1] += vcc
    c_bulk[2] += gnd

    # GPIO assignments
    gpio_pins = [
        "GPIO0",
        "GPIO1",
        "GPIO2",
        "GPIO3",
        "GPIO4",
        "GPIO5",
        "GPIO6",
        "GPIO7",
        "GPIO8",
        "GPIO9",
        "GPIO10",
        "GPIO11",
        "GPIO12",
        "GPIO13",
        "GPIO14",
        "GPIO15",
        "GPIO16",
        "GPIO17",
        "GPIO18",
        "GPIO19",
        "GPIO20",
        "GPIO21",
        "GPIO22",
        "GPIO23",
        "GPIO24",
        "GPIO25",
        "GPIO26/ADC0",
        "GPIO27/ADC1",
        "GPIO28/ADC2",
        "GPIO29/ADC3",
    ]
    for i, net in enumerate(gpio_nets):
        if i < len(gpio_pins):
            mcu[gpio_pins[i]] += net


# ==============================================================================
# Key Switches (12 Cherry MX, individually wired)
# ==============================================================================
@subcircuit
def key_switches(gpio_nets, gnd):
    """12 Cherry MX key switches, each directly wired to a GPIO pin."""
    for i in range(12):
        sw = Part(
            "Switch",
            "SW_Push",
            value=f"KEY{i+1}",
            footprint="Button_Switch_Keyboard:SW_Cherry_MX_1.00u_PCB",
        )
        sw[1] += gpio_nets[i]
        sw[2] += gnd


# ==============================================================================
# NeoPixel LED Chain (12 WS2812B)
# ==============================================================================
@subcircuit
def neopixel_chain(data_in, vcc, gnd):
    """Chain of 12 WS2812B NeoPixel LEDs with decoupling caps."""
    prev_dout = data_in
    for i in range(12):
        led = Part(
            "LED",
            "WS2812B",
            value=f"NP{i+1}",
            footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
        )
        led["VDD"] += vcc
        led["VSS"] += gnd
        led["DIN"] += prev_dout

        # Decoupling cap per NeoPixel
        c = Part(
            "Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric"
        )
        c[1] += vcc
        c[2] += gnd

        if i < 11:
            chain_net = Net(f"NP_D{i+1}")
            led["DOUT"] += chain_net
            prev_dout = chain_net
        else:
            led["DOUT"] += NC


# ==============================================================================
# Rotary Encoder
# ==============================================================================
@subcircuit
def rotary_encoder(enc_a, enc_b, enc_sw, vcc, gnd):
    """Rotary encoder with push switch and pull-up resistors."""
    enc = Part(
        "Device",
        "RotaryEncoder_Switch",
        footprint="Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm",
    )
    enc["A"] += enc_a
    enc["B"] += enc_b
    enc["C"] += gnd  # Common pin to ground
    enc["S1"] += enc_sw
    enc["S2"] += gnd

    # Pull-up resistors for encoder A and B
    r_a = Part(
        "Device", "R", value="10K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    r_b = Part(
        "Device", "R", value="10K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    r_sw = Part(
        "Device", "R", value="10K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    r_a[1] += vcc
    r_a[2] += enc_a
    r_b[1] += vcc
    r_b[2] += enc_b
    r_sw[1] += vcc
    r_sw[2] += enc_sw


# ==============================================================================
# SH1106 OLED Display (SPI)
# ==============================================================================
@subcircuit
def oled_display(spi_clk, spi_mosi, cs, dc, rst, vcc, gnd):
    """SH1106 128x64 OLED on hardware SPI with 7-pin header."""
    # SH1106 OLED module connector (typical 7-pin: GND, VCC, CLK, MOSI, CS, DC, RST)
    oled = Part(
        "Connector_Generic",
        "Conn_01x07",
        value="OLED_SH1106",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical",
    )
    oled[1] += gnd   # GND
    oled[2] += vcc   # VCC
    oled[3] += spi_clk   # CLK (SCK)
    oled[4] += spi_mosi  # MOSI (SDA/DIN)
    oled[5] += rst   # RES
    oled[6] += dc    # DC
    oled[7] += cs    # CS


# ==============================================================================
# Audio Amplifier (PAM8301 Class D)
# ==============================================================================
@subcircuit
def audio_amplifier(audio_in, vcc, gnd):
    """PAM8301 Class D mono amplifier driving an 8mm speaker."""
    amp = Part(
        "Amplifier_Audio",
        "PAM8301",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
    )
    amp["VDD"] += vcc
    amp["GND"] += gnd
    amp["~{SD}"] += vcc  # Always enabled (pull high)

    # Input coupling cap + series resistor
    c_in = Part(
        "Device", "C", value="1uF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    r_in = Part(
        "Device", "R", value="10K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    c_in[1] += audio_in
    c_in[2] += r_in[1]
    r_in[2] += amp["IN"]

    # Speaker connector (2-pin) for 8mm speaker
    spk = Part(
        "Connector_Generic",
        "Conn_01x02",
        value="Speaker_8mm",
        footprint="Buzzer_Beeper:PUIAudio_SMT_0825_S_4_R",
    )
    spk[1] += amp["OUT+"]
    spk[2] += amp["OUT-"]

    # Decoupling cap for amp
    c_dec = Part(
        "Device", "C", value="100nF", footprint="Capacitor_SMD:C_0402_1005Metric"
    )
    c_dec[1] += vcc
    c_dec[2] += gnd

    # Bulk cap for audio
    c_bulk = Part(
        "Device", "C", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric"
    )
    c_bulk[1] += vcc
    c_bulk[2] += gnd


# ==============================================================================
# STEMMA QT / I2C Connector
# ==============================================================================
@subcircuit
def stemma_qt(sda, scl, vcc, gnd):
    """STEMMA QT / Qwiic JST SH 4-pin I2C connector with pull-ups."""
    conn = Part(
        "Connector_Generic",
        "Conn_01x04",
        value="STEMMA_QT",
        footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
    )
    conn[1] += gnd
    conn[2] += vcc
    conn[3] += sda
    conn[4] += scl

    # I2C pull-up resistors
    r_sda = Part(
        "Device", "R", value="4.7K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    r_scl = Part(
        "Device", "R", value="4.7K", footprint="Resistor_SMD:R_0402_1005Metric"
    )
    r_sda[1] += vcc
    r_sda[2] += sda
    r_scl[1] += vcc
    r_scl[2] += scl


# ==============================================================================
# GPIO net allocation
# ==============================================================================
# GPIO0-11: Key switches (directly wired, active low)
key_gpio = [Net(f"KEY_SW{i}") for i in range(12)]

# GPIO12: NeoPixel data
neopixel_data = Net("NP_DIN")

# GPIO13: Rotary encoder A
enc_a_net = Net("ENC_A")
# GPIO14: Rotary encoder B
enc_b_net = Net("ENC_B")
# GPIO15: Rotary encoder switch
enc_sw_net = Net("ENC_SW")

# SPI0 for OLED (GPIO18=SCK, GPIO19=MOSI)
spi_clk_net = Net("OLED_SCK")
spi_mosi_net = Net("OLED_MOSI")
# GPIO20: OLED CS
oled_cs_net = Net("OLED_CS")
# GPIO21: OLED DC
oled_dc_net = Net("OLED_DC")
# GPIO22: OLED RST
oled_rst_net = Net("OLED_RST")

# GPIO23: Audio PWM output
audio_pwm_net = Net("AUDIO_PWM")

# I2C1 for STEMMA QT (GPIO16=SDA, GPIO17=SCL)
i2c_sda_net = Net("I2C_SDA")
i2c_scl_net = Net("I2C_SCL")

# USB D+/D-
usb_dp_net = Net("USB_DP")
usb_dm_net = Net("USB_DM")

# Collect all GPIO nets in order (GPIO0 through GPIO29)
# Only assign the ones we use; leave unused ones unconnected
all_gpio_nets = []
# GPIO0-11: keys
for i in range(12):
    all_gpio_nets.append(key_gpio[i])
# GPIO12: NeoPixel
all_gpio_nets.append(neopixel_data)
# GPIO13: Encoder A
all_gpio_nets.append(enc_a_net)
# GPIO14: Encoder B
all_gpio_nets.append(enc_b_net)
# GPIO15: Encoder SW
all_gpio_nets.append(enc_sw_net)
# GPIO16: I2C SDA
all_gpio_nets.append(i2c_sda_net)
# GPIO17: I2C SCL
all_gpio_nets.append(i2c_scl_net)
# GPIO18: SPI SCK (OLED)
all_gpio_nets.append(spi_clk_net)
# GPIO19: SPI MOSI (OLED)
all_gpio_nets.append(spi_mosi_net)
# GPIO20: OLED CS
all_gpio_nets.append(oled_cs_net)
# GPIO21: OLED DC
all_gpio_nets.append(oled_dc_net)
# GPIO22: OLED RST
all_gpio_nets.append(oled_rst_net)
# GPIO23: Audio PWM
all_gpio_nets.append(audio_pwm_net)

# ==============================================================================
# Instantiate subcircuits
# ==============================================================================
usb_connector(vbus, gnd, usb_dp_net, usb_dm_net)
voltage_regulator(vbus, vcc, gnd)
rp2040_mcu(vcc, gnd, vbus, usb_dp_net, usb_dm_net, all_gpio_nets)
key_switches(key_gpio, gnd)
neopixel_chain(neopixel_data, vcc, gnd)
rotary_encoder(enc_a_net, enc_b_net, enc_sw_net, vcc, gnd)
oled_display(spi_clk_net, spi_mosi_net, oled_cs_net, oled_dc_net, oled_rst_net, vcc, gnd)
audio_amplifier(audio_pwm_net, vcc, gnd)
stemma_qt(i2c_sda_net, i2c_scl_net, vcc, gnd)

# ==============================================================================
# Generate outputs
# ==============================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)

print("MacroPad RP2040 schematic generated successfully.")
