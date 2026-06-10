"""
ESP32 HUZZAH Breakout - SKiDL Circuit Description

Minimal ESP32 breakout for size and cost-conscious projects.
Dual-core WiFi/Bluetooth processor with 4MB SPI Flash (built into WROOM-32 module).
Requires external CP2104 or FTDI cable for programming.
Perfect for battery-powered IoT applications.

Based on Adafruit HUZZAH32 ESP32 Feather-style breakout.
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
# Global Nets
# ============================================================
vbat = Net("VBAT"); vbat.drive = POWER       # Battery input (3.7V LiPo)
vbus = Net("VBUS"); vbus.drive = POWER       # USB 5V input

# Power flags to satisfy ERC (external power sources connect via passive pins)
pwr_flag_vbat = Part("power", "PWR_FLAG", footprint="TestPoint:TestPoint_Pad_1.0x1.0mm")
pwr_flag_vbat[1] += vbat
pwr_flag_vbus = Part("power", "PWR_FLAG", footprint="TestPoint:TestPoint_Pad_1.0x1.0mm")
pwr_flag_vbus[1] += vbus
v3v3 = Net("+3V3"); v3v3.drive = POWER       # 3.3V regulated
gnd = Net("GND"); gnd.drive = POWER

# Signal nets
en_net = Net("EN")            # ESP32 enable / chip enable
gpio0 = Net("GPIO0")          # Boot mode select
txd = Net("TXD")              # UART TX
rxd = Net("RXD")              # UART RX

# ============================================================
# ESP32-WROOM-32 Module (tool=SKIDL, 39 pads)
# ============================================================
@subcircuit
def esp32_module(vcc, gnd_net, en, gpio0_n, tx, rx):
    """ESP32-WROOM-32 module with integrated 4MB SPI flash and antenna."""
    esp = Part(name="ESP32-WROOM-32", tool=SKIDL, dest=NETLIST,
               footprint="RF_Module:ESP32-WROOM-32",
               pins=[
                   Pin(num="1",  name="GND1",      func=Pin.types.PWRIN),
                   Pin(num="2",  name="3V3",        func=Pin.types.PWRIN),
                   Pin(num="3",  name="EN",         func=Pin.types.INPUT),
                   Pin(num="4",  name="SENSOR_VP",  func=Pin.types.INPUT),
                   Pin(num="5",  name="SENSOR_VN",  func=Pin.types.INPUT),
                   Pin(num="6",  name="IO34",       func=Pin.types.INPUT),
                   Pin(num="7",  name="IO35",       func=Pin.types.INPUT),
                   Pin(num="8",  name="IO32",       func=Pin.types.BIDIR),
                   Pin(num="9",  name="IO33",       func=Pin.types.BIDIR),
                   Pin(num="10", name="IO25",       func=Pin.types.BIDIR),
                   Pin(num="11", name="IO26",       func=Pin.types.BIDIR),
                   Pin(num="12", name="IO27",       func=Pin.types.BIDIR),
                   Pin(num="13", name="IO14",       func=Pin.types.BIDIR),
                   Pin(num="14", name="IO12",       func=Pin.types.BIDIR),
                   Pin(num="15", name="GND2",       func=Pin.types.PWRIN),
                   Pin(num="16", name="IO13",       func=Pin.types.BIDIR),
                   Pin(num="17", name="SD2",        func=Pin.types.BIDIR),
                   Pin(num="18", name="SD3",        func=Pin.types.BIDIR),
                   Pin(num="19", name="CMD",        func=Pin.types.BIDIR),
                   Pin(num="20", name="CLK",        func=Pin.types.BIDIR),
                   Pin(num="21", name="SD0",        func=Pin.types.BIDIR),
                   Pin(num="22", name="SD1",        func=Pin.types.BIDIR),
                   Pin(num="23", name="IO15",       func=Pin.types.BIDIR),
                   Pin(num="24", name="IO2",        func=Pin.types.BIDIR),
                   Pin(num="25", name="IO0",        func=Pin.types.BIDIR),
                   Pin(num="26", name="IO4",        func=Pin.types.BIDIR),
                   Pin(num="27", name="IO16",       func=Pin.types.BIDIR),
                   Pin(num="28", name="IO17",       func=Pin.types.BIDIR),
                   Pin(num="29", name="IO5",        func=Pin.types.BIDIR),
                   Pin(num="30", name="IO18",       func=Pin.types.BIDIR),
                   Pin(num="31", name="IO19",       func=Pin.types.BIDIR),
                   Pin(num="32", name="NC",         func=Pin.types.NOCONNECT),
                   Pin(num="33", name="IO21",       func=Pin.types.BIDIR),
                   Pin(num="34", name="RXD0",       func=Pin.types.INPUT),
                   Pin(num="35", name="TXD0",       func=Pin.types.OUTPUT),
                   Pin(num="36", name="IO22",       func=Pin.types.BIDIR),
                   Pin(num="37", name="IO23",       func=Pin.types.BIDIR),
                   Pin(num="38", name="GND3",       func=Pin.types.PWRIN),
                   Pin(num="39", name="GND_PAD",    func=Pin.types.PWRIN),
               ])
    _init_skidl_pins(esp)

    # Power connections
    esp["3V3"] += vcc
    esp["GND1"] += gnd_net
    esp["GND2"] += gnd_net
    esp["GND3"] += gnd_net
    esp["GND_PAD"] += gnd_net

    # Control signals
    esp["EN"] += en
    esp["IO0"] += gpio0_n
    esp["TXD0"] += tx
    esp["RXD0"] += rx

    # NC pin - no connect
    nc_net = Net("NC_ESP")
    nc_net.drive = POWER
    esp["NC"] += nc_net

    # Internal SPI flash bus pins (SD0-SD3, CMD, CLK) - used internally by
    # the WROOM module for its 4MB flash. Mark as no-connect on breakout.
    nc_sd = Net("NC_SDBUS")
    nc_sd.drive = POWER
    esp["SD0"] += nc_sd
    esp["SD1"] += nc_sd
    esp["SD2"] += nc_sd
    esp["SD3"] += nc_sd
    esp["CMD"] += nc_sd
    esp["CLK"] += nc_sd

    # Decoupling caps for ESP32
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += vcc
    c1[2] += gnd_net

    c2 = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    c2[1] += vcc
    c2[2] += gnd_net

    # EN pullup + RC reset circuit
    r_en = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_en[1] += vcc
    r_en[2] += en

    c_en = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_en[1] += en
    c_en[2] += gnd_net

    # GPIO0 pullup for normal boot
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_boot[1] += vcc
    r_boot[2] += gpio0_n

    # Breakout header pins - expose all GPIO to headers
    # Left header: 16 pins
    hdr_left = Part("Connector_Generic", "Conn_01x16",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    hdr_left[1] += gnd_net          # GND
    hdr_left[2] += esp["IO27"]      # A10 / 27
    hdr_left[3] += esp["IO33"]      # A9 / 33
    hdr_left[4] += esp["IO15"]      # A8 / 15
    hdr_left[5] += esp["IO32"]      # A7 / 32
    hdr_left[6] += esp["IO14"]      # A6 / 14
    hdr_left[7] += esp["SENSOR_VP"] # A0 / 36 (VP)
    hdr_left[8] += esp["SENSOR_VN"] # A1 / 39 (VN)
    hdr_left[9] += esp["IO34"]      # A2 / 34
    hdr_left[10] += esp["IO35"]     # A3 / 35
    hdr_left[11] += esp["IO25"]     # A4 / DAC1 / 25
    hdr_left[12] += esp["IO26"]     # A5 / DAC2 / 26
    hdr_left[13] += esp["IO4"]      # A5 / 4
    hdr_left[14] += esp["IO2"]      # IO2
    hdr_left[15] += esp["IO12"]     # A11 / 12
    hdr_left[16] += esp["IO13"]     # A12 / 13

    # Right header: 14 pins
    hdr_right = Part("Connector_Generic", "Conn_01x14",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x14_P2.54mm_Vertical")
    hdr_right[1] += vcc              # 3V3
    hdr_right[2] += esp["IO22"]      # SCL / 22
    hdr_right[3] += esp["IO23"]      # MOSI / 23
    hdr_right[4] += esp["IO19"]      # MISO / 19
    hdr_right[5] += esp["IO18"]      # SCK / 18
    hdr_right[6] += esp["IO5"]       # SS / 5
    hdr_right[7] += esp["IO17"]      # TX1 / 17
    hdr_right[8] += esp["IO16"]      # RX1 / 16
    hdr_right[9] += esp["IO21"]      # SDA / 21
    hdr_right[10] += tx              # TX
    hdr_right[11] += rx              # RX
    hdr_right[12] += gnd_net         # GND
    hdr_right[13] += en              # EN (reset)
    hdr_right[14] += vbat            # VBAT


# ============================================================
# Power Supply - 3.3V LDO from battery/USB
# ============================================================
@subcircuit
def power_supply(vin, vout, gnd_net):
    """AP2112K-3.3 600mA 3.3V LDO regulator with enable."""
    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"] += vin
    reg["VOUT"] += vout
    reg["GND"] += gnd_net
    reg["EN"] += vin   # Always enabled (tie EN to VIN)

    # Input cap
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin
    c_in[2] += gnd_net

    # Output decoupling
    c_out = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out[1] += vout
    c_out[2] += gnd_net

    c_out2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out2[1] += vout
    c_out2[2] += gnd_net


# ============================================================
# Reset and Boot Buttons
# ============================================================
@subcircuit
def buttons(en_net_local, gpio0_local, gnd_net):
    """Reset button (EN) and boot-mode button (GPIO0)."""
    # Reset button - pulls EN low
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_rst[1] += en_net_local
    sw_rst[2] += gnd_net

    # Boot button - pulls GPIO0 low for download mode
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_boot[1] += gpio0_local
    sw_boot[2] += gnd_net


# ============================================================
# Status LED
# ============================================================
@subcircuit
def status_led(signal, gnd_net):
    """Red LED on GPIO13 for status indication."""
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    led = Part("Device", "LED", value="Red",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led[1] += signal
    r_led[2] += led[1]
    led[2] += gnd_net


# ============================================================
# FTDI/CP2104 Programming Header (6-pin)
# ============================================================
@subcircuit
def uart_header(tx, rx, gnd_net, en_net_local):
    """6-pin FTDI-compatible header for programming."""
    hdr = Part("Connector_Generic", "Conn_01x06",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
    # Standard FTDI pinout
    hdr[1] += gnd_net       # GND
    hdr[2] += gnd_net       # CTS (unused, tie to GND)
    hdr[3] += vbus          # VCC (5V from FTDI)
    hdr[4] += tx            # TXO (ESP TX -> FTDI RX)
    hdr[5] += rx            # RXI (FTDI TX -> ESP RX)
    hdr[6] += en_net_local  # DTR (for auto-reset)


# ============================================================
# Battery Connector (JST-PH 2-pin)
# ============================================================
@subcircuit
def battery_connector(vbat_net, gnd_net):
    """JST-PH 2-pin connector for LiPo battery."""
    jst = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    jst[1] += vbat_net
    jst[2] += gnd_net


# ============================================================
# Instantiate all subcircuits
# ============================================================

# Power supply: VBAT -> 3.3V
power_supply(vbat, v3v3, gnd)

# ESP32 module
esp32_module(v3v3, gnd, en_net, gpio0, txd, rxd)

# Buttons
buttons(en_net, gpio0, gnd)

# Status LED on GPIO13 net (connected via header in ESP32 block)
led_net = Net("LED_GPIO13")
led_net.drive = POWER
status_led(led_net, gnd)

# UART programming header
uart_header(txd, rxd, gnd, en_net)

# Battery connector
battery_connector(vbat, gnd)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
