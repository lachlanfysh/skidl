"""
PyPortal - CircuitPython Powered Internet Display
===================================================
ATSAMD51J20 M4 processor + ESP32 WiFi coprocessor
3.2" 320x240 TFT with resistive touchscreen
ADT7410 temperature sensor, light sensor, NeoPixel
microSD card, 8MB SPI flash, speaker
USB native, I2C STEMMA port, 2x analog/digital ports
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


class _MockLib:
    """Minimal lib object so SKIDL-tool parts work with the schematic writer."""
    def __init__(self, filename):
        self.filename = filename


def skidl_part(name, footprint, pins, value=None, lib_name="skidl_custom"):
    """Create a Part using tool=SKIDL with synthetic draw_cmds so that the
    schematic generator can compute bounding boxes and place the part.

    Pins are arranged vertically along the left side of a rectangle body.
    Pin spacing is 2.54mm (standard KiCad grid).
    """
    p = Part(name=name, tool=SKIDL, dest=NETLIST,
             footprint=footprint,
             pins=pins)
    if value:
        p.value = value

    # Add a mock lib attribute for the schematic writer
    p.lib = _MockLib(lib_name)

    pin_spacing = 2.54  # mm
    pin_length = 2.54   # mm
    num_pins = len(p.pins)

    # Body dimensions
    body_height = max(num_pins * pin_spacing, pin_spacing * 2)
    body_width = 5.08  # mm (2 grid units)

    # Set pin attributes and build draw commands
    draw_cmds = []
    for i, pin in enumerate(p.pins):
        y_pos = body_height / 2 - i * pin_spacing
        pin.orientation = 0    # 0 degrees = "R" (pointing right into body)
        pin.x = -body_width / 2 - pin_length
        pin.y = y_pos

        # Pin function type string for draw_cmd
        func_map = {
            Pin.types.PWRIN: "power_in",
            Pin.types.PWROUT: "power_out",
            Pin.types.INPUT: "input",
            Pin.types.OUTPUT: "output",
            Pin.types.BIDIR: "bidirectional",
            Pin.types.TRISTATE: "tri_state",
            Pin.types.PASSIVE: "passive",
            Pin.types.UNSPEC: "unspecified",
            Pin.types.NOCONNECT: "no_connect",
        }
        func_str = func_map.get(pin.func, "passive")

        pin_cmd = [
            "pin", func_str, "line",
            ["at", pin.x, pin.y, 0],
            ["length", pin_length],
            ["name", pin.name,
             ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num),
             ["effects", ["font", ["size", 1.27, 1.27]]]],
        ]
        draw_cmds.append(pin_cmd)

    # Rectangle body
    rect_cmd = [
        "rectangle",
        ["start", -body_width / 2, body_height / 2 + pin_spacing / 2],
        ["end", body_width / 2, -(body_height / 2 + pin_spacing / 2)],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]
    draw_cmds.append(rect_cmd)

    # Property commands for Reference and Value
    draw_cmds.append([
        "property", "Reference", name[0] + "?",
        ["at", body_width / 2 + 1.27, 0, 0],
        ["effects", ["font", ["size", 1.27, 1.27]]],
    ])
    draw_cmds.append([
        "property", "Value", value or name,
        ["at", 0, -(body_height / 2 + pin_spacing), 0],
        ["effects", ["font", ["size", 1.27, 1.27]]],
    ])

    p.draw_cmds = {1: draw_cmds, 0: [rect_cmd]}

    return p


# =============================================================================
# Power Nets
# =============================================================================
vcc = Net("+3V3"); vcc.drive = POWER
v5 = Net("+5V"); v5.drive = POWER
vbus = Net("VBUS"); vbus.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# =============================================================================
# Subcircuit: Power Supply (USB + LDO regulator)
# =============================================================================
@subcircuit
def power_supply(vbus, v5, vcc, gnd):
    """USB Micro-B input + AP2112K-3.3 LDO for 3.3V rail."""
    # USB Micro-B connector
    usb = Part("Connector", "USB_B_Micro",
               footprint="Connector_USB:USB_Micro-B_Molex_47346-0001",
               value="USB_Micro-B")
    usb["VBUS"] += vbus
    usb["GND"] += gnd
    usb["Shield"] += gnd
    usb["ID"] += NC  # not used in device mode

    # USB data lines to SAMD51
    usb["D-"] += Net("USB_DM")
    usb["D+"] += Net("USB_DP")

    # Schottky diode for VBUS to 5V rail
    d1 = Part("Device", "D_Schottky", value="MBR0530",
              footprint="Diode_SMD:D_SOD-123")
    d1[1] += vbus   # anode
    d1[2] += v5     # cathode

    # AP2112K-3.3 LDO: 5V -> 3.3V
    ldo = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               value="AP2112K-3.3")
    ldo["VIN"] += v5
    ldo["VOUT"] += vcc
    ldo["GND"] += gnd
    ldo["EN"] += v5      # always enabled

    # Input cap
    cin = Part("Device", "C", value="10uF",
               footprint="Capacitor_SMD:C_0805_2012Metric")
    cin[1] += v5; cin[2] += gnd

    # Output cap
    cout = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    cout[1] += vcc; cout[2] += gnd

    # Decoupling on 3.3V rail
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vcc; c_dec[2] += gnd

# =============================================================================
# Subcircuit: ATSAMD51J20A MCU
# =============================================================================
@subcircuit
def samd51_mcu(vcc, gnd):
    """ATSAMD51J20A-A (TQFP-64) - main processor."""
    mcu = Part("MCU_Microchip_SAMD", "ATSAMD51J20A-A",
               footprint="Package_QFP:LQFP-64_10x10mm_P0.5mm",
               value="ATSAMD51J20A")

    # Power pins
    mcu["VDDANA"] += vcc
    mcu["VDDIOB"] += vcc
    mcu["VDDIO"] += vcc   # multiple VDDIO pins
    mcu["VSW"] += vcc
    mcu["GND"] += gnd
    mcu["GNDANA"] += gnd

    # VDDCORE - 1.2V core output, needs 1uF cap
    vcore = Net("VDDCORE")
    mcu["VDDCORE"] += vcore
    c_core = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_core[1] += vcore; c_core[2] += gnd

    # 32.768kHz crystal on PA00/PA01
    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["PA00"]
    xtal[2] += mcu["PA01"]
    # Crystal load caps
    cx1 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cx1[1] += mcu["PA00"]; cx1[2] += gnd
    cx2 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cx2[1] += mcu["PA01"]; cx2[2] += gnd

    # USB data
    mcu["PA24"] += Net("USB_DM")
    mcu["PA25"] += Net("USB_DP")

    # SPI to TFT display (SERCOM2)
    mcu["PA12"] += Net("TFT_MOSI")
    mcu["PA13"] += Net("TFT_SCK")
    mcu["PA14"] += Net("TFT_MISO")
    mcu["PB09"] += Net("TFT_CS")
    mcu["PB06"] += Net("TFT_DC")
    mcu["PA15"] += Net("TFT_RST")

    # Touch screen analog lines (resistive touch)
    mcu["PB04"] += Net("TOUCH_YD")
    mcu["PB05"] += Net("TOUCH_XL")
    mcu["PA04"] += Net("TOUCH_YU")
    mcu["PA05"] += Net("TOUCH_XR")

    # TFT backlight PWM
    mcu["PB31"] += Net("TFT_BACKLIGHT")

    # SPI to SD card (SERCOM5)
    mcu["PB22"] += Net("SD_MOSI")
    mcu["PB23"] += Net("SD_SCK")
    mcu["PB02"] += Net("SD_MISO")
    mcu["PB30"] += Net("SD_CS")

    # SPI to flash (SERCOM3, QSPI)
    mcu["PA08"] += Net("FLASH_MOSI")
    mcu["PA09"] += Net("FLASH_MISO")
    mcu["PA10"] += Net("FLASH_SCK")
    mcu["PA11"] += Net("FLASH_CS")

    # UART to ESP32 (SERCOM4)
    mcu["PB12"] += Net("ESP_TX")
    mcu["PB13"] += Net("ESP_RX")
    mcu["PB14"] += Net("ESP_BUSY")
    mcu["PB15"] += Net("ESP_CS")
    mcu["PB16"] += Net("ESP_RESET")
    mcu["PB17"] += Net("ESP_GPIO0")

    # I2C bus (SERCOM1)
    mcu["PA16"] += Net("SDA")
    mcu["PA17"] += Net("SCL")

    # NeoPixel data
    mcu["PB00"] += Net("NEOPIXEL")

    # Speaker output (DAC)
    mcu["PA02"] += Net("SPEAKER_OUT")

    # Light sensor analog input
    mcu["PB01"] += Net("LIGHT_SENSE")

    # Analog/digital port pins
    mcu["PA06"] += Net("D3_A1")
    mcu["PA07"] += Net("D4_A2")

    # Reset
    mcu["~{RESET}"] += Net("RESET")

    # SWD debug
    mcu["PA30"] += Net("SWCLK")
    mcu["PA31"] += Net("SWDIO")

    # Remaining pins to general nets
    mcu["PA03"] += Net("A0_SPEAKER")
    mcu["PB03"] += Net("D13_LED")
    mcu["PA18"] += Net("PA18_NC")
    mcu["PA19"] += Net("PA19_NC")
    mcu["PA20"] += Net("PA20_NC")
    mcu["PA21"] += Net("PA21_NC")
    mcu["PA22"] += Net("PA22_NC")
    mcu["PA23"] += Net("PA23_NC")
    mcu["PA27"] += Net("PA27_NC")
    mcu["PB07"] += Net("PB07_NC")
    mcu["PB08"] += Net("PB08_NC")
    mcu["PB10"] += Net("PB10_NC")
    mcu["PB11"] += Net("PB11_NC")

    # Decoupling caps (one per power pin group)
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vcc; c[2] += gnd

    # Reset pull-up + cap
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vcc; r_rst[2] += Net("RESET")
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_rst[1] += Net("RESET"); c_rst[2] += gnd

# =============================================================================
# Subcircuit: ESP32 WiFi Coprocessor
# =============================================================================
@subcircuit
def esp32_wifi(vcc, gnd):
    """ESP32-WROOM-32 module as WiFi/TLS coprocessor."""
    esp = skidl_part("ESP32-WROOM-32",
                     footprint="RF_Module:ESP32-WROOM-32",
                     pins=[
                         Pin(num="1", name="GND_1", func=Pin.types.PWRIN),
                         Pin(num="2", name="VDD", func=Pin.types.PWRIN),
                         Pin(num="3", name="EN", func=Pin.types.INPUT),
                         Pin(num="4", name="SENSOR_VP", func=Pin.types.INPUT),
                         Pin(num="5", name="SENSOR_VN", func=Pin.types.INPUT),
                         Pin(num="6", name="IO34", func=Pin.types.INPUT),
                         Pin(num="7", name="IO35", func=Pin.types.INPUT),
                         Pin(num="8", name="IO32", func=Pin.types.BIDIR),
                         Pin(num="9", name="IO33", func=Pin.types.BIDIR),
                         Pin(num="10", name="IO25", func=Pin.types.BIDIR),
                         Pin(num="11", name="IO26", func=Pin.types.BIDIR),
                         Pin(num="12", name="IO27", func=Pin.types.BIDIR),
                         Pin(num="13", name="IO14", func=Pin.types.BIDIR),
                         Pin(num="14", name="IO12", func=Pin.types.BIDIR),
                         Pin(num="15", name="GND_2", func=Pin.types.PWRIN),
                         Pin(num="16", name="IO13", func=Pin.types.BIDIR),
                         Pin(num="17", name="SHD_SD2", func=Pin.types.BIDIR),
                         Pin(num="18", name="SWP_SD3", func=Pin.types.BIDIR),
                         Pin(num="19", name="SCS_CMD", func=Pin.types.BIDIR),
                         Pin(num="20", name="SCK_CLK", func=Pin.types.BIDIR),
                         Pin(num="21", name="SDO_SD0", func=Pin.types.BIDIR),
                         Pin(num="22", name="SDI_SD1", func=Pin.types.BIDIR),
                         Pin(num="23", name="IO15", func=Pin.types.BIDIR),
                         Pin(num="24", name="IO2", func=Pin.types.BIDIR),
                         Pin(num="25", name="IO0", func=Pin.types.BIDIR),
                         Pin(num="26", name="IO4", func=Pin.types.BIDIR),
                         Pin(num="27", name="IO16", func=Pin.types.BIDIR),
                         Pin(num="28", name="IO17", func=Pin.types.BIDIR),
                         Pin(num="29", name="IO5", func=Pin.types.BIDIR),
                         Pin(num="30", name="IO18", func=Pin.types.BIDIR),
                         Pin(num="31", name="IO23", func=Pin.types.BIDIR),
                         Pin(num="32", name="GND_3", func=Pin.types.PWRIN),
                         Pin(num="33", name="IO19", func=Pin.types.BIDIR),
                         Pin(num="34", name="IO22", func=Pin.types.BIDIR),
                         Pin(num="35", name="RXD0", func=Pin.types.INPUT),
                         Pin(num="36", name="TXD0", func=Pin.types.OUTPUT),
                         Pin(num="37", name="IO21", func=Pin.types.BIDIR),
                         Pin(num="38", name="IO3", func=Pin.types.BIDIR),
                         Pin(num="39", name="GND_4", func=Pin.types.PWRIN),
                     ],
                     value="ESP32-WROOM-32")

    # Power
    esp["VDD"] += vcc
    esp["GND_1"] += gnd
    esp["GND_2"] += gnd
    esp["GND_3"] += gnd
    esp["GND_4"] += gnd

    # Enable with pull-up + RC delay
    r_en = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_en[1] += vcc; r_en[2] += esp["EN"]
    c_en = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_en[1] += esp["EN"]; c_en[2] += gnd

    # UART from SAMD51
    esp["RXD0"] += Net("ESP_TX")
    esp["TXD0"] += Net("ESP_RX")

    # SPI handshake / control from SAMD51
    esp["IO5"] += Net("ESP_CS")
    esp["IO33"] += Net("ESP_BUSY")
    esp["IO0"] += Net("ESP_GPIO0")

    # ESP reset
    r_esp_rst = Part("Device", "R", value="10K",
                     footprint="Resistor_SMD:R_0402_1005Metric")
    r_esp_rst[1] += vcc; r_esp_rst[2] += Net("ESP_RESET")

    # Boot mode pull-up
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += vcc; r_boot[2] += esp["IO0"]

    # Internal SPI flash pins (module internal)
    esp["SHD_SD2"] += Net("ESP_SD2")
    esp["SWP_SD3"] += Net("ESP_SD3")
    esp["SCS_CMD"] += Net("ESP_CMD")
    esp["SCK_CLK"] += Net("ESP_CLK")
    esp["SDO_SD0"] += Net("ESP_SD0")
    esp["SDI_SD1"] += Net("ESP_SD1")

    # Unused IO
    esp["SENSOR_VP"] += Net("ESP_VP")
    esp["SENSOR_VN"] += Net("ESP_VN")
    esp["IO34"] += Net("ESP_IO34")
    esp["IO35"] += Net("ESP_IO35")
    esp["IO32"] += Net("ESP_IO32")
    esp["IO25"] += Net("ESP_IO25")
    esp["IO26"] += Net("ESP_IO26")
    esp["IO27"] += Net("ESP_IO27")
    esp["IO14"] += Net("ESP_IO14")
    esp["IO12"] += Net("ESP_IO12")
    esp["IO13"] += Net("ESP_IO13")
    esp["IO15"] += Net("ESP_IO15")
    esp["IO2"] += Net("ESP_IO2")
    esp["IO4"] += Net("ESP_IO4")
    esp["IO16"] += Net("ESP_IO16")
    esp["IO17"] += Net("ESP_IO17")
    esp["IO18"] += Net("ESP_IO18")
    esp["IO23"] += Net("ESP_IO23")
    esp["IO19"] += Net("ESP_IO19")
    esp["IO22"] += Net("ESP_IO22")
    esp["IO21"] += Net("ESP_IO21")
    esp["IO3"] += Net("ESP_IO3")

    # Decoupling
    c_esp1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_esp1[1] += vcc; c_esp1[2] += gnd
    c_esp2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_esp2[1] += vcc; c_esp2[2] += gnd

# =============================================================================
# Subcircuit: TFT Display Interface
# =============================================================================
@subcircuit
def tft_display(vcc, gnd):
    """ILI9341 TFT display connector."""
    tft = skidl_part("ILI9341_TFT",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_2x07_P2.54mm_Vertical",
                     pins=[
                         Pin(num="1", name="VCC", func=Pin.types.PWRIN),
                         Pin(num="2", name="GND_1", func=Pin.types.PWRIN),
                         Pin(num="3", name="CS", func=Pin.types.INPUT),
                         Pin(num="4", name="DC", func=Pin.types.INPUT),
                         Pin(num="5", name="MOSI", func=Pin.types.INPUT),
                         Pin(num="6", name="SCK", func=Pin.types.INPUT),
                         Pin(num="7", name="LED", func=Pin.types.INPUT),
                         Pin(num="8", name="MISO", func=Pin.types.OUTPUT),
                         Pin(num="9", name="RST", func=Pin.types.INPUT),
                         Pin(num="10", name="GND_2", func=Pin.types.PWRIN),
                         Pin(num="11", name="TOUCH_YD", func=Pin.types.PASSIVE),
                         Pin(num="12", name="TOUCH_XL", func=Pin.types.PASSIVE),
                         Pin(num="13", name="TOUCH_YU", func=Pin.types.PASSIVE),
                         Pin(num="14", name="TOUCH_XR", func=Pin.types.PASSIVE),
                     ],
                     value="ILI9341_320x240")

    tft["VCC"] += vcc
    tft["GND_1"] += gnd
    tft["GND_2"] += gnd
    tft["CS"] += Net("TFT_CS")
    tft["DC"] += Net("TFT_DC")
    tft["MOSI"] += Net("TFT_MOSI")
    tft["SCK"] += Net("TFT_SCK")
    tft["MISO"] += Net("TFT_MISO")
    tft["RST"] += Net("TFT_RST")

    # Touch screen connections
    tft["TOUCH_YD"] += Net("TOUCH_YD")
    tft["TOUCH_XL"] += Net("TOUCH_XL")
    tft["TOUCH_YU"] += Net("TOUCH_YU")
    tft["TOUCH_XR"] += Net("TOUCH_XR")

    # Backlight MOSFET driver
    q_bl = Part("Transistor_FET", "2N7002",
                footprint="Package_TO_SOT_SMD:SOT-23",
                value="2N7002")
    q_bl[1] += Net("TFT_BACKLIGHT")  # Gate
    q_bl[2] += tft["LED"]            # Drain
    q_bl[3] += gnd                   # Source

    r_bl = Part("Device", "R", value="10R",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_bl[1] += vcc; r_bl[2] += tft["LED"]

    # Decoupling
    c_tft = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_tft[1] += vcc; c_tft[2] += gnd

# =============================================================================
# Subcircuit: 8MB SPI Flash
# =============================================================================
@subcircuit
def spi_flash(vcc, gnd):
    """W25Q64JV 8MB SPI flash for CircuitPython filesystem."""
    flash = Part("Memory_Flash", "W25Q128JVS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                 value="W25Q64JV-8MB")

    flash["VCC"] += vcc
    flash["GND"] += gnd
    flash["~{CS}"] += Net("FLASH_CS")
    flash["DI/IO_{0}"] += Net("FLASH_MOSI")
    flash["DO/IO_{1}"] += Net("FLASH_MISO")
    flash["CLK"] += Net("FLASH_SCK")
    flash["~{WP}/IO_{2}"] += vcc
    flash["~{HOLD}/~{RESET}/IO_{3}"] += vcc

    # Decoupling
    c_flash = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_flash[1] += vcc; c_flash[2] += gnd

# =============================================================================
# Subcircuit: microSD Card
# =============================================================================
@subcircuit
def sd_card(vcc, gnd):
    """microSD card slot for data storage."""
    sd = Part("Connector", "Micro_SD_Card",
              footprint="Connector_Card:microSD_HC_Molex_104031-0811",
              value="microSD")

    sd["VDD"] += vcc
    sd["VSS"] += gnd
    sd["SHIELD"] += gnd

    # SPI mode
    sd["CMD"] += Net("SD_MOSI")
    sd["CLK"] += Net("SD_SCK")
    sd["DAT0"] += Net("SD_MISO")
    sd["DAT3/CD"] += Net("SD_CS")

    # Unused DAT pins - pull up
    r_dat1 = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_dat1[1] += vcc; r_dat1[2] += sd["DAT1"]
    r_dat2 = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_dat2[1] += vcc; r_dat2[2] += sd["DAT2"]

    # Decoupling
    c_sd = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_sd[1] += vcc; c_sd[2] += gnd

# =============================================================================
# Subcircuit: ADT7410 Temperature Sensor
# =============================================================================
@subcircuit
def temp_sensor(vcc, gnd):
    """ADT7410 high-accuracy I2C temperature sensor."""
    adt = skidl_part("ADT7410",
                     footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                     pins=[
                         Pin(num="1", name="SCL", func=Pin.types.INPUT),
                         Pin(num="2", name="SDA", func=Pin.types.BIDIR),
                         Pin(num="3", name="A0", func=Pin.types.INPUT),
                         Pin(num="4", name="GND_ADT", func=Pin.types.PWRIN),
                         Pin(num="5", name="CT", func=Pin.types.OUTPUT),
                         Pin(num="6", name="INT_ADT", func=Pin.types.OUTPUT),
                         Pin(num="7", name="A1", func=Pin.types.INPUT),
                         Pin(num="8", name="VDD", func=Pin.types.PWRIN),
                     ],
                     value="ADT7410")

    adt["VDD"] += vcc
    adt["GND_ADT"] += gnd
    adt["SCL"] += Net("SCL")
    adt["SDA"] += Net("SDA")
    adt["A0"] += gnd
    adt["A1"] += gnd
    adt["CT"] += Net("TEMP_CT")
    adt["INT_ADT"] += Net("TEMP_INT")

    # Decoupling
    c_adt = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_adt[1] += vcc; c_adt[2] += gnd

# =============================================================================
# Subcircuit: Light Sensor
# =============================================================================
@subcircuit
def light_sensor(vcc, gnd):
    """Ambient light sensor with analog output."""
    pt = skidl_part("ALS-PT19",
                    footprint="LED_SMD:LED_0805_2012Metric",
                    pins=[
                        Pin(num="1", name="C", func=Pin.types.PASSIVE),
                        Pin(num="2", name="E", func=Pin.types.PASSIVE),
                    ],
                    value="ALS-PT19")
    pt["C"] += vcc
    pt["E"] += Net("LIGHT_SENSE")

    # Load resistor
    r_load = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_load[1] += Net("LIGHT_SENSE")
    r_load[2] += gnd

# =============================================================================
# Subcircuit: NeoPixel Status LED
# =============================================================================
@subcircuit
def neopixel_led(vcc, gnd):
    """Single NeoPixel (SK6812) status LED."""
    neo = Part("LED", "SK6812",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
               value="NeoPixel")
    neo["VDD"] += vcc
    neo["VSS"] += gnd
    neo["DIN"] += Net("NEOPIXEL")
    neo["DOUT"] += Net("NEO_DOUT")

    # Decoupling
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_neo[1] += vcc; c_neo[2] += gnd

# =============================================================================
# Subcircuit: Speaker + Amplifier
# =============================================================================
@subcircuit
def speaker_amp(vcc, gnd):
    """Speaker output with Class-D amplifier."""
    amp = skidl_part("PAM8302A",
                     footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                     pins=[
                         Pin(num="1", name="nSD", func=Pin.types.INPUT),
                         Pin(num="2", name="INP", func=Pin.types.INPUT),
                         Pin(num="3", name="INN", func=Pin.types.INPUT),
                         Pin(num="4", name="GND_AMP1", func=Pin.types.PWRIN),
                         Pin(num="5", name="OUTP", func=Pin.types.OUTPUT),
                         Pin(num="6", name="VDD", func=Pin.types.PWRIN),
                         Pin(num="7", name="GND_AMP2", func=Pin.types.PWRIN),
                         Pin(num="8", name="OUTN", func=Pin.types.OUTPUT),
                     ],
                     value="PAM8302A")

    amp["VDD"] += vcc
    amp["GND_AMP1"] += gnd
    amp["GND_AMP2"] += gnd
    amp["nSD"] += vcc

    # Input from SAMD DAC via coupling cap
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += Net("SPEAKER_OUT")
    c_in[2] += amp["INP"]
    amp["INN"] += gnd

    # Speaker connector (2-pin JST)
    spk = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
               value="Speaker")
    spk["Pin_1"] += amp["OUTP"]
    spk["Pin_2"] += amp["OUTN"]

    # Decoupling
    c_amp = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_amp[1] += vcc; c_amp[2] += gnd
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vcc; c_bulk[2] += gnd

# =============================================================================
# Subcircuit: I2C Bus
# =============================================================================
@subcircuit
def i2c_bus(vcc, gnd):
    """I2C bus pull-ups and STEMMA/Qwiic connector."""
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc; r_sda[2] += Net("SDA")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl[1] += vcc; r_scl[2] += Net("SCL")

    # STEMMA QT / Qwiic connector (JST SH 4-pin)
    stemma = Part("Connector_Generic", "Conn_01x04",
                  footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
                  value="STEMMA_QT")
    stemma["Pin_1"] += gnd
    stemma["Pin_2"] += vcc
    stemma["Pin_3"] += Net("SDA")
    stemma["Pin_4"] += Net("SCL")

# =============================================================================
# Subcircuit: Expansion Ports
# =============================================================================
@subcircuit
def expansion_ports(vcc, gnd):
    """Two analog/digital expansion ports."""
    port1 = Part("Connector_Generic", "Conn_01x03",
                 footprint="Connector_JST:JST_PH_S3B-PH-K_1x03_P2.00mm_Horizontal",
                 value="D3_A1_Port")
    port1["Pin_1"] += gnd
    port1["Pin_2"] += vcc
    port1["Pin_3"] += Net("D3_A1")

    port2 = Part("Connector_Generic", "Conn_01x03",
                 footprint="Connector_JST:JST_PH_S3B-PH-K_1x03_P2.00mm_Horizontal",
                 value="D4_A2_Port")
    port2["Pin_1"] += gnd
    port2["Pin_2"] += vcc
    port2["Pin_3"] += Net("D4_A2")

# =============================================================================
# Subcircuit: Debug/Reset
# =============================================================================
@subcircuit
def debug_reset(vcc, gnd):
    """Reset button and SWD debug header."""
    btn = skidl_part("RESET_SW",
                     footprint="Button_Switch_SMD:SW_SPST_PTS645Sx43SMTR92",
                     pins=[
                         Pin(num="1", name="A", func=Pin.types.PASSIVE),
                         Pin(num="2", name="B", func=Pin.types.PASSIVE),
                     ],
                     value="Reset")
    btn["A"] += Net("RESET")
    btn["B"] += gnd

    # SWD debug header
    swd = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_1.27mm:PinHeader_1x04_P1.27mm_Vertical",
               value="SWD")
    swd["Pin_1"] += vcc
    swd["Pin_2"] += Net("SWCLK")
    swd["Pin_3"] += Net("SWDIO")
    swd["Pin_4"] += gnd

    # D13 LED indicator
    led = Part("Device", "LED", value="Red",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led[1] += Net("D13_LED")
    led[2] += r_led[1]
    r_led[2] += gnd

# =============================================================================
# Instantiate all subcircuits
# =============================================================================
power_supply(vbus, v5, vcc, gnd)
samd51_mcu(vcc, gnd)
esp32_wifi(vcc, gnd)
tft_display(vcc, gnd)
spi_flash(vcc, gnd)
sd_card(vcc, gnd)
temp_sensor(vcc, gnd)
light_sensor(vcc, gnd)
neopixel_led(vcc, gnd)
speaker_amp(vcc, gnd)
i2c_bus(vcc, gnd)
expansion_ports(vcc, gnd)
debug_reset(vcc, gnd)

# =============================================================================
# Generate Schematic
# =============================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3)
