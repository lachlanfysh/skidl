"""
STM32 Blue Pill development board clone.
STM32F103C8T6 ARM Cortex-M3 @72MHz, LQFP-48.
Micro-USB power/programming, AMS1117-3.3 LDO, 8MHz + 32.768kHz crystals,
2x 20-pin GPIO headers, SWD header, Boot0/Boot1 jumpers, reset button,
power LED + user LED (PC13). Board: ~53x23mm.
"""

from skidl import *

# ── Power rails ──────────────────────────────────────────────────────────────
vbus  = Net("VBUS");  vbus.drive  = POWER
vcc   = Net("+3.3V"); vcc.drive   = POWER
gnd   = Net("GND");   gnd.drive   = POWER

# ── USB Micro-B connector ────────────────────────────────────────────────────
@subcircuit
def usb_connector():
    global vbus, vcc, gnd, usb_dm, usb_dp
    usb = Part("Connector", "USB_B_Micro",
               footprint="Connector_USB:USB_Micro-B_Molex-105017-0001")
    usb.edge_preference = "left"

    vbus     += usb["VBUS"]
    gnd      += usb["GND"], usb["Shield"]
    usb["ID"] += gnd  # ID tied to GND for device mode

    # D+ pull-up 1.5k to 3V3 for full-speed USB (PA12=DP, PA11=DM)
    r_dp = Part("Device", "R", value="1k5",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_dp[1] += vcc
    r_dp[2] += usb["D+"]

    # USB data nets — connected externally to PA11/PA12
    usb_dm = usb["D-"]
    usb_dp = usb["D+"]

usb_dm = Net("USB_DM")
usb_dp = Net("USB_DP")
usb_connector()

# ── LDO Regulator: AMS1117-3.3 ──────────────────────────────────────────────
@subcircuit
def power_regulation():
    global vbus, vcc, gnd
    u2 = Part("Regulator_Linear", "AMS1117-3.3",
               footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")

    vbus += u2["VI"]
    vcc  += u2["VO"]
    gnd  += u2["GND"]

    # Input cap 10uF
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vbus
    c_in[2] += gnd

    # Output caps 10uF + 100nF
    c_out1 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out1[1] += vcc
    c_out1[2] += gnd

    c_out2 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out2[1] += vcc
    c_out2[2] += gnd

power_regulation()

# ── Power LED ────────────────────────────────────────────────────────────────
@subcircuit
def power_led():
    global vcc, gnd
    led_pwr = Part("Device", "LED", value="PWR_LED",
                   footprint="LED_SMD:LED_0805_2012Metric")
    r_pwr   = Part("Device", "R", value="1k",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    vcc    += r_pwr[1]
    r_pwr[2] += led_pwr["A"]
    gnd    += led_pwr["K"]

power_led()

# ── STM32F103C8T6 ────────────────────────────────────────────────────────────
@subcircuit
def mcu():
    global vcc, gnd, usb_dm, usb_dp
    global net_pc13, net_osc_in, net_osc_out, net_osc32_in, net_osc32_out
    global net_nrst, net_boot0, net_boot1, net_swdio, net_swdclk, net_swo
    global pa_pins, pb_pins
    u1 = Part("MCU_ST_STM32F1", "STM32F103C8Tx",
               footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm")

    # Power pins
    vcc  += u1["VDD"], u1["VDDA"]
    gnd  += u1["VSS"], u1["VSSA"]
    vcc  += u1["VBAT"]   # VBAT from 3.3V (no coin cell)

    # Decoupling caps — 100nF per VDD pin + VDDA
    for _ in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vcc
        c[2] += gnd

    # 4.7uF bulk
    c_bulk = Part("Device", "C", value="4u7",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vcc
    c_bulk[2] += gnd

    # USB D+/D- on PA11/PA12
    usb_dm += u1["PA11"]
    usb_dp += u1["PA12"]

    # User LED on PC13 (active low — current sink)
    global net_pc13
    net_pc13 = u1["PC13"]

    # Crystal OSC pins on PD0/PD1 (also OSC_IN/OSC_OUT)
    global net_osc_in, net_osc_out
    net_osc_in  = u1["PD0"]
    net_osc_out = u1["PD1"]

    # RTC crystal pins PC14/PC15 (OSC32_IN / OSC32_OUT)
    global net_osc32_in, net_osc32_out
    net_osc32_in  = u1["PC14"]
    net_osc32_out = u1["PC15"]

    # NRST
    global net_nrst
    net_nrst = u1["NRST"]

    # BOOT0
    global net_boot0
    net_boot0 = u1["BOOT0"]

    # BOOT1 is PB2
    global net_boot1
    net_boot1 = u1["PB2"]

    # SWD pins: PA13=SWDIO, PA14=SWCLK, PB3=SWO
    global net_swdio, net_swdclk, net_swo
    net_swdio  = u1["PA13"]
    net_swdclk = u1["PA14"]
    net_swo    = u1["PB3"]

    # Expose remaining GPIO via global nets for headers
    global pa_pins, pb_pins
    pa_pins = {
        "PA0":  u1["PA0"],  "PA1":  u1["PA1"],  "PA2":  u1["PA2"],
        "PA3":  u1["PA3"],  "PA4":  u1["PA4"],  "PA5":  u1["PA5"],
        "PA6":  u1["PA6"],  "PA7":  u1["PA7"],  "PA8":  u1["PA8"],
        "PA9":  u1["PA9"],  "PA10": u1["PA10"],
        "PA15": u1["PA15"],
    }
    pb_pins = {
        "PB0":  u1["PB0"],  "PB1":  u1["PB1"],
        "PB4":  u1["PB4"],  "PB5":  u1["PB5"],
        "PB6":  u1["PB6"],  "PB7":  u1["PB7"],
        "PB8":  u1["PB8"],  "PB9":  u1["PB9"],
        "PB10": u1["PB10"], "PB11": u1["PB11"],
        "PB12": u1["PB12"], "PB13": u1["PB13"],
        "PB14": u1["PB14"], "PB15": u1["PB15"],
    }

net_pc13     = Net("PC13")
net_osc_in   = Net("OSC_IN")
net_osc_out  = Net("OSC_OUT")
net_osc32_in  = Net("OSC32_IN")
net_osc32_out = Net("OSC32_OUT")
net_nrst     = Net("NRST")
net_boot0    = Net("BOOT0")
net_boot1    = Net("BOOT1")
net_swdio    = Net("SWDIO")
net_swdclk   = Net("SWDCLK")
net_swo      = Net("SWO")
pa_pins = {}
pb_pins = {}
mcu()

# ── User LED (PC13, active low) ───────────────────────────────────────────────
@subcircuit
def user_led():
    global vcc, gnd, net_pc13
    led_usr = Part("Device", "LED", value="USER_LED",
                   footprint="LED_SMD:LED_0805_2012Metric")
    r_usr   = Part("Device", "R", value="1k",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    vcc        += led_usr["A"]
    led_usr["K"] += r_usr[1]
    r_usr[2]   += net_pc13

user_led()

# ── 8MHz Main Crystal ─────────────────────────────────────────────────────────
@subcircuit
def crystal_8mhz():
    global gnd, net_osc_in, net_osc_out
    # Crystal_GND23: pin 1=OSC_IN, pin 2=GND, pin 3=GND, pin 4=OSC_OUT
    xtal = Part("Device", "Crystal_GND23",
                footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm")
    net_osc_in  += xtal[1]
    gnd         += xtal[2], xtal[3]
    net_osc_out += xtal[4]

    c1 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c2 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += net_osc_in;  c1[2] += gnd
    c2[1] += net_osc_out; c2[2] += gnd

crystal_8mhz()

# ── 32.768kHz RTC Crystal ─────────────────────────────────────────────────────
@subcircuit
def crystal_32k():
    global gnd, net_osc32_in, net_osc32_out
    # Crystal: pin 1 = OSC32_IN, pin 2 = OSC32_OUT
    xtal = Part("Device", "Crystal",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    net_osc32_in  += xtal[1]
    net_osc32_out += xtal[2]

    c1 = Part("Device", "C", value="12pF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c2 = Part("Device", "C", value="12pF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += net_osc32_in;  c1[2] += gnd
    c2[1] += net_osc32_out; c2[2] += gnd

crystal_32k()

# ── Reset Button ──────────────────────────────────────────────────────────────
@subcircuit
def reset_circuit():
    global vcc, gnd, net_nrst
    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_SPST_B3S-1000")
    r_rst  = Part("Device", "R", value="10k",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    c_rst  = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")

    vcc       += r_rst[1]
    r_rst[2]  += net_nrst
    net_nrst  += sw_rst[1]
    gnd       += sw_rst[2]
    net_nrst  += c_rst[1]
    gnd       += c_rst[2]

reset_circuit()

# ── BOOT0 Jumper ──────────────────────────────────────────────────────────────
@subcircuit
def boot0_circuit():
    global vcc, gnd, net_boot0
    jp0 = Part("Connector", "Conn_01x02_Pin",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    r0  = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0603_1608Metric")
    # Pin 1 = selectable: GND (run) or VCC (bootloader)
    # Jumper bridges JP0[1] to either GND or VCC
    # Default: BOOT0 pulled low
    r0[1]    += gnd
    r0[2]    += net_boot0
    net_boot0 += jp0["Pin_1"]
    vcc       += jp0["Pin_2"]

boot0_circuit()

# ── BOOT1 (PB2) Jumper ────────────────────────────────────────────────────────
@subcircuit
def boot1_circuit():
    global vcc, gnd, net_boot1
    jp1 = Part("Connector", "Conn_01x02_Pin",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    r1  = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0603_1608Metric")
    r1[1]    += gnd
    r1[2]    += net_boot1
    net_boot1 += jp1["Pin_1"]
    vcc       += jp1["Pin_2"]

boot1_circuit()

# ── SWD Debug Header (4-pin: VCC, GND, SWDIO, SWCLK) ─────────────────────────
@subcircuit
def swd_header():
    global vcc, gnd, net_swdio, net_swdclk
    swd = Part("Connector", "Conn_01x04_Pin",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    swd.edge_preference = "right"
    vcc      += swd["Pin_1"]
    net_swdio  += swd["Pin_2"]
    net_swdclk += swd["Pin_3"]
    gnd      += swd["Pin_4"]

swd_header()

# ── GPIO Headers — 2x 20-pin ──────────────────────────────────────────────────
@subcircuit
def gpio_header_a():
    global vcc, gnd, pa_pins, pb_pins
    """Left header: PA0-PA10, PA15, PB0, PB1, PB10, PB11, GND, GND, VCC, VCC, 3V3"""
    h = Part("Connector", "Conn_01x20_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x20_P2.54mm_Vertical")
    h.edge_preference = "bottom"

    nets = [
        Net("PA0_HDR"),  Net("PA1_HDR"),  Net("PA2_HDR"),  Net("PA3_HDR"),
        Net("PA4_HDR"),  Net("PA5_HDR"),  Net("PA6_HDR"),  Net("PA7_HDR"),
        Net("PA8_HDR"),  Net("PA9_HDR"),  Net("PA10_HDR"),
        vcc, gnd,
        Net("PA15_HDR"),
        Net("PB0_HDR"),  Net("PB1_HDR"),
        Net("PB10_HDR"), Net("PB11_HDR"),
        gnd, vcc,
    ]
    for i, n in enumerate(nets, start=1):
        n += h[f"Pin_{i}"]

    # Wire to MCU nets
    pa_pins["PA0"] += nets[0];  pa_pins["PA1"] += nets[1]
    pa_pins["PA2"] += nets[2];  pa_pins["PA3"] += nets[3]
    pa_pins["PA4"] += nets[4];  pa_pins["PA5"] += nets[5]
    pa_pins["PA6"] += nets[6];  pa_pins["PA7"] += nets[7]
    pa_pins["PA8"] += nets[8];  pa_pins["PA9"] += nets[9]
    pa_pins["PA10"] += nets[10]
    pa_pins["PA15"] += nets[13]
    pb_pins["PB0"]  += nets[14]; pb_pins["PB1"]  += nets[15]
    pb_pins["PB10"] += nets[16]; pb_pins["PB11"] += nets[17]

@subcircuit
def gpio_header_b():
    global vcc, gnd, pb_pins, net_pc13, net_nrst, net_swdio, net_swdclk, net_swo
    """Right header: PB12-PB15, PB3-PB9, PB4, PB5, PC13, NRST, GND, GND, VCC, VCC"""
    h = Part("Connector", "Conn_01x20_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x20_P2.54mm_Vertical")
    h.edge_preference = "bottom"

    nets = [
        Net("PB12_HDR"), Net("PB13_HDR"), Net("PB14_HDR"), Net("PB15_HDR"),
        Net("PB3_HDR"),  Net("PB4_HDR"),  Net("PB5_HDR"),  Net("PB6_HDR"),
        Net("PB7_HDR"),  Net("PB8_HDR"),  Net("PB9_HDR"),
        Net("PC13_HDR"), Net("NRST_HDR"),
        gnd, gnd, vcc, vcc,
        net_swdio, net_swdclk, net_swo,
    ]
    for i, n in enumerate(nets, start=1):
        n += h[f"Pin_{i}"]

    pb_pins["PB12"] += nets[0];  pb_pins["PB13"] += nets[1]
    pb_pins["PB14"] += nets[2];  pb_pins["PB15"] += nets[3]
    pb_pins["PB4"]  += nets[5];  pb_pins["PB5"]  += nets[6]
    pb_pins["PB6"]  += nets[7];  pb_pins["PB7"]  += nets[8]
    pb_pins["PB8"]  += nets[9];  pb_pins["PB9"]  += nets[10]
    net_pc13    += nets[11]
    net_nrst    += nets[12]
    # nets[4] is PB3_HDR; connect to net_swo (PB3 is SWO, already in MCU)
    net_swo     += nets[4]

gpio_header_a()
gpio_header_b()
