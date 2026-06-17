"""
INA219 High Side DC Current Sensor Breakout
============================================
INA219B chip measures both high side voltage and DC current draw over I2C
with 1% precision. Handles high side current measuring up to +26VDC, even
though powered with 3 or 5V. Precision amplifier measures voltage across 0.1
ohm shunt resistor. I2C interface with address select pins for up to 16
devices on one bus.

MCP pipeline result: succeeded (DRC clean, fully routed, 40x25mm board)
Layout score: 69.2/100, run_id: 898e56bddac8

Known issues discovered during MCP iteration:
- INA219BIDR converted via convert_lcsc() has Unicode en-dash in "IN-" pin
  name (shows as "IN–"). Must use u1[7] numeric pin access instead of u1["IN-"].
- C_Polarized has no named +/- pins; only numeric pins 1 and 2 work.
- LCSC-derived footprint "C2155799:SOIC-8_L5.0-W4.0-P1.27-LS6.1-BL." is not
  installed on Railway; must substitute Package_SO:SOIC-8_3.9x4.9mm_P1.27mm.
- Horizontal screw terminal footprints cause DRC (silkscreen/Edge.Cuts overlap)
  when placed at board edge by placer. Use pin headers or ensure large margins.
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Signal nets
vin_pos = Net("VIN_POS")
vin_neg = Net("VIN_NEG")
i2c_sda = Net("SDA")
i2c_scl = Net("SCL")
addr0   = Net("ADDR0")
addr1   = Net("ADDR1")

# INA219B SOIC-8 current/power monitor (LCSC C2155799)
# Pin 1=A1, 2=A0, 3=SDA, 4=SCL, 5=VS, 6=GND, 7=IN-, 8=IN+
# pin 7 name is Unicode en-dash "IN–" in converted symbol; use numeric index
u1 = Part("C2155799", "INA219BIDR", footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
vcc     += u1["VS"]
gnd     += u1["GND"]
i2c_sda += u1["SDA"]
i2c_scl += u1["SCL"]
addr0   += u1["A0"]
addr1   += u1["A1"]
vin_neg += u1[7]    # IN- via numeric index (unicode en-dash workaround)
vin_pos += u1[8]    # IN+

# 0.1 ohm shunt resistor in high-side current path (2512 for 1W power handling)
r_shunt = Part("Device", "R", value="0.1", footprint="Resistor_SMD:R_2512_6332Metric")
vin_pos += r_shunt[1]
vin_neg += r_shunt[2]

# 100nF decoupling cap on VCC
c1 = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
vcc += c1[1]
gnd += c1[2]

# 10uF bulk cap on VCC (C_Polarized pins are numeric only, not +/-)
c2 = Part("Device", "C_Polarized", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")
vcc += c2[1]
gnd += c2[2]

# I2C pull-up resistors (4.7K to VCC)
r_sda = Part("Device", "R", value="4.7K", footprint="Resistor_SMD:R_0603_1608Metric")
r_scl = Part("Device", "R", value="4.7K", footprint="Resistor_SMD:R_0603_1608Metric")
vcc     += r_sda[1]
i2c_sda += r_sda[2]
vcc     += r_scl[1]
i2c_scl += r_scl[2]

# Address select: 0-ohm to GND → I2C address 0x40
# Replace with VCC/SDA/SCL connections for other addresses (up to 16 devices)
r_a0 = Part("Device", "R", value="0", footprint="Resistor_SMD:R_0603_1608Metric")
r_a1 = Part("Device", "R", value="0", footprint="Resistor_SMD:R_0603_1608Metric")
addr0 += r_a0[1]
gnd   += r_a0[2]
addr1 += r_a1[1]
gnd   += r_a1[2]

# High-side current IN header (VIN+ and GND reference)
# Pin headers avoid screw terminal silkscreen/Edge.Cuts DRC clearance issues
j_vin = Part("Connector_Generic", "Conn_01x02",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
j_vin[1] += vin_pos
j_vin[2] += gnd

# Load output header (after shunt — connect to load positive terminal)
j_load = Part("Connector_Generic", "Conn_01x02",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
j_load[1] += vin_neg
j_load[2] += gnd

# I2C + Power header (VCC, GND, SDA, SCL)
j_i2c = Part("Connector_Generic", "Conn_01x04",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
j_i2c[1] += vcc
j_i2c[2] += gnd
j_i2c[3] += i2c_sda
j_i2c[4] += i2c_scl

EDA_FLOORPLAN = {
    "outline": {"width_mm": 40.0, "height_mm": 25.0, "corner_radius_mm": 1.5},
}
