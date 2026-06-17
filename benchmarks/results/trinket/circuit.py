"""
Trinket ATtiny85 dev board — SKiDL circuit description.

Based on the Adafruit Trinket hardware:
- ATtiny85-20S (SOIC-8) MCU with V-USB software USB
- USB-A male plug (horizontal, plugs directly into host USB port)
- MIC5225-3.3YM5 SOT-23-5 3.3V LDO regulator
- Green power LED (always on)
- Red user LED on PB1 (Arduino digital pin 1)
- Tactile reset button
- 5 GPIO pins broken out on 0.1" header
- Board ~31x15mm

V-USB wiring (low-speed, per Adafruit Trinket reference design):
  D- → PB3/XTAL1 (pin 2) via 68R
  D+ → PB4/XTAL2 (pin 3) via 68R + 1.5kΩ pull-up to 3.3V

LCSC converted parts:
  MIC5225-3.3YM5-TR: C512101
"""

from skidl import *

set_default_tool(KICAD9)

# ---------------------------------------------------------------------------
# Power rails
# ---------------------------------------------------------------------------
vbus = Net("VBUS"); vbus.drive = POWER    # 5V from USB host
vcc = Net("3V3"); vcc.drive = POWER       # 3.3V regulated
gnd = Net("GND"); gnd.drive = POWER

# ---------------------------------------------------------------------------
# Signal nets
# ---------------------------------------------------------------------------
usb_dp_raw = Net("USB_DP_RAW")   # D+ at connector
usb_dm_raw = Net("USB_DM_RAW")   # D- at connector
usb_dp = Net("USB_DP")           # D+ at MCU (post 68R)
usb_dm = Net("USB_DM")           # D- at MCU (post 68R)
reset_n = Net("~{RESET}")
pb0 = Net("PB0")
pb1 = Net("PB1")                  # Red LED / Arduino pin 1
pb2 = Net("PB2")

# ---------------------------------------------------------------------------
# USB-A male plug (horizontal, board plugs into host port)
# ---------------------------------------------------------------------------
@subcircuit
def usb_connector(vbus, gnd, dp_raw, dm_raw):
    usb = Part("Connector", "USB_A",
               footprint="Connector_USB:USB_A_Molex_67643_Horizontal")
    usb.edge_preference = "left"
    vbus += usb["VBUS"]
    gnd  += usb["GND"]
    dp_raw += usb["D+"]
    dm_raw += usb["D-"]

usb_connector(vbus, gnd, usb_dp_raw, usb_dm_raw)

# ---------------------------------------------------------------------------
# 3.3V LDO: MIC5205-3.3YM5 (SOT-23-5, KiCad symbol — functional equiv of MIC5225)
# Pins: IN=1, GND=2, EN=3, BP=4, OUT=5
# EN tied high = always on. BP = bypass cap pin (float/NC on MIC5225 equivalent)
# ---------------------------------------------------------------------------
@subcircuit
def power_block(vin, vout, gnd):
    reg = Part("Regulator_Linear", "MIC5205-3.3YM5",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    vin  += reg["IN"]
    gnd  += reg["GND"]
    vout += reg["OUT"]
    vout += reg["EN"]   # EN tied high = always on
    reg["BP"] += NC     # Bypass cap pin (optional noise filter, left open)

    # Input bulk + decoupling
    c_in = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    vin += c_in[1]; gnd += c_in[2]

    c_in_dec = Part("Device", "C", value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    vin += c_in_dec[1]; gnd += c_in_dec[2]

    # Output bulk + decoupling
    c_out = Part("Device", "C_Polarized", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    vout += c_out[1]; gnd += c_out[2]

    c_out_dec = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0603_1608Metric")
    vout += c_out_dec[1]; gnd += c_out_dec[2]

power_block(vbus, vcc, gnd)

# ---------------------------------------------------------------------------
# V-USB data lines: 68R series + 1.5k D+ pull-up
# ---------------------------------------------------------------------------
@subcircuit
def vusb_data(dp_raw, dm_raw, dp_mcu, dm_mcu, vcc):
    r_dp = Part("Device", "R", value="68R",
                footprint="Resistor_SMD:R_0603_1608Metric")
    dp_raw += r_dp[1]; dp_mcu += r_dp[2]

    r_dm = Part("Device", "R", value="68R",
                footprint="Resistor_SMD:R_0603_1608Metric")
    dm_raw += r_dm[1]; dm_mcu += r_dm[2]

    # 1.5kΩ pull-up on D+ for low-speed USB device signalling
    r_pu = Part("Device", "R", value="1K5",
                footprint="Resistor_SMD:R_0603_1608Metric")
    vcc += r_pu[1]; dp_mcu += r_pu[2]

vusb_data(usb_dp_raw, usb_dm_raw, usb_dp, usb_dm, vcc)

# ---------------------------------------------------------------------------
# ATtiny85-20S (SOIC-8) MCU
# V-USB: D-=PB3/XTAL1, D+=PB4/XTAL2 (per Adafruit Trinket schematic)
# ---------------------------------------------------------------------------
@subcircuit
def mcu_block(vcc, gnd, reset_n, pb0, pb1, pb2, usb_dp, usb_dm):
    mcu = Part("MCU_Microchip_ATtiny", "ATtiny85-20S",
               footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
               value="ATtiny85")
    mcu["VCC"] += vcc
    mcu["GND"] += gnd
    mcu["~{RESET}/PB5"] += reset_n
    mcu["AREF/PB0"] += pb0
    mcu["PB1"] += pb1
    mcu["PB2"] += pb2
    mcu["XTAL1/PB3"] += usb_dm   # D-
    mcu["XTAL2/PB4"] += usb_dp   # D+

    # MCU VCC decoupling
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    vcc += c_dec[1]; gnd += c_dec[2]

    # Reset pull-up + button
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    vcc += r_rst[1]; reset_n += r_rst[2]

    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1TS009xxxx-xxxx-xxxx_6x6x5mm",
                  value="RESET")
    reset_n += sw_rst[1]; gnd += sw_rst[2]

mcu_block(vcc, gnd, reset_n, pb0, pb1, pb2, usb_dp, usb_dm)

# ---------------------------------------------------------------------------
# LEDs: green power + red user (PB1)
# ---------------------------------------------------------------------------
@subcircuit
def led_block(vcc, gnd, pb1):
    # Green power LED (anode = pin 2, cathode = pin 1 per KiCad LED symbol)
    led_pwr = Part("Device", "LED", value="GREEN",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    vcc += r_pwr[1]; r_pwr[2] += led_pwr["A"]
    gnd += led_pwr["K"]

    # Red user LED on PB1
    led_usr = Part("Device", "LED", value="RED",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_usr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    pb1 += r_usr[1]; r_usr[2] += led_usr["A"]
    gnd += led_usr["K"]

led_block(vcc, gnd, pb1)

# ---------------------------------------------------------------------------
# GPIO header: PB0, PB1, PB2, PB3(D-), PB4(D+), 3V3, GND
# 7-pin 0.1" header on board edge
# ---------------------------------------------------------------------------
@subcircuit
def gpio_header(vcc, gnd, pb0, pb1, pb2, usb_dm, usb_dp):
    hdr = Part("Connector_Generic", "Conn_01x07",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical",
               value="GPIO")
    hdr.edge_preference = "right"
    hdr[1] += vcc
    hdr[2] += pb0
    hdr[3] += pb1
    hdr[4] += pb2
    hdr[5] += usb_dm   # PB3
    hdr[6] += usb_dp   # PB4
    hdr[7] += gnd

gpio_header(vcc, gnd, pb0, pb1, pb2, usb_dm, usb_dp)

# ---------------------------------------------------------------------------
# Board floorplan: 40x25mm (compact but routable dev board)
# USB-A plug hangs off left edge (edge connector). GPIO header at right.
# ---------------------------------------------------------------------------
EDA_FLOORPLAN = {
    "outline": {"width_mm": 40, "height_mm": 25, "corner_radius_mm": 1.5},
}
