"""
PAM8302 Class-D Mono Audio Amplifier Breakout
2.5W into 4-8 ohm speakers, 2.0-5.5V supply, 90% efficiency.
Volume adjustable via SMD trimmer pot.
Built-in thermal and over-current protection.
Fully differential inputs for clean audio without ground issues.
"""

from skidl import *
set_default_tool(KICAD9)

# Power rails
vdd = Net("VDD"); vdd.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Internal signal nets
in_p = Net("IN_P")    # Differential input +
in_n = Net("IN_N")    # Differential input -
out_p = Net("OUT_P")  # Speaker output +
out_n = Net("OUT_N")  # Speaker output -
vol_wiper = Net("VOL_WIPER")  # Trimmer wiper to IN+


@subcircuit
def power_block(vdd, gnd):
    """Power input header with decoupling caps."""
    # 2-pin 2.54mm power header (VDD + GND)
    j_pwr = Part("Connector_Generic", "Conn_01x02",
                 value="PWR_IN",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_pwr[1] += vdd
    j_pwr[2] += gnd

    # 10uF bulk cap on supply rail
    c_bulk = Part("Device", "C_Polarized",
                  value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vdd
    c_bulk[2] += gnd

    # 100nF decoupling cap
    c_dec = Part("Device", "C",
                 value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec[1] += vdd
    c_dec[2] += gnd


@subcircuit
def audio_input_block(vdd, gnd, in_p, in_n, vol_wiper):
    """
    Audio input section:
    - 3-pin header (AUDIO_IN, AUDIO_GND, NC) for audio input
    - DC-blocking cap on signal path
    - 10K trimmer pot for volume control (wiper -> IN+)
    - 1K input resistors on IN+ and IN- (differential input matching)
    - IN- tied to GND for single-ended to differential conversion
    """
    audio_sig = Net("AUDIO_SIG")

    # 2-pin header for audio input (pin1=signal, pin2=GND)
    j_audio = Part("Connector_Generic", "Conn_01x02",
                   value="AUDIO_IN",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_audio[1] += audio_sig
    j_audio[2] += gnd

    # DC-blocking capacitor on audio input (blocks DC bias from source)
    c_in = Part("Device", "C",
                value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_in[1] += audio_sig

    # 10K trimmer potentiometer for volume control
    # Pin 1 = top resistive end (from input signal after DC block)
    # Pin 2 = wiper (goes to IN+ via series resistor)
    # Pin 3 = bottom end (to GND - attenuation reference)
    rv1 = Part("Device", "R_Potentiometer_Trim",
               value="10K",
               footprint="Potentiometer_THT:Potentiometer_Bourns_3296W_Vertical")
    rv1[1] += c_in[2]      # Audio signal after DC block
    rv1[3] += gnd           # Bottom to GND for voltage divider
    rv1[2] += vol_wiper     # Wiper output is volume-controlled signal

    # 1K series resistor on IN+ (input matching + protection)
    r_inp = Part("Device", "R",
                 value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_inp[1] += vol_wiper
    r_inp[2] += in_p

    # IN- resistor - tie to GND for single-ended input
    # (PAM8302 datasheet: connect IN- to GND via same value resistor as IN+)
    r_inn = Part("Device", "R",
                 value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_inn[1] += in_n
    r_inn[2] += gnd


@subcircuit
def amplifier_block(vdd, gnd, in_p, in_n, out_p, out_n):
    """
    PAM8302AAD Class-D amplifier core.
    Pins: ~{SD}=1, NC=2, IN+=3, IN-=4, OUT+=5, VDD=6, GND=7, OUT-=8
    """
    u1 = Part("Amplifier_Audio", "PAM8302AAD",
              value="PAM8302AAD",
              footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")

    # Power
    u1["VDD"] += vdd
    u1["GND"] += gnd

    # Enable: SD pin active-low shutdown - pull high via 100K to enable
    r_sd = Part("Device", "R",
                value="100K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_sd[1] += vdd
    r_sd[2] += u1["~{SD}"]

    # Audio differential inputs
    u1["IN+"] += in_p
    u1["IN-"] += in_n

    # Differential speaker outputs
    u1["OUT+"] += out_p
    u1["OUT-"] += out_n

    # NC pin - leave unconnected (PAM8302 pin 2 is NC)
    # u1["NC"] is left floating intentionally

    # Supply decoupling - 100nF right at VDD (auto-placed near U1 by engine)
    c_vdd = Part("Device", "C",
                 value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd[1] += vdd
    c_vdd[2] += gnd


@subcircuit
def speaker_output_block(gnd, out_p, out_n):
    """
    Speaker output connector - differential (BTL) output to 4-8 ohm speaker.
    2-pin 2.54mm header for speaker wires.
    """
    # 2-pin header for speaker
    j_spk = Part("Connector_Generic", "Conn_01x02",
                 value="SPEAKER",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_spk[1] += out_p
    j_spk[2] += out_n


# Instantiate all blocks
power_block(vdd, gnd)
audio_input_block(vdd, gnd, in_p, in_n, vol_wiper)
amplifier_block(vdd, gnd, in_p, in_n, out_p, out_n)
speaker_output_block(gnd, out_p, out_n)
