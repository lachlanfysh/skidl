from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C and signal nets
sda   = Net("SDA")
scl   = Net("SCL")
alert = Net("ALERT")
addr0 = Net("ADDR0")
addr1 = Net("ADDR1")
addr2 = Net("ADDR2")

# MCP9808 temperature sensor (MSOP-8, easier to hand solder than DFN)
u1 = Part("Sensor_Temperature", "MCP9808_MSOP",
          footprint="Package_SO:MSOP-8_3x3mm_P0.65mm")
vcc   += u1["V_{DD}"]
gnd   += u1["GND"]
sda   += u1["SDA"]
scl   += u1["SCL"]
alert += u1["Alert"]
addr0 += u1["A0"]
addr1 += u1["A1"]
addr2 += u1["A2"]

# Decoupling cap 100nF on VCC (auto-placed near U1)
c1 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0603_1608Metric")
vcc += c1[1]
gnd += c1[2]

# Bulk decoupling cap 10uF
c2 = Part("Device", "C", value="10uF",
          footprint="Capacitor_SMD:C_0805_2012Metric")
vcc += c2[1]
gnd += c2[2]

# I2C pull-up resistors 4.7k on SDA and SCL
r_sda = Part("Device", "R", value="4.7k",
             footprint="Resistor_SMD:R_0603_1608Metric")
vcc += r_sda[1]
sda += r_sda[2]

r_scl = Part("Device", "R", value="4.7k",
             footprint="Resistor_SMD:R_0603_1608Metric")
vcc += r_scl[1]
scl += r_scl[2]

# Address resistors (pull low, 10k to GND — default I2C addr 0x18)
r_a0 = Part("Device", "R", value="10k",
            footprint="Resistor_SMD:R_0603_1608Metric")
gnd   += r_a0[1]
addr0 += r_a0[2]

r_a1 = Part("Device", "R", value="10k",
            footprint="Resistor_SMD:R_0603_1608Metric")
gnd   += r_a1[1]
addr1 += r_a1[2]

r_a2 = Part("Device", "R", value="10k",
            footprint="Resistor_SMD:R_0603_1608Metric")
gnd   += r_a2[1]
addr2 += r_a2[2]

# Alert pull-up resistor 10k (Alert is open-drain output)
r_alert = Part("Device", "R", value="10k",
               footprint="Resistor_SMD:R_0603_1608Metric")
vcc   += r_alert[1]
alert += r_alert[2]

# 6-pin I2C breakout header on bottom edge: VCC, GND, SDA, SCL, ALERT, GND
j1 = Part("Connector", "Conn_01x06_Pin",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
j1.edge_preference = "bottom"
vcc   += j1[1]
gnd   += j1[2]
sda   += j1[3]
scl   += j1[4]
alert += j1[5]
gnd   += j1[6]

EDA_FLOORPLAN = {
    "outline": {"width_mm": 30, "height_mm": 28, "corner_radius_mm": 1},
}
