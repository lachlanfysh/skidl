"""
DS3231 RTC Breakout Board — MCP Server Design
===============================================
Extremely accurate I2C RTC with integrated TCXO.
Battery-backed by CR2032 coin cell (SMD holder).

MCP server run: job 790e8e47e893 / run 3ab3473b0322
Board: 65 x 47mm, layout score 58.7/100, 0 overlaps
Status: SUCCEEDED (all DRC, placement, manufacturing gates passed)

Design notes:
- Used DS3231M (SOIC-16W) from Timer_RTC library
- SMD CR2032 holder (LINX BAT-HLD-012-SMT) — Keystone 500 is 25mm diameter,
  too large for compact boards and causes overlaps with other parts
- Mounting holes must be placed >=4mm from edges (courtyard adds ~1mm clearance)
- Board needed 65x47mm to clear DRC courtyard and routing congestion
  (compact estimate was 70x52mm; 65x47 sufficed after route-aware placement)
- VBAT net name: watch for potential auto-enrichment of lipo charger block
  on some server versions; use VCOIN if that occurs
"""

from skidl import *

vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
vbat = Net("VBAT"); vbat.drive = POWER
scl = Net("SCL")
sda = Net("SDA")
sqw_int = Net("SQW_INT")
freq_32k = Net("32KHZ")

@subcircuit
def rtc_core(vcc, gnd, vbat, scl, sda, sqw_int, freq_32k):
    u1 = Part("Timer_RTC", "DS3231M",
              footprint="Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm")
    u1.ref = "U1"
    vcc += u1["VCC"]; vbat += u1["VBAT"]; gnd += u1["GND"]
    scl += u1["SCL"]; sda += u1["SDA"]
    sqw_int += u1["~{INT}/SQW"]; freq_32k += u1["32KHZ"]
    u1["~{RST}"] += vcc

    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c1.ref = "C1"; vcc += c1[1]; gnd += c1[2]

    c2 = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    c2.ref = "C2"; vcc += c2[1]; gnd += c2[2]

    c3 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c3.ref = "C3"; vbat += c3[1]; gnd += c3[2]

    r1 = Part("Device", "R", value="4.7k",
              footprint="Resistor_SMD:R_0603_1608Metric")
    r1.ref = "R1"; vcc += r1[1]; scl += r1[2]

    r2 = Part("Device", "R", value="4.7k",
              footprint="Resistor_SMD:R_0603_1608Metric")
    r2.ref = "R2"; vcc += r2[1]; sda += r2[2]

    r3 = Part("Device", "R", value="10k",
              footprint="Resistor_SMD:R_0603_1608Metric")
    r3.ref = "R3"; vcc += r3[1]; sqw_int += r3[2]

@subcircuit
def battery_section(vbat, gnd):
    bat = Part("Device", "Battery_Cell",
               value="CR2032",
               footprint="Battery:BatteryHolder_LINX_BAT-HLD-012-SMT")
    bat.ref = "BT1"
    vbat += bat[1]; gnd += bat[2]

rtc_core(vcc, gnd, vbat, scl, sda, sqw_int, freq_32k)
battery_section(vbat, gnd)

j1 = Part("Connector", "Conn_01x06_Pin",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")
j1.ref = "J1"
j1.edge_preference = "bottom"
vcc += j1[1]; gnd += j1[2]
scl += j1[3]; sda += j1[4]
sqw_int += j1[5]; freq_32k += j1[6]

mh1 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
mh1.ref = "H1"
mh2 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
mh2.ref = "H2"
mh3 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
mh3.ref = "H3"
mh4 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
mh4.ref = "H4"

EDA_FLOORPLAN = {
    "outline": {"width_mm": 65, "height_mm": 47, "corner_radius_mm": 1},
    "fixed_positions": [
        {"ref": "H1", "x_mm": 4.0, "y_mm": 4.0, "rotation_deg": 0},
        {"ref": "H2", "x_mm": 61.0, "y_mm": 4.0, "rotation_deg": 0},
        {"ref": "H3", "x_mm": 4.0, "y_mm": 43.0, "rotation_deg": 0},
        {"ref": "H4", "x_mm": 61.0, "y_mm": 43.0, "rotation_deg": 0},
    ],
    "edge_anchors": [{"ref": "J1", "edge": "bottom"}],
}
