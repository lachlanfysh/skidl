from skidl import *

# Power rails
vin = Net("VIN"); vin.drive = POWER
v33 = Net("3V3"); v33.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C signal nets (5V side - from host)
sda_5v = Net("SDA_5V")
scl_5v = Net("SCL_5V")

# I2C signal nets (3.3V side - to sensor)
sda_33 = Net("SDA_3V3")
scl_33 = Net("SCL_3V3")

# --- AMS1117-3.3: 5V -> 3.3V LDO ---
ldo = Part("Regulator_Linear", "AMS1117-3.3",
           footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
ldo["VI"] += vin
ldo["GND"] += gnd
ldo["VO"] += v33

# Input bulk cap on VIN
cin = Part("Device", "C", value="10uF",
           footprint="Capacitor_SMD:C_0805_2012Metric")
cin[1] += vin
cin[2] += gnd

# Output bulk cap on 3V3
cout = Part("Device", "C", value="10uF",
            footprint="Capacitor_SMD:C_0805_2012Metric")
cout[1] += v33
cout[2] += gnd

# --- BMP280 pressure/temperature sensor (I2C mode) ---
bmp = Part("Sensor_Pressure", "BMP280",
           footprint="Package_LGA:Bosch_LGA-8_2x2.5mm_P0.65mm_ClockwisePinNumbering")

bmp["VDD"] += v33
bmp["VDDIO"] += v33
bmp["GND"] += gnd

# I2C mode: CSB tied HIGH (to 3.3V), SDO LOW sets I2C address 0x76
bmp["CSB"] += v33
bmp["SDO"] += gnd

# I2C pin connections (BMP280 uses SPI-style names: SDI=SDA, SCK=SCL)
bmp["SDI"] += sda_33
bmp["SCK"] += scl_33

# BMP280 decoupling caps
c_bmp1 = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
c_bmp1[1] += v33
c_bmp1[2] += gnd

c_bmp2 = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
c_bmp2[1] += v33
c_bmp2[2] += gnd

# --- BSS138 bidirectional I2C level shifter ---
# Standard topology: Gate to LV rail (3.3V), Source = LV side, Drain = HV side

# SDA level shifter
mos_sda = Part("Transistor_FET", "BSS138",
               footprint="Package_TO_SOT_SMD:SOT-23")
mos_sda["G"] += v33       # Gate to 3.3V (LV rail)
mos_sda["S"] += sda_33    # Source = 3.3V side
mos_sda["D"] += sda_5v    # Drain = 5V side

# SCL level shifter
mos_scl = Part("Transistor_FET", "BSS138",
               footprint="Package_TO_SOT_SMD:SOT-23")
mos_scl["G"] += v33       # Gate to 3.3V (LV rail)
mos_scl["S"] += scl_33    # Source = 3.3V side
mos_scl["D"] += scl_5v    # Drain = 5V side

# Pull-ups on 3.3V side
r_sda_33 = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
r_sda_33[1] += v33
r_sda_33[2] += sda_33

r_scl_33 = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
r_scl_33[1] += v33
r_scl_33[2] += scl_33

# Pull-ups on 5V side
r_sda_5v = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
r_sda_5v[1] += vin
r_sda_5v[2] += sda_5v

r_scl_5v = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
r_scl_5v[1] += vin
r_scl_5v[2] += scl_5v

# --- 6-pin header: VIN, 3V3, GND, SDA, SCL, GND ---
hdr = Part("Connector", "Conn_01x06_Pin",
           footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
hdr["Pin_1"] += vin      # VIN (5V input)
hdr["Pin_2"] += v33      # 3V3 output (for reference)
hdr["Pin_3"] += gnd      # GND
hdr["Pin_4"] += sda_5v   # SDA (5V-safe I2C)
hdr["Pin_5"] += scl_5v   # SCL (5V-safe I2C)
hdr["Pin_6"] += gnd      # GND (second ground pin)

hdr.edge_preference = "bottom"
