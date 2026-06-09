"""
VS1053 Codec (MP3/WAV/MIDI/Ogg) Breakout
==========================================
VLSI VS1053B DSP codec chip breakout board. Decodes MP3, AAC, Ogg Vorbis, WMA,
MIDI, FLAC, WAV (PCM/ADPCM). Records audio in PCM (WAV) and compressed Ogg Vorbis.
Adjustable bass, treble, volume. 8 GPIO pins for LEDs/buttons. SPI interface for
microcontroller audio playback from SD card. MIDI mode on UART (31250 baud) for
synth/drum machine with built-in instruments.

Architecture:
- VS1053B LQFP-48 codec IC with 12.288MHz crystal
- 1.8V core LDO (on-chip, external caps) + 3.3V I/O supply
- 3.5mm stereo headphone output jack with coupling caps
- Microphone input with bias resistor
- MicroSD card slot for audio file storage
- SPI control/data interface header
- 8 GPIO breakout header
- MIDI/UART input header
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
    spacing_mm = 2.54  # KiCad standard grid
    pin_length_mm = 2.54
    n = len(part.pins)

    # Split pins: left half on the left side, right half on the right side.
    left_count = (n + 1) // 2
    right_count = n - left_count

    # Body dimensions: height accommodates the larger side, width is fixed.
    body_h = max(left_count, right_count, 1) * spacing_mm
    body_w = max(5.08, body_h * 0.6)  # Min width 5.08mm

    draw_cmds = []
    for idx, pin in enumerate(part.pins):
        if idx < left_count:
            # Left-side pin: points left (orientation "R" means pin stub extends right
            # from the body, i.e., connection point is to the left).
            row = idx
            pin.x = -(body_w / 2 + pin_length_mm)
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "R"
            pin.rotation = 0
        else:
            # Right-side pin: points right.
            row = idx - left_count
            pin.x = body_w / 2 + pin_length_mm
            pin.y = body_h / 2 - row * spacing_mm
            pin.orientation = "L"
            pin.rotation = 180

        pin.length = pin_length_mm

        # Synthesize a pin draw_cmd in KiCad s-expression format.
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

    # Add a rectangle for the body.
    rect_cmd = [
        "rectangle",
        ["start", -body_w / 2, -body_h / 2 - spacing_mm / 2],
        ["end", body_w / 2, body_h / 2 + spacing_mm / 2],
        ["stroke", ["width", 0.254], ["type", "default"]],
        ["fill", ["type", "none"]],
    ]
    draw_cmds.append(rect_cmd)

    # Store draw_cmds on the part (unit 1 and unit 0).
    part.draw_cmds = {1: draw_cmds, 0: draw_cmds}

    # Give the part a mock lib so the schematic writer can form a lib_id.
    if not hasattr(part, "lib") or part.lib is None:
        class _MockLib:
            def __init__(self, name):
                self.filename = name
        part.lib = _MockLib("skidl_custom")


# ===========================================================================
# Global power nets
# ===========================================================================
vcc = Net("VCC")
vcc.drive = POWER

vcc_1v8 = Net("+1V8")
vcc_1v8.drive = POWER

gnd = Net("GND")
gnd.drive = POWER

# SPI control nets
spi_sck = Net("SCK")
spi_mosi = Net("MOSI")
spi_miso = Net("MISO")
sci_cs = Net("SCI_CS")     # Command chip select (active low)
sdi_cs = Net("SDI_CS")     # Data chip select (active low)
dreq = Net("DREQ")         # Data request (active high when ready)
reset_n = Net("RESET_N")   # Hardware reset (active low)

# SD card SPI nets
sd_cs = Net("SD_CS")
sd_sck = Net("SD_SCK")
sd_mosi = Net("SD_MOSI")
sd_miso = Net("SD_MISO")

# Audio output nets
line_l = Net("LINE_L")
line_r = Net("LINE_R")
hp_l = Net("HP_L")
hp_r = Net("HP_R")

# Microphone input nets
mic_p = Net("MIC_P")
mic_n = Net("MIC_N")

# GPIO nets
gpio = [Net(f"GPIO{i}") for i in range(8)]

# MIDI/UART net
midi_rx = Net("MIDI_RX")

# Crystal nets
xtal_in = Net("XTAL_IN")
xtal_out = Net("XTAL_OUT")


# ===========================================================================
# Subcircuit: VS1053B codec IC (LQFP-48)
# ===========================================================================
@subcircuit
def vs1053b_codec(vcc, vcc_1v8, gnd, spi_sck, spi_mosi, spi_miso,
                  sci_cs, sdi_cs, dreq, reset_n, line_l, line_r,
                  mic_p, mic_n, gpio_nets, midi_rx, xtal_in, xtal_out):
    """
    VS1053B DSP codec. LQFP-48 package.
    Pin assignments from VLSI VS1053B datasheet.
    """
    ic = Part(
        name="VS1053B",
        tool=SKIDL,
        dest=NETLIST,
        footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm",
        pins=[
            # Power pins
            Pin(num="1", name="MICP", func=Pin.types.INPUT),
            Pin(num="2", name="MICN", func=Pin.types.INPUT),
            Pin(num="3", name="XTAL_IN", func=Pin.types.INPUT),
            Pin(num="4", name="XTAL_OUT", func=Pin.types.OUTPUT),
            Pin(num="5", name="CVDD1", func=Pin.types.PWRIN),
            Pin(num="6", name="IOVDD1", func=Pin.types.PWRIN),
            Pin(num="7", name="VCC_INTERNAL1", func=Pin.types.PASSIVE),
            Pin(num="8", name="DREQ", func=Pin.types.OUTPUT),
            Pin(num="9", name="GPIO0", func=Pin.types.BIDIR),
            Pin(num="10", name="GPIO1", func=Pin.types.BIDIR),
            Pin(num="11", name="GPIO2", func=Pin.types.BIDIR),
            Pin(num="12", name="GPIO3", func=Pin.types.BIDIR),
            Pin(num="13", name="AVDD1", func=Pin.types.PWRIN),
            Pin(num="14", name="RIGHT", func=Pin.types.OUTPUT),
            Pin(num="15", name="AGND1", func=Pin.types.PWRIN),
            Pin(num="16", name="LEFT", func=Pin.types.OUTPUT),
            Pin(num="17", name="AGND2", func=Pin.types.PWRIN),
            Pin(num="18", name="GBUF", func=Pin.types.OUTPUT),
            Pin(num="19", name="AVDD2", func=Pin.types.PWRIN),
            Pin(num="20", name="RCAP", func=Pin.types.PASSIVE),
            Pin(num="21", name="CVDD2", func=Pin.types.PWRIN),
            Pin(num="22", name="IOVDD2", func=Pin.types.PWRIN),
            Pin(num="23", name="RX", func=Pin.types.INPUT),
            Pin(num="24", name="TX", func=Pin.types.OUTPUT),
            Pin(num="25", name="SCLK", func=Pin.types.INPUT),
            Pin(num="26", name="SI", func=Pin.types.INPUT),
            Pin(num="27", name="SO", func=Pin.types.OUTPUT),
            Pin(num="28", name="XCS", func=Pin.types.INPUT),
            Pin(num="29", name="IOVDD3", func=Pin.types.PWRIN),
            Pin(num="30", name="XRESET", func=Pin.types.INPUT),
            Pin(num="31", name="DGND1", func=Pin.types.PWRIN),
            Pin(num="32", name="CVDD3", func=Pin.types.PWRIN),
            Pin(num="33", name="TEST", func=Pin.types.INPUT),
            Pin(num="34", name="GPIO4", func=Pin.types.BIDIR),
            Pin(num="35", name="GPIO5", func=Pin.types.BIDIR),
            Pin(num="36", name="GPIO6", func=Pin.types.BIDIR),
            Pin(num="37", name="GPIO7", func=Pin.types.BIDIR),
            Pin(num="38", name="DGND2", func=Pin.types.PWRIN),
            Pin(num="39", name="IOVDD4", func=Pin.types.PWRIN),
            Pin(num="40", name="XDCS", func=Pin.types.INPUT),
            Pin(num="41", name="DGND3", func=Pin.types.PWRIN),
            Pin(num="42", name="CVDD4", func=Pin.types.PWRIN),
            Pin(num="43", name="VCC_INTERNAL2", func=Pin.types.PASSIVE),
            Pin(num="44", name="GPIO_CTRL", func=Pin.types.INPUT),
            Pin(num="45", name="IOVDD5", func=Pin.types.PWRIN),
            Pin(num="46", name="LINE_AGND", func=Pin.types.PWRIN),
            Pin(num="47", name="LINEL", func=Pin.types.OUTPUT),
            Pin(num="48", name="LINER", func=Pin.types.OUTPUT),
        ],
    )
    _init_skidl_pins(ic)

    # Power connections
    ic["IOVDD1"] += vcc
    ic["IOVDD2"] += vcc
    ic["IOVDD3"] += vcc
    ic["IOVDD4"] += vcc
    ic["IOVDD5"] += vcc
    ic["AVDD1"] += vcc
    ic["AVDD2"] += vcc

    ic["CVDD1"] += vcc_1v8
    ic["CVDD2"] += vcc_1v8
    ic["CVDD3"] += vcc_1v8
    ic["CVDD4"] += vcc_1v8

    ic["DGND1"] += gnd
    ic["DGND2"] += gnd
    ic["DGND3"] += gnd
    ic["AGND1"] += gnd
    ic["AGND2"] += gnd
    ic["LINE_AGND"] += gnd

    # SPI control interface
    ic["SCLK"] += spi_sck
    ic["SI"] += spi_mosi
    ic["SO"] += spi_miso
    ic["XCS"] += sci_cs
    ic["XDCS"] += sdi_cs
    ic["DREQ"] += dreq
    ic["XRESET"] += reset_n

    # Audio outputs (headphone driver)
    ic["LEFT"] += line_l
    ic["RIGHT"] += line_r
    ic["LINEL"] += Net("LINE_OUT_L")  # Line-level outputs
    ic["LINER"] += Net("LINE_OUT_R")

    # GBUF is a 1.65V reference for headphone ground
    gbuf = Net("GBUF")
    ic["GBUF"] += gbuf

    # Microphone inputs
    ic["MICP"] += mic_p
    ic["MICN"] += mic_n

    # GPIO pins
    ic["GPIO0"] += gpio_nets[0]
    ic["GPIO1"] += gpio_nets[1]
    ic["GPIO2"] += gpio_nets[2]
    ic["GPIO3"] += gpio_nets[3]
    ic["GPIO4"] += gpio_nets[4]
    ic["GPIO5"] += gpio_nets[5]
    ic["GPIO6"] += gpio_nets[6]
    ic["GPIO7"] += gpio_nets[7]

    # MIDI/UART
    ic["RX"] += midi_rx
    ic["TX"] += Net("UART_TX")

    # Crystal
    ic["XTAL_IN"] += xtal_in
    ic["XTAL_OUT"] += xtal_out

    # TEST pin tied low
    ic["TEST"] += gnd

    # GPIO_CTRL tied low for normal operation
    ic["GPIO_CTRL"] += gnd

    # Internal regulator bypass — 1.8V output (VCC_INTERNAL pins)
    # These output 1.8V from the internal regulator, need external caps
    ic["VCC_INTERNAL1"] += vcc_1v8
    ic["VCC_INTERNAL2"] += vcc_1v8

    # RCAP — reference capacitor for internal ADC
    rcap = Net("RCAP")
    ic["RCAP"] += rcap
    c_rcap = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_rcap[1] += rcap
    c_rcap[2] += gnd

    # GBUF decoupling cap
    c_gbuf = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_gbuf[1] += gbuf
    c_gbuf[2] += gnd

    # IOVDD decoupling caps (one per pair)
    for i in range(3):
        c_io = Part(
            "Device", "C",
            value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric",
        )
        c_io[1] += vcc
        c_io[2] += gnd

    # CVDD (1.8V core) decoupling
    for i in range(2):
        c_cv = Part(
            "Device", "C",
            value="100nF",
            footprint="Capacitor_SMD:C_0603_1608Metric",
        )
        c_cv[1] += vcc_1v8
        c_cv[2] += gnd

    # AVDD decoupling
    c_avdd = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_avdd[1] += vcc
    c_avdd[2] += gnd

    # Bulk capacitors
    c_bulk_io = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_bulk_io[1] += vcc
    c_bulk_io[2] += gnd

    c_bulk_core = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_bulk_core[1] += vcc_1v8
    c_bulk_core[2] += gnd


# ===========================================================================
# Subcircuit: 12.288MHz crystal oscillator
# ===========================================================================
@subcircuit
def crystal_osc(xtal_in, xtal_out, gnd):
    """12.288 MHz crystal with load capacitors for VS1053B."""
    xtal = Part(
        "Device",
        "Crystal",
        value="12.288MHz",
        footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm",
    )
    xtal[1] += xtal_in
    xtal[2] += xtal_out

    # Load capacitors (15pF typical for 12.288MHz crystal)
    c_x1 = Part(
        "Device", "C",
        value="15pF",
        footprint="Capacitor_SMD:C_0402_1005Metric",
    )
    c_x1[1] += xtal_in
    c_x1[2] += gnd

    c_x2 = Part(
        "Device", "C",
        value="15pF",
        footprint="Capacitor_SMD:C_0402_1005Metric",
    )
    c_x2[1] += xtal_out
    c_x2[2] += gnd


# ===========================================================================
# Subcircuit: Audio output stage (headphone + line out)
# ===========================================================================
@subcircuit
def audio_output(line_l, line_r, gnd):
    """
    Headphone output with DC blocking caps and 3.5mm stereo jack.
    VS1053B LEFT/RIGHT outputs are referenced to GBUF (1.65V), so
    AC-couple before the jack.
    """
    # DC blocking capacitors for headphone outputs
    c_hp_l = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_hp_l[1] += line_l
    c_hp_l[2] += hp_l

    c_hp_r = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_hp_r[1] += line_r
    c_hp_r[2] += hp_r

    # 3.5mm stereo headphone jack (TRS: tip=L, ring=R, sleeve=GND)
    jack = Part(
        "Connector_Audio",
        "AudioJack3",
        footprint="Connector_Audio:Jack_3.5mm_CUI_SJ1-3533NG_Horizontal",
        value="HP_JACK",
    )
    jack["T"] += hp_l       # Tip = left channel
    jack["R"] += hp_r       # Ring = right channel
    jack["S"] += gnd        # Sleeve = ground

    # Series resistors on headphone outputs (22 ohm for short circuit protection)
    r_hp_l = Part(
        "Device", "R",
        value="22",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_hp_l[1] += hp_l
    hp_l_out = Net("HP_L_OUT")
    r_hp_l[2] += hp_l_out

    r_hp_r = Part(
        "Device", "R",
        value="22",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_hp_r[1] += hp_r
    hp_r_out = Net("HP_R_OUT")
    r_hp_r[2] += hp_r_out


# ===========================================================================
# Subcircuit: Microphone input with bias
# ===========================================================================
@subcircuit
def mic_input(mic_p, mic_n, vcc, gnd):
    """
    Electret microphone bias circuit. MIC+ gets bias voltage through
    a resistor, MIC- connects to ground via capacitor.
    """
    # Mic bias resistor (2.2K to VCC for electret mic)
    r_bias = Part(
        "Device", "R",
        value="2.2K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_bias[1] += vcc
    r_bias[2] += mic_p

    # DC blocking cap on MIC+ input
    c_mic = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    mic_in = Net("MIC_IN")
    c_mic[1] += mic_in
    c_mic[2] += mic_p

    # MIC- to ground
    c_micn = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_micn[1] += mic_n
    c_micn[2] += gnd

    # Mic connector (2-pin header)
    j_mic = Part(
        "Connector_Generic",
        "Conn_01x02",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        value="MIC",
    )
    j_mic[1] += mic_in
    j_mic[2] += gnd


# ===========================================================================
# Subcircuit: MicroSD card slot
# ===========================================================================
@subcircuit
def microsd_slot(sd_cs, sd_sck, sd_mosi, sd_miso, vcc, gnd):
    """
    MicroSD card slot with SPI interface. Separate SPI bus from VS1053B
    to allow independent SD card access.
    """
    # MicroSD connector (Hirose DM3AT)
    sd = Part(
        name="MicroSD",
        tool=SKIDL,
        dest=NETLIST,
        footprint="Connector_Card:microSD_HC_Hirose_DM3AT-SF-PEJM5",
        pins=[
            Pin(num="1", name="DAT2", func=Pin.types.BIDIR),
            Pin(num="2", name="CD_DAT3", func=Pin.types.BIDIR),
            Pin(num="3", name="CMD", func=Pin.types.BIDIR),
            Pin(num="4", name="VDD", func=Pin.types.PWRIN),
            Pin(num="5", name="CLK", func=Pin.types.INPUT),
            Pin(num="6", name="VSS", func=Pin.types.PWRIN),
            Pin(num="7", name="DAT0", func=Pin.types.BIDIR),
            Pin(num="8", name="DAT1", func=Pin.types.BIDIR),
            Pin(num="9", name="SHIELD", func=Pin.types.PASSIVE),
        ],
    )
    _init_skidl_pins(sd)

    # SPI mode connections:
    # CS = CD/DAT3 (pin 2)
    # MOSI = CMD (pin 3)
    # SCLK = CLK (pin 5)
    # MISO = DAT0 (pin 7)
    sd["CD_DAT3"] += sd_cs
    sd["CMD"] += sd_mosi
    sd["CLK"] += sd_sck
    sd["DAT0"] += sd_miso
    sd["VDD"] += vcc
    sd["VSS"] += gnd
    sd["SHIELD"] += gnd

    # Unused data lines pulled high
    r_dat1 = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_dat1[1] += vcc
    r_dat1[2] += sd["DAT1"]

    r_dat2 = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_dat2[1] += vcc
    r_dat2[2] += sd["DAT2"]

    # Decoupling cap for SD card
    c_sd = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_sd[1] += vcc
    c_sd[2] += gnd

    # Bulk cap
    c_sd_bulk = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_sd_bulk[1] += vcc
    c_sd_bulk[2] += gnd


# ===========================================================================
# Subcircuit: SPI + control breakout header
# ===========================================================================
@subcircuit
def spi_header(vcc, gnd, spi_sck, spi_mosi, spi_miso, sci_cs, sdi_cs,
               dreq, reset_n, sd_cs, sd_sck, sd_mosi, sd_miso):
    """
    Main breakout header for SPI control/data interface.
    Pin order matches common Adafruit-style VS1053 breakouts:
    VCC, GND, SCK, MOSI, MISO, CS, DCS, DREQ, RST,
    SD_CS, SD_SCK, SD_MOSI, SD_MISO
    """
    j_spi = Part(
        "Connector_Generic",
        "Conn_01x13",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x13_P2.54mm_Vertical",
        value="SPI_HDR",
    )
    j_spi[1] += vcc
    j_spi[2] += gnd
    j_spi[3] += spi_sck
    j_spi[4] += spi_mosi
    j_spi[5] += spi_miso
    j_spi[6] += sci_cs
    j_spi[7] += sdi_cs
    j_spi[8] += dreq
    j_spi[9] += reset_n
    j_spi[10] += sd_cs
    j_spi[11] += sd_sck
    j_spi[12] += sd_mosi
    j_spi[13] += sd_miso

    # Pull-up on RESET (keep VS1053B out of reset by default)
    r_reset = Part(
        "Device", "R",
        value="100K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_reset[1] += vcc
    r_reset[2] += reset_n

    # Pull-up on SCI_CS (deselect by default)
    r_cs = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_cs[1] += vcc
    r_cs[2] += sci_cs

    # Pull-up on SDI_CS (deselect by default)
    r_dcs = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_dcs[1] += vcc
    r_dcs[2] += sdi_cs

    # Pull-up on SD_CS
    r_sdcs = Part(
        "Device", "R",
        value="10K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    r_sdcs[1] += vcc
    r_sdcs[2] += sd_cs


# ===========================================================================
# Subcircuit: GPIO breakout + MIDI input header
# ===========================================================================
@subcircuit
def gpio_header(gpio_nets, midi_rx, vcc, gnd):
    """
    8 GPIO pins + MIDI RX pin on a 1x10 breakout header.
    MIDI mode: GPIO0 selects MIDI mode when low at boot, MIDI data on RX pin.
    """
    j_gpio = Part(
        "Connector_Generic",
        "Conn_01x10",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical",
        value="GPIO_HDR",
    )
    j_gpio[1] += gpio_nets[0]
    j_gpio[2] += gpio_nets[1]
    j_gpio[3] += gpio_nets[2]
    j_gpio[4] += gpio_nets[3]
    j_gpio[5] += gpio_nets[4]
    j_gpio[6] += gpio_nets[5]
    j_gpio[7] += gpio_nets[6]
    j_gpio[8] += gpio_nets[7]
    j_gpio[9] += midi_rx
    j_gpio[10] += gnd

    # MIDI input protection resistor (220 ohm as per MIDI spec)
    r_midi = Part(
        "Device", "R",
        value="220",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    midi_in = Net("MIDI_IN")
    r_midi[1] += midi_in
    r_midi[2] += midi_rx


# ===========================================================================
# Subcircuit: Power input with regulator
# ===========================================================================
@subcircuit
def power_supply(vcc, gnd):
    """
    3.3V LDO regulator (AP2112K-3.3) for 5V to 3.3V conversion.
    Accepts 3.3-5V input from breakout header.
    """
    reg = Part(
        "Regulator_Linear",
        "AP2112K-3.3",
        footprint="Package_TO_SOT_SMD:SOT-23-5",
        value="AP2112K-3.3",
    )
    vin = Net("VIN")
    vin.drive = POWER
    reg["VIN"] += vin
    reg["EN"] += vin   # Always enabled
    reg["GND"] += gnd
    reg["VOUT"] += vcc

    # Input cap
    c_in = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_in[1] += vin
    c_in[2] += gnd

    # Output cap
    c_out = Part(
        "Device", "C",
        value="10uF",
        footprint="Capacitor_SMD:C_0805_2012Metric",
    )
    c_out[1] += vcc
    c_out[2] += gnd

    # Additional output filter
    c_filt = Part(
        "Device", "C",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    c_filt[1] += vcc
    c_filt[2] += gnd

    # Power input header (2-pin: VIN, GND)
    j_pwr = Part(
        "Connector_Generic",
        "Conn_01x02",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        value="PWR",
    )
    j_pwr[1] += vin
    j_pwr[2] += gnd

    # Power LED indicator
    led = Part(
        "Device",
        "LED",
        value="GREEN",
        footprint="LED_SMD:LED_0603_1608Metric",
    )
    r_led = Part(
        "Device", "R",
        value="1K",
        footprint="Resistor_SMD:R_0603_1608Metric",
    )
    led[1] += vcc
    led[2] += r_led[1]
    r_led[2] += gnd


# ===========================================================================
# Top-level: instantiate all subcircuits
# ===========================================================================

# Power supply: 5V -> 3.3V LDO
power_supply(vcc, gnd)

# VS1053B codec with all connections
vs1053b_codec(
    vcc, vcc_1v8, gnd,
    spi_sck, spi_mosi, spi_miso,
    sci_cs, sdi_cs, dreq, reset_n,
    line_l, line_r,
    mic_p, mic_n,
    gpio, midi_rx,
    xtal_in, xtal_out,
)

# 12.288MHz crystal
crystal_osc(xtal_in, xtal_out, gnd)

# Audio output with headphone jack
audio_output(line_l, line_r, gnd)

# Microphone input
mic_input(mic_p, mic_n, vcc, gnd)

# MicroSD card slot
microsd_slot(sd_cs, sd_sck, sd_mosi, sd_miso, vcc, gnd)

# SPI + control breakout header
spi_header(
    vcc, gnd, spi_sck, spi_mosi, spi_miso,
    sci_cs, sdi_cs, dreq, reset_n,
    sd_cs, sd_sck, sd_mosi, sd_miso,
)

# GPIO breakout + MIDI header
gpio_header(gpio, midi_rx, vcc, gnd)

# ===========================================================================
# Generate schematic
# ===========================================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
print("Schematic generated successfully.")
