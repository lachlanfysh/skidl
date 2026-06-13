#!/usr/bin/env python3
"""INA219 high-side current/power monitor breakout board.

TI INA219: 12-bit I2C current/voltage/power monitor, 0-26V high-side.
Breakout with:
  - Screw terminals for VIN+/VIN- (high-side sense)
  - 0.1 ohm sense resistor between VIN+ and VIN-
  - I2C header (GND, VCC, SDA, SCL)
  - Address config jumpers/pads (A0, A1)
  - 100nF decoupling cap on VS (VCC)
"""

import os, sys
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ── Power nets ────────────────────────────────────────────────
vcc = Net("VCC")
vcc.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# ── High-side sense nets ──────────────────────────────────────
vin_plus  = Net("VIN_PLUS")
vin_minus = Net("VIN_MINUS")

# ── I2C bus nets ──────────────────────────────────────────────
sda = Net("SDA")
scl = Net("SCL")

# ── Address config nets ───────────────────────────────────────
a0_net = Net("ADDR0")
a1_net = Net("ADDR1")


@subcircuit
def sense_input(vin_p, vin_m, gnd_net):
    """Screw terminals and sense resistor for high-side current measurement."""
    # Two individual 2-pin screw terminals: one for VIN+, one for VIN-
    # (easier to label on a breakout than a single 2-pos terminal)
    t_pos = Part(
        "Connector", "Screw_Terminal_01x02",
        footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
        value="VIN+/GND_REF",
    )
    # Pin 1 = high-side supply input, Pin 2 = board GND reference (screw terminal ground)
    t_pos[1] += vin_p
    t_pos[2] += gnd_net

    t_neg = Part(
        "Connector", "Screw_Terminal_01x02",
        footprint="TerminalBlock_Phoenix:TerminalBlock_Phoenix_MKDS-1,5-2-5.08_1x02_P5.08mm_Horizontal",
        value="VIN-/GND_REF",
    )
    t_neg[1] += vin_m
    t_neg[2] += gnd_net

    # 0.1 ohm current sense resistor (2512 SMD for power capability)
    r_shunt = Part(
        "Device", "R",
        value="0.1",
        footprint="Resistor_SMD:R_2512_6332Metric",
    )
    r_shunt[1] += vin_p
    r_shunt[2] += vin_m


@subcircuit
def ina219_core(vin_p, vin_m, vs, gnd_net, sda_net, scl_net, a0_n, a1_n):
    """INA219 IC with decoupling capacitor."""
    ic = Part(
        "Sensor_Energy", "INA219AxD",
        footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
    )
    ic[8] += vin_p    # IN+
    ic[7] += vin_m    # IN-
    ic[5] += vs       # VS (supply)
    ic[6] += gnd_net  # GND
    ic[3] += sda_net  # SDA
    ic[4] += scl_net  # SCL
    ic[2] += a0_n     # A0
    ic[1] += a1_n     # A1

    # 100nF decoupling cap on VS — placed automatically near IC by layout engine
    c_bypass = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_bypass[1] += vs
    c_bypass[2] += gnd_net

    # 10uF bulk cap for stability under transients
    c_bulk = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_bulk[1] += vs
    c_bulk[2] += gnd_net


@subcircuit
def i2c_connector(vs, gnd_net, sda_net, scl_net):
    """4-pin I2C header: GND, VCC, SDA, SCL (standard breakout pinout)."""
    hdr = Part(
        "Connector_Generic", "Conn_01x04",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
        value="I2C",
    )
    hdr[1] += gnd_net  # GND
    hdr[2] += vs       # VCC
    hdr[3] += sda_net  # SDA
    hdr[4] += scl_net  # SCL

    # I2C pull-up resistors (4.7k, standard for 100kHz/400kHz)
    r_sda = Part(
        "Device", "R",
        value="4.7k",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sda[1] += vs
    r_sda[2] += sda_net

    r_scl = Part(
        "Device", "R",
        value="4.7k",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_scl[1] += vs
    r_scl[2] += scl_net


@subcircuit
def address_config(a0_n, a1_n, gnd_net):
    """Address configuration: A0 and A1 pulled low via 10k resistors.

    Pad/jumper to select I2C address:
      A0=0, A1=0 → 0x40 (default)
      A0=1, A1=0 → 0x41
      A0=0, A1=1 → 0x44
      A0=1, A1=1 → 0x45
    Solder bridge pads allow tying A0/A1 to VCC for address selection.
    """
    r_a0 = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_a0[1] += a0_n
    r_a0[2] += gnd_net

    r_a1 = Part(
        "Device", "R",
        value="10k",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_a1[1] += a1_n
    r_a1[2] += gnd_net


# ── Instantiate all subcircuits ────────────────────────────────
sense_input(vin_plus, vin_minus, gnd)
ina219_core(vin_plus, vin_minus, vcc, gnd, sda, scl, a0_net, a1_net)
i2c_connector(vcc, gnd, sda, scl)
address_config(a0_net, a1_net, gnd)

# ── Generate schematic ─────────────────────────────────────────
generate_schematic(
    auto_stub=True,
    auto_stub_fanout=3,
    erc_max_iterations=8,
)
