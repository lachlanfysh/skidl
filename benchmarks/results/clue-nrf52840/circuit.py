"""
CLUE nRF52840 Express - Sensor-packed development board
BBC micro:bit form factor with edge connector and 5 big pads.

Nordic nRF52840: 1MB Flash, 256KB RAM, 64MHz Cortex M4
1.3" 240x240 Color IPS TFT display
Sensors: LSM6DS33 + LIS3MDL (9-DoF), APDS9960, PDM mic, SHT30, BMP280
RGB NeoPixel, 2MB external flash, buzzer, 2x white LEDs
STEMMA QT / Qwiic I2C connector
USB-C input, battery power 3-6V
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


def _init_skidl_pins(part):
    """Set default schematic attributes on SKIDL-defined part pins and
    synthesize draw_cmds so the schematic generator can compute bounding boxes.

    Library-loaded parts get orientation/x/y/length/rotation from the .kicad_sym
    file and draw_cmds from the symbol definition. Part(tool=SKIDL) pins lack
    these, causing NaN in the placement engine.
    """
    spacing_mm = 2.54
    pin_length_mm = 2.54
    n = len(part.pins)

    left_count = (n + 1) // 2
    right_count = n - left_count

    body_h = max(left_count, right_count, 1) * spacing_mm
    body_w = max(5.08, body_h * 0.6)

    draw_cmds = []
    for idx, pin in enumerate(part.pins):
        if idx < left_count:
            row = idx
            pin.x = -(body_w / 2 + pin_length_mm)
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "R"
            pin.rotation = 0
        else:
            row = idx - left_count
            pin.x = body_w / 2 + pin_length_mm
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "L"
            pin.rotation = 180

        pin.length = pin_length_mm

        pin_cmd = [
            "pin", pin.func if isinstance(pin.func, str) else "passive", "line",
            ["at", pin.x, pin.y, int(pin.rotation)],
            ["length", pin_length_mm],
            ["name", pin.name,
                ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num),
                ["effects", ["font", ["size", 1.27, 1.27]]]],
        ]
        draw_cmds.append(pin_cmd)

    rect_cmd = [
        "rectangle",
        ["start", -body_w / 2, -body_h / 2 - spacing_mm / 2],
        ["end", body_w / 2, body_h / 2 + spacing_mm / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]
    draw_cmds.append(rect_cmd)

    part.draw_cmds = {1: draw_cmds, 0: draw_cmds}

    if not hasattr(part, "lib") or part.lib is None:
        class _MockLib:
            def __init__(self, name):
                self.filename = name
        part.lib = _MockLib("skidl_custom")


# ============================================================
# Global nets
# ============================================================
vdd = Net("+3V3"); vdd.drive = POWER
vbus = Net("VBUS"); vbus.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
sda = Net("SDA")
scl = Net("SCL")

# ============================================================
# Subcircuit: nRF52840 MCU block
# ============================================================
@subcircuit
def nrf52840_mcu(vdd, gnd, vbus, sda, scl):
    """Nordic nRF52840 SoC with decoupling and crystal."""

    mcu = Part(name="nRF52840", tool=SKIDL, dest=NETLIST,
        footprint="Package_DFN_QFN:Nordic_AQFN-73-1EP_7x7mm_P0.5mm",
        pins=[
            Pin(num="1",  name="VDD1",     func=Pin.types.PWRIN),
            Pin(num="2",  name="DCC",      func=Pin.types.PWROUT),
            Pin(num="3",  name="DEC4",     func=Pin.types.PASSIVE),
            Pin(num="4",  name="VSS",      func=Pin.types.PWRIN),
            Pin(num="5",  name="P0.02",    func=Pin.types.BIDIR),
            Pin(num="6",  name="P0.03",    func=Pin.types.BIDIR),
            Pin(num="7",  name="P0.28",    func=Pin.types.BIDIR),   # SDA
            Pin(num="8",  name="P0.29",    func=Pin.types.BIDIR),
            Pin(num="9",  name="P0.30",    func=Pin.types.BIDIR),
            Pin(num="10", name="P0.31",    func=Pin.types.BIDIR),
            Pin(num="11", name="P0.00",    func=Pin.types.BIDIR),   # XL1
            Pin(num="12", name="P0.01",    func=Pin.types.BIDIR),   # XL2
            Pin(num="13", name="DEC1",     func=Pin.types.PASSIVE),
            Pin(num="14", name="P0.26",    func=Pin.types.BIDIR),   # SPI SCK
            Pin(num="15", name="P0.27",    func=Pin.types.BIDIR),   # SPI MOSI
            Pin(num="16", name="P0.04",    func=Pin.types.BIDIR),   # SPI CS TFT
            Pin(num="17", name="P0.05",    func=Pin.types.BIDIR),   # SPI MISO
            Pin(num="18", name="P0.06",    func=Pin.types.BIDIR),   # TFT DC
            Pin(num="19", name="P0.07",    func=Pin.types.BIDIR),   # TFT RST
            Pin(num="20", name="P0.08",    func=Pin.types.BIDIR),   # TFT Backlight
            Pin(num="21", name="P1.08",    func=Pin.types.BIDIR),   # Buzzer
            Pin(num="22", name="P1.09",    func=Pin.types.BIDIR),
            Pin(num="23", name="P0.11",    func=Pin.types.BIDIR),   # Flash CS
            Pin(num="24", name="P0.12",    func=Pin.types.BIDIR),   # NeoPixel
            Pin(num="25", name="P0.14",    func=Pin.types.BIDIR),   # SCL (I2C)
            Pin(num="26", name="P0.15",    func=Pin.types.BIDIR),
            Pin(num="27", name="P0.16",    func=Pin.types.BIDIR),   # SDA (I2C)
            Pin(num="28", name="P0.17",    func=Pin.types.BIDIR),
            Pin(num="29", name="P0.18",    func=Pin.types.BIDIR),   # RESET
            Pin(num="30", name="P0.19",    func=Pin.types.BIDIR),   # White LED 1
            Pin(num="31", name="P0.20",    func=Pin.types.BIDIR),
            Pin(num="32", name="P0.21",    func=Pin.types.BIDIR),
            Pin(num="33", name="P0.22",    func=Pin.types.BIDIR),
            Pin(num="34", name="P0.23",    func=Pin.types.BIDIR),
            Pin(num="35", name="P0.24",    func=Pin.types.BIDIR),   # White LED 2
            Pin(num="36", name="P0.25",    func=Pin.types.BIDIR),   # User Button A
            Pin(num="37", name="P1.00",    func=Pin.types.BIDIR),   # PDM CLK
            Pin(num="38", name="P1.01",    func=Pin.types.BIDIR),
            Pin(num="39", name="P1.02",    func=Pin.types.BIDIR),   # PDM DAT
            Pin(num="40", name="P1.03",    func=Pin.types.BIDIR),
            Pin(num="41", name="P1.04",    func=Pin.types.BIDIR),
            Pin(num="42", name="P1.05",    func=Pin.types.BIDIR),
            Pin(num="43", name="P1.06",    func=Pin.types.BIDIR),
            Pin(num="44", name="P1.07",    func=Pin.types.BIDIR),
            Pin(num="45", name="VDDH",     func=Pin.types.PWRIN),
            Pin(num="46", name="VSS_PA",   func=Pin.types.PWRIN),
            Pin(num="47", name="ANT",      func=Pin.types.PASSIVE),
            Pin(num="48", name="DEC3",     func=Pin.types.PASSIVE),
            Pin(num="49", name="DEC6",     func=Pin.types.PASSIVE),
            Pin(num="50", name="DEC5",     func=Pin.types.PASSIVE),
            Pin(num="51", name="P0.09",    func=Pin.types.BIDIR),   # NFC1
            Pin(num="52", name="P0.10",    func=Pin.types.BIDIR),   # NFC2
            Pin(num="53", name="P1.10",    func=Pin.types.BIDIR),
            Pin(num="54", name="P1.11",    func=Pin.types.BIDIR),   # User Button B
            Pin(num="55", name="P1.12",    func=Pin.types.BIDIR),
            Pin(num="56", name="P1.13",    func=Pin.types.BIDIR),
            Pin(num="57", name="P1.14",    func=Pin.types.BIDIR),
            Pin(num="58", name="P1.15",    func=Pin.types.BIDIR),
            Pin(num="59", name="VDD2",     func=Pin.types.PWRIN),
            Pin(num="60", name="DEC2",     func=Pin.types.PASSIVE),
            Pin(num="61", name="VBUS_MCU", func=Pin.types.PWRIN),
            Pin(num="62", name="DECUSB",   func=Pin.types.PASSIVE),
            Pin(num="63", name="D_MINUS",  func=Pin.types.BIDIR),
            Pin(num="64", name="D_PLUS",   func=Pin.types.BIDIR),
            Pin(num="65", name="P0.13",    func=Pin.types.BIDIR),   # SPI Flash CLK
            Pin(num="66", name="SWDCLK",   func=Pin.types.INPUT),
            Pin(num="67", name="SWDIO",    func=Pin.types.BIDIR),
            Pin(num="68", name="VDD3",     func=Pin.types.PWRIN),
            Pin(num="69", name="VDD4",     func=Pin.types.PWRIN),
            Pin(num="70", name="XC1",      func=Pin.types.INPUT),
            Pin(num="71", name="XC2",      func=Pin.types.INPUT),
            Pin(num="72", name="P0.09_2",  func=Pin.types.BIDIR),
            Pin(num="73", name="EP",       func=Pin.types.PASSIVE),
        ])

    # Power connections
    mcu["VDD1"] += vdd
    mcu["VDD2"] += vdd
    mcu["VDD3"] += vdd
    mcu["VDD4"] += vdd
    mcu["VDDH"] += vbus
    mcu["VBUS_MCU"] += vbus
    mcu["VSS"] += gnd
    mcu["VSS_PA"] += gnd
    mcu["EP"] += gnd

    # Decoupling capacitors on VDD
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vdd
        c[2] += gnd

    # Bulk cap
    c_bulk = Part("Device", "C", value="4.7uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bulk[1] += vdd
    c_bulk[2] += gnd

    # DEC pin caps (100nF to each DEC pin)
    for pin_name in ["DEC1", "DEC2", "DEC3", "DEC4", "DEC5", "DEC6"]:
        c_dec = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0402_1005Metric")
        c_dec[1] += mcu[pin_name]
        c_dec[2] += gnd

    # DECUSB cap
    c_usb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usb[1] += mcu["DECUSB"]
    c_usb[2] += gnd

    # DCC inductor for internal DC-DC
    l_dcc = Part("Device", "L", value="10uH",
                 footprint="Inductor_SMD:L_0603_1608Metric")
    l_dcc[1] += mcu["DCC"]
    l_dcc[2] += vdd

    # 32.768kHz crystal for RTC
    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["P0.00"]    # XL1
    xtal[2] += mcu["P0.01"]    # XL2

    # Crystal load caps
    c_xtal1 = Part("Device", "C", value="12pF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xtal2 = Part("Device", "C", value="12pF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xtal1[1] += mcu["P0.00"]
    c_xtal1[2] += gnd
    c_xtal2[1] += mcu["P0.01"]
    c_xtal2[2] += gnd

    # I2C connections
    mcu["P0.28"] += sda
    mcu["P0.14"] += scl

    # I2C pullups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vdd; r_sda[2] += sda
    r_scl[1] += vdd; r_scl[2] += scl

    # Reset pull-up
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vdd
    r_rst[2] += mcu["P0.18"]

    # Antenna matching network (simplified)
    ant_net = Net("ANT_FEED")
    l_ant = Part("Device", "L", value="3.3nH",
                 footprint="Inductor_SMD:L_0402_1005Metric")
    c_ant = Part("Device", "C", value="0.8pF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    l_ant[1] += mcu["ANT"]
    l_ant[2] += ant_net
    c_ant[1] += ant_net
    c_ant[2] += gnd

    return mcu


# ============================================================
# Subcircuit: USB-C input with ESD protection
# ============================================================
@subcircuit
def usb_input(vbus, gnd, dp, dm):
    """USB-C connector with ESD protection."""
    usb_conn = Part(name="USB_C_Receptacle", tool=SKIDL, dest=NETLIST,
        footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
        pins=[
            Pin(num="1",  name="GND1",  func=Pin.types.PWRIN),
            Pin(num="2",  name="VBUS1", func=Pin.types.PASSIVE),
            Pin(num="3",  name="CC1",   func=Pin.types.BIDIR),
            Pin(num="4",  name="D_P1",  func=Pin.types.BIDIR),
            Pin(num="5",  name="D_N1",  func=Pin.types.BIDIR),
            Pin(num="6",  name="SBU1",  func=Pin.types.PASSIVE),
            Pin(num="7",  name="VBUS2", func=Pin.types.PASSIVE),
            Pin(num="8",  name="CC2",   func=Pin.types.BIDIR),
            Pin(num="9",  name="D_P2",  func=Pin.types.BIDIR),
            Pin(num="10", name="D_N2",  func=Pin.types.BIDIR),
            Pin(num="11", name="SBU2",  func=Pin.types.PASSIVE),
            Pin(num="12", name="GND2",  func=Pin.types.PWRIN),
            Pin(num="13", name="SHIELD", func=Pin.types.PASSIVE),
        ])

    usb_conn["VBUS1"] += vbus
    usb_conn["VBUS2"] += vbus
    usb_conn["GND1"] += gnd
    usb_conn["GND2"] += gnd
    usb_conn["SHIELD"] += gnd
    usb_conn["D_P1"] += dp
    usb_conn["D_P2"] += dp
    usb_conn["D_N1"] += dm
    usb_conn["D_N2"] += dm

    # CC resistors for USB-C (5.1K to GND for device mode)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb_conn["CC1"]; r_cc1[2] += gnd
    r_cc2[1] += usb_conn["CC2"]; r_cc2[2] += gnd

    # SBU pins left floating (connect to ground through resistor)
    r_sbu1 = Part("Device", "R", value="1M",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_sbu2 = Part("Device", "R", value="1M",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_sbu1[1] += usb_conn["SBU1"]; r_sbu1[2] += gnd
    r_sbu2[1] += usb_conn["SBU2"]; r_sbu2[2] += gnd

    # USB ESD protection (TPD2E001 style, simplified as SKIDL part)
    esd = Part(name="TPD2E001", tool=SKIDL, dest=NETLIST,
        footprint="Package_TO_SOT_SMD:SOT-23-6",
        pins=[
            Pin(num="1", name="D_P",  func=Pin.types.PASSIVE),
            Pin(num="2", name="GND",  func=Pin.types.PWRIN),
            Pin(num="3", name="NC1",  func=Pin.types.NOCONNECT),
            Pin(num="4", name="NC2",  func=Pin.types.NOCONNECT),
            Pin(num="5", name="VCC",  func=Pin.types.PWRIN),
            Pin(num="6", name="D_N",  func=Pin.types.PASSIVE),
        ])
    esd["D_P"] += dp
    esd["D_N"] += dm
    esd["VCC"] += vbus
    esd["GND"] += gnd

    # Decoupling on VBUS
    c_vbus = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd


# ============================================================
# Subcircuit: Power supply (3.3V regulator from VBUS/battery)
# ============================================================
@subcircuit
def power_supply(vin, vout, gnd):
    """3.3V LDO regulator (AP2112K style)."""
    reg = Part(name="AP2112K-3.3", tool=SKIDL, dest=NETLIST,
        footprint="Package_TO_SOT_SMD:SOT-23-5",
        pins=[
            Pin(num="1", name="VIN",  func=Pin.types.PWRIN),
            Pin(num="2", name="GND",  func=Pin.types.PWRIN),
            Pin(num="3", name="EN",   func=Pin.types.INPUT),
            Pin(num="4", name="NC",   func=Pin.types.NOCONNECT),
            Pin(num="5", name="VOUT", func=Pin.types.PWROUT),
        ])
    reg["VIN"] += vin
    reg["GND"] += gnd
    reg["EN"] += vin   # Always enabled
    reg["VOUT"] += vout

    # Input cap
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output cap
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vout
    c_out[2] += gnd

    # Decoupling on output
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dec[1] += vout
    c_dec[2] += gnd


# ============================================================
# Subcircuit: TFT Display (1.3" 240x240 ST7789 via SPI)
# ============================================================
@subcircuit
def tft_display(vdd, gnd, spi_clk, spi_mosi, spi_cs, dc, rst, bl):
    """1.3 inch 240x240 IPS TFT with ST7789 controller, FFC connector."""
    ffc = Part(name="TFT_FFC_24P", tool=SKIDL, dest=NETLIST,
        footprint="Connector_FFC-FPC:Hirose_FH12-24S-0.5SH_1x24-1MP_P0.50mm_Horizontal",
        pins=[
            Pin(num="1",  name="GND1",    func=Pin.types.PWRIN),
            Pin(num="2",  name="LEDK1",   func=Pin.types.PASSIVE),
            Pin(num="3",  name="LEDK2",   func=Pin.types.PASSIVE),
            Pin(num="4",  name="LEDK3",   func=Pin.types.PASSIVE),
            Pin(num="5",  name="LEDA",    func=Pin.types.PASSIVE),
            Pin(num="6",  name="VDD_TFT", func=Pin.types.PWRIN),
            Pin(num="7",  name="GND2",    func=Pin.types.PWRIN),
            Pin(num="8",  name="DB0",     func=Pin.types.BIDIR),
            Pin(num="9",  name="DB1",     func=Pin.types.BIDIR),
            Pin(num="10", name="DB2",     func=Pin.types.BIDIR),
            Pin(num="11", name="DB3",     func=Pin.types.BIDIR),
            Pin(num="12", name="DB4",     func=Pin.types.BIDIR),
            Pin(num="13", name="DB5",     func=Pin.types.BIDIR),
            Pin(num="14", name="DB6",     func=Pin.types.BIDIR),
            Pin(num="15", name="DB7",     func=Pin.types.BIDIR),
            Pin(num="16", name="CS",      func=Pin.types.INPUT),
            Pin(num="17", name="DC",      func=Pin.types.INPUT),
            Pin(num="18", name="WR",      func=Pin.types.INPUT),
            Pin(num="19", name="RD",      func=Pin.types.INPUT),
            Pin(num="20", name="RST_TFT", func=Pin.types.INPUT),
            Pin(num="21", name="SDA_TFT", func=Pin.types.BIDIR),
            Pin(num="22", name="SCL_TFT", func=Pin.types.INPUT),
            Pin(num="23", name="VDD2",    func=Pin.types.PWRIN),
            Pin(num="24", name="GND3",    func=Pin.types.PWRIN),
            Pin(num="25", name="SHIELD",  func=Pin.types.PASSIVE),
        ])

    ffc["GND1"] += gnd
    ffc["GND2"] += gnd
    ffc["GND3"] += gnd
    ffc["SHIELD"] += gnd
    ffc["VDD_TFT"] += vdd
    ffc["VDD2"] += vdd
    ffc["SCL_TFT"] += spi_clk
    ffc["SDA_TFT"] += spi_mosi
    ffc["CS"] += spi_cs
    ffc["DC"] += dc
    ffc["RST_TFT"] += rst

    # Backlight control via MOSFET
    q_bl = Part("Device", "Q_NMOS", value="BSS138",
                footprint="Package_TO_SOT_SMD:SOT-23")
    r_bl = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    q_bl["G"] += bl      # gate
    q_bl["S"] += gnd     # source

    # LED cathodes through MOSFET
    bl_net = Net("TFT_BL_SINK")
    q_bl["D"] += bl_net  # drain
    ffc["LEDK1"] += bl_net
    ffc["LEDK2"] += bl_net
    ffc["LEDK3"] += bl_net
    ffc["LEDA"] += vdd  # LED anode

    r_bl[1] += bl
    r_bl[2] += gnd

    # Unused parallel data pins tied to GND
    for pin_name in ["DB0", "DB1", "DB2", "DB3", "DB4", "DB5", "DB6", "DB7", "WR", "RD"]:
        r_pull = Part("Device", "R", value="10K",
                      footprint="Resistor_SMD:R_0402_1005Metric")
        r_pull[1] += ffc[pin_name]
        r_pull[2] += gnd

    # Display decoupling
    c_tft = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_tft[1] += vdd
    c_tft[2] += gnd


# ============================================================
# Subcircuit: LSM6DS33 Accel/Gyro + LIS3MDL Magnetometer (9-DoF)
# ============================================================
@subcircuit
def imu_9dof(vdd, gnd, sda, scl):
    """LSM6DS33 accel/gyro + LIS3MDL magnetometer, both I2C."""

    # LSM6DS33 - 6-DoF IMU (LGA-14)
    lsm = Part(name="LSM6DS33", tool=SKIDL, dest=NETLIST,
        footprint="Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y",
        pins=[
            Pin(num="1",  name="SDO_SA0",  func=Pin.types.BIDIR),
            Pin(num="2",  name="SDX",      func=Pin.types.BIDIR),
            Pin(num="3",  name="SCX",      func=Pin.types.INPUT),
            Pin(num="4",  name="INT1",     func=Pin.types.OUTPUT),
            Pin(num="5",  name="VDDIO",    func=Pin.types.PWRIN),
            Pin(num="6",  name="GND1",     func=Pin.types.PWRIN),
            Pin(num="7",  name="GND2",     func=Pin.types.PWRIN),
            Pin(num="8",  name="VDD",      func=Pin.types.PWRIN),
            Pin(num="9",  name="INT2",     func=Pin.types.OUTPUT),
            Pin(num="10", name="OCS_AUX",  func=Pin.types.INPUT),
            Pin(num="11", name="SDO_AUX",  func=Pin.types.OUTPUT),
            Pin(num="12", name="CS",       func=Pin.types.INPUT),
            Pin(num="13", name="SCL_SPC",  func=Pin.types.INPUT),
            Pin(num="14", name="SDA_SDI",  func=Pin.types.BIDIR),
        ])
    lsm["VDD"] += vdd
    lsm["VDDIO"] += vdd
    lsm["GND1"] += gnd
    lsm["GND2"] += gnd
    lsm["SDA_SDI"] += sda
    lsm["SCL_SPC"] += scl
    lsm["CS"] += vdd        # I2C mode (CS high)
    lsm["SDO_SA0"] += gnd   # I2C address select

    # INT pins - connect with pull-up for use by MCU
    lsm_int1 = Net("LSM_INT1")
    lsm["INT1"] += lsm_int1

    # Unused pins
    lsm_ocsaux = Net("LSM_OCS")
    lsm_sdoaux = Net("LSM_SDOAUX")
    lsm["OCS_AUX"] += lsm_ocsaux
    lsm["SDO_AUX"] += lsm_sdoaux
    lsm["SDX"] += sda
    lsm["SCX"] += scl
    lsm_int2 = Net("LSM_INT2")
    lsm["INT2"] += lsm_int2

    # Decoupling
    c_lsm1 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lsm1[1] += vdd; c_lsm1[2] += gnd
    c_lsm2 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lsm2[1] += vdd; c_lsm2[2] += gnd

    # LIS3MDL - 3-axis magnetometer (LGA-12)
    lis = Part(name="LIS3MDL", tool=SKIDL, dest=NETLIST,
        footprint="Package_LGA:LGA-12_2x2mm_P0.5mm",
        pins=[
            Pin(num="1",  name="SCL_SPC",  func=Pin.types.INPUT),
            Pin(num="2",  name="GND1",     func=Pin.types.PWRIN),
            Pin(num="3",  name="C1",       func=Pin.types.PASSIVE),
            Pin(num="4",  name="VDD",      func=Pin.types.PWRIN),
            Pin(num="5",  name="VDD_IO",   func=Pin.types.PWRIN),
            Pin(num="6",  name="INT_MAG",  func=Pin.types.OUTPUT),
            Pin(num="7",  name="DRDY",     func=Pin.types.OUTPUT),
            Pin(num="8",  name="GND2",     func=Pin.types.PWRIN),
            Pin(num="9",  name="SDA_SDI",  func=Pin.types.BIDIR),
            Pin(num="10", name="SDO_SA1",  func=Pin.types.BIDIR),
            Pin(num="11", name="CS",       func=Pin.types.INPUT),
            Pin(num="12", name="GND3",     func=Pin.types.PWRIN),
        ])
    lis["VDD"] += vdd
    lis["VDD_IO"] += vdd
    lis["GND1"] += gnd
    lis["GND2"] += gnd
    lis["GND3"] += gnd
    lis["SDA_SDI"] += sda
    lis["SCL_SPC"] += scl
    lis["CS"] += vdd          # I2C mode
    lis["SDO_SA1"] += gnd     # Address select

    # C1 filter cap
    c_lis_c1 = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lis_c1[1] += lis["C1"]
    c_lis_c1[2] += gnd

    # INT/DRDY
    lis_int = Net("MAG_INT")
    lis_drdy = Net("MAG_DRDY")
    lis["INT_MAG"] += lis_int
    lis["DRDY"] += lis_drdy

    # Decoupling
    c_lis1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lis1[1] += vdd; c_lis1[2] += gnd


# ============================================================
# Subcircuit: APDS9960 proximity/light/color/gesture sensor
# ============================================================
@subcircuit
def apds9960_block(vdd, gnd, sda, scl):
    """APDS-9960 proximity, light, color, gesture sensor."""
    apds = Part(name="APDS-9960", tool=SKIDL, dest=NETLIST,
        footprint="Package_LGA:AMS_OLGA-8_2x3.1mm_P0.8mm",
        pins=[
            Pin(num="1", name="SDA",   func=Pin.types.BIDIR),
            Pin(num="2", name="VDD",   func=Pin.types.PWRIN),
            Pin(num="3", name="LDR",   func=Pin.types.PASSIVE),
            Pin(num="4", name="INT",   func=Pin.types.OUTPUT),
            Pin(num="5", name="LEDK",  func=Pin.types.PASSIVE),
            Pin(num="6", name="LEDA",  func=Pin.types.PASSIVE),
            Pin(num="7", name="GND",   func=Pin.types.PWRIN),
            Pin(num="8", name="SCL",   func=Pin.types.INPUT),
        ])
    apds["VDD"] += vdd
    apds["GND"] += gnd
    apds["SDA"] += sda
    apds["SCL"] += scl

    # IR LED current limit resistor
    r_led = Part("Device", "R", value="68",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_led[1] += vdd
    r_led[2] += apds["LEDA"]

    # LEDK to ground
    apds["LEDK"] += gnd

    # LDR bias
    r_ldr = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_ldr[1] += apds["LDR"]
    r_ldr[2] += gnd

    # INT pin
    apds_int = Net("APDS_INT")
    apds["INT"] += apds_int

    # Decoupling
    c_apds = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_apds[1] += vdd; c_apds[2] += gnd


# ============================================================
# Subcircuit: PDM Microphone
# ============================================================
@subcircuit
def pdm_microphone(vdd, gnd, pdm_clk, pdm_dat):
    """PDM MEMS microphone (MP34DT01-M style)."""
    mic = Part(name="MP34DT01", tool=SKIDL, dest=NETLIST,
        footprint="Package_LGA:LGA-12_2x2mm_P0.5mm",
        pins=[
            Pin(num="1",  name="VDD",    func=Pin.types.PWRIN),
            Pin(num="2",  name="GND1",   func=Pin.types.PWRIN),
            Pin(num="3",  name="LR",     func=Pin.types.INPUT),
            Pin(num="4",  name="CLK",    func=Pin.types.INPUT),
            Pin(num="5",  name="DOUT",   func=Pin.types.OUTPUT),
            Pin(num="6",  name="GND2",   func=Pin.types.PWRIN),
            Pin(num="7",  name="GND3",   func=Pin.types.PWRIN),
            Pin(num="8",  name="GND4",   func=Pin.types.PWRIN),
            Pin(num="9",  name="GND5",   func=Pin.types.PWRIN),
            Pin(num="10", name="GND6",   func=Pin.types.PWRIN),
            Pin(num="11", name="GND7",   func=Pin.types.PWRIN),
            Pin(num="12", name="GND8",   func=Pin.types.PWRIN),
        ])
    mic["VDD"] += vdd
    for p in ["GND1", "GND2", "GND3", "GND4", "GND5", "GND6", "GND7", "GND8"]:
        mic[p] += gnd
    mic["CLK"] += pdm_clk
    mic["DOUT"] += pdm_dat
    mic["LR"] += gnd  # Left channel select

    # Decoupling
    c_mic = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_mic[1] += vdd; c_mic[2] += gnd


# ============================================================
# Subcircuit: SHT30 Humidity/Temperature Sensor
# ============================================================
@subcircuit
def sht_sensor(vdd, gnd, sda, scl):
    """SHT30 humidity and temperature sensor, I2C."""
    sht = Part(name="SHT30", tool=SKIDL, dest=NETLIST,
        footprint="Sensor_Humidity:Sensirion_DFN-8-1EP_2.5x2.5mm_P0.5mm_EP1.1x1.7mm",
        pins=[
            Pin(num="1", name="SDA",   func=Pin.types.BIDIR),
            Pin(num="2", name="ADDR",  func=Pin.types.INPUT),
            Pin(num="3", name="ALERT", func=Pin.types.OUTPUT),
            Pin(num="4", name="SCL",   func=Pin.types.INPUT),
            Pin(num="5", name="VDD",   func=Pin.types.PWRIN),
            Pin(num="6", name="nRST",  func=Pin.types.INPUT),
            Pin(num="7", name="R",     func=Pin.types.PASSIVE),
            Pin(num="8", name="VSS",   func=Pin.types.PWRIN),
            Pin(num="9", name="EP",    func=Pin.types.PASSIVE),
        ])
    sht["VDD"] += vdd
    sht["VSS"] += gnd
    sht["EP"] += gnd
    sht["SDA"] += sda
    sht["SCL"] += scl
    sht["ADDR"] += gnd     # Default address
    sht["nRST"] += vdd     # Not reset

    # R pin to VSS
    sht["R"] += gnd

    # Alert
    sht_alert = Net("SHT_ALERT")
    sht["ALERT"] += sht_alert

    # Decoupling
    c_sht = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_sht[1] += vdd; c_sht[2] += gnd


# ============================================================
# Subcircuit: BMP280 Barometric Pressure/Temp/Altitude
# ============================================================
@subcircuit
def bmp280_block(vdd, gnd, sda, scl):
    """BMP280 barometric pressure sensor, I2C."""
    bmp = Part(name="BMP280", tool=SKIDL, dest=NETLIST,
        footprint="Package_LGA:Bosch_LGA-8_2x2.5mm_P0.65mm_ClockwisePinNumbering",
        pins=[
            Pin(num="1", name="GND1",  func=Pin.types.PWRIN),
            Pin(num="2", name="CSB",   func=Pin.types.INPUT),
            Pin(num="3", name="SDI",   func=Pin.types.BIDIR),
            Pin(num="4", name="SCK",   func=Pin.types.INPUT),
            Pin(num="5", name="SDO",   func=Pin.types.BIDIR),
            Pin(num="6", name="VDDIO", func=Pin.types.PWRIN),
            Pin(num="7", name="GND2",  func=Pin.types.PWRIN),
            Pin(num="8", name="VDD",   func=Pin.types.PWRIN),
        ])
    bmp["VDD"] += vdd
    bmp["VDDIO"] += vdd
    bmp["GND1"] += gnd
    bmp["GND2"] += gnd
    bmp["SDI"] += sda
    bmp["SCK"] += scl
    bmp["CSB"] += vdd      # I2C mode
    bmp["SDO"] += gnd      # I2C address select (0x76)

    # Decoupling
    c_bmp = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_bmp[1] += vdd; c_bmp[2] += gnd


# ============================================================
# Subcircuit: 2MB SPI Flash (GD25Q16C)
# ============================================================
@subcircuit
def spi_flash(vdd, gnd, spi_clk, spi_mosi, spi_miso, flash_cs):
    """2MB SPI flash memory (GD25Q16C or W25Q16)."""
    flash = Part(name="GD25Q16C", tool=SKIDL, dest=NETLIST,
        footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        pins=[
            Pin(num="1", name="CS",   func=Pin.types.INPUT),
            Pin(num="2", name="DO",   func=Pin.types.OUTPUT),
            Pin(num="3", name="WP",   func=Pin.types.INPUT),
            Pin(num="4", name="GND",  func=Pin.types.PWRIN),
            Pin(num="5", name="DI",   func=Pin.types.INPUT),
            Pin(num="6", name="CLK",  func=Pin.types.INPUT),
            Pin(num="7", name="HOLD", func=Pin.types.INPUT),
            Pin(num="8", name="VCC",  func=Pin.types.PWRIN),
        ])
    flash["VCC"] += vdd
    flash["GND"] += gnd
    flash["CS"] += flash_cs
    flash["CLK"] += spi_clk
    flash["DI"] += spi_mosi
    flash["DO"] += spi_miso
    flash["WP"] += vdd      # Write protect disabled
    flash["HOLD"] += vdd    # Hold disabled

    # Decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += vdd; c_flash[2] += gnd


# ============================================================
# Subcircuit: NeoPixel RGB LED
# ============================================================
@subcircuit
def neopixel_block(vdd, gnd, data_in):
    """WS2812B NeoPixel RGB LED."""
    neo = Part(name="WS2812B", tool=SKIDL, dest=NETLIST,
        footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
        pins=[
            Pin(num="1", name="VDD",  func=Pin.types.PWRIN),
            Pin(num="2", name="DOUT", func=Pin.types.OUTPUT),
            Pin(num="3", name="GND",  func=Pin.types.PWRIN),
            Pin(num="4", name="DIN",  func=Pin.types.INPUT),
        ])
    neo["VDD"] += vdd
    neo["GND"] += gnd
    neo["DIN"] += data_in

    neo_dout = Net("NEO_DOUT")
    neo["DOUT"] += neo_dout

    # Decoupling
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += vdd; c_neo[2] += gnd

    # Data line series resistor
    r_data = Part("Device", "R", value="470",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_data[1] += data_in
    r_data[2] += data_in  # Series in-line (simplified, both ends same net for schematic)


# ============================================================
# Subcircuit: Buzzer/Speaker with driver
# ============================================================
@subcircuit
def buzzer_block(vdd, gnd, buzzer_pin):
    """Magnetic buzzer with MOSFET driver."""
    buzzer = Part(name="Buzzer", tool=SKIDL, dest=NETLIST,
        footprint="Buzzer_Beeper:Buzzer_Murata_PKMCS0909E",
        pins=[
            Pin(num="1", name="PLUS",  func=Pin.types.PASSIVE),
            Pin(num="2", name="MINUS", func=Pin.types.PASSIVE),
        ])

    # MOSFET driver
    q_buz = Part("Device", "Q_NMOS", value="BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")

    r_gate = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0402_1005Metric")

    buzzer["PLUS"] += vdd
    buzzer["MINUS"] += q_buz["D"]   # drain
    q_buz["S"] += gnd               # source
    r_gate[1] += buzzer_pin
    r_gate[2] += q_buz["G"]         # gate


# ============================================================
# Subcircuit: White LEDs (2x for illumination/color sensing)
# ============================================================
@subcircuit
def white_leds(vdd, gnd, led1_pin, led2_pin):
    """Two white LEDs for illumination and color sensing."""
    for pin in [led1_pin, led2_pin]:
        led = Part("Device", "LED", value="White",
                   footprint="LED_SMD:LED_0603_1608Metric")
        r_led = Part("Device", "R", value="100",
                     footprint="Resistor_SMD:R_0402_1005Metric")
        r_led[1] += pin
        r_led[2] += led[2]  # pin 2 = A (anode)
        led[1] += gnd       # pin 1 = K (cathode)


# ============================================================
# Subcircuit: Buttons (A, B, Reset)
# ============================================================
@subcircuit
def buttons(gnd, btn_a_pin, btn_b_pin, rst_pin):
    """User buttons A and B, plus reset button."""
    for pin in [btn_a_pin, btn_b_pin, rst_pin]:
        sw = Part("Switch", "SW_Push", value="Button",
                  footprint="Button_Switch_SMD:SW_SPST_PTS810")
        sw[1] += pin
        sw[2] += gnd


# ============================================================
# Subcircuit: Edge connector (BBC micro:bit compatible)
# ============================================================
@subcircuit
def edge_connector(vdd, gnd, mcu_ref):
    """BBC micro:bit compatible edge connector (2x40 1.27mm pitch)."""
    edge = Part(name="Edge_Connector", tool=SKIDL, dest=NETLIST,
        footprint="Connector_PinHeader_1.27mm:PinHeader_2x40_P1.27mm_Vertical",
        pins=[Pin(num=str(i), name=f"P{i}", func=Pin.types.PASSIVE)
              for i in range(1, 81)])

    # Power pins
    edge["P1"] += vdd
    edge["P2"] += vdd
    edge["P3"] += gnd
    edge["P4"] += gnd

    # Route some GPIO through to edge
    edge["P5"] += mcu_ref["P0.02"]
    edge["P6"] += mcu_ref["P0.03"]
    edge["P7"] += mcu_ref["P0.04"]
    edge["P8"] += mcu_ref["P0.05"]

    # Remaining pins connect to various MCU GPIOs
    # (simplified - in reality each pin maps to specific nRF52840 GPIO)
    for i in range(9, 81):
        edge_net = Net(f"EDGE_{i}")
        edge[f"P{i}"] += edge_net


# ============================================================
# Subcircuit: Big pads (5 alligator-clip pads)
# ============================================================
@subcircuit
def big_pads(vdd, gnd, mcu_ref):
    """5 big pads for alligator clips (P0, P1, P2, 3V, GND)."""
    pads = Part(name="BigPads", tool=SKIDL, dest=NETLIST,
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
        pins=[
            Pin(num="1", name="PAD0",  func=Pin.types.PASSIVE),
            Pin(num="2", name="PAD1",  func=Pin.types.PASSIVE),
            Pin(num="3", name="PAD2",  func=Pin.types.PASSIVE),
            Pin(num="4", name="3V3",   func=Pin.types.PASSIVE),
            Pin(num="5", name="GNDP",  func=Pin.types.PASSIVE),
        ])
    pads["PAD0"] += mcu_ref["P0.02"]
    pads["PAD1"] += mcu_ref["P0.03"]
    pads["PAD2"] += mcu_ref["P0.04"]
    pads["3V3"] += vdd
    pads["GNDP"] += gnd


# ============================================================
# Subcircuit: STEMMA QT / Qwiic I2C connector
# ============================================================
@subcircuit
def stemma_qt(vdd, gnd, sda, scl):
    """STEMMA QT / Qwiic JST SH 4-pin I2C connector."""
    jst = Part(name="STEMMA_QT", tool=SKIDL, dest=NETLIST,
        footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
        pins=[
            Pin(num="1", name="GND",  func=Pin.types.PWRIN),
            Pin(num="2", name="VCC",  func=Pin.types.PASSIVE),
            Pin(num="3", name="SDA",  func=Pin.types.BIDIR),
            Pin(num="4", name="SCL",  func=Pin.types.INPUT),
            Pin(num="5", name="MP",   func=Pin.types.PASSIVE),
        ])
    jst["GND"] += gnd
    jst["VCC"] += vdd
    jst["SDA"] += sda
    jst["SCL"] += scl
    jst["MP"] += gnd


# ============================================================
# Subcircuit: Battery connector
# ============================================================
@subcircuit
def battery_input(vbus, gnd):
    """JST PH 2-pin battery connector with reverse protection."""
    bat_conn = Part(name="Battery_JST", tool=SKIDL, dest=NETLIST,
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        pins=[
            Pin(num="1", name="VBAT", func=Pin.types.PASSIVE),
            Pin(num="2", name="GND",  func=Pin.types.PASSIVE),
        ])

    # Schottky diode for reverse polarity protection
    d_bat = Part("Device", "D_Schottky", value="MBR0520",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    d_bat[2] += bat_conn["VBAT"]   # pin 2 = A (anode)
    d_bat[1] += vbus               # pin 1 = K (cathode, to VBUS rail)
    bat_conn["GND"] += gnd


# ============================================================
# Subcircuit: SWD Debug header
# ============================================================
@subcircuit
def debug_header(vdd, gnd, mcu_ref):
    """SWD debug header (simplified 2-pin)."""
    swd_clk = Net("SWDCLK")
    swd_io = Net("SWDIO")
    mcu_ref["SWDCLK"] += swd_clk
    mcu_ref["SWDIO"] += swd_io

    hdr = Part(name="SWD_Header", tool=SKIDL, dest=NETLIST,
        footprint="Connector_PinHeader_1.27mm:PinHeader_1x05_P1.27mm_Vertical",
        pins=[
            Pin(num="1", name="VCC",    func=Pin.types.PASSIVE),
            Pin(num="2", name="SWDIO",  func=Pin.types.BIDIR),
            Pin(num="3", name="SWDCLK", func=Pin.types.INPUT),
            Pin(num="4", name="RST",    func=Pin.types.INPUT),
            Pin(num="5", name="GND",    func=Pin.types.PASSIVE),
        ])
    hdr["VCC"] += vdd
    hdr["GND"] += gnd
    hdr["SWDIO"] += swd_io
    hdr["SWDCLK"] += swd_clk
    hdr["RST"] += mcu_ref["P0.18"]


# ============================================================
# Build the circuit
# ============================================================

# USB data lines
dp = Net("USB_DP")
dm = Net("USB_DM")

# SPI bus (shared between TFT and flash)
spi_clk = Net("SPI_CLK")
spi_mosi = Net("SPI_MOSI")
spi_miso = Net("SPI_MISO")
tft_cs = Net("TFT_CS")
tft_dc = Net("TFT_DC")
tft_rst = Net("TFT_RST")
tft_bl = Net("TFT_BL")
flash_cs = Net("FLASH_CS")

# PDM microphone signals
pdm_clk = Net("PDM_CLK")
pdm_dat = Net("PDM_DAT")

# NeoPixel data
neo_data = Net("NEOPIXEL_DATA")

# Buzzer
buzzer_sig = Net("BUZZER_SIG")

# White LED signals
led1_sig = Net("WHITE_LED1")
led2_sig = Net("WHITE_LED2")

# Button signals
btn_a = Net("BTN_A")
btn_b = Net("BTN_B")
btn_rst = Net("BTN_RST")

# Instantiate MCU
mcu = nrf52840_mcu(vdd, gnd, vbus, sda, scl)

# Connect MCU to SPI bus
mcu["P0.26"] += spi_clk
mcu["P0.27"] += spi_mosi
mcu["P0.05"] += spi_miso
mcu["P0.06"] += tft_cs
mcu["P0.07"] += tft_dc
mcu["P0.08"] += tft_rst
mcu["P0.16"] += tft_bl
mcu["P0.11"] += flash_cs

# Connect MCU to USB
mcu["D_PLUS"] += dp
mcu["D_MINUS"] += dm

# Connect MCU to peripherals
mcu["P1.00"] += pdm_clk
mcu["P1.02"] += pdm_dat
mcu["P0.12"] += neo_data
mcu["P1.08"] += buzzer_sig
mcu["P0.19"] += led1_sig
mcu["P0.24"] += led2_sig
mcu["P0.25"] += btn_a
mcu["P1.11"] += btn_b
mcu["P0.18"] += btn_rst

# Connect MCU to SPI flash CLK
mcu["P0.13"] += spi_clk

# Instantiate all subcircuits
usb_input(vbus, gnd, dp, dm)
power_supply(vbus, vdd, gnd)
tft_display(vdd, gnd, spi_clk, spi_mosi, tft_cs, tft_dc, tft_rst, tft_bl)
imu_9dof(vdd, gnd, sda, scl)
apds9960_block(vdd, gnd, sda, scl)
pdm_microphone(vdd, gnd, pdm_clk, pdm_dat)
sht_sensor(vdd, gnd, sda, scl)
bmp280_block(vdd, gnd, sda, scl)
spi_flash(vdd, gnd, spi_clk, spi_mosi, spi_miso, flash_cs)
neopixel_block(vdd, gnd, neo_data)
buzzer_block(vdd, gnd, buzzer_sig)
white_leds(vdd, gnd, led1_sig, led2_sig)
buttons(gnd, btn_a, btn_b, btn_rst)
edge_connector(vdd, gnd, mcu)
big_pads(vdd, gnd, mcu)
stemma_qt(vdd, gnd, sda, scl)
battery_input(vbus, gnd)
debug_header(vdd, gnd, mcu)

# Initialize SKIDL-tool parts for schematic generation
for p in default_circuit.parts:
    if getattr(p, "tool", None) == SKIDL:
        _init_skidl_pins(p)

# Generate schematic
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
