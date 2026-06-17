"""
Circuit Playground Express -- SKiDL circuit description.

Adafruit Circuit Playground Express: ATSAMD21G18A-based educational board
with 10x NeoPixels, LIS3DH accelerometer, MEMS microphone, mini speaker,
IR transceiver, light sensor, temperature sensor, 2 buttons, slide switch,
USB Micro-B, JST battery, 3.3V regulator, and 10 alligator-clip pads.
~51mm diameter circular board.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


# ── Top-level nets ──────────────────────────────────────────────────────────
VUSB = Net("VUSB"); VUSB.drive = POWER
VBAT = Net("VBAT"); VBAT.drive = POWER
VCC  = Net("VCC");  VCC.drive  = POWER   # 3.3V regulated rail
GND  = Net("GND");  GND.drive  = POWER

# I2C bus
SCL = Net("SCL")
SDA = Net("SDA")

# SPI bus (SERCOM0 via PA08/PA09/PA10/PA11)
MOSI = Net("MOSI")
MISO = Net("MISO")
SCK  = Net("SCK")

# NeoPixel data chain
NEOPIXEL_DATA = Net("NEOPIXEL_DATA")

# USB D+/D-
USB_DP = Net("USB_DP")
USB_DM = Net("USB_DM")

# Misc MCU signals
IR_TX      = Net("IR_TX")
IR_RX      = Net("IR_RX")
BTN_A      = Net("BTN_A")
BTN_B      = Net("BTN_B")
SLIDE_SW   = Net("SLIDE_SW")
SPEAKER    = Net("SPEAKER")
SPK_EN     = Net("SPK_EN")
MIC_DATA   = Net("MIC_DATA")
MIC_CLK    = Net("MIC_CLK")
MIC_SEL    = Net("MIC_SEL")
LIGHT_ADC  = Net("LIGHT_ADC")
TEMP_ADC   = Net("TEMP_ADC")
LIS3DH_CS  = Net("LIS3DH_CS")
LIS3DH_INT = Net("LIS3DH_INT")
RESET_N    = Net("RESET_N")
SWDCLK     = Net("SWDCLK")
SWDIO      = Net("SWDIO")

# Alligator pad connections (also GPIO)
PAD_A0 = Net("PAD_A0")
PAD_A1 = Net("PAD_A1")
PAD_A2 = Net("PAD_A2")
PAD_A3 = Net("PAD_A3")
PAD_A4 = Net("PAD_A4")
PAD_A5 = Net("PAD_A5")
PAD_A6 = Net("PAD_A6")
PAD_TX = Net("PAD_TX")
PAD_RX = Net("PAD_RX")
# SCL alligator pad is the same net as SCL bus - use SCL directly


# ── Floorplan ───────────────────────────────────────────────────────────────
# Circuit Playground Express: circular ~51mm diameter board.
# Simulate circle as 51x51mm square with corner_radius_mm=25.5.
EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 51,
        "height_mm": 51,
        "corner_radius_mm": 25.5
    },
    "edge_anchors": [
        {"ref": "J1",  "edge": "bottom", "offset_mm": 25},
        {"ref": "J2",  "edge": "top",    "offset_mm": 25},
        {"ref": "SW3", "edge": "left",   "offset_mm": 25},
        {"ref": "SW1", "edge": "left",   "offset_mm": 15},
        {"ref": "SW2", "edge": "right",  "offset_mm": 15},
    ],
    "fixed_positions": [
        {"ref": "U1", "x_mm": 25.5, "y_mm": 25.5},
    ],
}


@subcircuit
def mcu_core(vcc, gnd, usb_dp, usb_dm, scl, sda, mosi, miso, sck,
             neopixel, ir_tx, ir_rx, btn_a, btn_b, slide_sw,
             speaker, spk_en, mic_data, mic_clk, mic_sel,
             light_adc, temp_adc, lis3dh_cs, lis3dh_int,
             reset_n, swdclk, swdio,
             a0, a1, a2, a3, a4, a5, a6, tx, rx):
    """ATSAMD21G18A (TQFP-48) core with decoupling caps.

    Available pins on TQFP-48:
      PA00-PA11, PA12-PA25, PA27, PA28, PA30, PA31
      PB02, PB03, PB08, PB09, PB10, PB11, PB22, PB23
      Power: VDDIO (x2), VDDIN, VDDANA, GNDANA, GND (x3), VDDCORE
      ~{RESET}
    """
    global VCC, GND

    mcu = Part("MCU_Microchip_SAMD", "ATSAMD21G18A-A",
               footprint="Package_QFP:TQFP-48_7x7mm_P0.5mm")
    mcu.ref = "U1"

    # Power pins - TQFP-48 uses VDDIO, VDDIN, VDDANA
    mcu["VDDIO"]  += vcc   # pins 17 and 36 (same name, both connect)
    mcu["VDDIN"]  += vcc   # pin 44 (USB power input)
    mcu["VDDANA"] += vcc   # pin 6 (analog supply)
    mcu["GND"]    += gnd   # pins 18, 35, 42
    mcu["GNDANA"] += gnd   # pin 5

    # VDDCORE needs a 100nF cap to GND (internally regulated)
    c_core = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_core[1] += mcu["VDDCORE"]
    c_core[2] += gnd

    # USB
    mcu["PA24"] += usb_dm   # D-
    mcu["PA25"] += usb_dp   # D+

    # SWD / RESET
    mcu["~{RESET}"] += reset_n
    mcu["PA30"]     += swdclk
    mcu["PA31"]     += swdio

    # I2C SERCOM3: PA22=SCL, PA23=SDA
    mcu["PA22"] += scl    # SCL also routed to alligator pad at net level
    mcu["PA23"] += sda

    # SPI to LIS3DH: use SERCOM1 PA16/PA17/PA18/PA19 to avoid I2C conflict
    # PA16=MOSI(SERCOM1 pad0), PA17=SCK(pad1), PA18=MISO(pad2), PA19=CS(pad3)
    mcu["PA18"] += mosi
    mcu["PA19"] += sck
    mcu["PA16"] += miso
    mcu["PA17"] += lis3dh_cs

    # NeoPixel data output
    mcu["PA06"] += neopixel

    # IR TX: PA02 (TCCx/WO for carrier)
    mcu["PA02"] += ir_tx
    # IR RX: PA03
    mcu["PA03"] += ir_rx

    # Buttons - active low with pull-up
    mcu["PA04"] += btn_a
    mcu["PA05"] += btn_b

    # Slide switch
    mcu["PA07"] += slide_sw

    # Speaker: PA02 is IR_TX (PWM modulated). Audio out uses PA00 (DAC on SAMD21).
    # CPX uses class-D via PA02, but we separate concerns: use PA12 for speaker
    mcu["PA12"] += speaker

    # Speaker enable
    mcu["PA14"] += spk_en

    # Microphone I2S: PA20=BCLK, PA21=DATA, PA13=WS
    mcu["PA20"] += mic_clk
    mcu["PA21"] += mic_data
    mcu["PA13"] += mic_sel   # WS/LRCLK

    # LIS3DH interrupt
    mcu["PA28"] += lis3dh_int

    # ADC inputs
    mcu["PB02"] += light_adc   # AIN10
    mcu["PB03"] += temp_adc    # AIN11

    # Alligator pad GPIOs - separate pads on dedicated pins
    # CPX has 10 pads: A0-A6, TX, RX, and shared GND/3.3V/VBAT
    mcu["PA00"] += a0    # A0
    mcu["PA01"] += a1    # A1
    mcu["PB08"] += a2    # A2
    mcu["PB09"] += a3    # A3
    mcu["PA09"] += a4    # A4 (separate from BTN_A which is PA04)
    mcu["PA08"] += a5    # A5 (separate from BTN_B which is PA05)
    mcu["PB10"] += a6    # A6 (separate from NEOPIXEL on PA06)
    mcu["PB22"] += tx    # TX SERCOM5
    mcu["PB23"] += rx    # RX SERCOM5
    # PA11 available but used for LIS3DH CS above; PB11 free

    # Decoupling caps on VDDIO / VDDIN / VDDANA (100nF each)
    for i in range(5):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vcc
        c[2] += gnd

    # Bulk 10uF
    cbulk = Part("Device", "C_Polarized", value="10uF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    cbulk[1] += vcc
    cbulk[2] += gnd


@subcircuit
def power_supply(vusb, vbat, vcc, gnd):
    """AP2112K-3.3 LDO."""
    global VCC, GND

    ldo = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    ldo.ref = "U2"
    ldo["EN"]   += vusb   # EN high = on
    ldo["GND"]  += gnd
    ldo["VIN"]  += vusb
    ldo["VOUT"] += vcc
    ldo["NC"]   += gnd    # NC pin tied to GND

    cin = Part("Device", "C", value="1uF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cin[1] += vusb
    cin[2] += gnd

    cout = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cout[1] += vcc
    cout[2] += gnd

    cout2 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    cout2[1] += vcc
    cout2[2] += gnd


@subcircuit
def usb_connector(vusb, gnd, usb_dp, usb_dm):
    """USB Micro-B connector."""
    global VCC, GND

    usb = Part("Connector", "USB_B_Micro",
               footprint="Connector_USB:USB_Micro-B_Amphenol_10103594-0001LF_Horizontal")
    usb.ref = "J1"
    usb["VBUS"]   += vusb
    usb["GND"]    += gnd
    usb["D-"]     += usb_dm
    usb["D+"]     += usb_dp
    usb["ID"]     += gnd
    usb["Shield"] += gnd

    cv = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    cv[1] += vusb
    cv[2] += gnd

    # 22-ohm USB series resistors
    usb_dp_mcu = Net("USB_DP_MCU")
    rdp = Part("Device", "R", value="22",
               footprint="Resistor_SMD:R_0402_1005Metric")
    rdp[1] += usb_dp
    rdp[2] += usb_dp_mcu

    usb_dm_mcu = Net("USB_DM_MCU")
    rdm = Part("Device", "R", value="22",
               footprint="Resistor_SMD:R_0402_1005Metric")
    rdm[1] += usb_dm
    rdm[2] += usb_dm_mcu


@subcircuit
def neopixel_ring(vcc, gnd, din):
    """10x WS2812B NeoPixels in daisy-chain ring.

    WS2812 pins: VDD(5), VCC(3), VSS(6), DIN(2), DOUT(1), NC(4)
    VDD and VCC are both power; VSS is ground.
    """
    global VCC, GND

    prev_dout = din
    for i in range(10):
        px = Part("LED", "WS2812",
                  footprint="LED_SMD:LED_WS2812_PLCC6_5.0x5.0mm_P1.6mm")
        px.ref = f"D{i+1}"
        px["VDD"] += vcc
        px["VCC"] += vcc
        px["VSS"] += gnd
        px["NC"]  += gnd   # NC tied to GND by pad number
        px["DIN"] += prev_dout
        dout_net = Net(f"NP_DOUT_{i}")
        px["DOUT"] += dout_net
        prev_dout = dout_net

        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += vcc
        c[2] += gnd

    # Bulk cap for NeoPixel ring inrush
    cbulk = Part("Device", "C_Polarized", value="100uF",
                 footprint="Capacitor_SMD:C_1210_3225Metric")
    cbulk[1] += vcc
    cbulk[2] += gnd


@subcircuit
def accelerometer(vcc, gnd, scl, sda, cs_net, int1):
    """LIS3DH via I2C (CS=high, SDO/SA0=low for addr 0x18).

    LIS3DH pins: VDD(14), VDD_IO(1), GND(5,10,12),
                 SPC(4), SDI(6), SDO(7), CS(8),
                 INT1(11), INT2(9), NC(2,3), ADC1-3(13-16)
    """
    global VCC, GND

    lis = Part("Sensor_Motion", "LIS3DH",
               footprint="Package_LGA:LGA-16_3x3mm_P0.5mm_LayoutBorder3x5y")
    lis.ref = "U3"
    # Use pin numbers to avoid alternate-pin-name confusion
    # (LIS3DH SDI pin has alternate "SDA" which confuses net-name resolution)
    # Pin map: 1=VDD_IO, 2=NC, 3=NC, 4=SPC, 5=GND, 6=SDI, 7=SDO,
    #          8=CS, 9=INT2, 10=GND, 11=INT1, 12=GND, 13=ADC3,
    #          14=VDD, 15=ADC2, 16=ADC1
    lis[14]  += vcc    # VDD
    lis[1]   += vcc    # VDD_IO
    lis[5]   += gnd    # GND
    lis[10]  += gnd    # GND
    lis[12]  += gnd    # GND
    lis[4]   += scl    # SPC -> SCL
    lis[6]   += sda    # SDI -> SDA
    lis[7]   += gnd    # SDO/SA0 -> GND (I2C addr 0x18)
    lis[8]   += cs_net # CS -> LIS3DH_CS (high = I2C mode)
    lis[11]  += int1   # INT1
    lis[9]   += gnd    # INT2
    lis[2]   += gnd    # NC
    lis[3]   += gnd    # NC
    lis[13]  += gnd    # ADC3 (unused)
    lis[15]  += gnd    # ADC2 (unused)
    lis[16]  += gnd    # ADC1 (unused)

    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += vcc
    c1[2] += gnd

    c2 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2[1] += vcc
    c2[2] += gnd


@subcircuit
def mems_microphone(vcc, gnd, data, clk, sel):
    """SPH0645LM4H I2S microphone.

    Pins: WS(1), SEL(2), GND(3), BCLK(4), VDD(5), DATA(6)
    """
    global VCC, GND

    mic = Part("Sensor_Audio", "SPH0645LM4H",
               footprint="Sensor_Audio:Knowles_SPH0645LM4H-6_3.5x2.65mm")
    mic.ref = "U4"
    mic["VDD"]  += vcc
    mic["GND"]  += gnd
    mic["BCLK"] += clk
    mic["DATA"] += data    # DOUT in previous search was wrong; actual pin name is DATA
    mic["WS"]   += sel     # word select = LRCLK
    mic["SEL"]  += gnd     # left channel select

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += vcc
    c[2] += gnd


@subcircuit
def light_sensor(vcc, gnd, adc_out):
    """ALS-PT19 phototransistor (R_Photo symbol) + pull-down for analog light sensing."""
    global VCC, GND

    pt = Part("Device", "R_Photo",
              footprint="Resistor_SMD:R_0402_1005Metric")
    pt.ref = "U5"
    pt[1] += vcc
    pt[2] += adc_out

    r = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0402_1005Metric")
    r[1] += adc_out
    r[2] += gnd


@subcircuit
def temperature_sensor(vcc, gnd, adc_out):
    """NTC thermistor voltage divider."""
    global VCC, GND

    ntc = Part("Device", "Thermistor_NTC",
               footprint="Resistor_SMD:R_0402_1005Metric")
    ntc.ref = "RT1"
    ntc[1] += vcc
    ntc[2] += adc_out

    r_series = Part("Device", "R", value="10K",
                    footprint="Resistor_SMD:R_0402_1005Metric")
    r_series[1] += adc_out
    r_series[2] += gnd


@subcircuit
def ir_transceiver(vcc, gnd, ir_tx_net, ir_rx_net):
    """IR 940nm LED + IS485 IR receiver.

    IS485 pins: GND(1), OUT(2), Vs(3)
    """
    global VCC, GND

    ir_led = Part("LED", "IR26-21C_L110_TR8",
                  footprint="LED_SMD:LED_1206_3216Metric")
    ir_led.ref = "D11"
    ir_anode = Net("IR_LED_ANODE")
    ir_led["A"] += ir_anode
    ir_led["K"] += gnd

    # Current limiting resistor; MCU drives gate via NPN or direct PA02 PWM
    r_ir = Part("Device", "R", value="33",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_ir[1] += vcc
    r_ir[2] += ir_anode

    ir_recv = Part("Interface_Optical", "IS485",
                   footprint="OptoDevice:Sharp_IS485")
    ir_recv.ref = "U6"
    ir_recv["Vs"]  += vcc    # pin 3 = Vs (supply)
    ir_recv["GND"] += gnd    # pin 1 = GND
    ir_recv["OUT"] += ir_rx_net   # pin 2 = output

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += vcc
    c[2] += gnd


@subcircuit
def speaker_circuit(vcc, gnd, spk_in, spk_en_net):
    """Mini speaker connector with DC-blocking coupling cap."""
    global VCC, GND

    spk_conn = Part("Connector", "Conn_01x02_Pin",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    spk_conn.ref = "J3"
    spk_conn[1] += spk_in
    spk_conn[2] += gnd

    # DC-blocking cap between DAC output and speaker
    spk_out = Net("SPK_CAP_OUT")
    c_spk = Part("Device", "C_Polarized", value="100uF",
                 footprint="Capacitor_SMD:C_1210_3225Metric")
    c_spk[1] += spk_in
    c_spk[2] += spk_out

    # Speaker enable pull-down resistor
    r_spk = Part("Device", "R", value="100",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    spk_en_r = Net("SPK_EN_R")
    r_spk[1] += spk_en_net
    r_spk[2] += spk_en_r


@subcircuit
def buttons_and_switch(vcc, gnd, btn_a_net, btn_b_net, slide_net):
    """2 push buttons + SPDT slide switch.

    SW_Push pins: 1, 2 (passive)
    SW_SPDT pins: A, B, C (passive)
    """
    global VCC, GND

    sw_a = Part("Switch", "SW_Push",
                footprint="Button_Switch_SMD:SW_SPST_EVPBF")
    sw_a.ref = "SW1"
    sw_a[1] += btn_a_net
    sw_a[2] += gnd

    r_a = Part("Device", "R", value="10K",
               footprint="Resistor_SMD:R_0402_1005Metric")
    r_a[1] += vcc
    r_a[2] += btn_a_net

    sw_b = Part("Switch", "SW_Push",
                footprint="Button_Switch_SMD:SW_SPST_EVPBF")
    sw_b.ref = "SW2"
    sw_b[1] += btn_b_net
    sw_b[2] += gnd

    r_b = Part("Device", "R", value="10K",
               footprint="Resistor_SMD:R_0402_1005Metric")
    r_b[1] += vcc
    r_b[2] += btn_b_net

    slide = Part("Switch", "SW_SPDT",
                 footprint="Button_Switch_SMD:SW_SPDT_PCM12")
    slide.ref = "SW3"
    slide["A"] += vcc
    slide["B"] += gnd
    slide["C"] += slide_net


@subcircuit
def battery_connector(vbat, gnd):
    """JST PH 2-pin battery connector + Schottky diode to VUSB."""
    global VCC, GND, VUSB

    jst = Part("Connector", "Conn_01x02_Pin",
               footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    jst.ref = "J2"
    jst[1] += vbat
    jst[2] += gnd

    # Schottky diode: battery feeds VUSB rail when USB not present
    d = Part("Device", "D", value="BAT54",
             footprint="Diode_SMD:D_SOD-323")
    d["A"] += vbat
    d["K"] += VUSB


@subcircuit
def alligator_pads(vcc, gnd,
                   a0, a1, a2, a3, a4, a5, a6, tx, rx, scl):
    """10 alligator-clip edge pads around board perimeter.
    The SCL pad shares the I2C SCL net.
    Use 2-pin headers as proxy for large copper pads.
    """
    global VCC, GND

    # Represent with a single 10-pad connector to reduce part count
    # (In real PCB, these are individual large copper pads on edge)
    pad_defs = [
        ("A0",  a0),
        ("A1",  a1),
        ("A2",  a2),
        ("A3",  a3),
        ("A4",  a4),
        ("A5",  a5),
        ("A6",  a6),
        ("TX",  tx),
        ("RX",  rx),
        ("SCL", scl),
    ]
    for name, net in pad_defs:
        pad = Part("Connector", "Conn_01x01_Pin",
                   footprint="TestPoint:TestPoint_Pad_D2.5mm")
        pad.ref = f"TP_{name}"
        pad[1] += net


@subcircuit
def reset_circuit(vcc, gnd, reset_n, swdclk, swdio):
    """Reset button + RC filter + SWD debug header."""
    global VCC, GND

    sw_rst = Part("Switch", "SW_Push",
                  footprint="Button_Switch_SMD:SW_SPST_EVPBF")
    sw_rst.ref = "SW4"
    sw_rst[1] += reset_n
    sw_rst[2] += gnd

    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vcc
    r_rst[2] += reset_n

    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_rst[1] += reset_n
    c_rst[2] += gnd

    # SWD 4-pin debug header
    swd = Part("Connector", "Conn_01x04_Pin",
               footprint="Connector_PinHeader_1.27mm:PinHeader_1x04_P1.27mm_Vertical")
    swd.ref = "J4"
    swd[1] += swdclk
    swd[2] += swdio
    swd[3] += vcc
    swd[4] += gnd


# ── Instantiate all subcircuits ────────────────────────────────────────────

mcu_core(
    vcc=VCC, gnd=GND,
    usb_dp=USB_DP, usb_dm=USB_DM,
    scl=SCL, sda=SDA,
    mosi=MOSI, miso=MISO, sck=SCK,
    neopixel=NEOPIXEL_DATA,
    ir_tx=IR_TX, ir_rx=IR_RX,
    btn_a=BTN_A, btn_b=BTN_B,
    slide_sw=SLIDE_SW,
    speaker=SPEAKER, spk_en=SPK_EN,
    mic_data=MIC_DATA, mic_clk=MIC_CLK, mic_sel=MIC_SEL,
    light_adc=LIGHT_ADC, temp_adc=TEMP_ADC,
    lis3dh_cs=LIS3DH_CS, lis3dh_int=LIS3DH_INT,
    reset_n=RESET_N, swdclk=SWDCLK, swdio=SWDIO,
    a0=PAD_A0, a1=PAD_A1, a2=PAD_A2, a3=PAD_A3, a4=PAD_A4,
    a5=PAD_A5, a6=PAD_A6, tx=PAD_TX, rx=PAD_RX,
)

power_supply(vusb=VUSB, vbat=VBAT, vcc=VCC, gnd=GND)

usb_connector(vusb=VUSB, gnd=GND, usb_dp=USB_DP, usb_dm=USB_DM)

neopixel_ring(vcc=VCC, gnd=GND, din=NEOPIXEL_DATA)

accelerometer(vcc=VCC, gnd=GND, scl=SCL, sda=SDA,
              cs_net=LIS3DH_CS, int1=LIS3DH_INT)

mems_microphone(vcc=VCC, gnd=GND, data=MIC_DATA, clk=MIC_CLK, sel=MIC_SEL)

light_sensor(vcc=VCC, gnd=GND, adc_out=LIGHT_ADC)

temperature_sensor(vcc=VCC, gnd=GND, adc_out=TEMP_ADC)

ir_transceiver(vcc=VCC, gnd=GND, ir_tx_net=IR_TX, ir_rx_net=IR_RX)

speaker_circuit(vcc=VCC, gnd=GND, spk_in=SPEAKER, spk_en_net=SPK_EN)

buttons_and_switch(vcc=VCC, gnd=GND,
                   btn_a_net=BTN_A, btn_b_net=BTN_B, slide_net=SLIDE_SW)

battery_connector(vbat=VBAT, gnd=GND)

alligator_pads(
    vcc=VCC, gnd=GND,
    a0=PAD_A0, a1=PAD_A1, a2=PAD_A2, a3=PAD_A3, a4=PAD_A4,
    a5=PAD_A5, a6=PAD_A6, tx=PAD_TX, rx=PAD_RX, scl=SCL,
)

reset_circuit(vcc=VCC, gnd=GND, reset_n=RESET_N,
              swdclk=SWDCLK, swdio=SWDIO)


if __name__ == "__main__":
    generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
