"""
ADS1115 16-bit ADC Breakout Board
Generated via eda-mcp MCP server (board_id: ads1115-adc).

Board: ADS1115IDGS in TSSOP-10, I2C interface, 4 analog inputs
Connectors: separate I2C/power header and analog input header
I2C address: 0x48 (ADDR tied to GND)
Power: 3.3V or 5V compatible

Best MCP run: job d73bb22da2d5 / run a7dec0192891
Layout score: 84.8/100, schematic OK, 13/13 parts placed, no overlaps
Routing: failed_reviewable (DRC_UNCONNECTED on AIN and power nets - server routing issue)
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C nets
sda = Net("SDA")
scl = Net("SCL")

# Analog input nets
ain0 = Net("AIN0")
ain1 = Net("AIN1")
ain2 = Net("AIN2")
ain3 = Net("AIN3")

# Alert/ready net
alert = Net("ALERT")


@subcircuit
def ads1115_block(vcc, gnd, sda, scl, ain0, ain1, ain2, ain3, alert):
    u1 = Part("Analog_ADC", "ADS1115IDGS",
              footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm")

    vcc += u1["VDD"]
    gnd += u1["GND"]
    sda += u1["SDA"]
    scl += u1["SCL"]
    ain0 += u1["AIN0"]
    ain1 += u1["AIN1"]
    ain2 += u1["AIN2"]
    ain3 += u1["AIN3"]
    alert += u1["ALERT/RDY"]
    gnd += u1["ADDR"]  # 0x48 I2C address

    # Decoupling cap (100nF auto-placed near IC)
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    vcc += c1[1]
    gnd += c1[2]

    # Bulk cap on VCC rail (C_Polarized uses pin numbers 1/2, not +/-)
    c2 = Part("Device", "C_Polarized", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    vcc += c2[1]
    gnd += c2[2]

    # I2C pull-up resistors (4.7k to VCC)
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    vcc += r_sda[1]
    sda += r_sda[2]
    vcc += r_scl[1]
    scl += r_scl[2]


ads1115_block(vcc, gnd, sda, scl, ain0, ain1, ain2, ain3, alert)

# I2C + power connector (VCC, GND, SDA, SCL)
j_i2c = Part("Connector_Generic", "Conn_01x04",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
j_i2c.edge_preference = "left"
vcc += j_i2c[1]
gnd += j_i2c[2]
sda += j_i2c[3]
scl += j_i2c[4]

# Analog inputs connector (AIN0-AIN3)
j_ain = Part("Connector_Generic", "Conn_01x04",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
j_ain.edge_preference = "right"
ain0 += j_ain[1]
ain1 += j_ain[2]
ain2 += j_ain[3]
ain3 += j_ain[4]

# Alert output connector
j_alert = Part("Connector_Generic", "Conn_01x01",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x01_P2.54mm_Vertical")
j_alert.edge_preference = "top"
alert += j_alert[1]

# Mounting holes
mh1 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_3.2mm_M3")
mh2 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_3.2mm_M3")
mh3 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_3.2mm_M3")
mh4 = Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_3.2mm_M3")

# Board outline with mounting holes - centers at 4mm inward for M3 courtyard clearance
EDA_FLOORPLAN = {
    "outline": {"width_mm": 40.0, "height_mm": 30.0, "corner_radius_mm": 1},
    "fixed_positions": [
        {"ref": "H1", "x_mm": 4.0, "y_mm": 4.0, "rotation_deg": 0},
        {"ref": "H2", "x_mm": 36.0, "y_mm": 4.0, "rotation_deg": 0},
        {"ref": "H3", "x_mm": 4.0, "y_mm": 26.0, "rotation_deg": 0},
        {"ref": "H4", "x_mm": 36.0, "y_mm": 26.0, "rotation_deg": 0},
    ],
}
