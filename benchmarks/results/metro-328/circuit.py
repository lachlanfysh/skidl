"""
Metro 328 — Arduino Uno-compatible development board.

ATmega328P in DIP-28 socket. CH340G USB-UART (LCSC C14267, SOIC-16).
AMS1117-5.0 (DC jack → 5V) + AMS1117-3.3 (5V → 3.3V).
16 MHz crystal. Micro-USB. DC barrel jack. 500 mA polyfuse.
Arduino-standard headers. ICSP 2x3. Reset button.
Power LED + user LED (D13) + TX/RX LEDs.
~68x53 mm.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ── Power nets ─────────────────────────────────────────────────────────────
vin_raw  = Net("VIN");   vin_raw.drive = POWER
vusb     = Net("VBUS");  vusb.drive    = POWER
vcc      = Net("VCC");   vcc.drive     = POWER
v3v3     = Net("+3V3");  v3v3.drive    = POWER
gnd      = Net("GND");   gnd.drive     = POWER

# ── Signal nets ────────────────────────────────────────────────────────────
usb_dp   = Net("USB_DP")
usb_dm   = Net("USB_DM")
uart_tx  = Net("UART_TX")
uart_rx  = Net("UART_RX")
dtr_net  = Net("DTR")
reset_n  = Net("RESET")
xtal1    = Net("XTAL1")
xtal2    = Net("XTAL2")
sda      = Net("SDA")
scl      = Net("SCL")
mosi     = Net("MOSI")
miso     = Net("MISO")
sck_net  = Net("SCK")
tx_led_n = Net("TX_LED")
rx_led_n = Net("RX_LED")
d2 = Net("D2"); d3 = Net("D3"); d4 = Net("D4"); d5 = Net("D5")
d6 = Net("D6"); d7 = Net("D7"); d8 = Net("D8"); d9 = Net("D9")
d10 = Net("D10")
a0 = Net("A0"); a1 = Net("A1"); a2 = Net("A2")
a3 = Net("A3"); a4 = Net("A4"); a5 = Net("A5")


# ── Power supply ────────────────────────────────────────────────────────────
@subcircuit
def power_supply(vin_in, vusb_in, vcc_out, v3v3_out, gnd_in):
    # DC barrel jack
    j_dc = Part("Connector", "Barrel_Jack", value="DC_Jack_2.1mm",
                footprint="Connector_BarrelJack:BarrelJack_Horizontal")
    j_dc[1] += vin_in
    j_dc[2] += gnd_in

    # Schottky polarity protection on VIN
    d_pol = Part("Device", "D_Schottky", value="B5819W",
                 footprint="Diode_SMD:D_SOD-123")
    vin_prot = Net("VIN_PROT")
    d_pol["A"] += vin_in
    d_pol["K"] += vin_prot

    # AMS1117-5.0 — DC → 5V
    reg5 = Part("Regulator_Linear", "AMS1117-5.0",
                footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
    reg5.lcsc = "C6187"
    reg5["VI"]  += vin_prot
    reg5["VO"]  += vcc_out
    reg5["GND"] += gnd_in

    # AMS1117-5.0 input cap
    c_vin = Part("Device", "C_Polarized", value="100uF",
                 footprint="Capacitor_SMD:CP_Elec_6.3x7.7")
    c_vin[1] += vin_prot
    c_vin[2] += gnd_in

    # AMS1117-5.0 output decoupling
    c_vcc1 = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:CP_Elec_4x5.4")
    c_vcc1[1] += vcc_out
    c_vcc1[2] += gnd_in

    c_vcc2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcc2[1] += vcc_out
    c_vcc2[2] += gnd_in

    # AMS1117-3.3 — 5V → 3.3V
    reg33 = Part("Regulator_Linear", "AMS1117-3.3",
                 footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
    reg33.lcsc = "C347471"
    reg33["VI"]  += vcc_out
    reg33["VO"]  += v3v3_out
    reg33["GND"] += gnd_in

    c_33_1 = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:CP_Elec_4x5.4")
    c_33_1[1] += v3v3_out
    c_33_1[2] += gnd_in

    c_33_2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_33_2[1] += v3v3_out
    c_33_2[2] += gnd_in

    # 500 mA polyfuse on USB VBUS
    vbus_raw = Net("VBUS_IN")
    fuse = Part("Device", "Polyfuse", value="500mA",
                footprint="Fuse:Fuse_0805_2012Metric")
    fuse[1] += vbus_raw
    fuse[2] += vusb_in

    # USB power OR-ing diode — VCC takes whichever is higher
    d_usb = Part("Device", "D_Schottky", value="B5819W",
                 footprint="Diode_SMD:D_SOD-123")
    d_usb["A"] += vusb_in
    d_usb["K"] += vcc_out

    # USB Micro-B connector
    j_usb = Part("Connector", "USB_B_Micro", value="USB_Micro-B",
                 footprint="Connector_USB:USB_Micro-B_Amphenol_10118193-0001LF_Horizontal")
    j_usb["VBUS"]   += vbus_raw
    j_usb["D-"]     += usb_dm
    j_usb["D+"]     += usb_dp
    j_usb["GND"]    += gnd_in
    j_usb["Shield"] += gnd_in
    j_usb["ID"]     += gnd_in

power_supply(vin_raw, vusb, vcc, v3v3, gnd)


# ── CH340G USB-UART bridge ──────────────────────────────────────────────────
@subcircuit
def usb_uart(dp, dm, tx_out, rx_in, dtr_out, pwr, ref3v3, gnd_in,
             tx_led, rx_led):
    # CH340G pin names from LCSC C14267:
    # 1=GND, 2=TXD, 3=RXD, 4=V3, 5=UD+, 6=UD-, 7=XI, 8=XO,
    # 9=~{CTS}, 10=~{DSR}, 11=~{RI}, 12=~{DCD}, 13=~{DTR}, 14=~{RTS}, 15=R232, 16=VCC
    # CH340G via LCSC C14267; footprint substituted to standard KiCad SOIC-16
    # (server doesn't have C14267 EasyEDA footprint installed)
    ch340 = Part("C14267", "CH340G",
                 footprint="Package_SO:SOIC-16_3.9x9.9mm_P1.27mm")
    ch340.lcsc = "C14267"

    ch340["VCC"]    += pwr
    ch340["GND"]    += gnd_in
    ch340["V3"]     += ref3v3

    ch340["UD+"]    += dp
    ch340["UD-"]    += dm

    ch340["TXD"]    += rx_in      # CH340 TX → ATmega RX
    ch340["RXD"]    += tx_out     # CH340 RX ← ATmega TX

    ch340["~{DTR}"] += dtr_out

    # RTS/CTS used for TX/RX LEDs (active-low)
    ch340["~{RTS}"] += tx_led
    ch340["~{CTS}"] += rx_led

    # Unused handshake — tie high (inactive)
    ch340["~{DSR}"] += pwr
    ch340["~{DCD}"] += pwr
    ch340["~{RI}"]  += pwr
    ch340["R232"]   += gnd_in     # RS-232 level disabled

    # 12 MHz crystal (4-pin SMD: pins 1=osc, 2/4=GND, 3=osc)
    xi_net = Net("CH340_XI")
    xo_net = Net("CH340_XO")
    y_usb = Part("Device", "Crystal_GND24", value="12MHz",
                 footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm")
    y_usb[1] += xi_net
    y_usb[2] += gnd_in
    y_usb[3] += xo_net
    y_usb[4] += gnd_in
    ch340["XI"] += xi_net
    ch340["XO"] += xo_net

    c_xi = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xi[1] += xi_net
    c_xi[2] += gnd_in

    c_xo = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_xo[1] += xo_net
    c_xo[2] += gnd_in

    c_ch = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_ch[1] += pwr
    c_ch[2] += gnd_in

    c_v3 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_v3[1] += ref3v3
    c_v3[2] += gnd_in

usb_uart(usb_dp, usb_dm, uart_tx, uart_rx, dtr_net, vcc, v3v3, gnd,
         tx_led_n, rx_led_n)


# ── ATmega328P in DIP-28 socket ─────────────────────────────────────────────
@subcircuit
def atmega_mcu(pwr, gnd_in, rst, xt1, xt2,
               tx, rx, dtr,
               i2c_sda, i2c_scl, spi_mosi, spi_miso, spi_sck,
               pd2, pd3, pd4, pd5, pd6, pd7, pb0, pb1, pb2,
               pc0, pc1, pc2, pc3, pc4, pc5):

    mcu = Part("MCU_Microchip_ATmega", "ATmega328P-P",
               footprint="Package_DIP:DIP-28_W7.62mm")
    mcu.lcsc = "C601782"

    mcu["VCC"]           += pwr
    mcu["AVCC"]          += pwr
    mcu["GND"]           += gnd_in
    mcu["AREF"]          += NC

    mcu["XTAL1/PB6"]     += xt1
    mcu["XTAL2/PB7"]     += xt2

    mcu["PD0"]           += rx
    mcu["PD1"]           += tx
    mcu["~{RESET}/PC6"]  += rst

    mcu["PD2"]           += pd2
    mcu["PD3"]           += pd3
    mcu["PD4"]           += pd4
    mcu["PD5"]           += pd5
    mcu["PD6"]           += pd6
    mcu["PD7"]           += pd7
    mcu["PB0"]           += pb0
    mcu["PB1"]           += pb1
    mcu["PB2"]           += pb2

    mcu["PB3"]           += spi_mosi
    mcu["PB4"]           += spi_miso
    mcu["PB5"]           += spi_sck

    mcu["PC0"]           += pc0
    mcu["PC1"]           += pc1
    mcu["PC2"]           += pc2
    mcu["PC3"]           += pc3
    mcu["PC4"]           += pc4
    mcu["PC5"]           += pc5

    # A4/A5 also serve SDA/SCL
    pc4 += i2c_sda
    pc5 += i2c_scl

    # 16 MHz crystal
    y1 = Part("Device", "Crystal", value="16MHz",
              footprint="Crystal:Crystal_SMD_5032-2Pin_5.0x3.2mm")
    y1[1] += xt1
    y1[2] += xt2

    c_x1 = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_x1[1] += xt1
    c_x1[2] += gnd_in

    c_x2 = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_x2[1] += xt2
    c_x2[2] += gnd_in

    c_v1 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_v1[1] += pwr
    c_v1[2] += gnd_in

    c_v2 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_v2[1] += pwr
    c_v2[2] += gnd_in

    # Auto-reset: DTR → 100nF → RESET
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_rst[1] += dtr
    c_rst[2] += rst

    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += pwr
    r_rst[2] += rst

    sw_rst = Part("Switch", "SW_Push", value="RESET",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KSC6xxJ")
    sw_rst[1] += rst
    sw_rst[2] += gnd_in

atmega_mcu(vcc, gnd, reset_n, xtal1, xtal2,
           uart_tx, uart_rx, dtr_net,
           sda, scl, mosi, miso, sck_net,
           d2, d3, d4, d5, d6, d7, d8, d9, d10,
           a0, a1, a2, a3, a4, a5)


# ── Status LEDs ─────────────────────────────────────────────────────────────
@subcircuit
def status_leds(pwr, gnd_in, d13_net, tx_led, rx_led):
    # Power LED — always on
    led_pwr = Part("Device", "LED", value="Green",
                   footprint="LED_SMD:LED_0603_1608Metric")
    r_pwr = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_pwr[1] += pwr
    r_pwr[2] += led_pwr["A"]
    led_pwr["K"] += gnd_in

    # User LED on D13/SCK
    led_l = Part("Device", "LED", value="Yellow",
                 footprint="LED_SMD:LED_0603_1608Metric")
    r_l = Part("Device", "R", value="1K",
               footprint="Resistor_SMD:R_0603_1608Metric")
    r_l[1] += pwr
    r_l[2] += led_l["A"]
    led_l["K"] += d13_net

    # TX LED — active low
    led_tx = Part("Device", "LED", value="Green",
                  footprint="LED_SMD:LED_0603_1608Metric")
    r_tx = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_tx[1] += pwr
    r_tx[2] += led_tx["A"]
    led_tx["K"] += tx_led

    # RX LED — active low
    led_rx = Part("Device", "LED", value="Green",
                  footprint="LED_SMD:LED_0603_1608Metric")
    r_rx = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_rx[1] += pwr
    r_rx[2] += led_rx["A"]
    led_rx["K"] += rx_led

status_leds(vcc, gnd, sck_net, tx_led_n, rx_led_n)


# ── Arduino shield headers ──────────────────────────────────────────────────
@subcircuit
def shield_headers(pwr, ref3v3, gnd_in, rst, vin,
                   pd2, pd3, pd4, pd5, pd6, pd7, pb0, pb1, pb2,
                   d0, d1, spi_mosi, spi_miso, spi_sck,
                   pc0, pc1, pc2, pc3, pc4, pc5, i2c_sda, i2c_scl):

    # Power header 8-pin: IOREF, RESET, 3V3, 5V, GND, GND, VIN, NC
    j_pwr = Part("Connector_Generic", "Conn_01x08", value="PWR_HDR",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_pwr[1] += pwr     # IOREF = 5V
    j_pwr[2] += rst
    j_pwr[3] += ref3v3
    j_pwr[4] += pwr
    j_pwr[5] += gnd_in
    j_pwr[6] += gnd_in
    j_pwr[7] += vin
    j_pwr[8] += NC

    # Analog header A0-A5
    j_ana = Part("Connector_Generic", "Conn_01x06", value="ANALOG_HDR",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical")
    j_ana[1] += pc0
    j_ana[2] += pc1
    j_ana[3] += pc2
    j_ana[4] += pc3
    j_ana[5] += pc4
    j_ana[6] += pc5

    # Digital low header D0-D7
    j_dlo = Part("Connector_Generic", "Conn_01x08", value="DIG_LO_HDR",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_dlo[1] += d0      # D0/RXD
    j_dlo[2] += d1      # D1/TXD
    j_dlo[3] += pd2
    j_dlo[4] += pd3
    j_dlo[5] += pd4
    j_dlo[6] += pd5
    j_dlo[7] += pd6
    j_dlo[8] += pd7

    # Digital high header D8-D13 + GND + AREF + SDA + SCL
    j_dhi = Part("Connector_Generic", "Conn_01x10", value="DIG_HI_HDR",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x10_P2.54mm_Vertical")
    j_dhi[1]  += pb0
    j_dhi[2]  += pb1
    j_dhi[3]  += pb2
    j_dhi[4]  += spi_mosi   # D11
    j_dhi[5]  += spi_miso   # D12
    j_dhi[6]  += spi_sck    # D13
    j_dhi[7]  += gnd_in
    j_dhi[8]  += NC          # AREF
    j_dhi[9]  += i2c_sda
    j_dhi[10] += i2c_scl

shield_headers(vcc, v3v3, gnd, reset_n, vin_raw,
               d2, d3, d4, d5, d6, d7, d8, d9, d10,
               uart_rx, uart_tx, mosi, miso, sck_net,
               a0, a1, a2, a3, a4, a5, sda, scl)


# ── ICSP header ─────────────────────────────────────────────────────────────
@subcircuit
def icsp_header(pwr, gnd_in, rst, spi_mosi, spi_miso, spi_sck):
    j_icsp = Part("Connector_Generic", "Conn_02x03_Odd_Even", value="ICSP",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical")
    j_icsp[1] += spi_miso
    j_icsp[2] += pwr
    j_icsp[3] += spi_sck
    j_icsp[4] += spi_mosi
    j_icsp[5] += rst
    j_icsp[6] += gnd_in

icsp_header(vcc, gnd, reset_n, mosi, miso, sck_net)


# ── Floorplan ───────────────────────────────────────────────────────────────
# Metro 328 (Arduino Uno compatible, ~70x55mm):
# Ref assignment order: J1=DC barrel, J2=USB Micro-B, J3=PWR_HDR,
# J4=ANALOG_HDR, J5=DIG_LO, J6=DIG_HI, J7=ICSP, U1=AMS1117-5.0,
# U2=AMS1117-3.3, U3=ATmega328P-P, U4=CH340G
EDA_FLOORPLAN = {
    "outline": {"width_mm": 75, "height_mm": 56},
    "edge_anchors": [
        # Right edge: DC jack top, USB below it
        {"ref": "J1", "edge": "right", "offset_mm": 12},
        {"ref": "J2", "edge": "right", "offset_mm": 36},
        # Top edge: power header at left, analog header to the right
        {"ref": "J3", "edge": "top",   "offset_mm": 10},
        {"ref": "J4", "edge": "top",   "offset_mm": 50},
        # Bottom edge: two digital headers
        {"ref": "J5", "edge": "bottom","offset_mm": 10},
        {"ref": "J6", "edge": "bottom","offset_mm": 43},
    ],
}
