"""
Si5351A Clock Generator Breakout Board
- Si5351A I2C clock generator with 25MHz crystal reference
- Three independent clock outputs (CLK0, CLK1, CLK2)
- 3.3V LDO regulator for 3-5V input power
- I2C level shifting for 3V/5V logic compatibility
- Optional SMA connector for RF output on CLK0
- Breakout header for all signals
"""
import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)


def _init_skidl_pins(part):
    """Set default schematic attributes on SKIDL-defined part pins and
    synthesize draw_cmds so the schematic generator can compute bounding boxes.

    Library-loaded parts get orientation/x/y/length/rotation from the .kicad_sym
    file and draw_cmds from the symbol definition. Part(tool=SKIDL) pins lack
    these, causing NaN in the placement engine.
    """
    spacing_mm = 2.54
    pin_length_mm = 2.54
    n = len(part.pins)

    left_count = (n + 1) // 2
    right_count = n - left_count

    body_h = max(left_count, right_count, 1) * spacing_mm
    body_w = max(5.08, body_h * 0.6)

    draw_cmds = []
    for idx, pin in enumerate(part.pins):
        if idx < left_count:
            row = idx
            pin.x = -(body_w / 2 + pin_length_mm)
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "R"
            pin.rotation = 0
        else:
            row = idx - left_count
            pin.x = body_w / 2 + pin_length_mm
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "L"
            pin.rotation = 180

        pin.length = pin_length_mm

        pin_cmd = [
            "pin", pin.func if isinstance(pin.func, str) else "passive", "line",
            ["at", pin.x, pin.y, int(pin.rotation)],
            ["length", pin_length_mm],
            ["name", pin.name,
                ["effects", ["font", ["size", 1.27, 1.27]]]],
            ["number", str(pin.num),
                ["effects", ["font", ["size", 1.27, 1.27]]]],
        ]
        draw_cmds.append(pin_cmd)

    rect_cmd = [
        "rectangle",
        ["start", -body_w / 2, -body_h / 2 - spacing_mm / 2],
        ["end", body_w / 2, body_h / 2 + spacing_mm / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]
    draw_cmds.append(rect_cmd)

    part.draw_cmds = {1: draw_cmds, 0: draw_cmds}

    if not hasattr(part, "lib") or part.lib is None:
        class _MockLib:
            def __init__(self, name):
                self.filename = name
        part.lib = _MockLib("skidl_custom")


# ============================================================
# Power supply: 3.3V LDO from VIN (3-5V)
# ============================================================
@subcircuit
def power_supply(vin, v3v3, gnd):
    """3.3V LDO regulator with input/output caps."""
    # AP2112K-3.3 style LDO, SOT-23-5
    ldo = Part(name="AP2112K-3.3", tool=SKIDL, dest=NETLIST,
               footprint="Package_TO_SOT_SMD:SOT-23-5",
               pins=[
                   Pin(num="1", name="VIN", func=Pin.types.PWRIN),
                   Pin(num="2", name="GND", func=Pin.types.PWRIN),
                   Pin(num="3", name="EN", func=Pin.types.INPUT),
                   Pin(num="4", name="NC", func=Pin.types.NOCONNECT),
                   Pin(num="5", name="VOUT", func=Pin.types.PWROUT),
               ])
    _init_skidl_pins(ldo)
    ldo["VIN"] += vin
    ldo["GND"] += gnd
    ldo["EN"] += vin  # Always enabled
    # Pin 4 is NC - already set as NOCONNECT type
    ldo["VOUT"] += v3v3

    # Input bulk cap 10uF
    cin = Part("Device", "C", value="10uF",
               footprint="Capacitor_SMD:C_0805_2012Metric")
    cin[1] += vin
    cin[2] += gnd

    # Output bulk cap 10uF
    cout = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    cout[1] += v3v3
    cout[2] += gnd

    # Output decoupling 100nF
    cdec = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cdec[1] += v3v3
    cdec[2] += gnd


# ============================================================
# Si5351A clock generator with 25MHz crystal
# ============================================================
@subcircuit
def clock_generator(v3v3, gnd, sda_3v, scl_3v, clk0, clk1, clk2):
    """Si5351A clock generator IC with 25MHz crystal reference."""
    # Si5351A MSOP-10 pinout:
    # 1=SDA, 2=SCL, 3=VDD, 4=VSS, 5=CLK0, 6=CLK1, 7=CLK2, 8=VDDO, 9=XB, 10=XA
    si5351 = Part(name="Si5351A", tool=SKIDL, dest=NETLIST,
                  footprint="Package_SO:MSOP-10_3x3mm_P0.5mm",
                  pins=[
                      Pin(num="1", name="SDA", func=Pin.types.BIDIR),
                      Pin(num="2", name="SCL", func=Pin.types.INPUT),
                      Pin(num="3", name="VDD", func=Pin.types.PWRIN),
                      Pin(num="4", name="VSS", func=Pin.types.PWRIN),
                      Pin(num="5", name="CLK0", func=Pin.types.OUTPUT),
                      Pin(num="6", name="CLK1", func=Pin.types.OUTPUT),
                      Pin(num="7", name="CLK2", func=Pin.types.OUTPUT),
                      Pin(num="8", name="VDDO", func=Pin.types.PWRIN),
                      Pin(num="9", name="XB", func=Pin.types.PASSIVE),
                      Pin(num="10", name="XA", func=Pin.types.PASSIVE),
                  ])
    _init_skidl_pins(si5351)
    si5351["VDD"] += v3v3
    si5351["VSS"] += gnd
    si5351["VDDO"] += v3v3
    si5351["SDA"] += sda_3v
    si5351["SCL"] += scl_3v
    si5351["CLK0"] += clk0
    si5351["CLK1"] += clk1
    si5351["CLK2"] += clk2

    # VDD decoupling cap
    cvdd = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cvdd[1] += v3v3
    cvdd[2] += gnd

    # VDDO decoupling cap
    cvddo = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    cvddo[1] += v3v3
    cvddo[2] += gnd

    # 25MHz crystal between XA and XB
    xtal = Part("Device", "Crystal", value="25MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += si5351["XA"]
    xtal[2] += si5351["XB"]

    # Crystal load caps (10pF each)
    cxa = Part("Device", "C", value="10pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cxa[1] += si5351["XA"]
    cxa[2] += gnd

    cxb = Part("Device", "C", value="10pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cxb[1] += si5351["XB"]
    cxb[2] += gnd


# ============================================================
# I2C level shifter (BSS138 MOSFET-based bidirectional)
# ============================================================
@subcircuit
def i2c_level_shifter(v3v3, vio, gnd, sda_3v, scl_3v, sda_io, scl_io):
    """BSS138-based bidirectional I2C level shifter for SDA and SCL."""
    # SDA channel: BSS138 N-MOSFET level shifter
    # Gate = 3.3V (low-voltage rail)
    # Source = SDA_3V (low-voltage side, with pullup to 3.3V)
    # Drain = SDA_IO (high-voltage side, with pullup to VIO)
    q_sda = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_sda["G"] += v3v3
    q_sda["S"] += sda_3v
    q_sda["D"] += sda_io

    # SDA pullup on 3.3V side
    r_sda_lo = Part("Device", "R", value="10K",
                    footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda_lo[1] += v3v3
    r_sda_lo[2] += sda_3v

    # SDA pullup on VIO side
    r_sda_hi = Part("Device", "R", value="10K",
                    footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda_hi[1] += vio
    r_sda_hi[2] += sda_io

    # SCL channel: BSS138 N-MOSFET level shifter
    q_scl = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_scl["G"] += v3v3
    q_scl["S"] += scl_3v
    q_scl["D"] += scl_io

    # SCL pullup on 3.3V side
    r_scl_lo = Part("Device", "R", value="10K",
                    footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl_lo[1] += v3v3
    r_scl_lo[2] += scl_3v

    # SCL pullup on VIO side
    r_scl_hi = Part("Device", "R", value="10K",
                    footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl_hi[1] += vio
    r_scl_hi[2] += scl_io


# ============================================================
# Clock output section: headers + optional SMA
# ============================================================
@subcircuit
def clock_outputs(clk0, clk1, clk2, gnd):
    """Clock output headers and optional SMA connector."""
    # Individual 1x2 headers for each clock output (signal + GND)
    hdr_clk0 = Part("Connector_Generic", "Conn_01x02",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    hdr_clk0[1] += clk0
    hdr_clk0[2] += gnd

    hdr_clk1 = Part("Connector_Generic", "Conn_01x02",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    hdr_clk1[1] += clk1
    hdr_clk1[2] += gnd

    hdr_clk2 = Part("Connector_Generic", "Conn_01x02",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    hdr_clk2[1] += clk2
    hdr_clk2[2] += gnd

    # Optional SMA connector on CLK0 for RF work
    sma = Part("Connector_Generic", "Conn_01x01",
               footprint="Connector_Coaxial:SMA_Amphenol_132134-11_Vertical")
    sma[1] += clk0


# ============================================================
# Breakout header for power and I2C
# ============================================================
@subcircuit
def breakout_header(vin, gnd, sda_io, scl_io, vio):
    """Main breakout pin header for power and I2C signals."""
    # 1x7 header: VIN, GND, SCL, SDA, VIO, GND, EN
    hdr = Part("Connector_Generic", "Conn_01x07",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x07_P2.54mm_Vertical")
    hdr[1] += vin
    hdr[2] += gnd
    hdr[3] += scl_io
    hdr[4] += sda_io
    hdr[5] += vio
    hdr[6] += gnd
    hdr[7] += vin   # OE/EN tied to VIN (always on)


# ============================================================
# Top-level circuit
# ============================================================
# Power nets
vin = Net("VIN"); vin.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
vio = Net("VIO"); vio.drive = POWER

# I2C nets
sda_3v = Net("SDA_3V")
scl_3v = Net("SCL_3V")
sda_io = Net("SDA")
scl_io = Net("SCL")

# Clock output nets
clk0 = Net("CLK0")
clk1 = Net("CLK1")
clk2 = Net("CLK2")

# Instantiate all blocks
power_supply(vin, v3v3, gnd)
clock_generator(v3v3, gnd, sda_3v, scl_3v, clk0, clk1, clk2)
i2c_level_shifter(v3v3, vio, gnd, sda_3v, scl_3v, sda_io, scl_io)
clock_outputs(clk0, clk1, clk2, gnd)
breakout_header(vin, gnd, sda_io, scl_io, vio)

generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
