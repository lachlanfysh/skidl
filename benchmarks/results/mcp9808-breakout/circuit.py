from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Internal nets
scl  = Net("SCL")
sda  = Net("SDA")
alert = Net("ALERT")
a0   = Net("A0")
a1   = Net("A1")
a2   = Net("A2")

# ── MCP9808 temperature sensor (MSOP-8) ──────────────────────────────────────
u1 = Part("Sensor_Temperature", "MCP9808_MSOP",
          footprint="Package_SO:MSOP-8_3x3mm_P0.65mm")

u1["V_{DD}"] += vcc
u1["GND"]   += gnd
u1["SDA"]   += sda
u1["SCL"]   += scl
u1["Alert"] += alert
u1["A0"]    += a0
u1["A1"]    += a1
u1["A2"]    += a2

# ── Decoupling cap 100nF (auto-placed near MCP9808) ──────────────────────────
c1 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0603_1608Metric")
c1[1] += vcc
c1[2] += gnd

# ── Bulk decoupling cap 10uF ──────────────────────────────────────────────────
c2 = Part("Device", "C_Polarized", value="10uF",
          footprint="Capacitor_SMD:C_0805_2012Metric")
c2[1] += vcc
c2[2] += gnd

# ── I2C pull-ups (10K on SCL and SDA) ────────────────────────────────────────
r_scl = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_scl[1] += vcc
r_scl[2] += scl

r_sda = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_sda[1] += vcc
r_sda[2] += sda

# ── ALERT pull-up 10K (open-drain output) ────────────────────────────────────
r_alert = Part("Device", "R", value="10K",
               footprint="Resistor_SMD:R_0603_1608Metric")
r_alert[1] += vcc
r_alert[2] += alert

# ── Address pin pull-downs 10K (A0, A1, A2 to GND = default address 0x18) ───
r_a0 = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_a0[1] += a0
r_a0[2] += gnd

r_a1 = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_a1[1] += a1
r_a1[2] += gnd

r_a2 = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_a2[1] += a2
r_a2[2] += gnd

# ── 6-pin breakout header: VIN, GND, SCL, SDA, ALERT, A0 ────────────────────
j1 = Part("Connector_Generic", "Conn_01x06",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
j1[1] += vcc    # VIN
j1[2] += gnd    # GND
j1[3] += scl    # SCL
j1[4] += sda    # SDA
j1[5] += alert  # ALERT
j1[6] += a0     # A0
