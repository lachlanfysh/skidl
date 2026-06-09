"""
MAX31856 Thermocouple Amplifier Board
======================================
Precision thermocouple-to-digital converter with cold junction compensation.
Supports all common thermocouple types via SPI interface.

Subcircuits:
  - power_supply: 5V input -> 3.3V LDO (AP2112K-3.3) with decoupling
  - thermocouple_frontend: MAX31856 IC with bias resistor and thermocouple input
  - spi_interface: SPI header + DRDY/FAULT outputs with pull-ups
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# =============================================================================
# Global power nets
# =============================================================================
vcc = Net("+3V3"); vcc.drive = POWER
vin = Net("VIN"); vin.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# =============================================================================
# Power Supply: 5V -> 3.3V LDO
# =============================================================================
@subcircuit
def power_supply(vin, vcc, gnd):
    """AP2112K-3.3 LDO regulator with input and output decoupling."""
    # LDO regulator
    u_reg = Part("Regulator_Linear", "AP2112K-3.3",
                 footprint="Package_TO_SOT_SMD:SOT-23-5")
    u_reg["VIN"] += vin
    u_reg["GND"] += gnd
    u_reg["EN"] += vin       # Enable tied to input (always on)
    u_reg["VOUT"] += vcc

    # Input decoupling cap (1uF ceramic)
    c_in = Part("Device", "C", value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += vin
    c_in[2] += gnd

    # Output decoupling cap (1uF ceramic)
    c_out = Part("Device", "C", value="1uF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_out[1] += vcc
    c_out[2] += gnd

    # Bulk input cap (10uF)
    c_bulk = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vin
    c_bulk[2] += gnd

# =============================================================================
# Thermocouple Frontend: MAX31856 + bias + thermocouple connector
# =============================================================================
@subcircuit
def thermocouple_frontend(vcc, gnd, spi_cs, spi_sck, spi_sdo, spi_sdi,
                          drdy_net, fault_net):
    """MAX31856 thermocouple-to-digital converter with support circuitry."""
    # MAX31856 IC
    u_tc = Part("Sensor_Temperature", "MAX31856",
                footprint="Package_SO:TSSOP-14_4.4x5mm_P0.65mm")
    u_tc["AVDD"] += vcc
    u_tc["DVDD"] += vcc
    u_tc["AGND"] += gnd
    u_tc["DGND"] += gnd

    # SPI interface
    u_tc["~{CS}"] += spi_cs
    u_tc["SCK"] += spi_sck
    u_tc["SDO"] += spi_sdo
    u_tc["SDI"] += spi_sdi

    # Status outputs
    u_tc["~{DRDY}"] += drdy_net
    u_tc["~{FAULT}"] += fault_net

    # BIAS resistor: 2.2K from BIAS pin to T- for noise filtering
    # Per datasheet, connects to thermocouple negative input
    r_bias = Part("Device", "R", value="2.2K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_bias[1] += u_tc["BIAS"]
    r_bias[2] += u_tc["T-"]

    # Analog supply decoupling: 100nF close to AVDD
    c_avdd = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_avdd[1] += vcc
    c_avdd[2] += gnd

    # Digital supply decoupling: 100nF close to DVDD
    c_dvdd = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dvdd[1] += vcc
    c_dvdd[2] += gnd

    # Thermocouple input connector (2-pin screw terminal / JST)
    j_tc = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    j_tc[1] += u_tc["T+"]
    j_tc[2] += u_tc["T-"]

# =============================================================================
# SPI Interface: header + pull-ups for DRDY and FAULT
# =============================================================================
@subcircuit
def spi_interface(vcc, gnd, spi_cs, spi_sck, spi_sdo, spi_sdi,
                  drdy_net, fault_net):
    """SPI header with pull-ups on active-low status outputs."""
    # Pull-up on ~DRDY (10K to VCC)
    r_drdy = Part("Device", "R", value="10K",
                  footprint="Resistor_SMD:R_0603_1608Metric")
    r_drdy[1] += vcc
    r_drdy[2] += drdy_net

    # Pull-up on ~FAULT (10K to VCC)
    r_fault = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
    r_fault[1] += vcc
    r_fault[2] += fault_net

    # Pull-up on ~CS (10K to VCC) - keeps chip deselected when MCU not driving
    r_cs = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_cs[1] += vcc
    r_cs[2] += spi_cs

    # SPI + status header (8-pin):
    # 1=VCC, 2=GND, 3=SCK, 4=SDI(MOSI), 5=SDO(MISO), 6=CS, 7=DRDY, 8=FAULT
    j_spi = Part("Connector_Generic", "Conn_01x08",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
    j_spi[1] += vcc
    j_spi[2] += gnd
    j_spi[3] += spi_sck
    j_spi[4] += spi_sdi
    j_spi[5] += spi_sdo
    j_spi[6] += spi_cs
    j_spi[7] += drdy_net
    j_spi[8] += fault_net

# =============================================================================
# Power input connector
# =============================================================================
j_pwr = Part("Connector_Generic", "Conn_01x02",
             footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
j_pwr[1] += vin
j_pwr[2] += gnd

# Status LED on 3.3V rail
led = Part("Device", "LED", value="GREEN",
           footprint="LED_SMD:LED_0603_1608Metric")
r_led = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
r_led[1] += vcc
r_led[2] += led[1]
led[2] += gnd

# =============================================================================
# Internal signal nets
# =============================================================================
spi_cs = Net("SPI_CS")
spi_sck = Net("SPI_SCK")
spi_sdo = Net("SPI_SDO")
spi_sdi = Net("SPI_SDI")
drdy = Net("DRDY")
fault = Net("FAULT")

# =============================================================================
# Instantiate subcircuits
# =============================================================================
power_supply(vin, vcc, gnd)
thermocouple_frontend(vcc, gnd, spi_cs, spi_sck, spi_sdo, spi_sdi, drdy, fault)
spi_interface(vcc, gnd, spi_cs, spi_sck, spi_sdo, spi_sdi, drdy, fault)

# =============================================================================
# Generate schematic
# =============================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
