"""
QT Py RP2040 - Tiny RP2040 dev board in Seeeduino XIAO form factor (~17.5x21mm)
RP2040 dual-core ARM Cortex-M0+, 8MB QSPI flash (W25Q64/128), USB-C, NeoPixel,
Boot button, STEMMA QT I2C connector, 11 GPIO castellated edge pads, 3.3V LDO.
"""

from skidl import *

# ============================================================
# Power nets
# ============================================================
vbus = Net("VBUS"); vbus.drive = POWER
vcc_3v3 = Net("+3V3"); vcc_3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
dvdd = Net("DVDD"); dvdd.drive = POWER
vreg_out = Net("VREG_VOUT")

# Signal nets
usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")
sda = Net("SDA")
scl = Net("SCL")
neopixel_data = Net("NEOPIXEL")
neopixel_pwr = Net("NEOPIXEL_PWR")
xin = Net("XIN")
xout = Net("XOUT")
boot_n = Net("BOOT_N")
run_n = Net("RUN_N")

# QSPI Flash nets
qspi_clk = Net("QSPI_SCLK")
qspi_cs = Net("QSPI_CS")
qspi_sd0 = Net("QSPI_SD0")
qspi_sd1 = Net("QSPI_SD1")
qspi_sd2 = Net("QSPI_SD2")
qspi_sd3 = Net("QSPI_SD3")

# GPIO breakout nets (QT Py RP2040 exposed pins)
gpio0 = Net("GPIO0")
gpio1 = Net("GPIO1")
gpio2 = Net("GPIO2")
gpio3 = Net("GPIO3")
gpio4 = Net("GPIO4")
gpio5 = Net("GPIO5")
gpio6 = Net("GPIO6")
gpio7 = Net("GPIO7")
gpio8 = Net("GPIO8")
gpio9 = Net("GPIO9")
gpio10 = Net("GPIO10")
gpio20 = Net("GPIO20")
gpio24 = Net("GPIO24")
gpio25 = Net("GPIO25")
gpio26 = Net("GPIO26")
gpio27 = Net("GPIO27")
gpio28 = Net("GPIO28")
gpio29 = Net("GPIO29")


# ============================================================
# Subcircuit: USB-C connector with CC resistors
# ============================================================
@subcircuit
def usb_input(vbus_net, dp_net, dm_net, gnd_net):
    global vbus, usb_dp, usb_dm, gnd
    """USB-C connector with CC pull-down resistors for UFP (device) mode."""
    usb_conn = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
                    footprint="Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal")
    usb_conn.edge_preference = "bottom"

    # GND and shield pins
    usb_conn["A1"] += gnd_net
    usb_conn["B1"] += gnd_net
    usb_conn["A12"] += gnd_net
    usb_conn["B12"] += gnd_net
    usb_conn["SHIELD"] += gnd_net

    # VBUS pins
    usb_conn["A4"] += vbus_net
    usb_conn["A9"] += vbus_net
    usb_conn["B4"] += vbus_net
    usb_conn["B9"] += vbus_net

    # D+/D- (tied together for both orientations)
    usb_conn["A6"] += dp_net
    usb_conn["B6"] += dp_net
    usb_conn["A7"] += dm_net
    usb_conn["B7"] += dm_net

    # CC1/CC2 pull-down resistors (5.1k for UFP/device mode)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    usb_conn["CC1"] += r_cc1[1]
    r_cc1[2] += gnd_net
    usb_conn["CC2"] += r_cc2[1]
    r_cc2[2] += gnd_net

    # SBU pins not connected
    usb_conn["SBU1"] += NC
    usb_conn["SBU2"] += NC

usb_input(vbus, usb_dp, usb_dm, gnd)


# ============================================================
# Subcircuit: 3.3V voltage regulator (AP2112K-3.3)
# ============================================================
@subcircuit
def power_regulation(vin_net, vout_net, gnd_net):
    global vbus, vcc_3v3, gnd
    """AP2112K-3.3 LDO regulator with input/output decoupling."""
    reg = Part("Regulator_Linear", "AP2112K-3.3", value="AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg.lcsc = "C89358"
    reg["VIN"] += vin_net
    reg["GND"] += gnd_net
    reg["EN"] += vin_net  # Always enabled
    reg["VOUT"] += vout_net
    reg["NC"] += NC

    # Input decoupling
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vin_net
    c_in[2] += gnd_net

    # Output decoupling caps
    c_out1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out1[1] += vout_net
    c_out1[2] += gnd_net

    c_out2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out2[1] += vout_net
    c_out2[2] += gnd_net

power_regulation(vbus, vcc_3v3, gnd)


# ============================================================
# Subcircuit: RP2040 MCU
# ============================================================
@subcircuit
def rp2040_mcu(vcc_net, dvdd_net, gnd_net, usb_dp_net, usb_dm_net,
               xin_net, xout_net, vreg_out_net,
               qspi_clk_net, qspi_cs_net,
               qspi_sd0_net, qspi_sd1_net, qspi_sd2_net, qspi_sd3_net,
               boot_net, run_net, neopixel_net, neopixel_pwr_net,
               sda_net, scl_net,
               gpio0_net, gpio1_net, gpio2_net, gpio3_net, gpio4_net,
               gpio5_net, gpio6_net, gpio7_net, gpio8_net, gpio9_net,
               gpio10_net, gpio20_net, gpio24_net, gpio25_net,
               gpio26_net, gpio27_net, gpio28_net, gpio29_net):
    global vbus, vcc_3v3, gnd, dvdd, vreg_out
    """RP2040 dual-core MCU with power, crystal, and GPIO connections."""
    mcu = Part("MCU_RaspberryPi", "RP2040",
               footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm")

    # Power: all IOVDD pins
    mcu["IOVDD"] += vcc_net

    # DVDD pins - core digital power (from internal regulator via filter)
    mcu["DVDD"] += dvdd_net

    # Internal voltage regulator
    mcu["VREG_IN"] += vcc_net
    mcu["VREG_VOUT"] += vreg_out_net

    # Filter between VREG_VOUT and DVDD
    r_dvdd = Part("Device", "R", value="1R",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_dvdd[1] += vreg_out_net
    r_dvdd[2] += dvdd_net

    # USB
    mcu["USB_VDD"] += vcc_net
    mcu["USB_DP"] += usb_dp_net
    mcu["USB_DM"] += usb_dm_net

    # ADC reference
    mcu["ADC_AVDD"] += vcc_net

    # Ground (EP)
    mcu["GND"] += gnd_net

    # Crystal 12MHz
    mcu["XIN"] += xin_net
    mcu["XOUT"] += xout_net

    # TESTEN - must be GND
    mcu["TESTEN"] += gnd_net

    # QSPI Flash interface
    mcu["QSPI_SCLK"] += qspi_clk_net
    mcu["QSPI_SS"] += qspi_cs_net
    mcu["QSPI_SD0"] += qspi_sd0_net
    mcu["QSPI_SD1"] += qspi_sd1_net
    mcu["QSPI_SD2"] += qspi_sd2_net
    mcu["QSPI_SD3"] += qspi_sd3_net

    # SWD debug - expose (leave NC for now; could break out)
    mcu["SWCLK"] += NC
    mcu["SWD"] += NC

    # RUN pin
    mcu["RUN"] += run_net

    # GPIO connections
    mcu["GPIO0"] += gpio0_net
    mcu["GPIO1"] += gpio1_net
    mcu["GPIO2"] += gpio2_net
    mcu["GPIO3"] += gpio3_net
    mcu["GPIO4"] += gpio4_net
    mcu["GPIO5"] += gpio5_net
    mcu["GPIO6"] += gpio6_net
    mcu["GPIO7"] += gpio7_net
    mcu["GPIO8"] += gpio8_net
    mcu["GPIO9"] += gpio9_net
    mcu["GPIO10"] += gpio10_net

    # Boot select on GPIO11
    mcu["GPIO11"] += boot_net

    # NeoPixel data on GPIO12
    mcu["GPIO12"] += neopixel_net

    # NeoPixel power switch on GPIO13
    mcu["GPIO13"] += neopixel_pwr_net

    # Not broken out
    mcu["GPIO14"] += NC
    mcu["GPIO15"] += NC

    # I2C (STEMMA QT) on GPIO16/17
    mcu["GPIO16"] += sda_net
    mcu["GPIO17"] += scl_net

    # Not broken out
    mcu["GPIO18"] += NC
    mcu["GPIO19"] += NC

    # Broken out
    mcu["GPIO20"] += gpio20_net
    mcu["GPIO21"] += NC
    mcu["GPIO22"] += NC
    mcu["GPIO23"] += NC
    mcu["GPIO24"] += gpio24_net
    mcu["GPIO25"] += gpio25_net

    # ADC GPIOs
    mcu["GPIO26_ADC0"] += gpio26_net
    mcu["GPIO27_ADC1"] += gpio27_net
    mcu["GPIO28_ADC2"] += gpio28_net
    mcu["GPIO29_ADC3"] += gpio29_net

    # IOVDD decoupling caps (one per power domain pin)
    for _i in range(6):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vcc_net
        c[2] += gnd_net

    # DVDD decoupling
    c_dvdd = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dvdd[1] += dvdd_net
    c_dvdd[2] += gnd_net

    # DVDD bulk cap
    c_dvdd_bulk = Part("Device", "C", value="10uF",
                       footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dvdd_bulk[1] += dvdd_net
    c_dvdd_bulk[2] += gnd_net

    # USB_VDD decoupling
    c_usb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usb[1] += vcc_net
    c_usb[2] += gnd_net

    # ADC_AVDD decoupling
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_adc[1] += vcc_net
    c_adc[2] += gnd_net

    # VREG_VOUT bulk cap
    c_vreg = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vreg[1] += vreg_out_net
    c_vreg[2] += gnd_net

    # 12MHz crystal
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xin_net
    xtal[2] += xout_net

    # Crystal load capacitors (15pF)
    c_x1 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += xin_net
    c_x1[2] += gnd_net

    c_x2 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2[1] += xout_net
    c_x2[2] += gnd_net

    # RUN pin pull-up
    r_run = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_run[1] += vcc_net
    r_run[2] += run_net


rp2040_mcu(
    vcc_3v3, dvdd, gnd, usb_dp, usb_dm,
    xin, xout, vreg_out,
    qspi_clk, qspi_cs, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3,
    boot_n, run_n, neopixel_data, neopixel_pwr,
    sda, scl,
    gpio0, gpio1, gpio2, gpio3, gpio4,
    gpio5, gpio6, gpio7, gpio8, gpio9,
    gpio10, gpio20, gpio24, gpio25,
    gpio26, gpio27, gpio28, gpio29,
)


# ============================================================
# Subcircuit: 8MB QSPI Flash (W25Q128JVS used as W25Q64JV)
# ============================================================
@subcircuit
def qspi_flash(vcc_net, gnd_net, clk_net, cs_net,
               sd0_net, sd1_net, sd2_net, sd3_net):
    global vcc_3v3, gnd, qspi_clk, qspi_cs
    """W25Q128JVS (8MB / SOIC-8) QSPI Flash with decoupling."""
    flash = Part("Memory_Flash", "W25Q128JVS", value="W25Q64JVS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    flash.lcsc = "C97521"

    flash["CLK"] += clk_net
    flash["~{CS}"] += cs_net
    flash["DI/IO_{0}"] += sd0_net
    flash["DO/IO_{1}"] += sd1_net
    flash["~{WP}/IO_{2}"] += sd2_net
    flash["~{HOLD}/~{RESET}/IO_{3}"] += sd3_net
    flash["VCC"] += vcc_net
    flash["GND"] += gnd_net

    # Decoupling cap
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash[1] += vcc_net
    c_flash[2] += gnd_net

qspi_flash(vcc_3v3, gnd, qspi_clk, qspi_cs,
           qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3)


# ============================================================
# Subcircuit: NeoPixel RGB LED (WS2812B-2020)
# ============================================================
@subcircuit
def neopixel_led(data_in_net, vcc_net, gnd_net):
    global vcc_3v3, gnd, neopixel_data
    """WS2812B-2020 NeoPixel with bypass cap."""
    neo = Part("LED", "WS2812B-2020",
               footprint="LED_SMD:LED_WS2812B-2020_PLCC4_2.0x2.0mm")
    neo["VDD"] += vcc_net
    neo["VSS"] += gnd_net
    neo["DIN"] += data_in_net
    neo["DOUT"] += NC

    # Bypass cap close to NeoPixel
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += vcc_net
    c_neo[2] += gnd_net

neopixel_led(neopixel_data, vcc_3v3, gnd)


# ============================================================
# Subcircuit: Boot button
# ============================================================
@subcircuit
def buttons(boot_net, run_net, gnd_net, vcc_net):
    global boot_n, run_n, gnd, vcc_3v3
    """Boot select and reset buttons."""
    # CK KMR2 is a compact SMD tactile switch (3.1x2.5mm), good for tiny boards
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_boot[1] += boot_net
    sw_boot[2] += gnd_net

    # Boot pull-up
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += vcc_net
    r_boot[2] += boot_net

    # Reset button
    sw_reset = Part("Switch", "SW_Push",
                    footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_reset[1] += run_net
    sw_reset[2] += gnd_net

buttons(boot_n, run_n, gnd, vcc_3v3)


# ============================================================
# Subcircuit: STEMMA QT / Qwiic I2C connector (JST SH 4-pin)
# ============================================================
@subcircuit
def stemma_qt(sda_net, scl_net, vcc_net, gnd_net):
    global sda, scl, vcc_3v3, gnd
    """JST SH 4-pin STEMMA QT / Qwiic I2C connector with I2C pull-ups."""
    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    conn.edge_preference = "right"
    # STEMMA QT pinout: GND, VCC, SDA, SCL
    conn["Pin_1"] += gnd_net
    conn["Pin_2"] += vcc_net
    conn["Pin_3"] += sda_net
    conn["Pin_4"] += scl_net

    # I2C pull-up resistors
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc_net
    r_sda[2] += sda_net

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl[1] += vcc_net
    r_scl[2] += scl_net

stemma_qt(sda, scl, vcc_3v3, gnd)


# ============================================================
# Subcircuit: GPIO breakout headers (castellated pads via pin headers)
# ============================================================
@subcircuit
def gpio_header(vcc_net, gnd_net,
                gpio0_net, gpio1_net, gpio2_net, gpio3_net, gpio4_net,
                gpio5_net, gpio6_net, gpio7_net, gpio8_net, gpio9_net,
                gpio10_net, gpio20_net, gpio24_net, gpio25_net,
                gpio26_net, gpio27_net, gpio28_net, gpio29_net):
    global vcc_3v3, gnd
    """Castellated edge headers — QT Py XIAO form factor."""
    # Left side: A0(GPIO26), A1(GPIO27), A2(GPIO28), A3(GPIO29),
    #            SDA(GPIO16→SDA net), SCL(GPIO17→SCL net), GND
    hdr_left = Part("Connector_Generic", "Conn_01x07",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical")
    hdr_left["Pin_1"] += gpio26_net   # A0
    hdr_left["Pin_2"] += gpio27_net   # A1
    hdr_left["Pin_3"] += gpio28_net   # A2
    hdr_left["Pin_4"] += gpio29_net   # A3
    hdr_left["Pin_5"] += gpio24_net   # GPIO24
    hdr_left["Pin_6"] += gpio25_net   # GPIO25
    hdr_left["Pin_7"] += gnd_net

    # Right side: TX(GPIO20), RX(GPIO5→but actually GPIO1 on QT Py), SCK(GPIO6),
    #             MISO(GPIO4), MOSI(GPIO3), 3V3, GND
    hdr_right = Part("Connector_Generic", "Conn_01x07",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical")
    hdr_right["Pin_1"] += gpio20_net  # TX
    hdr_right["Pin_2"] += gpio5_net   # RX
    hdr_right["Pin_3"] += gpio6_net   # SCK
    hdr_right["Pin_4"] += gpio4_net   # MISO
    hdr_right["Pin_5"] += gpio3_net   # MOSI
    hdr_right["Pin_6"] += vcc_net     # 3V3
    hdr_right["Pin_7"] += gnd_net

gpio_header(
    vcc_3v3, gnd,
    gpio0, gpio1, gpio2, gpio3, gpio4,
    gpio5, gpio6, gpio7, gpio8, gpio9,
    gpio10, gpio20, gpio24, gpio25,
    gpio26, gpio27, gpio28, gpio29,
)

# ============================================================
# EDA_FLOORPLAN: guide placement for this ultra-compact board
# Board: 21mm wide x 17.5mm tall (XIAO/QT Py form factor)
# J3 (left header) and J4 (right header) anchor to top/bottom edges
# USB-C (J1) anchors to top center
# STEMMA QT (J2) anchors to right edge
# ============================================================
EDA_FLOORPLAN = {
    "outline": {"width_mm": 21.0, "height_mm": 17.5},
    "edge_anchors": [
        # Left GPIO header along the left (top) edge
        {"ref": "J3", "edge": "top", "offset_mm": 10.5},
        # Right GPIO header along the right (bottom) edge
        {"ref": "J4", "edge": "bottom", "offset_mm": 10.5},
        # USB-C connector on the left edge (facing out)
        {"ref": "J1", "edge": "left", "offset_mm": 8.75},
        # STEMMA QT on the right edge
        {"ref": "J2", "edge": "right", "offset_mm": 8.75},
    ],
}
