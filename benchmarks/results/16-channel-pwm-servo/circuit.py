"""
16-Channel PWM Servo Driver
I2C-controlled 16-channel PWM driver using PCA9685PW.
Features: 12-bit resolution, adjustable frequency, chainable (up to 62 boards),
reverse polarity protection, 220 ohm series resistors on outputs.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ── Power nets ──────────────────────────────────────────────────────
vdd = Net("VDD"); vdd.drive = POWER        # Logic supply (3.3-5V)
vservo = Net("VSERVO"); vservo.drive = POWER  # Servo supply (separate, up to 6V)
gnd = Net("GND"); gnd.drive = POWER

# ── I2C bus nets ────────────────────────────────────────────────────
sda = Net("SDA")
scl = Net("SCL")

# ── Output enable (active low) ─────────────────────────────────────
oe_net = Net("OE")

# ── PCA9685PW — 16-channel PWM controller ──────────────────────────
@subcircuit
def pwm_controller(vdd, gnd, sda, scl, oe, pwm_outputs):
    """PCA9685PW with decoupling, address config, I2C pull-ups, OE pull-up."""
    ic = Part("Driver_LED", "PCA9685PW",
              footprint="Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm")

    # Power
    ic["VDD"] += vdd
    ic["VSS"] += gnd

    # Decoupling caps
    c_dec1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec1[1] += vdd; c_dec1[2] += gnd

    c_dec2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_dec2[1] += vdd; c_dec2[2] += gnd

    # I2C
    ic["SDA"] += sda
    ic["SCL"] += scl

    # I2C pull-up resistors (10K to VDD)
    r_sda = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vdd; r_sda[2] += sda

    r_scl = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vdd; r_scl[2] += scl

    # Output enable — active low, pull-up to VDD (default: outputs enabled)
    ic["~{OE}"] += oe
    r_oe = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_oe[1] += vdd; r_oe[2] += oe

    # External clock input — tie low if unused (internal 25MHz oscillator)
    r_extclk = Part("Device", "R", value="10K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_extclk[1] += ic["EXTCLK"]; r_extclk[2] += gnd

    # Address configuration — A0..A5 with pull-down resistors (default addr 0x40)
    # Each address bit has a solder jumper pad (resistor to GND = 0)
    for i in range(6):
        pin_name = f"A{i}"
        r_addr = Part("Device", "R", value="10K",
                      footprint="Resistor_SMD:R_0603_1608Metric")
        r_addr[1] += ic[pin_name]; r_addr[2] += gnd

    # PWM outputs
    for i in range(16):
        pwm_outputs[i] += ic[f"LED{i}"]


# ── Output stage: 220 ohm series resistors + servo headers ─────────
@subcircuit
def output_stage(vservo, gnd, pwm_in, ch_start, ch_count):
    """Series protection resistors and 3-pin servo headers for a bank of channels."""
    for i in range(ch_count):
        ch = ch_start + i
        # 220 ohm series resistor on each output for protection
        r_out = Part("Device", "R", value="220",
                     footprint="Resistor_SMD:R_0603_1608Metric")
        r_out[1] += pwm_in[i]

        # 3-pin servo header: Signal, VCC_SERVO, GND
        hdr = Part("Connector_Generic", "Conn_01x03",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
        hdr[1] += r_out[2]     # Signal (through 220R)
        hdr[2] += vservo       # Servo power
        hdr[3] += gnd          # Ground


# ── Reverse polarity protection on servo power ─────────────────────
@subcircuit
def servo_power_protection(vin, vout, gnd):
    """P-channel MOSFET reverse polarity protection on servo supply."""
    # P-MOSFET for reverse polarity protection
    q_rpol = Part(name="Si2301CDS", tool=SKIDL, dest=NETLIST,
                  footprint="Package_TO_SOT_SMD:SOT-23",
                  pins=[
                      Pin(num="1", name="G", func=Pin.types.INPUT),
                      Pin(num="2", name="S", func=Pin.types.PASSIVE),
                      Pin(num="3", name="D", func=Pin.types.PASSIVE),
                  ])
    # Gate to GND (turns on when VIN is positive)
    q_rpol["G"] += gnd
    # Source to input (terminal block side)
    q_rpol["S"] += vin
    # Drain to protected servo supply
    q_rpol["D"] += vout

    # Bulk capacitor on servo power
    c_servo = Part("Device", "C", value="100uF",
                   footprint="Capacitor_SMD:C_1206_3216Metric")
    c_servo[1] += vout; c_servo[2] += gnd

    # Additional decoupling
    c_servo_dec = Part("Device", "C", value="100nF",
                       footprint="Capacitor_SMD:C_0603_1608Metric")
    c_servo_dec[1] += vout; c_servo_dec[2] += gnd


# ── Connectors ──────────────────────────────────────────────────────
@subcircuit
def connectors(vdd, vservo_in, gnd, sda, scl, oe):
    """Power input terminal blocks, I2C header, and OE header."""
    # Logic power input terminal block (2-pin)
    j_logic = Part("Connector_Generic", "Conn_01x02",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_logic[1] += vdd
    j_logic[2] += gnd

    # Servo power input terminal block (2-pin)
    j_servo = Part("Connector_Generic", "Conn_01x02",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_servo[1] += vservo_in
    j_servo[2] += gnd

    # I2C input header (4-pin: VDD, GND, SDA, SCL)
    j_i2c_in = Part("Connector_Generic", "Conn_01x04",
                    footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    j_i2c_in[1] += vdd
    j_i2c_in[2] += gnd
    j_i2c_in[3] += sda
    j_i2c_in[4] += scl

    # I2C output/chain header (4-pin: VDD, GND, SDA, SCL)
    j_i2c_out = Part("Connector_Generic", "Conn_01x04",
                     footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    j_i2c_out[1] += vdd
    j_i2c_out[2] += gnd
    j_i2c_out[3] += sda
    j_i2c_out[4] += scl

    # OE control header (2-pin: OE, GND)
    j_oe = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_oe[1] += oe
    j_oe[2] += gnd

    # Power LED indicator on logic supply
    led = Part("Device", "LED", value="GREEN",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_led[1] += vdd
    r_led[2] += led[1]  # Anode
    led[2] += gnd        # Cathode


# ── Instantiate the circuit ─────────────────────────────────────────

# PWM output nets
pwm_nets = [Net(f"PWM{i}") for i in range(16)]

# Unprotected servo power input (from terminal block)
vservo_in = Net("VSERVO_IN")

# PCA9685 controller with I2C, address config, decoupling
pwm_controller(vdd, gnd, sda, scl, oe_net, pwm_nets)

# Output channels: 2 banks of 8 with series resistors and servo headers
output_stage(vservo, gnd, pwm_nets[0:8], 0, 8)
output_stage(vservo, gnd, pwm_nets[8:16], 8, 8)

# Reverse polarity protection on servo power rail
servo_power_protection(vservo_in, vservo, gnd)

# Connectors: power, I2C, OE
connectors(vdd, vservo_in, gnd, sda, scl, oe_net)

# ── Generate outputs ────────────────────────────────────────────────
generate_schematic(auto_stub=True)
