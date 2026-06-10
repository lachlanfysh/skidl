"""
Feather RP2350 Dual-Core -- SKiDL Circuit
RP2350A dual-core (ARM Cortex-M33 / RISC-V), 8MB QSPI Flash,
STEMMA QT, NeoPixel, USB-C, battery charger, Feather form factor.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ---------------------------------------------------------------
# Global power nets
# ---------------------------------------------------------------
vcc = Net("+3V3"); vcc.drive = POWER
vbus = Net("VBUS"); vbus.drive = POWER
vbat = Net("VBAT"); vbat.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ---------------------------------------------------------------
# USB-C input with CC resistors
# ---------------------------------------------------------------
@subcircuit
def usb_input(vbus, gnd, usb_dp, usb_dm):
    """USB-C connector with CC resistors for UFP."""
    usb_conn = Part(name="USB_C_16P", tool=SKIDL, dest=NETLIST,
                    footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
                    pins=[
                        Pin(num="A1", name="GND1", func=Pin.types.PWRIN),
                        Pin(num="A4", name="VBUS1", func=Pin.types.PASSIVE),
                        Pin(num="A5", name="CC1", func=Pin.types.BIDIR),
                        Pin(num="A6", name="DP1", func=Pin.types.BIDIR),
                        Pin(num="A7", name="DM1", func=Pin.types.BIDIR),
                        Pin(num="A8", name="SBU1", func=Pin.types.BIDIR),
                        Pin(num="A9", name="VBUS2", func=Pin.types.PASSIVE),
                        Pin(num="A12", name="GND2", func=Pin.types.PASSIVE),
                        Pin(num="B1", name="GND3", func=Pin.types.PASSIVE),
                        Pin(num="B4", name="VBUS3", func=Pin.types.PASSIVE),
                        Pin(num="B5", name="CC2", func=Pin.types.BIDIR),
                        Pin(num="B6", name="DP2", func=Pin.types.BIDIR),
                        Pin(num="B7", name="DM2", func=Pin.types.BIDIR),
                        Pin(num="B8", name="SBU2", func=Pin.types.BIDIR),
                        Pin(num="B9", name="VBUS4", func=Pin.types.PASSIVE),
                        Pin(num="B12", name="GND4", func=Pin.types.PASSIVE),
                        Pin(num="S1", name="SHIELD", func=Pin.types.PASSIVE),
                    ])
    usb_conn["VBUS1"] += vbus
    usb_conn["VBUS2"] += vbus
    usb_conn["VBUS3"] += vbus
    usb_conn["VBUS4"] += vbus
    usb_conn["GND1"] += gnd
    usb_conn["GND2"] += gnd
    usb_conn["GND3"] += gnd
    usb_conn["GND4"] += gnd
    usb_conn["DP1"] += usb_dp
    usb_conn["DP2"] += usb_dp
    usb_conn["DM1"] += usb_dm
    usb_conn["DM2"] += usb_dm
    usb_conn["SHIELD"] += gnd

    # SBU pins not connected in USB 2.0 mode -- leave NC
    # (they're BIDIR, won't cause ERC errors on multi-pin nets)

    # 5.1k CC pull-downs for UFP detection
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    usb_conn["CC1"] += r_cc1[1]
    r_cc1[2] += gnd
    usb_conn["CC2"] += r_cc2[1]
    r_cc2[2] += gnd

# ---------------------------------------------------------------
# Power supply: 3.3V regulator from VBUS/VBAT
# ---------------------------------------------------------------
@subcircuit
def power_supply(vin, vout, gnd):
    """AP2112K-3.3 LDO regulator (SOT-23-5)."""
    reg = Part(name="AP2112K-3.3", tool=SKIDL, dest=NETLIST,
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               pins=[
                   Pin(num="1", name="VIN", func=Pin.types.PWRIN),
                   Pin(num="2", name="GND", func=Pin.types.PWRIN),
                   Pin(num="3", name="EN", func=Pin.types.INPUT),
                   Pin(num="4", name="NC", func=Pin.types.NOCONNECT),
                   Pin(num="5", name="VOUT", func=Pin.types.PWROUT),
               ])
    reg["VIN"] += vin
    reg["EN"] += vin   # Regulator always enabled
    reg["GND"] += gnd
    reg["VOUT"] += vout

    # Decoupling on input
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vin; c_in[2] += gnd

    # Output decoupling
    c_out1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out1[1] += vout; c_out1[2] += gnd
    c_out2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out2[1] += vout; c_out2[2] += gnd

# ---------------------------------------------------------------
# Battery charger: MCP73831
# ---------------------------------------------------------------
@subcircuit
def battery_charger(vbus, vbat, gnd):
    """MCP73831 single-cell LiPo charger."""
    chg = Part(name="MCP73831", tool=SKIDL, dest=NETLIST,
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               pins=[
                   Pin(num="1", name="STAT", func=Pin.types.OUTPUT),
                   Pin(num="2", name="VSS", func=Pin.types.PWRIN),
                   Pin(num="3", name="VBAT", func=Pin.types.PWROUT),
                   Pin(num="4", name="VDD", func=Pin.types.PWRIN),
                   Pin(num="5", name="PROG", func=Pin.types.INPUT),
               ])
    chg["VDD"] += vbus
    chg["VSS"] += gnd
    chg["VBAT"] += vbat

    # Charge current setting: 500mA => R = 1000/I = 2K
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    chg["PROG"] += r_prog[1]
    r_prog[2] += gnd

    # Charge status LED (active-low)
    stat_net = Net("CHG_STAT")
    chg["STAT"] += stat_net
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0603_1608Metric")
    stat_net += r_led[1]
    r_led[2] += led_chg[1]
    led_chg[2] += gnd

    # Decoupling
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vbus[1] += vbus; c_vbus[2] += gnd
    c_bat = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bat[1] += vbat; c_bat[2] += gnd

    # Battery connector (JST-PH 2-pin)
    bat_conn = Part("Connector_Generic", "Conn_01x02",
                    footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    bat_conn[1] += vbat
    bat_conn[2] += gnd

# ---------------------------------------------------------------
# RP2350A MCU block
# ---------------------------------------------------------------
@subcircuit
def mcu_block(vcc, gnd, vbus, gpio_nets, usb_dp, usb_dm,
              qspi_clk, qspi_d0, qspi_d1, qspi_d2, qspi_d3, qspi_cs,
              sda, scl, neopixel_out, xin_net, xout_net):
    """RP2350A MCU with decoupling and buck converter."""
    mcu = Part("MCU_RaspberryPi", "RP2350A",
               footprint="Package_DFN_QFN:QFN-60-1EP_7x7mm_P0.4mm_EP3.4x3.4mm")

    # Power connections
    mcu["IOVDD"] += vcc
    mcu["DVDD"] += vcc
    mcu["ADC_AVDD"] += vcc
    mcu["USB_OTP_VDD"] += vcc
    mcu["QSPI_IOVDD"] += vcc
    mcu["GND"] += gnd

    # Internal buck regulator connections
    mcu["VREG_VIN"] += vcc
    mcu["VREG_AVDD"] += vcc
    mcu["VREG_PGND"] += gnd

    # VREG_FB -- feedback for internal buck
    vreg_fb_net = Net("VREG_FB")
    mcu["VREG_FB"] += vreg_fb_net

    # VREG_LX -- inductor output
    vreg_lx_net = Net("VREG_LX")
    mcu["VREG_LX"] += vreg_lx_net

    # Buck inductor: LX to DVDD core via 3.3uH
    l_buck = Part("Device", "L", value="3.3uH",
                  footprint="Resistor_SMD:R_0805_2012Metric")
    l_buck[1] += vreg_lx_net
    l_buck[2] += vcc

    # Feedback divider
    r_fb1 = Part("Device", "R", value="100K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_fb2 = Part("Device", "R", value="220K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_fb1[1] += vcc
    r_fb1[2] += vreg_fb_net
    r_fb2[1] += vreg_fb_net
    r_fb2[2] += gnd

    # USB
    mcu["USB_DP"] += usb_dp
    mcu["USB_DM"] += usb_dm

    # QSPI Flash interface
    mcu["QSPI_SCLK"] += qspi_clk
    mcu["QSPI_SD0"] += qspi_d0
    mcu["QSPI_SD1"] += qspi_d1
    mcu["QSPI_SD2"] += qspi_d2
    mcu["QSPI_SD3"] += qspi_d3
    mcu["~{QSPI_SS}"] += qspi_cs

    # 12MHz crystal
    mcu["XIN"] += xin_net
    mcu["XOUT"] += xout_net

    # SWD debug
    swd_clk = Net("SWCLK")
    swd_io = Net("SWDIO")
    mcu["SWCLK"] += swd_clk
    mcu["SWDIO"] += swd_io

    # RUN pin with pull-up (connected externally to reset button)
    run_net = Net("RUN")
    mcu["RUN"] += run_net
    r_run = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_run[1] += vcc
    r_run[2] += run_net

    # GPIO assignments
    mcu["GPIO0"] += gpio_nets[0]
    mcu["GPIO1"] += gpio_nets[1]
    mcu["GPIO2"] += sda        # I2C SDA
    mcu["GPIO3"] += scl        # I2C SCL
    mcu["GPIO4"] += gpio_nets[4]
    mcu["GPIO5"] += gpio_nets[5]
    mcu["GPIO6"] += gpio_nets[6]
    mcu["GPIO7"] += gpio_nets[7]
    mcu["GPIO8"] += gpio_nets[8]
    mcu["GPIO9"] += gpio_nets[9]
    mcu["GPIO10"] += gpio_nets[10]
    mcu["GPIO11"] += gpio_nets[11]
    mcu["GPIO12"] += gpio_nets[12]
    mcu["GPIO13"] += gpio_nets[13]
    mcu["GPIO14"] += gpio_nets[14]
    mcu["GPIO15"] += gpio_nets[15]
    mcu["GPIO16"] += neopixel_out
    mcu["GPIO17"] += gpio_nets[17]
    mcu["GPIO18"] += gpio_nets[18]
    mcu["GPIO19"] += gpio_nets[19]
    mcu["GPIO20"] += gpio_nets[20]
    mcu["GPIO21"] += gpio_nets[21]
    mcu["GPIO22"] += gpio_nets[22]
    mcu["GPIO23"] += gpio_nets[23]
    mcu["GPIO24"] += gpio_nets[24]
    mcu["GPIO25"] += gpio_nets[25]
    mcu["GPIO26/ADC0"] += gpio_nets[26]
    mcu["GPIO27/ADC1"] += gpio_nets[27]
    mcu["GPIO28/ADC2"] += gpio_nets[28]
    mcu["GPIO29/ADC3"] += gpio_nets[29]

    # Decoupling caps for MCU power pins
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vcc; c[2] += gnd

    # Bulk cap
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vcc; c_bulk[2] += gnd

# ---------------------------------------------------------------
# Crystal oscillator 12 MHz
# ---------------------------------------------------------------
@subcircuit
def crystal_osc(xin_net, xout_net, gnd):
    """12 MHz crystal for RP2350."""
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xin_net
    xtal[2] += xout_net

    # Load capacitors
    c_x1 = Part("Device", "C", value="20pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2 = Part("Device", "C", value="20pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += xin_net;  c_x1[2] += gnd
    c_x2[1] += xout_net; c_x2[2] += gnd

# ---------------------------------------------------------------
# QSPI Flash (8MB / 64Mbit) -- W25Q64JVSS or GD25Q64
# ---------------------------------------------------------------
@subcircuit
def spi_flash(vcc, gnd, qspi_clk, qspi_d0, qspi_d1, qspi_d2, qspi_d3, qspi_cs):
    """8MB QSPI NOR flash (SOIC-8)."""
    flash = Part(name="W25Q64JV", tool=SKIDL, dest=NETLIST,
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 pins=[
                     Pin(num="1", name="CS", func=Pin.types.INPUT),
                     Pin(num="2", name="DO", func=Pin.types.OUTPUT),
                     Pin(num="3", name="WP", func=Pin.types.INPUT),
                     Pin(num="4", name="GND", func=Pin.types.PWRIN),
                     Pin(num="5", name="DI", func=Pin.types.INPUT),
                     Pin(num="6", name="CLK", func=Pin.types.INPUT),
                     Pin(num="7", name="HOLD", func=Pin.types.INPUT),
                     Pin(num="8", name="VCC", func=Pin.types.PWRIN),
                 ])
    flash["VCC"] += vcc
    flash["GND"] += gnd
    flash["CS"] += qspi_cs
    flash["CLK"] += qspi_clk
    flash["DI"] += qspi_d0    # SD0
    flash["DO"] += qspi_d1    # SD1
    flash["WP"] += qspi_d2    # SD2
    flash["HOLD"] += qspi_d3  # SD3

    # Decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_flash[1] += vcc; c_flash[2] += gnd

# ---------------------------------------------------------------
# NeoPixel (WS2812B)
# ---------------------------------------------------------------
@subcircuit
def neopixel_block(vcc, gnd, data_in):
    """Single WS2812B NeoPixel."""
    neo = Part(name="WS2812B", tool=SKIDL, dest=NETLIST,
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
               pins=[
                   Pin(num="1", name="VDD", func=Pin.types.PWRIN),
                   Pin(num="2", name="DOUT", func=Pin.types.OUTPUT),
                   Pin(num="3", name="VSS", func=Pin.types.PWRIN),
                   Pin(num="4", name="DIN", func=Pin.types.INPUT),
               ])
    neo["VDD"] += vcc
    neo["VSS"] += gnd
    neo["DIN"] += data_in
    # DOUT unconnected (single pixel, end of chain)

    # Decoupling
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_neo[1] += vcc; c_neo[2] += gnd

# ---------------------------------------------------------------
# STEMMA QT / Qwiic connector (JST SH 4-pin)
# ---------------------------------------------------------------
@subcircuit
def stemma_qt(vcc, gnd, sda, scl):
    """STEMMA QT / Qwiic I2C connector."""
    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    conn[1] += gnd
    conn[2] += vcc
    conn[3] += sda
    conn[4] += scl

    # I2C pull-ups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc; r_sda[2] += sda
    r_scl[1] += vcc; r_scl[2] += scl

# ---------------------------------------------------------------
# Reset and Boot buttons
# ---------------------------------------------------------------
@subcircuit
def reset_button(gnd, run_net):
    """Reset button pulls RUN low."""
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_SPST_PTS810")
    sw_rst[1] += run_net
    sw_rst[2] += gnd

@subcircuit
def boot_button(vcc, gnd, boot_net):
    """Boot/DFU button with pull-up."""
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_SPST_PTS810")
    sw_boot[1] += boot_net
    sw_boot[2] += gnd

    # Boot pin pull-up
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += vcc
    r_boot[2] += boot_net

# ---------------------------------------------------------------
# Feather edge headers (1x16 + 1x12)
# ---------------------------------------------------------------
@subcircuit
def edge_headers(vcc, gnd, vbus, vbat, gpio_nets, sda, scl):
    """Feather-standard edge headers."""
    # Left header: 1x16 (power + digital)
    hdr_l = Part("Connector_Generic", "Conn_01x16",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    hdr_l[1] += vbat
    hdr_l[2] += vcc       # EN (tied to 3V3 for simplicity)
    hdr_l[3] += vbus
    hdr_l[4] += gpio_nets[14]   # D13
    hdr_l[5] += gpio_nets[13]   # D12
    hdr_l[6] += gpio_nets[12]   # D11
    hdr_l[7] += gpio_nets[11]   # D10
    hdr_l[8] += gpio_nets[10]   # D9
    hdr_l[9] += gpio_nets[9]    # D6
    hdr_l[10] += gpio_nets[8]   # D5
    hdr_l[11] += gpio_nets[24]  # SCK
    hdr_l[12] += gpio_nets[23]  # MOSI
    hdr_l[13] += gpio_nets[22]  # MISO
    hdr_l[14] += gpio_nets[1]   # RX
    hdr_l[15] += gpio_nets[0]   # TX
    hdr_l[16] += gnd            # GND

    # Right header: 1x12 (analog + I2C + power)
    hdr_r = Part("Connector_Generic", "Conn_01x12",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    hdr_r[1] += vcc            # 3V3
    hdr_r[2] += gpio_nets[29]  # AREF / A5
    hdr_r[3] += gnd            # GND
    hdr_r[4] += gpio_nets[26]  # A0
    hdr_r[5] += gpio_nets[27]  # A1
    hdr_r[6] += gpio_nets[28]  # A2
    hdr_r[7] += gpio_nets[29]  # A3
    hdr_r[8] += gpio_nets[25]  # A4
    hdr_r[9] += gpio_nets[21]  # A5
    hdr_r[10] += scl           # SCL
    hdr_r[11] += sda           # SDA
    hdr_r[12] += gpio_nets[4]  # D4

# ---------------------------------------------------------------
# User LED on GPIO25
# ---------------------------------------------------------------
@subcircuit
def user_led(gnd, gpio_net):
    """Simple user LED on a GPIO pin."""
    r_led = Part("Device", "R", value="330R",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led = Part("Device", "LED", value="RED",
               footprint="LED_SMD:LED_0603_1608Metric")
    gpio_net += r_led[1]
    r_led[2] += led[1]
    led[2] += gnd

# ---------------------------------------------------------------
# Build the circuit
# ---------------------------------------------------------------

# Signal nets
usb_dp = Net("USB_DP")
usb_dm = Net("USB_DM")

# QSPI bus
qspi_clk = Net("QSPI_CLK")
qspi_d0 = Net("QSPI_D0")
qspi_d1 = Net("QSPI_D1")
qspi_d2 = Net("QSPI_D2")
qspi_d3 = Net("QSPI_D3")
qspi_cs = Net("QSPI_CS")

# I2C
sda = Net("SDA")
scl = Net("SCL")

# NeoPixel data
neopixel_data = Net("NEOPIXEL")

# Crystal
xin_net = Net("XIN")
xout_net = Net("XOUT")

# GPIO nets array (0-29)
gpio_nets = [Net(f"GPIO{i}") for i in range(30)]

# RUN and BOOT
run_net = Net("RUN")
boot_net = Net("BOOTSEL")

# ---------------------------------------------------------------
# Instantiate subcircuits
# ---------------------------------------------------------------
usb_input(vbus, gnd, usb_dp, usb_dm)
power_supply(vbus, vcc, gnd)
battery_charger(vbus, vbat, gnd)
mcu_block(vcc, gnd, vbus, gpio_nets, usb_dp, usb_dm,
          qspi_clk, qspi_d0, qspi_d1, qspi_d2, qspi_d3, qspi_cs,
          sda, scl, neopixel_data, xin_net, xout_net)
crystal_osc(xin_net, xout_net, gnd)
spi_flash(vcc, gnd, qspi_clk, qspi_d0, qspi_d1, qspi_d2, qspi_d3, qspi_cs)
neopixel_block(vcc, gnd, neopixel_data)
stemma_qt(vcc, gnd, sda, scl)
reset_button(gnd, run_net)
boot_button(vcc, gnd, boot_net)
edge_headers(vcc, gnd, vbus, vbat, gpio_nets, sda, scl)
user_led(gnd, gpio_nets[25])  # LED on GPIO25

# ---------------------------------------------------------------
# Generate schematic
# ---------------------------------------------------------------
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
