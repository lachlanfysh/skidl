"""
Trinket 3.3/5V Mini Microcontroller — SKiDL Circuit
=====================================================
Ultra-compact ATtiny85-based development board with bitbang USB support,
MCP1700 3.3V LDO regulator, power/user LEDs, reset button, and GPIO breakout.
Based on the Adafruit Trinket design.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ===========================================================================
# Power nets
# ===========================================================================
vusb_raw = Net("VBUS_RAW"); vusb_raw.drive = POWER   # USB connector 5V (pre-diode)
vusb = Net("VBUS"); vusb.drive = POWER                # Protected USB 5V (post-diode)
vcc = Net("VCC"); vcc.drive = POWER                    # Regulated 3.3V output
gnd = Net("GND"); gnd.drive = POWER                    # Ground

# ===========================================================================
# Signal nets
# ===========================================================================
usb_conn_dp = Net("USB_CONN_DP")              # USB connector D+ (pre-resistor)
usb_conn_dm = Net("USB_CONN_DM")              # USB connector D- (pre-resistor)
usb_dp = Net("USB_DP")                        # USB D+ to MCU (post-resistor)
usb_dm = Net("USB_DM")                        # USB D- to MCU (post-resistor)
reset_n = Net("~{RESET}")                     # Active-low reset
pb0 = Net("PB0")                              # GPIO0 / AREF
pb1 = Net("PB1")                              # GPIO1 / user LED
pb2 = Net("PB2")                              # GPIO2 / analog1

# ===========================================================================
# Subcircuit: USB interface
# ===========================================================================
@subcircuit
def usb_interface(vusb_raw, vusb, gnd, conn_dp, conn_dm, mcu_dp, mcu_dm):
    """Micro-USB connector with Schottky protection, series resistors, and pull-up."""

    # USB Micro-B connector
    usb = Part("Connector", "USB_B_Micro",
               footprint="Connector_USB:USB_Micro-B_Molex_47346-0001",
               value="USB_Micro-B")
    usb["VBUS"] += vusb_raw
    usb["GND"] += gnd
    usb["D+"] += conn_dp
    usb["D-"] += conn_dm
    usb["ID"] += NC             # Not used for device mode
    usb["Shield"] += gnd        # Shield tied to ground

    # Schottky diode for reverse-voltage protection on USB VBUS
    # Anode = raw USB 5V, Cathode = protected rail
    d1 = Part("Device", "D_Schottky",
              footprint="Diode_SMD:D_SOD-323",
              value="MBR0520")
    d1["A"] += vusb_raw
    d1["K"] += vusb

    # 68-ohm series resistors on USB data lines (V-USB spec)
    r_dp = Part("Device", "R", value="68R",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_dp[1] += conn_dp
    r_dp[2] += mcu_dp

    r_dm = Part("Device", "R", value="68R",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_dm[1] += conn_dm
    r_dm[2] += mcu_dm

    # 1.5k pull-up on D- for low-speed USB device identification
    r_pullup = Part("Device", "R", value="1K5",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_pullup[1] += vusb
    r_pullup[2] += mcu_dm

    # 100nF decoupling on VBUS
    c_usb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_usb[1] += vusb
    c_usb[2] += gnd


usb_interface(vusb_raw, vusb, gnd, usb_conn_dp, usb_conn_dm, usb_dp, usb_dm)

# ===========================================================================
# Subcircuit: Power regulation (3.3V LDO)
# ===========================================================================
@subcircuit
def power_regulation(vin, vout, gnd):
    """MCP1700-3302E/TT 3.3V LDO regulator with input/output caps."""

    # MCP1700 3.3V LDO (SOT-23-3)
    reg = Part("Regulator_Linear", "MCP1700x-300xxTT",
               footprint="Package_TO_SOT_SMD:SOT-23",
               value="MCP1700-3302")
    reg["VI"] += vin
    reg["VO"] += vout
    reg["GND"] += gnd

    # Input capacitor — 1uF ceramic
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output capacitor — 1uF ceramic
    c_out = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out[1] += vout
    c_out[2] += gnd

    # Output decoupling — 100nF ceramic
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vout
    c_dec[2] += gnd


power_regulation(vusb, vcc, gnd)

# ===========================================================================
# Subcircuit: MCU (ATtiny85)
# ===========================================================================
@subcircuit
def mcu_block(vcc, gnd, reset_n, pb0, pb1, pb2, usb_dp, usb_dm):
    """ATtiny85 microcontroller with decoupling and reset circuit."""

    # ATtiny85 in SOIC-8 package
    mcu = Part("MCU_Microchip_ATtiny", "ATtiny85-20S",
               footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
               value="ATtiny85")
    mcu["VCC"] += vcc
    mcu["GND"] += gnd
    mcu["~{RESET}/PB5"] += reset_n
    mcu["AREF/PB0"] += pb0
    mcu["PB1"] += pb1
    mcu["PB2"] += pb2
    mcu["XTAL1/PB3"] += usb_dp    # PB3 = USB D+ (V-USB)
    mcu["XTAL2/PB4"] += usb_dm    # PB4 = USB D- (V-USB)

    # 100nF VCC decoupling cap — placed close to MCU
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vcc
    c_dec[2] += gnd

    # 10K pull-up resistor on RESET
    r_reset = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    r_reset[1] += vcc
    r_reset[2] += reset_n

    # Reset button (active low, pulls RESET to GND)
    sw_reset = Part("Switch", "SW_Push",
                    footprint="Button_Switch_SMD:SW_Push_1TS009xxxx-xxxx-xxxx_6x6x5mm",
                    value="RESET")
    sw_reset[1] += reset_n
    sw_reset[2] += gnd


mcu_block(vcc, gnd, reset_n, pb0, pb1, pb2, usb_dp, usb_dm)

# ===========================================================================
# Subcircuit: LEDs and indicators
# ===========================================================================
@subcircuit
def led_indicators(vcc, gnd, pb1):
    """Power LED (green) and user-programmable LED (red) on PB1."""

    # Power LED — green, always on when powered
    led_pwr = Part("Device", "LED", value="GREEN",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_pwr[1] += vcc
    r_pwr[2] += led_pwr[2]   # Anode
    led_pwr[1] += gnd        # Cathode

    # User LED — red, driven by PB1 (GPIO #1)
    led_usr = Part("Device", "LED", value="RED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_usr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_usr[1] += pb1
    r_usr[2] += led_usr[2]   # Anode
    led_usr[1] += gnd        # Cathode


led_indicators(vcc, gnd, pb1)

# ===========================================================================
# Subcircuit: GPIO breakout header
# ===========================================================================
@subcircuit
def gpio_header(gnd, pb0, pb1, pb2, vcc):
    """5-pin breakout header: GND, PB0, PB1, PB2, VCC (like Trinket pinout)."""

    hdr = Part("Connector_Generic", "Conn_01x05",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
               value="GPIO")
    hdr[1] += gnd
    hdr[2] += pb0
    hdr[3] += pb1
    hdr[4] += pb2
    hdr[5] += vcc


gpio_header(gnd, pb0, pb1, pb2, vcc)

# ===========================================================================
# Generate schematic
# ===========================================================================
generate_schematic(auto_stub=True)
