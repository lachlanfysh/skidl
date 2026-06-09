"""Feather nRF52840 Sense (Bluefruit) — Feather form factor with full sensor suite.

Nordic nRF52840 ARM Cortex M4F @ 64MHz, 1MB flash, 256KB SRAM, BLE 5.0.
Sensors: LSM6DS33 Accel/Gyro + LIS3MDL magnetometer (9-DoF), APDS9960
proximity/light/color/gesture, PDM microphone, SHT30 humidity, BMP280
barometric pressure/temperature. NeoPixel RGB LED, USB-C, LiPo charging,
21 GPIO, 6x 12-bit ADC, SWD debug header. Feather headers (1x12 + 1x16).
"""

import os, sys

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from skidl import *

set_default_tool(KICAD9)

from collections import defaultdict


def make_pin(num, name, func, orientation="R"):
    """Create a Pin with schematic-required attributes."""
    return Pin(num=num, name=name, func=func,
               x=0, y=0, orientation=orientation, length=100, rotation=0)


def add_skidl_draw_cmds(part):
    """Add rectangle + pin draw_cmds to a tool=SKIDL Part for schematic gen."""
    pins = list(part.pins)
    n = len(pins)
    if n == 0:
        return

    spacing = 2.54
    pin_len = 2.54

    left_pins = pins[:n // 2]
    right_pins = pins[n // 2:]

    max_side = max(len(left_pins), len(right_pins), 1)
    body_h = max(max_side * spacing, spacing * 2)
    body_w = max(spacing * 4, spacing * 2)

    rect_cmd = [
        "rectangle",
        ["start", -body_w / 2, -body_h / 2],
        ["end", body_w / 2, body_h / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]

    pin_cmds = []
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
# MCU — Nordic nRF52840 (AQFN-73 package)
# =============================================================================
@subcircuit
def mcu_block(vcc, gnd, sda, scl, neo_data, pdm_clk, pdm_data,
              btn_usr, uart_tx, uart_rx, a0, a1, a2, a3, a4, a5,
              d5, d6, d9, d10, d11, d12, d13,
              qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3):
    """nRF52840 MCU with decoupling, crystal, and antenna matching."""
    nrf = Part(
        name="nRF52840",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_DFN_QFN:Nordic_AQFN-73-1EP_7x7mm_P0.5mm",
        pins=[
            # Power
            make_pin("1", "DEC1", Pin.types.PASSIVE),
            make_pin("2", "P0.00/XL1", Pin.types.BIDIR),
            make_pin("3", "P0.01/XL2", Pin.types.BIDIR),
            make_pin("4", "P0.02/AIN0", Pin.types.BIDIR),
            make_pin("5", "P0.03/AIN1", Pin.types.BIDIR),
            make_pin("6", "P0.04/AIN2", Pin.types.BIDIR),
            make_pin("7", "P0.05/AIN3", Pin.types.BIDIR),
            make_pin("8", "P0.06", Pin.types.BIDIR),
            make_pin("9", "P0.07", Pin.types.BIDIR),
            make_pin("10", "P0.08", Pin.types.BIDIR),
            make_pin("11", "P0.09/NFC1", Pin.types.BIDIR),
            make_pin("12", "P0.10/NFC2", Pin.types.BIDIR),
            make_pin("13", "VDD1", Pin.types.PWRIN),
            make_pin("14", "P0.11", Pin.types.BIDIR),
            make_pin("15", "P0.12", Pin.types.BIDIR),
            make_pin("16", "P0.13", Pin.types.BIDIR),
            make_pin("17", "P0.14", Pin.types.BIDIR),
            make_pin("18", "P0.15", Pin.types.BIDIR),
            make_pin("19", "P0.16", Pin.types.BIDIR),
            make_pin("20", "P0.17", Pin.types.BIDIR),
            make_pin("21", "P0.18/NRESET", Pin.types.INPUT),
            make_pin("22", "P0.19", Pin.types.BIDIR),
            make_pin("23", "P0.20", Pin.types.BIDIR),
            make_pin("24", "P0.21", Pin.types.BIDIR),
            make_pin("25", "P0.22", Pin.types.BIDIR),
            make_pin("26", "P0.23", Pin.types.BIDIR),
            make_pin("27", "P0.24", Pin.types.BIDIR),
            make_pin("28", "P0.25", Pin.types.BIDIR),
            make_pin("29", "ANT", Pin.types.PASSIVE),
            make_pin("30", "VSS1", Pin.types.PWRIN),
            make_pin("31", "DEC2", Pin.types.PASSIVE),
            make_pin("32", "DEC3", Pin.types.PASSIVE),
            make_pin("33", "XC1", Pin.types.INPUT),
            make_pin("34", "XC2", Pin.types.OUTPUT),
            make_pin("35", "VDD2", Pin.types.PWRIN),
            make_pin("36", "P0.26", Pin.types.BIDIR),
            make_pin("37", "P0.27", Pin.types.BIDIR),
            make_pin("38", "P0.28/AIN4", Pin.types.BIDIR),
            make_pin("39", "P0.29/AIN5", Pin.types.BIDIR),
            make_pin("40", "P0.30/AIN6", Pin.types.BIDIR),
            make_pin("41", "P0.31/AIN7", Pin.types.BIDIR),
            make_pin("42", "NC1", Pin.types.NOCONNECT),
            make_pin("43", "NC2", Pin.types.NOCONNECT),
            make_pin("44", "P1.00", Pin.types.BIDIR),
            make_pin("45", "P1.01", Pin.types.BIDIR),
            make_pin("46", "P1.02", Pin.types.BIDIR),
            make_pin("47", "P1.03", Pin.types.BIDIR),
            make_pin("48", "P1.04", Pin.types.BIDIR),
            make_pin("49", "P1.05", Pin.types.BIDIR),
            make_pin("50", "P1.06", Pin.types.BIDIR),
            make_pin("51", "P1.07", Pin.types.BIDIR),
            make_pin("52", "P1.08", Pin.types.BIDIR),
            make_pin("53", "P1.09", Pin.types.BIDIR),
            make_pin("54", "VDD3", Pin.types.PWRIN),
            make_pin("55", "P1.10", Pin.types.BIDIR),
            make_pin("56", "P1.11", Pin.types.BIDIR),
            make_pin("57", "P1.12", Pin.types.BIDIR),
            make_pin("58", "P1.13", Pin.types.BIDIR),
            make_pin("59", "P1.14", Pin.types.BIDIR),
            make_pin("60", "P1.15", Pin.types.BIDIR),
            make_pin("61", "VDD4", Pin.types.PWRIN),
            make_pin("62", "USB_DM", Pin.types.BIDIR),
            make_pin("63", "USB_DP", Pin.types.BIDIR),
            make_pin("64", "VBUS_DET", Pin.types.INPUT),
            make_pin("65", "DEC4", Pin.types.PASSIVE),
            make_pin("66", "USB_REG_VOUT", Pin.types.PWROUT),
            make_pin("67", "DEC5", Pin.types.PASSIVE),
            make_pin("68", "DEC6", Pin.types.PASSIVE),
            make_pin("69", "VDD5", Pin.types.PWRIN),
            make_pin("70", "SWDIO", Pin.types.BIDIR),
            make_pin("71", "SWDCLK", Pin.types.INPUT),
            make_pin("72", "VSS2", Pin.types.PWRIN),
            make_pin("73", "GNDPAD", Pin.types.PWRIN),
        ],
    )

    # Power connections
    for p in ["VDD1", "VDD2", "VDD3", "VDD4", "VDD5"]:
        nrf[p] += vcc
    for p in ["VSS1", "VSS2", "GNDPAD"]:
        nrf[p] += gnd
    nrf["NC1"] += NC
    nrf["NC2"] += NC

    # Decoupling capacitors (4x 100nF for VDD pins)
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vcc
        c[2] += gnd

    # DEC pins need 100nF to GND per datasheet
    for dec_pin in ["DEC1", "DEC2", "DEC3", "DEC4", "DEC5", "DEC6"]:
        cdec = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
        cdec[1] += nrf[dec_pin]
        cdec[2] += gnd

    # 32.768 kHz crystal (low-frequency clock for BLE timing)
    xtal_lf = Part("Device", "Crystal", value="32.768kHz",
                   footprint="Crystal:Crystal_SMD_2012-2Pin_2.0x1.2mm")
    xtal_lf[1] += nrf["P0.00/XL1"]
    xtal_lf[2] += nrf["P0.01/XL2"]

    # 32 MHz crystal (high-frequency clock)
    xtal_hf = Part("Device", "Crystal", value="32MHz",
                   footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal_hf[1] += nrf["XC1"]
    xtal_hf[2] += nrf["XC2"]

    # Crystal load caps for HF
    for xpin in ["XC1", "XC2"]:
        cxl = Part("Device", "C", value="12pF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
        cxl[1] += nrf[xpin]
        cxl[2] += gnd

    # Antenna matching network (simple pi network)
    ant_net = Net("ANT_FEED")
    nrf["ANT"] += ant_net

    # Chip antenna (2.4GHz BLE)
    ant = Part(
        name="ANT_2.4GHz",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="AE",
        footprint="RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz",
        pins=[
            make_pin("1", "FEED", Pin.types.PASSIVE),
            make_pin("2", "GND", Pin.types.PASSIVE),
        ],
    )
    ant["GND"] += gnd

    # Matching: series inductor + shunt cap
    l_match = Part("Device", "L", value="3.9nH",
                   footprint="Inductor_SMD:L_0402_1005Metric")
    l_match[1] += ant_net
    l_match[2] += ant["FEED"]

    c_match = Part("Device", "C", value="0.8pF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_match[1] += ant_net
    c_match[2] += gnd

    # Reset pullup + button
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vcc
    r_rst[2] += nrf["P0.18/NRESET"]

    sw_rst = Part("Switch", "SW_Push", value="RESET",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_rst[1] += nrf["P0.18/NRESET"]
    sw_rst[2] += gnd

    # USB data lines
    usb_dm = Net("USB_DM")
    usb_dp = Net("USB_DP")
    nrf["USB_DM"] += usb_dm
    nrf["USB_DP"] += usb_dp
    nrf["VBUS_DET"] += vbus

    # USB regulator output decoupling
    c_usbreg = Part("Device", "C", value="1uF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usbreg[1] += nrf["USB_REG_VOUT"]
    c_usbreg[2] += gnd

    # SWD debug header (2x5 1.27mm)
    swd_net_dio = Net("SWDIO")
    swd_net_clk = Net("SWDCLK")
    nrf["SWDIO"] += swd_net_dio
    nrf["SWDCLK"] += swd_net_clk

    # Signal pin assignments (matching Adafruit Feather nRF52840 Sense)
    nrf["P0.26"] += sda       # I2C SDA
    nrf["P0.27"] += scl       # I2C SCL
    nrf["P0.16"] += neo_data  # NeoPixel
    nrf["P0.00/XL1"]  # Already used for LF crystal
    nrf["P1.00"] += pdm_clk   # PDM CLK
    nrf["P0.06"] += pdm_data  # PDM DATA
    nrf["P1.02"] += btn_usr   # User switch (active low)
    nrf["P0.24"] += uart_tx   # UART TX
    nrf["P0.25"] += uart_rx   # UART RX

    # Analog pins (A0-A5 on Feather header)
    nrf["P0.04/AIN2"] += a0
    nrf["P0.05/AIN3"] += a1
    nrf["P0.30/AIN6"] += a2
    nrf["P0.28/AIN4"] += a3
    nrf["P0.02/AIN0"] += a4
    nrf["P0.03/AIN1"] += a5

    # Digital pins
    nrf["P0.14"] += d5
    nrf["P0.13"] += d6
    nrf["P0.15"] += d9
    nrf["P1.06"] += d10
    nrf["P1.08"] += d11
    nrf["P1.09"] += d12
    nrf["P0.08"] += d13     # Built-in red LED

    # QSPI flash
    nrf["P0.19"] += qspi_sck
    nrf["P0.20"] += qspi_cs
    nrf["P0.17"] += qspi_d0
    nrf["P0.22"] += qspi_d1
    nrf["P0.23"] += qspi_d2
    nrf["P0.21"] += qspi_d3

    # Unused GPIO pins as NC
    for p in ["P0.07", "P0.09/NFC1", "P0.10/NFC2", "P0.11", "P0.12",
              "P1.01", "P1.03", "P1.04", "P1.05", "P1.07",
              "P1.10", "P1.11", "P1.12", "P1.13", "P1.14", "P1.15",
              "P0.29/AIN5", "P0.31/AIN7", "P1.10"]:
        try:
            nrf[p] += NC
        except Exception:
            pass  # Duplicate pin names handled


# =============================================================================
# Power — LDO Regulator + LiPo Charger
# =============================================================================
@subcircuit
def power_supply(vbat, vbus, vcc, gnd):
    """AP2112K-3.3 LDO regulator + MCP73831 LiPo charger."""
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

    # 100nF decoupling
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vcc
    c_dec[2] += gnd

    # Schottky diode for battery/USB OR-ing
    d_usb = Part("Device", "D_Schottky", value="MBR120",
                 footprint="Diode_SMD:D_SOD-123")
    d_usb[1] += vbus   # anode = USB
    d_usb[2] += vbat   # cathode = VBAT rail

    # MCP73831 LiPo charger (SOT-23-5)
    chg = Part(
        name="MCP73831",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
        pins=[
            make_pin("1", "STAT", Pin.types.OUTPUT),
            make_pin("2", "VSS", Pin.types.PWRIN),
            make_pin("3", "VBAT", Pin.types.PWROUT),
            make_pin("4", "VDD", Pin.types.PWRIN),
            make_pin("5", "PROG", Pin.types.PASSIVE),
        ],
    )

    chg["VDD"] += vbus
    chg["VSS"] += gnd
    chg["VBAT"] += vbat

    # Charge current set resistor (2K = 500mA)
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_prog[1] += chg["PROG"]
    r_prog[2] += gnd

    # Charge status LED (orange)
    led_chg = Part("Device", "LED", value="Orange",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_led_chg = Part("Device", "R", value="1K",
                     footprint="Resistor_SMD:R_0402_1005Metric")
    r_led_chg[1] += vbus
    r_led_chg[2] += led_chg[1]
    led_chg[2] += chg["STAT"]

    # Battery connector (JST-PH 2-pin)
    j_batt = Part("Connector_Generic", "Conn_01x02",
                  footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    j_batt[1] += vbat
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

    # CC pulldown resistors (5.1K for UFP/device mode)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["CC1"]
    r_cc1[2] += gnd

    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2[1] += usb["CC2"]
    r_cc2[2] += gnd

    # SBU pins not used
    usb["SBU1"] += NC
    usb["SBU2"] += NC


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
    lsm["CS"] += vcc  # I2C mode
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
        footprint="Package_LGA:LGA-12_2x2mm_P0.5mm",
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

    # C1 decoupling
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
        footprint="Package_DFN_QFN:DFN-8-1EP_3x3mm_P0.5mm_EP1.65x2.38mm",
        pins=[
            make_pin("1", "SDA", Pin.types.BIDIR),
            make_pin("2", "GND1", Pin.types.PWRIN),
            make_pin("3", "LEDA", Pin.types.PASSIVE),
            make_pin("4", "SCL", Pin.types.INPUT),
            make_pin("5", "VDD", Pin.types.PWRIN),
            make_pin("6", "INT", Pin.types.OUTPUT),
            make_pin("7", "LDR", Pin.types.PASSIVE),
            make_pin("8", "GND2", Pin.types.PWRIN),
            make_pin("9", "GNDPAD", Pin.types.PWRIN),
        ],
    )

    apds["VDD"] += vcc
    apds["GND1"] += gnd
    apds["GND2"] += gnd
    apds["GNDPAD"] += gnd
    apds["SDA"] += sda
    apds["SCL"] += scl

    # LED current limit resistor for proximity IR LED
    r_ldr = Part("Device", "R", value="10R",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_ldr[1] += vcc
    r_ldr[2] += apds["LEDA"]

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
    """SHT30 humidity and temperature sensor."""
    sht = Part(
        name="SHT30-DIS",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Sensor_Humidity:Sensirion_DFN-8-1EP_2.5x2.5mm_P0.5mm_EP1.1x1.7mm",
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
# PDM Microphone (MP34DT01-like / SPH0645)
# =============================================================================
@subcircuit
def pdm_mic_block(vcc, gnd, clk, data):
    """PDM MEMS microphone."""
    mic = Part(
        name="PDM_MIC",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Sensor_Audio:Knowles_SPH0645LM4H-6_3.5x2.65mm",
        pins=[
            make_pin("1", "VDD", Pin.types.PWRIN),
            make_pin("2", "LR", Pin.types.INPUT),
            make_pin("3", "CLK", Pin.types.INPUT),
            make_pin("4", "DATA", Pin.types.OUTPUT),
            make_pin("5", "GND", Pin.types.PWRIN),
            make_pin("6", "GND2", Pin.types.PWRIN),
        ],
    )

    mic["VDD"] += vcc
    mic["GND"] += gnd
    mic["GND2"] += gnd
    mic["CLK"] += clk
    mic["DATA"] += data
    mic["LR"] += gnd  # Channel select

    # Decoupling
    c_mic = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_mic[1] += vcc
    c_mic[2] += gnd


# =============================================================================
# 2MB QSPI Flash (GD25Q16C)
# =============================================================================
@subcircuit
def qspi_flash(vcc, gnd, sck, cs, d0, d1, d2, d3):
    """2MB QSPI NOR flash for CircuitPython storage."""
    flash = Part(
        name="GD25Q16C",
        tool=SKIDL,
        dest=NETLIST,
        ref_prefix="U",
        footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
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
    """Single NeoPixel RGB LED."""
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
# User Button
# =============================================================================
@subcircuit
def user_button(gnd, btn_net):
    """User button with debounce cap."""
    sw = Part("Switch", "SW_Push", value="SW_USR",
              footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw[1] += btn_net
    sw[2] += gnd

    # Debounce cap
    c_deb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_deb[1] += btn_net
    c_deb[2] += gnd


# =============================================================================
# Built-in Red LED (D13)
# =============================================================================
@subcircuit
def builtin_led(gnd, ctrl):
    """Red LED on D13 with current-limiting resistor."""
    led = Part("Device", "LED", value="Red",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_led[1] += ctrl
    r_led[2] += led[1]
    led[2] += gnd


# =============================================================================
# Blue BLE Connection LED
# =============================================================================
@subcircuit
def ble_led(vcc, gnd):
    """Blue BLE connection indicator LED."""
    ble_ctrl = Net("BLE_LED")
    led = Part("Device", "LED", value="Blue",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_led[1] += ble_ctrl
    r_led[2] += led[1]
    led[2] += gnd


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
# SWD Debug Header
# =============================================================================
@subcircuit
def swd_header(vcc, gnd):
    """2x5 1.27mm SWD debug header."""
    swd = Part("Connector_Generic", "Conn_02x05_Odd_Even",
               footprint="Connector_PinHeader_1.27mm:PinHeader_2x05_P1.27mm_Vertical")
    swdio = Net("SWDIO")
    swdclk = Net("SWDCLK")

    swd[1] += vcc        # VTref
    swd[2] += vcc        # VSUPPLY
    swd[3] += gnd        # GND
    swd[4] += gnd        # GND
    swd[5] += gnd        # GND (key)
    swd[6] += gnd        # GND
    swd[7] += swdio      # SWDIO
    swd[8] += gnd        # GND
    swd[9] += swdclk     # SWDCLK
    swd[10] += gnd       # GND


# =============================================================================
# Feather Headers (1x16 + 1x12)
# =============================================================================
@subcircuit
def feather_headers(vcc, vbat, vbus, gnd,
                    a0, a1, a2, a3, a4, a5,
                    d5, d6, d9, d10, d11, d12, d13,
                    sda, scl, uart_tx, uart_rx):
    """Feather form factor pin headers."""
    # Left header (1x16)
    j_left = Part("Connector_Generic", "Conn_01x16",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    j_left[1] += gnd       # GND (RST side)
    j_left[2] += vbus      # USB (5V)
    j_left[3] += vbat      # VBAT
    j_left[4] += gnd       # GND
    j_left[5] += vcc       # 3V3
    j_left[6] += a0        # A0
    j_left[7] += a1        # A1
    j_left[8] += a2        # A2
    j_left[9] += a3        # A3
    j_left[10] += a4       # A4
    j_left[11] += a5       # A5
    j_left[12] += d5       # D5/SCK
    j_left[13] += d6       # D6/MOSI
    j_left[14] += d9       # D9/MISO
    j_left[15] += uart_rx  # RX
    j_left[16] += uart_tx  # TX

    # Right header (1x12)
    j_right = Part("Connector_Generic", "Conn_01x12",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    j_right[1] += sda      # SDA
    j_right[2] += scl      # SCL
    j_right[3] += d5       # D5
    j_right[4] += d6       # D6
    j_right[5] += d9       # D9
    j_right[6] += d10      # D10
    j_right[7] += d11      # D11
    j_right[8] += d12      # D12
    j_right[9] += d13      # D13
    j_right[10] += gnd     # GND
    j_right[11] += gnd     # AREF (used as GND)
    j_right[12] += vcc     # 3V3 OUT


# =============================================================================
# Build the circuit
# =============================================================================

# I2C bus nets
sda = Net("SDA")
scl = Net("SCL")

# NeoPixel data
neo_data = Net("NEOPIXEL")

# PDM microphone
pdm_clk = Net("PDM_CLK")
pdm_data_net = Net("PDM_DATA")

# User button
btn_usr = Net("BTN_USR")

# UART
uart_tx = Net("UART_TX")
uart_rx = Net("UART_RX")

# Analog pins
a0 = Net("A0")
a1 = Net("A1")
a2 = Net("A2")
a3 = Net("A3")
a4 = Net("A4")
a5 = Net("A5")

# Digital pins
d5 = Net("D5")
d6 = Net("D6")
d9 = Net("D9")
d10 = Net("D10")
d11 = Net("D11")
d12 = Net("D12")
d13 = Net("D13")

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

mcu_block(vcc, gnd, sda, scl, neo_data, pdm_clk, pdm_data_net,
          btn_usr, uart_tx, uart_rx, a0, a1, a2, a3, a4, a5,
          d5, d6, d9, d10, d11, d12, d13,
          qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3)

imu_9dof(vcc, gnd, sda, scl)

apds9960_block(vcc, gnd, sda, scl)

sht_block(vcc, gnd, sda, scl)

bmp280_block(vcc, gnd, sda, scl)

pdm_mic_block(vcc, gnd, pdm_clk, pdm_data_net)

qspi_flash(vcc, gnd, qspi_sck, qspi_cs, qspi_d0, qspi_d1, qspi_d2, qspi_d3)

neopixel_block(vcc, gnd, neo_data)

user_button(gnd, btn_usr)

builtin_led(gnd, d13)

ble_led(vcc, gnd)

i2c_pullups(vcc, sda, scl)

stemma_qt(vcc, gnd, sda, scl)

swd_header(vcc, gnd)

feather_headers(vcc, vbat, vbus, gnd,
                a0, a1, a2, a3, a4, a5,
                d5, d6, d9, d10, d11, d12, d13,
                sda, scl, uart_tx, uart_rx)


# =============================================================================
# Add draw_cmds and lib stubs for schematic generation
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
