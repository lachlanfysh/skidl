"""
Adafruit Feather M0 Basic Proto
Feather form factor (50.8 x 22.86mm) with ATSAMD21G18 ARM Cortex-M0+ MCU.
Native USB-C, 256KB flash, 32KB SRAM. AP2112K-3.3 LDO + MCP73831 LiPo charger.
32.768kHz crystal for RTC. Power LED + user LED (D13). Reset button.
20 GPIO pins on standard Feather headers.

Note: ATSAMD21G15A-A used as symbol proxy for G18A — identical TQFP-48 pinout.
The G18 is not present in the KiCad MCU_Microchip_SAMD library; the G15A
shares the same package and pin assignment.
"""

from skidl import *

# ── Power rails ──────────────────────────────────────────────────────────────
vbus   = Net("VBUS");   vbus.drive   = POWER
vbat   = Net("VBAT");   vbat.drive   = POWER
v3v3   = Net("3V3");    v3v3.drive   = POWER
gnd    = Net("GND");    gnd.drive    = POWER

# ── Signal nets ──────────────────────────────────────────────────────────────
usb_dp    = Net("USB_DP")
usb_dm    = Net("USB_DM")
cc1       = Net("CC1")
cc2       = Net("CC2")
xin32     = Net("XIN32")
xout32    = Net("XOUT32")
mcu_reset = Net("NRST")
swclk     = Net("SWCLK")
swdio     = Net("SWDIO")
sda       = Net("SDA")
scl       = Net("SCL")
mosi      = Net("MOSI")
miso      = Net("MISO")
sck       = Net("SCK")
uart_tx   = Net("UART_TX")
uart_rx   = Net("UART_RX")
bat_div   = Net("BAT_DIV")

# GPIO/header nets
a0  = Net("A0");  a1 = Net("A1");  a2 = Net("A2")
a3  = Net("A3");  a4 = Net("A4");  a5 = Net("A5")
d5  = Net("D5");  d6 = Net("D6");  d9  = Net("D9")
d10 = Net("D10"); d11 = Net("D11"); d12 = Net("D12")
d13 = Net("D13")


@subcircuit
def usb_power_input(vbus, gnd, usb_dp, usb_dm, cc1, cc2):
    """USB-C receptacle (USB2.0 16P) with CC pull-downs and VBUS decoupling."""
    j = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
             footprint="Connector_USB:USB_C_Receptacle_GCT_USB4085")
    j.ref = "J_USB"
    j.edge_preference = "top"

    # VBUS on all four VBUS pins
    vbus += j["A4"], j["A9"], j["B4"], j["B9"]
    # GND on all GND/SHIELD pins
    gnd  += j["A1"], j["A12"], j["B1"], j["B12"], j["SHIELD"]
    # USB 2.0 data
    usb_dp += j["A6"], j["B6"]
    usb_dm += j["A7"], j["B7"]
    # CC1/CC2
    cc1 += j["CC1"]
    cc2 += j["CC2"]
    # SBU unused
    gnd += j["SBU1"], j["SBU2"]

    # 5.1k CC pull-downs for USB-C power sink
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    cc1 += r_cc1[1]; gnd += r_cc1[2]
    cc2 += r_cc2[1]; gnd += r_cc2[2]

    # VBUS bulk decoupling
    c_bulk = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    vbus += c_bulk[1]; gnd += c_bulk[2]

    c_byp = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    vbus += c_byp[1]; gnd += c_byp[2]


@subcircuit
def lipo_charger(vbus, vbat, gnd):
    """MCP73831 single-cell LiPo charger. Charge current ~100mA (Rprog=10k)."""
    u = Part("Battery_Management", "MCP73831-2-OT",
             footprint="Package_TO_SOT_SMD:SOT-23-5")
    u.ref = "U_CHG"

    vbus += u["V_{DD}"]
    vbat += u["V_{BAT}"]
    gnd  += u["V_{SS}"]

    # Charge current programming: Icharge = 1000 / Rprog_kohm mA → 10k = 100mA
    r_prog = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    u["PROG"] += r_prog[1]; gnd += r_prog[2]

    # Charge-status LED (open-drain STAT, active-low)
    r_stat = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    led_chg = Part("Device", "LED", value="ORANGE",
                   footprint="LED_SMD:LED_0402_1005Metric")
    vbus += r_stat[1]
    u["STAT"] += r_stat[2], led_chg[2]
    gnd += led_chg[1]

    # VBAT bulk cap
    c_bat = Part("Device", "C_Polarized", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    vbat += c_bat[1]; gnd += c_bat[2]


@subcircuit
def power_supply(vbus, vbat, gnd, v3v3):
    """
    Auto power-path: VBUS and VBAT OR'd via Schottky diodes into VREG net.
    AP2112K-3.3 LDO delivers 3.3V. Power LED on 3V3 rail.
    """
    vreg = Net("VREG"); vreg.drive = POWER

    d_usb = Part("Device", "D_Schottky", value="BAT54",
                 footprint="Diode_SMD:D_SOD-323")
    vbus += d_usb["A"]; vreg += d_usb["K"]

    d_bat = Part("Device", "D_Schottky", value="BAT54",
                 footprint="Diode_SMD:D_SOD-323")
    vbat += d_bat["A"]; vreg += d_bat["K"]

    # Input bulk cap
    c_reg_in = Part("Device", "C_Polarized", value="10uF",
                    footprint="Capacitor_SMD:C_0805_2012Metric")
    vreg += c_reg_in[1]; gnd += c_reg_in[2]

    # AP2112K-3.3 LDO
    ldo = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    ldo.ref = "U_LDO"
    vreg  += ldo["VIN"]
    gnd   += ldo["GND"]
    v3v3  += ldo["EN"]   # tie EN to output to stay enabled
    v3v3  += ldo["VOUT"]
    # Pin 4 is NC — leave unconnected

    # Output caps
    c_out_bulk = Part("Device", "C_Polarized", value="10uF",
                      footprint="Capacitor_SMD:C_0805_2012Metric")
    v3v3 += c_out_bulk[1]; gnd += c_out_bulk[2]

    c_out_byp = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0402_1005Metric")
    v3v3 += c_out_byp[1]; gnd += c_out_byp[2]

    # Power LED
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led_pwr = Part("Device", "LED", value="RED",
                   footprint="LED_SMD:LED_0402_1005Metric")
    v3v3 += r_pwr[1]
    r_pwr[2] += led_pwr[2]
    gnd  += led_pwr[1]


@subcircuit
def battery_monitor(vbat, gnd, bat_div):
    """100K/100K voltage divider: VBAT/2 on analog input for battery monitoring."""
    r_top = Part("Device", "R", value="100K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_bot = Part("Device", "R", value="100K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    vbat    += r_top[1]
    bat_div += r_top[2], r_bot[1]
    gnd     += r_bot[2]


@subcircuit
def samd21_mcu(v3v3, gnd, usb_dp, usb_dm, xin32, xout32, mcu_reset,
               swclk, swdio, sda, scl, mosi, miso, sck,
               uart_tx, uart_rx, bat_div,
               a0, a1, a2, a3, a4, a5,
               d5, d6, d9, d10, d11, d12, d13):
    """
    ATSAMD21G15A-A (proxy for G18A, TQFP-48) with full power and signal wiring.
    """
    u = Part("MCU_Microchip_SAMD", "ATSAMD21G15A-A",
             footprint="Package_QFP:TQFP-48_7x7mm_P0.5mm")
    u.ref = "U_MCU"

    # Power
    v3v3 += u["VDDIO"], u["VDDANA"], u["VDDIN"]
    gnd  += u["GND"], u["GNDANA"]

    # VDDCORE: internal 1.2V regulator output — decouple with 1uF
    vddcore = Net("VDDCORE")
    u["VDDCORE"] += vddcore
    c_core = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    vddcore += c_core[1]; gnd += c_core[2]

    # USB D+/D- → PA24/PA25
    usb_dp += u["PA24"]
    usb_dm += u["PA25"]

    # Reset
    mcu_reset += u["~{RESET}"]

    # SWD
    swclk += u["PA30"]
    swdio += u["PA31"]

    # 32.768kHz RTC crystal → PA08(XIN32)/PA09(XOUT32)
    xin32  += u["PA08"]
    xout32 += u["PA09"]

    # I2C (SERCOM3): PA22=SDA, PA23=SCL
    sda += u["PA22"]
    scl += u["PA23"]

    # SPI (SERCOM1): PA11=SCK, PA10=MOSI, PA12=MISO
    sck  += u["PA11"]
    mosi += u["PA10"]
    miso += u["PA12"]

    # UART (SERCOM5): PB22=TX, PB23=RX
    uart_tx += u["PB22"]
    uart_rx += u["PB23"]

    # Analog header: PA02=A0, PB08=A1, PB09=A2, PA04=A3, PA05=A4, PB02=A5
    a0 += u["PA02"]
    a1 += u["PB08"]
    a2 += u["PB09"]
    a3 += u["PA04"]
    a4 += u["PA05"]
    a5 += u["PB02"]

    # Battery divider on PA07
    bat_div += u["PA07"]

    # Digital header
    d5  += u["PA15"]
    d6  += u["PA20"]
    d9  += u["PA06"]    # D9 on Feather M0 = PA06
    d10 += u["PA18"]
    d11 += u["PA16"]
    d12 += u["PA19"]
    d13 += u["PA17"]    # D13 = also onboard LED

    # Unused pins → dedicated NC nets (avoids ERC warnings)
    u["PA00"] += Net("NC_PA00")
    u["PA01"] += Net("NC_PA01")
    u["PA03"] += Net("NC_PA03")
    u["PA13"] += Net("NC_PA13")
    u["PA14"] += Net("NC_PA14")
    u["PA21"] += Net("NC_PA21")
    u["PA27"] += Net("NC_PA27")
    u["PB03"] += Net("NC_PB03")
    u["PB10"] += Net("NC_PB10")
    u["PB11"] += Net("NC_PB11")

    # Decoupling: one 100nF per VDD power domain
    for _ in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        v3v3 += c[1]; gnd += c[2]

    # Bulk cap on digital supply
    c_bulk = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    v3v3 += c_bulk[1]; gnd += c_bulk[2]


@subcircuit
def rtc_crystal(xin32, xout32, gnd):
    """32.768kHz crystal with 15pF load caps for SAMD21 RTC oscillator."""
    y = Part("Device", "Crystal",
             value="32.768kHz",
             footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xin32  += y[1]
    xout32 += y[2]

    c1 = Part("Device", "C", value="15pF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2 = Part("Device", "C", value="15pF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    xin32  += c1[1]; gnd += c1[2]
    xout32 += c2[1]; gnd += c2[2]


@subcircuit
def reset_button(v3v3, gnd, mcu_reset):
    """Reset button (SW_Push) with 10K pull-up and 100nF debounce cap."""
    sw = Part("Switch", "SW_Push",
              footprint="Button_Switch_SMD:SW_SPST_TL3342")
    sw.ref = "SW_RST"
    mcu_reset += sw[1], sw[2]

    r = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0402_1005Metric")
    v3v3 += r[1]; mcu_reset += r[2]

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    mcu_reset += c[1]; gnd += c[2]


@subcircuit
def user_led(d13, gnd):
    """D13 user LED with 1K current-limit resistor."""
    r = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0402_1005Metric")
    led = Part("Device", "LED", value="RED",
               footprint="LED_SMD:LED_0603_1608Metric")
    d13   += r[1]
    r[2]  += led[2]
    gnd   += led[1]


@subcircuit
def swd_header(v3v3, gnd, swclk, swdio, mcu_reset):
    """4-pin SWD debug header (VCC, SWDIO, SWCLK, GND)."""
    j = Part("Connector_Generic", "Conn_01x04",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    j.ref = "J_SWD"
    v3v3      += j[1]
    swdio     += j[2]
    swclk     += j[3]
    gnd       += j[4]


@subcircuit
def battery_connector(vbat, gnd):
    """JST-PH 2-pin LiPo battery connector."""
    j = Part("Connector_Generic", "Conn_01x02",
             footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    j.ref = "J_BAT"
    j.edge_preference = "bottom"
    vbat += j[1]; gnd += j[2]


@subcircuit
def feather_headers(v3v3, vbat, vbus, gnd, mcu_reset,
                    a0, a1, a2, a3, a4, a5,
                    sck, mosi, miso, uart_tx, uart_rx,
                    d5, d6, d9, d10, d11, d12, d13,
                    sda, scl):
    """
    Standard Feather headers:
      Left  (J_HDR_L): 16-pin — RST/3V3/AREF/GND/A0-A5/SCK/MOSI/MISO/RX/TX/~
      Right (J_HDR_R): 12-pin — BAT/EN/USB/D13/D12/D11/D10/D9/D6/D5/SDA/SCL
    """
    jl = Part("Connector_Generic", "Conn_01x16",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    jl.ref = "J_HDR_L"

    mcu_reset  += jl[1]   # RST
    v3v3       += jl[2]   # 3V3
    v3v3       += jl[3]   # AREF (tied to 3V3 on Feather basic)
    gnd        += jl[4]   # GND
    a0         += jl[5]   # A0
    a1         += jl[6]   # A1
    a2         += jl[7]   # A2
    a3         += jl[8]   # A3
    a4         += jl[9]   # A4
    a5         += jl[10]  # A5
    nc_aref2 = Net("NC_AREF2"); nc_aref2 += jl[11]  # spare (sometimes A6/VDIV)
    sck        += jl[12]  # SCK
    mosi       += jl[13]  # MOSI
    miso       += jl[14]  # MISO
    uart_tx    += jl[15]  # TX
    uart_rx    += jl[16]  # RX

    jr = Part("Connector_Generic", "Conn_01x12",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical")
    jr.ref = "J_HDR_R"

    vbat   += jr[1]   # BAT
    v3v3   += jr[2]   # EN (enable, pulled to 3V3)
    vbus   += jr[3]   # USB
    d13    += jr[4]   # D13
    d12    += jr[5]   # D12
    d11    += jr[6]   # D11
    d10    += jr[7]   # D10
    d9     += jr[8]   # D9
    d6     += jr[9]   # D6
    d5     += jr[10]  # D5
    sda    += jr[11]  # SDA
    scl    += jr[12]  # SCL


@subcircuit
def mounting_holes():
    """4x M2 mounting holes at Feather corners."""
    for _ in range(4):
        Part("Mechanical", "MountingHole",
             footprint="MountingHole:MountingHole_2.2mm_M2")


# ── Top-level instantiation ───────────────────────────────────────────────────
usb_power_input(vbus, gnd, usb_dp, usb_dm, cc1, cc2)
lipo_charger(vbus, vbat, gnd)
power_supply(vbus, vbat, gnd, v3v3)
battery_monitor(vbat, gnd, bat_div)
samd21_mcu(v3v3, gnd, usb_dp, usb_dm, xin32, xout32, mcu_reset,
           swclk, swdio, sda, scl, mosi, miso, sck,
           uart_tx, uart_rx, bat_div,
           a0, a1, a2, a3, a4, a5,
           d5, d6, d9, d10, d11, d12, d13)
rtc_crystal(xin32, xout32, gnd)
reset_button(v3v3, gnd, mcu_reset)
user_led(d13, gnd)
swd_header(v3v3, gnd, swclk, swdio, mcu_reset)
battery_connector(vbat, gnd)
feather_headers(v3v3, vbat, vbus, gnd, mcu_reset,
                a0, a1, a2, a3, a4, a5,
                sck, mosi, miso, uart_tx, uart_rx,
                d5, d6, d9, d10, d11, d12, d13,
                sda, scl)
mounting_holes()

# ── Board outline (Feather standard: 50.8 x 22.86mm) ────────────────────────
EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 50.8,
        "height_mm": 22.86,
        "corner_radius_mm": 1.5,
    },
    "edge_anchors": [
        {"ref": "J_USB", "edge": "left"},
        {"ref": "J_BAT", "edge": "right"},
    ],
}
