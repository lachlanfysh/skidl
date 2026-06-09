"""
FT232H Multi-Protocol USB Breakout Board
=========================================
Swiss-army-knife USB breakout for SPI, I2C, serial UART, JTAG protocols.
Built-in GPIO pins for LED control and button reading.
Direct computer-to-device communication without intermediate microcontroller.

Functional blocks:
  - USB-C connector + ESD protection
  - FT232H USB-to-multi-protocol bridge (LQFP-48)
  - 93C46 EEPROM for FT232H configuration
  - 12 MHz crystal oscillator
  - Power regulation (3.3V LDO from USB 5V)
  - Status LEDs (power, TX, RX)
  - User button (active low, directly on GPIO)
  - Breakout headers: ADBUS[0:7] + ACBUS[0:9] + power
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)


def make_skidl_part(name, footprint, pin_defs):
    """Create a SKIDL-tool Part with proper draw_cmds for schematic generation.

    pin_defs: list of (num, name, func) tuples.
    Each pin is placed at 2.54mm spacing on the left side of a rectangle.
    """
    n_pins = len(pin_defs)
    # Symbol size in mm: width=10mm, height = max(n_pins * 2.54, 5)mm
    sym_w = 10.0
    sym_h = max(n_pins * 2.54, 5.0)
    pin_len = 2.54  # standard pin length in mm

    pins = []
    draw_cmds = {1: [], 0: []}

    # Add a rectangle for the body
    draw_cmds[1].append([
        "rectangle",
        ["start", 0, 0],
        ["end", sym_w, sym_h],
    ])

    # Place pins on the left side
    for i, (num, pname, func) in enumerate(pin_defs):
        py = sym_h / 2 - (i - (n_pins - 1) / 2) * 2.54
        px = -pin_len
        pins.append(Pin(
            num=num, name=pname, func=func,
            orientation="R", x=px, y=py,
            length=pin_len * 1000 / 25.4,  # convert mm to mils for length
            rotation=0,
        ))
        draw_cmds[1].append([
            "pin", "passive", "line",
            ["at", px, py, 0],
            ["length", pin_len],
            ["name", pname],
            ["number", str(num)],
        ])

    part = Part(name=name, tool=SKIDL, dest=NETLIST,
                footprint=footprint, pins=pins)
    part.draw_cmds = draw_cmds
    return part


# ─── Global Nets ─────────────────────────────────────────────────────
vbus = Net("VBUS")
vbus.drive = POWER
vcc3v3 = Net("+3V3")
vcc3v3.drive = POWER
gnd = Net("GND")
gnd.drive = POWER


# ═══════════════════════════════════════════════════════════════════════
# SUBCIRCUIT: USB input + ESD protection
# ═══════════════════════════════════════════════════════════════════════
@subcircuit
def usb_input(vbus, dm, dp, gnd):
    """USB-C 16-pin connector with CC pull-downs and ESD TVS diodes."""
    usb = make_skidl_part(
        "USB_C_16P",
        "Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
        [
            ("A1",  "GND",      Pin.types.PWRIN),
            ("A4",  "VBUS",     Pin.types.PWRIN),
            ("A5",  "CC1",      Pin.types.BIDIR),
            ("A6",  "DP",       Pin.types.BIDIR),
            ("A7",  "DM",       Pin.types.BIDIR),
            ("A8",  "SBU1",     Pin.types.PASSIVE),
            ("A9",  "VBUS_A9",  Pin.types.PASSIVE),
            ("A12", "GND_A12",  Pin.types.PASSIVE),
            ("B1",  "GND_B1",   Pin.types.PASSIVE),
            ("B4",  "VBUS_B4",  Pin.types.PASSIVE),
            ("B5",  "CC2",      Pin.types.BIDIR),
            ("B6",  "DP_B6",    Pin.types.PASSIVE),
            ("B7",  "DM_B7",    Pin.types.PASSIVE),
            ("B8",  "SBU2",     Pin.types.PASSIVE),
            ("B9",  "VBUS_B9",  Pin.types.PASSIVE),
            ("B12", "GND_B12",  Pin.types.PASSIVE),
            ("S1",  "SHIELD",   Pin.types.PASSIVE),
        ],
    )

    usb["VBUS"] += vbus
    usb["VBUS_A9"] += vbus
    usb["VBUS_B4"] += vbus
    usb["VBUS_B9"] += vbus

    usb["GND"] += gnd
    usb["GND_A12"] += gnd
    usb["GND_B1"] += gnd
    usb["GND_B12"] += gnd
    usb["SHIELD"] += gnd

    usb["DM"] += dm
    usb["DM_B7"] += dm
    usb["DP"] += dp
    usb["DP_B6"] += dp

    usb["SBU1"] += NC()
    usb["SBU2"] += NC()

    # CC pull-down resistors (5.1k for UFP/device mode)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["CC1"]
    r_cc1[2] += gnd
    r_cc2[1] += usb["CC2"]
    r_cc2[2] += gnd

    # ESD protection TVS diodes on D+/D-
    tvs_dm = Part("Device", "D_TVS", value="ESD5V0",
                  footprint="Diode_SMD:D_SOD-323_HandSoldering")
    tvs_dp = Part("Device", "D_TVS", value="ESD5V0",
                  footprint="Diode_SMD:D_SOD-323_HandSoldering")
    tvs_dm[1] += dm
    tvs_dm[2] += gnd
    tvs_dp[1] += dp
    tvs_dp[2] += gnd

    # VBUS input capacitor
    c_vbus = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd


# ═══════════════════════════════════════════════════════════════════════
# SUBCIRCUIT: 3.3V LDO voltage regulator
# ═══════════════════════════════════════════════════════════════════════
@subcircuit
def power_regulation(vin, vout, gnd):
    """AMS1117-3.3 LDO: 5V USB to 3.3V for FT232H VCCIO and peripherals."""
    ldo = make_skidl_part(
        "AMS1117-3.3",
        "Package_TO_SOT_SMD:SOT-223-3",
        [
            ("1", "GND", Pin.types.PWRIN),
            ("2", "VO",  Pin.types.PWROUT),
            ("3", "VI",  Pin.types.PWRIN),
        ],
    )
    ldo["VI"] += vin
    ldo["VO"] += vout
    ldo["GND"] += gnd

    # Input cap
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output cap
    c_out = Part("Device", "C", value="22uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vout
    c_out[2] += gnd

    # 100nF decoupling on output
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vout
    c_dec[2] += gnd


# ═══════════════════════════════════════════════════════════════════════
# SUBCIRCUIT: FT232H core + crystal + decoupling
# ═══════════════════════════════════════════════════════════════════════
@subcircuit
def ft232h_core(vbus, vcc3v3, gnd, dm, dp,
                adbus, acbus,
                eecs, eeclk, eedata):
    """
    FT232H with 12MHz crystal, proper decoupling, and all IO broken out.
    adbus: list of 8 nets (ADBUS0-7)
    acbus: list of 10 nets (ACBUS0-9)
    """
    ft = Part("Interface_USB", "FT232H",
              footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm")

    # ── Power ──
    ft["VREGIN"] += vbus
    ft["VCCD"] += vcc3v3

    vcccore = Net("VCCCORE")
    vcca = Net("VCCA")
    ft["VCCCORE"] += vcccore
    ft["VCCA"] += vcca

    ft["VCCIO"] += vcc3v3

    vphy = Net("VPHY")
    vpll = Net("VPLL")
    ft["VPHY"] += vphy
    ft["VPLL"] += vpll

    # ── Ground ──
    ft["GND"] += gnd
    ft["AGND"] += gnd

    # ── Decoupling caps ──
    c_vregin = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vregin[1] += vbus
    c_vregin[2] += gnd

    c_vcccore = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcccore[1] += vcccore
    c_vcccore[2] += gnd

    c_vcca = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcca[1] += vcca
    c_vcca[2] += gnd

    c_vphy = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vphy[1] += vphy
    c_vphy[2] += gnd

    c_vpll = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vpll[1] += vpll
    c_vpll[2] += gnd

    c_vccio = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vccio[1] += vcc3v3
    c_vccio[2] += gnd

    c_vccd = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vccd[1] += vcc3v3
    c_vccd[2] += gnd

    # ── USB Data ──
    ft["DM"] += dm
    ft["DP"] += dp

    # ── Reset ──
    reset_net = Net("RESET_N")
    ft["~{RESET}"] += reset_net
    r_reset = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0402_1005Metric")
    r_reset[1] += vcc3v3
    r_reset[2] += reset_net

    sw_rst = make_skidl_part(
        "SW_RST",
        "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KSC6xxJ",
        [
            ("1", "1", Pin.types.PASSIVE),
            ("2", "2", Pin.types.PASSIVE),
        ],
    )
    sw_rst["1"] += reset_net
    sw_rst["2"] += gnd

    # ── REF pin: 12K to GND ──
    r_ref = Part("Device", "R", value="12K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    ft["REF"] += r_ref[1]
    r_ref[2] += gnd

    # ── Crystal: 12MHz ──
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_2012-2Pin_2.0x1.2mm")
    xtal[1] += ft["XCSI"]
    xtal[2] += ft["XCSO"]

    c_xtal1 = Part("Device", "C", value="18pF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xtal2 = Part("Device", "C", value="18pF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xtal1[1] += ft["XCSI"]
    c_xtal1[2] += gnd
    c_xtal2[1] += ft["XCSO"]
    c_xtal2[2] += gnd

    # ── TEST pin: tie to GND ──
    ft["TEST"] += gnd

    # ── EEPROM interface ──
    ft["EECS"] += eecs
    ft["EECLK"] += eeclk
    ft["EEDATA"] += eedata

    # ── ADBUS[0:7] ──
    for i in range(8):
        ft[f"ADBUS{i}"] += adbus[i]

    # ── ACBUS[0:9] ──
    for i in range(10):
        ft[f"ACBUS{i}"] += acbus[i]


# ═══════════════════════════════════════════════════════════════════════
# SUBCIRCUIT: EEPROM (93C46)
# ═══════════════════════════════════════════════════════════════════════
@subcircuit
def eeprom_93c46(vcc, gnd, cs, clk, data):
    """93C46 1Kbit EEPROM for FT232H USB descriptor storage."""
    ee = Part("Memory_EEPROM", "93CxxC",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    ee["VCC"] += vcc
    ee["GND"] += gnd
    ee["CS"] += cs
    ee["SCLK"] += clk
    ee["DI"] += data
    ee["DO"] += data
    ee["ORG"] += gnd
    ee["NC"] += NC()

    c_ee = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_ee[1] += vcc
    c_ee[2] += gnd


# ═══════════════════════════════════════════════════════════════════════
# SUBCIRCUIT: Status LEDs + user button
# ═══════════════════════════════════════════════════════════════════════
@subcircuit
def status_io(vcc, gnd, tx_gpio, rx_gpio, btn_gpio):
    """Power LED, TX/RX activity LEDs, user pushbutton."""
    # Power LED (green)
    led_pwr = Part("Device", "LED", value="GREEN",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_pwr[1] += vcc
    r_pwr[2] += led_pwr[1]
    led_pwr[2] += gnd

    # TX LED (yellow)
    led_tx = Part("Device", "LED", value="YELLOW",
                  footprint="LED_SMD:LED_0603_1608Metric")
    r_tx = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_tx[1] += vcc
    r_tx[2] += led_tx[1]
    led_tx[2] += tx_gpio

    # RX LED (yellow)
    led_rx = Part("Device", "LED", value="YELLOW",
                  footprint="LED_SMD:LED_0603_1608Metric")
    r_rx = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_rx[1] += vcc
    r_rx[2] += led_rx[1]
    led_rx[2] += rx_gpio

    # User button (active low with pull-up)
    r_btn = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_btn[1] += vcc
    r_btn[2] += btn_gpio

    sw_btn = make_skidl_part(
        "SW_USER",
        "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KSC6xxJ",
        [
            ("1", "1", Pin.types.PASSIVE),
            ("2", "2", Pin.types.PASSIVE),
        ],
    )
    sw_btn["1"] += btn_gpio
    sw_btn["2"] += gnd


# ═══════════════════════════════════════════════════════════════════════
# SUBCIRCUIT: Breakout headers
# ═══════════════════════════════════════════════════════════════════════
@subcircuit
def breakout_headers(vcc3v3, vbus, gnd, adbus, acbus):
    """Pin headers exposing all IO."""
    hdr_a = Part("Connector_Generic", "Conn_01x10",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
    hdr_a[1] += vcc3v3
    hdr_a[2] += gnd
    for i in range(8):
        hdr_a[3 + i] += adbus[i]

    hdr_b = Part("Connector_Generic", "Conn_01x10",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
    hdr_b[1] += vbus
    hdr_b[2] += gnd
    for i in range(8):
        hdr_b[3 + i] += acbus[i]

    hdr_c = Part("Connector_Generic", "Conn_01x04",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    hdr_c[1] += vcc3v3
    hdr_c[2] += gnd
    hdr_c[3] += acbus[8]
    hdr_c[4] += acbus[9]


# ═══════════════════════════════════════════════════════════════════════
# TOP-LEVEL CIRCUIT ASSEMBLY
# ═══════════════════════════════════════════════════════════════════════

dm = Net("USB_DM")
dp = Net("USB_DP")

eecs = Net("EECS")
eeclk = Net("EECLK")
eedata = Net("EEDATA")

adbus = [Net(f"ADBUS{i}") for i in range(8)]
acbus = [Net(f"ACBUS{i}") for i in range(10)]

usb_input(vbus, dm, dp, gnd)
power_regulation(vbus, vcc3v3, gnd)
ft232h_core(vbus, vcc3v3, gnd, dm, dp, adbus, acbus, eecs, eeclk, eedata)
eeprom_93c46(vcc3v3, gnd, eecs, eeclk, eedata)
status_io(vcc3v3, gnd, tx_gpio=acbus[6], rx_gpio=acbus[5], btn_gpio=acbus[4])
breakout_headers(vcc3v3, vbus, gnd, adbus, acbus)

# ── Generate Schematic ──
generate_schematic(auto_stub=True)
