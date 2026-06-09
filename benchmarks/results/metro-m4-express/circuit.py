"""
Metro M4 Express (SAMD51) - Arduino-compatible Metro form factor
Microchip ATSAMD51J19A: 120MHz Cortex M4 with FPU, 512KB flash, 192KB RAM
Same size and shield-compatible as other Metros with analog pins, SPI/UART/I2C.
Compatible with all Arduino shields. Powered by ATSAMD51J19 processor.
Features: 8MB QSPI flash, NeoPixel, USB, 7-12V DC barrel jack, 3.3V logic
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

from collections import defaultdict
from types import SimpleNamespace


# ============================================================
# Helper: Create SKIDL part with proper draw commands
# ============================================================
def _pin_func_to_str(func):
    """Map pin function enum to KiCad s-expression string."""
    mapping = {
        Pin.types.PWRIN: "power_in",
        Pin.types.PWROUT: "power_out",
        Pin.types.INPUT: "input",
        Pin.types.OUTPUT: "output",
        Pin.types.BIDIR: "bidirectional",
        Pin.types.PASSIVE: "passive",
        Pin.types.TRISTATE: "tri_state",
        Pin.types.UNSPEC: "unspecified",
        Pin.types.NOCONNECT: "passive",
    }
    return mapping.get(func, "passive")


def skidl_part(name, footprint, pin_defs):
    """Create a Part with tool=SKIDL including draw_cmds for schematic generation.

    pin_defs: list of (num, name, func) tuples.
    """
    y_step = 2.54  # mm spacing between pins
    left_pins = []
    right_pins = []
    for num, pname, pfunc in pin_defs:
        if pfunc in (Pin.types.OUTPUT, Pin.types.PWROUT):
            right_pins.append((num, pname, pfunc))
        else:
            left_pins.append((num, pname, pfunc))

    max_side = max(len(left_pins), len(right_pins), 1)
    box_h = (max_side + 1) * y_step
    box_w = 10.16  # mm
    pin_len = 2.54  # mm

    all_pins = []
    draw_cmds = []

    # Left side pins
    for i, (num, pname, pfunc) in enumerate(left_pins):
        px = -(box_w / 2) - pin_len
        py = (box_h / 2) - (i + 1) * y_step
        p = Pin(num=num, name=pname, func=pfunc)
        p.x = px
        p.y = py
        p.orientation = "R"
        p.length = pin_len
        p.rotation = 0
        all_pins.append(p)
        draw_cmds.append([
            "pin", _pin_func_to_str(pfunc), "line",
            ["at", px, py, 0],
            ["length", pin_len],
            ["name", pname, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", num, ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    # Right side pins
    for i, (num, pname, pfunc) in enumerate(right_pins):
        px = (box_w / 2) + pin_len
        py = (box_h / 2) - (i + 1) * y_step
        p = Pin(num=num, name=pname, func=pfunc)
        p.x = px
        p.y = py
        p.orientation = "L"
        p.length = pin_len
        p.rotation = 180
        all_pins.append(p)
        draw_cmds.append([
            "pin", _pin_func_to_str(pfunc), "line",
            ["at", px, py, 180],
            ["length", pin_len],
            ["name", pname, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", num, ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    # Rectangle body
    draw_cmds.append([
        "rectangle",
        ["start", -(box_w / 2), (box_h / 2)],
        ["end", (box_w / 2), -(box_h / 2)],
        ["stroke", ["width", 0], ["type", "default"]],
        ["fill", ["type", "background"]],
    ])

    part = Part(name=name, tool=SKIDL, dest=NETLIST,
                footprint=footprint, pins=all_pins)
    part.draw_cmds = defaultdict(list)
    part.draw_cmds[1] = draw_cmds
    part.lib = SimpleNamespace(filename="Custom")

    return part


# ============================================================
# Power Nets
# ============================================================
vbus = Net("VBUS"); vbus.drive = POWER       # USB 5V
vin_raw = Net("VIN"); vin_raw.drive = POWER  # 7-12V barrel jack input
v5v = Net("+5V"); v5v.drive = POWER          # Regulated 5V rail
v3v3 = Net("+3V3"); v3v3.drive = POWER       # 3.3V rail
gnd = Net("GND"); gnd.drive = POWER

# Internal nets
vin_switched = Net("VIN_SW")   # Barrel jack after on/off switch
usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")

# QSPI bus
qspi_sck = Net("QSPI_SCK")
qspi_cs = Net("QSPI_CS")
qspi_d0 = Net("QSPI_D0")
qspi_d1 = Net("QSPI_D1")
qspi_d2 = Net("QSPI_D2")
qspi_d3 = Net("QSPI_D3")

# NeoPixel data
neopixel_data = Net("NEOPIXEL")

# Reset
reset_n = Net("~{RESET}")

# SWD debug
swdio = Net("SWDIO")
swdclk = Net("SWDCLK")

# Crystal
xin = Net("XIN")
xout = Net("XOUT")
xin32 = Net("XIN32")
xout32 = Net("XOUT32")

# GPIO nets for Arduino headers
gpio = [Net(f"GPIO{i}") for i in range(25)]

# Analog nets
aref = Net("AREF")
dac0_out = Net("DAC0")


# ============================================================
# Subcircuit: ATSAMD51J19A MCU (64-pin TQFP)
# ============================================================
@subcircuit
def mcu_block(v3v3, gnd, usb_dp, usb_dm, reset_n,
              qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3,
              neopixel_data,
              swdio, swdclk, xin, xout, xin32, xout32,
              aref, dac0_out, gpio):
    """ATSAMD51J19A in 64-pin TQFP with decoupling and crystals."""

    mcu = Part("MCU_Microchip_SAMD", "ATSAMD51J19A-A",
               footprint="Package_QFP:TQFP-64_10x10mm_P0.5mm")

    # Power connections
    mcu["VDDANA"] += v3v3
    mcu["VDDIOB"] += v3v3
    for p in mcu.pins:
        if p.name == "VDDIO":
            p += v3v3

    # VDDCORE (pin 53) - 1.2V internal regulator output
    vcore_net = Net("VDDCORE")
    mcu["VDDCORE"] += vcore_net
    c_vcore = Part("Device", "C", value="1uF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcore[1] += vcore_net
    c_vcore[2] += gnd

    # VSW (pin 55) - switching regulator inductor
    vsw_net = Net("VSW")
    mcu["VSW"] += vsw_net
    l_vsw = Part("Device", "L", value="10uH",
                 footprint="Inductor_SMD:L_0805_2012Metric")
    l_vsw[1] += vsw_net
    l_vsw[2] += vcore_net

    # GND connections
    mcu["GNDANA"] += gnd
    for p in mcu.pins:
        if p.name == "GND":
            p += gnd

    # Decoupling capacitors (one per VDD group)
    for i in range(5):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += v3v3
        c[2] += gnd

    # Bulk capacitor
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

    # USB (PA24=D-, PA25=D+)
    mcu["PA24"] += usb_dm
    mcu["PA25"] += usb_dp

    # Reset with pull-up
    mcu["~{RESET}"] += reset_n
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += v3v3
    r_rst[2] += reset_n

    # Main crystal 12MHz (PA00=XIN, PA01=XOUT)
    y1 = Part("Device", "Crystal", value="12MHz",
              footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    y1[1] += xin
    y1[2] += xout
    mcu["PA00"] += xin
    mcu["PA01"] += xout

    c_xin = Part("Device", "C", value="20pF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xin[1] += xin
    c_xin[2] += gnd

    c_xout = Part("Device", "C", value="20pF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xout[1] += xout
    c_xout[2] += gnd

    # 32.768 kHz crystal for RTC
    y2 = Part("Device", "Crystal", value="32.768kHz",
              footprint="Crystal:Crystal_SMD_2012-2Pin_2.0x1.2mm")
    y2[1] += xin32
    y2[2] += xout32

    c_xin32 = Part("Device", "C", value="6.8pF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xin32[1] += xin32
    c_xin32[2] += gnd

    c_xout32 = Part("Device", "C", value="6.8pF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xout32[1] += xout32
    c_xout32[2] += gnd

    # SWD debug
    mcu["PA30"] += swdclk
    mcu["PA31"] += swdio

    # AREF
    mcu["PA03"] += aref

    # QSPI flash connections
    mcu["PA08"] += qspi_d0
    mcu["PA09"] += qspi_d1
    mcu["PA10"] += qspi_d2
    mcu["PA11"] += qspi_d3
    mcu["PB10"] += qspi_sck
    mcu["PB11"] += qspi_cs

    # NeoPixel (PB22 on Metro M4)
    mcu["PB22"] += neopixel_data

    # DAC output (PA02 = DAC0)
    mcu["PA02"] += dac0_out

    # GPIO pin assignments to Arduino headers
    gpio_map = {
        "PA23": 0,   # D0 (RX)
        "PA22": 1,   # D1 (TX)
        "PB17": 2,   # D2
        "PB16": 3,   # D3
        "PB13": 4,   # D4
        "PB14": 5,   # D5
        "PB15": 6,   # D6
        "PB12": 7,   # D7
        "PA21": 8,   # D8
        "PA20": 9,   # D9
        "PA18": 10,  # D10 (SS)
        "PA19": 11,  # D11 (MOSI)
        "PA17": 12,  # D12 (MISO)
        "PA16": 13,  # D13 (SCK/LED)
        "PA05": 14,  # A1
        "PB08": 15,  # A2
        "PB09": 16,  # A3
        "PA04": 17,  # A4
        "PA06": 18,  # A5
        "PA12": 19,  # SDA
        "PA13": 20,  # SCL
        "PB23": 21,  # D14/TX3
        "PB02": 22,  # D15/RX3
        "PB03": 23,  # D16
        "PA07": 24,  # D17
    }
    for pin_name, gpio_idx in gpio_map.items():
        mcu[pin_name] += gpio[gpio_idx]


# ============================================================
# Subcircuit: Power Supply (Barrel Jack + Switch + 5V + 3.3V regulators)
# ============================================================
@subcircuit
def power_supply(vin_raw, vin_switched, v5v, v3v3, vbus, gnd):
    """7-12V barrel jack, on/off switch, 5V buck, 3.3V LDO."""

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

    # Input protection - reverse polarity Schottky diode
    d_rev = Part("Device", "D_Schottky",
                 footprint="Diode_SMD:D_SMA")
    d_rev["K"] += vin_switched
    d_rev["A"] += gnd

    # Input filter cap
    c_vin = Part("Device", "C", value="47uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vin[1] += vin_switched
    c_vin[2] += gnd

    # 5V buck regulator (AP63205 style, SOT-23-6)
    vreg_5v = skidl_part("AP63205", "Package_TO_SOT_SMD:SOT-23-6", [
        ("1", "FB", Pin.types.INPUT),
        ("2", "EN", Pin.types.INPUT),
        ("3", "VIN", Pin.types.PWRIN),
        ("4", "GND", Pin.types.PWRIN),
        ("5", "SW", Pin.types.OUTPUT),
        ("6", "BST", Pin.types.PASSIVE),
    ])
    vreg_5v["VIN"] += vin_switched
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
    d_usb["A"] += vbus
    d_usb["K"] += v5v

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
    tvs["A1"] += usb_dp
    tvs["A2"] += gnd

    # VBUS filter cap
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd


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
    """Reset button with RC debounce filter."""

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
    j_swd[1] += v3v3
    j_swd[2] += swdio
    j_swd[3] += gnd
    j_swd[4] += swdclk
    j_swd[5] += gnd
    j_swd[6] += gnd
    j_swd[7] += gnd
    j_swd[8] += gnd
    j_swd[9] += gnd
    j_swd[10] += reset_n


# ============================================================
# Subcircuit: Arduino Metro Headers
# ============================================================
@subcircuit
def arduino_headers(v5v, v3v3, gnd, gpio, aref, dac0_out, reset_n, vin_raw):
    """Arduino Uno/Metro form factor pin headers."""

    # Digital header (1x16): D0-D13 + GND + AREF
    j_digital = Part("Connector_Generic", "Conn_01x16",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    for i in range(14):
        j_digital[i+1] += gpio[i]
    j_digital[15] += gnd
    j_digital[16] += aref

    # Analog header (1x06): A0-A5
    j_analog = Part("Connector_Generic", "Conn_01x06",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
    j_analog[1] += dac0_out
    j_analog[2] += gpio[14]
    j_analog[3] += gpio[15]
    j_analog[4] += gpio[16]
    j_analog[5] += gpio[17]
    j_analog[6] += gpio[18]

    # Power header (1x08): RESET, 3V3, 5V, GND, GND, VIN, SDA, SCL
    j_power = Part("Connector_Generic", "Conn_01x08",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
    j_power[1] += reset_n
    j_power[2] += v3v3
    j_power[3] += v5v
    j_power[4] += gnd
    j_power[5] += gnd
    j_power[6] += vin_raw
    j_power[7] += gpio[19]
    j_power[8] += gpio[20]

    # ICSP/SPI header (2x3)
    j_icsp = Part("Connector_Generic", "Conn_02x03_Odd_Even",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical")
    j_icsp[1] += gpio[12]
    j_icsp[2] += v5v
    j_icsp[3] += gpio[13]
    j_icsp[4] += gpio[11]
    j_icsp[5] += reset_n
    j_icsp[6] += gnd


# ============================================================
# Subcircuit: Status LEDs
# ============================================================
@subcircuit
def status_leds(v3v3, gnd, gpio):
    """Power LED + user LED (on D13)."""

    # Power LED (green)
    led_pwr = Part("Device", "LED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_pwr[1] += v3v3
    r_pwr[2] += led_pwr["A"]
    led_pwr["K"] += gnd

    # User LED on D13 (red)
    led_d13 = Part("Device", "LED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_d13 = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_d13[1] += gpio[13]
    r_d13[2] += led_d13["A"]
    led_d13["K"] += gnd


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
          neopixel_data,
          swdio, swdclk, xin, xout, xin32, xout32,
          aref, dac0_out, gpio)

power_supply(vin_raw, vin_switched, v5v, v3v3, vbus, gnd)

usb_interface(vbus, gnd, usb_dp, usb_dm)

qspi_flash(v3v3, gnd, qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3)

neopixel_led(v5v, gnd, neopixel_data)

reset_circuit(v3v3, gnd, reset_n)

swd_header(v3v3, gnd, swdio, swdclk, reset_n)

arduino_headers(v5v, v3v3, gnd, gpio, aref, dac0_out, reset_n, vin_raw)

status_leds(v3v3, gnd, gpio)

analog_ref(v3v3, gnd, aref)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)

print("Metro M4 Express schematic generated successfully!")
print(f"Parts: {len(default_circuit.parts)}")
print(f"Nets: {len(default_circuit.nets)}")
