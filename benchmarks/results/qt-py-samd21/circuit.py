"""
QT Py SAMD21 Tiny Development Board
====================================
Ultra-small SAMD21-based board with STEMMA QT I2C connector for plug-and-play
sensors. Seeed Xiao-compatible pinout. 256KB Flash, 32KB RAM. RGB NeoPixel,
reset button. Optional SOIC SPI Flash on bottom pads.

Key components:
- ATSAMD21E18A-A (TQFP-32, 256KB flash, 32KB RAM)
- AP2112K-3.3 LDO voltage regulator (3.3V from USB 5V)
- USB-C connector for power and data
- WS2812B RGB NeoPixel
- JST SH 4-pin STEMMA QT / Qwiic I2C connector
- Reset button
- Optional GD25Q16 SPI flash (SOIC-8)
- 32.768 kHz crystal for RTC
- Edge castellated headers (Xiao-compatible pinout)
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


def skidl_part(name, footprint, pins, ref=None):
    """Create a Part(tool=SKIDL) with pin defaults for schematic generation.

    The schematic generator expects pins to have x, y, orientation, and length
    attributes plus draw_cmds for bounding box computation. This helper sets
    sensible defaults on each pin so that SKIDL-defined parts work with
    generate_schematic().
    """
    from collections import defaultdict

    n = len(pins)
    # Split pins: left side and right side
    left_count = (n + 1) // 2
    right_count = n - left_count
    pin_spacing = 2.54  # mm
    pin_length = 2.54   # mm

    # Box dimensions: height = max(left, right) * spacing, width reasonable
    box_h = max(left_count, right_count) * pin_spacing
    box_w = max(10.0, box_h * 0.6)  # min width 10mm

    pin_objs = []
    draw_cmds = defaultdict(list)

    # Add rectangle for the body
    draw_cmds[1].append(
        ["rectangle",
         ["start", -box_w / 2, -box_h / 2],
         ["end", box_w / 2, box_h / 2],
         ["stroke", ["width", 0.254], ["type", "default"]],
         ["fill", ["type", "background"]]]
    )

    for i, p in enumerate(pins):
        if i < left_count:
            # Left-side pin
            px = -box_w / 2 - pin_length
            py = -box_h / 2 + (i + 0.5) * pin_spacing if left_count > 1 else 0
            orient = "R"
            angle = 0
        else:
            # Right-side pin
            ri = i - left_count
            px = box_w / 2 + pin_length
            py = -box_h / 2 + (ri + 0.5) * pin_spacing if right_count > 1 else 0
            orient = "L"
            angle = 180

        pin_objs.append(Pin(
            num=p["num"],
            name=p["name"],
            func=p["func"],
            x=px,
            y=py,
            orientation=orient,
            length=pin_length,
            rotation=angle,
        ))

        # Add pin draw command
        draw_cmds[1].append(
            ["pin", "bidirectional", "line",
             ["at", px, py, angle],
             ["length", pin_length],
             ["name", p["name"],
              ["effects", ["font", ["size", 1.27, 1.27]]]],
             ["number", p["num"],
              ["effects", ["font", ["size", 1.27, 1.27]]]]]
        )

    part = Part(name=name, tool=SKIDL, dest=NETLIST,
                footprint=footprint, pins=pin_objs)
    part.draw_cmds = draw_cmds

    # Provide a stub lib with filename so schematic output can build lib_id
    class _StubLib:
        def __init__(self, fname):
            self.filename = fname
    part.lib = _StubLib("skidl_custom")

    if ref:
        part.ref = ref
    return part


# ============================================================================
# Global nets
# ============================================================================
vbus = Net("VBUS"); vbus.drive = POWER       # USB 5V
vcc = Net("+3V3"); vcc.drive = POWER          # 3.3V regulated
gnd = Net("GND"); gnd.drive = POWER

# I2C bus (STEMMA QT / Qwiic)
sda = Net("SDA")
scl = Net("SCL")

# USB data lines
usb_dp = Net("USB_D+")
usb_dm = Net("USB_D-")

# SPI Flash bus
flash_mosi = Net("FLASH_MOSI")
flash_miso = Net("FLASH_MISO")
flash_sck = Net("FLASH_SCK")
flash_cs = Net("FLASH_CS")

# NeoPixel data
neo_data = Net("NEOPIXEL")

# Reset
nreset = Net("~{RESET}")

# Crystal
xin = Net("XIN32")
xout = Net("XOUT32")

# GPIO for headers (Xiao-compatible pinout)
gpio_a0 = Net("A0_D0")
gpio_a1 = Net("A1_D1")
gpio_a2 = Net("A2_D2")
gpio_a3 = Net("A3_D3")
gpio_sck = Net("SCK_D8")
gpio_miso = Net("MISO_D9")
gpio_mosi = Net("MOSI_D10")

# ============================================================================
# Subcircuit: USB-C Connector and ESD/CC resistors
# ============================================================================
@subcircuit
def usb_input(vbus_net, gnd_net, dp_net, dm_net):
    """USB-C connector with CC resistors for UFP (device) role."""

    # USB-C receptacle (simplified for USB 2.0)
    usb_conn = skidl_part("USB_C",
        "Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11",
        [
            {"num": "A1",  "name": "GND_A1",  "func": Pin.types.PASSIVE},
            {"num": "A4",  "name": "VBUS_A4", "func": Pin.types.PASSIVE},
            {"num": "A5",  "name": "CC1",     "func": Pin.types.PASSIVE},
            {"num": "A6",  "name": "DP_A6",   "func": Pin.types.PASSIVE},
            {"num": "A7",  "name": "DM_A7",   "func": Pin.types.PASSIVE},
            {"num": "A8",  "name": "SBU1",    "func": Pin.types.PASSIVE},
            {"num": "A9",  "name": "VBUS_A9", "func": Pin.types.PASSIVE},
            {"num": "A12", "name": "GND_A12", "func": Pin.types.PASSIVE},
            {"num": "B1",  "name": "GND_B1",  "func": Pin.types.PASSIVE},
            {"num": "B4",  "name": "VBUS_B4", "func": Pin.types.PASSIVE},
            {"num": "B5",  "name": "CC2",     "func": Pin.types.PASSIVE},
            {"num": "B6",  "name": "DP_B6",   "func": Pin.types.PASSIVE},
            {"num": "B7",  "name": "DM_B7",   "func": Pin.types.PASSIVE},
            {"num": "B8",  "name": "SBU2",    "func": Pin.types.PASSIVE},
            {"num": "B9",  "name": "VBUS_B9", "func": Pin.types.PASSIVE},
            {"num": "B12", "name": "GND_B12", "func": Pin.types.PASSIVE},
            {"num": "S1",  "name": "SHIELD",  "func": Pin.types.PASSIVE},
        ], ref="J1")

    # VBUS connections
    usb_conn["VBUS_A4"] += vbus_net
    usb_conn["VBUS_A9"] += vbus_net
    usb_conn["VBUS_B4"] += vbus_net
    usb_conn["VBUS_B9"] += vbus_net

    # GND connections
    usb_conn["GND_A1"] += gnd_net
    usb_conn["GND_A12"] += gnd_net
    usb_conn["GND_B1"] += gnd_net
    usb_conn["GND_B12"] += gnd_net
    usb_conn["SHIELD"] += gnd_net

    # USB data lines (both orientations tied together for USB 2.0)
    usb_conn["DP_A6"] += dp_net
    usb_conn["DP_B6"] += dp_net
    usb_conn["DM_A7"] += dm_net
    usb_conn["DM_B7"] += dm_net

    # CC1 and CC2 pull-down resistors (5.1K for UFP/device role)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1.ref = "R1"
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2.ref = "R2"

    usb_conn["CC1"] += r_cc1[1]
    r_cc1[2] += gnd_net
    usb_conn["CC2"] += r_cc2[1]
    r_cc2[2] += gnd_net

    # SBU pins not connected (leave floating via NC net)
    nc_sbu1 = Net("NC_SBU1")
    nc_sbu2 = Net("NC_SBU2")
    usb_conn["SBU1"] += nc_sbu1
    usb_conn["SBU2"] += nc_sbu2


# ============================================================================
# Subcircuit: 3.3V Power Supply (AP2112K-3.3)
# ============================================================================
@subcircuit
def power_supply(vin_net, vout_net, gnd_net):
    """AP2112K-3.3 LDO regulator with input/output caps."""

    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg.ref = "U2"

    reg["VIN"] += vin_net
    reg["EN"] += vin_net      # Enable tied to input (always on)
    reg["GND"] += gnd_net
    reg["VOUT"] += vout_net

    # Input capacitor
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in.ref = "C1"
    c_in[1] += vin_net
    c_in[2] += gnd_net

    # Output capacitor
    c_out = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out.ref = "C2"
    c_out[1] += vout_net
    c_out[2] += gnd_net


# ============================================================================
# Subcircuit: SAMD21 MCU with decoupling and crystal
# ============================================================================
@subcircuit
def mcu_block(vcc_net, gnd_net, usb_dp_net, usb_dm_net, sda_net, scl_net,
              nreset_net, neo_net,
              flash_mosi_net, flash_miso_net, flash_sck_net, flash_cs_net,
              xin_net, xout_net,
              a0_net, a1_net, a2_net, a3_net,
              sck_net, miso_net, mosi_net):
    """ATSAMD21E18A-A MCU with decoupling caps, crystal, and reset circuit."""

    mcu = Part("MCU_Microchip_SAMD", "ATSAMD21E18A-A",
               footprint="Package_QFP:TQFP-32_7x7mm_P0.8mm")
    mcu.ref = "U1"

    # Power
    mcu["VDDIN"] += vcc_net
    mcu["VDDANA"] += vcc_net
    mcu["GND"] += gnd_net

    # VDDCORE: 1.2V core output, needs 1uF cap
    vddcore = Net("VDDCORE")
    mcu["VDDCORE"] += vddcore
    c_core = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_core.ref = "C3"
    c_core[1] += vddcore
    c_core[2] += gnd_net

    # Decoupling caps for VDD
    c_vdd = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vdd.ref = "C4"
    c_vdd[1] += vcc_net
    c_vdd[2] += gnd_net

    c_vdda = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vdda.ref = "C5"
    c_vdda[1] += vcc_net
    c_vdda[2] += gnd_net

    # USB data pins: PA24 = D-, PA25 = D+
    mcu["PA24"] += usb_dm_net
    mcu["PA25"] += usb_dp_net

    # I2C: PA16 = SDA (SERCOM1 PAD0), PA17 = SCL (SERCOM1 PAD1)
    mcu["PA16"] += sda_net
    mcu["PA17"] += scl_net

    # Reset
    mcu["~{RESET}"] += nreset_net

    # NeoPixel data: PA27
    mcu["PA27"] += neo_net

    # SPI Flash: PA04=MOSI, PA05=SCK, PA06=MISO, PA07=CS
    mcu["PA04"] += flash_mosi_net
    mcu["PA05"] += flash_sck_net
    mcu["PA06"] += flash_miso_net
    mcu["PA07"] += flash_cs_net

    # 32.768 kHz crystal: PA00 = XIN32, PA01 = XOUT32
    mcu["PA00"] += xin_net
    mcu["PA01"] += xout_net

    # GPIO header pins (Xiao-compatible pinout)
    mcu["PA02"] += a0_net      # A0/D0
    mcu["PA03"] += a1_net      # A1/D1 (DAC REF)
    mcu["PA10"] += a2_net      # A2/D2
    mcu["PA11"] += a3_net      # A3/D3
    mcu["PA08"] += sck_net     # SCK/D8
    mcu["PA09"] += miso_net    # MISO/D9
    mcu["PA14"] += mosi_net    # MOSI/D10

    # SWD debug: PA30=SWCLK, PA31=SWDIO (directly on pads, no header)
    swclk = Net("SWCLK")
    swdio = Net("SWDIO")
    mcu["PA30"] += swclk
    mcu["PA31"] += swdio

    # Remaining GPIOs left unconnected via NC nets
    nc_pa15 = Net("NC_PA15")
    nc_pa18 = Net("NC_PA18")
    nc_pa19 = Net("NC_PA19")
    nc_pa22 = Net("NC_PA22")
    nc_pa23 = Net("NC_PA23")
    nc_pa28 = Net("NC_PA28")
    mcu["PA15"] += nc_pa15
    mcu["PA18"] += nc_pa18
    mcu["PA19"] += nc_pa19
    mcu["PA22"] += nc_pa22
    mcu["PA23"] += nc_pa23
    mcu["PA28"] += nc_pa28

    # 32.768 kHz crystal
    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal.ref = "Y1"
    xtal[1] += xin_net
    xtal[2] += xout_net

    # Crystal load capacitors (typical 6.8pF for 32.768 kHz)
    c_xin = Part("Device", "C", value="6.8pF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xin.ref = "C6"
    c_xin[1] += xin_net
    c_xin[2] += gnd_net

    c_xout = Part("Device", "C", value="6.8pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xout.ref = "C7"
    c_xout[1] += xout_net
    c_xout[2] += gnd_net

    # Reset pull-up resistor (10K to VCC)
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst.ref = "R3"
    r_rst[1] += vcc_net
    r_rst[2] += nreset_net

    # Reset capacitor (100nF for debounce)
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_rst.ref = "C8"
    c_rst[1] += nreset_net
    c_rst[2] += gnd_net


# ============================================================================
# Subcircuit: RGB NeoPixel (WS2812B)
# ============================================================================
@subcircuit
def neopixel_block(vcc_net, gnd_net, data_in_net):
    """Single WS2812B RGB NeoPixel with decoupling cap."""

    led = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    led.ref = "D1"
    led["VDD"] += vcc_net
    led["VSS"] += gnd_net
    led["DIN"] += data_in_net

    # DOUT not connected (single LED, no chain)
    nc_dout = Net("NC_DOUT")
    led["DOUT"] += nc_dout

    # Bypass capacitor
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo.ref = "C9"
    c_neo[1] += vcc_net
    c_neo[2] += gnd_net


# ============================================================================
# Subcircuit: Reset Button
# ============================================================================
@subcircuit
def reset_button(nreset_net, gnd_net):
    """Tactile reset button connected between RESET and GND."""

    sw = skidl_part("SW_Push",
        "Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
        [
            {"num": "1", "name": "P1", "func": Pin.types.PASSIVE},
            {"num": "2", "name": "P2", "func": Pin.types.PASSIVE},
        ], ref="SW1")
    sw["P1"] += nreset_net
    sw["P2"] += gnd_net


# ============================================================================
# Subcircuit: STEMMA QT / Qwiic I2C Connector
# ============================================================================
@subcircuit
def stemma_qt(vcc_net, gnd_net, sda_net, scl_net):
    """JST SH 4-pin STEMMA QT / Qwiic connector with I2C pull-ups."""

    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    conn.ref = "J2"

    # STEMMA QT/Qwiic pinout: GND, VCC, SDA, SCL
    conn[1] += gnd_net
    conn[2] += vcc_net
    conn[3] += sda_net
    conn[4] += scl_net

    # I2C pull-up resistors (10K on QT Py)
    r_sda = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda.ref = "R4"
    r_sda[1] += vcc_net
    r_sda[2] += sda_net

    r_scl = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl.ref = "R5"
    r_scl[1] += vcc_net
    r_scl[2] += scl_net


# ============================================================================
# Subcircuit: Optional SPI Flash (GD25Q16 / W25Q16)
# ============================================================================
@subcircuit
def spi_flash(vcc_net, gnd_net, mosi_net, miso_net, sck_net, cs_net):
    """Optional SOIC-8 SPI flash on bottom pads."""

    flash = skidl_part("GD25Q16",
        "Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
        [
            {"num": "1", "name": "nCS",   "func": Pin.types.INPUT},
            {"num": "2", "name": "DO",    "func": Pin.types.OUTPUT},
            {"num": "3", "name": "nWP",   "func": Pin.types.INPUT},
            {"num": "4", "name": "GND",   "func": Pin.types.PWRIN},
            {"num": "5", "name": "DI",    "func": Pin.types.INPUT},
            {"num": "6", "name": "CLK",   "func": Pin.types.INPUT},
            {"num": "7", "name": "nHOLD", "func": Pin.types.INPUT},
            {"num": "8", "name": "VCC",   "func": Pin.types.PWRIN},
        ], ref="U3")

    flash["VCC"] += vcc_net
    flash["GND"] += gnd_net
    flash["DI"] += mosi_net
    flash["DO"] += miso_net
    flash["CLK"] += sck_net
    flash["nCS"] += cs_net

    # WP and HOLD tied high (not write-protected, not held)
    flash["nWP"] += vcc_net
    flash["nHOLD"] += vcc_net

    # Decoupling cap
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_flash.ref = "C10"
    c_flash[1] += vcc_net
    c_flash[2] += gnd_net


# ============================================================================
# Subcircuit: Edge Castellated Headers (Xiao-compatible pinout)
# ============================================================================
@subcircuit
def edge_headers(vcc_net, gnd_net, a0, a1, a2, a3,
                 sda_net, scl_net, sck_net, miso_net, mosi_net):
    """2x 7-pin castellated edge headers (Seeed Xiao footprint compatible)."""

    # Left header: 3V3, GND, A0, A1, A2, A3, SDA
    hdr_l = Part("Connector_Generic", "Conn_01x07",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical")
    hdr_l.ref = "J3"
    hdr_l[1] += vcc_net
    hdr_l[2] += gnd_net
    hdr_l[3] += a0
    hdr_l[4] += a1
    hdr_l[5] += a2
    hdr_l[6] += a3
    hdr_l[7] += sda_net

    # Right header: SCL, SCK, MISO, MOSI, RX(D6), TX(D7), 5V
    # Note: TX/RX on QT Py are shared with SDA/SCL in some configs
    hdr_r = Part("Connector_Generic", "Conn_01x07",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical")
    hdr_r.ref = "J4"
    hdr_r[1] += scl_net
    hdr_r[2] += sck_net
    hdr_r[3] += miso_net
    hdr_r[4] += mosi_net
    hdr_r[5] += sda_net     # TX/RX alternate
    hdr_r[6] += scl_net     # TX/RX alternate
    hdr_r[7] += Net("VBUS_HDR")  # 5V pad


# ============================================================================
# Top-level circuit assembly
# ============================================================================

# USB input connector
usb_input(vbus, gnd, usb_dp, usb_dm)

# 3.3V power supply from USB 5V
power_supply(vbus, vcc, gnd)

# Main MCU
mcu_block(vcc, gnd, usb_dp, usb_dm, sda, scl, nreset, neo_data,
          flash_mosi, flash_miso, flash_sck, flash_cs,
          xin, xout,
          gpio_a0, gpio_a1, gpio_a2, gpio_a3,
          gpio_sck, gpio_miso, gpio_mosi)

# RGB NeoPixel
neopixel_block(vcc, gnd, neo_data)

# Reset button
reset_button(nreset, gnd)

# STEMMA QT I2C connector
stemma_qt(vcc, gnd, sda, scl)

# Optional SPI flash
spi_flash(vcc, gnd, flash_mosi, flash_miso, flash_sck, flash_cs)

# Edge castellated headers
edge_headers(vcc, gnd, gpio_a0, gpio_a1, gpio_a2, gpio_a3,
             sda, scl, gpio_sck, gpio_miso, gpio_mosi)

# ============================================================================
# Generate schematic
# ============================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
