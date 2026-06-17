"""
Feather M4 Express SAMD51 — SKiDL circuit description.

Adafruit Feather M4 Express with ATSAMD51J20A-AUT (TQFP-64, 120MHz Cortex-M4F),
2MB SPI flash (GD25Q16), USB Micro-B, LiPo charging (MCP73831), 3.3V LDO (AP2112K-3.3),
32.768kHz crystal, WS2812B NeoPixel, and standard Feather headers.

Parts sourced from:
  MCU:   LCSC C619371  -> Part("C619371", "ATSAMD51J20A-AUT", ...)
  Flash: LCSC C2904431 -> Part("C2904431", "GD25Q16ETIGR", ...)
  LDO:   LCSC C51118   -> Part("C51118", "AP2112K-3.3TRG1", ...)
  CHG:   KiCad Battery_Management:MCP73831-2-OT
"""

EDA_FLOORPLAN = {
    "outline": {"width_mm": 52.0, "height_mm": 23.0, "corner_radius_mm": 1.0},
    "edge_anchors": [
        {"ref": "J1", "edge": "bottom"},
    ],
}

from skidl import *

# ── Power Nets ────────────────────────────────────────────────
vbus  = Net("VBUS");  vbus.drive  = POWER
vbat  = Net("VBAT");  vbat.drive  = POWER
v3v3  = Net("+3V3");  v3v3.drive  = POWER
gnd   = Net("GND");   gnd.drive   = POWER

# ── Signal Nets ───────────────────────────────────────────────
usb_dp       = Net("USB_DP")
usb_dm       = Net("USB_DM")
qspi_sck     = Net("QSPI_SCK")
qspi_cs      = Net("QSPI_CS")
qspi_io0     = Net("QSPI_IO0")
qspi_io1     = Net("QSPI_IO1")
qspi_io2     = Net("QSPI_IO2")
qspi_io3     = Net("QSPI_IO3")
neopixel_dat = Net("NEOPIXEL")
reset_n      = Net("RESET_N")
swdio        = Net("SWDIO")
swdclk       = Net("SWDCLK")
chg_stat     = Net("CHG_STAT")
sda          = Net("SDA")
scl          = Net("SCL")
uart_tx      = Net("TX")
uart_rx      = Net("RX")
a0 = Net("A0"); a1 = Net("A1"); a2 = Net("A2")
a3 = Net("A3"); a4 = Net("A4"); a5 = Net("A5")
d4  = Net("D4");  d5  = Net("D5");  d6  = Net("D6")
d9  = Net("D9");  d10 = Net("D10"); d11 = Net("D11")
d12 = Net("D12"); d13 = Net("D13")


# ═══════════════════════════════════════════════════════════════
#  USB Micro-B connector + input decoupling
# ═══════════════════════════════════════════════════════════════
@subcircuit
def usb_connector(p_vbus, p_gnd, p_usb_dp, p_usb_dm):
    j1 = Part("Connector", "USB_B_Micro",
               footprint="Connector_USB:USB_Micro-B_Molex-105017-0001",
               value="USB_Micro-B")
    j1["VBUS"]   += p_vbus
    j1["GND"]    += p_gnd
    j1["D+"]     += p_usb_dp
    j1["D-"]     += p_usb_dm
    j1["Shield"] += p_gnd
    j1["ID"]     += p_gnd

    c1 = Part("Device", "C", value="10uF",
               footprint="Capacitor_SMD:C_0805_2012Metric")
    c1[1] += p_vbus; c1[2] += p_gnd

    c2 = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    c2[1] += p_vbus; c2[2] += p_gnd

usb_connector(vbus, gnd, usb_dp, usb_dm)


# ═══════════════════════════════════════════════════════════════
#  LiPo Battery Charger (MCP73831-2-OT, SOT-23-5)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def lipo_charger(p_vbus, p_vbat, p_gnd, p_chg_stat):
    chg = Part("Battery_Management", "MCP73831-2-OT",
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               value="MCP73831")
    chg["V_{DD}"]  += p_vbus
    chg["V_{BAT}"] += p_vbat
    chg["V_{SS}"]  += p_gnd
    chg["STAT"]    += p_chg_stat

    r_prog = Part("Device", "R", value="2K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    chg["PROG"] += r_prog[1]; r_prog[2] += p_gnd

    # CHG LED (active low: STAT pulls low when charging)
    r_led = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    led_chg = Part("Device", "LED", value="CHG",
                    footprint="LED_SMD:LED_0603_1608Metric")
    p_chg_stat += r_led[1]
    r_led[2] += led_chg["A"]
    led_chg["K"] += p_gnd

    # JST PH 2-pin battery connector
    j_bat = Part("Connector_Generic", "Conn_01x02",
                  footprint="Connector_JST:JST_PH_B2B-PH-K_1x02_P2.00mm_Vertical",
                  value="BATT")
    j_bat[1] += p_vbat; j_bat[2] += p_gnd

    # Schottky power-OR: USB VBUS -> VBAT
    d_or = Part("Device", "D_Schottky", value="BAT54",
                 footprint="Diode_SMD:D_SOD-123")
    d_or["A"] += p_vbus; d_or["K"] += p_vbat

    c_chg = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_chg[1] += p_vbus; c_chg[2] += p_gnd

    c_bat = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bat[1] += p_vbat; c_bat[2] += p_gnd

lipo_charger(vbus, vbat, gnd, chg_stat)


# ═══════════════════════════════════════════════════════════════
#  3.3V LDO (AP2112K-3.3TRG1, SOT-25-5, LCSC C51118)
#  Pins: 1=VIN, 2=GND, 3=EN, 4=NC, 5=VOUT
# ═══════════════════════════════════════════════════════════════
@subcircuit
def ldo_3v3(p_vbat, p_v3v3, p_gnd):
    reg = Part("C51118", "AP2112K-3.3TRG1",
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               value="AP2112K-3.3")
    reg["1"] += p_vbat   # VIN
    reg["2"] += p_gnd    # GND
    reg["3"] += p_vbat   # EN — always on
    reg["4"] += p_gnd    # NC
    reg["5"] += p_v3v3   # VOUT

    c_in = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += p_vbat; c_in[2] += p_gnd

    c_out1 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out1[1] += p_v3v3; c_out1[2] += p_gnd

    c_out2 = Part("Device", "C", value="10uF",
                   footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out2[1] += p_v3v3; c_out2[2] += p_gnd

ldo_3v3(vbat, v3v3, gnd)


# ═══════════════════════════════════════════════════════════════
#  ATSAMD51J20A-AUT MCU (TQFP-64, LCSC C619371)
#  Pin map (from convert_lcsc output):
#  1=PA00, 2=PA01, 3=PA02, 4=PA03, 5=PB04, 6=PB05,
#  7=GNDANA, 8=VDDANA, 9=PB06, 10=PB07, 11=PB08, 12=PB09,
#  13=PA04, 14=PA05, 15=PA06, 16=PA07, 17=PA08, 18=PA09,
#  19=PA10, 20=PA11, 21=VDDIOB, 22=GND, 23=PB10, 24=PB11,
#  25=PB12, 26=PB13, 27=PB14, 28=PB15, 29=PA12, 30=PA13,
#  31=PA14, 32=PA15, 33=GND, 34=VDDIO, 35=PA16, 36=PA17,
#  37=PA18, 38=PA19, 39=PB16, 40=PB17, 41=PA20, 42=PA21,
#  43=PA22, 44=PA23, 45=PA24, 46=PA25, 47=GND, 48=VDDIO,
#  49=PB22, 50=PB23, 51=PA27, 52=RESETN, 53=VDDCORE,
#  54=GND, 55=VSW, 56=VDDIO, 57=PA30, 58=PA31, 59=PB30,
#  60=PB31, 61=PB00, 62=PB01, 63=PB02, 64=PB03
# ═══════════════════════════════════════════════════════════════
@subcircuit
def mcu_core(p_v3v3, p_gnd, p_usb_dp, p_usb_dm, p_reset_n,
             p_swdio, p_swdclk,
             p_qspi_sck, p_qspi_cs, p_qspi_io0, p_qspi_io1, p_qspi_io2, p_qspi_io3,
             p_neo, p_sda, p_scl, p_uart_tx, p_uart_rx,
             p_a0, p_a1, p_a2, p_a3, p_a4, p_a5,
             p_d4, p_d5, p_d6, p_d9, p_d10, p_d11, p_d12, p_d13):

    mcu = Part("C619371", "ATSAMD51J20A-AUT",
               footprint="Package_QFP:TQFP-64_10x10mm_P0.5mm",
               value="ATSAMD51J20A")

    # Power
    mcu["8"]  += p_v3v3    # VDDANA
    mcu["21"] += p_v3v3    # VDDIOB
    mcu["34"] += p_v3v3    # VDDIO
    mcu["48"] += p_v3v3    # VDDIO
    mcu["56"] += p_v3v3    # VDDIO

    # VDDCORE (internal regulator output): bypass only
    vddcore = Net("VDDCORE")
    mcu["53"] += vddcore
    c_core = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_core[1] += vddcore; c_core[2] += p_gnd

    # VSW: DC-DC switch node — tie to GND when internal LDO mode used
    mcu["55"] += p_gnd

    # GND
    mcu["7"]  += p_gnd   # GNDANA
    mcu["22"] += p_gnd
    mcu["33"] += p_gnd
    mcu["47"] += p_gnd
    mcu["54"] += p_gnd

    # VDDIO decoupling caps (one per supply group)
    for _ in range(4):
        c = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += p_v3v3; c[2] += p_gnd

    c_ana = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_ana[1] += p_v3v3; c_ana[2] += p_gnd

    c_bulk = Part("Device", "C", value="10uF",
                   footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += p_v3v3; c_bulk[2] += p_gnd

    # USB: PA24=DM, PA25=DP
    mcu["45"] += p_usb_dm
    mcu["46"] += p_usb_dp

    # Reset
    mcu["52"] += p_reset_n
    r_rst = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += p_v3v3; r_rst[2] += p_reset_n
    sw_rst = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KMR2",
                   value="RESET")
    sw_rst[1] += p_reset_n; sw_rst[2] += p_gnd

    # SWD: PA30=SWDCLK, PA31=SWDIO
    mcu["57"] += p_swdclk
    mcu["58"] += p_swdio

    # 32.768kHz crystal: PA00=XIN32, PA01=XOUT32
    xin32  = Net("XIN32")
    xout32 = Net("XOUT32")
    mcu["1"] += xin32
    mcu["2"] += xout32
    xtal = Part("Device", "Crystal", value="32.768kHz",
                 footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xin32; xtal[2] += xout32
    c_x1 = Part("Device", "C", value="12pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += xin32; c_x1[2] += p_gnd
    c_x2 = Part("Device", "C", value="12pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2[1] += xout32; c_x2[2] += p_gnd

    # QSPI: PB10=SCK, PB11=CS, PA08=IO0, PA09=IO1, PA10=IO2, PA11=IO3
    mcu["23"] += p_qspi_sck
    mcu["24"] += p_qspi_cs
    mcu["17"] += p_qspi_io0
    mcu["18"] += p_qspi_io1
    mcu["19"] += p_qspi_io2
    mcu["20"] += p_qspi_io3

    # NeoPixel: PB22
    mcu["49"] += p_neo

    # I2C: PA22=SDA, PA23=SCL
    mcu["43"] += p_sda
    mcu["44"] += p_scl

    # UART: PB16=TX, PB17=RX
    mcu["39"] += p_uart_tx
    mcu["40"] += p_uart_rx

    # Analog: PA02=A0, PA05=A1, PB08=A2, PB09=A3, PA04=A4, PA06=A5
    mcu["3"]  += p_a0
    mcu["14"] += p_a1
    mcu["11"] += p_a2
    mcu["12"] += p_a3
    mcu["13"] += p_a4
    mcu["15"] += p_a5

    # Digital: PA14=D4, PA15=D5, PA18=D6, PA19=D9
    #          PA20=D10, PA21=D11, PA03=D12, PA16=D13
    mcu["31"] += p_d4
    mcu["32"] += p_d5
    mcu["37"] += p_d6
    mcu["38"] += p_d9
    mcu["41"] += p_d10
    mcu["42"] += p_d11
    mcu["4"]  += p_d12
    mcu["35"] += p_d13

    # D13 indicator LED (red)
    led_d13 = Part("Device", "LED", value="D13",
                    footprint="LED_SMD:LED_0603_1608Metric")
    r_d13 = Part("Device", "R", value="1K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    p_d13    += r_d13[1]
    r_d13[2] += led_d13["A"]
    led_d13["K"] += p_gnd

    # Unused pins -> GND (safe default for GPIO)
    for p in ["5", "6", "9", "10", "16", "25", "26", "27", "28",
              "29", "30", "36", "50", "51", "59", "60",
              "61", "62", "63", "64"]:
        mcu[p] += p_gnd

mcu_core(v3v3, gnd, usb_dp, usb_dm, reset_n,
         swdio, swdclk,
         qspi_sck, qspi_cs, qspi_io0, qspi_io1, qspi_io2, qspi_io3,
         neopixel_dat, sda, scl, uart_tx, uart_rx,
         a0, a1, a2, a3, a4, a5,
         d4, d5, d6, d9, d10, d11, d12, d13)


# ═══════════════════════════════════════════════════════════════
#  GD25Q16ETIGR 2MB QSPI Flash (SOP-8, LCSC C2904431)
#  Pins: 1=~{CS}, 2=SO(IO1), 3=WP#(IO2), 4=VSS,
#        5=SI(IO0), 6=SCLK, 7=HOLD#(IO3), 8=VCC
# ═══════════════════════════════════════════════════════════════
@subcircuit
def qspi_flash(p_v3v3, p_gnd, p_sck, p_cs, p_io0, p_io1, p_io2, p_io3):
    flash = Part("C2904431", "GD25Q16ETIGR",
                  footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
                  value="GD25Q16")
    flash["1"] += p_cs    # ~{CS}
    flash["2"] += p_io1   # SO / IO1
    flash["3"] += p_io2   # WP# / IO2
    flash["4"] += p_gnd   # VSS
    flash["5"] += p_io0   # SI / IO0
    flash["6"] += p_sck   # SCLK
    flash["7"] += p_io3   # HOLD# / IO3
    flash["8"] += p_v3v3  # VCC

    r_cs = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_cs[1] += p_v3v3; r_cs[2] += p_cs

    c_f = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_f[1] += p_v3v3; c_f[2] += p_gnd

qspi_flash(v3v3, gnd, qspi_sck, qspi_cs, qspi_io0, qspi_io1, qspi_io2, qspi_io3)


# ═══════════════════════════════════════════════════════════════
#  WS2812B NeoPixel (PLCC4)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def neopixel_rgb(p_v3v3, p_gnd, p_din):
    neo = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
               value="NeoPixel")
    neo["VDD"]  += p_v3v3
    neo["VSS"]  += p_gnd
    neo["DIN"]  += p_din
    neo["DOUT"] += p_gnd    # single LED, no chain

    c_neo = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_neo[1] += p_v3v3; c_neo[2] += p_gnd

neopixel_rgb(v3v3, gnd, neopixel_dat)


# ═══════════════════════════════════════════════════════════════
#  I2C Pull-ups
# ═══════════════════════════════════════════════════════════════
@subcircuit
def i2c_pullups(p_v3v3, p_sda, p_scl):
    r_sda = Part("Device", "R", value="4.7K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += p_v3v3; r_sda[2] += p_sda

    r_scl = Part("Device", "R", value="4.7K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += p_v3v3; r_scl[2] += p_scl

i2c_pullups(v3v3, sda, scl)


# ═══════════════════════════════════════════════════════════════
#  SWD Debug Header (5-pin, 1.27mm)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def swd_header(p_v3v3, p_gnd, p_swdio, p_swdclk, p_reset_n):
    j_swd = Part("Connector_Generic", "Conn_01x05",
                  footprint="Connector_PinHeader_1.27mm:PinHeader_1x05_P1.27mm_Vertical",
                  value="SWD")
    j_swd[1] += p_v3v3
    j_swd[2] += p_swdio
    j_swd[3] += p_gnd
    j_swd[4] += p_swdclk
    j_swd[5] += p_reset_n

swd_header(v3v3, gnd, swdio, swdclk, reset_n)


# ═══════════════════════════════════════════════════════════════
#  Feather Form-Factor Headers
#  Left 16-pin + Right 12-pin (standard Feather pinout)
# ═══════════════════════════════════════════════════════════════
@subcircuit
def feather_headers(p_v3v3, p_vbat, p_gnd, p_reset_n,
                    p_a0, p_a1, p_a2, p_a3, p_a4, p_a5,
                    p_sda, p_scl,
                    p_d4, p_d5, p_d6, p_d9, p_d10, p_d11, p_d12, p_d13,
                    p_uart_tx, p_uart_rx, p_miso):

    # Left header: RST, 3V3, AREF, GND, A0-A5, SDA, SCL, D5, D6, D9, D10
    j_l = Part("Connector_Generic", "Conn_01x16",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x16_P2.54mm_Vertical",
               value="HEADER_L")
    j_l[1]  += p_reset_n
    j_l[2]  += p_v3v3
    j_l[3]  += p_gnd        # AREF (tied GND on this board)
    j_l[4]  += p_gnd
    j_l[5]  += p_a0
    j_l[6]  += p_a1
    j_l[7]  += p_a2
    j_l[8]  += p_a3
    j_l[9]  += p_a4
    j_l[10] += p_a5
    j_l[11] += p_sda
    j_l[12] += p_scl
    j_l[13] += p_d5
    j_l[14] += p_d6
    j_l[15] += p_d9
    j_l[16] += p_d10

    # Right header: VBAT, EN, VBUS, D13, D12, D11, D10, D9, D4, RX, TX, MISO
    j_r = Part("Connector_Generic", "Conn_01x12",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x12_P2.54mm_Vertical",
               value="HEADER_R")
    j_r[1]  += p_vbat
    j_r[2]  += p_gnd        # EN pin
    j_r[3]  += p_gnd        # USB 5V (not exposed here)
    j_r[4]  += p_d13
    j_r[5]  += p_d12
    j_r[6]  += p_d11
    j_r[7]  += p_d10
    j_r[8]  += p_d9
    j_r[9]  += p_d4
    j_r[10] += p_uart_rx
    j_r[11] += p_uart_tx
    j_r[12] += p_miso

feather_headers(v3v3, vbat, gnd, reset_n,
                a0, a1, a2, a3, a4, a5,
                sda, scl,
                d4, d5, d6, d9, d10, d11, d12, d13,
                uart_tx, uart_rx, qspi_io1)
