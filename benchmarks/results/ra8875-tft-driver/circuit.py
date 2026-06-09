"""
RA8875 TFT Display Driver
=========================
Hardware-accelerated TFT display controller for 40-pin parallel TFT displays
up to 800x480. Features:
  - RA8875 display controller (LQFP-100) with 768KB on-chip RAM
  - Hardware shape drawing (line, circle, rectangle, triangle, etc.)
  - Built-in fonts and character ROM
  - Resistive touchscreen controller (4-wire) over same SPI interface
  - SPI host interface (up to 20MHz)
  - 40-pin FPC connector for TFT panel
  - AP2112K-3.3 LDO regulator for 3.3V from 5V
  - 20MHz crystal oscillator for RA8875 clock
  - Decoupling capacitors on all power rails
  - Backlight driver with PWM control
  - SPI breakout header for host MCU connection
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ---- Power Nets ----
vin_net = Net("VIN")
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ---- SPI Host Interface Nets ----
spi_sck = Net("SPI_SCK")
spi_mosi = Net("SPI_MOSI")
spi_miso = Net("SPI_MISO")
spi_cs = Net("SPI_CS")

# ---- Control Nets ----
int_net = Net("INT")
rst_net = Net("nRESET")
wait_net = Net("WAIT")

# ---- Touch Nets ----
tp_xp = Net("TP_XP")
tp_xn = Net("TP_XN")
tp_yp = Net("TP_YP")
tp_yn = Net("TP_YN")

# ---- TFT Data Bus Nets ----
tft_data = [Net(f"DB{i}") for i in range(16)]  # 16-bit data bus

# ---- TFT Control Nets ----
tft_hsync = Net("HSYNC")
tft_vsync = Net("VSYNC")
tft_de = Net("DE")
tft_pclk = Net("PCLK")

# ---- Crystal Nets ----
xi_net = Net("XI")
xo_net = Net("XO")

# ---- Backlight ----
bl_pwm = Net("BL_PWM")

# ============================================================
# Subcircuit: 3.3V LDO Regulator (AP2112K-3.3)
# ============================================================
@subcircuit
def voltage_regulator(vin, vout, gnd_net):
    """AP2112K-3.3 LDO with input/output decoupling."""
    u_reg = Part("Regulator_Linear", "AP2112K-3.3",
                 footprint="Package_TO_SOT_SMD:SOT-23-5",
                 value="AP2112K-3.3")
    u_reg["VIN"] += vin
    u_reg["VOUT"] += vout
    u_reg["GND"] += gnd_net
    u_reg["EN"] += vin  # Always enabled
    u_reg["NC"] += NC

    # Input decoupling (10uF)
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vin
    c_in[2] += gnd_net

    # Output decoupling (10uF)
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += vout
    c_out[2] += gnd_net

    # Output filter (100nF)
    c_filt = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_filt[1] += vout
    c_filt[2] += gnd_net


# ============================================================
# Subcircuit: RA8875 Display Controller
# ============================================================
@subcircuit
def ra8875_controller(vdd, gnd_net, sck, mosi, miso, cs,
                      int_pin, rst_pin, wait_pin,
                      xi, xo,
                      db, hsync, vsync, de, pclk,
                      txp, txn, typ, tyn,
                      bl):
    """RA8875 LQFP-100 display controller with crystal and decoupling.

    The RA8875 is defined with SKIDL tool because it's not in standard
    KiCad symbol libraries. Pin assignments based on RA8875 datasheet.
    """

    # RA8875 - LQFP-100 package
    # Key pin groups:
    #   SPI host: SCLK, SDI, SDO, SCS
    #   TFT data: DB0-DB15 (16-bit parallel)
    #   TFT control: HSYNC, VSYNC, DE, PCLK
    #   Touch: TP_XP, TP_XN, TP_YP, TP_YN
    #   Crystal: XI, XO
    #   Control: INT, RESET, WAIT
    #   Backlight: PWM1
    pin_list = [
        # Power pins (multiple VDD/VSS)
        Pin(num="8",  name="VDD_1",  func=Pin.types.PWRIN),
        Pin(num="22", name="VDD_2",  func=Pin.types.PWRIN),
        Pin(num="37", name="VDD_3",  func=Pin.types.PWRIN),
        Pin(num="54", name="VDD_4",  func=Pin.types.PWRIN),
        Pin(num="67", name="VDD_5",  func=Pin.types.PWRIN),
        Pin(num="82", name="VDD_6",  func=Pin.types.PWRIN),
        Pin(num="96", name="VDD_7",  func=Pin.types.PWRIN),
        Pin(num="9",  name="VSS_1",  func=Pin.types.PWRIN),
        Pin(num="23", name="VSS_2",  func=Pin.types.PWRIN),
        Pin(num="38", name="VSS_3",  func=Pin.types.PWRIN),
        Pin(num="55", name="VSS_4",  func=Pin.types.PWRIN),
        Pin(num="68", name="VSS_5",  func=Pin.types.PWRIN),
        Pin(num="83", name="VSS_6",  func=Pin.types.PWRIN),
        Pin(num="97", name="VSS_7",  func=Pin.types.PWRIN),

        # Core voltage (1.8V internal regulator output, bypass with cap)
        Pin(num="100", name="VDDCORE", func=Pin.types.PWROUT),

        # SPI host interface
        Pin(num="71", name="SCLK",  func=Pin.types.INPUT),
        Pin(num="72", name="SDI",   func=Pin.types.INPUT),
        Pin(num="73", name="SDO",   func=Pin.types.OUTPUT),
        Pin(num="74", name="SCS",   func=Pin.types.INPUT),

        # Control signals
        Pin(num="75", name="INT",   func=Pin.types.OUTPUT),
        Pin(num="76", name="WAIT",  func=Pin.types.OUTPUT),
        Pin(num="77", name="RESET", func=Pin.types.INPUT),

        # Crystal oscillator
        Pin(num="98", name="XI",    func=Pin.types.INPUT),
        Pin(num="99", name="XO",    func=Pin.types.OUTPUT),

        # TFT data bus DB0-DB15
        Pin(num="39", name="DB0",   func=Pin.types.OUTPUT),
        Pin(num="40", name="DB1",   func=Pin.types.OUTPUT),
        Pin(num="41", name="DB2",   func=Pin.types.OUTPUT),
        Pin(num="42", name="DB3",   func=Pin.types.OUTPUT),
        Pin(num="43", name="DB4",   func=Pin.types.OUTPUT),
        Pin(num="44", name="DB5",   func=Pin.types.OUTPUT),
        Pin(num="45", name="DB6",   func=Pin.types.OUTPUT),
        Pin(num="46", name="DB7",   func=Pin.types.OUTPUT),
        Pin(num="47", name="DB8",   func=Pin.types.OUTPUT),
        Pin(num="48", name="DB9",   func=Pin.types.OUTPUT),
        Pin(num="49", name="DB10",  func=Pin.types.OUTPUT),
        Pin(num="50", name="DB11",  func=Pin.types.OUTPUT),
        Pin(num="51", name="DB12",  func=Pin.types.OUTPUT),
        Pin(num="52", name="DB13",  func=Pin.types.OUTPUT),
        Pin(num="53", name="DB14",  func=Pin.types.OUTPUT),
        Pin(num="56", name="DB15",  func=Pin.types.OUTPUT),

        # TFT control signals
        Pin(num="33", name="HSYNC", func=Pin.types.OUTPUT),
        Pin(num="34", name="VSYNC", func=Pin.types.OUTPUT),
        Pin(num="35", name="DE",    func=Pin.types.OUTPUT),
        Pin(num="36", name="PCLK",  func=Pin.types.OUTPUT),

        # Touchscreen interface (4-wire resistive)
        Pin(num="84", name="TP_XP", func=Pin.types.BIDIR),
        Pin(num="85", name="TP_XN", func=Pin.types.BIDIR),
        Pin(num="86", name="TP_YP", func=Pin.types.BIDIR),
        Pin(num="87", name="TP_YN", func=Pin.types.BIDIR),

        # PWM outputs (backlight control)
        Pin(num="78", name="PWM1",  func=Pin.types.OUTPUT),
        Pin(num="79", name="PWM2",  func=Pin.types.OUTPUT),

        # GPIO pins
        Pin(num="88", name="GPIO0", func=Pin.types.BIDIR),
        Pin(num="89", name="GPIO1", func=Pin.types.BIDIR),
        Pin(num="90", name="GPIO2", func=Pin.types.BIDIR),
        Pin(num="91", name="GPIO3", func=Pin.types.BIDIR),

        # Config pins (active during reset)
        Pin(num="92", name="PS",    func=Pin.types.INPUT),  # SPI/parallel select
        Pin(num="93", name="SIFT",  func=Pin.types.INPUT),  # 8/16 bit select

        # Key scan pins (active when key-scan enabled)
        Pin(num="1",  name="KOUT0", func=Pin.types.OUTPUT),
        Pin(num="2",  name="KOUT1", func=Pin.types.OUTPUT),
        Pin(num="3",  name="KOUT2", func=Pin.types.OUTPUT),
        Pin(num="4",  name="KOUT3", func=Pin.types.OUTPUT),
        Pin(num="5",  name="KIN0",  func=Pin.types.INPUT),
        Pin(num="6",  name="KIN1",  func=Pin.types.INPUT),
        Pin(num="7",  name="KIN2",  func=Pin.types.INPUT),

        # Display configuration
        Pin(num="10", name="STBY",  func=Pin.types.INPUT),   # Standby
        Pin(num="11", name="LED_A", func=Pin.types.OUTPUT),   # LED anode control
        Pin(num="12", name="LED_K", func=Pin.types.OUTPUT),   # LED cathode control

        # I2C address/config (active during reset for I2C mode)
        Pin(num="13", name="IICA0", func=Pin.types.INPUT),
        Pin(num="14", name="IICA1", func=Pin.types.INPUT),

        # Display timing
        Pin(num="15", name="PDAT0", func=Pin.types.OUTPUT),
        Pin(num="16", name="PDAT1", func=Pin.types.OUTPUT),
        Pin(num="17", name="PDAT2", func=Pin.types.OUTPUT),
        Pin(num="18", name="PDAT3", func=Pin.types.OUTPUT),
        Pin(num="19", name="PDAT4", func=Pin.types.OUTPUT),
        Pin(num="20", name="PDAT5", func=Pin.types.OUTPUT),
        Pin(num="21", name="PDAT6", func=Pin.types.OUTPUT),

        Pin(num="24", name="PDAT7", func=Pin.types.OUTPUT),
        Pin(num="25", name="PDAT8", func=Pin.types.OUTPUT),
        Pin(num="26", name="PDAT9", func=Pin.types.OUTPUT),
        Pin(num="27", name="PDAT10", func=Pin.types.OUTPUT),
        Pin(num="28", name="PDAT11", func=Pin.types.OUTPUT),
        Pin(num="29", name="PDAT12", func=Pin.types.OUTPUT),
        Pin(num="30", name="PDAT13", func=Pin.types.OUTPUT),
        Pin(num="31", name="PDAT14", func=Pin.types.OUTPUT),
        Pin(num="32", name="PDAT15", func=Pin.types.OUTPUT),

        # Remaining data pins
        Pin(num="57", name="PDAT16", func=Pin.types.OUTPUT),
        Pin(num="58", name="PDAT17", func=Pin.types.OUTPUT),
        Pin(num="59", name="PDAT18", func=Pin.types.OUTPUT),
        Pin(num="60", name="PDAT19", func=Pin.types.OUTPUT),
        Pin(num="61", name="PDAT20", func=Pin.types.OUTPUT),
        Pin(num="62", name="PDAT21", func=Pin.types.OUTPUT),
        Pin(num="63", name="PDAT22", func=Pin.types.OUTPUT),
        Pin(num="64", name="PDAT23", func=Pin.types.OUTPUT),

        # Additional control
        Pin(num="65", name="XNCS",  func=Pin.types.OUTPUT),   # External flash CS
        Pin(num="66", name="XRD",   func=Pin.types.OUTPUT),   # External read
        Pin(num="69", name="XWR",   func=Pin.types.OUTPUT),   # External write
        Pin(num="70", name="XRST",  func=Pin.types.OUTPUT),   # External reset

        Pin(num="80", name="KSCR",  func=Pin.types.OUTPUT),   # Key scan row
        Pin(num="81", name="KSCC",  func=Pin.types.OUTPUT),   # Key scan column

        Pin(num="94", name="SFCL",  func=Pin.types.OUTPUT),   # Serial flash clock
        Pin(num="95", name="SFDA",  func=Pin.types.BIDIR),    # Serial flash data
    ]

    u = Part(name="RA8875", tool=SKIDL, dest=NETLIST, pins=pin_list,
             footprint="Package_QFP:LQFP-100_14x14mm_P0.5mm",
             value="RA8875")

    # Power connections - all VDD pins to 3.3V, all VSS to GND
    for i in range(1, 8):
        u[f"VDD_{i}"] += vdd
        u[f"VSS_{i}"] += gnd_net

    # SPI host interface
    u["SCLK"] += sck
    u["SDI"] += mosi
    u["SDO"] += miso
    u["SCS"] += cs

    # Control signals
    u["INT"] += int_pin
    u["WAIT"] += wait_pin
    u["RESET"] += rst_pin

    # Crystal oscillator
    u["XI"] += xi
    u["XO"] += xo

    # TFT data bus
    for i in range(16):
        u[f"DB{i}"] += db[i]

    # TFT control
    u["HSYNC"] += hsync
    u["VSYNC"] += vsync
    u["DE"] += de
    u["PCLK"] += pclk

    # Touchscreen interface
    u["TP_XP"] += txp
    u["TP_XN"] += txn
    u["TP_YP"] += typ
    u["TP_YN"] += tyn

    # Backlight PWM
    u["PWM1"] += bl
    u["PWM2"] += NC  # Second PWM unused

    # Config pins: PS=LOW for SPI mode, SIFT unused for SPI
    u["PS"] += gnd_net    # SPI mode select (active low)
    u["SIFT"] += gnd_net  # Don't care in SPI, tie low

    # Standby: tie high for normal operation
    u["STBY"] += vdd

    # Unused pins tied off
    u["KOUT0"] += NC
    u["KOUT1"] += NC
    u["KOUT2"] += NC
    u["KOUT3"] += NC
    u["KIN0"] += gnd_net   # Key inputs tied low
    u["KIN1"] += gnd_net
    u["KIN2"] += gnd_net

    u["LED_A"] += NC
    u["LED_K"] += NC
    u["IICA0"] += gnd_net  # I2C address irrelevant in SPI mode
    u["IICA1"] += gnd_net

    # PDAT pins: main 24-bit parallel TFT data (directly to FPC)
    # These go to the TFT connector, but we handle them through
    # the 40-pin FPC connector subcircuit. NC the ones not used.
    for i in range(24):
        u[f"PDAT{i}"] += NC

    # Unused external memory/flash control
    u["XNCS"] += NC
    u["XRD"] += NC
    u["XWR"] += NC
    u["XRST"] += NC
    u["KSCR"] += NC
    u["KSCC"] += NC
    u["SFCL"] += NC
    u["SFDA"] += NC

    # GPIO pins unused
    u["GPIO0"] += NC
    u["GPIO1"] += NC
    u["GPIO2"] += NC
    u["GPIO3"] += NC

    # VDDCORE: internal 1.8V core regulator output, decouple with 1uF
    core_net = Net("VDDCORE")
    u["VDDCORE"] += core_net
    c_core = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_core[1] += core_net
    c_core[2] += gnd_net

    # Decoupling caps on VDD (100nF each, placed near IC)
    for i in range(4):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vdd
        c[2] += gnd_net

    # Bulk decoupling (10uF)
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vdd
    c_bulk[2] += gnd_net


# ============================================================
# Subcircuit: 20MHz Crystal Oscillator
# ============================================================
@subcircuit
def crystal_osc(xi, xo, gnd_net):
    """20MHz crystal with load capacitors for RA8875 clock."""
    xtal = Part("Device", "Crystal", value="20MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += xi
    xtal[2] += xo

    # Load capacitors (18pF typical for 20MHz crystal)
    c_xi = Part("Device", "C", value="18pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xi[1] += xi
    c_xi[2] += gnd_net

    c_xo = Part("Device", "C", value="18pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xo[1] += xo
    c_xo[2] += gnd_net


# ============================================================
# Subcircuit: SPI Interface and Pull-ups
# ============================================================
@subcircuit
def spi_interface(vdd, gnd_net, sck, mosi, miso, cs, int_pin, rst_pin, wait_pin):
    """SPI pull-ups and reset/interrupt conditioning."""

    # CS pull-up (10K) - active low, ensure high when host not driving
    r_cs = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_cs[1] += vdd
    r_cs[2] += cs

    # Reset pull-up (10K) with RC delay
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += vdd
    r_rst[2] += rst_pin

    # Reset capacitor (100nF for RC delay)
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_rst[1] += rst_pin
    c_rst[2] += gnd_net

    # INT pull-up (10K) - active low interrupt output
    r_int = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_int[1] += vdd
    r_int[2] += int_pin

    # WAIT pull-up (10K)
    r_wait = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_wait[1] += vdd
    r_wait[2] += wait_pin


# ============================================================
# Subcircuit: Touchscreen Filter
# ============================================================
@subcircuit
def touch_filter(txp, txn, typ, tyn, gnd_net):
    """Filter capacitors for resistive touchscreen lines."""
    # Series resistors for ESD protection on touch lines
    r_xp = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_xn = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_yp = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_yn = Part("Device", "R", value="1K",
                footprint="Resistor_SMD:R_0603_1608Metric")

    # Filter caps (100pF on each touch line)
    c_xp = Part("Device", "C", value="100pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_xn = Part("Device", "C", value="100pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_yp = Part("Device", "C", value="100pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_yn = Part("Device", "C", value="100pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")

    # Touch lines to RA8875 through series resistors
    # Exposed side goes to FPC connector in connectors subcircuit
    tp_xp_filt = Net("TP_XP_FILT")
    tp_xn_filt = Net("TP_XN_FILT")
    tp_yp_filt = Net("TP_YP_FILT")
    tp_yn_filt = Net("TP_YN_FILT")

    r_xp[1] += txp;  r_xp[2] += tp_xp_filt
    r_xn[1] += txn;  r_xn[2] += tp_xn_filt
    r_yp[1] += typ;  r_yp[2] += tp_yp_filt
    r_yn[1] += tyn;  r_yn[2] += tp_yn_filt

    c_xp[1] += tp_xp_filt; c_xp[2] += gnd_net
    c_xn[1] += tp_xn_filt; c_xn[2] += gnd_net
    c_yp[1] += tp_yp_filt; c_yp[2] += gnd_net
    c_yn[1] += tp_yn_filt; c_yn[2] += gnd_net


# ============================================================
# Subcircuit: Backlight Driver
# ============================================================
@subcircuit
def backlight_driver(bl_pwm_in, vdd, gnd_net):
    """N-channel MOSFET backlight driver controlled by RA8875 PWM1."""
    # N-FET for backlight switching (driven by PWM1 output)
    q = Part("Transistor_FET", "BSS138",
             footprint="Package_TO_SOT_SMD:SOT-23",
             value="BSS138")
    q["G"] += bl_pwm_in   # Gate driven by PWM1
    q["S"] += gnd_net      # Source to GND
    # Drain goes to backlight LED cathode (via connector)

    bl_out = Net("BL_DRIVE")
    q["D"] += bl_out

    # Gate pull-down (100K) to keep FET off when not driven
    r_gd = Part("Device", "R", value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_gd[1] += bl_pwm_in
    r_gd[2] += gnd_net

    # Current-limiting resistor for backlight (10 ohm)
    r_bl = Part("Device", "R", value="10R",
                footprint="Resistor_SMD:R_0805_2012Metric")
    r_bl[1] += vdd
    r_bl[2] += bl_out


# ============================================================
# Subcircuit: Connectors
# ============================================================
@subcircuit
def connectors(vin, v3v3_net, gnd_net, sck, mosi, miso, cs,
               int_pin, rst_pin, wait_pin,
               db, hsync, vsync, de, pclk):
    """SPI host header and 40-pin FPC TFT connector."""

    # SPI host breakout header:
    # Pin 1: VIN, 2: 3V3, 3: GND, 4: SCK, 5: MOSI, 6: MISO, 7: CS, 8: INT, 9: RST, 10: WAIT
    j_spi = Part("Connector_Generic", "Conn_01x10",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical",
                 value="SPI_HDR")
    j_spi[1] += vin
    j_spi[2] += v3v3_net
    j_spi[3] += gnd_net
    j_spi[4] += sck
    j_spi[5] += mosi
    j_spi[6] += miso
    j_spi[7] += cs
    j_spi[8] += int_pin
    j_spi[9] += rst_pin
    j_spi[10] += wait_pin

    # 40-pin FPC connector for TFT panel
    # Standard 40-pin TFT pinout:
    # Pins 1-2: GND, 3: VDD (3.3V backlight power)
    # Pins 4-19: DB0-DB15 (data bus)
    # Pins 20-21: GND
    # Pin 22: HSYNC, 23: VSYNC, 24: DE, 25: PCLK
    # Pins 26-29: GND
    # Pins 30-33: Touch (XP, XN, YP, YN)
    # Pins 34-36: GND
    # Pin 37: LED_A (backlight anode - VDD)
    # Pin 38: LED_K (backlight cathode - through driver)
    # Pins 39-40: GND
    j_tft = Part("Connector_Generic", "Conn_01x40",
                 footprint="Connector_FFC-FPC:Hirose_FH12-40S-0.5SH_1x40-1MP_P0.50mm_Horizontal",
                 value="TFT_FPC")
    j_tft[1] += gnd_net
    j_tft[2] += gnd_net
    j_tft[3] += v3v3_net

    # Data bus DB0-DB15 on pins 4-19
    for i in range(16):
        j_tft[4 + i] += db[i]

    j_tft[20] += gnd_net
    j_tft[21] += gnd_net
    j_tft[22] += hsync
    j_tft[23] += vsync
    j_tft[24] += de
    j_tft[25] += pclk
    j_tft[26] += gnd_net
    j_tft[27] += gnd_net
    j_tft[28] += gnd_net
    j_tft[29] += gnd_net

    # Touch connections (filtered externally)
    tp_xp_filt = Net("TP_XP_FILT")
    tp_xn_filt = Net("TP_XN_FILT")
    tp_yp_filt = Net("TP_YP_FILT")
    tp_yn_filt = Net("TP_YN_FILT")
    j_tft[30] += tp_xp_filt
    j_tft[31] += tp_xn_filt
    j_tft[32] += tp_yp_filt
    j_tft[33] += tp_yn_filt

    j_tft[34] += gnd_net
    j_tft[35] += gnd_net
    j_tft[36] += gnd_net

    # Backlight
    j_tft[37] += v3v3_net      # LED anode power
    bl_drive = Net("BL_DRIVE")
    j_tft[38] += bl_drive      # LED cathode through FET driver

    j_tft[39] += gnd_net
    j_tft[40] += gnd_net

    # Touch panel connector (separate 4-pin header for external touch)
    j_touch = Part("Connector_Generic", "Conn_01x04",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
                   value="TOUCH_HDR")
    j_touch[1] += tp_xp_filt
    j_touch[2] += tp_xn_filt
    j_touch[3] += tp_yp_filt
    j_touch[4] += tp_yn_filt


# ============================================================
# Top-level: Instantiate all subcircuits
# ============================================================

# Power regulation (5V in -> 3.3V out)
voltage_regulator(vin_net, v3v3, gnd)

# RA8875 display controller
ra8875_controller(v3v3, gnd, spi_sck, spi_mosi, spi_miso, spi_cs,
                  int_net, rst_net, wait_net,
                  xi_net, xo_net,
                  tft_data, tft_hsync, tft_vsync, tft_de, tft_pclk,
                  tp_xp, tp_xn, tp_yp, tp_yn,
                  bl_pwm)

# Crystal oscillator
crystal_osc(xi_net, xo_net, gnd)

# SPI interface pull-ups and conditioning
spi_interface(v3v3, gnd, spi_sck, spi_mosi, spi_miso, spi_cs,
              int_net, rst_net, wait_net)

# Touchscreen input filter
touch_filter(tp_xp, tp_xn, tp_yp, tp_yn, gnd)

# Backlight driver
backlight_driver(bl_pwm, v3v3, gnd)

# Connectors (SPI header + TFT FPC)
connectors(vin_net, v3v3, gnd, spi_sck, spi_mosi, spi_miso, spi_cs,
           int_net, rst_net, wait_net,
           tft_data, tft_hsync, tft_vsync, tft_de, tft_pclk)

# Generate schematic
generate_schematic(auto_stub=True)
