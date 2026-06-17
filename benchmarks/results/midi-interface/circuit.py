"""
MIDI Interface — IN / OUT / THRU
- MIDI IN: 5-pin DIN, 6N138 optocoupler, 220Ω series + 1N4148 protection diode
- MIDI OUT: 5-pin DIN, driven from MCU TX via 220Ω resistors
- MIDI THRU: 5-pin DIN, buffered copy of IN via 74HC14 hex Schmitt inverter
- Power: 5V header or USB
- Status LEDs: IN activity + power
- Board: ~60x45mm, all through-hole

NOTE: KiCad standard libraries have no 5-pin circular DIN footprint.
Using Conn_01x05 with generic 5-pin header footprint as placeholder.
Real boards would use CUI MD-5100 or similar with a custom footprint.
"""

from skidl import *

set_default_tool(KICAD9)

# ── Power rails ──────────────────────────────────────────────────────────────
vcc = Net("VCC");  vcc.drive = POWER
gnd = Net("GND");  gnd.drive = POWER

# ── Signal nets ──────────────────────────────────────────────────────────────
midi_in_raw   = Net("MIDI_IN_RAW")    # optocoupler output → MCU RX
midi_out_tx   = Net("MIDI_OUT_TX")    # MCU TX → MIDI OUT
midi_thru_buf = Net("MIDI_THRU_BUF") # 74HC14 output → THRU connector

# Optocoupler input loop
opto_anode    = Net("OPTO_A")
opto_cathode  = Net("OPTO_C")
mid_inv       = Net("MID_INV")       # first-stage inverter out (inverted)

# ── Power/MCU header ─────────────────────────────────────────────────────────
@subcircuit
def power_section(vcc, gnd, midi_out_tx, midi_in_raw):
    j_pwr = Part(
        "Connector_Generic", "Conn_01x02",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical",
        value="5V PWR"
    )
    vcc += j_pwr[1]
    gnd += j_pwr[2]

    j_mcu = Part(
        "Connector_Generic", "Conn_01x04",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
        value="MCU HDR"
    )
    vcc         += j_mcu[1]
    gnd         += j_mcu[2]
    midi_out_tx += j_mcu[3]   # MCU TX
    midi_in_raw += j_mcu[4]   # MCU RX

    # Bulk decoupling
    c_bulk = Part("Device", "C", value="10uF", footprint="Capacitor_THT:C_Radial_D5.0mm_H11.0mm_P2.00mm")
    vcc += c_bulk[1]
    gnd += c_bulk[2]

    # Power LED
    led_pwr   = Part("Device", "LED", value="GREEN", footprint="LED_THT:LED_D3.0mm")
    r_led_pwr = Part("Device", "R",   value="470",   footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    vcc       += r_led_pwr[1]
    led_pwr[2] += r_led_pwr[2]   # anode
    gnd        += led_pwr[1]      # cathode

power_section(vcc, gnd, midi_out_tx, midi_in_raw)

# ── MIDI IN section ──────────────────────────────────────────────────────────
@subcircuit
def midi_in_section(vcc, gnd, opto_anode, opto_cathode, midi_in_raw):
    # 5-pin DIN connector (placeholder footprint — no standard KiCad DIN-5 footprint)
    din_in = Part(
        "Connector_Generic", "Conn_01x05",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
        value="MIDI IN"
    )
    gnd += din_in[2]           # pin 2 = shield
    din_in[1].do_erc = False   # unused
    din_in[3].do_erc = False   # unused

    # 220Ω current-limiting resistor
    r_in = Part("Device", "R", value="220", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    din_in[4]  += r_in[1]
    r_in[2]    += opto_anode

    # 1N4148 protection diode (reverse-biased, across LED input)
    d_prot = Part("Device", "D", value="1N4148", footprint="Diode_THT:D_DO-35_SOD27_P7.62mm_Horizontal")
    opto_cathode += d_prot[2]   # cathode
    opto_anode   += d_prot[1]   # anode
    din_in[5]    += opto_cathode

    # 6N138 optocoupler
    u_opto = Part("Isolator", "6N138", footprint="Package_DIP:DIP-8_W7.62mm", value="6N138")
    u_opto[1].do_erc = False    # NC
    u_opto[4].do_erc = False    # NC
    u_opto["VO2"].do_erc = False

    opto_anode   += u_opto["C1"]    # pin 2
    opto_cathode += u_opto["C2"]    # pin 3
    vcc          += u_opto["VCC"]   # pin 8
    gnd          += u_opto["GND"]   # pin 5

    # 4.7k pull-up on VO1
    r_pull = Part("Device", "R", value="4.7k", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    vcc          += r_pull[1]
    u_opto["VO1"] += r_pull[2]
    midi_in_raw  += u_opto["VO1"]

    # 100nF decoupling for opto
    c_opto = Part("Device", "C", value="100nF", footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm")
    vcc += c_opto[1]
    gnd += c_opto[2]

    # IN activity LED (active-low from opto)
    led_in   = Part("Device", "LED", value="YELLOW", footprint="LED_THT:LED_D3.0mm")
    r_led_in = Part("Device", "R",   value="470",    footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    vcc          += r_led_in[1]
    led_in[2]    += r_led_in[2]    # anode
    midi_in_raw  += led_in[1]      # cathode

midi_in_section(vcc, gnd, opto_anode, opto_cathode, midi_in_raw)

# ── MIDI THRU section (74HC14 buffer) ────────────────────────────────────────
@subcircuit
def midi_thru_section(vcc, gnd, midi_in_raw, midi_thru_buf, mid_inv):
    u_buf = Part("74xx", "74HC14", footprint="Package_DIP:DIP-14_W7.62mm", value="74HC14")
    vcc += u_buf[14]
    gnd += u_buf[7]

    # One inverter pair: IN → inv → inv → THRU (restores polarity)
    u_buf[1] += midi_in_raw    # first inverter in
    u_buf[2] += mid_inv        # inverted
    u_buf[3] += mid_inv        # second inverter in
    u_buf[4] += midi_thru_buf  # re-inverted = original polarity

    # Tie unused inverter inputs low to avoid floating
    u_buf[5]  += gnd;  u_buf[6].do_erc  = False
    u_buf[9]  += gnd;  u_buf[8].do_erc  = False
    u_buf[11] += gnd;  u_buf[10].do_erc = False
    u_buf[13] += gnd;  u_buf[12].do_erc = False

    # 100nF decoupling
    c_buf = Part("Device", "C", value="100nF", footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P2.50mm")
    vcc += c_buf[1]
    gnd += c_buf[2]

midi_thru_section(vcc, gnd, midi_in_raw, midi_thru_buf, mid_inv)

# ── MIDI OUT connector ────────────────────────────────────────────────────────
@subcircuit
def midi_out_connector(vcc, gnd, midi_out_tx):
    din_out = Part(
        "Connector_Generic", "Conn_01x05",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
        value="MIDI OUT"
    )
    gnd += din_out[2]
    din_out[1].do_erc = False
    din_out[3].do_erc = False

    r_out4 = Part("Device", "R", value="220", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    vcc        += r_out4[1]
    din_out[4] += r_out4[2]

    r_out5 = Part("Device", "R", value="220", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    midi_out_tx += r_out5[1]
    din_out[5]  += r_out5[2]

midi_out_connector(vcc, gnd, midi_out_tx)

# ── MIDI THRU connector ───────────────────────────────────────────────────────
@subcircuit
def midi_thru_connector(vcc, gnd, midi_thru_buf):
    din_thru = Part(
        "Connector_Generic", "Conn_01x05",
        footprint="Connector_PinHeader_2.54mm:PinHeader_1x05_P2.54mm_Vertical",
        value="MIDI THRU"
    )
    gnd += din_thru[2]
    din_thru[1].do_erc = False
    din_thru[3].do_erc = False

    r_thru4 = Part("Device", "R", value="220", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    vcc         += r_thru4[1]
    din_thru[4] += r_thru4[2]

    r_thru5 = Part("Device", "R", value="220", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    midi_thru_buf += r_thru5[1]
    din_thru[5]   += r_thru5[2]

midi_thru_connector(vcc, gnd, midi_thru_buf)
