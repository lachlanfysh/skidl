"""
OpenLog data logger board (~25x18mm).
ATmega328P (TQFP-32) + microSD + 16MHz crystal + 3.3V MIC5205 LDO + FTDI header.
SPI bus for SD card; UART for logging input via FTDI header.
"""
import os
os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from skidl import *

set_default_tool(KICAD9)

# ── Nets ─────────────────────────────────────────────────────────────────────
VCC    = Net("VCC");    VCC.drive = POWER     # 5 V input (from FTDI / USB)
V33    = Net("3V3");    V33.drive = POWER     # 3.3 V regulated
GND    = Net("GND");    GND.drive = POWER

# SPI bus (MCU → SD card)
SPI_MOSI = Net("SPI_MOSI")
SPI_MISO = Net("SPI_MISO")
SPI_SCK  = Net("SPI_SCK")
SD_CS    = Net("SD_CS")

# UART (from FTDI header)
UART_RX  = Net("UART_RX")   # MCU RX ← FTDI TX
UART_TX  = Net("UART_TX")   # MCU TX → FTDI RX

# Crystal
XTAL1 = Net("XTAL1")
XTAL2 = Net("XTAL2")

# Misc
RESET      = Net("RESET")
SD_DET     = Net("SD_DET")
DTR_RESET  = Net("DTR_RESET")   # FTDI DTR → MCU RESET (auto-reset)


# ── Subcircuits ───────────────────────────────────────────────────────────────

@subcircuit
def power_block(vin, v33, gnd):
    """MIC5205-3.3 LDO + bulk + bypass caps."""
    global VCC, V33, GND
    reg = Part(
        "Regulator_Linear", "MIC5205-3.3YM5",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
    )
    reg["IN"]  += vin
    reg["OUT"] += v33
    reg["GND"] += gnd
    reg["EN"]  += vin     # always-on
    reg["BP"]  += Net("LDO_BP")

    # 100 nF bypass on BP pin
    c_bp = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_bp[1] += reg["BP"]; c_bp[2] += gnd

    # Input bulk cap 10 µF
    cin = Part("Device", "C_Polarized", value="10uF",
               footprint="Capacitor_SMD:CP_Elec_4x5.4")
    cin[1] += vin; cin[2] += gnd

    # Input decoupling
    cin2 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    cin2[1] += vin; cin2[2] += gnd

    # Output bulk cap
    cout = Part("Device", "C_Polarized", value="10uF",
                footprint="Capacitor_SMD:CP_Elec_4x5.4")
    cout[1] += v33; cout[2] += gnd

    # Output decoupling
    cout2 = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    cout2[1] += v33; cout2[2] += gnd


@subcircuit
def mcu_block(vcc, v33, gnd, xtal1, xtal2,
              spi_mosi, spi_miso, spi_sck, sd_cs,
              uart_rx, uart_tx, reset):
    """ATmega328P-A TQFP-32 with decoupling caps and reset pull-up."""
    global VCC, V33, GND
    mcu = Part(
        "MCU_Microchip_ATmega", "ATmega328P-A",
        footprint="Package_QFP:TQFP-32_7x7mm_P0.8mm",
    )
    # Power
    mcu["VCC"] += vcc       # pins 4, 6
    mcu["GND"] += gnd       # pins 3, 5, 21
    mcu["AVCC"] += vcc

    # Decoupling caps on each VCC pin
    for _ in range(2):
        cd = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
        cd[1] += vcc; cd[2] += gnd

    # AREF bypass
    c_aref = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_aref[1] += mcu["AREF"]; c_aref[2] += gnd

    # Crystal pins (PB6/PB7 = XTAL1/XTAL2)
    mcu["XTAL1/PB6"] += xtal1
    mcu["XTAL2/PB7"] += xtal2

    # SPI → SD card (PB2=SS/SD_CS, PB3=MOSI, PB4=MISO, PB5=SCK)
    mcu["PB2"] += sd_cs
    mcu["PB3"] += spi_mosi
    mcu["PB4"] += spi_miso
    mcu["PB5"] += spi_sck

    # UART (PD0=RX, PD1=TX)
    mcu["PD0"] += uart_rx
    mcu["PD1"] += uart_tx

    # RESET pin with pull-up resistor
    mcu["~{RESET}/PC6"] += reset
    r_rst = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_rst[1] += vcc; r_rst[2] += reset

    # Unused GPIO left unconnected (auto_stub will stub them)


@subcircuit
def crystal_block(gnd, xtal1, xtal2):
    """16 MHz crystal with load capacitors."""
    xtal = Part(
        "Device", "Crystal",
        value="16MHz",
        footprint="Crystal:Crystal_SMD_3225-4Pin_3.2x2.5mm",
    )
    xtal[1] += xtal1
    xtal[2] += xtal2

    # 22 pF load caps
    cl1 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cl1[1] += xtal1; cl1[2] += gnd

    cl2 = Part("Device", "C", value="22pF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cl2[1] += xtal2; cl2[2] += gnd


@subcircuit
def sd_card_block(v33, gnd, spi_mosi, spi_miso, spi_sck, sd_cs, sd_det):
    """Micro SD card slot (Hirose DM3D 11-pad) with pull-ups and level caution.
    ATmega328 runs at 3.3 V so SPI is directly compatible — no level shifter needed.
    """
    global V33, GND
    sd = Part(
        "Connector", "Micro_SD_Card_Det2",
        footprint="Connector_Card:microSD_HC_Hirose_DM3D-SF",
    )
    # SPI in SD mode: DAT0=MISO, DAT3/CD=CS, CMD=MOSI, CLK=SCK
    sd["DAT0"]    += spi_miso
    sd["DAT3/CD"] += sd_cs
    sd["CMD"]     += spi_mosi
    sd["CLK"]     += spi_sck
    sd["VDD"]     += v33
    sd["VSS"]     += gnd
    sd["SHIELD"]  += gnd

    # Card detect: DET_A/DET_B short when card inserted (normally open)
    sd["DET_A"] += sd_det
    sd["DET_B"] += gnd

    # Unused in SPI mode: DAT1, DAT2 — pulled high
    for pin_name in ["DAT1", "DAT2"]:
        r = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
        r[1] += v33; r[2] += sd[pin_name]

    # Pull-up on CD/CS line
    r_cs = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0402_1005Metric")
    r_cs[1] += v33; r_cs[2] += sd_cs

    # Card detect pull-up
    r_det = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_det[1] += v33; r_det[2] += sd_det

    # Decoupling on SD VDD
    c_sd = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_sd[1] += v33; c_sd[2] += gnd


@subcircuit
def ftdi_header_block(vcc, gnd, uart_rx, uart_tx, reset, dtr_reset):
    """FTDI-compatible 6-pin serial header (DTR/RX/TX/+5V/CTS/GND)."""
    hdr = Part(
        "Connector", "Conn_01x06_Pin",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical",
        value="FTDI_HEADER",
    )
    # Standard FTDI header pinout: 1=DTR, 2=RX-in, 3=TX-out, 4=+5V, 5=CTS, 6=GND
    hdr["Pin_1"] += dtr_reset    # DTR → auto-reset circuit
    hdr["Pin_2"] += uart_rx      # FTDI TX → MCU RX
    hdr["Pin_3"] += uart_tx      # FTDI RX ← MCU TX
    hdr["Pin_4"] += vcc          # 5 V power from FTDI
    hdr["Pin_5"] += Net("CTS")   # CTS (tied high via resistor below)
    hdr["Pin_6"] += gnd

    # DTR auto-reset: 100nF coupling cap to RESET
    c_dtr = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dtr[1] += dtr_reset; c_dtr[2] += reset

    # CTS pull-up to keep CTS asserted when FTDI absent
    r_cts = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cts[1] += vcc; r_cts[2] += hdr["Pin_5"]


@subcircuit
def status_led_block(v33, gnd, uart_tx):
    """Status LED driven from MCU TX via current-limiting resistor.
    Blinks during serial activity — classic OpenLog visual indicator.
    """
    led = Part("Device", "LED", value="STAT",
               footprint="LED_SMD:LED_0402_1005Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    # Anode → 3.3 V, cathode → resistor → MCU TX (active-low blink)
    led["A"]  += v33
    led["K"]  += r_led[1]
    r_led[2]  += uart_tx


@subcircuit
def reset_button_block(gnd, reset):
    """Tactile reset button."""
    sw = Part("Switch", "SW_Push",
              footprint="Button_Switch_SMD:SW_SPST_EVPBF")
    sw[1] += reset
    sw[2] += gnd


# ── Top-level instantiation ───────────────────────────────────────────────────
power_block(VCC, V33, GND)

mcu_block(
    vcc=V33, v33=V33, gnd=GND,
    xtal1=XTAL1, xtal2=XTAL2,
    spi_mosi=SPI_MOSI, spi_miso=SPI_MISO,
    spi_sck=SPI_SCK, sd_cs=SD_CS,
    uart_rx=UART_RX, uart_tx=UART_TX,
    reset=RESET,
)

crystal_block(GND, XTAL1, XTAL2)

sd_card_block(V33, GND, SPI_MOSI, SPI_MISO, SPI_SCK, SD_CS, SD_DET)

ftdi_header_block(VCC, GND, UART_RX, UART_TX, RESET, DTR_RESET)

status_led_block(V33, GND, UART_TX)

reset_button_block(GND, RESET)

# ── Floorplan / mechanical intent ─────────────────────────────────────────────
# 25 x 18 mm board.  FTDI header on left edge, SD slot on right edge.
# MCU (TQFP) centre-top, reset button bottom-right.
EDA_FLOORPLAN = {
    "outline_mm": [25.0, 18.0],
    "edge_anchors": [
        # FTDI 6-pin header on left edge, centred vertically
        {"ref": "J1", "edge": "left", "pos_mm": [0, 9]},
        # SD card slot on right edge
        {"ref": "J2", "edge": "right", "pos_mm": [25, 9]},
    ],
    "fixed_positions": [
        # ATmega328P centred in board
        {"ref": "U2", "pos_mm": [12.5, 7.0]},
        # LDO close to FTDI header (power entry)
        {"ref": "U1", "pos_mm": [3.5, 14.0]},
        # Reset button bottom-centre
        {"ref": "SW1", "pos_mm": [12.5, 15.5]},
    ],
}

# ── Generate ──────────────────────────────────────────────────────────────────
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
