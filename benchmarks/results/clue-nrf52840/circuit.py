"""CLUE nRF52840 Express — Sensor-packed dev board in BBC micro:bit form factor.

Nordic nRF52840 MCU with 1.3" IPS TFT, 9-DoF IMU, gesture/proximity/light,
humidity, barometric pressure, PDM mic, NeoPixel, buzzer, 2x white LEDs,
STEMMA QT I2C, edge connector, and 5 big pads.
"""

import os, sys

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *

set_default_tool(KICAD9)


from collections import defaultdict


def make_pin(num, name, func, orientation="R"):
    """Create a Pin with schematic-required attributes."""
    return Pin(num=num, name=name, func=func,
               x=0, y=0, orientation=orientation, length=100, rotation=0)


def add_skidl_draw_cmds(part):
    """Add rectangle + pin draw_cmds to a tool=SKIDL Part for schematic gen.

    Arranges pins evenly: left-side pins on the left, right-side on the right.
    Uses nested list format matching KiCad s-expression parsed structure.
    """
    pins = list(part.pins)
    n = len(pins)
    if n == 0:
        return

    # Pin spacing in mm (KiCad library units).
    spacing = 2.54
    pin_len = 2.54

    left_pins = pins[:n // 2]
    right_pins = pins[n // 2:]

    max_side = max(len(left_pins), len(right_pins), 1)
    body_h = max(max_side * spacing, spacing * 2)
    body_w = max(spacing * 4, spacing * 2)

    # Rectangle draw command (nested lists, matching Sexp format)
    rect_cmd = [
        "rectangle",
        ["start", -body_w / 2, -body_h / 2],
        ["end", body_w / 2, body_h / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]

    pin_cmds = []
    # Left-side pins (extend to the left of body, point right = angle 0)
    for i, pin in enumerate(left_pins):
        y = -body_h / 2 + spacing * (i + 0.5)
        x = -body_w / 2 - pin_len
        pin.x = x
        pin.y = y
        pin.orientation = "R"
        pin.rotation = 0
        pin_cmds.append([
            "pin", "passive", "line",
            ["at", x, y, 0],
            ["length", pin_len],
            ["name", pin.name, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num), ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    # Right-side pins (extend to the right of body, point left = angle 180)
    for i, pin in enumerate(right_pins):
        y = -body_h / 2 + spacing * (i + 0.5)
        x = body_w / 2 + pin_len
        pin.x = x
        pin.y = y
        pin.orientation = "L"
        pin.rotation = 180
        pin_cmds.append([
            "pin", "passive", "line",
            ["at", x, y, 180],
            ["length", pin_len],
            ["name", pin.name, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num), ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    # Store draw_cmds (unit 0 = shared graphics, unit 1 = pins + body)
    part.draw_cmds = defaultdict(list)
    part.draw_cmds[0] = [rect_cmd]
    part.draw_cmds[1] = pin_cmds + [rect_cmd]


# =============================================================================
# Power nets
# =============================================================================
vcc = Net("+3V3")
vcc.drive = POWER
vbat = Net("VBAT")
vbat.drive = POWER
gnd = Net("GND")
gnd.drive = POWER
vbus = Net("VBUS")
vbus.drive = POWER


# =============================================================================
# MCU — Nordic nRF52840 (QFN-73, AQFN-73)
# =============================================================================
@subcircuit
def mcu_block(vcc, gnd, sda, scl, tft_cs, tft_dc, tft_rst, tft_sck, tft_mosi,
              tft_lite, neo_data, buzzer, led_white1, led_white2, pdm_clk,
              pdm_data, btn_a, btn_b, uart_tx, uart_rx, d0, d1, d2, d3, d4,
              d5, d6, d7, d8, d9, d10, d16, qspi_sck, qspi_cs, qspi_d0,
              qspi_d1, qspi_d2, qspi_d3):
    """nRF52840 MCU with decoupling."""
    nrf = Part(
        name="nRF52840",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_DFN_QFN:QFN-48-1EP_7x7mm_P0.5mm_EP5.3x5.3mm",
        pins=[
            # Power
            make_pin("1", "VDD1", Pin.types.PWRIN),
            make_pin("13", "VDD2", Pin.types.PWRIN),
            make_pin("36", "VDD3", Pin.types.PWRIN),
            make_pin("49", "GNDPAD", Pin.types.PWRIN),
            make_pin("2", "VSS1", Pin.types.PWRIN),
            make_pin("31", "DEC1", Pin.types.PASSIVE),
            make_pin("30", "DEC2", Pin.types.PASSIVE),
            # I2C
            make_pin("3", "P0.26/SDA", Pin.types.BIDIR),
            make_pin("4", "P0.27/SCL", Pin.types.BIDIR),
            # SPI for TFT
            make_pin("5", "P0.05/TFT_CS", Pin.types.OUTPUT),
            make_pin("6", "P0.06/TFT_DC", Pin.types.OUTPUT),
            make_pin("7", "P1.01/TFT_RST", Pin.types.OUTPUT),
            make_pin("8", "P0.14/TFT_SCK", Pin.types.OUTPUT),
            make_pin("9", "P0.15/TFT_MOSI", Pin.types.OUTPUT),
            make_pin("10", "P1.05/TFT_LITE", Pin.types.OUTPUT),
            # NeoPixel
            make_pin("11", "P0.16/NEOPIXEL", Pin.types.OUTPUT),
            # Buzzer
            make_pin("12", "P1.00/BUZZER", Pin.types.OUTPUT),
            # White LEDs
            make_pin("14", "P0.17/LED_W1", Pin.types.OUTPUT),
            make_pin("15", "P1.10/LED_W2", Pin.types.OUTPUT),
            # PDM Microphone
            make_pin("16", "P0.00/PDM_CLK", Pin.types.OUTPUT),
            make_pin("17", "P0.01/PDM_DATA", Pin.types.INPUT),
            # Buttons
            make_pin("18", "P1.02/BTN_A", Pin.types.INPUT),
            make_pin("19", "P1.10b/BTN_B", Pin.types.INPUT),
            # UART
            make_pin("20", "P0.04/TX", Pin.types.OUTPUT),
            make_pin("21", "P0.03/RX", Pin.types.INPUT),
            # GPIO (edge connector)
            make_pin("22", "P0.02/D0", Pin.types.BIDIR),
            make_pin("23", "P0.03b/D1", Pin.types.BIDIR),
            make_pin("24", "P0.04b/D2", Pin.types.BIDIR),
            make_pin("25", "P0.30/D3", Pin.types.BIDIR),
            make_pin("26", "P0.28/D4", Pin.types.BIDIR),
            make_pin("27", "P0.14b/D5", Pin.types.BIDIR),
            make_pin("28", "P0.11/D6", Pin.types.BIDIR),
            make_pin("29", "P0.07/D7", Pin.types.BIDIR),
            make_pin("32", "P1.08/D8", Pin.types.BIDIR),
            make_pin("33", "P0.12/D9", Pin.types.BIDIR),
            make_pin("34", "P0.13/D10", Pin.types.BIDIR),
            make_pin("35", "P1.09/D16", Pin.types.BIDIR),
            # QSPI for flash
            make_pin("37", "P0.19/QSPI_SCK", Pin.types.OUTPUT),
            make_pin("38", "P0.20/QSPI_CS", Pin.types.OUTPUT),
            make_pin("39", "P0.17b/QSPI_D0", Pin.types.BIDIR),
            make_pin("40", "P0.22/QSPI_D1", Pin.types.BIDIR),
            make_pin("41", "P0.23/QSPI_D2", Pin.types.BIDIR),
            make_pin("42", "P0.21/QSPI_D3", Pin.types.BIDIR),
            # Crystal
            make_pin("43", "XC1", Pin.types.INPUT),
            make_pin("44", "XC2", Pin.types.OUTPUT),
            # Reset
            make_pin("45", "NRESET", Pin.types.INPUT),
            # USB
            make_pin("46", "USB_DM", Pin.types.BIDIR),
            make_pin("47", "USB_DP", Pin.types.BIDIR),
            make_pin("48", "VBUS_DET", Pin.types.INPUT),
        ],
    )

    # Power connections
    nrf["VDD1"] += vcc
    nrf["VDD2"] += vcc
    nrf["VDD3"] += vcc
    nrf["GNDPAD"] += gnd
    nrf["VSS1"] += gnd

    # Decoupling capacitors
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vcc
        c[2] += gnd

    # Decoupling pins (DEC1/DEC2 need 100nF to GND per datasheet)
    cdec1 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    cdec1[1] += nrf["DEC1"]
    cdec1[2] += gnd

    cdec2 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    cdec2[1] += nrf["DEC2"]
    cdec2[2] += gnd

    # Signal connections
    nrf["P0.26/SDA"] += sda
    nrf["P0.27/SCL"] += scl
    nrf["P0.05/TFT_CS"] += tft_cs
    nrf["P0.06/TFT_DC"] += tft_dc
    nrf["P1.01/TFT_RST"] += tft_rst
    nrf["P0.14/TFT_SCK"] += tft_sck
    nrf["P0.15/TFT_MOSI"] += tft_mosi
    nrf["P1.05/TFT_LITE"] += tft_lite
    nrf["P0.16/NEOPIXEL"] += neo_data
    nrf["P1.00/BUZZER"] += buzzer
    nrf["P0.17/LED_W1"] += led_white1
    nrf["P1.10/LED_W2"] += led_white2
    nrf["P0.00/PDM_CLK"] += pdm_clk
    nrf["P0.01/PDM_DATA"] += pdm_data
    nrf["P1.02/BTN_A"] += btn_a
    nrf["P1.10b/BTN_B"] += btn_b
    nrf["P0.04/TX"] += uart_tx
    nrf["P0.03/RX"] += uart_rx
    nrf["P0.02/D0"] += d0
    nrf["P0.03b/D1"] += d1
    nrf["P0.04b/D2"] += d2
    nrf["P0.30/D3"] += d3
    nrf["P0.28/D4"] += d4
    nrf["P0.14b/D5"] += d5
    nrf["P0.11/D6"] += d6
    nrf["P0.07/D7"] += d7
    nrf["P1.08/D8"] += d8
    nrf["P0.12/D9"] += d9
    nrf["P0.13/D10"] += d10
    nrf["P1.09/D16"] += d16
    nrf["P0.19/QSPI_SCK"] += qspi_sck
    nrf["P0.20/QSPI_CS"] += qspi_cs
    nrf["P0.17b/QSPI_D0"] += qspi_d0
    nrf["P0.22/QSPI_D1"] += qspi_d1
    nrf["P0.23/QSPI_D2"] += qspi_d2
    nrf["P0.21/QSPI_D3"] += qspi_d3

    # 32 MHz crystal
    xtal = Part("Device", "Crystal", value="32MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += nrf["XC1"]
    xtal[2] += nrf["XC2"]

    # Crystal load caps
    cxl1 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cxl1[1] += nrf["XC1"]
    cxl1[2] += gnd
    cxl2 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cxl2[1] += nrf["XC2"]
    cxl2[2] += gnd

    # Reset pullup
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vcc
    r_rst[2] += nrf["NRESET"]

    # USB data lines (exposed to USB connector via nets)
    usb_dm = Net("USB_DM")
    usb_dp = Net("USB_DP")
    nrf["USB_DM"] += usb_dm
    nrf["USB_DP"] += usb_dp
    nrf["VBUS_DET"] += vbus


# =============================================================================
# Power — 3.3V regulator from battery/USB input
# =============================================================================
@subcircuit
def power_supply(vbat, vbus, vcc, gnd):
    """3.3V LDO regulator with battery/USB input selection."""
    # AP2112K-3.3 LDO regulator (SOT-23-5)
    reg = Part(
        name="AP2112K-3.3",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
        pins=[
            make_pin("1", "VIN", Pin.types.PWRIN),
            make_pin("2", "GND", Pin.types.PWRIN),
            make_pin("3", "EN", Pin.types.INPUT),
            make_pin("4", "NC_PIN", Pin.types.NOCONNECT),
            make_pin("5", "VOUT", Pin.types.PWROUT),
        ],
    )

    reg["VIN"] += vbat
    reg["GND"] += gnd
    reg["EN"] += vbat  # Always enabled
    reg["VOUT"] += vcc
    reg["NC_PIN"] += NC

    # Input cap
    cin = Part("Device", "C", value="4.7uF",
               footprint="Capacitor_SMD:C_0805_2012Metric")
    cin[1] += vbat
    cin[2] += gnd

    # Output cap
    cout = Part("Device", "C", value="4.7uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    cout[1] += vcc
    cout[2] += gnd

    # Schottky diode for battery/USB OR-ing
    d1 = Part("Device", "D_Schottky", value="MBR120",
              footprint="Diode_SMD:D_SOD-123")
    d1[1] += vbus  # anode = USB
    d1[2] += vbat  # cathode = VBAT rail

    d2 = Part("Device", "D_Schottky", value="MBR120",
              footprint="Diode_SMD:D_SOD-123")
    # Battery connector feeds VBAT through a diode
    batt_in = Net("BATT_IN")
    batt_in.drive = POWER
    d2[1] += batt_in
    d2[2] += vbat

    # Battery connector (JST-PH 2-pin)
    j_batt = Part("Connector_Generic", "Conn_01x02",
                   footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    j_batt[1] += batt_in
    j_batt[2] += gnd


# =============================================================================
# USB-C connector
# =============================================================================
@subcircuit
def usb_connector(vbus, gnd):
    """USB-C connector for power and data."""
    usb = Part(
        name="USB_C_Receptacle",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="J",
        footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
        pins=[
            make_pin("A1", "GND_A", Pin.types.PWRIN),
            make_pin("A4", "VBUS_A", Pin.types.PWRIN),
            make_pin("A5", "CC1", Pin.types.BIDIR),
            make_pin("A6", "DP1", Pin.types.BIDIR),
            make_pin("A7", "DN1", Pin.types.BIDIR),
            make_pin("A8", "SBU1", Pin.types.BIDIR),
            make_pin("A9", "VBUS_A2", Pin.types.PWRIN),
            make_pin("A12", "GND_A2", Pin.types.PWRIN),
            make_pin("B1", "GND_B", Pin.types.PWRIN),
            make_pin("B4", "VBUS_B", Pin.types.PWRIN),
            make_pin("B5", "CC2", Pin.types.BIDIR),
            make_pin("B6", "DP2", Pin.types.BIDIR),
            make_pin("B7", "DN2", Pin.types.BIDIR),
            make_pin("B8", "SBU2", Pin.types.BIDIR),
            make_pin("B9", "VBUS_B2", Pin.types.PWRIN),
            make_pin("B12", "GND_B2", Pin.types.PWRIN),
            make_pin("S1", "SHIELD", Pin.types.PASSIVE),
        ],
    )

    usb["GND_A"] += gnd
    usb["GND_A2"] += gnd
    usb["GND_B"] += gnd
    usb["GND_B2"] += gnd
    usb["VBUS_A"] += vbus
    usb["VBUS_A2"] += vbus
    usb["VBUS_B"] += vbus
    usb["VBUS_B2"] += vbus
    usb["SHIELD"] += gnd

    # USB data
    usb_dm = Net("USB_DM")
    usb_dp = Net("USB_DP")
    usb["DN1"] += usb_dm
    usb["DN2"] += usb_dm
    usb["DP1"] += usb_dp
    usb["DP2"] += usb_dp

    # CC pulldown resistors (5.1K for UFP)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["CC1"]
    r_cc1[2] += gnd

    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2[1] += usb["CC2"]
    r_cc2[2] += gnd

    # SBU pins no connect
    usb["SBU1"] += NC
    usb["SBU2"] += NC


# =============================================================================
# TFT Display — 1.3" 240x240 IPS (ST7789)
# =============================================================================
@subcircuit
def tft_display(vcc, gnd, cs, dc, rst, sck, mosi, lite):
    """1.3 inch 240x240 IPS TFT with ST7789 driver."""
    tft = Part(
        name="ST7789_TFT",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Connector_FFC-FPC:Hirose_FH12-24S-0.5SH_1x24-1MP_P0.50mm_Horizontal",
        pins=[
            make_pin("1", "GND1", Pin.types.PWRIN),
            make_pin("2", "LEDK", Pin.types.PASSIVE),
            make_pin("3", "LEDA", Pin.types.PASSIVE),
            make_pin("4", "VDD", Pin.types.PWRIN),
            make_pin("5", "GND2", Pin.types.PWRIN),
            make_pin("6", "GND3", Pin.types.PWRIN),
            make_pin("7", "D/C", Pin.types.INPUT),
            make_pin("8", "CS", Pin.types.INPUT),
            make_pin("9", "SCL", Pin.types.INPUT),
            make_pin("10", "SDA", Pin.types.INPUT),
            make_pin("11", "RST", Pin.types.INPUT),
            make_pin("12", "VDD2", Pin.types.PWRIN),
            make_pin("13", "GND4", Pin.types.PWRIN),
            make_pin("14", "TE", Pin.types.OUTPUT),
            make_pin("15", "GND5", Pin.types.PWRIN),
            make_pin("16", "GND6", Pin.types.PWRIN),
            make_pin("17", "GND7", Pin.types.PWRIN),
            make_pin("18", "GND8", Pin.types.PWRIN),
            make_pin("19", "GND9", Pin.types.PWRIN),
            make_pin("20", "GND10", Pin.types.PWRIN),
            make_pin("21", "GND11", Pin.types.PWRIN),
            make_pin("22", "GND12", Pin.types.PWRIN),
            make_pin("23", "GND13", Pin.types.PWRIN),
            make_pin("24", "GND14", Pin.types.PWRIN),
        ],
    )

    tft["VDD"] += vcc
    tft["VDD2"] += vcc
    for p in ["GND1", "GND2", "GND3", "GND4", "GND5", "GND6", "GND7",
              "GND8", "GND9", "GND10", "GND11", "GND12", "GND13", "GND14"]:
        tft[p] += gnd

    tft["CS"] += cs
    tft["D/C"] += dc
    tft["RST"] += rst
    tft["SCL"] += sck
    tft["SDA"] += mosi

    # Backlight MOSFET for PWM control
    q_bl = Part(
        name="SI2301",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="Q",
        footprint="Package_TO_SOT_SMD:SOT-23",
        pins=[
            make_pin("1", "G", Pin.types.INPUT),
            make_pin("2", "S", Pin.types.PASSIVE),
            make_pin("3", "D", Pin.types.PASSIVE),
        ],
    )
    q_bl["G"] += lite
    q_bl["S"] += vcc
    q_bl["D"] += tft["LEDA"]
    tft["LEDK"] += gnd

    # Backlight resistor
    r_bl = Part("Device", "R", value="10R",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_bl[1] += tft["LEDA"]
    r_bl[2] += tft["LEDK"]

    # Decoupling
    c_tft = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_tft[1] += vcc
    c_tft[2] += gnd

    # TE pin not connected
    tft["TE"] += NC


# =============================================================================
# IMU — LSM6DS33 (Accel/Gyro) + LIS3MDL (Magnetometer) = 9-DoF
# =============================================================================
@subcircuit
def imu_9dof(vcc, gnd, sda, scl):
    """LSM6DS33 accel/gyro + LIS3MDL magnetometer on I2C."""
    # LSM6DS33 — LGA-14
    lsm = Part(
        name="LSM6DS33",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y",
        pins=[
            make_pin("1", "SDO/SA0", Pin.types.BIDIR),
            make_pin("2", "SDX", Pin.types.BIDIR),
            make_pin("3", "SCX", Pin.types.INPUT),
            make_pin("4", "INT1", Pin.types.OUTPUT),
            make_pin("5", "VDDIO", Pin.types.PWRIN),
            make_pin("6", "GND1", Pin.types.PWRIN),
            make_pin("7", "GND2", Pin.types.PWRIN),
            make_pin("8", "VDD", Pin.types.PWRIN),
            make_pin("9", "INT2", Pin.types.OUTPUT),
            make_pin("10", "OCS_AUX", Pin.types.BIDIR),
            make_pin("11", "ODA_AUX", Pin.types.BIDIR),
            make_pin("12", "SDO_AUX", Pin.types.BIDIR),
            make_pin("13", "CS", Pin.types.INPUT),
            make_pin("14", "SDA", Pin.types.BIDIR),
        ],
    )

    lsm["VDD"] += vcc
    lsm["VDDIO"] += vcc
    lsm["GND1"] += gnd
    lsm["GND2"] += gnd
    lsm["SDA"] += sda
    lsm["SCX"] += scl
    lsm["CS"] += vcc  # I2C mode (CS high)
    lsm["SDO/SA0"] += gnd  # Address select

    # Decoupling
    c_lsm = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_lsm[1] += vcc
    c_lsm[2] += gnd

    # Unused pins NC
    lsm["INT1"] += NC
    lsm["INT2"] += NC
    lsm["OCS_AUX"] += NC
    lsm["ODA_AUX"] += NC
    lsm["SDO_AUX"] += NC
    lsm["SDX"] += NC

    # LIS3MDL — LGA-12
    lis = Part(
        name="LIS3MDL",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_LGA:LGA-12_2x2mm_P0.5mm_LayoutBorder2x4y",
        pins=[
            make_pin("1", "SCL", Pin.types.INPUT),
            make_pin("2", "GND1", Pin.types.PWRIN),
            make_pin("3", "C1", Pin.types.PASSIVE),
            make_pin("4", "VDD", Pin.types.PWRIN),
            make_pin("5", "VDD_IO", Pin.types.PWRIN),
            make_pin("6", "INT", Pin.types.OUTPUT),
            make_pin("7", "DRDY", Pin.types.OUTPUT),
            make_pin("8", "SDA", Pin.types.BIDIR),
            make_pin("9", "SDO", Pin.types.OUTPUT),
            make_pin("10", "CS", Pin.types.INPUT),
            make_pin("11", "GND2", Pin.types.PWRIN),
            make_pin("12", "GND3", Pin.types.PWRIN),
        ],
    )

    lis["VDD"] += vcc
    lis["VDD_IO"] += vcc
    lis["GND1"] += gnd
    lis["GND2"] += gnd
    lis["GND3"] += gnd
    lis["SDA"] += sda
    lis["SCL"] += scl
    lis["CS"] += vcc  # I2C mode
    lis["SDO"] += gnd  # Address select

    # C1 decoupling (datasheet: 100nF)
    c_lis_c1 = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lis_c1[1] += lis["C1"]
    c_lis_c1[2] += gnd

    # VDD decoupling
    c_lis = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_lis[1] += vcc
    c_lis[2] += gnd

    lis["INT"] += NC
    lis["DRDY"] += NC


# =============================================================================
# APDS9960 — Proximity, Light, Color, Gesture sensor
# =============================================================================
@subcircuit
def apds9960_block(vcc, gnd, sda, scl):
    """APDS-9960 proximity/light/color/gesture sensor."""
    apds = Part(
        name="APDS-9960",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_LGA:LGA-4_2x2mm_P1mm",
        pins=[
            make_pin("1", "SDA", Pin.types.BIDIR),
            make_pin("2", "GND", Pin.types.PWRIN),
            make_pin("3", "VDD", Pin.types.PWRIN),
            make_pin("4", "SCL", Pin.types.INPUT),
            make_pin("5", "INT", Pin.types.OUTPUT),
            make_pin("6", "LDR", Pin.types.PASSIVE),
        ],
    )

    apds["VDD"] += vcc
    apds["GND"] += gnd
    apds["SDA"] += sda
    apds["SCL"] += scl

    # Decoupling
    c_apds = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_apds[1] += vcc
    c_apds[2] += gnd

    # Bulk cap
    c_apds_bulk = Part("Device", "C", value="1uF",
                       footprint="Capacitor_SMD:C_0402_1005Metric")
    c_apds_bulk[1] += vcc
    c_apds_bulk[2] += gnd

    apds["INT"] += NC
    apds["LDR"] += NC


# =============================================================================
# SHT30 — Humidity + Temperature sensor
# =============================================================================
@subcircuit
def sht_block(vcc, gnd, sda, scl):
    """SHT30/SHT31 humidity and temperature sensor."""
    sht = Part(
        name="SHT30-DIS",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_DFN_QFN:DFN-8-1EP_2.5x2.5mm_P0.5mm_EP1.1x1.7mm",
        pins=[
            make_pin("1", "SDA", Pin.types.BIDIR),
            make_pin("2", "ADDR", Pin.types.INPUT),
            make_pin("3", "ALERT", Pin.types.OUTPUT),
            make_pin("4", "SCL", Pin.types.INPUT),
            make_pin("5", "VDD", Pin.types.PWRIN),
            make_pin("6", "NRESET", Pin.types.INPUT),
            make_pin("7", "R", Pin.types.PASSIVE),
            make_pin("8", "VSS", Pin.types.PWRIN),
            make_pin("9", "GNDPAD", Pin.types.PWRIN),
        ],
    )

    sht["VDD"] += vcc
    sht["VSS"] += gnd
    sht["GNDPAD"] += gnd
    sht["SDA"] += sda
    sht["SCL"] += scl
    sht["ADDR"] += gnd  # Address 0x44
    sht["NRESET"] += vcc  # Not reset

    # Decoupling
    c_sht = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_sht[1] += vcc
    c_sht[2] += gnd

    sht["ALERT"] += NC
    sht["R"] += NC


# =============================================================================
# BMP280 — Barometric pressure / temperature / altitude
# =============================================================================
@subcircuit
def bmp280_block(vcc, gnd, sda, scl):
    """BMP280 barometric pressure and temperature sensor."""
    bmp = Part(
        name="BMP280",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_LGA:Bosch_LGA-8_2x2.5mm_P0.65mm_ClockwisePinNumbering",
        pins=[
            make_pin("1", "GND", Pin.types.PWRIN),
            make_pin("2", "CSB", Pin.types.INPUT),
            make_pin("3", "SDI", Pin.types.BIDIR),
            make_pin("4", "SCK", Pin.types.INPUT),
            make_pin("5", "SDO", Pin.types.OUTPUT),
            make_pin("6", "VDDIO", Pin.types.PWRIN),
            make_pin("7", "GND2", Pin.types.PWRIN),
            make_pin("8", "VDD", Pin.types.PWRIN),
        ],
    )

    bmp["VDD"] += vcc
    bmp["VDDIO"] += vcc
    bmp["GND"] += gnd
    bmp["GND2"] += gnd
    bmp["SDI"] += sda
    bmp["SCK"] += scl
    bmp["CSB"] += vcc  # I2C mode
    bmp["SDO"] += gnd  # Address 0x76

    # Decoupling
    c_bmp = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bmp[1] += vcc
    c_bmp[2] += gnd


# =============================================================================
# PDM Microphone
# =============================================================================
@subcircuit
def pdm_mic_block(vcc, gnd, clk, data):
    """PDM MEMS microphone (MP34DT01-like)."""
    mic = Part(
        name="PDM_MIC",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_LGA:LGA-5_3.76x2.95mm_P1.6mm",
        pins=[
            make_pin("1", "VDD", Pin.types.PWRIN),
            make_pin("2", "LR", Pin.types.INPUT),
            make_pin("3", "CLK", Pin.types.INPUT),
            make_pin("4", "DATA", Pin.types.OUTPUT),
            make_pin("5", "GND", Pin.types.PWRIN),
        ],
    )

    mic["VDD"] += vcc
    mic["GND"] += gnd
    mic["CLK"] += clk
    mic["DATA"] += data
    mic["LR"] += gnd  # Channel select

    # Decoupling
    c_mic = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_mic[1] += vcc
    c_mic[2] += gnd


# =============================================================================
# 2MB QSPI Flash (GD25Q16C or similar)
# =============================================================================
@subcircuit
def qspi_flash(vcc, gnd, sck, cs, d0, d1, d2, d3):
    """2MB QSPI NOR flash."""
    flash = Part(
        name="GD25Q16C",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_SO:SOIC-8_5.23x5.23mm_P1.27mm",
        pins=[
            make_pin("1", "CS", Pin.types.INPUT),
            make_pin("2", "DO/IO1", Pin.types.BIDIR),
            make_pin("3", "WP/IO2", Pin.types.BIDIR),
            make_pin("4", "GND", Pin.types.PWRIN),
            make_pin("5", "DI/IO0", Pin.types.BIDIR),
            make_pin("6", "CLK", Pin.types.INPUT),
            make_pin("7", "HOLD/IO3", Pin.types.BIDIR),
            make_pin("8", "VCC", Pin.types.PWRIN),
        ],
    )

    flash["VCC"] += vcc
    flash["GND"] += gnd
    flash["CLK"] += sck
    flash["CS"] += cs
    flash["DI/IO0"] += d0
    flash["DO/IO1"] += d1
    flash["WP/IO2"] += d2
    flash["HOLD/IO3"] += d3

    # Decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_flash[1] += vcc
    c_flash[2] += gnd


# =============================================================================
# NeoPixel RGB LED (WS2812B)
# =============================================================================
@subcircuit
def neopixel_block(vcc, gnd, data_in):
    """Single WS2812B NeoPixel RGB LED."""
    neo = Part(
        name="WS2812B",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="D",
        footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
        pins=[
            make_pin("1", "VDD", Pin.types.PWRIN),
            make_pin("2", "DOUT", Pin.types.OUTPUT),
            make_pin("3", "VSS", Pin.types.PWRIN),
            make_pin("4", "DIN", Pin.types.INPUT),
        ],
    )

    neo["VDD"] += vcc
    neo["VSS"] += gnd
    neo["DIN"] += data_in

    # Decoupling
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_neo[1] += vcc
    c_neo[2] += gnd

    neo["DOUT"] += NC


# =============================================================================
# Buzzer / Speaker
# =============================================================================
@subcircuit
def buzzer_block(gnd, ctrl):
    """Magnetic buzzer with MOSFET driver."""
    buz = Part(
        name="Buzzer",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="LS",
        footprint="Buzzer_Beeper:Buzzer_12x9.5RM7.6",
        pins=[
            make_pin("1", "+", Pin.types.PASSIVE),
            make_pin("2", "-", Pin.types.PASSIVE),
        ],
    )

    # N-FET driver
    q_buz = Part(
        name="2N7002",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="Q",
        footprint="Package_TO_SOT_SMD:SOT-23",
        pins=[
            make_pin("1", "G", Pin.types.INPUT),
            make_pin("2", "S", Pin.types.PASSIVE),
            make_pin("3", "D", Pin.types.PASSIVE),
        ],
    )

    buz_vcc = Net("+3V3")
    q_buz["G"] += ctrl
    q_buz["S"] += gnd
    q_buz["D"] += buz["-"]
    buz["+"] += buz_vcc

    # Gate pulldown
    r_gate = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_gate[1] += ctrl
    r_gate[2] += gnd


# =============================================================================
# White LEDs (2x for illumination/color sensing)
# =============================================================================
@subcircuit
def white_leds(vcc, gnd, ctrl1, ctrl2):
    """Two white LEDs with current-limiting resistors."""
    for ctrl in [ctrl1, ctrl2]:
        led = Part("Device", "LED", value="White",
                   footprint="LED_SMD:LED_0603_1608Metric")
        r_led = Part("Device", "R", value="68R",
                     footprint="Resistor_SMD:R_0402_1005Metric")
        r_led[1] += ctrl
        r_led[2] += led[1]
        led[2] += gnd


# =============================================================================
# Buttons A and B
# =============================================================================
@subcircuit
def buttons(gnd, btn_a, btn_b):
    """Two user buttons with pullups (internal to nRF, external caps for debounce)."""
    for btn_net in [btn_a, btn_b]:
        sw = Part("Switch", "SW_Push", value="BTN",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_6x6mm_H9.5mm")
        sw[1] += btn_net
        sw[2] += gnd

        # Debounce cap
        c_deb = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0402_1005Metric")
        c_deb[1] += btn_net
        c_deb[2] += gnd


# =============================================================================
# STEMMA QT / Qwiic I2C connector
# =============================================================================
@subcircuit
def stemma_qt(vcc, gnd, sda, scl):
    """STEMMA QT / Qwiic JST SH 4-pin I2C connector."""
    j_qt = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    j_qt[1] += gnd
    j_qt[2] += vcc
    j_qt[3] += sda
    j_qt[4] += scl


# =============================================================================
# I2C Pullups
# =============================================================================
@subcircuit
def i2c_pullups(vcc, sda, scl):
    """I2C bus pullup resistors."""
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl[1] += vcc
    r_scl[2] += scl


# =============================================================================
# Edge Connector (BBC micro:bit compatible, 25 pins)
# =============================================================================
@subcircuit
def edge_connector(vcc, gnd, d0, d1, d2, d3, d4, d5, d6, d7, d8, d9, d10,
                   d16, sda, scl):
    """BBC micro:bit compatible edge connector."""
    edge = Part("Connector_Generic", "Conn_01x25",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x25_P2.54mm_Vertical")
    # micro:bit pin mapping (simplified)
    edge[1] += gnd
    edge[2] += vcc
    edge[3] += d0   # P0 (big pad 0)
    edge[4] += d1   # P1 (big pad 1)
    edge[5] += d2   # P2 (big pad 2)
    edge[6] += d3   # P3
    edge[7] += d4   # P4
    edge[8] += d5   # P5
    edge[9] += d6   # P6
    edge[10] += d7  # P7
    edge[11] += d8  # P8
    edge[12] += d9  # P9
    edge[13] += d10 # P10
    edge[14] += gnd
    edge[15] += vcc
    edge[16] += d16 # P16
    edge[17] += gnd
    edge[18] += gnd
    edge[19] += sda # P19/SDA
    edge[20] += scl # P20/SCL
    edge[21] += gnd
    edge[22] += gnd
    edge[23] += gnd
    edge[24] += gnd
    edge[25] += vcc


# =============================================================================
# Build the circuit
# =============================================================================

# I2C bus nets
sda = Net("SDA")
scl = Net("SCL")

# TFT control nets
tft_cs = Net("TFT_CS")
tft_dc = Net("TFT_DC")
tft_rst = Net("TFT_RST")
tft_sck = Net("TFT_SCK")
tft_mosi = Net("TFT_MOSI")
tft_lite = Net("TFT_LITE")

# NeoPixel data
neo_data = Net("NEOPIXEL")

# Buzzer control
buzzer_ctrl = Net("BUZZER")

# White LED controls
led_w1 = Net("LED_W1")
led_w2 = Net("LED_W2")

# PDM microphone
pdm_clk = Net("PDM_CLK")
pdm_data_net = Net("PDM_DATA")

# Buttons
btn_a = Net("BTN_A")
btn_b = Net("BTN_B")

# UART
uart_tx = Net("UART_TX")
uart_rx = Net("UART_RX")

# GPIO for edge connector
d0 = Net("D0")
d1 = Net("D1")
d2 = Net("D2")
d3 = Net("D3")
d4 = Net("D4")
d5 = Net("D5")
d6 = Net("D6")
d7 = Net("D7")
d8 = Net("D8")
d9 = Net("D9")
d10 = Net("D10")
d16 = Net("D16")

# QSPI flash
qspi_sck = Net("QSPI_SCK")
qspi_cs = Net("QSPI_CS")
qspi_d0 = Net("QSPI_D0")
qspi_d1 = Net("QSPI_D1")
qspi_d2 = Net("QSPI_D2")
qspi_d3 = Net("QSPI_D3")

# Instantiate all subcircuits
power_supply(vbat, vbus, vcc, gnd)

usb_connector(vbus, gnd)

mcu_block(vcc, gnd, sda, scl, tft_cs, tft_dc, tft_rst, tft_sck, tft_mosi,
          tft_lite, neo_data, buzzer_ctrl, led_w1, led_w2, pdm_clk,
          pdm_data_net, btn_a, btn_b, uart_tx, uart_rx, d0, d1, d2, d3, d4,
          d5, d6, d7, d8, d9, d10, d16, qspi_sck, qspi_cs, qspi_d0,
          qspi_d1, qspi_d2, qspi_d3)

tft_display(vcc, gnd, tft_cs, tft_dc, tft_rst, tft_sck, tft_mosi, tft_lite)

imu_9dof(vcc, gnd, sda, scl)

apds9960_block(vcc, gnd, sda, scl)

sht_block(vcc, gnd, sda, scl)

bmp280_block(vcc, gnd, sda, scl)

pdm_mic_block(vcc, gnd, pdm_clk, pdm_data_net)

qspi_flash(vcc, gnd, qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3)

neopixel_block(vcc, gnd, neo_data)

buzzer_block(gnd, buzzer_ctrl)

white_leds(vcc, gnd, led_w1, led_w2)

buttons(gnd, btn_a, btn_b)

stemma_qt(vcc, gnd, sda, scl)

i2c_pullups(vcc, sda, scl)

edge_connector(vcc, gnd, d0, d1, d2, d3, d4, d5, d6, d7, d8, d9, d10,
               d16, sda, scl)

# =============================================================================
# Add draw_cmds and lib stubs to SKIDL parts for schematic generation
# =============================================================================
class _FakeLib:
    """Minimal lib stub so sexp_schematic can write lib_id."""
    def __init__(self, name="skidl"):
        self.filename = name

_fake_lib = _FakeLib()

for part in default_circuit.parts:
    if not hasattr(part, "draw_cmds") or not part.draw_cmds:
        add_skidl_draw_cmds(part)
    if not hasattr(part, "lib") or part.lib is None:
        try:
            _ = part.lib.filename
        except (AttributeError, TypeError):
            part.lib = _fake_lib

# =============================================================================
# Generate schematic
# =============================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)

print("\n--- Circuit generated successfully ---")
print(f"Parts: {len(default_circuit.parts)}")
print(f"Nets:  {len(default_circuit.nets)}")
