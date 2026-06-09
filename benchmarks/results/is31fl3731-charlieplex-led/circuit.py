"""
IS31FL3731 16x9 Charlieplex LED Matrix Driver Board

Features:
- IS31FL3731 PWM LED driver for 16x9 LED matrices
- I2C interface with address selection
- 8 frames of display memory for animations
- STEMMA QT (JST SH 4-pin) connectors for chainable I2C
- Supports stacking up to 4 drivers (address select via AD pin)
- R_EXT for LED current setting
- C_FILT for internal charge pump filter
- Decoupling caps for power stability
"""

import os

os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *

set_default_tool(KICAD9)

# ============================================================
# Power nets
# ============================================================
vcc = Net("VCC")
vcc.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# I2C bus
sda = Net("SDA")
scl = Net("SCL")

# ============================================================
# Subcircuit: IS31FL3731 LED driver with support components
# ============================================================
@subcircuit
def led_driver(vcc, gnd, sda, scl):
    """IS31FL3731 LED matrix driver with external components."""

    # IS31FL3731-QF LED matrix driver
    u1 = Part(
        "Driver_LED",
        "IS31FL3731-QF",
        footprint="Package_DFN_QFN:QFN-28-1EP_4x4mm_P0.4mm_EP2.3x2.3mm",
    )

    # Power connections
    u1["VCC"] += vcc
    u1["GND"] += gnd

    # I2C connections
    u1["SDA"] += sda
    u1["SCL"] += scl

    # Address select pin - connect to GND for default address 0x74
    u1["AD"] += gnd

    # Shutdown pin - pull high via resistor to enable the chip
    sdb_net = Net("SDB")
    r_sdb = Part(
        "Device",
        "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sdb[1] += vcc
    r_sdb[2] += sdb_net
    u1["~{SDB}"] += sdb_net

    # Interrupt pin - active low open collector, leave with pullup
    intb_net = Net("INTB")
    r_int = Part(
        "Device",
        "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_int[1] += vcc
    r_int[2] += intb_net
    u1["~{INTB}"] += intb_net

    # Audio input pin - tie to GND when not used
    u1["IN"] += gnd

    # C_FILT - charge pump filter capacitor (1uF recommended)
    c_filt = Part(
        "Device",
        "C",
        value="1uF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_filt[1] += u1["C_FILT"]
    c_filt[2] += gnd

    # R_EXT - external resistor to set LED current (20K for ~20mA per LED)
    r_ext = Part(
        "Device",
        "R",
        value="20K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_ext[1] += u1["R_EXT"]
    r_ext[2] += gnd

    # Decoupling capacitor for VCC
    c_dec = Part(
        "Device",
        "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_dec[1] += vcc
    c_dec[2] += gnd

    # Bulk capacitor for VCC
    c_bulk = Part(
        "Device",
        "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_bulk[1] += vcc
    c_bulk[2] += gnd

    # ---- LED Matrix Outputs (directly to header) ----
    # CA1-CA9 and CB1-CB9 are the charlieplex matrix lines
    # They go to the LED matrix connector/header

    # CA outputs header (9 pins: CA1-CA9)
    ca_header = Part(
        "Connector_Generic",
        "Conn_01x09",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Vertical",
    )
    for i in range(1, 10):
        ca_header[i] += u1[f"CA{i}"]

    # CB outputs header (9 pins: CB1-CB9)
    cb_header = Part(
        "Connector_Generic",
        "Conn_01x09",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x09_P2.54mm_Vertical",
    )
    for i in range(1, 10):
        cb_header[i] += u1[f"CB{i}"]


# ============================================================
# Subcircuit: I2C interface with pullups and STEMMA QT connectors
# ============================================================
@subcircuit
def i2c_interface(vcc, gnd, sda, scl):
    """I2C bus with pull-up resistors and two STEMMA QT connectors for daisy-chaining."""

    # I2C pull-up resistors
    r_sda = Part(
        "Device",
        "R",
        value="4.7K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sda[1] += vcc
    r_sda[2] += sda

    r_scl = Part(
        "Device",
        "R",
        value="4.7K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_scl[1] += vcc
    r_scl[2] += scl

    # STEMMA QT connector 1 (input) - JST SH 4-pin
    # Pin order: GND, VCC, SDA, SCL
    j_qt1 = Part(
        "Connector_Generic",
        "Conn_01x04",
        footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
    )
    j_qt1[1] += gnd
    j_qt1[2] += vcc
    j_qt1[3] += sda
    j_qt1[4] += scl

    # STEMMA QT connector 2 (output for daisy-chaining)
    j_qt2 = Part(
        "Connector_Generic",
        "Conn_01x04",
        footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal",
    )
    j_qt2[1] += gnd
    j_qt2[2] += vcc
    j_qt2[3] += sda
    j_qt2[4] += scl

    # Decoupling cap near connectors
    c_con = Part(
        "Device",
        "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_con[1] += vcc
    c_con[2] += gnd


# ============================================================
# Instantiate subcircuits
# ============================================================
led_driver(vcc, gnd, sda, scl)
i2c_interface(vcc, gnd, sda, scl)

# ============================================================
# Generate schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3)
