"""
Feather nRF52840 Sense (Bluefruit) -- SKiDL circuit description.

Nordic nRF52840 BLE SoC with built-in 9-DoF (LSM6DS33 + LIS3MDL),
APDS9960 gesture/color/proximity, PDM microphone, SHT30 humidity,
BMP280 barometric pressure, NeoPixel, USB-C, LiPo charger,
3.3V regulator, and Feather headers.
"""
import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


def _fix_skidl_pins(part):
    """Set default orientation/position on SKIDL-tool part pins for schematic gen.

    Also synthesize minimal draw_cmds so calc_symbol_bbox produces a real
    bounding box instead of an empty/inf one.
    """
    n = len(part.pins)
    spacing = 2.54  # mm between pins
    pin_len = 2.54  # mm pin stub length

    # Assign pin positions: stack vertically, pin stub pointing right
    for i, pin in enumerate(part.pins):
        if not hasattr(pin, "orientation") or not pin.orientation:
            pin.orientation = "R"
        pin.x = float(pin_len)           # tip of pin (connection point)
        pin.y = float(-i * spacing)      # stack downward

    # Build draw_cmds with pin entries and a body rectangle so
    # calc_symbol_bbox can compute a proper bounding box.
    # Format: nested lists mimicking KiCad s-expression structure.
    body_w = max(5.0, pin_len + 2.0)
    body_h = max(5.0, (n - 1) * spacing + 2.0)
    body_top = 1.0
    body_bot = body_top - body_h
    body_left = pin_len + 0.5
    body_right = body_left + body_w

    cmds = []
    # Rectangle for the body (nested list format for _draw_cmd_to_dict)
    cmds.append(["rectangle",
                 ["start", body_left, body_top],
                 ["end", body_right, body_bot]])
    # Pin entries
    for i, pin in enumerate(part.pins):
        cmds.append([
            "pin", "passive", "line",
            ["at", 0.0, float(-i * spacing), 0],
            ["length", pin_len],
            ["name", pin.name or f"P{pin.num}"],
            ["number", str(pin.num)],
        ])

    part.draw_cmds = {1: cmds, 0: []}

    # SKIDL-tool parts also need a lib attribute with filename for schematic output
    if not hasattr(part, "lib") or part.lib is None:
        class _FakeLib:
            filename = "SKIDL"
        part.lib = _FakeLib()

    return part


# ---------------------------------------------------------------
# Global nets
# ---------------------------------------------------------------
vbus = Net("VBUS"); vbus.drive = POWER
vbat = Net("VBAT"); vbat.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd  = Net("GND");  gnd.drive  = POWER

i2c_sda = Net("SDA")
i2c_scl = Net("SCL")

# ---------------------------------------------------------------
# USB Input
# ---------------------------------------------------------------
@subcircuit
def usb_input(vbus, gnd, usb_dp, usb_dm):
    """USB-C connector with ESD protection."""
    usb_conn = Part(name="USB_C_Receptacle", tool=SKIDL, dest=NETLIST,
                    footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
                    pins=[
                        Pin(num="1",  name="VBUS",   func=Pin.types.PWROUT),
                        Pin(num="2",  name="D-",     func=Pin.types.BIDIR),
                        Pin(num="3",  name="D+",     func=Pin.types.BIDIR),
                        Pin(num="4",  name="CC1",    func=Pin.types.BIDIR),
                        Pin(num="5",  name="CC2",    func=Pin.types.BIDIR),
                        Pin(num="6",  name="GND",    func=Pin.types.PWRIN),
                        Pin(num="7",  name="SHIELD", func=Pin.types.PASSIVE),
                    ])
    usb_conn["VBUS"]  += vbus
    usb_conn["GND"]   += gnd
    usb_conn["D+"]    += usb_dp
    usb_conn["D-"]    += usb_dm
    usb_conn["CC1"]   += Net("CC1")
    usb_conn["CC2"]   += Net("CC2")
    usb_conn["SHIELD"] += gnd

    # CC pull-down resistors (5.1k for device mode)
    for net_name in ["CC1", "CC2"]:
        r = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
        r[1] += usb_conn[net_name]
        r[2] += gnd

usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")
usb_input(vbus, gnd, usb_dp, usb_dm)

# ---------------------------------------------------------------
# Power Supply -- LiPo charger + 3.3V LDO
# ---------------------------------------------------------------
@subcircuit
def power_supply(vbus, vbat, v3v3, gnd):
    """MCP73831 LiPo charger + AP2112 3.3V LDO."""

    # MCP73831 LiPo charger (SOT-23-5)
    chg = Part(name="MCP73831", tool=SKIDL, dest=NETLIST,
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               pins=[
                   Pin(num="1", name="STAT",  func=Pin.types.OUTPUT),
                   Pin(num="2", name="VSS",   func=Pin.types.PWRIN),
                   Pin(num="3", name="VBAT",  func=Pin.types.PWROUT),
                   Pin(num="4", name="VDD",   func=Pin.types.PWRIN),
                   Pin(num="5", name="PROG",  func=Pin.types.INPUT),
               ])
    chg["VDD"]  += vbus
    chg["VSS"]  += gnd
    chg["VBAT"] += vbat

    # Charge-rate resistor (2K = 500mA)
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_prog[1] += chg["PROG"]
    r_prog[2] += gnd

    # Charge LED
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    chg["STAT"] += r_led[1]
    r_led[2] += led_chg[1]
    led_chg[2] += gnd

    # Input decoupling for charger
    c_chg_in = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_chg_in[1] += vbus
    c_chg_in[2] += gnd

    # Battery decoupling
    c_bat = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bat[1] += vbat
    c_bat[2] += gnd

    # AP2112K-3.3 LDO (SOT-23-5)
    ldo = Part(name="AP2112K-3.3", tool=SKIDL, dest=NETLIST,
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               pins=[
                   Pin(num="1", name="VIN",  func=Pin.types.PWRIN),
                   Pin(num="2", name="GND",  func=Pin.types.PWRIN),
                   Pin(num="3", name="EN",   func=Pin.types.INPUT),
                   Pin(num="4", name="NC",   func=Pin.types.NOCONNECT),
                   Pin(num="5", name="VOUT", func=Pin.types.PWROUT),
               ])
    ldo["VIN"]  += vbat
    ldo["GND"]  += gnd
    ldo["EN"]   += vbat  # Always enabled
    ldo["VOUT"] += v3v3

    # NC pin
    nc_net = Net("LDO_NC")
    nc_net.drive = POWER
    ldo["NC"] += nc_net

    # LDO input cap
    c_ldo_in = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_ldo_in[1] += vbat
    c_ldo_in[2] += gnd

    # LDO output cap
    c_ldo_out = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0402_1005Metric")
    c_ldo_out[1] += v3v3
    c_ldo_out[2] += gnd

    # Bulk output cap
    c_ldo_bulk = Part("Device", "C", value="10uF",
                      footprint="Capacitor_SMD:C_0805_2012Metric")
    c_ldo_bulk[1] += v3v3
    c_ldo_bulk[2] += gnd

power_supply(vbus, vbat, v3v3, gnd)

# ---------------------------------------------------------------
# nRF52840 MCU
# ---------------------------------------------------------------
@subcircuit
def nrf52840_mcu(v3v3, gnd, usb_dp, usb_dm, sda, scl):
    """Nordic nRF52840 BLE SoC with essential passives."""

    nrf = Part(name="nRF52840", tool=SKIDL, dest=NETLIST,
               footprint="Package_DFN_QFN:Nordic_AQFN-73-1EP_7x7mm_P0.5mm",
               pins=[
                   # Power pins
                   Pin(num="1",  name="DEC1",     func=Pin.types.PASSIVE),
                   Pin(num="2",  name="P0.00",    func=Pin.types.BIDIR),
                   Pin(num="3",  name="P0.01",    func=Pin.types.BIDIR),
                   Pin(num="4",  name="P0.02",    func=Pin.types.BIDIR),
                   Pin(num="5",  name="P0.03",    func=Pin.types.BIDIR),
                   Pin(num="6",  name="P0.04",    func=Pin.types.BIDIR),
                   Pin(num="7",  name="P0.05",    func=Pin.types.BIDIR),
                   Pin(num="8",  name="P0.06",    func=Pin.types.BIDIR),
                   Pin(num="9",  name="P0.07",    func=Pin.types.BIDIR),
                   Pin(num="10", name="P0.08",    func=Pin.types.BIDIR),
                   Pin(num="11", name="VDD_1",    func=Pin.types.PWRIN),
                   Pin(num="12", name="P0.09",    func=Pin.types.BIDIR),
                   Pin(num="13", name="P0.10",    func=Pin.types.BIDIR),
                   Pin(num="14", name="NFC1",     func=Pin.types.BIDIR),
                   Pin(num="15", name="NFC2",     func=Pin.types.BIDIR),
                   Pin(num="16", name="P0.13",    func=Pin.types.BIDIR),
                   Pin(num="17", name="P0.14",    func=Pin.types.BIDIR),
                   Pin(num="18", name="P0.15",    func=Pin.types.BIDIR),
                   Pin(num="19", name="P0.16",    func=Pin.types.BIDIR),
                   Pin(num="20", name="P0.17",    func=Pin.types.BIDIR),
                   Pin(num="21", name="P0.18",    func=Pin.types.BIDIR),
                   Pin(num="22", name="P0.19",    func=Pin.types.BIDIR),
                   Pin(num="23", name="P0.20",    func=Pin.types.BIDIR),
                   Pin(num="24", name="P0.21",    func=Pin.types.BIDIR),
                   Pin(num="25", name="P0.22",    func=Pin.types.BIDIR),
                   Pin(num="26", name="P0.23",    func=Pin.types.BIDIR),
                   Pin(num="27", name="P0.24",    func=Pin.types.BIDIR),
                   Pin(num="28", name="SWDCLK",   func=Pin.types.INPUT),
                   Pin(num="29", name="SWDIO",    func=Pin.types.BIDIR),
                   Pin(num="30", name="P0.25",    func=Pin.types.BIDIR),
                   Pin(num="31", name="ANT",      func=Pin.types.PASSIVE),
                   Pin(num="32", name="VSS_1",    func=Pin.types.PWRIN),
                   Pin(num="33", name="DEC2",     func=Pin.types.PASSIVE),
                   Pin(num="34", name="DEC3",     func=Pin.types.PASSIVE),
                   Pin(num="35", name="XC1",      func=Pin.types.INPUT),
                   Pin(num="36", name="XC2",      func=Pin.types.OUTPUT),
                   Pin(num="37", name="VDD_2",    func=Pin.types.PWRIN),
                   Pin(num="38", name="P0.26",    func=Pin.types.BIDIR),
                   Pin(num="39", name="P0.27",    func=Pin.types.BIDIR),
                   Pin(num="40", name="P0.28",    func=Pin.types.BIDIR),  # AIN4
                   Pin(num="41", name="P0.29",    func=Pin.types.BIDIR),  # AIN5
                   Pin(num="42", name="P0.30",    func=Pin.types.BIDIR),  # AIN6
                   Pin(num="43", name="P0.31",    func=Pin.types.BIDIR),  # AIN7
                   Pin(num="44", name="P1.00",    func=Pin.types.BIDIR),
                   Pin(num="45", name="P1.01",    func=Pin.types.BIDIR),
                   Pin(num="46", name="P1.02",    func=Pin.types.BIDIR),
                   Pin(num="47", name="P1.03",    func=Pin.types.BIDIR),
                   Pin(num="48", name="P1.04",    func=Pin.types.BIDIR),
                   Pin(num="49", name="P1.05",    func=Pin.types.BIDIR),
                   Pin(num="50", name="P1.06",    func=Pin.types.BIDIR),
                   Pin(num="51", name="P1.07",    func=Pin.types.BIDIR),
                   Pin(num="52", name="VDD_3",    func=Pin.types.PWRIN),
                   Pin(num="53", name="P1.08",    func=Pin.types.BIDIR),
                   Pin(num="54", name="P1.09",    func=Pin.types.BIDIR),
                   Pin(num="55", name="P1.10",    func=Pin.types.BIDIR),
                   Pin(num="56", name="P1.11",    func=Pin.types.BIDIR),
                   Pin(num="57", name="P1.12",    func=Pin.types.BIDIR),
                   Pin(num="58", name="P1.13",    func=Pin.types.BIDIR),
                   Pin(num="59", name="P1.14",    func=Pin.types.BIDIR),
                   Pin(num="60", name="P1.15",    func=Pin.types.BIDIR),
                   Pin(num="61", name="VDD_4",    func=Pin.types.PWRIN),
                   Pin(num="62", name="D-",       func=Pin.types.BIDIR),
                   Pin(num="63", name="D+",       func=Pin.types.BIDIR),
                   Pin(num="64", name="VBUS_NRF", func=Pin.types.PWRIN),
                   Pin(num="65", name="VSS_2",    func=Pin.types.PWRIN),
                   Pin(num="66", name="DEC4",     func=Pin.types.PASSIVE),
                   Pin(num="67", name="DCC",      func=Pin.types.PASSIVE),
                   Pin(num="68", name="VDD_5",    func=Pin.types.PWRIN),
                   Pin(num="69", name="DEC5",     func=Pin.types.PASSIVE),
                   Pin(num="70", name="DEC6",     func=Pin.types.PASSIVE),
                   Pin(num="71", name="VDDH",     func=Pin.types.PWRIN),
                   Pin(num="72", name="VSS_3",    func=Pin.types.PWRIN),
                   Pin(num="73", name="RESET",    func=Pin.types.INPUT),
                   Pin(num="74", name="EP",       func=Pin.types.PWRIN),  # Exposed pad
               ])

    # Power connections
    for p in ["VDD_1", "VDD_2", "VDD_3", "VDD_4", "VDD_5"]:
        nrf[p] += v3v3
    nrf["VDDH"]     += v3v3
    nrf["VBUS_NRF"] += vbus
    for p in ["VSS_1", "VSS_2", "VSS_3", "EP"]:
        nrf[p] += gnd

    # USB data
    nrf["D+"] += usb_dp
    nrf["D-"] += usb_dm

    # I2C
    nrf["P0.26"] += sda
    nrf["P0.27"] += scl

    # Decoupling caps for VDD (100nF per VDD pair + one bulk 4.7uF)
    for _ in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v3v3
        c[2] += gnd

    c_bulk = Part("Device", "C", value="4.7uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

    # DEC pins need their own decoupling caps (100nF each)
    for dec_pin in ["DEC1", "DEC2", "DEC3", "DEC4", "DEC5", "DEC6"]:
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += nrf[dec_pin]
        c[2] += gnd

    # DC/DC inductor on DCC pin
    l_dcc = Part("Device", "L", value="10uH",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    l_dcc[1] += nrf["DCC"]
    l_dcc[2] += v3v3

    # 32 MHz crystal
    xtal = Part("Device", "Crystal", value="32MHz",
                footprint="Crystal:Crystal_SMD_2016-4Pin_2.0x1.6mm")
    xtal[1] += nrf["XC1"]
    xtal[2] += nrf["XC2"]
    # Crystal load caps
    c_x1 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += nrf["XC1"]
    c_x1[2] += gnd
    c_x2[1] += nrf["XC2"]
    c_x2[2] += gnd

    # Antenna matching network (simple pi: series inductor + shunt caps)
    ant_net = Net("ANT_RF")
    l_ant = Part("Device", "L", value="3.3nH",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    c_ant1 = Part("Device", "C", value="0.8pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_ant2 = Part("Device", "C", value="1.0pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    nrf["ANT"] += c_ant1[1]
    c_ant1[2] += gnd
    nrf["ANT"] += l_ant[1]
    l_ant[2] += ant_net
    c_ant2[1] += ant_net
    c_ant2[2] += gnd

    # Chip antenna
    antenna = Part(name="ANT_2.4GHz", tool=SKIDL, dest=NETLIST,
                   footprint="RF_Antenna:Johanson_2450AT18x100_2400-2500Mhz",
                   pins=[
                       Pin(num="1", name="ANT",  func=Pin.types.PASSIVE),
                       Pin(num="2", name="GND",  func=Pin.types.PASSIVE),
                   ])
    antenna["ANT"] += ant_net
    antenna["GND"] += gnd

    # Reset button + pullup
    sw_rst = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    sw_rst[1] += v3v3
    sw_rst[2] += nrf["RESET"]

    btn_rst = Part(name="SW_RST", tool=SKIDL, dest=NETLIST,
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                   pins=[
                       Pin(num="1", name="A", func=Pin.types.PASSIVE),
                       Pin(num="2", name="B", func=Pin.types.PASSIVE),
                   ])
    btn_rst["A"] += nrf["RESET"]
    btn_rst["B"] += gnd

    # User button (active-low on P1.02)
    btn_usr = Part(name="SW_USR", tool=SKIDL, dest=NETLIST,
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                   pins=[
                       Pin(num="1", name="A", func=Pin.types.PASSIVE),
                       Pin(num="2", name="B", func=Pin.types.PASSIVE),
                   ])
    btn_usr["A"] += nrf["P1.02"]
    btn_usr["B"] += gnd

    # User LED (red, active-low on P1.15)
    led_usr = Part("Device", "LED", value="RED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_led_usr = Part("Device", "R", value="1K",
                     footprint="Resistor_SMD:R_0402_1005Metric")
    nrf["P1.15"] += r_led_usr[1]
    r_led_usr[2] += led_usr[1]
    led_usr[2] += gnd

    # Blue BLE LED on P1.10
    led_ble = Part("Device", "LED", value="BLUE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_led_ble = Part("Device", "R", value="1K",
                     footprint="Resistor_SMD:R_0402_1005Metric")
    nrf["P1.10"] += r_led_ble[1]
    r_led_ble[2] += led_ble[1]
    led_ble[2] += gnd

    # NeoPixel on P0.16
    neo = Part(name="WS2812B", tool=SKIDL, dest=NETLIST,
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
               pins=[
                   Pin(num="1", name="VDD",  func=Pin.types.PWRIN),
                   Pin(num="2", name="DOUT", func=Pin.types.OUTPUT),
                   Pin(num="3", name="VSS",  func=Pin.types.PWRIN),
                   Pin(num="4", name="DIN",  func=Pin.types.INPUT),
               ])
    neo["VDD"] += v3v3
    neo["VSS"] += gnd
    neo["DIN"] += nrf["P0.16"]
    neo_dout = Net("NEO_DOUT")
    neo_dout.drive = POWER
    neo["DOUT"] += neo_dout

    # NeoPixel decoupling
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += v3v3
    c_neo[2] += gnd

    # I2C pull-ups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v3v3; r_sda[2] += sda
    r_scl[1] += v3v3; r_scl[2] += scl

    # SPI flash (QSPI) GD25Q16 on SOIC-8
    flash = Part(name="GD25Q16", tool=SKIDL, dest=NETLIST,
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
    flash["VCC"] += v3v3
    flash["GND"] += gnd
    flash["CS"]  += nrf["P0.20"]   # QSPI CS
    flash["CLK"] += nrf["P0.21"]   # QSPI CLK
    flash["DI"]  += nrf["P0.22"]   # QSPI D0
    flash["DO"]  += nrf["P0.23"]   # QSPI D1
    flash["WP"]  += nrf["P0.24"]   # QSPI D2
    flash["HOLD"]+= nrf["P0.25"]   # QSPI D3

    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += v3v3
    c_flash[2] += gnd

nrf52840_mcu(v3v3, gnd, usb_dp, usb_dm, i2c_sda, i2c_scl)

# ---------------------------------------------------------------
# LSM6DS33 Accel/Gyro (I2C)
# ---------------------------------------------------------------
@subcircuit
def lsm6ds33_sensor(v3v3, gnd, sda, scl):
    """LSM6DS33 6-DoF IMU on I2C."""
    imu = Part(name="LSM6DS33", tool=SKIDL, dest=NETLIST,
               footprint="Package_LGA:LGA-14_3x2.5mm_P0.5mm_LayoutBorder3x4y",
               pins=[
                   Pin(num="1",  name="SDO_SA0",  func=Pin.types.BIDIR),
                   Pin(num="2",  name="SDX",       func=Pin.types.BIDIR),
                   Pin(num="3",  name="SCX",       func=Pin.types.INPUT),
                   Pin(num="4",  name="INT1",      func=Pin.types.OUTPUT),
                   Pin(num="5",  name="VDDIO",     func=Pin.types.PWRIN),
                   Pin(num="6",  name="GND1",      func=Pin.types.PWRIN),
                   Pin(num="7",  name="GND2",      func=Pin.types.PWRIN),
                   Pin(num="8",  name="VDD",       func=Pin.types.PWRIN),
                   Pin(num="9",  name="INT2",      func=Pin.types.OUTPUT),
                   Pin(num="10", name="OCS_AUX",   func=Pin.types.BIDIR),
                   Pin(num="11", name="SDO_AUX",   func=Pin.types.BIDIR),
                   Pin(num="12", name="CS",        func=Pin.types.INPUT),
                   Pin(num="13", name="SCL",       func=Pin.types.INPUT),
                   Pin(num="14", name="SDA",       func=Pin.types.BIDIR),
               ])
    imu["VDD"]   += v3v3
    imu["VDDIO"] += v3v3
    imu["GND1"]  += gnd
    imu["GND2"]  += gnd
    imu["SDA"]   += sda
    imu["SCL"]   += scl
    imu["CS"]    += v3v3   # CS high = I2C mode
    imu["SDO_SA0"] += gnd  # SA0 low = address 0x6A

    # Tie unused aux pins
    imu_int1_net = Net("IMU_INT1")
    imu_int1_net.drive = POWER
    imu["INT1"] += imu_int1_net
    imu_int2_net = Net("IMU_INT2")
    imu_int2_net.drive = POWER
    imu["INT2"] += imu_int2_net
    imu_sdx_net = Net("IMU_SDX")
    imu_sdx_net.drive = POWER
    imu["SDX"] += imu_sdx_net
    imu_scx_net = Net("IMU_SCX")
    imu_scx_net.drive = POWER
    imu["SCX"] += imu_scx_net
    imu_ocsaux_net = Net("IMU_OCS_AUX")
    imu_ocsaux_net.drive = POWER
    imu["OCS_AUX"] += imu_ocsaux_net
    imu_sdoaux_net = Net("IMU_SDO_AUX")
    imu_sdoaux_net.drive = POWER
    imu["SDO_AUX"] += imu_sdoaux_net

    # Decoupling caps
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += v3v3; c1[2] += gnd

lsm6ds33_sensor(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------
# LIS3MDL Magnetometer (I2C)
# ---------------------------------------------------------------
@subcircuit
def lis3mdl_sensor(v3v3, gnd, sda, scl):
    """LIS3MDL 3-axis magnetometer."""
    mag = Part(name="LIS3MDL", tool=SKIDL, dest=NETLIST,
               footprint="Package_LGA:LGA-12_2x2mm_P0.5mm",
               pins=[
                   Pin(num="1",  name="SCL",   func=Pin.types.INPUT),
                   Pin(num="2",  name="GND1",  func=Pin.types.PWRIN),
                   Pin(num="3",  name="C1",    func=Pin.types.PASSIVE),
                   Pin(num="4",  name="VDD",   func=Pin.types.PWRIN),
                   Pin(num="5",  name="VDD_IO",func=Pin.types.PWRIN),
                   Pin(num="6",  name="INT",   func=Pin.types.OUTPUT),
                   Pin(num="7",  name="DRDY",  func=Pin.types.OUTPUT),
                   Pin(num="8",  name="SDA",   func=Pin.types.BIDIR),
                   Pin(num="9",  name="SDO",   func=Pin.types.BIDIR),
                   Pin(num="10", name="CS",    func=Pin.types.INPUT),
                   Pin(num="11", name="GND2",  func=Pin.types.PWRIN),
                   Pin(num="12", name="GND3",  func=Pin.types.PWRIN),
               ])
    mag["VDD"]    += v3v3
    mag["VDD_IO"] += v3v3
    mag["GND1"]   += gnd
    mag["GND2"]   += gnd
    mag["GND3"]   += gnd
    mag["SDA"]    += sda
    mag["SCL"]    += scl
    mag["CS"]     += v3v3   # CS high = I2C mode
    mag["SDO"]    += gnd    # SDO low = address 0x1C

    mag_int_net = Net("MAG_INT")
    mag_int_net.drive = POWER
    mag["INT"] += mag_int_net
    mag_drdy_net = Net("MAG_DRDY")
    mag_drdy_net.drive = POWER
    mag["DRDY"] += mag_drdy_net

    # C1 filter cap
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += mag["C1"]
    c1[2] += gnd

    # Decoupling
    c2 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2[1] += v3v3; c2[2] += gnd

lis3mdl_sensor(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------
# APDS9960 Proximity/Light/Color/Gesture (I2C)
# ---------------------------------------------------------------
@subcircuit
def apds9960_sensor(v3v3, gnd, sda, scl):
    """APDS9960 gesture/color/proximity sensor."""
    apds = Part(name="APDS9960", tool=SKIDL, dest=NETLIST,
                footprint="Package_LGA:AMS_LGA-10-1EP_2.7x4mm_P0.6mm",
                pins=[
                    Pin(num="1",  name="SDA",     func=Pin.types.BIDIR),
                    Pin(num="2",  name="GND1",    func=Pin.types.PWRIN),
                    Pin(num="3",  name="LEDK",    func=Pin.types.PASSIVE),
                    Pin(num="4",  name="LEDA",    func=Pin.types.PASSIVE),
                    Pin(num="5",  name="VDD",     func=Pin.types.PWRIN),
                    Pin(num="6",  name="GND2",    func=Pin.types.PWRIN),
                    Pin(num="7",  name="SCL",     func=Pin.types.INPUT),
                    Pin(num="8",  name="INT",     func=Pin.types.OUTPUT),
                    Pin(num="9",  name="LDR",     func=Pin.types.PASSIVE),
                    Pin(num="10", name="VLED",    func=Pin.types.PWRIN),
                    Pin(num="11", name="EP",      func=Pin.types.PWRIN),
                ])
    apds["VDD"]  += v3v3
    apds["VLED"] += v3v3
    apds["GND1"] += gnd
    apds["GND2"] += gnd
    apds["EP"]   += gnd
    apds["SDA"]  += sda
    apds["SCL"]  += scl

    # LED drive resistor
    r_led = Part("Device", "R", value="68R",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    apds["LEDA"] += v3v3
    apds["LEDK"] += r_led[1]
    r_led[2] += gnd

    # LDR connection
    apds_ldr_net = Net("APDS_LDR")
    apds_ldr_net.drive = POWER
    apds["LDR"] += apds_ldr_net

    # Interrupt
    apds_int_net = Net("APDS_INT")
    apds_int_net.drive = POWER
    apds["INT"] += apds_int_net

    # Decoupling
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += v3v3; c1[2] += gnd

    c2 = Part("Device", "C", value="1uF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2[1] += v3v3; c2[2] += gnd

apds9960_sensor(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------
# PDM Microphone
# ---------------------------------------------------------------
@subcircuit
def pdm_microphone(v3v3, gnd):
    """MP34DT01 MEMS PDM microphone."""
    mic = Part(name="MP34DT01", tool=SKIDL, dest=NETLIST,
               footprint="Package_LGA:ST_HLGA-10_2.5x2.5mm_P0.6mm_LayoutBorder3x2y",
               pins=[
                   Pin(num="1",  name="SDO",    func=Pin.types.OUTPUT),
                   Pin(num="2",  name="WS",     func=Pin.types.INPUT),
                   Pin(num="3",  name="NC1",    func=Pin.types.NOCONNECT),
                   Pin(num="4",  name="GND1",   func=Pin.types.PWRIN),
                   Pin(num="5",  name="VDD",    func=Pin.types.PWRIN),
                   Pin(num="6",  name="NC2",    func=Pin.types.NOCONNECT),
                   Pin(num="7",  name="NC3",    func=Pin.types.NOCONNECT),
                   Pin(num="8",  name="CLK",    func=Pin.types.INPUT),
                   Pin(num="9",  name="GND2",   func=Pin.types.PWRIN),
                   Pin(num="10", name="GND3",   func=Pin.types.PWRIN),
               ])
    mic["VDD"]  += v3v3
    mic["GND1"] += gnd
    mic["GND2"] += gnd
    mic["GND3"] += gnd

    # NC pins
    mic_nc1 = Net("MIC_NC1"); mic_nc1.drive = POWER
    mic_nc2 = Net("MIC_NC2"); mic_nc2.drive = POWER
    mic_nc3 = Net("MIC_NC3"); mic_nc3.drive = POWER
    mic["NC1"] += mic_nc1
    mic["NC2"] += mic_nc2
    mic["NC3"] += mic_nc3

    # PDM data and clock connected to nRF52840
    pdm_data = Net("PDM_DATA")
    pdm_clk  = Net("PDM_CLK")
    pdm_data.drive = POWER
    pdm_clk.drive  = POWER
    mic["SDO"] += pdm_data
    mic["CLK"] += pdm_clk

    # WS (L/R select) tied to GND for left channel
    mic["WS"] += gnd

    # Decoupling
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += v3v3; c1[2] += gnd

pdm_microphone(v3v3, gnd)

# ---------------------------------------------------------------
# SHT30 Humidity/Temperature (I2C)
# ---------------------------------------------------------------
@subcircuit
def sht30_sensor(v3v3, gnd, sda, scl):
    """SHT30 humidity and temperature sensor."""
    sht = Part(name="SHT30", tool=SKIDL, dest=NETLIST,
               footprint="Package_DFN_QFN:DFN-8_2x2mm_P0.5mm",
               pins=[
                   Pin(num="1", name="SDA",   func=Pin.types.BIDIR),
                   Pin(num="2", name="ADDR",  func=Pin.types.INPUT),
                   Pin(num="3", name="ALERT", func=Pin.types.OUTPUT),
                   Pin(num="4", name="SCL",   func=Pin.types.INPUT),
                   Pin(num="5", name="VDD",   func=Pin.types.PWRIN),
                   Pin(num="6", name="NRESET",func=Pin.types.INPUT),
                   Pin(num="7", name="R",     func=Pin.types.PASSIVE),
                   Pin(num="8", name="VSS",   func=Pin.types.PWRIN),
               ])
    sht["VDD"]    += v3v3
    sht["VSS"]    += gnd
    sht["SDA"]    += sda
    sht["SCL"]    += scl
    sht["ADDR"]   += gnd    # Address 0x44
    sht["NRESET"] += v3v3   # Not reset

    # Alert (unused, float OK with net)
    sht_alert_net = Net("SHT_ALERT")
    sht_alert_net.drive = POWER
    sht["ALERT"] += sht_alert_net

    # R pin - filter
    sht_r_net = Net("SHT_R")
    sht_r_net.drive = POWER
    sht["R"] += sht_r_net

    # Decoupling
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += v3v3; c1[2] += gnd

sht30_sensor(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------
# BMP280 Barometric Pressure/Temperature (I2C)
# ---------------------------------------------------------------
@subcircuit
def bmp280_sensor(v3v3, gnd, sda, scl):
    """BMP280 pressure/temperature sensor."""
    bmp = Part(name="BMP280", tool=SKIDL, dest=NETLIST,
               footprint="Package_LGA:Bosch_LGA-8_2x2.5mm_P0.65mm_ClockwisePinNumbering",
               pins=[
                   Pin(num="1", name="GND1",   func=Pin.types.PWRIN),
                   Pin(num="2", name="CSB",    func=Pin.types.INPUT),
                   Pin(num="3", name="SDA",    func=Pin.types.BIDIR),
                   Pin(num="4", name="SCL",    func=Pin.types.INPUT),
                   Pin(num="5", name="GND2",   func=Pin.types.PWRIN),
                   Pin(num="6", name="VDDIO",  func=Pin.types.PWRIN),
                   Pin(num="7", name="GND3",   func=Pin.types.PWRIN),
                   Pin(num="8", name="VDD",    func=Pin.types.PWRIN),
               ])
    bmp["VDD"]   += v3v3
    bmp["VDDIO"] += v3v3
    bmp["GND1"]  += gnd
    bmp["GND2"]  += gnd
    bmp["GND3"]  += gnd
    bmp["SDA"]   += sda
    bmp["SCL"]   += scl
    bmp["CSB"]   += v3v3  # CS high = I2C mode

    # Decoupling
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += v3v3; c1[2] += gnd

bmp280_sensor(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------
# Feather Headers (1x16 + 1x12)
# ---------------------------------------------------------------
@subcircuit
def feather_headers(v3v3, vbus, vbat, gnd):
    """Standard Feather-format pin headers."""
    # 16-pin header (left side)
    hdr_l = Part("Connector_Generic", "Conn_01x16",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    # Pin assignments: RST, 3V3, AREF, GND, A0-A5, SCK, MOSI, MISO, RX, TX, D4
    hdr_l[1]  += Net("HDR_RST")
    hdr_l[2]  += v3v3
    hdr_l[3]  += Net("AREF")
    hdr_l[4]  += gnd
    hdr_l[5]  += Net("A0")
    hdr_l[6]  += Net("A1")
    hdr_l[7]  += Net("A2")
    hdr_l[8]  += Net("A3")
    hdr_l[9]  += Net("A4")
    hdr_l[10] += Net("A5")
    hdr_l[11] += Net("SCK")
    hdr_l[12] += Net("MOSI")
    hdr_l[13] += Net("MISO")
    hdr_l[14] += Net("UART_RX")
    hdr_l[15] += Net("UART_TX")
    hdr_l[16] += Net("D4")

    # Drive all header nets
    for n in [hdr_l[p].net for p in range(1, 17) if hdr_l[p].net]:
        n.drive = POWER

    # 12-pin header (right side)
    hdr_r = Part("Connector_Generic", "Conn_01x12",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    # Pin assignments: BAT, EN, VBUS, D13-D5
    hdr_r[1]  += vbat
    hdr_r[2]  += Net("EN")
    hdr_r[3]  += vbus
    hdr_r[4]  += Net("D13")
    hdr_r[5]  += Net("D12")
    hdr_r[6]  += Net("D11")
    hdr_r[7]  += Net("D10")
    hdr_r[8]  += Net("D9")
    hdr_r[9]  += Net("D6")
    hdr_r[10] += Net("D5")
    hdr_r[11] += i2c_sda
    hdr_r[12] += i2c_scl

    for n in [hdr_r[p].net for p in range(1, 13) if hdr_r[p].net]:
        n.drive = POWER

feather_headers(v3v3, vbus, vbat, gnd)

# ---------------------------------------------------------------
# Battery Connector (JST-PH 2-pin)
# ---------------------------------------------------------------
@subcircuit
def battery_connector(vbat, gnd):
    """JST-PH 2-pin LiPo battery connector."""
    jst = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    jst[1] += vbat
    jst[2] += gnd

battery_connector(vbat, gnd)

# ---------------------------------------------------------------
# SWD Debug Header
# ---------------------------------------------------------------
@subcircuit
def swd_header(v3v3, gnd):
    """SWD debug header (2x5 1.27mm)."""
    swd = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    swd[1] += v3v3
    swdclk_net = Net("SWDCLK")
    swdclk_net.drive = POWER
    swdio_net = Net("SWDIO")
    swdio_net.drive = POWER
    swd[2] += swdclk_net
    swd[3] += swdio_net
    swd[4] += gnd

swd_header(v3v3, gnd)

# ---------------------------------------------------------------
# Fix all SKIDL-tool parts for schematic generation
# ---------------------------------------------------------------
for part in default_circuit.parts:
    if part.tool == SKIDL:
        _fix_skidl_pins(part)

# ---------------------------------------------------------------
# Generate schematic
# ---------------------------------------------------------------
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
