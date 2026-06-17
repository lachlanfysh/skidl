from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
v3v3 = Net("3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C bus nets
sda = Net("SDA")
scl = Net("SCL")

# --- Input connector (5V input + I2C passthrough) ---
@subcircuit
def input_header(vcc, gnd, sda, scl):
    j = Part("Connector_Generic", "Conn_01x04",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    vcc += j[1]
    gnd += j[2]
    sda += j[3]
    scl += j[4]
    j.edge_preference = "bottom"

# --- 3.3V LDO Regulator (AP2112K-3.3) ---
@subcircuit
def power_reg(vin, vout, gnd):
    u = Part("Regulator_Linear", "AP2112K-3.3",
             footprint="Package_TO_SOT_SMD:SOT-23-5")
    vin += u["VIN"]
    gnd += u["GND"]
    u["EN"] += vin   # EN tied to VIN for always-on
    u["NC"] += NC()
    vout += u["VOUT"]
    cin = Part("Device", "C_Polarized", value="10uF",
               footprint="Capacitor_SMD:C_0805_2012Metric")
    vin += cin[1]; gnd += cin[2]
    cin2 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    vin += cin2[1]; gnd += cin2[2]
    cout = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    vout += cout[1]; gnd += cout[2]
    cout2 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    vout += cout2[1]; gnd += cout2[2]

# --- BME280 Sensor ---
# BME280 uses SPI-style pin names; in I2C mode: SDI=SDA, SCK=SCL, CSB=high, SDO=addr
@subcircuit
def bme280_sensor(v3v3, gnd, sda, scl):
    u = Part("Sensor", "BME280",
             footprint="Package_LGA:Bosch_LGA-8_2.5x2.5mm_P0.65mm_ClockwisePinNumbering")
    v3v3 += u["VDD"], u["VDDIO"]
    gnd += u["GND"]
    sda += u["SDI"]
    scl += u["SCK"]
    u["CSB"] += v3v3  # I2C mode
    u["SDO"] += gnd   # I2C address 0x76
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    v3v3 += c[1]; gnd += c[2]

# --- I2C pull-up resistors ---
@subcircuit
def i2c_pullups(v3v3, gnd, sda, scl):
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    v3v3 += r_sda[1]; sda += r_sda[2]
    v3v3 += r_scl[1]; scl += r_scl[2]

# --- Power LED indicator ---
@subcircuit
def power_led(v3v3, gnd):
    led = Part("Device", "LED",
               footprint="LED_SMD:LED_0805_2012Metric")
    r = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
    v3v3 += r[1]; r[2] += led["K"]; gnd += led["A"]

# --- Mounting holes ---
@subcircuit
def mounting_holes():
    for i in range(4):
        mh = Part("Mechanical", "MountingHole",
                  footprint="MountingHole:MountingHole_3.2mm_M3")

# Instantiate all blocks
input_header(vcc, gnd, sda, scl)
power_reg(vcc, v3v3, gnd)
bme280_sensor(v3v3, gnd, sda, scl)
i2c_pullups(v3v3, gnd, sda, scl)
power_led(v3v3, gnd)
mounting_holes()

EDA_FLOORPLAN = {
    "outline": {"width_mm": 36.0, "height_mm": 30.0, "corner_radius_mm": 1.0},
}
