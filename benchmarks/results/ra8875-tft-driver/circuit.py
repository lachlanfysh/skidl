"""
RA8875 TFT Display Driver Breakout
===================================
RAIO RA8875 hardware-accelerated TFT display controller breakout.
- RA8875 LQFP-100 (tool=SKIDL, minimal pin set to avoid schematic timeout)
- SPI host interface up to 20MHz, 40-pin FPC for TFT panel
- AP2112K-3.3 LDO regulator (5V in, 3.3V out)
- 20MHz crystal oscillator
- 100nF decoupling on all 7 VDD pins + 1uF VDDCORE bypass
- N-MOSFET (BSS138) PWM backlight driver
- SPI breakout header for host MCU
- 4-wire resistive touch input filter
- Board ~60x35mm
"""

from skidl import *


def _init_skidl_pins(part):
    """Set schematic geometry on SKIDL-defined part pins.

    Without x/y/orientation/draw_cmds, the global schematic router crashes
    with AttributeError on pin.face. This synthesises the geometry so the
    router can compute bounding boxes and assign face attributes.
    """
    spacing_mm = 2.54
    pin_length_mm = 2.54
    n = len(part.pins)

    left_count = (n + 1) // 2
    body_h = max(left_count, 1) * spacing_mm
    body_w = max(5.08, body_h * 0.6)

    draw_cmds = []
    for idx, pin in enumerate(part.pins):
        if idx < left_count:
            pin.x = -(body_w / 2 + pin_length_mm)
            pin.y = body_h / 2 - idx * spacing_mm
            pin.orientation = "R"
            pin.rotation = 0
        else:
            row = idx - left_count
            pin.x = body_w / 2 + pin_length_mm
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "L"
            pin.rotation = 180
        pin.length = pin_length_mm

        draw_cmds.append([
            "pin", "passive", "line",
            ["at", pin.x, pin.y, int(pin.rotation)],
            ["length", pin_length_mm],
            ["name", pin.name, ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num), ["effects", ["font", ["size", 1.27, 1.27]]]],
        ])

    draw_cmds.append([
        "rectangle",
        ["start", -body_w / 2, -body_h / 2 - spacing_mm / 2],
        ["end",    body_w / 2,  body_h / 2 + spacing_mm / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill",   ["type", "none"]],
    ])
    part.draw_cmds = {1: draw_cmds, 0: draw_cmds}

    if not hasattr(part, "lib") or part.lib is None:
        class _MockLib:
            def __init__(self, name):
                self.filename = name
        part.lib = _MockLib("skidl_custom")


# ============================================================
# Power Nets
# ============================================================
vin_net = Net("VIN");   vin_net.drive = POWER
v3v3    = Net("+3V3");  v3v3.drive    = POWER
gnd     = Net("GND");   gnd.drive     = POWER

spi_sck  = Net("SPI_SCK")
spi_mosi = Net("SPI_MOSI")
spi_miso = Net("SPI_MISO")
spi_cs   = Net("SPI_CS")

int_net  = Net("INT")
rst_net  = Net("nRESET")
wait_net = Net("WAIT")

tp_xp = Net("TP_XP")
tp_xn = Net("TP_XN")
tp_yp = Net("TP_YP")
tp_yn = Net("TP_YN")

tft_data  = [Net(f"DB{i}") for i in range(16)]
tft_hsync = Net("HSYNC")
tft_vsync = Net("VSYNC")
tft_de    = Net("DE")
tft_pclk  = Net("PCLK")

xi_net   = Net("XI")
xo_net   = Net("XO")
bl_pwm   = Net("BL_PWM")
bl_drive = Net("BL_DRIVE")
vddcore  = Net("VDDCORE")


# ============================================================
# RA8875 Display Controller (LQFP-100, tool=SKIDL)
# Only wired pins included — NC pins omitted to reduce schematic load.
# ============================================================
@subcircuit
def ra8875_controller(vdd, gnd_net, vddcore_net,
                      sck, mosi, miso, cs,
                      int_pin, rst_pin, wait_pin,
                      xi, xo,
                      db, hsync, vsync, de, pclk,
                      txp, txn, typ, tyn,
                      bl):
    u = Part(name="RA8875DBXG", tool=SKIDL, dest=NETLIST,
             footprint="Package_QFP:LQFP-100_14x14mm_P0.5mm",
             pins=[
                 # Power (7x VDD + 7x VSS)
                 Pin(num="8",   name="VDD1",  func=Pin.types.PWRIN),
                 Pin(num="22",  name="VDD2",  func=Pin.types.PWRIN),
                 Pin(num="37",  name="VDD3",  func=Pin.types.PWRIN),
                 Pin(num="54",  name="VDD4",  func=Pin.types.PWRIN),
                 Pin(num="67",  name="VDD5",  func=Pin.types.PWRIN),
                 Pin(num="82",  name="VDD6",  func=Pin.types.PWRIN),
                 Pin(num="96",  name="VDD7",  func=Pin.types.PWRIN),
                 Pin(num="9",   name="VSS1",  func=Pin.types.PWRIN),
                 Pin(num="23",  name="VSS2",  func=Pin.types.PWRIN),
                 Pin(num="38",  name="VSS3",  func=Pin.types.PWRIN),
                 Pin(num="55",  name="VSS4",  func=Pin.types.PWRIN),
                 Pin(num="68",  name="VSS5",  func=Pin.types.PWRIN),
                 Pin(num="83",  name="VSS6",  func=Pin.types.PWRIN),
                 Pin(num="97",  name="VSS7",  func=Pin.types.PWRIN),
                 Pin(num="100", name="VDDCORE", func=Pin.types.PWROUT),
                 # SPI host interface
                 Pin(num="71",  name="SCLK",  func=Pin.types.INPUT),
                 Pin(num="72",  name="SDI",   func=Pin.types.INPUT),
                 Pin(num="73",  name="SDO",   func=Pin.types.OUTPUT),
                 Pin(num="74",  name="SCS",   func=Pin.types.INPUT),
                 # Control
                 Pin(num="75",  name="INT",   func=Pin.types.OUTPUT),
                 Pin(num="76",  name="WAIT",  func=Pin.types.OUTPUT),
                 Pin(num="77",  name="RESET", func=Pin.types.INPUT),
                 # Crystal
                 Pin(num="98",  name="XI",    func=Pin.types.INPUT),
                 Pin(num="99",  name="XO",    func=Pin.types.OUTPUT),
                 # TFT data bus
                 Pin(num="39",  name="DB0",   func=Pin.types.OUTPUT),
                 Pin(num="40",  name="DB1",   func=Pin.types.OUTPUT),
                 Pin(num="41",  name="DB2",   func=Pin.types.OUTPUT),
                 Pin(num="42",  name="DB3",   func=Pin.types.OUTPUT),
                 Pin(num="43",  name="DB4",   func=Pin.types.OUTPUT),
                 Pin(num="44",  name="DB5",   func=Pin.types.OUTPUT),
                 Pin(num="45",  name="DB6",   func=Pin.types.OUTPUT),
                 Pin(num="46",  name="DB7",   func=Pin.types.OUTPUT),
                 Pin(num="47",  name="DB8",   func=Pin.types.OUTPUT),
                 Pin(num="48",  name="DB9",   func=Pin.types.OUTPUT),
                 Pin(num="49",  name="DB10",  func=Pin.types.OUTPUT),
                 Pin(num="50",  name="DB11",  func=Pin.types.OUTPUT),
                 Pin(num="51",  name="DB12",  func=Pin.types.OUTPUT),
                 Pin(num="52",  name="DB13",  func=Pin.types.OUTPUT),
                 Pin(num="53",  name="DB14",  func=Pin.types.OUTPUT),
                 Pin(num="56",  name="DB15",  func=Pin.types.OUTPUT),
                 # TFT control
                 Pin(num="33",  name="HSYNC", func=Pin.types.OUTPUT),
                 Pin(num="34",  name="VSYNC", func=Pin.types.OUTPUT),
                 Pin(num="35",  name="DE",    func=Pin.types.OUTPUT),
                 Pin(num="36",  name="PCLK",  func=Pin.types.OUTPUT),
                 # Touch interface
                 Pin(num="84",  name="TP_XP", func=Pin.types.BIDIR),
                 Pin(num="85",  name="TP_XN", func=Pin.types.BIDIR),
                 Pin(num="86",  name="TP_YP", func=Pin.types.BIDIR),
                 Pin(num="87",  name="TP_YN", func=Pin.types.BIDIR),
                 # PWM backlight
                 Pin(num="78",  name="PWM1",  func=Pin.types.OUTPUT),
                 # Config pins
                 Pin(num="92",  name="PS",    func=Pin.types.INPUT),
                 Pin(num="93",  name="SIFT",  func=Pin.types.INPUT),
                 Pin(num="10",  name="STBY",  func=Pin.types.INPUT),
             ])
    u.ref = "U2"
    _init_skidl_pins(u)

    # Power
    for i in range(1, 8):
        u[f"VDD{i}"] += vdd
        u[f"VSS{i}"] += gnd_net
    u["VDDCORE"] += vddcore_net

    # SPI
    u["SCLK"]  += sck
    u["SDI"]   += mosi
    u["SDO"]   += miso
    u["SCS"]   += cs

    # Control
    u["INT"]   += int_pin
    u["WAIT"]  += wait_pin
    u["RESET"] += rst_pin

    # Crystal
    u["XI"]    += xi
    u["XO"]    += xo

    # TFT data
    for i in range(16):
        u[f"DB{i}"] += db[i]

    # TFT control
    u["HSYNC"] += hsync
    u["VSYNC"] += vsync
    u["DE"]    += de
    u["PCLK"]  += pclk

    # Touch
    u["TP_XP"] += txp
    u["TP_XN"] += txn
    u["TP_YP"] += typ
    u["TP_YN"] += tyn

    # Backlight
    u["PWM1"]  += bl

    # Config: SPI mode, always on
    u["PS"]    += gnd_net
    u["SIFT"]  += gnd_net
    u["STBY"]  += vdd


# ============================================================
# 3.3V LDO: AP2112K-3.3 SOT-23-5
# ============================================================
@subcircuit
def voltage_regulator(vin, vout, gnd_net):
    u = Part("Regulator_Linear", "AP2112K-3.3",
             footprint="Package_TO_SOT_SMD:SOT-23-5",
             value="AP2112K-3.3")
    u["VIN"] += vin; u["VOUT"] += vout; u["GND"] += gnd_net
    u["EN"]  += vin; u["NC"]   += NC

    cin  = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_SMD:CP_Elec_4x5.4")
    cout = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_SMD:CP_Elec_4x5.4")
    cf   = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cin[1]  += vin;  cin[2]  += gnd_net
    cout[1] += vout; cout[2] += gnd_net
    cf[1]   += vout; cf[2]   += gnd_net


# ============================================================
# RA8875 Decoupling: 7x 100nF + 2x 10uF + 1uF VDDCORE
# ============================================================
@subcircuit
def ra8875_decoupling(vdd, gnd_net, vddcore_net):
    for _ in range(7):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vdd; c[2] += gnd_net

    for _ in range(2):
        cb = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:CP_Elec_4x5.4")
        cb[1] += vdd; cb[2] += gnd_net

    c_core = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_core[1] += vddcore_net; c_core[2] += gnd_net


# ============================================================
# 20MHz Crystal Oscillator
# ============================================================
@subcircuit
def crystal_osc(xi, xo, gnd_net):
    xtal = Part("Device", "Crystal", value="20MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xi; xtal[2] += xo

    cxi = Part("Device", "C", value="18pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cxo = Part("Device", "C", value="18pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cxi[1] += xi; cxi[2] += gnd_net
    cxo[1] += xo; cxo[2] += gnd_net


# ============================================================
# SPI Pull-ups and Reset RC
# ============================================================
@subcircuit
def spi_conditioning(vdd, gnd_net, cs, int_pin, rst_pin, wait_pin):
    for sig in [cs, int_pin, wait_pin]:
        r = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
        r[1] += vdd; r[2] += sig

    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    r_rst[1] += vdd;   r_rst[2] += rst_pin
    c_rst[1] += rst_pin; c_rst[2] += gnd_net


# ============================================================
# Touch Filter: 1K + 100pF per line
# ============================================================
@subcircuit
def touch_filter(txp, txn, typ, tyn, gnd_net):
    out_nets = []
    for sig_in, name in [(txp, "TP_XP_F"), (txn, "TP_XN_F"),
                          (typ, "TP_YP_F"), (tyn, "TP_YN_F")]:
        sig_out = Net(name)
        r = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
        c = Part("Device", "C", value="100pF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        r[1] += sig_in; r[2] += sig_out
        c[1] += sig_out; c[2] += gnd_net
        out_nets.append(sig_out)
    return out_nets


# ============================================================
# Backlight Driver: BSS138 N-MOSFET
# ============================================================
@subcircuit
def backlight_driver(pwm_in, vdd, gnd_net, bl_out):
    q = Part("Transistor_FET", "BSS138",
             footprint="Package_TO_SOT_SMD:SOT-23",
             value="BSS138")
    q["G"] += pwm_in; q["S"] += gnd_net; q["D"] += bl_out

    rpd  = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    rcur = Part("Device", "R", value="10R",
                footprint="Resistor_SMD:R_0805_2012Metric")
    rpd[1]  += pwm_in; rpd[2]  += gnd_net
    rcur[1] += vdd;    rcur[2] += bl_out


# ============================================================
# SPI Host Header (1x10, 2.54mm pitch)
# ============================================================
@subcircuit
def spi_header(vin, v3v3_net, gnd_net, sck, mosi, miso, cs,
               int_pin, rst_pin, wait_pin):
    j = Part("Connector_Generic", "Conn_01x10",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical",
             value="SPI_HDR")
    j[1]  += vin;      j[2]  += v3v3_net; j[3]  += gnd_net
    j[4]  += sck;      j[5]  += mosi;     j[6]  += miso
    j[7]  += cs;       j[8]  += int_pin;  j[9]  += rst_pin
    j[10] += wait_pin


# ============================================================
# 40-Pin FPC TFT Connector (Hirose FH12-40S-0.5SH compatible)
# ============================================================
@subcircuit
def tft_fpc_connector(v3v3_net, gnd_net,
                      db, hsync, vsync, de, pclk,
                      tp_f, bl_out_net):
    j = Part("Connector_Generic", "Conn_01x40",
             footprint="Connector_FFC-FPC:Hirose_FH12-40S-0.5SH_1x40-1MP_P0.50mm_Horizontal",
             value="TFT_FPC_40P")
    j[1]  += gnd_net; j[2]  += gnd_net; j[3]  += v3v3_net
    for i in range(16):
        j[4 + i] += db[i]
    j[20] += gnd_net; j[21] += gnd_net
    j[22] += hsync;   j[23] += vsync; j[24] += de; j[25] += pclk
    for pin_n in [26, 27, 28, 29]:
        j[pin_n] += gnd_net
    j[30] += tp_f[0]; j[31] += tp_f[1]; j[32] += tp_f[2]; j[33] += tp_f[3]
    for pin_n in [34, 35, 36]:
        j[pin_n] += gnd_net
    j[37] += v3v3_net   # LED anode
    j[38] += bl_out_net # LED cathode via MOSFET
    j[39] += gnd_net; j[40] += gnd_net


# ============================================================
# Top-level instantiation
# ============================================================
voltage_regulator(vin_net, v3v3, gnd)
ra8875_decoupling(v3v3, gnd, vddcore)
crystal_osc(xi_net, xo_net, gnd)
spi_conditioning(v3v3, gnd, spi_cs, int_net, rst_net, wait_net)
tp_filtered = touch_filter(tp_xp, tp_xn, tp_yp, tp_yn, gnd)
backlight_driver(bl_pwm, v3v3, gnd, bl_drive)
spi_header(vin_net, v3v3, gnd, spi_sck, spi_mosi, spi_miso, spi_cs,
           int_net, rst_net, wait_net)
tft_fpc_connector(v3v3, gnd,
                  tft_data, tft_hsync, tft_vsync, tft_de, tft_pclk,
                  tp_filtered, bl_drive)
ra8875_controller(v3v3, gnd, vddcore,
                  spi_sck, spi_mosi, spi_miso, spi_cs,
                  int_net, rst_net, wait_net,
                  xi_net, xo_net,
                  tft_data, tft_hsync, tft_vsync, tft_de, tft_pclk,
                  tp_xp, tp_xn, tp_yp, tp_yn,
                  bl_pwm)

EDA_FLOORPLAN = {
    "outline": {"width_mm": 60.0, "height_mm": 35.0},
    "edge_anchors": [
        {"ref": "J1", "edge": "right", "offset_mm": 17.5},
        {"ref": "J2", "edge": "left",  "offset_mm": 17.5},
    ],
}
