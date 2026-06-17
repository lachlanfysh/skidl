from skidl import *

# Power rails
vcc = Net('VCC'); vcc.drive = POWER
gnd = Net('GND'); gnd.drive = POWER

# SPI signals
sck  = Net('SCK')
miso = Net('MISO')
mosi = Net('MOSI')
cs_n = Net('CS_N')
drdy = Net('DRDY')
fault = Net('FAULT')

# Thermocouple input nets
tc_plus  = Net('TC_PLUS')
tc_minus = Net('TC_MINUS')

# BIAS net
bias_net = Net('BIAS')

# MAX31856 IC
u1 = Part('Sensor_Temperature', 'MAX31856',
          footprint='Package_SO:TSSOP-14_4.4x5mm_P0.65mm')

# Power connections
vcc += u1['AVDD'], u1['DVDD']
gnd += u1['AGND'], u1['DGND']

# SPI connections
cs_n  += u1['~{CS}']
sck   += u1['SCK']
miso  += u1['SDO']
mosi  += u1['SDI']
drdy  += u1['~{DRDY}']
fault += u1['~{FAULT}']

# Thermocouple inputs
tc_plus  += u1['T+']
tc_minus += u1['T-']

# BIAS pin
bias_net += u1['BIAS']

# DNC pin — connect to GND (pad 6 is SMD, needs a net assignment)
gnd += u1['DNC']

# Decoupling caps on VCC (AVDD and DVDD share single 3.3V supply on this breakout)
c1 = Part('Device', 'C', value='100nF', footprint='Capacitor_SMD:C_0603_1608Metric')
vcc += c1[1]
gnd += c1[2]

c2 = Part('Device', 'C', value='10uF', footprint='Capacitor_SMD:C_0805_2012Metric')
vcc += c2[1]
gnd += c2[2]

c3 = Part('Device', 'C', value='100nF', footprint='Capacitor_SMD:C_0603_1608Metric')
vcc += c3[1]
gnd += c3[2]

# BIAS bypass cap (0.1uF from BIAS pin to GND per MAX31856 datasheet)
c4 = Part('Device', 'C', value='100nF', footprint='Capacitor_SMD:C_0603_1608Metric')
bias_net += c4[1]
gnd += c4[2]

# Pull-up resistors for open-drain outputs (DRDY and FAULT are active-low open-drain)
r1 = Part('Device', 'R', value='10K', footprint='Resistor_SMD:R_0603_1608Metric')
vcc   += r1[1]
drdy  += r1[2]

r2 = Part('Device', 'R', value='10K', footprint='Resistor_SMD:R_0603_1608Metric')
vcc   += r2[1]
fault += r2[2]

# Thermocouple terminal block (2-pin screw terminal for thermocouple wires)
j1 = Part('Connector', 'Screw_Terminal_01x02',
          footprint='TerminalBlock_Altech:Altech_AK100_1x02_P5.00mm',
          value='THERMOCOUPLE')
tc_plus  += j1[1]
tc_minus += j1[2]

# SPI + power header (8-pin: 3V3, GND, SCK, MISO, MOSI, CS, DRDY, FAULT)
j2 = Part('Connector', 'Conn_01x08_Pin',
          footprint='Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical',
          value='SPI_HEADER')
vcc   += j2[1]
gnd   += j2[2]
sck   += j2[3]
miso  += j2[4]
mosi  += j2[5]
cs_n  += j2[6]
drdy  += j2[7]
fault += j2[8]
