from skidl import *

# ============================================================
# ESP32 IoT Gateway Board v2
# ESP32-WROOM-32 + CP2102N USB-UART (QFN-24) + AMS1117-3.3 LDO
# Auto-reset (DTR/RTS + 2x MMBT2222A) + Status LED
# USB-C 16P, Qwiic/STEMMA QT (JST-SH 4-pin), 2x1x10 GPIO headers
# 68x50mm 2-layer board
# ============================================================

# Power rails
vbus  = Net("VBUS");  vbus.drive  = POWER
v3v3  = Net("3V3");   v3v3.drive  = POWER
gnd   = Net("GND");   gnd.drive   = POWER

# Signal nets
uart_tx  = Net("UART_TX")
uart_rx  = Net("UART_RX")
uart_rts = Net("UART_RTS")
uart_dtr = Net("UART_DTR")
esp_en   = Net("ESP_EN")
esp_io0  = Net("ESP_IO0")
usb_dp   = Net("USB_DP")
usb_dm   = Net("USB_DM")
i2c_sda  = Net("I2C_SDA")
i2c_scl  = Net("I2C_SCL")
led_io   = Net("LED_IO2")


# ── USB-C Receptacle (bottom edge) ───────────────────────────
@subcircuit
def usb_c_input(vbus_net, gnd_net, dp_net, dm_net):
    usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
               footprint="Connector_USB:USB_C_Receptacle_GCT_USB4105-xx-A_16P_TopMnt_Horizontal")
    usb.edge_preference = "bottom"

    vbus_net += usb["VBUS"]
    gnd_net  += usb["GND"], usb["SHIELD"]
    dp_net   += usb["D+"]
    dm_net   += usb["D-"]

    # CC pull-downs for USB-C sink
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += usb["CC1"]
    r_cc1[2] += gnd_net
    r_cc2[1] += usb["CC2"]
    r_cc2[2] += gnd_net

    # SBU floating
    usb["SBU1"] += Net("NC_SBU1")
    usb["SBU2"] += Net("NC_SBU2")

    # VBUS bulk cap
    c_vbus = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_vbus[1] += vbus_net
    c_vbus[2] += gnd_net

usb_c_input(vbus, gnd, usb_dp, usb_dm)


# ── AMS1117-3.3 LDO (VBUS → 3V3) ────────────────────────────
@subcircuit
def ldo_3v3(vin_net, vout_net, gnd_net):
    u = Part("Regulator_Linear", "AMS1117-3.3",
             footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
    vin_net  += u["VI"]
    vout_net += u["VO"]
    gnd_net  += u["GND"]

    c_in = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin_net
    c_in[2] += gnd_net

    c_out1 = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out1[1] += vout_net
    c_out1[2] += gnd_net
    c_out2[1] += vout_net
    c_out2[2] += gnd_net

ldo_3v3(vbus, v3v3, gnd)


# ── CP2102N USB-UART bridge (QFN-24 for DTR) ─────────────────
@subcircuit
def cp2102n_bridge(vbus_net, v3v3_net, gnd_net, dp_net, dm_net,
                   tx_net, rx_net, rts_net, dtr_net):
    u = Part("Interface_USB", "CP2102N-Axx-xQFN24",
             footprint="Package_DFN_QFN:QFN-24-1EP_4x4mm_P0.5mm_EP2.6x2.6mm")

    # Power: VREGIN from VBUS (internal USB regulator), VDD/VIO from 3V3
    vbus_net += u["VREGIN"], u["VBUS"]
    v3v3_net += u["VDD"], u["VIO"]
    gnd_net  += u["GND"]

    # USB data
    dp_net += u["D+"]
    dm_net += u["D-"]

    # UART
    tx_net  += u["TXD"]
    rx_net  += u["RXD"]
    rts_net += u["~{RTS}"]
    dtr_net += u["~{DTR}"]

    # Unused flow-control and GPIO pins — connect to avoid floating
    u["~{CTS}"]          += gnd_net       # always clear to send
    u["~{DSR}"]          += v3v3_net      # data set ready
    u["~{DCD}"]          += v3v3_net      # carrier detect
    u["~{RI}/CLK"]       += Net("NC_RI")  # ring indicator NC
    u["~{TXT}/GPIO.0"]   += Net("NC_TXT")
    u["~{RXT}/GPIO.1"]   += Net("NC_RXT")
    u["RS485/GPIO.2"]    += Net("NC_RS485")
    u["~{WAKEUP}/GPIO.3"] += v3v3_net     # pull high: no remote wakeup

    # Decoupling caps
    for _ in range(2):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        v3v3_net += c[1]
        gnd_net  += c[2]
    c_bulk = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    v3v3_net += c_bulk[1]
    gnd_net  += c_bulk[2]

cp2102n_bridge(vbus, v3v3, gnd, usb_dp, usb_dm,
               uart_tx, uart_rx, uart_rts, uart_dtr)


# ── Auto-reset circuit ────────────────────────────────────────
# DTR → Q1 base → couples EN pulse via 100nF cap
# RTS → Q2 base → couples IO0 pulse via 100nF cap
@subcircuit
def auto_reset(v3v3_net, gnd_net, rts_net, dtr_net, en_net, io0_net):
    q1 = Part("Transistor_BJT", "MMBT2222A",
              footprint="Package_TO_SOT_SMD:SOT-23")
    q2 = Part("Transistor_BJT", "MMBT2222A",
              footprint="Package_TO_SOT_SMD:SOT-23")

    r_b1 = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_b2 = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")

    c_en  = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_io0 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")

    # Q1: DTR → EN via cap
    dtr_net += r_b1[1]
    r_b1[2] += q1["B"]
    q1["E"] += gnd_net
    mid_en = Net("AUTORST_EN_MID")
    q1["C"] += mid_en
    c_en[1] += mid_en
    c_en[2] += en_net

    # Q2: RTS → IO0 via cap
    rts_net += r_b2[1]
    r_b2[2] += q2["B"]
    q2["E"] += gnd_net
    mid_io0 = Net("AUTORST_IO0_MID")
    q2["C"] += mid_io0
    c_io0[1] += mid_io0
    c_io0[2] += io0_net

    # Pull-ups on EN and IO0
    r_en  = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_io0 = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    v3v3_net += r_en[1]
    r_en[2]  += en_net
    v3v3_net += r_io0[1]
    r_io0[2] += io0_net

auto_reset(v3v3, gnd, uart_rts, uart_dtr, esp_en, esp_io0)


# ── ESP32-WROOM-32 module ─────────────────────────────────────
@subcircuit
def esp32_module(v3v3_net, gnd_net, en_net, io0_net,
                 tx_net, rx_net, sda_net, scl_net, led_net):
    esp = Part("RF_Module", "ESP32-WROOM-32",
               footprint="RF_Module:ESP32-WROOM-32")

    v3v3_net += esp["VDD"]
    gnd_net  += esp["GND"]
    en_net   += esp["EN"]
    io0_net  += esp["IO0"]

    # UART: TXD0/IO1 = ESP TX → CP RXD; RXD0/IO3 = ESP RX ← CP TXD
    rx_net += esp["TXD0/IO1"]
    tx_net += esp["RXD0/IO3"]

    # I2C on IO21/IO22
    sda_net += esp["IO21"]
    scl_net += esp["IO22"]

    # Status LED on IO2
    led_net += esp["IO2"]

    # Decoupling
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2 = Part("Device", "C_Polarized", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    v3v3_net += c1[1], c2[1]
    gnd_net  += c1[2], c2[2]

    # I2C pull-ups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    v3v3_net += r_sda[1], r_scl[1]
    r_sda[2] += sda_net
    r_scl[2] += scl_net

    # GPIO header 1 (right edge): 3V3, GND, IO4, IO5, IO12..IO17
    h1 = Part("Connector_Generic", "Conn_01x10",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
    h1.edge_preference = "right"
    v3v3_net += h1[1]
    gnd_net  += h1[2]
    h1[3]    += esp["IO4"]
    h1[4]    += esp["IO5"]
    h1[5]    += esp["IO12"]
    h1[6]    += esp["IO13"]
    h1[7]    += esp["IO14"]
    h1[8]    += esp["IO15"]
    h1[9]    += esp["IO16"]
    h1[10]   += esp["IO17"]

    # GPIO header 2 (right edge): 3V3, GND, IO18, IO19, IO25..IO33
    h2 = Part("Connector_Generic", "Conn_01x10",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
    h2.edge_preference = "right"
    v3v3_net += h2[1]
    gnd_net  += h2[2]
    h2[3]    += esp["IO18"]
    h2[4]    += esp["IO19"]
    h2[5]    += esp["IO25"]
    h2[6]    += esp["IO26"]
    h2[7]    += esp["IO27"]
    h2[8]    += esp["IO32"]
    h2[9]    += esp["IO33"]
    h2[10]   += esp["IO34"]

esp32_module(v3v3, gnd, esp_en, esp_io0,
             uart_tx, uart_rx, i2c_sda, i2c_scl, led_io)


# ── Boot button (holds IO0 low during reset = flash mode) ─────
btn_boot = Part("Switch", "SW_Push",
                footprint="Button_Switch_SMD:SW_SPST_PTS645")
btn_boot[1] += esp_io0
btn_boot[2] += gnd

# ── Reset button (pulls EN low) ───────────────────────────────
btn_rst = Part("Switch", "SW_Push",
               footprint="Button_Switch_SMD:SW_SPST_PTS645")
btn_rst[1] += esp_en
btn_rst[2] += gnd

# ── Status LED (IO2 → 330R → LED → GND) ──────────────────────
r_led = Part("Device", "R", value="330R",
             footprint="Resistor_SMD:R_0402_1005Metric")
led_status = Part("Device", "LED",
                  footprint="LED_SMD:LED_0402_1005Metric")
led_io += r_led[1]
r_led[2] += led_status["A"]
led_status["K"] += gnd

# ── STEMMA QT / Qwiic (JST-SH 4-pin, top edge) ───────────────
qwiic = Part("Connector_Generic", "Conn_01x04",
             footprint="Connector_JST:JST_SH_BM04B-SRSS-TB_1x04-1MP_P1.00mm_Vertical")
qwiic.edge_preference = "top"
# Qwiic standard: pin1=GND, pin2=3V3, pin3=SDA, pin4=SCL
qwiic[1] += gnd
qwiic[2] += v3v3
qwiic[3] += i2c_sda
qwiic[4] += i2c_scl

# ── Mounting holes (4x M3) ────────────────────────────────────
for _ in range(4):
    mh = Part("Mechanical", "MountingHole",
              footprint="MountingHole:MountingHole_3.2mm_M3")

# ── Board floorplan ───────────────────────────────────────────
# Rely on part.edge_preference for connectors; just set outline here.
EDA_FLOORPLAN = {
    "outline": {"width_mm": 70, "height_mm": 55, "corner_radius_mm": 2},
}
