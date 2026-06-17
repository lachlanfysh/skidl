"""
FT232H USB-to-Multipurpose Breakout Board
Swiss-army-knife USB breakout for SPI, I2C, serial UART, JTAG protocols.
Built-in GPIO pins for LED control and button reading.
Direct computer-to-device communication without intermediate microcontroller.

Submit via MCP server: submit_skidl_code(code, board_name='ft232h-breakout')
"""
from skidl import *

vbus = Net("VBUS"); vbus.drive = POWER
vcc3v3 = Net("3V3"); vcc3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# USB Mini-B connector at bottom edge
usb = Part("Connector", "USB_B_Mini",
           footprint="Connector_USB:USB_Mini-B_Wuerth_65100516121_Horizontal")
usb.edge_preference = "bottom"
usb["VBUS"] += vbus
usb["GND"] += gnd
usb["ID"] += gnd
c_shield = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_shield[1] += usb["Shield"]; c_shield[2] += gnd

# 3.3V LDO (AP2112K-3.3): pin 4 is NC, tied to GND for footprint pad match
ldo = Part("Regulator_Linear", "AP2112K-3.3",
           footprint="Package_TO_SOT_SMD:SOT-23-5")
ldo["VIN"] += vbus; ldo["GND"] += gnd; ldo["EN"] += vbus; ldo["VOUT"] += vcc3v3
ldo["NC"] += gnd  # tie NC pad to GND so footprint pad gets a net

c_ldo_in_bulk = Part("Device", "C", value="10uF",
    footprint="Capacitor_SMD:C_0805_2012Metric")
c_ldo_in_bulk[1] += vbus; c_ldo_in_bulk[2] += gnd

c_ldo_in_byp = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_ldo_in_byp[1] += vbus; c_ldo_in_byp[2] += gnd

c_ldo_out_bulk = Part("Device", "C", value="10uF",
    footprint="Capacitor_SMD:C_0805_2012Metric")
c_ldo_out_bulk[1] += vcc3v3; c_ldo_out_bulk[2] += gnd

c_ldo_out_byp = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_ldo_out_byp[1] += vcc3v3; c_ldo_out_byp[2] += gnd

# FT232H USB Hi-Speed UART/FIFO/SPI/I2C/JTAG IC (LQFP-48)
ft = Part("Interface_USB", "FT232H",
          footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm")

usb["D+"] += ft["DP"]
usb["D-"] += ft["DM"]
ft["VREGIN"] += vbus  # USB 5V to internal regulator

# Internal 3.3V regulator output (VCCD) - connect VPHY and VPLL here too
vccd_net = Net("VCCD")
ft["VCCD"] += vccd_net; ft["VPHY"] += vccd_net; ft["VPLL"] += vccd_net
c_vccd1 = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_vccd1[1] += vccd_net; c_vccd1[2] += gnd
c_vccd2 = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_vccd2[1] += vccd_net; c_vccd2[2] += gnd

# Internal core supply output
vcccore_net = Net("VCCCORE")
ft["VCCCORE"] += vcccore_net
c_vcccore = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_vcccore[1] += vcccore_net; c_vcccore[2] += gnd

# Analog supply output
vcca_net = Net("VCCA")
ft["VCCA"] += vcca_net
c_vcca = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_vcca[1] += vcca_net; c_vcca[2] += gnd

# I/O supply from external 3.3V LDO
ft["VCCIO"] += vcc3v3
c_vccio = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_vccio[1] += vcc3v3; c_vccio[2] += gnd

ft["GND"] += gnd; ft["AGND"] += gnd

# USB current reference: 12K to GND (per FT232H datasheet)
r_ref = Part("Device", "R", value="12K",
    footprint="Resistor_SMD:R_0402_1005Metric")
r_ref[1] += ft["REF"]; r_ref[2] += gnd

# RESET: 10K pull-up to 3.3V
r_rst = Part("Device", "R", value="10K",
    footprint="Resistor_SMD:R_0402_1005Metric")
r_rst[1] += ft["~{RESET}"]; r_rst[2] += vcc3v3

ft["TEST"] += gnd  # required for normal operation per datasheet

# 12MHz crystal (FT232H requires 12MHz for Hi-Speed USB)
# Crystal_GND24: pins 1,2 = signal; pins 3,4 = GND pads
xtal = Part("Device", "Crystal_GND24",
            footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm")
xtal[1] += ft["XCSI"]
xtal[2] += ft["XCSO"]
xtal[3] += gnd
xtal[4] += gnd

c_xtal1 = Part("Device", "C", value="12pF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_xtal1[1] += ft["XCSI"]; c_xtal1[2] += gnd

c_xtal2 = Part("Device", "C", value="12pF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_xtal2[1] += ft["XCSO"]; c_xtal2[2] += gnd

# 93LC46BT EEPROM for FT232H device configuration storage (SOIC-8)
# Symbol from LCSC C16253 via EasyEDA conversion
# Bug note: pins 6,7 are NC in schematic but exist as pads in SOIC-8 footprint;
# must tie to GND to avoid FOOTPRINT_PAD_UNMATCHED errors in MCP pipeline
eeprom = Part("C16253", "93LC46BT-I_SN_C16253",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
eeprom["VCC"] += vcc3v3
eeprom["VSS"] += gnd
eeprom["CS"] += ft["EECS"]
eeprom["CLK"] += ft["EECLK"]
eeprom["DI"] += ft["EEDATA"]
eeprom["DO"] += ft["EEDATA"]
eeprom[6] += gnd  # NC pin - tied to GND for pad matching
eeprom[7] += gnd  # NC pin - tied to GND for pad matching

c_ee = Part("Device", "C", value="100nF",
    footprint="Capacitor_SMD:C_0402_1005Metric")
c_ee[1] += vcc3v3; c_ee[2] += gnd

# Power LED (green, VBUS present indicator)
led_pwr = Part("Device", "LED",
               footprint="LED_SMD:LED_0402_1005Metric")
r_led_pwr = Part("Device", "R", value="1K",
    footprint="Resistor_SMD:R_0402_1005Metric")
r_led_pwr[1] += vbus; r_led_pwr[2] += led_pwr["A"]
led_pwr["K"] += gnd

# Activity LED on ACBUS6 (configurable as TXLED#/RXLED# via EEPROM)
led_act = Part("Device", "LED",
               footprint="LED_SMD:LED_0402_1005Metric")
r_led_act = Part("Device", "R", value="1K",
    footprint="Resistor_SMD:R_0402_1005Metric")
act_net = Net("ACT_LED")
act_net += ft["ACBUS6"]
r_led_act[1] += vcc3v3; r_led_act[2] += led_act["A"]
led_act["K"] += act_net

# ADBUS0-7 breakout header at right edge (SPI/JTAG/MPSSE data bus)
hdr_adbus = Part("Connector_Generic", "Conn_01x08",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
hdr_adbus.edge_preference = "right"
adbus_nets = [Net(f"ADBUS{i}") for i in range(8)]
for i in range(8):
    adbus_nets[i] += ft[f"ADBUS{i}"]
    hdr_adbus[i+1] += adbus_nets[i]

# ACBUS0-7 breakout header at left edge (GPIO, JTAG signals)
hdr_acbus = Part("Connector_Generic", "Conn_01x08",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
hdr_acbus.edge_preference = "left"
acbus_nets_low = [Net(f"ACBUS{i}") for i in range(8)]
for i in range(8):
    acbus_nets_low[i] += ft[f"ACBUS{i}"]
    hdr_acbus[i+1] += acbus_nets_low[i]

# ACBUS8, ACBUS9 + power reference at top edge
hdr_misc = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
hdr_misc.edge_preference = "top"
acbus8_net = Net("ACBUS8"); acbus8_net += ft["ACBUS8"]
acbus9_net = Net("ACBUS9"); acbus9_net += ft["ACBUS9"]
hdr_misc[1] += acbus8_net
hdr_misc[2] += acbus9_net
hdr_misc[3] += vcc3v3
hdr_misc[4] += gnd

# Reset/power breakout header at top edge
hdr_pwr = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
hdr_pwr.edge_preference = "top"
hdr_pwr[1] += vcc3v3
hdr_pwr[2] += vbus
hdr_pwr[3] += gnd
hdr_pwr[4] += ft["~{RESET}"]

# Board outline: development breakout size
EDA_FLOORPLAN = {
    "outline": {"width_mm": 62, "height_mm": 51},
}
