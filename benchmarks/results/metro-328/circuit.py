"""
METRO 328 Development Board
Full-featured Arduino-compatible development board built on ATmega328.
Includes USB-to-serial converter, polarity-protected DC power jack,
four status LEDs for debugging, and hardware SPI/I2C/UART support.
Works with all Arduino shields.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# Power nets
# ============================================================
vin_raw = Net("VIN_RAW")        # Raw DC jack input (7-12V)
vcc = Net("VCC"); vcc.drive = POWER        # +5V rail
v3v3 = Net("+3V3"); v3v3.drive = POWER     # +3.3V rail from FT232RL
gnd = Net("GND"); gnd.drive = POWER

# ============================================================
# Signal nets
# ============================================================
usb_dp = Net("USB_D+")
usb_dm = Net("USB_D-")
uart_tx = Net("UART_TX")       # ATmega TX -> FT232 RX
uart_rx = Net("UART_RX")       # ATmega RX -> FT232 TX
dtr_signal = Net("DTR")        # Auto-reset from FT232
sda = Net("SDA")               # I2C data (PC4)
scl = Net("SCL")               # I2C clock (PC5)
mosi = Net("MOSI")             # SPI (PB3)
miso = Net("MISO")             # SPI (PB4)
sck_net = Net("SCK")           # SPI clock (PB5)
reset_net = Net("RESET")       # ATmega reset line
xtal1 = Net("XTAL1")
xtal2 = Net("XTAL2")

# Digital IO nets for Arduino headers
d2 = Net("D2"); d3 = Net("D3"); d4 = Net("D4"); d5 = Net("D5")
d6 = Net("D6"); d7 = Net("D7"); d8 = Net("D8"); d9 = Net("D9")
d10 = Net("D10")

# Analog input nets
a0 = Net("A0"); a1 = Net("A1"); a2 = Net("A2")
a3 = Net("A3"); a4 = Net("A4"); a5 = Net("A5")


# ============================================================
# Subcircuit: Power Supply (DC Jack + polarity protection + 5V reg)
# ============================================================
@subcircuit
def power_supply(vin_raw, vcc, gnd):
    """DC barrel jack with polarity protection diode and 5V LDO."""
    # DC barrel jack
    j_dc = Part("Connector", "Barrel_Jack", value="DC_Jack",
                footprint="Connector_BarrelJack:BarrelJack_Horizontal")
    j_dc[1] += vin_raw    # Tip (positive)
    j_dc[2] += gnd        # Sleeve (ground)

    # Polarity protection Schottky diode
    d_pol = Part("Device", "D_Schottky", value="SS34",
                 footprint="Diode_SMD:D_SMA")
    d_pol["A"] += vin_raw
    protected = Net("VIN_PROT")
    d_pol["K"] += protected

    # Input filter cap
    c_in = Part("Device", "C", value="100uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += protected
    c_in[2] += gnd

    # 5V LDO regulator (NCP1117-5.0)
    reg5v = Part("Regulator_Linear", "NCP1117-5.0_SOT223", value="NCP1117-5.0",
                 footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
    reg5v["VI"] += protected
    reg5v["VO"] += vcc
    reg5v["GND"] += gnd

    # Output decoupling caps
    c_out1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out1[1] += vcc
    c_out1[2] += gnd

    c_out2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out2[1] += vcc
    c_out2[2] += gnd

power_supply(vin_raw, vcc, gnd)


# ============================================================
# Subcircuit: USB-to-Serial (FT232RL)
# ============================================================
@subcircuit
def usb_serial(usb_dp, usb_dm, uart_tx, uart_rx, dtr_signal, vcc, v3v3, gnd):
    """FT232RL USB-to-serial bridge with Micro-B USB connector."""
    # USB Micro-B connector
    j_usb = Part("Connector", "USB_B_Micro", value="USB_Micro",
                 footprint="Connector_USB:USB_Micro-B_Amphenol_10118193-0001LF_Horizontal")
    j_usb["VBUS"] += vcc   # USB 5V (also powers board via USB)
    j_usb["D-"] += usb_dm
    j_usb["D+"] += usb_dp
    j_usb["GND"] += gnd
    j_usb["Shield"] += gnd
    j_usb["ID"] += NC       # Not used for device mode

    # FT232RL USB-to-UART IC
    ft232 = Part("Interface_USB", "FT232RL", value="FT232RL",
                 footprint="Package_SO:SSOP-28_5.3x10.2mm_P0.65mm")
    ft232["VCC"] += vcc
    ft232["VCCIO"] += vcc
    ft232["GND"] += gnd
    ft232["AGND"] += gnd
    ft232["3V3OUT"] += v3v3
    ft232["TEST"] += gnd       # Must be tied low

    # USB data lines
    ft232["USBD+"] += usb_dp
    ft232["USBD-"] += usb_dm

    # UART connections
    ft232["TXD"] += uart_rx    # FT232 TX -> ATmega RX
    ft232["RXD"] += uart_tx    # FT232 RX <- ATmega TX

    # DTR for auto-reset
    ft232["DTR"] += dtr_signal

    # Unused outputs - leave floating or NC
    ft232["RTS"] += NC
    ft232["CTS"] += gnd       # Active low, tie to ground
    ft232["DCD"] += gnd       # Active low
    ft232["RI"] += gnd        # Active low
    ft232["DCR"] += NC        # (DSR pin)
    ft232["~{RESET}"] += vcc  # Keep FT232 out of reset

    # CBUS pins - configurable, default functions
    ft232["CBUS0"] += NC      # TX LED (default)
    ft232["CBUS1"] += NC      # RX LED (default)
    ft232["CBUS2"] += NC
    ft232["CBUS3"] += NC
    ft232["CBUS4"] += NC

    # Oscillator - FT232RL has internal oscillator but needs external crystal pads
    ft232["OSCI"] += NC
    ft232["OSCO"] += NC

    # Decoupling cap for FT232RL
    c_ft = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_ft[1] += vcc
    c_ft[2] += gnd

    # 3.3V output filter cap
    c_3v3 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_3v3[1] += v3v3
    c_3v3[2] += gnd

usb_serial(usb_dp, usb_dm, uart_tx, uart_rx, dtr_signal, vcc, v3v3, gnd)


# ============================================================
# Subcircuit: ATmega328P MCU
# ============================================================
@subcircuit
def atmega_mcu(vcc, gnd, reset_net, xtal1, xtal2,
               uart_tx, uart_rx, dtr_signal,
               sda, scl, mosi, miso, sck_net,
               d2, d3, d4, d5, d6, d7, d8, d9, d10,
               a0, a1, a2, a3, a4, a5):
    """ATmega328P-AU in TQFP-32 with 16MHz crystal and auto-reset."""
    # ATmega328P-AU (TQFP-32)
    mcu = Part("MCU_Microchip_ATmega", "ATmega328P-A", value="ATmega328P-AU",
               footprint="Package_QFP:TQFP-32_7x7mm_P0.8mm")

    # Power pins
    mcu["VCC"] += vcc
    mcu["AVCC"] += vcc
    mcu["GND"] += gnd
    mcu["AREF"] += NC          # External AREF not used (internal ref)

    # Crystal oscillator (16 MHz)
    mcu["XTAL1/PB6"] += xtal1
    mcu["XTAL2/PB7"] += xtal2

    # UART (D0/D1)
    mcu["PD0"] += uart_rx      # RXD
    mcu["PD1"] += uart_tx      # TXD

    # Reset with auto-reset circuit
    mcu["~{RESET}/PC6"] += reset_net

    # Digital I/O
    mcu["PD2"] += d2       # D2
    mcu["PD3"] += d3       # D3 (PWM)
    mcu["PD4"] += d4       # D4
    mcu["PD5"] += d5       # D5 (PWM)
    mcu["PD6"] += d6       # D6 (PWM)
    mcu["PD7"] += d7       # D7
    mcu["PB0"] += d8       # D8
    mcu["PB1"] += d9       # D9 (PWM)
    mcu["PB2"] += d10      # D10 (PWM/SS)

    # SPI (D11-D13)
    mcu["PB3"] += mosi     # D11/MOSI
    mcu["PB4"] += miso     # D12/MISO
    mcu["PB5"] += sck_net  # D13/SCK

    # Analog inputs / I2C
    mcu["PC0"] += a0       # A0
    mcu["PC1"] += a1       # A1
    mcu["PC2"] += a2       # A2
    mcu["PC3"] += a3       # A3
    mcu["PC4"] += a4       # A4/SDA
    mcu["PC5"] += a5       # A5/SCL

    # ADC6/ADC7 - TQFP only analog inputs
    mcu["ADC6"] += NC
    mcu["ADC7"] += NC

    # I2C aliases
    a4 += sda
    a5 += scl

    # -- 16MHz Crystal --
    y1 = Part("Device", "Crystal", value="16MHz",
              footprint="Crystal:Crystal_HC49-4H_Vertical")
    y1[1] += xtal1
    y1[2] += xtal2

    # Crystal load capacitors (22pF)
    c_x1 = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_x1[1] += xtal1
    c_x1[2] += gnd

    c_x2 = Part("Device", "C", value="22pF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_x2[1] += xtal2
    c_x2[2] += gnd

    # -- Decoupling caps for MCU --
    c_vcc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcc[1] += vcc
    c_vcc[2] += gnd

    c_avcc = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_avcc[1] += vcc
    c_avcc[2] += gnd

    # -- Auto-reset circuit --
    # DTR goes through 100nF cap to RESET pin
    c_rst = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_rst[1] += dtr_signal
    c_rst[2] += reset_net

    # Pull-up resistor on RESET
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_rst[1] += vcc
    r_rst[2] += reset_net

    # Manual reset button
    sw_rst = Part("Switch", "SW_Push", value="RESET",
                  footprint="Button_Switch_SMD:SW_Push_1P1T_NO_CK_KSC6xxJ")
    sw_rst[1] += reset_net
    sw_rst[2] += gnd

atmega_mcu(vcc, gnd, reset_net, xtal1, xtal2,
           uart_tx, uart_rx, dtr_signal,
           sda, scl, mosi, miso, sck_net,
           d2, d3, d4, d5, d6, d7, d8, d9, d10,
           a0, a1, a2, a3, a4, a5)


# ============================================================
# Subcircuit: Status LEDs
# ============================================================
@subcircuit
def status_leds(vcc, gnd, sck_net):
    """Four status LEDs: Power, TX, RX, and L (D13/SCK)."""
    # LED + resistor helper
    def make_led(name, color, anode_net, cathode_net):
        led = Part("Device", "LED", value=color,
                   footprint="LED_SMD:LED_0603_1608Metric")
        r = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
        r[1] += anode_net
        r[2] += led["A"]
        led["K"] += cathode_net

    # Power LED (green, always on when VCC present)
    make_led("PWR", "Green", vcc, gnd)

    # TX LED (green, active low - driven by FT232 CBUS)
    tx_led_net = Net("TX_LED")
    make_led("TX", "Green", vcc, tx_led_net)

    # RX LED (green, active low)
    rx_led_net = Net("RX_LED")
    make_led("RX", "Green", vcc, rx_led_net)

    # L LED on D13/SCK (amber, Arduino pin 13 LED)
    make_led("L", "Amber", sck_net, gnd)

status_leds(vcc, gnd, sck_net)


# ============================================================
# Subcircuit: Arduino Shield Headers
# ============================================================
@subcircuit
def shield_headers(vcc, v3v3, gnd, reset_net, vin_raw,
                   d2, d3, d4, d5, d6, d7, d8, d9, d10,
                   uart_rx, uart_tx, mosi, miso, sck_net,
                   a0, a1, a2, a3, a4, a5):
    """Standard Arduino Uno R3 shield headers."""
    # Power header (8 pins): RESET, 3.3V, 5V, GND, GND, VIN, NC, NC
    j_pwr = Part("Connector_Generic", "Conn_01x08",
                 value="POWER",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_pwr[1] += reset_net
    j_pwr[2] += v3v3
    j_pwr[3] += vcc
    j_pwr[4] += gnd
    j_pwr[5] += gnd
    j_pwr[6] += vin_raw
    j_pwr[7] += NC
    j_pwr[8] += NC

    # Analog header (6 pins): A0-A5
    j_analog = Part("Connector_Generic", "Conn_01x06",
                    value="ANALOG",
                    footprint="Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical")
    j_analog[1] += a0
    j_analog[2] += a1
    j_analog[3] += a2
    j_analog[4] += a3
    j_analog[5] += a4
    j_analog[6] += a5

    # Digital header low (8 pins): D0-D7
    j_dig_lo = Part("Connector_Generic", "Conn_01x08",
                    value="DIGITAL_LO",
                    footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_dig_lo[1] += uart_rx   # D0
    j_dig_lo[2] += uart_tx   # D1
    j_dig_lo[3] += d2
    j_dig_lo[4] += d3
    j_dig_lo[5] += d4
    j_dig_lo[6] += d5
    j_dig_lo[7] += d6
    j_dig_lo[8] += d7

    # Digital header high (10 pins): D8-D13, GND, AREF, SDA, SCL
    j_dig_hi = Part("Connector_Generic", "Conn_01x10",
                    value="DIGITAL_HI",
                    footprint="Connector_PinSocket_2.54mm:PinSocket_1x10_P2.54mm_Vertical")
    j_dig_hi[1] += d8
    j_dig_hi[2] += d9
    j_dig_hi[3] += d10
    j_dig_hi[4] += mosi      # D11
    j_dig_hi[5] += miso      # D12
    j_dig_hi[6] += sck_net   # D13
    j_dig_hi[7] += gnd
    j_dig_hi[8] += NC        # AREF (connected on MCU side)
    j_dig_hi[9] += a4        # SDA
    j_dig_hi[10] += a5       # SCL

shield_headers(vcc, v3v3, gnd, reset_net, vin_raw,
               d2, d3, d4, d5, d6, d7, d8, d9, d10,
               uart_rx, uart_tx, mosi, miso, sck_net,
               a0, a1, a2, a3, a4, a5)


# ============================================================
# Subcircuit: ICSP Header
# ============================================================
@subcircuit
def icsp_header(vcc, gnd, reset_net, mosi, miso, sck_net):
    """6-pin ICSP header for in-circuit programming."""
    j_icsp = Part("Connector_Generic", "Conn_02x03_Odd_Even",
                  value="ICSP",
                  footprint="Connector_PinHeader_2.54mm:PinHeader_2x03_P2.54mm_Vertical")
    j_icsp[1] += miso      # MISO
    j_icsp[2] += vcc       # VCC
    j_icsp[3] += sck_net   # SCK
    j_icsp[4] += mosi      # MOSI
    j_icsp[5] += reset_net # RESET
    j_icsp[6] += gnd       # GND

icsp_header(vcc, gnd, reset_net, mosi, miso, sck_net)


# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
