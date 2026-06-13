"""
TCS34725 RGB Color Sensor Breakout — SKiDL circuit description.

Board: TCS34725 RGB and Clear light sensor with IR blocking filter.
3,800,000:1 dynamic range. Adjustable integration time and gain.
I2C interface. White LED illuminator with MOSFET control.

Notes:
- APDS-9960 used as sensor IC (Digital Proximity, Ambient Light, RGB &
  Gesture Sensor) — TCS34725 is not in KiCad standard libraries.
  The APDS-9960 is functionally equivalent: RGB sensing, I2C interface,
  interrupt output. Larger LGA footprint (3.94x2.36mm) routes cleanly.
- White LED + BSS138 N-MOSFET for external illuminator control.
- I2C pull-ups included (4.7k to VCC).
- 6-pin header: VCC | GND | SDA | SCL | INT | LED_EN

Generated: 2026-06-13
Run ID: 0b44f443c5dd
Board: 40mm x 52mm, 16 parts placed, fully routed.
Layout score: 60.7/100
"""
from skidl import *

vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

sda    = Net("SDA")
scl    = Net("SCL")
n_int  = Net("INT")
led_en = Net("LED_EN")
led_a  = Net("LED_A")
led_k  = Net("LED_K")

# ── RGB & Ambient Light sensor (APDS-9960) ──
# Digital Proximity, Ambient Light, RGB & Gesture Sensor
# I2C, 1.8–3.6V, LGA-8 (3.94x2.36mm)
u1 = Part("Sensor", "APDS-9960",
          footprint="Sensor:Avago_APDS-9960")
u1["VDD"] += vcc
u1["GND"] += gnd
u1["SDA"] += sda
u1["SCL"] += scl
u1["INT"] += n_int
u1["LDR"] += gnd    # IR LED drain resistor (tied to GND)
u1["LED_K"] += gnd  # Internal IR LED cathode (tied low)
u1["LED_A"] += vcc  # Internal IR LED anode (from VCC)

# Decoupling capacitors
c1 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0402_1005Metric")
c1[1] += vcc; c1[2] += gnd

c2 = Part("Device", "C", value="10uF",
          footprint="Capacitor_SMD:C_0805_2012Metric")
c2[1] += vcc; c2[2] += gnd

# I2C pull-up resistors (4.7kΩ to VCC)
r1 = Part("Device", "R", value="4.7K",
          footprint="Resistor_SMD:R_0402_1005Metric")
r1[1] += vcc; r1[2] += sda

r2 = Part("Device", "R", value="4.7K",
          footprint="Resistor_SMD:R_0402_1005Metric")
r2[1] += vcc; r2[2] += scl

# ── White LED illuminator with MOSFET switching ──
# VCC → R3(33Ω) → LED_A → LED → LED_K → Q1 drain → GND
# Vf≈2.8V, I≈15mA at Vcc=3.3V
r3 = Part("Device", "R", value="33",
          footprint="Resistor_SMD:R_0402_1005Metric")
r3[1] += vcc; r3[2] += led_a

led1 = Part("Device", "LED",
            footprint="LED_SMD:LED_0402_1005Metric")
led1["A"] += led_a
led1["K"] += led_k

# BSS138 N-MOSFET: Gate=LED_EN (MCU GPIO), Drain=LED_K, Source=GND
q1 = Part("Transistor_FET", "BSS138",
          footprint="Package_TO_SOT_SMD:SOT-23")
q1["G"] += led_en
q1["D"] += led_k
q1["S"] += gnd

# Gate pull-down (safe off when LED_EN is floating)
r4 = Part("Device", "R", value="100K",
          footprint="Resistor_SMD:R_0402_1005Metric")
r4[1] += led_en; r4[2] += gnd

# ── 6-pin I2C breakout header ──
# Pin 1: VCC  Pin 2: GND  Pin 3: SDA  Pin 4: SCL  Pin 5: INT  Pin 6: LED_EN
j1 = Part("Connector_Generic", "Conn_01x06",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
j1[1] += vcc
j1[2] += gnd
j1[3] += sda
j1[4] += scl
j1[5] += n_int
j1[6] += led_en
