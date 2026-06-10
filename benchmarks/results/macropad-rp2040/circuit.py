"""
MacroPad RP2040 -- SKiDL circuit description.

3x4 keyboard macropad powered by Raspberry Pi RP2040 with 8MB QSPI flash.
USB-C for power/data (HID, MIDI, UART). 12 Cherry MX-compatible key switch
sockets individually tied to GPIO (not matrix-wired), each with one NeoPixel
RGB LED. Rotary encoder with push-switch. 128x64 SH1106 OLED on hardware SPI.
8mm speaker/buzzer driven by Class D amplifier. STEMMA QT I2C connector.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


# -----------------------------------------------------------------------
# Helper: create SKIDL-tool parts with synthetic draw_cmds for schematic gen
# -----------------------------------------------------------------------
class _MockLib:
    """Mock library object for SKIDL-tool parts."""
    def __init__(self, name="skidl_lib"):
        self.filename = name


def make_skidl_part(name, footprint, pin_defs):
    """Create a SKIDL-tool Part with proper draw_cmds for schematic generation.

    pin_defs: list of (num, name, func) tuples.
    Each pin is placed at 2.54mm spacing on the left side of a rectangle.
    """
    n_pins = len(pin_defs)
    sym_w = 10.0
    sym_h = max(n_pins * 2.54, 5.0)
    pin_len = 2.54

    pins = []
    draw_cmds = {1: [], 0: []}

    # Add a rectangle for the body
    draw_cmds[1].append([
        "rectangle",
        ["start", 0, 0],
        ["end", sym_w, sym_h],
    ])

    # Place pins on the left side
    for i, (pnum, pname, pfunc) in enumerate(pin_defs):
        py = sym_h / 2 - (i - (n_pins - 1) / 2) * 2.54
        px = -pin_len
        pins.append(Pin(
            num=pnum, name=pname, func=pfunc,
            orientation="R", x=px, y=py,
            length=pin_len * 1000 / 25.4,
            rotation=0,
        ))
        draw_cmds[1].append([
            "pin", "passive", "line",
            ["at", px, py, 0],
            ["length", pin_len],
            ["name", pname],
            ["number", str(pnum)],
        ])

    part = Part(name=name, tool=SKIDL, dest=NETLIST,
                footprint=footprint, pins=pins)
    part.draw_cmds = draw_cmds
    part.lib = _MockLib("skidl_lib")
    return part


# -----------------------------------------------------------------------
# Global power nets
# -----------------------------------------------------------------------
vcc = Net("+5V");   vcc.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND");   gnd.drive = POWER

# -----------------------------------------------------------------------
# USB-C input & power supply
# -----------------------------------------------------------------------
@subcircuit
def usb_power_supply(vbus, v3v3, gnd):
    """USB-C connector and 3.3V LDO regulator."""
    usb = make_skidl_part("USB_C_Receptacle",
                          "Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
                          [
                              ("A1",  "GND_A",   Pin.types.PWRIN),
                              ("A4",  "VBUS_A",  Pin.types.PWRIN),
                              ("A5",  "CC1",     Pin.types.BIDIR),
                              ("A6",  "DP1",     Pin.types.BIDIR),
                              ("A7",  "DM1",     Pin.types.BIDIR),
                              ("A8",  "SBU1",    Pin.types.BIDIR),
                              ("A9",  "VBUS_A2", Pin.types.PWRIN),
                              ("A12", "GND_A2",  Pin.types.PWRIN),
                              ("B1",  "GND_B",   Pin.types.PWRIN),
                              ("B4",  "VBUS_B",  Pin.types.PWRIN),
                              ("B5",  "CC2",     Pin.types.BIDIR),
                              ("B6",  "DP2",     Pin.types.BIDIR),
                              ("B7",  "DM2",     Pin.types.BIDIR),
                              ("B8",  "SBU2",    Pin.types.BIDIR),
                              ("B9",  "VBUS_B2", Pin.types.PWRIN),
                              ("B12", "GND_B2",  Pin.types.PWRIN),
                              ("S1",  "SHIELD",  Pin.types.PASSIVE),
                          ])
    usb["VBUS_A"]  += vbus
    usb["VBUS_A2"] += vbus
    usb["VBUS_B"]  += vbus
    usb["VBUS_B2"] += vbus
    usb["GND_A"]   += gnd
    usb["GND_A2"]  += gnd
    usb["GND_B"]   += gnd
    usb["GND_B2"]  += gnd
    usb["SHIELD"]  += gnd

    # D+/D- to RP2040
    usb_dp = Net("USB_DP")
    usb_dm = Net("USB_DM")
    usb["DP1"] += usb_dp
    usb["DM1"] += usb_dm
    usb["DP2"] += usb_dp
    usb["DM2"] += usb_dm

    # SBU pins unused
    sbu1_nc = Net("SBU1_NC"); sbu1_nc.drive = POWER
    sbu2_nc = Net("SBU2_NC"); sbu2_nc.drive = POWER
    usb["SBU1"] += sbu1_nc
    usb["SBU2"] += sbu2_nc

    # 5.1K CC pull-down resistors (USB-C device identification)
    cc1_net = Net("CC1_NET")
    cc2_net = Net("CC2_NET")
    usb["CC1"] += cc1_net
    usb["CC2"] += cc2_net
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += cc1_net
    r_cc1[2] += gnd
    r_cc2[1] += cc2_net
    r_cc2[2] += gnd

    # 3.3V LDO (AP2112K-3.3, SOT-23-5)
    ldo = make_skidl_part("AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5",
                          [
                              ("1", "VIN",  Pin.types.PWRIN),
                              ("2", "GND",  Pin.types.PWRIN),
                              ("3", "EN",   Pin.types.INPUT),
                              ("4", "NC",   Pin.types.NOCONNECT),
                              ("5", "VOUT", Pin.types.PWROUT),
                          ])
    ldo["VIN"]  += vbus
    ldo["GND"]  += gnd
    ldo["EN"]   += vbus  # always enabled
    ldo["VOUT"] += v3v3

    # Input cap for LDO
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vbus
    c_in[2] += gnd

    # Output decoupling for LDO
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += v3v3
    c_out[2] += gnd

    # 100nF decoupling for LDO output
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += v3v3
    c_dec[2] += gnd

usb_power_supply(vcc, v3v3, gnd)

# -----------------------------------------------------------------------
# RP2040 MCU block with flash and crystal
# -----------------------------------------------------------------------
@subcircuit
def rp2040_mcu(v3v3, gnd):
    """RP2040 MCU with crystal, decoupling caps, and QSPI flash."""
    mcu = make_skidl_part("RP2040",
        "Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm",
        [
            # Power
            ("1",  "IOVDD_0",   Pin.types.PWRIN),
            ("10", "IOVDD_1",   Pin.types.PWRIN),
            ("22", "IOVDD_2",   Pin.types.PWRIN),
            ("33", "IOVDD_3",   Pin.types.PWRIN),
            ("42", "IOVDD_4",   Pin.types.PWRIN),
            ("49", "IOVDD_5",   Pin.types.PWRIN),
            ("44", "VREG_VIN",  Pin.types.PWRIN),
            ("45", "VREG_VOUT", Pin.types.PWROUT),
            ("23", "DVDD_0",    Pin.types.PWRIN),
            ("50", "DVDD_1",    Pin.types.PWRIN),
            ("43", "ADC_AVDD",  Pin.types.PWRIN),
            ("48", "USB_VDD",   Pin.types.PWRIN),
            ("57", "GND",       Pin.types.PWRIN),
            # Crystal
            ("20", "XIN",       Pin.types.INPUT),
            ("21", "XOUT",      Pin.types.OUTPUT),
            # USB
            ("46", "USB_DM",    Pin.types.BIDIR),
            ("47", "USB_DP",    Pin.types.BIDIR),
            # QSPI flash
            ("51", "QSPI_SD3",  Pin.types.BIDIR),
            ("52", "QSPI_SCLK", Pin.types.OUTPUT),
            ("53", "QSPI_SD0",  Pin.types.BIDIR),
            ("54", "QSPI_SD2",  Pin.types.BIDIR),
            ("55", "QSPI_SD1",  Pin.types.BIDIR),
            ("56", "QSPI_SS",   Pin.types.OUTPUT),
            # SWD
            ("24", "SWCLK",     Pin.types.INPUT),
            ("25", "SWDIO",     Pin.types.BIDIR),
            # Test/Run
            ("19", "TESTEN",    Pin.types.INPUT),
            ("26", "RUN",       Pin.types.INPUT),
            # GPIO 0-29
            ("2",  "GPIO0",     Pin.types.BIDIR),
            ("3",  "GPIO1",     Pin.types.BIDIR),
            ("4",  "GPIO2",     Pin.types.BIDIR),
            ("5",  "GPIO3",     Pin.types.BIDIR),
            ("6",  "GPIO4",     Pin.types.BIDIR),
            ("7",  "GPIO5",     Pin.types.BIDIR),
            ("8",  "GPIO6",     Pin.types.BIDIR),
            ("9",  "GPIO7",     Pin.types.BIDIR),
            ("11", "GPIO8",     Pin.types.BIDIR),
            ("12", "GPIO9",     Pin.types.BIDIR),
            ("13", "GPIO10",    Pin.types.BIDIR),
            ("14", "GPIO11",    Pin.types.BIDIR),
            ("15", "GPIO12",    Pin.types.BIDIR),
            ("16", "GPIO13",    Pin.types.BIDIR),
            ("17", "GPIO14",    Pin.types.BIDIR),
            ("18", "GPIO15",    Pin.types.BIDIR),
            ("27", "GPIO16",    Pin.types.BIDIR),
            ("28", "GPIO17",    Pin.types.BIDIR),
            ("29", "GPIO18",    Pin.types.BIDIR),
            ("30", "GPIO19",    Pin.types.BIDIR),
            ("31", "GPIO20",    Pin.types.BIDIR),
            ("32", "GPIO21",    Pin.types.BIDIR),
            ("34", "GPIO22",    Pin.types.BIDIR),
            ("35", "GPIO23",    Pin.types.BIDIR),
            ("36", "GPIO24",    Pin.types.BIDIR),
            ("37", "GPIO25",    Pin.types.BIDIR),
            ("38", "GPIO26",    Pin.types.BIDIR),
            ("39", "GPIO27",    Pin.types.BIDIR),
            ("40", "GPIO28",    Pin.types.BIDIR),
            ("41", "GPIO29",    Pin.types.BIDIR),
        ])

    # Power connections
    for pin_name in ["IOVDD_0", "IOVDD_1", "IOVDD_2", "IOVDD_3", "IOVDD_4", "IOVDD_5"]:
        mcu[pin_name] += v3v3
    mcu["VREG_VIN"]  += v3v3
    mcu["ADC_AVDD"]  += v3v3
    mcu["USB_VDD"]   += v3v3
    mcu["GND"]       += gnd
    mcu["TESTEN"]    += gnd  # tie low for normal operation

    # VREG_VOUT -> DVDD (internal 1.1V core supply)
    dvdd = Net("DVDD")
    dvdd.drive = POWER
    mcu["VREG_VOUT"] += dvdd
    mcu["DVDD_0"]    += dvdd
    mcu["DVDD_1"]    += dvdd

    # DVDD decoupling: 1uF close to VREG_VOUT
    c_dvdd = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dvdd[1] += dvdd
    c_dvdd[2] += gnd

    # IOVDD decoupling caps (100nF per bank)
    for i in range(6):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v3v3
        c[2] += gnd

    # ADC_AVDD decoupling
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_adc[1] += v3v3
    c_adc[2] += gnd

    # USB_VDD decoupling
    c_usb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usb[1] += v3v3
    c_usb[2] += gnd

    # 12MHz crystal
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["XIN"]
    xtal[2] += mcu["XOUT"]

    # Crystal load caps (15pF)
    c_x1 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += mcu["XIN"]
    c_x1[2] += gnd
    c_x2[1] += mcu["XOUT"]
    c_x2[2] += gnd

    # USB data lines
    usb_dp = Net("USB_DP")
    usb_dm = Net("USB_DM")
    mcu["USB_DP"] += usb_dp
    mcu["USB_DM"] += usb_dm

    # RUN pin pull-up (10K to 3.3V)
    r_run = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_run[1] += v3v3
    r_run[2] += mcu["RUN"]

    # SWD header for debugging
    swd = Part("Connector_Generic", "Conn_01x03",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
    swd[1] += mcu["SWCLK"]
    swd[2] += mcu["SWDIO"]
    swd[3] += gnd

    # ---------------------------------------------------------------
    # 8MB QSPI Flash (W25Q64JV)
    # ---------------------------------------------------------------
    flash = make_skidl_part("W25Q64JV", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                            [
                                ("1", "CS",   Pin.types.INPUT),
                                ("2", "DO",   Pin.types.OUTPUT),
                                ("3", "WP",   Pin.types.INPUT),
                                ("4", "GND",  Pin.types.PWRIN),
                                ("5", "DI",   Pin.types.INPUT),
                                ("6", "CLK",  Pin.types.INPUT),
                                ("7", "HOLD", Pin.types.INPUT),
                                ("8", "VCC",  Pin.types.PWRIN),
                            ])
    flash["CS"]   += mcu["QSPI_SS"]
    flash["DO"]   += mcu["QSPI_SD1"]
    flash["WP"]   += mcu["QSPI_SD2"]
    flash["GND"]  += gnd
    flash["DI"]   += mcu["QSPI_SD0"]
    flash["CLK"]  += mcu["QSPI_SCLK"]
    flash["HOLD"] += mcu["QSPI_SD3"]
    flash["VCC"]  += v3v3

    # Flash decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += v3v3
    c_flash[2] += gnd

    # ---------------------------------------------------------------
    # GPIO assignments (matching Adafruit MacroPad RP2040):
    # Keys: GPIO1-GPIO12 (directly wired, no matrix)
    # NeoPixel data: GPIO19
    # Rotary encoder: GPIO17 (A), GPIO18 (B), GPIO0 (switch)
    # OLED SPI: GPIO22 (SCK), GPIO23 (MOSI), GPIO24 (DC), GPIO25 (CS), GPIO26 (RST)
    # Speaker: GPIO16 (via Class D amp)
    # STEMMA QT I2C: GPIO20 (SDA), GPIO21 (SCL)
    # ---------------------------------------------------------------

    # Key GPIO nets
    for i in range(12):
        n = Net(f"KEY{i}")
        mcu[f"GPIO{i+1}"] += n

    # Encoder nets
    enc_a   = Net("ENC_A");   mcu["GPIO17"] += enc_a
    enc_b   = Net("ENC_B");   mcu["GPIO18"] += enc_b
    enc_sw  = Net("ENC_SW");  mcu["GPIO0"]  += enc_sw

    # NeoPixel data
    neo_data = Net("NEOPIXEL_DATA"); mcu["GPIO19"] += neo_data

    # OLED SPI nets
    spi_sck  = Net("OLED_SCK");  mcu["GPIO22"] += spi_sck
    spi_mosi = Net("OLED_MOSI"); mcu["GPIO23"] += spi_mosi
    oled_dc  = Net("OLED_DC");   mcu["GPIO24"] += oled_dc
    oled_cs  = Net("OLED_CS");   mcu["GPIO25"] += oled_cs
    oled_rst = Net("OLED_RST");  mcu["GPIO26"] += oled_rst

    # Speaker PWM
    spk_pwm = Net("SPK_PWM"); mcu["GPIO16"] += spk_pwm

    # I2C nets
    i2c_sda = Net("I2C_SDA"); mcu["GPIO20"] += i2c_sda
    i2c_scl = Net("I2C_SCL"); mcu["GPIO21"] += i2c_scl

    # Unused GPIOs: GPIO13-15, GPIO27-29 -- leave unconnected
    for gpio_num in [13, 14, 15, 27, 28, 29]:
        nc = Net(f"GPIO{gpio_num}_NC")
        nc.drive = POWER  # suppress ERC
        mcu[f"GPIO{gpio_num}"] += nc

rp2040_mcu(v3v3, gnd)

# -----------------------------------------------------------------------
# Key switches (12x Cherry MX, directly wired to GPIO)
# -----------------------------------------------------------------------
@subcircuit
def key_switches(gnd):
    """12 Cherry MX-compatible key switches, active low."""
    for i in range(12):
        sw = Part("Switch", "SW_Push",
                  footprint="Button_Switch_Keyboard:SW_Cherry_MX_1.00u_PCB")
        key_net = Net(f"KEY{i}")
        sw[1] += key_net
        sw[2] += gnd

key_switches(gnd)

# -----------------------------------------------------------------------
# NeoPixel LED chain (12x WS2812B, daisy-chained)
# -----------------------------------------------------------------------
@subcircuit
def neopixel_chain(vcc, gnd):
    """12 WS2812B NeoPixels in a daisy chain, one per key."""
    neo_data_in = Net("NEOPIXEL_DATA")
    prev_out = neo_data_in

    for i in range(12):
        led = make_skidl_part(f"WS2812B",
            "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
            [
                ("1", "VDD",  Pin.types.PWRIN),
                ("2", "DOUT", Pin.types.OUTPUT),
                ("3", "GND",  Pin.types.PWRIN),
                ("4", "DIN",  Pin.types.INPUT),
            ])
        led["VDD"] += vcc
        led["GND"] += gnd
        led["DIN"] += prev_out

        if i < 11:
            chain_net = Net(f"NEO_CHAIN_{i}")
            led["DOUT"] += chain_net
            prev_out = chain_net
        else:
            # Last LED DOUT is left unconnected
            nc_net = Net(f"NEO_NC_{i}")
            nc_net.drive = POWER
            led["DOUT"] += nc_net

    # Bypass caps for NeoPixel power (one per 4 LEDs)
    for i in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vcc
        c[2] += gnd

neopixel_chain(vcc, gnd)

# -----------------------------------------------------------------------
# Rotary encoder with push switch
# -----------------------------------------------------------------------
@subcircuit
def rotary_encoder(v3v3, gnd):
    """Rotary encoder with integrated push switch (20 detents)."""
    enc = Part("Device", "RotaryEncoder_Switch",
               footprint="Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm")
    enc_a  = Net("ENC_A")
    enc_b  = Net("ENC_B")
    enc_sw = Net("ENC_SW")

    enc["A"]  += enc_a
    enc["B"]  += enc_b
    enc["C"]  += gnd
    enc["S1"] += enc_sw
    enc["S2"] += gnd

    # Pull-up resistors for encoder signals
    r_a = Part("Device", "R", value="10K",
               footprint="Resistor_SMD:R_0402_1005Metric")
    r_b = Part("Device", "R", value="10K",
               footprint="Resistor_SMD:R_0402_1005Metric")
    r_sw = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_a[1]  += v3v3; r_a[2]  += enc_a
    r_b[1]  += v3v3; r_b[2]  += enc_b
    r_sw[1] += v3v3; r_sw[2] += enc_sw

rotary_encoder(v3v3, gnd)

# -----------------------------------------------------------------------
# SH1106 128x64 OLED display (SPI interface)
# -----------------------------------------------------------------------
@subcircuit
def oled_display(v3v3, gnd):
    """SH1106 128x64 OLED on hardware SPI."""
    oled = make_skidl_part("SH1106_OLED",
        "Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical",
        [
            ("1", "GND",  Pin.types.PWRIN),
            ("2", "VCC",  Pin.types.PWRIN),
            ("3", "SCK",  Pin.types.INPUT),
            ("4", "MOSI", Pin.types.INPUT),
            ("5", "RST",  Pin.types.INPUT),
            ("6", "DC",   Pin.types.INPUT),
            ("7", "CS",   Pin.types.INPUT),
        ])
    oled["GND"]  += gnd
    oled["VCC"]  += v3v3
    oled["SCK"]  += Net("OLED_SCK")
    oled["MOSI"] += Net("OLED_MOSI")
    oled["RST"]  += Net("OLED_RST")
    oled["DC"]   += Net("OLED_DC")
    oled["CS"]   += Net("OLED_CS")

    # Decoupling for OLED
    c_oled = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_oled[1] += v3v3
    c_oled[2] += gnd

oled_display(v3v3, gnd)

# -----------------------------------------------------------------------
# Speaker / buzzer with Class D amplifier
# -----------------------------------------------------------------------
@subcircuit
def speaker_amplifier(v3v3, gnd):
    """Class D mono amplifier (PAM8302A) driving an 8mm speaker."""
    amp = make_skidl_part("PAM8302A", "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                          [
                              ("1", "SD",    Pin.types.INPUT),
                              ("2", "IN+",   Pin.types.INPUT),
                              ("3", "IN-",   Pin.types.INPUT),
                              ("4", "VDD",   Pin.types.PWRIN),
                              ("5", "VO-",   Pin.types.OUTPUT),
                              ("6", "GND_0", Pin.types.PWRIN),
                              ("7", "GND_1", Pin.types.PWRIN),
                              ("8", "VO+",   Pin.types.OUTPUT),
                          ])

    amp["VDD"]   += v3v3
    amp["GND_0"] += gnd
    amp["GND_1"] += gnd
    amp["SD"]    += v3v3  # Always enabled

    # Audio input from RP2040 PWM via RC filter
    spk_pwm = Net("SPK_PWM")
    audio_filt = Net("AUDIO_FILT")
    r_in = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    r_in[1] += spk_pwm
    r_in[2] += audio_filt
    c_in[1] += audio_filt
    c_in[2] += gnd
    amp["IN+"] += audio_filt

    # Inverting input to ground (single-ended mode)
    amp["IN-"] += gnd

    # Speaker
    spk = Part("Device", "Speaker",
               footprint="Buzzer_Beeper:Buzzer_12x9.5RM7.6")
    spk[1] += amp["VO+"]
    spk[2] += amp["VO-"]

    # Amp decoupling
    c_amp = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_amp[1] += v3v3
    c_amp[2] += gnd

    # Bulk capacitor for amp supply
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

speaker_amplifier(v3v3, gnd)

# -----------------------------------------------------------------------
# STEMMA QT / Qwiic I2C connector
# -----------------------------------------------------------------------
@subcircuit
def stemma_qt(v3v3, gnd):
    """STEMMA QT (JST SH 4-pin) I2C connector with pull-ups."""
    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    i2c_sda = Net("I2C_SDA")
    i2c_scl = Net("I2C_SCL")

    conn[1] += gnd
    conn[2] += v3v3
    conn[3] += i2c_sda
    conn[4] += i2c_scl

    # I2C pull-up resistors (2.2K)
    r_sda = Part("Device", "R", value="2.2K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="2.2K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v3v3; r_sda[2] += i2c_sda
    r_scl[1] += v3v3; r_scl[2] += i2c_scl

stemma_qt(v3v3, gnd)

# -----------------------------------------------------------------------
# Generate schematic
# -----------------------------------------------------------------------
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
