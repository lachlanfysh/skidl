#!/usr/bin/env python3
"""Generate PCB layout for MCP9808 temperature sensor breakout board."""

import os, sys
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

# Reset SKiDL state and rebuild circuit
import skidl
skidl.reset()

from skidl import *
set_default_tool(KICAD9)

# ── Power nets ────────────────────────────────────────────────
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# ── Signal nets ───────────────────────────────────────────────
sda   = Net("SDA")
scl   = Net("SCL")
alert = Net("ALERT")
a0    = Net("A0")
a1    = Net("A1")
a2    = Net("A2")


# ── Subcircuit: I2C pin header connector ─────────────────────
@subcircuit
def i2c_header(vcc, gnd, sda, scl, alert, a0, a1, a2):
    hdr = Part(
        "Connector", "Conn_01x08_Pin",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical",
    )
    hdr[1] += vcc
    hdr[2] += gnd
    hdr[3] += sda
    hdr[4] += scl
    hdr[5] += alert
    hdr[6] += a0
    hdr[7] += a1
    hdr[8] += a2


# ── Subcircuit: MCP9808 ───────────────────────────────────────
@subcircuit
def mcp9808_sensor(vcc, gnd, sda, scl, alert, a0, a1, a2):
    ic = Part(
        "Sensor_Temperature", "MCP9808_MSOP",
        footprint="Package_SO:MSOP-8_3x3mm_P0.65mm",
    )
    ic[1] += sda
    ic[2] += scl
    ic[3] += alert
    ic[4] += gnd
    ic[5] += a2
    ic[6] += a1
    ic[7] += a0
    ic[8] += vcc

    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dec[1] += vcc
    c_dec[2] += gnd

    r_sda = Part("Device", "R", value="10k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += vcc
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="10k",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl[1] += vcc
    r_scl[2] += scl

    r_a0 = Part("Device", "R", value="10k",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_a0[1] += a0
    r_a0[2] += gnd

    r_a1 = Part("Device", "R", value="10k",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_a1[1] += a1
    r_a1[2] += gnd

    r_a2 = Part("Device", "R", value="10k",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_a2[1] += a2
    r_a2[2] += gnd

    r_alert = Part("Device", "R", value="10k",
                   footprint="Resistor_SMD:R_0402_1005Metric")
    r_alert[1] += vcc
    r_alert[2] += alert


# ── Instantiate subcircuits ───────────────────────────────────
mcp9808_sensor(vcc, gnd, sda, scl, alert, a0, a1, a2)
i2c_header(vcc, gnd, sda, scl, alert, a0, a1, a2)

# ── PCB Layout ────────────────────────────────────────────────
from skidl.layout import (
    extract_groups,
    place_parts,
    write_kicad_pcb,
    validate,
    LayoutConstraints,
    BoardOutline,
    load_footprint_bboxes,
)

ckt = default_circuit
fp_names = {str(p.footprint) for p in ckt.parts if getattr(p, "footprint", None)}
fp_lib_dirs = ["/usr/share/kicad/footprints"]
fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)

# Small breakout board: 25mm x 20mm
constraints = LayoutConstraints(outline=BoardOutline(25.0, 20.0))
groups = extract_groups(ckt)
placed = place_parts(groups, constraints, fp_bboxes)

result = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(result.summary())

output_path = os.path.join(os.path.dirname(__file__), "board.kicad_pcb")
write_kicad_pcb(placed, ckt, fp_lib_dirs, output_path, outline=constraints.outline)
print(f"PCB written to {output_path}")
