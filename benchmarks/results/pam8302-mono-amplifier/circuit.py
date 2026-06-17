"""
PAM8302A Class-D Mono Audio Amplifier Breakout
2.5W into 4-8 ohm speakers, 2.0-5.5V supply, 90% efficiency.
Volume adjustable via SMD trimmer pot.
Built-in thermal and over-current protection.
Fully differential inputs, filterless BTL output.

PAM8302AASCR (LCSC C113367, MSOP-8):
  Pin 1: ~{SD}  (active-low shutdown)
  Pin 2: NC
  Pin 3: IN+
  Pin 4: IN-
  Pin 5: VO+   (speaker out +)
  Pin 6: VDD
  Pin 7: GND
  Pin 8: VO-   (speaker out -)
"""

from skidl import *

# Power rails
vdd = Net("VDD")
vdd.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# Signal nets
audio_sig = Net("AUDIO_SIG")   # raw audio input after connector
in_p = Net("IN_P")             # differential + after vol pot and series R
in_n = Net("IN_N")             # differential - (tied to GND via R)
vol_wiper = Net("VOL_WIPER")   # trimmer wiper
out_p = Net("SPK_P")           # BTL output +
out_n = Net("SPK_N")           # BTL output -


@subcircuit
def power_input(vdd, gnd):
    """2-pin power header + bulk and bypass decoupling."""
    j_pwr = Part("Connector_Generic", "Conn_01x02",
                 value="PWR_IN",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_pwr[1] += vdd
    j_pwr[2] += gnd
    j_pwr.edge_preference = "top"

    # 10uF bulk cap
    c_bulk = Part("Device", "C_Polarized",
                  value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vdd
    c_bulk[2] += gnd

    # 100nF HF bypass
    c_bypass = Part("Device", "C",
                    value="100nF",
                    footprint="Capacitor_SMD:C_0603_1608Metric")
    c_bypass[1] += vdd
    c_bypass[2] += gnd


@subcircuit
def audio_input(vdd, gnd, audio_sig, vol_wiper, in_p, in_n):
    """
    Audio input section:
    - 2-pin header for audio signal + GND
    - 1uF DC-blocking cap
    - 10k trimmer pot for volume control (wiper -> IN+)
    - 1k input resistors on IN+ and IN- (single-ended to differential)
    - IN- terminated to GND with matching resistor
    """
    j_audio = Part("Connector_Generic", "Conn_01x02",
                   value="AUDIO_IN",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_audio[1] += audio_sig
    j_audio[2] += gnd
    j_audio.edge_preference = "left"

    # DC-blocking cap
    c_dc = Part("Device", "C",
                value="1uF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    sig_after_dc = Net("SIG_DC")
    audio_sig += c_dc[1]
    sig_after_dc += c_dc[2]

    # 10k trimmer pot: top=audio, wiper=vol_wiper, bottom=GND
    rv1 = Part("Device", "R_Potentiometer_Trim",
               value="10k",
               footprint="Potentiometer_SMD:Potentiometer_Bourns_3214W_Vertical")
    rv1[1] += sig_after_dc
    rv1[2] += vol_wiper
    rv1[3] += gnd

    # 1k series resistor on IN+
    r_inp = Part("Device", "R", value="1k",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    vol_wiper += r_inp[1]
    in_p += r_inp[2]

    # 1k matching resistor on IN- to GND
    r_inn = Part("Device", "R", value="1k",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    in_n += r_inn[1]
    gnd += r_inn[2]


@subcircuit
def amplifier_core(vdd, gnd, in_p, in_n, out_p, out_n):
    """
    PAM8302AASCR Class-D amplifier (LCSC C113367, MSOP-8).
    SD pin pulled high to enable. 100nF bypass near VDD pin.
    NC pin (pin 2) tied to GND per known-issues convention.
    """
    u1 = Part("C113367", "PAM8302AASCR",
              footprint="Package_SO:MSOP-8_3x3mm_P0.65mm")

    vdd += u1["VDD"]
    gnd += u1["GND"]

    # NC pin tied to GND by pin number (known issue: NC pins tie to GND by number)
    gnd += u1[2]

    # SD pin: pull high to keep enabled (active-low shutdown)
    r_sd = Part("Device", "R", value="100k",
                footprint="Resistor_SMD:R_0603_1608Metric")
    vdd += r_sd[1]
    u1["~{SD}"] += r_sd[2]

    # Inputs
    u1["IN+"] += in_p
    u1["IN-"] += in_n

    # Differential outputs
    u1["VO+"] += out_p
    u1["VO-"] += out_n

    # 100nF decoupling close to VDD pin
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    vdd += c_dec[1]
    gnd += c_dec[2]


@subcircuit
def speaker_output(out_p, out_n):
    """2-pin header for BTL differential speaker output."""
    j_spk = Part("Connector_Generic", "Conn_01x02",
                 value="SPEAKER",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_spk[1] += out_p
    j_spk[2] += out_n
    j_spk.edge_preference = "right"


# Mounting holes (M3, unconnected)
mh1 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")
mh2 = Part("Mechanical", "MountingHole",
           footprint="MountingHole:MountingHole_3.2mm_M3")

# Instantiate all blocks
power_input(vdd, gnd)
audio_input(vdd, gnd, audio_sig, vol_wiper, in_p, in_n)
amplifier_core(vdd, gnd, in_p, in_n, out_p, out_n)
speaker_output(out_p, out_n)

EDA_FLOORPLAN = {
    "outline": {"width_mm": 40, "height_mm": 30, "corner_radius_mm": 1.5},
}
