"""
QT Py RP2040 Dual-Core Board
Tiny RP2040 board with STEMMA QT I2C connector. Dual-core 125MHz processor,
264KB RAM, 8MB SPI Flash. RGB NeoPixel, boot and reset buttons.
Same form-factor as SAMD21 QT Py. CircuitPython and MicroPython support.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

import sys
sys.path.insert(0, "/home/lachlan/Projects/skidl/src")

from skidl import *
set_default_tool(KICAD9)

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
xin = Net("XIN")
xout = Net("XOUT")

# QSPI Flash nets
qspi_clk = Net("QSPI_SCLK")
qspi_cs = Net("QSPI_CS")
qspi_sd0 = Net("QSPI_SD0")
qspi_sd1 = Net("QSPI_SD1")
qspi_sd2 = Net("QSPI_SD2")
qspi_sd3 = Net("QSPI_SD3")

# GPIO breakout nets
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

# Boot/reset control nets
boot_n = Net("BOOT_N")
run_n = Net("RUN_N")

# ============================================================
# Subcircuit: USB-C connector with CC resistors
# ============================================================
@subcircuit
def usb_input(vbus_net, dp_net, dm_net, gnd_net):
    """USB-C connector with CC pull-down resistors for UFP (device) mode."""
    usb_conn = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
                    footprint="Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal")

    # GND and shield pins
    usb_conn["A1"] += gnd_net
    usb_conn["B1"] += gnd_net
    usb_conn["A12"] += gnd_net
    usb_conn["B12"] += gnd_net
    usb_conn["S1"] += gnd_net

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
    usb_conn["A5"] += r_cc1[1]
    r_cc1[2] += gnd_net
    usb_conn["B5"] += r_cc2[1]
    r_cc2[2] += gnd_net

    # SBU pins not connected
    usb_conn["A8"] += NC
    usb_conn["B8"] += NC

usb_input(vbus, usb_dp, usb_dm, gnd)

# ============================================================
# Subcircuit: 3.3V voltage regulator (AP2112K-3.3)
# ============================================================
@subcircuit
def power_regulation(vin_net, vout_net, gnd_net):
    """AP2112K-3.3 LDO regulator with input/output decoupling."""
    reg = Part("Regulator_Linear", "AP2112K-3.3", value="AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"] += vin_net
    reg["GND"] += gnd_net
    reg["EN"] += vin_net  # Always enabled
    reg["VOUT"] += vout_net

    # Input decoupling
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vin_net
    c_in[2] += gnd_net

    # Output decoupling
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
# Subcircuit: RP2040 MCU with decoupling and crystal
# ============================================================
@subcircuit
def rp2040_mcu(vcc_net, dvdd_net, gnd_net, usb_dp_net, usb_dm_net,
               xin_net, xout_net, vreg_out_net,
               qspi_clk_net, qspi_cs_net,
               qspi_sd0_net, qspi_sd1_net, qspi_sd2_net, qspi_sd3_net,
               gpio_nets, boot_net, run_net, neopixel_net,
               sda_net, scl_net):
    """RP2040 dual-core MCU with power, crystal, and GPIO connections."""
    mcu = Part("MCU_RaspberryPi", "RP2040",
               footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm")

    # Power connections - IOVDD pins
    mcu["IOVDD"] += vcc_net

    # DVDD pins - core digital power (from internal regulator)
    mcu["DVDD"] += dvdd_net

    # Internal voltage regulator
    mcu["VREG_VIN"] += vcc_net
    mcu["VREG_VOUT"] += vreg_out_net

    # Connect VREG_VOUT to DVDD via small resistor (LC filter simplified)
    r_dvdd = Part("Device", "R", value="1R",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_dvdd[1] += vreg_out_net
    r_dvdd[2] += dvdd_net

    # USB power
    mcu["USB_VDD"] += vcc_net
    mcu["USB_DP"] += usb_dp_net
    mcu["USB_DM"] += usb_dm_net

    # ADC reference
    mcu["ADC_AVDD"] += vcc_net

    # Ground
    mcu["GND"] += gnd_net

    # Crystal (12MHz for RP2040)
    mcu["XIN"] += xin_net
    mcu["XOUT"] += xout_net

    # TESTEN pin - must be grounded
    mcu["TESTEN"] += gnd_net

    # QSPI Flash interface
    mcu["QSPI_SCLK"] += qspi_clk_net
    mcu["~{QSPI_SS}"] += qspi_cs_net
    mcu["QSPI_SD0"] += qspi_sd0_net
    mcu["QSPI_SD1"] += qspi_sd1_net
    mcu["QSPI_SD2"] += qspi_sd2_net
    mcu["QSPI_SD3"] += qspi_sd3_net

    # SWD debug - NC
    mcu["SWCLK"] += NC
    mcu["SWDIO"] += NC

    # RUN pin (active-high reset with pull-up)
    mcu["RUN"] += run_net

    # GPIO assignments matching QT Py RP2040 pinout
    mcu["GPIO0"] += gpio_nets["gpio0"]
    mcu["GPIO1"] += gpio_nets["gpio1"]
    mcu["GPIO2"] += gpio_nets["gpio2"]
    mcu["GPIO3"] += gpio_nets["gpio3"]
    mcu["GPIO4"] += gpio_nets["gpio4"]
    mcu["GPIO5"] += gpio_nets["gpio5"]
    mcu["GPIO6"] += gpio_nets["gpio6"]
    mcu["GPIO7"] += gpio_nets["gpio7"]
    mcu["GPIO8"] += gpio_nets["gpio8"]
    mcu["GPIO9"] += gpio_nets["gpio9"]
    mcu["GPIO10"] += gpio_nets["gpio10"]

    # Boot select button on GPIO11
    mcu["GPIO11"] += boot_net

    # NeoPixel on GPIO12
    mcu["GPIO12"] += neopixel_net

    # NeoPixel power control on GPIO13
    neo_pwr_net = Net("NEOPIXEL_PWR")
    mcu["GPIO13"] += neo_pwr_net

    # GPIO14/15 - not broken out
    mcu["GPIO14"] += NC
    mcu["GPIO15"] += NC

    # I2C on GPIO16/17 (STEMMA QT)
    mcu["GPIO16"] += sda_net
    mcu["GPIO17"] += scl_net

    # GPIO18/19 - not broken out
    mcu["GPIO18"] += NC
    mcu["GPIO19"] += NC

    # GPIO20 - breakout
    mcu["GPIO20"] += gpio_nets["gpio20"]

    # GPIO21-23 not broken out
    mcu["GPIO21"] += NC
    mcu["GPIO22"] += NC
    mcu["GPIO23"] += NC

    # GPIO24/25 - breakout
    mcu["GPIO24"] += gpio_nets["gpio24"]
    mcu["GPIO25"] += gpio_nets["gpio25"]

    # GPIO26-29 (ADC capable) - breakout
    mcu["GPIO26/ADC0"] += gpio_nets["gpio26"]
    mcu["GPIO27/ADC1"] += gpio_nets["gpio27"]
    mcu["GPIO28/ADC2"] += gpio_nets["gpio28"]
    mcu["GPIO29/ADC3"] += gpio_nets["gpio29"]

    # ---- IOVDD decoupling caps ----
    for _i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vcc_net
        c[2] += gnd_net

    # DVDD decoupling
    c_dvdd1 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dvdd1[1] += dvdd_net
    c_dvdd1[2] += gnd_net

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

    # VREG_VOUT bulk cap (1uF)
    c_vreg = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vreg[1] += vreg_out_net
    c_vreg[2] += gnd_net

    # 12MHz crystal
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xin_net
    xtal[2] += xout_net

    # Crystal load capacitors (15pF typical for RP2040)
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

gpio_map = {
    "gpio0": gpio0, "gpio1": gpio1, "gpio2": gpio2, "gpio3": gpio3,
    "gpio4": gpio4, "gpio5": gpio5, "gpio6": gpio6, "gpio7": gpio7,
    "gpio8": gpio8, "gpio9": gpio9, "gpio10": gpio10,
    "gpio20": gpio20, "gpio24": gpio24, "gpio25": gpio25,
    "gpio26": gpio26, "gpio27": gpio27, "gpio28": gpio28, "gpio29": gpio29,
}

rp2040_mcu(vcc_3v3, dvdd, gnd, usb_dp, usb_dm,
           xin, xout, vreg_out,
           qspi_clk, qspi_cs,
           qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3,
           gpio_map, boot_n, run_n, neopixel_data,
           sda, scl)

# ============================================================
# Subcircuit: 8MB QSPI Flash (W25Q64JVS)
# ============================================================
@subcircuit
def qspi_flash(vcc_net, gnd_net, clk_net, cs_net,
               sd0_net, sd1_net, sd2_net, sd3_net):
    """W25Q64JVSSIQ 8MB QSPI Flash with decoupling."""
    flash = Part("Memory_Flash", "W25Q128JVS", value="W25Q64JVS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")

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
def neopixel(data_in_net, vcc_net, gnd_net):
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

neopixel(neopixel_data, vcc_3v3, gnd)

# ============================================================
# Subcircuit: Buttons (Boot and Reset)
# ============================================================
@subcircuit
def buttons(boot_net, run_net, gnd_net, vcc_net):
    """Boot select and reset buttons with pull-ups."""
    # Boot button (active low, GPIO11)
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A")
    sw_boot[1] += boot_net
    sw_boot[2] += gnd_net

    # Boot pull-up
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += vcc_net
    r_boot[2] += boot_net

    # Reset button (active low, RUN pin)
    sw_reset = Part("Switch", "SW_Push",
                    footprint="Button_Switch_SMD:SW_Push_1P1T_XKB_TS-1187A")
    sw_reset[1] += run_net
    sw_reset[2] += gnd_net

buttons(boot_n, run_n, gnd, vcc_3v3)

# ============================================================
# Subcircuit: STEMMA QT / Qwiic I2C connector
# ============================================================
@subcircuit
def stemma_qt(sda_net, scl_net, vcc_net, gnd_net):
    """JST SH 4-pin STEMMA QT / Qwiic I2C connector with pull-ups."""
    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
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
# Subcircuit: GPIO breakout headers (castellated pads)
# ============================================================
@subcircuit
def gpio_header(vcc_net, gnd_net, gpio_nets_map):
    """Castellated edge headers matching QT Py form factor."""
    # Left side header: A0-A3, SDA1, SCL1, GND
    hdr_left = Part("Connector_Generic", "Conn_01x07",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical")
    hdr_left["Pin_1"] += gpio_nets_map["gpio26"]   # A0
    hdr_left["Pin_2"] += gpio_nets_map["gpio27"]   # A1
    hdr_left["Pin_3"] += gpio_nets_map["gpio28"]   # A2
    hdr_left["Pin_4"] += gpio_nets_map["gpio29"]   # A3
    hdr_left["Pin_5"] += gpio_nets_map["gpio24"]   # SDA1/GPIO24
    hdr_left["Pin_6"] += gpio_nets_map["gpio25"]   # SCL1/GPIO25
    hdr_left["Pin_7"] += gnd_net

    # Right side header: TX, RX, SCK, MISO, MOSI, 3V3, GND
    hdr_right = Part("Connector_Generic", "Conn_01x07",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical")
    hdr_right["Pin_1"] += gpio_nets_map["gpio20"]  # TX/GPIO20
    hdr_right["Pin_2"] += gpio_nets_map["gpio5"]   # RX/GPIO5
    hdr_right["Pin_3"] += gpio_nets_map["gpio6"]   # SCK/GPIO6
    hdr_right["Pin_4"] += gpio_nets_map["gpio4"]   # MISO/GPIO4
    hdr_right["Pin_5"] += gpio_nets_map["gpio3"]   # MOSI/GPIO3
    hdr_right["Pin_6"] += vcc_net                   # 3V3
    hdr_right["Pin_7"] += gnd_net

gpio_header(vcc_3v3, gnd, gpio_map)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)

print("QT Py RP2040 circuit generated successfully!")
print(f"Parts: {len(default_circuit.parts)}")
print(f"Nets: {len(default_circuit.nets)}")
