"""KB2040 Keyboard RP2040 - Compact RP2040 dev board in Arduino Pro Micro form factor.

~33x18mm board with RP2040 QFN-56, 8MB QSPI flash (W25Q128JVS),
USB-C, NeoPixel (WS2812B), 3.3V LDO (AP2112K), boot button, 18 GPIO on
castellated edge pads + 2x13 pin headers, STEMMA QT connector.
"""
from skidl import *

set_default_tool(KICAD9)

# ─── Global power nets ───────────────────────────────────────────────────────
vbus = Net("VBUS"); vbus.drive = POWER
v33  = Net("3V3");  v33.drive  = POWER
gnd  = Net("GND");  gnd.drive  = POWER

# ─── Signal nets ─────────────────────────────────────────────────────────────
usb_dp  = Net("USB_DP")
usb_dm  = Net("USB_DM")
run_net = Net("RUN")
dvdd    = Net("DVDD"); dvdd.drive = POWER
adc_ref = Net("ADC_AVDD"); adc_ref.drive = POWER

qspi_cs   = Net("QSPI_SS")
qspi_clk  = Net("QSPI_SCLK")
qspi_sd0  = Net("QSPI_SD0")
qspi_sd1  = Net("QSPI_SD1")
qspi_sd2  = Net("QSPI_SD2")
qspi_sd3  = Net("QSPI_SD3")

gpio = [Net(f"GPIO{i}") for i in range(30)]

# Named aliases - use the GPIO net directly to avoid net merges
# GPIO16 = NeoPixel DIN, GPIO18 = SDA, GPIO19 = SCL, GPIO25 = BOOT

@subcircuit
def rp2040_core(v33, gnd, vbus, dvdd, adc_ref,
                usb_dp, usb_dm,
                qspi_cs, qspi_clk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3,
                run_net, gpio):
    global mcu
    mcu = Part("MCU_RaspberryPi", "RP2040",
               footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm")
    mcu.lcsc = "C2040"

    # Power
    mcu["IOVDD"]    += v33
    mcu["USB_VDD"]  += v33
    mcu["DVDD"]     += dvdd
    mcu["ADC_AVDD"] += adc_ref
    mcu["VREG_IN"]  += v33
    mcu["VREG_VOUT"] += dvdd   # VREG_VOUT is the internal 1.1V that feeds DVDD
    mcu["GND"]      += gnd
    mcu["TESTEN"]   += gnd

    # USB
    mcu["USB_DP"] += usb_dp
    mcu["USB_DM"] += usb_dm

    # QSPI
    mcu["QSPI_SS"]   += qspi_cs
    mcu["QSPI_SCLK"] += qspi_clk
    mcu["QSPI_SD0"]  += qspi_sd0
    mcu["QSPI_SD1"]  += qspi_sd1
    mcu["QSPI_SD2"]  += qspi_sd2
    mcu["QSPI_SD3"]  += qspi_sd3

    # GPIO
    for i in [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,
               16,17,18,19,20,21,22,23,24,25]:
        mcu[f"GPIO{i}"] += gpio[i]
    mcu["GPIO26_ADC0"] += gpio[26]
    mcu["GPIO27_ADC1"] += gpio[27]
    mcu["GPIO28_ADC2"] += gpio[28]
    mcu["GPIO29_ADC3"] += gpio[29]

    # RUN/BOOT
    mcu["RUN"] += run_net
    mcu["SWD"]   # debug, left unconnected
    mcu["SWCLK"] # debug, left unconnected

    # Crystal 12MHz
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["XIN"]
    xtal[2] += mcu["XOUT"]
    # Load caps on crystal pins
    c_xin = Part("Device", "C", value="15pF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xout = Part("Device", "C", value="15pF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xin[1]  += mcu["XIN"];  c_xin[2]  += gnd
    c_xout[1] += mcu["XOUT"]; c_xout[2] += gnd

    # VREG_VOUT connects to DVDD (internal 1.1V core rail)
    c_vreg = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vreg[1] += dvdd; c_vreg[2] += gnd
    # Bulk cap on DVDD
    c_dvdd_bulk = Part("Device", "C", value="10uF",
                       footprint="Capacitor_SMD:C_0805_2012Metric")
    c_dvdd_bulk[1] += dvdd; c_dvdd_bulk[2] += gnd

    # ADC_AVDD RC filter
    r_adc = Part("Device", "R", value="200",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    r_adc[1] += v33; r_adc[2] += adc_ref
    c_adc[1]  += adc_ref; c_adc[2] += gnd

    # IOVDD decoupling (6x 100nF + 1x 10uF bulk)
    for _ in range(6):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v33; c[2] += gnd
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += v33; c_bulk[2] += gnd

    # USB_VDD decoupling
    c_usb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usb[1] += v33; c_usb[2] += gnd

    # RUN pull-up
    r_run = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_run[1] += v33; r_run[2] += run_net

    # BOOT button (pulls GPIO25/BOOTSEL low during reset)
    sw_boot = Part("Switch", "SW_Push",
                   footprint="Button_Switch_SMD:SW_Push_SPST_NO_Alps_SKRK")
    sw_boot[1] += gpio[25]; sw_boot[2] += gnd
    r_boot = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    r_boot[1] += v33; r_boot[2] += gpio[25]


@subcircuit
def flash_8mb(v33, gnd, qspi_cs, qspi_clk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3):
    global flash
    flash = Part("Memory_Flash", "W25Q128JVS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    flash.lcsc = "C97521"

    flash["~{CS}"]                      += qspi_cs
    flash["CLK"]                         += qspi_clk
    flash["DI/IO_{0}"]                  += qspi_sd0
    flash["DO/IO_{1}"]                  += qspi_sd1
    flash["~{WP}/IO_{2}"]              += qspi_sd2
    flash["~{HOLD}/~{RESET}/IO_{3}"]   += qspi_sd3
    flash["VCC"] += v33
    flash["GND"]  += gnd

    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += v33; c1[2] += gnd
    c2[1] += v33; c2[2] += gnd


@subcircuit
def usb_c_port(vbus, gnd, usb_dp, usb_dm):
    global j_usb
    j_usb = Part("Connector", "USB_C_Receptacle_USB2.0_16P",
                 footprint="Connector_USB:USB_C_Receptacle_HCTL_HC-TYPE-C-16P-01A")
    j_usb.edge_preference = "bottom"

    j_usb["A4"] += vbus; j_usb["B4"] += vbus
    j_usb["A9"] += vbus; j_usb["B9"] += vbus

    j_usb["A1"]  += gnd; j_usb["B1"]  += gnd
    j_usb["A12"] += gnd; j_usb["B12"] += gnd
    j_usb["SHIELD"] += gnd

    j_usb["A6"] += usb_dp; j_usb["B6"] += usb_dp
    j_usb["A7"] += usb_dm; j_usb["B7"] += usb_dm

    # CC pull-downs (5.1k) for USB-C UFP current negotiation
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += j_usb["A5"]; r_cc1[2] += gnd
    r_cc2[1] += j_usb["B5"]; r_cc2[2] += gnd

    # SBU unused
    j_usb["A8"].do_erc = False
    j_usb["B8"].do_erc = False

    # VBUS filtering
    c_f1 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_f2 = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_f1[1] += vbus; c_f1[2] += gnd
    c_f2[1] += vbus; c_f2[2] += gnd


@subcircuit
def ldo_3v3(vbus, v33, gnd):
    global u_ldo
    u_ldo = Part("Regulator_Linear", "AP2112K-3.3",
                 footprint="Package_TO_SOT_SMD:SOT-23-5")
    u_ldo.lcsc = "C51118"

    u_ldo["VIN"]  += vbus
    u_ldo["EN"]   += vbus
    u_ldo["GND"]  += gnd
    u_ldo["NC"]   # no-connect
    u_ldo["VOUT"] += v33

    c_in  = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_out = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_in[1]  += vbus; c_in[2]  += gnd
    c_out[1] += v33;  c_out[2] += gnd


@subcircuit
def neopixel_led(v33, gnd, gpio_16):
    """WS2812B on GPIO16."""
    global led1
    led1 = Part("LED", "WS2812B",
                footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    led1.lcsc = "C114586"

    led1["VDD"] += v33
    led1["VSS"] += gnd
    led1["DIN"] += gpio_16
    led1["DOUT"].do_erc = False

    c_n = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    c_n[1] += v33; c_n[2] += gnd


@subcircuit
def stemma_qt(v33, gnd, gpio_18, gpio_19):
    """STEMMA QT connector on GPIO18 (SDA) / GPIO19 (SCL)."""
    global j_qt
    j_qt = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    j_qt[1] += gnd
    j_qt[2] += v33
    j_qt[3] += gpio_18
    j_qt[4] += gpio_19

    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v33; r_sda[2] += gpio_18
    r_scl[1] += v33; r_scl[2] += gpio_19


@subcircuit
def pin_headers(v33, gnd, vbus, gpio):
    """2x13 castellated headers along the long edges (top/bottom of 33x18mm board).
    Horizontal 2.54mm pitch: 13 pins * 2.54mm = 33mm fits along the 33mm edge.
    """
    # Top header (13 pins): GND, 3V3, GPIO0-10
    j_l = Part("Connector_Generic", "Conn_01x13",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x13_P2.54mm_Horizontal")
    j_l.edge_preference = "top"
    j_l[1]  += gnd
    j_l[2]  += v33
    j_l[3]  += gpio[0]
    j_l[4]  += gpio[1]
    j_l[5]  += gpio[2]
    j_l[6]  += gpio[3]
    j_l[7]  += gpio[4]
    j_l[8]  += gpio[5]
    j_l[9]  += gpio[6]
    j_l[10] += gpio[7]
    j_l[11] += gpio[8]
    j_l[12] += gpio[9]
    j_l[13] += gpio[10]

    # Bottom header (13 pins): VBUS, GND, GPIO11-29 selection
    j_r = Part("Connector_Generic", "Conn_01x13",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x13_P2.54mm_Horizontal")
    j_r.edge_preference = "bottom"
    j_r[1]  += vbus
    j_r[2]  += gnd
    j_r[3]  += gpio[11]
    j_r[4]  += gpio[12]
    j_r[5]  += gpio[13]
    j_r[6]  += gpio[14]
    j_r[7]  += gpio[15]
    j_r[8]  += gpio[17]
    j_r[9]  += gpio[20]
    j_r[10] += gpio[21]
    j_r[11] += gpio[26]  # A0/ADC0
    j_r[12] += gpio[27]  # A1/ADC1
    j_r[13] += gpio[28]  # A2/ADC2


# ─── Instantiate ─────────────────────────────────────────────────────────────
rp2040_core(v33, gnd, vbus, dvdd, adc_ref,
            usb_dp, usb_dm,
            qspi_cs, qspi_clk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3,
            run_net, gpio)

flash_8mb(v33, gnd, qspi_cs, qspi_clk, qspi_sd0, qspi_sd1, qspi_sd2, qspi_sd3)

usb_c_port(vbus, gnd, usb_dp, usb_dm)

ldo_3v3(vbus, v33, gnd)

neopixel_led(v33, gnd, gpio[16])   # GPIO16 = NeoPixel DIN

stemma_qt(v33, gnd, gpio[18], gpio[19])   # GPIO18/19 = I2C1 SDA/SCL

pin_headers(v33, gnd, vbus, gpio)

EDA_FLOORPLAN = {
    "outline": {"width_mm": 33.0, "height_mm": 18.0},
    "edge_anchors": [
        {"ref": "J1", "edge": "bottom"},   # USB-C on bottom edge
    ],
}
