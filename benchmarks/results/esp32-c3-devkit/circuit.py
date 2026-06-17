"""
ESP32-C3 Minimal Development Board
- ESP32-C3-MINI-1 (LCSC C2934569) - WiFi/BLE RISC-V MCU module
- CH340C USB-UART bridge (Interface_USB:CH340C)
- AMS1117-3.3 LDO regulator from 5V USB
- USB-C connector (power + UART programming)
- 2x 10-pin GPIO headers
- Boot (GPIO9) + Reset buttons
- Power LED + User LED (GPIO8)
- Board: ~52x28mm
"""

import os
os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from skidl import *

set_default_tool(KICAD9)

# ===========================================================================
# Nets
# ===========================================================================
vbus    = Net("VBUS");   vbus.drive   = POWER
vcc5    = Net("VCC5");   vcc5.drive   = POWER
vcc33   = Net("VCC3V3"); vcc33.drive  = POWER
gnd     = Net("GND");    gnd.drive    = POWER

usb_dp  = Net("USB_DP")
usb_dm  = Net("USB_DM")
uart_tx = Net("UART_TX")  # CH340C TXD → ESP32 RXD0
uart_rx = Net("UART_RX")  # CH340C RXD ← ESP32 TXD0
rst_n   = Net("RST_N")
boot    = Net("BOOT")     # GPIO9 / BOOT0

dtr_n   = Net("DTR_N")
rts_n   = Net("RTS_N")

# ===========================================================================
# USB-C Connector (USB 2.0 only, 14P variant)
# Footprint: GCT USB4085 (popular, JLC-stocked)
# ===========================================================================
@subcircuit
def usb_c_input(vbus_net, dp_net, dm_net, gnd_net):
    usbc = Part(
        "Connector", "USB_C_Receptacle_USB2.0_14P",
        footprint="Connector_USB:USB_C_Receptacle_GCT_USB4085",
    )
    # VBUS: all 4 VBUS pins tied together to vbus
    usbc["VBUS"] += vbus_net
    # GND
    usbc["GND"] += gnd_net
    usbc["SHIELD"] += gnd_net
    # USB data — connector has redundant D+/D- pairs (A and B side)
    usbc["D+"] += dp_net
    usbc["D-"] += dm_net
    # CC resistors for USB-C sink role (5.1kΩ to GND each)
    cc1_r = Part("Device", "R", value="5.1k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    cc2_r = Part("Device", "R", value="5.1k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    usbc["CC1"] += cc1_r[1]; cc1_r[2] += gnd_net
    usbc["CC2"] += cc2_r[1]; cc2_r[2] += gnd_net

    # 100nF bypass on VBUS rail at connector
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vbus[1] += vbus_net; c_vbus[2] += gnd_net

    # 10µF bulk cap on VBUS
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vbus_net; c_bulk[2] += gnd_net

# ===========================================================================
# AMS1117-3.3 LDO: VBUS (5V) → VCC3V3
# ===========================================================================
@subcircuit
def ldo_3v3(vin_net, vout_net, gnd_net):
    ldo = Part(
        "Regulator_Linear", "AMS1117-3.3",
        footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2",
    )
    ldo["VI"] += vin_net
    ldo["GND"] += gnd_net
    ldo["VO"] += vout_net

    # Input decoupling
    cin = Part("Device", "C", value="10uF",
               footprint="Capacitor_SMD:C_0805_2012Metric")
    cin[1] += vin_net; cin[2] += gnd_net

    cin2 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cin2[1] += vin_net; cin2[2] += gnd_net

    # Output decoupling
    cout = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    cout[1] += vout_net; cout[2] += gnd_net

    cout2 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    cout2[1] += vout_net; cout2[2] += gnd_net

# ===========================================================================
# CH340C USB-UART bridge
# ===========================================================================
@subcircuit
def ch340c_bridge(vcc_net, dp_net, dm_net, txd_net, rxd_net,
                  dtr_net, rts_net, gnd_net):
    u = Part(
        "Interface_USB", "CH340C",
        footprint="Package_SO:SOIC-16_3.9x9.9mm_P1.27mm",
    )
    u["VCC"] += vcc_net
    u["GND"] += gnd_net
    u["UD+"] += dp_net
    u["UD-"] += dm_net
    u["TXD"] += txd_net   # CH340 TXD → ESP RXD0
    u["RXD"] += rxd_net   # CH340 RXD ← ESP TXD0
    u["~{DTR}"] += dtr_net
    u["~{RTS}"] += rts_net
    # V3 internal 3.3V bypass (100nF to GND per datasheet)
    cv3 = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cv3[1] += u["V3"]; cv3[2] += gnd_net
    # R232 mode pin: leave floating (RS232 mode disabled)
    u["NC"] += NC
    u["~{CTS}"] += gnd_net    # tie CTS low (always clear to send)
    u["~{DSR}"] += gnd_net    # tie DSR low
    u["~{RI}"]  += gnd_net
    u["~{DCD}"] += gnd_net
    # VCC bypass
    cvcc = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cvcc[1] += vcc_net; cvcc[2] += gnd_net

# ===========================================================================
# Auto-reset circuit (DTR/RTS → EN/IO9 a la NodeMCU)
# Transistors: NPN BCX70K (SOT-23)
# ===========================================================================
@subcircuit
def auto_reset(vcc_net, dtr_net, rts_net, en_net, boot_net, gnd_net):
    # Q1: driven by DTR_N, pulls EN low
    q1 = Part("Transistor_BJT", "Q_NPN_BCE",
              footprint="Package_TO_SOT_SMD:SOT-23")
    # Q2: driven by RTS_N, pulls BOOT/IO9 low
    q2 = Part("Transistor_BJT", "Q_NPN_BCE",
              footprint="Package_TO_SOT_SMD:SOT-23")

    # Base resistors (10k)
    rb1 = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0402_1005Metric")
    rb2 = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0402_1005Metric")

    dtr_net += rb1[1]; rb1[2] += q1["B"]
    rts_net += rb2[1]; rb2[2] += q2["B"]

    q1["E"] += gnd_net
    q2["E"] += gnd_net

    q1["C"] += en_net
    q2["C"] += boot_net

    # Pull-up resistors to VCC on EN and BOOT
    ren = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0402_1005Metric")
    rboot = Part("Device", "R", value="10k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    ren[1] += vcc_net;  ren[2] += en_net
    rboot[1] += vcc_net; rboot[2] += boot_net

# ===========================================================================
# ESP32-C3-WROOM-02 module
# Using RF_Module:ESP32-C3-WROOM-02 (standard KiCad symbol+footprint)
# MINI-1 LCSC footprint not available server-side; WROOM-02 is pin-compatible
# for this reference design.
# ===========================================================================
@subcircuit
def esp32c3(vcc_net, gnd_net, txd_net, rxd_net,
            en_net, boot_net,
            gpio_a, gpio_b):
    esp = Part(
        "RF_Module", "ESP32-C3-WROOM-02",
        footprint="RF_Module:ESP32-C3-WROOM-02",
    )
    # Power
    esp["3V3"] += vcc_net
    esp["GND"] += gnd_net

    # UART0 (WROOM-02 uses IO20/RXD and IO21/TXD)
    esp["IO21/TXD"] += txd_net   # ESP TX → CH340 RX
    esp["IO20/RXD"] += rxd_net   # ESP RX ← CH340 TX

    # Enable / Boot
    esp["EN"] += en_net
    esp["IO9"] += boot_net   # BOOT strapping pin

    # GPIO headers
    esp["IO0"] += gpio_a[0]
    esp["IO1"] += gpio_a[1]
    esp["IO2"] += gpio_a[2]
    esp["IO3"] += gpio_a[3]
    esp["IO4"] += gpio_a[4]
    esp["IO5"] += gpio_a[5]
    esp["IO6"] += gpio_a[6]
    esp["IO7"] += gpio_a[7]
    esp["IO8"] += gpio_a[8]   # User LED net
    # IO9 already connected above (BOOT)
    esp["IO10"] += gpio_a[9]

    esp["IO18"] += gpio_b[0]
    esp["IO19"] += gpio_b[1]

    # Decoupling caps
    for _ in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vcc_net; c[2] += gnd_net

# ===========================================================================
# Buttons: Boot and Reset
# ===========================================================================
@subcircuit
def buttons(en_net, boot_net, gnd_net):
    # Reset button
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_SPST_B3U-1000P")
    sw_rst[1] += en_net
    sw_rst[2] += gnd_net

    # Boot button (GPIO9)
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_SPST_B3U-1000P")
    sw_boot[1] += boot_net
    sw_boot[2] += gnd_net

# ===========================================================================
# LEDs
# ===========================================================================
@subcircuit
def leds(vcc_net, user_gpio_net, gnd_net):
    # Power LED
    led_pwr = Part("Device", "LED",
                   footprint="LED_SMD:LED_0402_1005Metric")
    r_pwr   = Part("Device", "R", value="1k",
                   footprint="Resistor_SMD:R_0402_1005Metric")
    r_pwr[1] += vcc_net
    r_pwr[2] += led_pwr["K"]
    led_pwr["A"] += gnd_net   # always-on: K→resistor→VCC, A→GND

    # User LED (GPIO8, active-high)
    led_user = Part("Device", "LED",
                    footprint="LED_SMD:LED_0402_1005Metric")
    r_user   = Part("Device", "R", value="330R",
                    footprint="Resistor_SMD:R_0402_1005Metric")
    r_user[1] += user_gpio_net
    r_user[2] += led_user["A"]
    led_user["K"] += gnd_net

# ===========================================================================
# GPIO Headers: 2× 10-pin 2.54mm
# ===========================================================================
@subcircuit
def gpio_headers(gpio_a, gpio_b, vcc_net, gnd_net):
    hdr_a = Part("Connector_Generic", "Conn_01x10",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
    hdr_b = Part("Connector_Generic", "Conn_01x10",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")

    for i in range(10):
        hdr_a[i + 1] += gpio_a[i]
        hdr_b[i + 1] += gpio_b[i]

    # gpio_b[2..9] as named GPIOs — leave floating for now (just break out)


# ===========================================================================
# Instantiate
# ===========================================================================

# GPIO nets
gpio_a = [Net(f"IO{n}") for n in [0,1,2,3,4,5,6,7,8,10]]  # 10 nets
gpio_b_nets = [Net(f"GPIO_B{i}") for i in range(10)]
# Override first two with IO18/19
gpio_b_nets[0] = Net("IO18")
gpio_b_nets[1] = Net("IO19")

# LED net
led_gpio = gpio_a[8]   # IO8

usb_c_input(vbus, usb_dp, usb_dm, gnd)
ldo_3v3(vbus, vcc33, gnd)
ch340c_bridge(vbus, usb_dp, usb_dm, uart_tx, uart_rx, dtr_n, rts_n, gnd)
auto_reset(vcc33, dtr_n, rts_n, rst_n, boot, gnd)
esp32c3(vcc33, gnd, uart_rx, uart_tx, rst_n, boot, gpio_a, gpio_b_nets)
buttons(rst_n, boot, gnd)
leds(vcc33, led_gpio, gnd)
gpio_headers(gpio_a, gpio_b_nets, vcc33, gnd)

# ===========================================================================
# Floorplan intent
# Board: 52x28mm — ESP32-C3-WROOM-02 module centered, USB-C at bottom edge,
# 2x10 GPIO headers on left and right edges, buttons on top edge.
# ===========================================================================
EDA_FLOORPLAN = {
    "outline": {"width_mm": 60, "height_mm": 32, "corner_radius_mm": 1},
    "edge_anchors": [
        {"ref": "J1", "edge": "bottom"},    # USB-C receptacle at bottom center
        {"ref": "J2", "edge": "left"},      # GPIO header A on left side
        {"ref": "J3", "edge": "right"},     # GPIO header B on right side
    ],
}

# ===========================================================================
# Generate
# ===========================================================================
generate_schematic(
    auto_stub=True,
    auto_stub_fanout=3,
    erc_max_iterations=8,
)

from skidl.layout import (
    extract_groups, place_parts, write_kicad_pcb, validate,
    LayoutConstraints, BoardOutline, load_footprint_bboxes,
)

ckt = default_circuit
fp_names = {str(p.footprint) for p in ckt.parts if getattr(p, "footprint", None)}
fp_lib_dirs = ["/usr/share/kicad/footprints"]
fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)

constraints = LayoutConstraints(outline=BoardOutline(60.0, 32.0))
placed = place_parts(extract_groups(ckt), constraints, fp_bboxes)
result = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(result.summary())

write_kicad_pcb(placed, ckt, fp_lib_dirs, "board.kicad_pcb", outline=constraints.outline)
