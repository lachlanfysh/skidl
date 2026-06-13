"""
ADS1115 ADC Breakout - SKiDL code submitted to MCP server.
Run ID: db77d7983152 (job: b5caa6eb0f7b)
Status: layout complete, 1 overlap (J1/C1), schematic + PCB artifacts generated.
"""
from skidl import *

# Power rails
vdd = Net("VDD"); vdd.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C bus
scl = Net("SCL")
sda = Net("SDA")

# Analog inputs
ain0 = Net("AIN0")
ain1 = Net("AIN1")
ain2 = Net("AIN2")
ain3 = Net("AIN3")

# Misc nets
alert = Net("ALERT_RDY")
addr_net = Net("ADDR_SEL")

@subcircuit
def adc_core(vdd, gnd, scl, sda, ain0, ain1, ain2, ain3, alert, addr_net):
    # ADS1115 ADC IC (VSSOP-10)
    u1 = Part("Analog_ADC", "ADS1115IDGS",
              footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm")
    vdd  += u1["VDD"]
    gnd  += u1["GND"]
    scl  += u1["SCL"]
    sda  += u1["SDA"]
    ain0 += u1["AIN0"]
    ain1 += u1["AIN1"]
    ain2 += u1["AIN2"]
    ain3 += u1["AIN3"]
    alert    += u1["ALERT/RDY"]
    addr_net += u1["ADDR"]

    # 100nF decoupling cap on VDD (auto-detected by placer)
    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0402_1005Metric")
    vdd += c1[1]
    gnd += c1[2]

    # 10uF bulk cap on VDD
    c2 = Part("Device", "C_Polarized", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    vdd += c2[1]
    gnd += c2[2]

@subcircuit
def i2c_interface(vdd, gnd, scl, sda, alert, addr_net):
    # I2C pull-ups
    r_scl = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    vdd += r_scl[1]; scl += r_scl[2]
    vdd += r_sda[1]; sda += r_sda[2]

    # ALERT/RDY pull-up
    r_alert = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0402_1005Metric")
    vdd += r_alert[1]; alert += r_alert[2]

    # ADDR select: 0-ohm resistor to GND -> I2C address 0x48
    # Reflow to VDD pad for 0x49
    r_addr = Part("Device", "R", value="0",
                  footprint="Resistor_SMD:R_0402_1005Metric")
    addr_net += r_addr[1]; gnd += r_addr[2]

    # I2C + Power header (6-pin): VDD, GND, SCL, SDA, ALERT, ADDR_SEL
    j1 = Part("Connector_Generic", "Conn_01x06",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
    vdd      += j1[1]
    gnd      += j1[2]
    scl      += j1[3]
    sda      += j1[4]
    alert    += j1[5]
    addr_net += j1[6]

@subcircuit
def analog_inputs(ain0, ain1, ain2, ain3):
    # Analog inputs header (4-pin): AIN0, AIN1, AIN2, AIN3
    j2 = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    ain0 += j2[1]
    ain1 += j2[2]
    ain2 += j2[3]
    ain3 += j2[4]

adc_core(vdd, gnd, scl, sda, ain0, ain1, ain2, ain3, alert, addr_net)
i2c_interface(vdd, gnd, scl, sda, alert, addr_net)
analog_inputs(ain0, ain1, ain2, ain3)
