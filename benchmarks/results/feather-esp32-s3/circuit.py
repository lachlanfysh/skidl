"""
Feather ESP32-S3 -- SKiDL circuit description
WiFi/BLE Feather with ESP32-S3-WROOM-1 module (pre-certified, includes antenna + flash).
USB-C with native USB OTG. MCP73831 LiPo charger, JST-PH battery, AP2112K-3.3 LDO.
NeoPixel (WS2812B). Feather headers (2x 1x16). 50.8x22.86mm Feather form factor.
"""

from skidl import *

# Global power nets
vbus = Net("VBUS");  vbus.drive = POWER
vbat = Net("VBAT");  vbat.drive = POWER
v3v3 = Net("+3V3");  v3v3.drive = POWER
gnd  = Net("GND");   gnd.drive  = POWER

usb_dp        = Net("USB_DP")
usb_dm        = Net("USB_DM")
sda           = Net("SDA")
scl           = Net("SCL")
esp_en        = Net("ESP_EN")
neopixel_data = Net("NEOPIXEL")


@subcircuit
def usb_input(vbus, gnd, dp, dm):
    """USB-C receptacle with CC pull-down resistors for UFP device role."""
    global usb_dp, usb_dm
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
               footprint="Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal")

    # VBUS pins
    usb["A4"]  += vbus
    usb["A9"]  += vbus
    usb["B4"]  += vbus
    usb["B9"]  += vbus

    # GND pins
    usb["A1"]  += gnd
    usb["A12"] += gnd
    usb["B1"]  += gnd
    usb["B12"] += gnd
    usb["S1"]  += gnd   # shield

    # Data lines (both A and B side tied together for USB 2.0)
    usb["A6"]  += dp
    usb["B6"]  += dp
    usb["A7"]  += dm
    usb["B7"]  += dm

    # CC pull-down resistors (5.1k for UFP device detection)
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["A5"]   # CC1
    r_cc1[2] += gnd
    r_cc2[1] += usb["B5"]   # CC2
    r_cc2[2] += gnd

    # SBU pins - no connect
    usb["A8"] += Net("SBU1_NC")
    usb["B8"] += Net("SBU2_NC")

    # VBUS bulk decoupling
    c_vbus = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vbus[1] += vbus
    c_vbus[2] += gnd

    c_vbus2 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vbus2[1] += vbus
    c_vbus2[2] += gnd

usb_input(vbus, gnd, usb_dp, usb_dm)


@subcircuit
def battery_charger(vbus, vbat, gnd):
    """MCP73831T-2ATI/OT LiPo charger, 500mA charge current."""
    global v3v3
    chrg = Part("Battery_Management", "MCP73831-2-OT",
                footprint="Package_TO_SOT_SMD:SOT-23-5")
    chrg["V_{DD}"]  += vbus
    chrg["V_{SS}"]  += gnd
    chrg["V_{BAT}"] += vbat

    # PROG sets charge current: I_charge = 1000/R_PROG -> 2kΩ = 500mA
    r_prog = Part("Device", "R", value="2K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_prog[1] += chrg["PROG"]
    r_prog[2] += gnd

    # Charge status LED (STAT is open-drain, active low during charging)
    stat_net = Net("CHRG_STAT")
    chrg["STAT"] += stat_net
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    led = Part("Device", "LED", value="ORANGE",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led[1] += vbus
    r_led[2] += led["A"]
    led["K"] += stat_net

    # Input decoupling
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vbus
    c_in[2] += gnd

battery_charger(vbus, vbat, gnd)


@subcircuit
def battery_connector(vbat, gnd):
    """JST PH 2-pin horizontal connector for LiPo battery."""
    jst = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    jst[1] += vbat
    jst[2] += gnd

    # Battery bulk capacitor
    c_bat = Part("Device", "C_Polarized", value="100uF",
                 footprint="Capacitor_SMD:CP_Elec_6.3x5.4")
    c_bat[1] += vbat
    c_bat[2] += gnd

    c_bat2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_bat2[1] += vbat
    c_bat2[2] += gnd

battery_connector(vbat, gnd)


@subcircuit
def power_regulator(vbat, v3v3, gnd, en_net):
    """AP2112K-3.3 600mA LDO regulator."""
    reg = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"]  += vbat
    reg["GND"]  += gnd
    reg["EN"]   += en_net
    reg["VOUT"] += v3v3

    # NC pin
    reg["NC"] += Net("LDO_NC")

    # Enable pull-up - always on by default
    r_en = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_en[1] += vbat
    r_en[2] += en_net

    # Input capacitor
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1] += vbat
    c_in[2] += gnd

    # Output capacitor
    c_out = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out[1] += v3v3
    c_out[2] += gnd

    # Bulk output cap
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

power_regulator(vbat, v3v3, gnd, esp_en)


@subcircuit
def esp32_module(v3v3, gnd, dp, dm, sda_net, scl_net, en_net, neopixel):
    """ESP32-S3-WROOM-1 WiFi/BLE module with native USB."""
    global vbus, vbat
    esp = Part("RF_Module", "ESP32-S3-WROOM-1",
               footprint="RF_Module:ESP32-S3-WROOM-1")

    # Power
    esp["3V3"] += v3v3
    esp["GND"] += gnd

    # Enable (active high, RC delay for reliable power-on)
    esp["EN"] += en_net
    c_en = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_en[1] += en_net
    c_en[2] += gnd

    # Native USB D+/D- (IO19=D-, IO20=D+ in ESP32-S3)
    # WROOM-1 exposes USB_D- and USB_D+ directly
    esp["USB_D-"] += dm
    esp["USB_D+"] += dp

    # I2C on IO8(SDA), IO9(SCL)
    esp["IO8"]  += sda_net
    esp["IO9"]  += scl_net

    # I2C pull-ups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v3v3;  r_sda[2] += sda_net
    r_scl[1] += v3v3;  r_scl[2] += scl_net

    # NeoPixel on IO2
    esp["IO2"] += neopixel

    # BOOT button input on IO0 (active low, pull-up)
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += v3v3
    r_boot[2] += esp["IO0"]

    # UART TX/RX exposed on headers
    tx_net = Net("UART_TX")
    rx_net = Net("UART_RX")
    esp["TXD0"] += tx_net
    esp["RXD0"] += rx_net

    # Decoupling: multiple caps per module datasheet
    for _ in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v3v3
        c[2] += gnd

    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v3v3
    c_bulk[2] += gnd

    # Expose remaining GPIO on Feather headers
    esp["IO1"]  += Net("A0")
    esp["IO3"]  += Net("A1")
    esp["IO4"]  += Net("A2")
    esp["IO5"]  += Net("A3")
    esp["IO6"]  += Net("A4")
    esp["IO7"]  += Net("A5")
    esp["IO10"] += Net("GPIO_D5")
    esp["IO11"] += Net("GPIO_D6")
    esp["IO12"] += Net("GPIO_D9")
    esp["IO13"] += Net("GPIO_D10")
    esp["IO14"] += Net("GPIO_D11")
    esp["IO15"] += Net("GPIO_D12")
    esp["IO16"] += Net("GPIO_D13")

esp32_module(v3v3, gnd, usb_dp, usb_dm, sda, scl, esp_en, neopixel_data)


@subcircuit
def neopixel_led(v3v3, gnd, data_in):
    """Single WS2812B NeoPixel for status indication."""
    neo = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    neo["VDD"] += v3v3
    neo["VSS"] += gnd
    neo["DIN"] += data_in
    neo["DOUT"] += Net("NEO_DOUT_NC")

    # Bypass capacitor per WS2812B datasheet
    c_neo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_neo[1] += v3v3
    c_neo[2] += gnd

neopixel_led(v3v3, gnd, neopixel_data)


@subcircuit
def feather_headers(vbus, vbat, v3v3, gnd, en_net, sda_net, scl_net):
    """Standard Adafruit Feather headers (2x 1x16 pin headers)."""
    # Left header: RST, 3V3, AREF, GND, A0-A5, SCK, MOSI, MISO, RX, TX
    hdr_l = Part("Connector_Generic", "Conn_01x16",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    hdr_l[1]  += Net("RST_HDR")      # RST
    hdr_l[2]  += v3v3                # 3V3
    hdr_l[3]  += Net("AREF")         # AREF (3V3)
    hdr_l[4]  += gnd                 # GND
    hdr_l[5]  += Net("A0")
    hdr_l[6]  += Net("A1")
    hdr_l[7]  += Net("A2")
    hdr_l[8]  += Net("A3")
    hdr_l[9]  += Net("A4")
    hdr_l[10] += Net("A5")
    hdr_l[11] += scl_net             # SCL
    hdr_l[12] += sda_net             # SDA
    hdr_l[13] += Net("GPIO_D13")     # D13
    hdr_l[14] += Net("GPIO_D12")     # D12
    hdr_l[15] += Net("GPIO_D11")     # D11
    hdr_l[16] += Net("GPIO_D10")     # D10

    # Right header: VBAT, EN, VBUS, D5, D6, D9, D10, D11, D12, D13, TX, RX
    hdr_r = Part("Connector_Generic", "Conn_01x16",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical")
    hdr_r[1]  += Net("HDR_NC1")
    hdr_r[2]  += Net("HDR_NC2")
    hdr_r[3]  += Net("HDR_NC3")
    hdr_r[4]  += Net("HDR_NC4")
    hdr_r[5]  += vbat                # VBAT
    hdr_r[6]  += en_net              # EN
    hdr_r[7]  += vbus                # VBUS (5V)
    hdr_r[8]  += Net("GPIO_D5")
    hdr_r[9]  += Net("GPIO_D6")
    hdr_r[10] += Net("GPIO_D9")
    hdr_r[11] += Net("GPIO_D10")
    hdr_r[12] += Net("GPIO_D11")
    hdr_r[13] += Net("GPIO_D12")
    hdr_r[14] += Net("GPIO_D13")
    hdr_r[15] += Net("UART_TX")
    hdr_r[16] += Net("UART_RX")

feather_headers(vbus, vbat, v3v3, gnd, esp_en, sda, scl)


@subcircuit
def reset_boot_buttons(en_net, gnd, v3v3):
    """Reset and BOOT tactile buttons."""
    # Reset button (pulls EN low)
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_rst[1] += en_net
    sw_rst[2] += gnd

    # BOOT button (pulls IO0 low for firmware flash mode)
    boot_net = Net("BOOT_BTN")
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2")
    sw_boot[1] += boot_net
    sw_boot[2] += gnd

reset_boot_buttons(esp_en, gnd, v3v3)

# Feather form factor: 50.8x22.86mm
# NOTE: WROOM-1 courtyard is 48x42mm (includes antenna zone), too large for fixed position.
# The module uses castellated edges and conventionally overhangs the PCB edge.
# Letting the engine place U3 without fixed position to avoid courtyard violations.
EDA_FLOORPLAN = {
    "board_outline": {"width_mm": 50.8, "height_mm": 22.86},
    "edge_anchors": [
        {"ref": "J1", "edge": "left"},    # USB-C on left
        {"ref": "J2", "edge": "right"},   # JST battery on right
        {"ref": "J3", "edge": "bottom"},  # Feather header bottom
        {"ref": "J4", "edge": "top"},     # Feather header top
    ],
    "large_module_refs": ["U3"],
}
