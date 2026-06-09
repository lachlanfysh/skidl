"""
PAM8302 Mono Audio Amplifier
Class-D mono amplifier delivering 2.5W into 4-8 ohm speakers.
2.0-5.5V supply range with 90% efficiency.
Volume adjustable via trimmer pot.
Built-in thermal and over-current protection.
Fully differential inputs for clean audio.
"""

import os
os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from skidl import *
set_default_tool(KICAD9)

# ── Power Nets ──────────────────────────────────────────
vdd = Net("VDD"); vdd.drive = POWER
gnd = Net("GND"); gnd.drive = POWER


@subcircuit
def power_input(vdd, gnd):
    """Power input connector with bulk and bypass capacitors."""
    # 2-pin JST PH for power input (2.0-5.5V)
    j_pwr = Part("Connector_Generic", "Conn_01x02",
                  value="PWR_IN",
                  footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    j_pwr[1] += vdd
    j_pwr[2] += gnd

    # Bulk capacitor - 100uF electrolytic for supply rail stability
    c_bulk = Part("Device", "C_Polarized",
                  value="100uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vdd
    c_bulk[2] += gnd

    # Bypass capacitor - 100nF ceramic close to supply
    c_bypass = Part("Device", "C",
                    value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bypass[1] += vdd
    c_bypass[2] += gnd


@subcircuit
def audio_input(inp_net, inn_net, gnd):
    """Differential audio input with volume control trimmer pot and DC blocking."""
    # 3.5mm audio jack - mono input (tip=signal, sleeve=ground)
    j_audio = Part("Connector_Audio", "AudioJack2_Ground",
                   value="3.5mm_IN",
                   footprint="Connector_Audio:Jack_3.5mm_CUI_SJ1-3513N_Horizontal")
    # T = tip (signal), S = sleeve (signal return), G = ground
    j_audio["G"] += gnd

    # DC blocking cap on input signal path
    c_dc_block = Part("Device", "C",
                      value="1uF",
                      footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dc_block[1] += j_audio["T"]

    # Volume control trimmer pot (10K)
    # Pin 1 = one end, Pin 2 = wiper, Pin 3 = other end
    r_vol = Part("Device", "R_Potentiometer_Trim",
                 value="10K",
                 footprint="Potentiometer_SMD:Potentiometer_Bourns_3214W_Vertical")
    r_vol[1] += c_dc_block[2]   # Input side after DC blocking
    r_vol[3] += gnd             # Other end to ground
    # Wiper goes to IN+ via coupling resistor

    # Input coupling resistor to IN+ (recommended 1K for PAM8302)
    r_in = Part("Device", "R",
                value="1K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_in[1] += r_vol[2]    # From wiper
    r_in[2] += inp_net      # To amplifier IN+

    # IN- tied to ground via resistor for fully differential
    # (single-ended to differential conversion)
    r_inn = Part("Device", "R",
                 value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_inn[1] += inn_net     # To amplifier IN-
    r_inn[2] += gnd

    # Sleeve to ground via coupling network (use jack switch pin S)
    j_audio["S"] += gnd


@subcircuit
def amplifier(vdd, gnd, inp_net, inn_net, outp_net, outn_net):
    """PAM8302A Class-D amplifier with decoupling."""
    # PAM8302AAD in SOIC-8
    u1 = Part("Amplifier_Audio", "PAM8302AAD",
              value="PAM8302AAD",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")

    # Power connections
    u1["VDD"] += vdd
    u1["GND"] += gnd

    # Shutdown pin - pull high to enable (active low shutdown)
    r_sd = Part("Device", "R",
                value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_sd[1] += vdd
    r_sd[2] += u1["~{SD}"]

    # Audio input connections
    u1["IN+"] += inp_net
    u1["IN-"] += inn_net

    # Output connections (differential to speaker)
    u1["OUT+"] += outp_net
    u1["OUT-"] += outn_net

    # Supply decoupling - 100nF ceramic right at VDD pin
    c_dec = Part("Device", "C",
                 value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vdd
    c_dec[2] += gnd

    # Additional 10uF supply capacitor
    c_sup = Part("Device", "C_Polarized",
                 value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_sup[1] += vdd
    c_sup[2] += gnd


@subcircuit
def speaker_output(outp_net, outn_net, gnd):
    """Speaker output connector with snubber networks."""
    # 2-pin JST PH for speaker connection (4-8 ohm)
    j_spk = Part("Connector_Generic", "Conn_01x02",
                 value="SPEAKER",
                 footprint="Connector_JST:JST_PH_S2B-PH-K_1x02_P2.00mm_Horizontal")
    j_spk[1] += outp_net
    j_spk[2] += outn_net

    # Output filter / snubber on OUT+ (Zobel network)
    r_snub_p = Part("Device", "R",
                    value="10R",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    c_snub_p = Part("Device", "C",
                    value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    r_snub_p[1] += outp_net
    r_snub_p[2] += c_snub_p[1]
    c_snub_p[2] += gnd

    # Output filter / snubber on OUT- (Zobel network)
    r_snub_n = Part("Device", "R",
                    value="10R",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    c_snub_n = Part("Device", "C",
                    value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    r_snub_n[1] += outn_net
    r_snub_n[2] += c_snub_n[1]
    c_snub_n[2] += gnd


# ── Internal signal nets ────────────────────────────────
inp = Net("IN_P")      # Differential input +
inn = Net("IN_N")      # Differential input -
outp = Net("OUT_P")    # Differential output +
outn = Net("OUT_N")    # Differential output -

# ── Instantiate subcircuits ─────────────────────────────
power_input(vdd, gnd)
audio_input(inp, inn, gnd)
amplifier(vdd, gnd, inp, inn, outp, outn)
speaker_output(outp, outn, gnd)

# ── Generate schematic ──────────────────────────────────
generate_schematic(auto_stub=True, auto_stub_fanout=3)
