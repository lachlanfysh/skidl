"""Arduino Leonardo clone — ATmega32U4 TQFP-44, native USB, 68x53mm form factor."""

from skidl import *

# ── Power rails ──────────────────────────────────────────────────────────────
vcc_5v   = Net("VCC");    vcc_5v.drive  = POWER
vcc_3v3  = Net("+3.3V");  vcc_3v3.drive = POWER
gnd      = Net("GND");    gnd.drive     = POWER
vbus     = Net("VBUS");   vbus.drive    = POWER
dp       = Net("USB_DP")
dm       = Net("USB_DM")
ucap     = Net("UCAP")

# ── Micro USB connector ───────────────────────────────────────────────────────
usb = Part("Connector", "USB_B_Micro",
           footprint="Connector_USB:USB_Micro-B_Molex-105017-0001")
usb.edge_preference = "bottom"
vbus    += usb["VBUS"]
dm      += usb["D-"]
dp      += usb["D+"]
gnd     += usb["GND"], usb["Shield"], usb["ID"]

# ── Polyfuse 500mA on VBUS ───────────────────────────────────────────────────
pf1 = Part("Device", "Polyfuse",
           value="500mA",
           footprint="Fuse:Fuse_1812_4532Metric")
vbus    += pf1[1]
vbus_sw  = Net("VBUS_SW")
vbus_sw += pf1[2]

# ── NCP1117-5.0 5V regulator (powers board from 7-12V VIN header) ────────────
# Input is VBUS_SW (5V from USB) — on Leonardo the ATmega's VCC is 5V from USB
# NCP1117 here used as optional barrel-jack path (vin header → 5V)
# On a real Leonardo, VIN goes through NCP1117 to VCC when not using USB.
vin_net  = Net("VIN");  vin_net.drive = POWER
u_reg5  = Part("Regulator_Linear", "NCP1117-5.0_SOT223",
               footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
vin_net += u_reg5["VI"]
vcc_5v  += u_reg5["VO"]
gnd     += u_reg5["GND"]

# Decoupling caps for NCP1117
c_reg5_in  = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_1206_3216Metric")
c_reg5_out = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_1206_3216Metric")
vin_net += c_reg5_in[1];  gnd += c_reg5_in[2]
vcc_5v  += c_reg5_out[1]; gnd += c_reg5_out[2]

# Also feed 5V rail from VBUS via Schottky diode (OR-diode power mux)
# (VBUS_SW → D_VBUS → VCC) — simplified: connect VBUS_SW to VCC_5V via diode
d_vbus = Part("Device", "D_Schottky",
              value="B5819W",
              footprint="Diode_SMD:D_SOD-123")
vbus_sw  += d_vbus["A"]
vcc_5v   += d_vbus["K"]

# ── AP2112K-3.3 3.3V LDO ─────────────────────────────────────────────────────
u_reg33 = Part("Regulator_Linear", "AP2112K-3.3",
                footprint="Package_TO_SOT_SMD:SOT-23-5")
vcc_5v  += u_reg33["VIN"]
vcc_3v3 += u_reg33["VOUT"]
gnd     += u_reg33["GND"]
vcc_5v  += u_reg33["EN"]   # tie EN to VIN to always-on
u_reg33[4] += NC

c_33_in  = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
c_33_out = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
vcc_5v  += c_33_in[1];  gnd += c_33_in[2]
vcc_3v3 += c_33_out[1]; gnd += c_33_out[2]

# ── ATmega32U4 ────────────────────────────────────────────────────────────────
mcu = Part("MCU_Microchip_ATmega", "ATmega32U4-A",
           footprint="Package_QFP:TQFP-44_10x10mm_P0.8mm")

# Power
vcc_5v += mcu["VCC"], mcu["AVCC"]
gnd    += mcu["GND"], mcu["UGND"]
vcc_5v += mcu["UVCC"]   # USB transceiver supply (5V on Leonardo)

# USB lines
dp    += mcu["D+"]
dm    += mcu["D-"]
vbus  += mcu["VBUS"]
ucap  += mcu["UCAP"]

# UCAP decoupling cap (1uF per datasheet)
c_ucap = Part("Device", "C", value="1uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
ucap += c_ucap[1]
gnd  += c_ucap[2]

# AREF decoupling
c_aref = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
mcu["AREF"] += c_aref[1]
gnd          += c_aref[2]

# VCC bulk decoupling caps (4 × 100nF per power pin)
for _ in range(4):
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
    vcc_5v += c[1]
    gnd    += c[2]

# ── 16 MHz Crystal ────────────────────────────────────────────────────────────
# Crystal_GND23 = 4-pin symbol (pins 1,2,3,4). HC49-SD is a 4-pad SMD footprint.
xtal = Part("Device", "Crystal_GND23",
            value="16MHz",
            footprint="Crystal:Crystal_SMD_HC49-SD")
mcu["XTAL1"]  += xtal[1]
mcu["XTAL2"]  += xtal[4]
gnd           += xtal[2], xtal[3]

# Crystal load caps (22pF)
c_xtal1 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
c_xtal2 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
mcu["XTAL1"] += c_xtal1[1]; gnd += c_xtal1[2]
mcu["XTAL2"] += c_xtal2[1]; gnd += c_xtal2[2]

# ── RESET circuit ─────────────────────────────────────────────────────────────
reset_net = Net("RESET")
mcu["~{RESET}"] += reset_net

# Pull-up resistor 10k
r_reset = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0603_1608Metric")
vcc_5v    += r_reset[1]
reset_net += r_reset[2]

# Reset button
sw_reset = Part("Switch", "SW_Push",
                footprint="Button_Switch_THT:SW_PUSH_6mm")
reset_net += sw_reset[1]
gnd       += sw_reset[2]

# Reset cap (100nF)
c_reset = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
reset_net += c_reset[1]
gnd       += c_reset[2]

# ── HWB resistor (bootloader) ─────────────────────────────────────────────────
r_hwb = Part("Device", "R", value="10k",
             footprint="Resistor_SMD:R_0603_1608Metric")
vcc_5v            += r_hwb[1]
mcu["~{HWB}/PE2"] += r_hwb[2]

# ── LEDs ─────────────────────────────────────────────────────────────────────
def add_led(net_name, gpio_pin, color="green"):
    led_net = Net(net_name)
    led = Part("Device", "LED", value=color,
               footprint="LED_SMD:LED_0603_1608Metric")
    r   = Part("Device", "R", value="1k",
               footprint="Resistor_SMD:R_0603_1608Metric")
    led_net += led["A"]
    r[1]    += led["K"]
    gnd     += r[2]
    mcu[gpio_pin] += led_net
    return led_net

# Power LED (always on — tied to VCC via resistor, no GPIO)
led_pwr = Part("Device", "LED", value="green",
               footprint="LED_SMD:LED_0603_1608Metric")
r_pwr   = Part("Device", "R", value="1k",
               footprint="Resistor_SMD:R_0603_1608Metric")
vcc_5v  += r_pwr[1]
r_pwr[2] += led_pwr["A"]
gnd     += led_pwr["K"]

# D13 user LED (PC7 — Arduino Leonardo pin 13 = PC7)
led13_net = Net("LED13")
led_d13 = Part("Device", "LED", value="yellow",
               footprint="LED_SMD:LED_0603_1608Metric")
r_d13   = Part("Device", "R", value="1k",
               footprint="Resistor_SMD:R_0603_1608Metric")
led13_net  += led_d13["A"]
r_d13[1]   += led_d13["K"]
gnd        += r_d13[2]
mcu["PC7"] += led13_net

# TX LED (PD5 — active low)
txled_net = Net("TX_LED")
led_tx  = Part("Device", "LED", value="green",
               footprint="LED_SMD:LED_0603_1608Metric")
r_tx    = Part("Device", "R", value="1k",
               footprint="Resistor_SMD:R_0603_1608Metric")
vcc_5v    += led_tx["A"]
led_tx["K"] += r_tx[1]
txled_net  += r_tx[2]
mcu["PD5"] += txled_net

# RX LED (PB0 — active low)
rxled_net = Net("RX_LED")
led_rx  = Part("Device", "LED", value="yellow",
               footprint="LED_SMD:LED_0603_1608Metric")
r_rx    = Part("Device", "R", value="1k",
               footprint="Resistor_SMD:R_0603_1608Metric")
vcc_5v    += led_rx["A"]
led_rx["K"] += r_rx[1]
rxled_net  += r_rx[2]
mcu["PB0"] += rxled_net

# ── Arduino-standard pin headers ─────────────────────────────────────────────
# 2 × 1x8 (digital 0-7, digital 8-13 / power) + 2 × 1x6 (analog / power)

# Digital header 1 (D0-D7): PD2,PD3,PD1,PD0,PD4,PC6,PD7,PB4 (Arduino d0-d7)
hdr_d0_7 = Part("Connector", "Conn_01x08_Pin",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
hdr_d0_7.edge_preference = "bottom"
mcu["PD2"] += hdr_d0_7["Pin_1"]   # D0/RX1
mcu["PD3"] += hdr_d0_7["Pin_2"]   # D1/TX1
mcu["PD1"] += hdr_d0_7["Pin_3"]   # D2/SDA
mcu["PD0"] += hdr_d0_7["Pin_4"]   # D3/SCL/PWM
mcu["PD4"] += hdr_d0_7["Pin_5"]   # D4
mcu["PC6"] += hdr_d0_7["Pin_6"]   # D5/PWM
mcu["PD7"] += hdr_d0_7["Pin_7"]   # D6/PWM
mcu["PE6"] += hdr_d0_7["Pin_8"]   # D7

# Digital header 2 (D8-D13, GND, AREF, SDA, SCL): PB5,PB6,PB7,PD6,PC7,PB5...
hdr_d8_13 = Part("Connector", "Conn_01x08_Pin",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
hdr_d8_13.edge_preference = "bottom"
mcu["PB4"] += hdr_d8_13["Pin_1"]  # D8
mcu["PB5"] += hdr_d8_13["Pin_2"]  # D9/PWM
mcu["PB6"] += hdr_d8_13["Pin_3"]  # D10/PWM/SS
mcu["PB7"] += hdr_d8_13["Pin_4"]  # D11/MOSI/PWM
mcu["PD6"] += hdr_d8_13["Pin_5"]  # D12/MISO (shared with SPI MISO signal path)
mcu["PC7"] += hdr_d8_13["Pin_6"]  # D13/SCK/LED — PC7 on Leonardo
gnd        += hdr_d8_13["Pin_7"]  # GND
vcc_3v3    += hdr_d8_13["Pin_8"]  # AREF/3.3V

# Analog header 1x6 (A0-A5): PF7,PF6,PF5,PF4,PF1,PF0
hdr_a = Part("Connector", "Conn_01x06_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
hdr_a.edge_preference = "top"
mcu["PF7"] += hdr_a["Pin_1"]  # A0
mcu["PF6"] += hdr_a["Pin_2"]  # A1
mcu["PF5"] += hdr_a["Pin_3"]  # A2
mcu["PF4"] += hdr_a["Pin_4"]  # A3
mcu["PF1"] += hdr_a["Pin_5"]  # A4/SDA
mcu["PF0"] += hdr_a["Pin_6"]  # A5/SCL

# Power header 1x6 (RESET, 3.3V, 5V, GND, GND, VIN)
hdr_pwr = Part("Connector", "Conn_01x06_Pin",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
hdr_pwr.edge_preference = "top"
reset_net += hdr_pwr["Pin_1"]   # RESET
vcc_3v3   += hdr_pwr["Pin_2"]   # 3.3V
vcc_5v    += hdr_pwr["Pin_3"]   # 5V
gnd       += hdr_pwr["Pin_4"]   # GND
gnd       += hdr_pwr["Pin_5"]   # GND
vin_net   += hdr_pwr["Pin_6"]   # VIN

# ── ICSP 2x3 header ──────────────────────────────────────────────────────────
icsp = Part("Connector_Generic", "Conn_02x03_Odd_Even",
            footprint="Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical")
# ICSP standard pinout: MISO=1, VCC=2, SCK=3, MOSI=4, RESET=5, GND=6
# On ATmega32U4: MISO=PB3, SCK=PB1, MOSI=PB2
mcu["PB3"]   += icsp["Pin_1"]   # MISO
vcc_5v       += icsp["Pin_2"]   # VCC
mcu["PB1"]   += icsp["Pin_3"]   # SCK
mcu["PB2"]   += icsp["Pin_4"]   # MOSI
reset_net    += icsp["Pin_5"]   # RESET
gnd          += icsp["Pin_6"]   # GND

# PE6 is now D7 header pin; no unconnected GPIO pins remain

# ── VIN header decoupling ─────────────────────────────────────────────────────
c_vin = Part("Device", "C", value="100uF",
             footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
vin_net += c_vin[1]
gnd     += c_vin[2]

# ── Board metadata ────────────────────────────────────────────────────────────
EDA_FLOORPLAN = {
    "board_outline_mm": [68.0, 53.0],
    "assembly_policy": "single_side_smd_top",
}
