"""TCS34725 RGB Color Sensor Breakout Board

RGB and Clear light sensing color sensor with 3,800,000:1 dynamic range,
integrated IR blocking filter, adjustable integration time and gain.
I2C interface. 3.3V and 5V compatible. Includes white LED for illumination
with MOSFET control.

The TCS34725FN is not in KiCad standard libraries. This design uses the
TCS34727FN (LCSC C2649485), a pin-compatible 16-bit variant of the same
RGB+Clear sensor family in the same DFN-6 (2x2.4mm) package.

Features:
- TCS34727FN color sensor (I2C, DFN-6, LCSC C2649485)
- AP2112K-3.3 LDO regulator (3.8-6V input, 3.3V output)
- BSS138 N-MOSFET for white LED control
- 10K I2C pull-up resistors on SDA/SCL
- 6-pin breakout header (VIN, GND, SDA, SCL, INT, LED_EN)
- White LED with 33R current-limiting resistor
- 100nF + 1uF decoupling on VDD
"""

from skidl import *

# Power nets
vin = Net("VIN"); vin.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Signal nets
sda = Net("SDA")
scl = Net("SCL")
n_int = Net("INT")
led_en = Net("LED_EN")
led_sw = Net("LED_SW")   # LED cathode / MOSFET drain (switching node)


@subcircuit
def voltage_regulator(vin, vout, gnd):
    """AP2112K-3.3 LDO: 3.8-6V in, 3.3V out, 600mA."""
    reg = Part("Regulator_Linear", "AP2112K-3.3", value="AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    reg["VIN"]  += vin
    reg["GND"]  += gnd
    reg["EN"]   += vin
    reg["VOUT"] += vout

    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vin; c_in[2] += gnd

    c_out = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out[1] += vout; c_out[2] += gnd

    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vout; c_bulk[2] += gnd


@subcircuit
def tcs34725_sensor(vdd, gnd, sda, scl, n_int):
    """TCS34727FN RGB+Clear color sensor, DFN-6 (2x2.4mm).

    LCSC C2649485 pin map (verified from EasyEDA symbol):
      1=VDD  2=SCL  3=GND  4=NC  5=INT  6=SDA
    I2C address: 0x29 (fixed).
    LED pin is not present on this package variant; LED is always-on
    when enabled by external circuitry.
    """
    sensor = Part("C2649485", "TCS34727FN",
                  footprint="C2649485:QFN-6_L2.4-W2.0-P0.65-BL")
    sensor["VDD"] += vdd
    sensor["SCL"] += scl
    sensor["GND"] += gnd
    sensor["INT"] += n_int
    sensor["SDA"] += sda
    # Pin 4 = NC left unconnected

    # Decoupling per datasheet: 100nF + 1uF close to VDD
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c1[1] += vdd; c1[2] += gnd

    c2 = Part("Device", "C", value="1uF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    c2[1] += vdd; c2[2] += gnd


@subcircuit
def led_driver(vdd, gnd, gate, led_sw):
    """White LED with BSS138 MOSFET control.

    LED anode -> VDD, 33R series resistor, then LED cathode to MOSFET drain.
    MOSFET source to GND. Gate pull-down keeps LED off on float.
    33R @ 3.3V with 2.1V LED Vf = ~36mA.
    """
    led = Part("Device", "LED", value="White",
               footprint="LED_SMD:LED_0603_1608Metric")
    led["A"] += vdd

    r_led = Part("Device", "R", value="33R",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_led[1] += led["K"]
    r_led[2] += led_sw

    mosfet = Part("Transistor_FET", "BSS138", value="BSS138",
                  footprint="Package_TO_SOT_SMD:SOT-23")
    mosfet["G"] += gate
    mosfet["D"] += led_sw
    mosfet["S"] += gnd

    r_gate = Part("Device", "R", value="100K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_gate[1] += gate
    r_gate[2] += gnd


@subcircuit
def i2c_pullups(vdd, sda, scl):
    """10K I2C pull-up resistors on SDA and SCL."""
    r_sda = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vdd; r_sda[2] += sda

    r_scl = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vdd; r_scl[2] += scl


# Breakout Header: VIN, GND, SDA, SCL, INT, LED_EN
header = Part("Connector_Generic", "Conn_01x06",
              value="Conn_01x06",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
header.edge_preference = "bottom"
header[1] += vin
header[2] += gnd
header[3] += sda
header[4] += scl
header[5] += n_int
header[6] += led_en

# Instantiate all subcircuits
voltage_regulator(vin, v3v3, gnd)
tcs34725_sensor(v3v3, gnd, sda, scl, n_int)
led_driver(v3v3, gnd, led_en, led_sw)
i2c_pullups(v3v3, sda, scl)
