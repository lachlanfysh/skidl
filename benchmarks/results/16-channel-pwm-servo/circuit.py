"""
PCA9685 16-channel PWM/servo driver breakout board.

Features:
- PCA9685 16-channel 12-bit PWM driver (TSSOP-28, LCSC C92206)
- I2C interface with 6 address select pins (A0-A5) for up to 64 boards on one bus
- 10K I2C pull-ups on SCL/SDA
- 10uF + 100nF decoupling on VCC (logic)
- External V+ power supply for servo motors (10uF + 100nF decoupling)
- OE (output enable) pulled low via 10K resistor (active low, always enabled)
- EXTCLK pulled to GND via 10K (uses internal 25MHz oscillator)
- 16x Conn_01x03 headers for servo connections (GND/V+/PWM per channel)
  arranged in a single row along the bottom of the board
- 6-pin I2C/power header (VCC, GND, SCL, SDA, OE_N, V_SERVO) on left edge
"""

from skidl import *

# === Power nets ===
vcc = Net("VCC")
vcc.drive = POWER
gnd = Net("GND")
gnd.drive = POWER
v_servo = Net("V_SERVO")
v_servo.drive = POWER
scl = Net("SCL")
sda = Net("SDA")
oe_n = Net("OE_N")

# === PCA9685 (TSSOP-28, LCSC C92206) ===
u1 = Part("C92206", "PCA9685PW_Q900,118",
          footprint="Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm")

vcc += u1["VDD"]
gnd += u1["VSS"]
scl += u1["SCL"]
sda += u1["SDA"]
oe_n += u1["~{OE}"]

# Address pins with pull-down resistors (base address 0x40)
addr_nets = []
for i in range(6):
    n = Net(f"ADDR{i}")
    n += u1[f"A{i}"]
    addr_nets.append(n)

# EXTCLK pulled to GND (use internal 25MHz oscillator)
extclk_net = Net("EXTCLK")
extclk_net += u1["EXTCLK"]

# PWM output nets
pwm_nets = []
for i in range(16):
    n = Net(f"PWM{i}")
    n += u1[f"LED{i}"]
    pwm_nets.append(n)

# === I2C pull-ups (10K to VCC) ===
r_scl = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_sda = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
vcc += r_scl[1], r_sda[1]
scl += r_scl[2]
sda += r_sda[2]

# === OE pull-down to GND (outputs always enabled) ===
r_oe = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")
gnd += r_oe[1]
oe_n += r_oe[2]

# === EXTCLK pull-down to GND ===
r_extclk = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
gnd += r_extclk[1]
extclk_net += r_extclk[2]

# === Address pin pull-downs (A0-A5 to GND) ===
for i in range(6):
    r = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
    gnd += r[1]
    addr_nets[i] += r[2]

# === VCC (logic) decoupling ===
c_vcc_100n = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
vcc += c_vcc_100n[1]
gnd += c_vcc_100n[2]

c_vcc_10u = Part("Device", "C_Polarized", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
vcc += c_vcc_10u[1]
gnd += c_vcc_10u[2]

# === V_SERVO decoupling ===
c_sv_100n = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
v_servo += c_sv_100n[1]
gnd += c_sv_100n[2]

c_sv_10u = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
v_servo += c_sv_10u[1]
gnd += c_sv_10u[2]

# === 16x Servo headers (3-pin: GND / V_SERVO / PWM) ===
# Arranged in a single row of 16 along the bottom of the board.
# 2.54mm pitch x 16 = 40.64mm span. Board will be ~110mm x 45mm.
# Headers placed at y=42mm (bottom), x from 10mm to 50.64mm (step 2.54mm)
servo_hdr_refs = []
for i in range(16):
    h = Part("Connector_Generic", "Conn_01x03",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
    gnd += h["Pin_1"]
    v_servo += h["Pin_2"]
    pwm_nets[i] += h["Pin_3"]
    servo_hdr_refs.append(h.ref)

# === 6-pin I2C/power header (VCC, GND, SCL, SDA, OE_N, V_SERVO) ===
j_ctrl = Part("Connector_Generic", "Conn_01x06",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
j_ctrl.edge_preference = "left"
vcc += j_ctrl["Pin_1"]
gnd += j_ctrl["Pin_2"]
scl += j_ctrl["Pin_3"]
sda += j_ctrl["Pin_4"]
oe_n += j_ctrl["Pin_5"]
v_servo += j_ctrl["Pin_6"]

# === Floorplan: center U1 above a row of servo headers at bottom ===
# Board: 110mm x 50mm
# 16 headers at bottom row, 6.2mm pitch starting at x=7mm
# U1 centered at x=55mm (midpoint of 110mm board), y=20mm
# J_CTRL (6-pin) on left edge
EDA_FLOORPLAN = {
    "board_outline_mm": [110, 50],
    "fixed_positions": [
        {"ref": "U1", "x_mm": 55.0, "y_mm": 20.0, "rotation_deg": 0},
    ],
    "grid": {
        "refs": servo_hdr_refs,
        "rows": 1,
        "cols": 16,
        "x_mm": 7.0,
        "y_mm": 44.0,
        "dx_mm": 6.2,
        "dy_mm": 0,
        "soft": False,
    },
    "edge_anchors": [
        {"ref": j_ctrl.ref, "edge": "left"},
    ],
}
