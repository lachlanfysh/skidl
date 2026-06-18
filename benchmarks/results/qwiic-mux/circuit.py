from skidl import *

# Power nets
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C upstream bus
sda_up = Net("SDA")
scl_up = Net("SCL")

# 8 downstream I2C channels
sd = [Net(f"SD{i}") for i in range(8)]
sc = [Net(f"SC{i}") for i in range(8)]

# Address select nets
a0_net = Net("A0")
a1_net = Net("A1")
a2_net = Net("A2")

# Reset net
rst_net = Net("RESET_N")

# TCA9548A TSSOP-24 (EasyEDA-converted symbol, standard KiCad TSSOP-24 footprint)
u1 = Part("C130026", "TCA9548APWR",
          footprint="Package_SO:TSSOP-24_4.4x7.8mm_P0.65mm")
u1.lcsc = "C130026"

# Connect power
vcc += u1["VCC"]
gnd += u1["GND"]

# Connect upstream I2C
sda_up += u1["SDA"]
scl_up += u1["SCL"]

# Connect downstream I2C channels
for i in range(8):
    sd[i] += u1[f"SD{i}"]
    sc[i] += u1[f"SC{i}"]

# Connect address and reset pins
a0_net += u1["A0"]
a1_net += u1["A1"]
a2_net += u1["A2"]
rst_net += u1["~{RESET}"]

# Decoupling cap for TCA9548A
c_bypass = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
c_bypass[1] += vcc
c_bypass[2] += gnd

# Bulk decoupling cap
c_bulk = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
c_bulk[1] += vcc
c_bulk[2] += gnd

# 10K pull-up on RESET_N
r_rst = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_rst[1] += vcc
r_rst[2] += rst_net

# 10K I2C pull-ups on upstream SDA/SCL
r_sda = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_sda[1] += vcc
r_sda[2] += sda_up

r_scl = Part("Device", "R", value="10K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_scl[1] += vcc
r_scl[2] += scl_up

# Address select pull-downs (A0/A1/A2 to GND for default address 0x70)
r_a0 = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_a0[1] += a0_net
r_a0[2] += gnd

r_a1 = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_a1[1] += a1_net
r_a1[2] += gnd

r_a2 = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")
r_a2[1] += a2_net
r_a2[2] += gnd

# Upstream Qwiic JST-SH connector (J1) — GND/VCC/SDA/SCL
# Qwiic pinout: 1=GND, 2=VCC(3.3V), 3=SDA, 4=SCL
# Using Connector_Generic (4 pins), with JST_SH footprint (has MP mounting pad)
j1 = Part("Connector_Generic", "Conn_01x04",
          footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
j1[1] += gnd
j1[2] += vcc
j1[3] += sda_up
j1[4] += scl_up

# Downstream Qwiic connectors (J2, J3, J4) sharing channels 0-2 respectively
# (3 physical Qwiic ports covering the first 3 downstream channels)
j2 = Part("Connector_Generic", "Conn_01x04",
          footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
j2[1] += gnd
j2[2] += vcc
j2[3] += sd[0]
j2[4] += sc[0]

j3 = Part("Connector_Generic", "Conn_01x04",
          footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
j3[1] += gnd
j3[2] += vcc
j3[3] += sd[1]
j3[4] += sc[1]

j4 = Part("Connector_Generic", "Conn_01x04",
          footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
j4[1] += gnd
j4[2] += vcc
j4[3] += sd[2]
j4[4] += sc[2]

# Channels 3-7 are available via through-hole header pads for breadboard access
# 2x5 header: 5 SDA/SCL pairs for channels 3-7
j_header = Part("Connector_Generic", "Conn_02x05_Odd_Even",
                footprint="Connector_PinHeader_2.54mm:PinHeader_2x05_P2.54mm_Vertical")
# Odd pins: SDA3..7, Even pins: SCL3..7
j_header[1] += sd[3]; j_header[2] += sc[3]
j_header[3] += sd[4]; j_header[4] += sc[4]
j_header[5] += sd[5]; j_header[6] += sc[5]
j_header[7] += sd[6]; j_header[8] += sc[6]
j_header[9] += sd[7]; j_header[10] += sc[7]

