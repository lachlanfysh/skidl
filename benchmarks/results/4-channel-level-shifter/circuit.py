"""
4-Channel BSS138 Level Shifter
Bidirectional voltage level shifter for 4 channels.
Converts between 3.3V and 5V logic levels using BSS138 N-channel MOSFETs.
Each channel has a BSS138 MOSFET with pull-up resistors on both sides.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# Power nets
vcc_lv = Net("+3V3")       # Low-voltage side (3.3V)
vcc_lv.drive = POWER

vcc_hv = Net("+5V")        # High-voltage side (5V)
vcc_hv.drive = POWER

gnd = Net("GND")
gnd.drive = POWER

@subcircuit
def level_shift_channel(lv_io, hv_io, vcc_lv, vcc_hv, gnd, ch_name="CH"):
    """
    Single BSS138 bidirectional level shifter channel.
    - Gate tied to low-voltage supply (3.3V)
    - Source on low-voltage side with pull-up to 3.3V
    - Drain on high-voltage side with pull-up to 5V
    """
    # BSS138 N-channel MOSFET
    q = Part("Transistor_FET", "BSS138",
             footprint="Package_TO_SOT_SMD:SOT-23",
             value="BSS138")

    # Pull-up resistor on low-voltage side (source side)
    r_lv = Part("Device", "R",
                footprint="Resistor_SMD:R_0402_1005Metric",
                value="10K")

    # Pull-up resistor on high-voltage side (drain side)
    r_hv = Part("Device", "R",
                footprint="Resistor_SMD:R_0402_1005Metric",
                value="10K")

    # Gate to low-voltage supply
    q["G"] += vcc_lv

    # Source side = low-voltage I/O
    q["S"] += lv_io
    r_lv[1] += vcc_lv
    r_lv[2] += lv_io

    # Drain side = high-voltage I/O
    q["D"] += hv_io
    r_hv[1] += vcc_hv
    r_hv[2] += hv_io


# Decoupling caps for both supply rails
c_lv = Part("Device", "C",
            footprint="Capacitor_SMD:C_0603_1608Metric",
            value="100nF")
c_lv[1] += vcc_lv
c_lv[2] += gnd

c_hv = Part("Device", "C",
            footprint="Capacitor_SMD:C_0603_1608Metric",
            value="100nF")
c_hv[1] += vcc_hv
c_hv[2] += gnd

# Low-voltage side header: GND, LV1, LV2, LV3, LV4, 3V3
lv_header = Part("Connector_Generic", "Conn_01x06",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
                 value="LV_HDR")

# High-voltage side header: GND, HV1, HV2, HV3, HV4, 5V
hv_header = Part("Connector_Generic", "Conn_01x06",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
                 value="HV_HDR")

# Connect header power pins
lv_header[1] += gnd
lv_header[6] += vcc_lv

hv_header[1] += gnd
hv_header[6] += vcc_hv

# Create 4 level shifter channels
for i in range(4):
    ch_num = i + 1
    lv_net = Net(f"LV{ch_num}")
    hv_net = Net(f"HV{ch_num}")

    level_shift_channel(lv_net, hv_net, vcc_lv, vcc_hv, gnd, ch_name=f"CH{ch_num}")

    # Connect to headers (pins 2-5 for channels 1-4)
    lv_header[ch_num + 1] += lv_net
    hv_header[ch_num + 1] += hv_net

generate_schematic(auto_stub=True)
