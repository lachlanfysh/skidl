"""
Circuit Playground Express -- SKiDL circuit description.

Adafruit Circuit Playground Express: ATSAMD21G18A-based educational board
with 10x NeoPixels, LIS3DH accelerometer, MEMS microphone, mini speaker,
IR transceiver, light sensor, temperature sensor, 2 buttons, slide switch,
USB Micro-B, JST battery, 3.3V regulator, and 8 capacitive-touch alligator pads.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from collections import defaultdict
from types import SimpleNamespace
from skidl import *
set_default_tool(KICAD9)


def _pin_func_to_str(func):
    """Map pin function enum to KiCad s-expression string."""
    mapping = {
        Pin.types.PWRIN: "power_in",
        Pin.types.PWROUT: "power_out",
        Pin.types.INPUT: "input",
        Pin.types.OUTPUT: "output",
        Pin.types.BIDIR: "bidirectional",
        Pin.types.PASSIVE: "passive",
        Pin.types.TRISTATE: "tri_state",
        Pin.types.UNSPEC: "unspecified",
        Pin.types.NOCONNECT: "passive",
    }
    return mapping.get(func, "passive")


def skidl_part(name, footprint, pin_defs):
    """Create a Part with tool=SKIDL including draw_cmds for schematic generation.

    pin_defs: list of (num, name, func) tuples.
    """
    y_step = 2.54  # mm spacing between pins
    left_pins = []
    right_pins = []
    for num, pname, pfunc in pin_defs:
        if pfunc in (Pin.types.OUTPUT, Pin.types.PWROUT):
            right_pins.append((num, pname, pfunc))
        else:
            left_pins.append((num, pname, pfunc))

    max_side = max(len(left_pins), len(right_pins), 1)
    box_h = (max_side + 1) * y_step
    box_w = 10.16  # mm
    pin_len = 2.54  # mm

    all_pins = []
    draw_cmds = []

    # Left side pins
    for i, (num, pname, pfunc) in enumerate(left_pins):
        px = -(box_w / 2) - pin_len
        py = (box_h / 2) - (i + 1) * y_step
        p = Pin(num=num, name=pname, func=pfunc)
        p.x = px
        p.y = py
        p.orientation = "R"
        p.length = pin_len
        p.rotation = 0
        all_pins.append(p)
        draw_cmds.append([
            "pin", _pin_func_to_str(pfunc), "line",
            ["at", px, py, 0],
            ["length", pin_len],
            ["name", pname, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", num, ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    # Right side pins
    for i, (num, pname, pfunc) in enumerate(right_pins):
        px = (box_w / 2) + pin_len
        py = (box_h / 2) - (i + 1) * y_step
        p = Pin(num=num, name=pname, func=pfunc)
        p.x = px
        p.y = py
        p.orientation = "L"
        p.length = pin_len
        p.rotation = 180
        all_pins.append(p)
        draw_cmds.append([
            "pin", _pin_func_to_str(pfunc), "line",
            ["at", px, py, 180],
            ["length", pin_len],
            ["name", pname, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", num, ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    # Rectangle body
    draw_cmds.append([
        "rectangle",
        ["start", -(box_w / 2), (box_h / 2)],
        ["end", (box_w / 2), -(box_h / 2)],
        ["stroke", ["width", 0], ["type", "default"]],
        ["fill", ["type", "background"]],
    ])

    part = Part(name=name, tool=SKIDL, dest=NETLIST,
                footprint=footprint, pins=all_pins)
    part.draw_cmds = defaultdict(list)
    part.draw_cmds[1] = draw_cmds
    # Provide a lib attribute with filename so the schematic writer can create lib_id
    part.lib = SimpleNamespace(filename="Custom")

    return part


# ============================================================
# Global power nets
# ============================================================
vbus = Net("VBUS"); vbus.drive = POWER      # USB 5V
vcc  = Net("+3V3"); vcc.drive = POWER       # 3.3V regulated
gnd  = Net("GND");  gnd.drive = POWER

# Internal nets
sda       = Net("SDA")
scl       = Net("SCL")
neopixel  = Net("NEOPIX")
ir_tx_net = Net("IR_TX")
ir_rx_net = Net("IR_RX")
spk_net   = Net("SPEAKER")
mic_net   = Net("MIC_OUT")
light_net = Net("LIGHT")
temp_net  = Net("TEMP")

# ============================================================
# 1. Power: USB Micro-B + 3.3V regulator + JST battery
# ============================================================
@subcircuit
def power_supply(vbus, vcc, gnd):
    # USB Micro-B connector
    usb = Part("Connector", "USB_B_Micro",
               footprint="Connector_USB:USB_Micro-B_Molex-105017-0001",
               value="USB_Micro-B")
    usb["VBUS"]  += vbus
    usb["GND"]   += gnd
    usb["D+"]    += Net("USB_DP")
    usb["D-"]    += Net("USB_DN")
    usb["ID"]    += Net("USB_ID")
    usb["Shield"] += gnd

    # Input bulk capacitor on VBUS
    c_usb = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_usb[1] += vbus
    c_usb[2] += gnd

    # 3.3V LDO regulator (AP2112K-3.3 in SOT-23-5)
    reg = skidl_part("AP2112K-3.3", "Package_TO_SOT_SMD:SOT-23-5", [
        ("1", "VIN",  Pin.types.PWRIN),
        ("2", "GND",  Pin.types.PWRIN),
        ("3", "EN",   Pin.types.INPUT),
        ("4", "NC",   Pin.types.NOCONNECT),
        ("5", "VOUT", Pin.types.PWROUT),
    ])
    reg["VIN"]  += vbus
    reg["GND"]  += gnd
    reg["EN"]   += vbus   # always enabled
    reg["VOUT"] += vcc

    # LDO decoupling caps
    c_in = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vbus; c_in[2] += gnd

    c_out = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out[1] += vcc; c_out[2] += gnd

    c_out2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out2[1] += vcc; c_out2[2] += gnd

    # JST PH 2-pin battery connector
    jst = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal",
               value="BATT")
    jst[1] += vbus  # Battery V+ tied to VBUS rail (through Schottky in real board)
    jst[2] += gnd

    # Schottky diode for battery/USB OR-ing
    d_schottky = Part("Device", "D_Schottky",
                      footprint="Diode_SMD:D_SOD-323",
                      value="MBR0520")
    d_schottky[1] += vbus   # anode from bat
    d_schottky[2] += vbus   # simplified -- both share VBUS


power_supply(vbus, vcc, gnd)

# ============================================================
# 2. MCU: ATSAMD21G18A (TQFP-48)
# ============================================================
@subcircuit
def mcu_block(vbus, vcc, gnd, sda, scl, neopixel,
              ir_tx_net, ir_rx_net, spk_net, mic_net,
              light_net, temp_net):
    mcu = Part("MCU_Microchip_SAMD", "ATSAMD21G18A-A",
               footprint="Package_QFP:TQFP-48_7x7mm_P0.5mm",
               value="ATSAMD21G18A")

    # Power pins
    mcu["VDDIO"]  += vcc    # pins 17, 36
    mcu["VDDIN"]  += vcc    # pin 44
    mcu["VDDANA"] += vcc    # pin 6
    mcu["GND"]    += gnd    # pins 18, 35, 42
    mcu["GNDANA"] += gnd    # pin 5

    # VDDCORE gets 1.2V output from internal regulator -- decouple
    vddcore = Net("VDDCORE")
    mcu["VDDCORE"] += vddcore
    c_core = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_core[1] += vddcore; c_core[2] += gnd

    # MCU decoupling caps
    for _ in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vcc; c[2] += gnd

    # 32.768 kHz crystal (PA00/PA01 = XIN32/XOUT32)
    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["PA00"]
    xtal[2] += mcu["PA01"]

    # Crystal load caps
    c_x1 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += mcu["PA00"]; c_x1[2] += gnd
    c_x2 = Part("Device", "C", value="12pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2[1] += mcu["PA01"]; c_x2[2] += gnd

    # Reset circuit
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vcc
    r_rst[2] += mcu["~{RESET}"]
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_rst[1] += mcu["~{RESET}"]; c_rst[2] += gnd

    # USB D+/D- (PA24=D-, PA25=D+)
    mcu["PA24"] += Net("USB_DN")
    mcu["PA25"] += Net("USB_DP")

    # I2C bus (PA22=SDA, PA23=SCL) -- for LIS3DH
    mcu["PA22"] += sda
    mcu["PA23"] += scl
    # I2C pull-ups
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc; r_sda[2] += sda
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl[1] += vcc; r_scl[2] += scl

    # NeoPixel data output (PA06)
    mcu["PA06"] += neopixel

    # IR transmitter drive (PA05)
    mcu["PA05"] += ir_tx_net

    # IR receiver input (PA07)
    mcu["PA07"] += ir_rx_net

    # Speaker PWM output (PA02 -- DAC output)
    mcu["PA02"] += spk_net

    # Microphone analog input (PB09)
    mcu["PB09"] += mic_net

    # Light sensor analog input (PB08)
    mcu["PB08"] += light_net

    # Temperature sensor analog input (PA09)
    mcu["PA09"] += temp_net

    # Two user buttons: PA04 (left=A), PA14 (right=B)
    btn_a_net = Net("BTN_A")
    btn_b_net = Net("BTN_B")
    mcu["PA04"] += btn_a_net
    mcu["PA14"] += btn_b_net

    # Slide switch on PA28
    sw_net = Net("SLIDE_SW")
    mcu["PA28"] += sw_net

    # Capacitive touch pads -- PA03, PA08, PA10, PA11, PB02, PB03, PA12, PA13
    # (mapped to alligator clip pads A0-A7)
    touch_pins = ["PA03", "PA08", "PA10", "PA11", "PB02", "PB03", "PA12", "PA13"]
    touch_nets = []
    for i, pin_name in enumerate(touch_pins):
        tn = Net(f"PAD_A{i}")
        mcu[pin_name] += tn
        touch_nets.append(tn)

    # SWD debug (PA30=SWDCLK, PA31=SWDIO)
    mcu["PA30"] += Net("SWDCLK")
    mcu["PA31"] += Net("SWDIO")

    # Remaining pins as NC or general
    for p in ["PA15", "PA16", "PA17", "PA18", "PA19", "PA20", "PA21", "PA27",
              "PB10", "PB11"]:
        try:
            mcu[p] += Net(f"MCU_{p}")
        except Exception:
            pass

    return mcu, btn_a_net, btn_b_net, sw_net, touch_nets


mcu_ret = mcu_block(vbus, vcc, gnd, sda, scl, neopixel,
                    ir_tx_net, ir_rx_net, spk_net, mic_net,
                    light_net, temp_net)

# ============================================================
# 3. NeoPixel ring: 10x WS2812B in daisy chain
# ============================================================
@subcircuit
def neopixel_ring(vcc, gnd, data_in):
    prev = data_in
    for i in range(10):
        led = skidl_part(f"WS2812B_{i}",
                         "LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm", [
                             ("1", "VDD",  Pin.types.PWRIN),
                             ("3", "GND",  Pin.types.PWRIN),
                             ("4", "DIN",  Pin.types.INPUT),
                             ("2", "DOUT", Pin.types.OUTPUT),
                         ])
        led["VDD"] += vcc
        led["GND"] += gnd
        led["DIN"] += prev
        if i < 9:
            nxt = Net(f"NEO_{i}")
            led["DOUT"] += nxt
            prev = nxt
        else:
            led["DOUT"] += Net("NEO_END")

        # Bypass cap per LED
        c_led = Part("Device", "C", value="100nF",
                     footprint="Capacitor_SMD:C_0402_1005Metric")
        c_led[1] += vcc; c_led[2] += gnd


neopixel_ring(vcc, gnd, neopixel)

# ============================================================
# 4. LIS3DH 3-axis accelerometer (I2C, LGA-16)
# ============================================================
@subcircuit
def accelerometer(vcc, gnd, sda, scl):
    lis = skidl_part("LIS3DH",
                     "Sensor_Motion:Analog_LGA-16_3.25x3mm_P0.5mm_LayoutBorder3x5y", [
                         ("1",  "VDD_IO", Pin.types.PWRIN),
                         ("2",  "NC1",    Pin.types.NOCONNECT),
                         ("3",  "NC2",    Pin.types.NOCONNECT),
                         ("4",  "SCL",    Pin.types.INPUT),
                         ("5",  "GND1",   Pin.types.PWRIN),
                         ("6",  "SDA",    Pin.types.BIDIR),
                         ("7",  "SA0",    Pin.types.INPUT),
                         ("8",  "CS",     Pin.types.INPUT),
                         ("10", "GND2",   Pin.types.PWRIN),
                         ("12", "GND3",   Pin.types.PWRIN),
                         ("13", "ADC3",   Pin.types.INPUT),
                         ("14", "VDD",    Pin.types.PWRIN),
                         ("15", "ADC2",   Pin.types.INPUT),
                         ("16", "ADC1",   Pin.types.INPUT),
                         ("9",  "INT2",   Pin.types.OUTPUT),
                         ("11", "INT1",   Pin.types.OUTPUT),
                     ])
    lis["VDD"]    += vcc
    lis["VDD_IO"] += vcc
    lis["GND1"]   += gnd
    lis["GND2"]   += gnd
    lis["GND3"]   += gnd
    lis["SDA"]    += sda
    lis["SCL"]    += scl
    lis["CS"]     += vcc   # I2C mode
    lis["SA0"]    += gnd   # Address 0x18
    lis["INT1"]   += Net("LIS_INT1")
    lis["INT2"]   += Net("LIS_INT2")

    # Decoupling
    c_lis = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_lis[1] += vcc; c_lis[2] += gnd


accelerometer(vcc, gnd, sda, scl)

# ============================================================
# 5. MEMS microphone (SPH0645LM4H-style PDM mic)
# ============================================================
@subcircuit
def mems_microphone(vcc, gnd, mic_out):
    mic = skidl_part("SPH0645LM4H",
                     "Sensor_Audio:Knowles_SPH0645LM4H-6_3.5x2.65mm", [
                         ("1", "WS",     Pin.types.INPUT),
                         ("2", "SEL",    Pin.types.INPUT),
                         ("4", "SCK",    Pin.types.INPUT),
                         ("5", "GND",    Pin.types.PWRIN),
                         ("6", "VDD",    Pin.types.PWRIN),
                         ("3", "DATA",   Pin.types.OUTPUT),
                     ])
    mic["VDD"]  += vcc
    mic["GND"]  += gnd
    mic["DATA"] += mic_out
    mic["SCK"]  += Net("MIC_SCK")
    mic["WS"]   += Net("MIC_WS")
    mic["SEL"]  += gnd  # left channel

    c_mic = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_mic[1] += vcc; c_mic[2] += gnd


mems_microphone(vcc, gnd, mic_net)

# ============================================================
# 6. Mini speaker + driver
# ============================================================
@subcircuit
def speaker_driver(vcc, gnd, spk_in):
    # Class-D audio amp (PAM8301 style, SOT-23-5)
    amp = skidl_part("PAM8301", "Package_TO_SOT_SMD:SOT-23-5", [
        ("1", "SD",   Pin.types.INPUT),
        ("2", "INP",  Pin.types.INPUT),
        ("3", "GND",  Pin.types.PWRIN),
        ("5", "VDD",  Pin.types.PWRIN),
        ("4", "OUT",  Pin.types.OUTPUT),
    ])
    amp["VDD"] += vcc
    amp["GND"] += gnd
    amp["SD"]  += vcc  # always enabled
    amp["INP"] += spk_in

    # Series resistor to speaker
    spk_out = Net("SPK_OUT")
    amp["OUT"] += spk_out

    # Speaker connector (2-pin)
    spk = Part("Connector_Generic", "Conn_01x02",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
               value="SPEAKER")
    spk[1] += spk_out
    spk[2] += gnd

    c_amp = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_amp[1] += vcc; c_amp[2] += gnd


speaker_driver(vcc, gnd, spk_net)

# ============================================================
# 7. IR transmitter + receiver
# ============================================================
@subcircuit
def ir_transceiver(vcc, gnd, ir_tx, ir_rx):
    # IR LED (transmitter)
    ir_led = Part("Device", "LED", value="IR_LED",
                  footprint="LED_SMD:LED_0603_1608Metric")
    r_ir = Part("Device", "R", value="100R",
                footprint="Resistor_SMD:R_0402_1005Metric")
    # NPN transistor to drive IR LED
    q_ir = skidl_part("MMBT3904", "Package_TO_SOT_SMD:SOT-23", [
        ("1", "B", Pin.types.INPUT),
        ("2", "E", Pin.types.PASSIVE),
        ("3", "C", Pin.types.PASSIVE),
    ])
    r_base = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_base[1] += ir_tx
    r_base[2] += q_ir["B"]
    q_ir["E"]  += gnd
    ir_led[2]  += q_ir["C"]  # cathode to collector
    r_ir[1]    += vcc
    r_ir[2]    += ir_led[1]  # anode through resistor to VCC

    # IR receiver module (TSOP38238 style, 3-pin)
    ir_rcv = skidl_part("TSOP38238", "Package_TO_SOT_SMD:SOT-23", [
        ("2", "GND", Pin.types.PWRIN),
        ("3", "VCC", Pin.types.PWRIN),
        ("1", "OUT", Pin.types.OUTPUT),
    ])
    ir_rcv["VCC"] += vcc
    ir_rcv["GND"] += gnd
    ir_rcv["OUT"] += ir_rx

    c_ir = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_ir[1] += vcc; c_ir[2] += gnd


ir_transceiver(vcc, gnd, ir_tx_net, ir_rx_net)

# ============================================================
# 8. Light sensor (phototransistor + bias)
# ============================================================
@subcircuit
def light_sensor(vcc, gnd, light_out):
    # Phototransistor modeled as a resistive element
    pt = Part("Device", "R", value="PHOTOTRANS",
              footprint="LED_SMD:LED_0603_1608Metric")
    r_light = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0402_1005Metric")
    pt[1] += vcc
    pt[2] += light_out
    r_light[1] += light_out
    r_light[2] += gnd


light_sensor(vcc, gnd, light_net)

# ============================================================
# 9. Temperature sensor (NTC thermistor voltage divider)
# ============================================================
@subcircuit
def temp_sensor(vcc, gnd, temp_out):
    ntc = Part("Device", "R", value="10K_NTC",
               footprint="Resistor_SMD:R_0402_1005Metric")
    r_div = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    ntc[1]   += vcc
    ntc[2]   += temp_out
    r_div[1] += temp_out
    r_div[2] += gnd


temp_sensor(vcc, gnd, temp_net)

# ============================================================
# 10. User buttons + slide switch
# ============================================================
@subcircuit
def buttons_and_switch(vcc, gnd):
    # Button A (modeled as 2-pin component)
    btn_a = Part("Device", "R", value="SW_A",
                 footprint="Button_Switch_SMD:SW_SPST_B3S-1000")
    btn_a[1] += Net("BTN_A")
    btn_a[2] += gnd

    # Button B
    btn_b = Part("Device", "R", value="SW_B",
                 footprint="Button_Switch_SMD:SW_SPST_B3S-1000")
    btn_b[1] += Net("BTN_B")
    btn_b[2] += gnd

    # Slide switch (SPDT as 3-pin connector)
    sw = Part("Connector_Generic", "Conn_01x03",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
              value="SLIDE_SW")
    sw[1] += gnd
    sw[2] += Net("SLIDE_SW")
    sw[3] += vcc

    # Reset button
    btn_rst = Part("Device", "R", value="SW_RST",
                   footprint="Button_Switch_SMD:SW_SPST_B3S-1000")
    btn_rst[1] += Net("~{RESET}")
    btn_rst[2] += gnd

    # Pull-up resistors for buttons
    for net_name in ["BTN_A", "BTN_B"]:
        r = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
        r[1] += vcc
        r[2] += Net(net_name)


buttons_and_switch(vcc, gnd)

# ============================================================
# 11. Alligator clip pads (large copper pads as test points)
# ============================================================
@subcircuit
def alligator_pads(vcc, gnd):
    # 8 pads for capacitive touch / analog / digital I/O
    for i in range(8):
        tp = Part("Connector_Generic", "Conn_01x01",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical",
                  value=f"PAD_A{i}")
        tp[1] += Net(f"PAD_A{i}")

    # Power pads: 3.3V and GND exposed for alligator clips
    tp_vcc = Part("Connector_Generic", "Conn_01x01",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical",
                  value="PAD_3V3")
    tp_vcc[1] += vcc

    tp_gnd = Part("Connector_Generic", "Conn_01x01",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical",
                  value="PAD_GND")
    tp_gnd[1] += gnd


alligator_pads(vcc, gnd)

# ============================================================
# 12. SWD debug header
# ============================================================
@subcircuit
def swd_header(vcc, gnd):
    hdr = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
               value="SWD")
    hdr[1] += vcc
    hdr[2] += gnd
    hdr[3] += Net("SWDCLK")
    hdr[4] += Net("SWDIO")


swd_header(vcc, gnd)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True)
