"""
Feather nRF52840 Sense -- SKiDL circuit for MCP server submission.

Uses Raytac MDBT50Q-1MV2 pre-certified BLE module (nRF52840 inside).
Sensors: LSM6DS33 accel/gyro + LIS3MDL magnetometer + SHT31 humidity/temp (all I2C).
PDM MEMS mic (SPH0645LM4H), USB-C, MCP73831 LiPo charger, JST-PH battery,
AP2112K-3.3 LDO. NeoPixel WS2812B + red user LED. Feather 1x16 + 1x12 headers.
SWD debug 1x4. Board: 50.8 x 22.86 mm.
"""

import os
os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from skidl import *
set_default_tool(KICAD9)

# ---------------------------------------------------------------------------
# Feather form factor: 50.8 x 22.86 mm
# MDBT50Q module (17.0 x 13.0 mm) placed centre-right.
# Headers along long top/bottom edges. USB-C left short edge.
# JST battery connector right short edge.
# All small ICs and passives in the narrow strip to the left of the module.
# ---------------------------------------------------------------------------
EDA_FLOORPLAN = {
    "outline": [50.8, 22.86],
    "corner_radius_mm": 1.0,
    "edge_anchors": [
        # USB-C on left short edge (centred vertically)
        {"ref": "J1",  "edge": "left",   "position": 0.5},
        # JST battery connector on right short edge
        {"ref": "J5",  "edge": "right",  "position": 0.5},
        # Feather 1x16 header along top long edge
        {"ref": "J3",  "edge": "top",    "position": 0.5},
        # Feather 1x12 header along bottom long edge
        {"ref": "J4",  "edge": "bottom", "position": 0.5},
    ],
    "fixed_positions": [
        # MDBT50Q module (17x13mm) - placed right-of-centre, clear of headers
        # Headers are 2.54mm wide; module centre at x=33, y=11.43 (vertical centre)
        {"ref": "U3", "x": 33.0, "y": 11.43, "rotation": 0},
        # SWD header in upper-right area, clear of module
        {"ref": "J100", "x": 47.5, "y": 3.5, "rotation": 0},
        # Reset button in upper-left area
        {"ref": "SW1", "x": 5.0, "y": 3.5, "rotation": 0},
    ],
}

# ---------------------------------------------------------------------------
# Global power nets
# ---------------------------------------------------------------------------
vbus = Net("VBUS");  vbus.drive = POWER
vbat = Net("VBAT");  vbat.drive = POWER
v3v3 = Net("+3V3");  v3v3.drive = POWER
gnd  = Net("GND");   gnd.drive  = POWER

i2c_sda = Net("SDA")
i2c_scl = Net("SCL")
usb_dp  = Net("USB_DP")
usb_dm  = Net("USB_DM")

# ---------------------------------------------------------------------------
# USB-C connector (16-pin, USB 2.0 data + power)
# Pins: SHIELD=S1, GND=A1/A12/B1/B12, VBUS=A4/A9/B4/B9,
#       CC1=A5, CC2=B5, D-=A7/B7, D+=A6/B6, SBU1=A8, SBU2=B8
# ---------------------------------------------------------------------------
@subcircuit
def usb_c_block(vbus, gnd, dp, dm):
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
               footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11")
    usb["VBUS"] += vbus
    usb["GND"]  += gnd
    usb["D+"]   += dp
    usb["D-"]   += dm
    usb["SHIELD"] += gnd
    usb["SBU1"] += Net("SBU1")
    usb["SBU2"] += Net("SBU2")
    # CC pull-down resistors 5.1k for UFP mode
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["CC1"]; r_cc1[2] += gnd
    r_cc2[1] += usb["CC2"]; r_cc2[2] += gnd
    # VBUS decoupling
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += vbus; c[2] += gnd

usb_c_block(vbus, gnd, usb_dp, usb_dm)

# ---------------------------------------------------------------------------
# Power: MCP73831-2-OT LiPo charger + AP2112K-3.3 LDO
# MCP73831: STAT=1, V_{SS}=2, V_{BAT}=3, V_{DD}=4, PROG=5
# AP2112K-3.3: VIN=1, GND=2, EN=3, NC=4, VOUT=5
# ---------------------------------------------------------------------------
@subcircuit
def power_block(vbus, vbat, v3v3, gnd):
    chg = Part("Battery_Management", "MCP73831-2-OT",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    chg["V_{DD}"]  += vbus
    chg["V_{SS}"]  += gnd
    chg["V_{BAT}"] += vbat
    # Charge rate resistor: 2k = 500mA
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_prog[1] += chg["PROG"]
    r_prog[2] += gnd
    # Charge status LED
    r_stat = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    chg["STAT"] += r_stat[1]
    r_stat[2]   += led_chg["K"]
    led_chg["A"] += vbus
    # Charger input/output decoupling
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vbus; c_in[2] += gnd
    c_bat = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bat[1] += vbat; c_bat[2] += gnd

    # AP2112K-3.3 LDO (SOT-25): VIN=1, GND=2, EN=3, NC=4, VOUT=5
    ldo = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    ldo["VIN"]  += vbat
    ldo["GND"]  += gnd
    ldo["EN"]   += vbat     # always on
    ldo["VOUT"] += v3v3
    ldo["NC"]   += Net("LDO_NC")
    # LDO decoupling
    c_ldo_in = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0402_1005Metric")
    c_ldo_in[1] += vbat; c_ldo_in[2] += gnd
    c_ldo_out = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0402_1005Metric")
    c_ldo_out[1] += v3v3; c_ldo_out[2] += gnd
    c_ldo_bulk = Part("Device", "C", value="10uF",
                      footprint="Capacitor_SMD:C_0805_2012Metric")
    c_ldo_bulk[1] += v3v3; c_ldo_bulk[2] += gnd

power_block(vbus, vbat, v3v3, gnd)

# ---------------------------------------------------------------------------
# Raytac MDBT50Q-1MV2 BLE module (nRF52840 inside, pre-certified)
# Key pins: VDD=28, VDDH=30, GND=1/2/15/33/55, VBUS=32, D+=35, D-=34
#           SWDIO=51, SWDCLK=53, P0.11=27(SCL), P0.12=29(SDA)
# ---------------------------------------------------------------------------
@subcircuit
def mdbt50q_block(v3v3, vbus, gnd, dp, dm, sda, scl):
    mod = Part("RF_Module", "MDBT50Q-1MV2",
               footprint="RF_Module:Raytac_MDBT50Q")

    # Power
    mod["VDD"]  += v3v3
    mod["VDDH"] += v3v3
    mod["GND"]  += gnd
    mod["VBUS"] += vbus
    # USB data
    mod["D+"] += dp
    mod["D-"] += dm
    # I2C (Adafruit Sense: P0.11=SCL, P0.12=SDA)
    mod["P0.11"] += scl
    mod["P0.12"] += sda
    # SWD
    swdclk_net = Net("SWDCLK"); swdclk_net.drive = POWER
    swdio_net  = Net("SWDIO");  swdio_net.drive  = POWER
    mod["SWDCLK"] += swdclk_net
    mod["SWDIO"]  += swdio_net
    # PDM microphone
    pdm_clk  = Net("PDM_CLK");  pdm_clk.drive  = POWER
    pdm_data = Net("PDM_DATA"); pdm_data.drive = POWER
    mod["P0.00"] += pdm_clk
    mod["P0.01"] += pdm_data
    # NeoPixel data out
    neo_din = Net("NEO_DIN"); neo_din.drive = POWER
    mod["P0.16"] += neo_din
    # User red LED
    led_io = Net("LED_RED_IO"); led_io.drive = POWER
    mod["P1.15"] += led_io

    # Module decoupling caps
    for _ in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v3v3; c[2] += gnd
    c_bulk = Part("Device", "C", value="4.7uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bulk[1] += v3v3; c_bulk[2] += gnd

    # I2C pull-ups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v3v3; r_sda[2] += sda
    r_scl[1] += v3v3; r_scl[2] += scl

    # Reset button (Switch:SW_Push has pins 1 and 2)
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    rst_net = Net("nRST"); rst_net.drive = POWER
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += v3v3; r_rst[2] += rst_net
    sw_rst[1] += rst_net; sw_rst[2] += gnd

    # User red LED with series resistor
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led_red = Part("Device", "LED", value="RED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    mod["P1.15"] += r_led[1]
    r_led[2]     += led_red["K"]
    led_red["A"] += v3v3

    # DCCH pin bypass cap
    c_dcch = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dcch[1] += mod["DCCH"]
    c_dcch[2] += gnd

mdbt50q_block(v3v3, vbus, gnd, usb_dp, usb_dm, i2c_sda, i2c_scl)

# ---------------------------------------------------------------------------
# NeoPixel WS2812B
# Pins: DIN=4, VDD=1, VSS=3, DOUT=2
# ---------------------------------------------------------------------------
@subcircuit
def neopixel_block(v3v3, gnd):
    neo_din = Net("NEO_DIN")
    neo = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    neo["VDD"]  += v3v3
    neo["VSS"]  += gnd
    neo["DIN"]  += neo_din
    neo_dout = Net("NEO_DOUT"); neo_dout.drive = POWER
    neo["DOUT"] += neo_dout
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += v3v3; c[2] += gnd

neopixel_block(v3v3, gnd)

# ---------------------------------------------------------------------------
# LSM6DS33 Accel/Gyro (I2C) -- SKIDL custom part, LGA-14 footprint
# Real pin names from ST datasheet: VDD=8, VDDIO=5, GND=6/7,
# SDA=14, SCL=13, CS=12, SDO/SA0=1, INT1=4, INT2=9
# ---------------------------------------------------------------------------
@subcircuit
def lsm6ds33_block(v3v3, gnd, sda, scl):
    imu = Part(name="LSM6DS33", tool=SKIDL, dest=NETLIST,
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
                   Pin(num="10", name="OCS_AUX",  func=Pin.types.BIDIR),
                   Pin(num="11", name="SDO_AUX",  func=Pin.types.BIDIR),
                   Pin(num="12", name="CS",       func=Pin.types.INPUT),
                   Pin(num="13", name="SCL",      func=Pin.types.INPUT),
                   Pin(num="14", name="SDA",      func=Pin.types.BIDIR),
               ])
    imu["VDD"]     += v3v3
    imu["VDDIO"]   += v3v3
    imu["GND1"]    += gnd
    imu["GND2"]    += gnd
    imu["SDA"]     += sda
    imu["SCL"]     += scl
    imu["CS"]      += v3v3   # I2C mode
    imu["SDO_SA0"] += gnd    # addr 0x6A

    imu_int1 = Net("IMU_INT1"); imu_int1.drive = POWER
    imu_int2 = Net("IMU_INT2"); imu_int2.drive = POWER
    imu["INT1"] += imu_int1
    imu["INT2"] += imu_int2
    imu_sdx = Net("IMU_SDX"); imu_sdx.drive = POWER
    imu_scx = Net("IMU_SCX"); imu_scx.drive = POWER
    imu_ocs = Net("IMU_OCS"); imu_ocs.drive = POWER
    imu_sdoaux = Net("IMU_SDO_AUX"); imu_sdoaux.drive = POWER
    imu["SDX"]     += imu_sdx
    imu["SCX"]     += imu_scx
    imu["OCS_AUX"] += imu_ocs
    imu["SDO_AUX"] += imu_sdoaux

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += v3v3; c[2] += gnd

lsm6ds33_block(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------------------
# LIS3MDL Magnetometer (I2C) -- LGA-12
# KiCad pins: ~{CS}=10, SCL/SPC=1, SDA/SDI/SDO=11, SDO/SA1=9,
#             Vdd=5, GND=2/3/12, Vdd_IO=6, INT=7, DRDY=8, C1=4
# ---------------------------------------------------------------------------
@subcircuit
def lis3mdl_block(v3v3, gnd, sda, scl):
    mag = Part("Sensor_Magnetic", "LIS3MDL",
               footprint="Package_LGA:LGA-12_2x2mm_P0.5mm")
    mag["Vdd"]     += v3v3
    mag["Vdd_IO"]  += v3v3
    mag["GND"]     += gnd
    mag["SCL/SPC"]      += scl
    mag["SDA/SDI/SDO"]  += sda
    mag["~{CS}"]   += v3v3  # I2C mode (CS high)
    mag["SDO/SA1"] += gnd   # addr 0x1C

    mag_int  = Net("MAG_INT");  mag_int.drive  = POWER
    mag_drdy = Net("MAG_DRDY"); mag_drdy.drive = POWER
    mag["INT"]  += mag_int
    mag["DRDY"] += mag_drdy

    # C1 filter cap
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += mag["C1"]; c1[2] += gnd
    # Decoupling
    c2 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2[1] += v3v3; c2[2] += gnd

lis3mdl_block(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------------------
# SHT31-DIS Humidity/Temperature (I2C) -- DFN-8-1EP
# KiCad pins: ADDR=2, ~{RESET}=6, R=7, VDD=5, VSS=8/9, SDA=1, SCL=4, ALERT=3
# Note: pin 9 is second VSS (EP), also pin 9 = EP per library
# ---------------------------------------------------------------------------
@subcircuit
def sht31_block(v3v3, gnd, sda, scl):
    sht = Part("Sensor_Humidity", "SHT31-DIS",
               footprint="Sensor_Humidity:Sensirion_DFN-8-1EP_2.5x2.5mm_P0.5mm_EP1.1x1.7mm")
    sht["SDA"]      += sda
    sht["SCL"]      += scl
    sht["VDD"]      += v3v3
    sht["VSS"]      += gnd
    sht["ADDR"]     += gnd          # addr 0x44
    sht["~{RESET}"] += v3v3         # not in reset
    sht["ALERT"]    += Net("SHT_ALERT")
    sht["R"]        += Net("SHT_R")

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += v3v3; c[2] += gnd

sht31_block(v3v3, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------------------
# PDM MEMS Microphone -- SPH0645LM4H (I2S/PDM, LGA-6)
# KiCad pins: SEL=2, VDD=5, GND=3, WS=1, BCLK=4, DATA=6
# ---------------------------------------------------------------------------
@subcircuit
def pdm_mic_block(v3v3, gnd):
    mic = Part("Sensor_Audio", "SPH0645LM4H",
               footprint="Sensor_Audio:Knowles_SPH0645LM4H-6_3.5x2.65mm")
    mic["VDD"]  += v3v3
    mic["GND"]  += gnd
    pdm_clk  = Net("PDM_CLK")
    pdm_data = Net("PDM_DATA")
    mic["BCLK"] += pdm_clk
    mic["DATA"] += pdm_data
    mic["WS"]   += gnd     # L/R select = left channel
    mic["SEL"]  += gnd     # I2S mode select

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += v3v3; c[2] += gnd

pdm_mic_block(v3v3, gnd)

# ---------------------------------------------------------------------------
# JST-PH 2-pin battery connector
# ---------------------------------------------------------------------------
@subcircuit
def battery_connector_block(vbat, gnd):
    jst = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    jst[1] += vbat
    jst[2] += gnd

battery_connector_block(vbat, gnd)

# ---------------------------------------------------------------------------
# Feather headers: 1x16 (left) + 1x12 (right)
# Connector_Generic pins are numeric: Pin_1, Pin_2, etc. (accessed by number)
# ---------------------------------------------------------------------------
@subcircuit
def feather_headers_block(v3v3, vbus, vbat, gnd, sda, scl):
    # 1x16 left header
    hl = Part("Connector_Generic", "Conn_01x16",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    rst_net = Net("HDR_RST"); rst_net.drive = POWER
    hl[1]  += rst_net
    hl[2]  += v3v3
    hl[3]  += Net("AREF")
    hl[4]  += gnd
    hl[5]  += Net("A0")
    hl[6]  += Net("A1")
    hl[7]  += Net("A2")
    hl[8]  += Net("A3")
    hl[9]  += Net("A4")
    hl[10] += Net("A5")
    hl[11] += Net("SCK")
    hl[12] += Net("MOSI")
    hl[13] += Net("MISO")
    hl[14] += Net("UART_RX")
    hl[15] += Net("UART_TX")
    hl[16] += Net("D4")

    # 1x12 right header
    hr = Part("Connector_Generic", "Conn_01x12",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    hr[1]  += vbat
    hr[2]  += Net("EN_3V3")
    hr[3]  += vbus
    hr[4]  += Net("D13")
    hr[5]  += Net("D12")
    hr[6]  += Net("D11")
    hr[7]  += Net("D10")
    hr[8]  += Net("D9")
    hr[9]  += Net("D6")
    hr[10] += Net("D5")
    hr[11] += sda
    hr[12] += scl

feather_headers_block(v3v3, vbus, vbat, gnd, i2c_sda, i2c_scl)

# ---------------------------------------------------------------------------
# SWD debug header (1x4)
# ---------------------------------------------------------------------------
@subcircuit
def swd_block(v3v3, gnd):
    swd = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    swdclk = Net("SWDCLK"); swdclk.drive = POWER
    swdio  = Net("SWDIO");  swdio.drive  = POWER
    swd[1] += v3v3
    swd[2] += swdclk
    swd[3] += swdio
    swd[4] += gnd

swd_block(v3v3, gnd)
