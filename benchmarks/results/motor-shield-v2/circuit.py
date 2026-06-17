"""
Adafruit Motor Shield V2 for Arduino
=====================================
Drives up to 4 DC motors or 2 stepper motors.
PCA9685 PWM driver (I2C) + two TB6612FNG dual H-bridges + two 74HC595
shift registers for direction control.

Key ICs:
  - PCA9685PW: 16-channel 12-bit PWM (TSSOP-28, I2C 0x60-0x7F)
  - TB6612FNG x2: dual H-bridge motor drivers (SSOP-24, 1.2A/ch)
  - 74HC595 x2: 8-bit SIPO shift registers for direction (SOIC-16)

74HC595 pin mapping:
  pin 14 SER    - serial data input
  pin 11 SRCLK  - shift clock
  pin 12 RCLK   - latch clock
  pin 10 ~SRCLR - clear (active-low, tie to VCC)
  pin 13 ~OE    - output enable (active-low, tie to GND)
  pin 15 QA ... pin 7 QH - 8 parallel outputs
  pin 9  QH'   - serial output (for daisy-chain)

TB6612FNG pin mapping:
  pin 19 STBY, pin 23 PWMA, pin 15 PWMB
  pin 21 AIN1, pin 22 AIN2, pin 17 BIN1, pin 16 BIN2
  pin 20 VCC, pin 18 GND
  pin 24 VM1, pin 13 VM2, pin 14 VM3 (motor supply)
  pin 3,4 PGND1, pin 9,10 PGND2 (motor ground)
  pin 1,2 AO1, pin 5,6 AO2, pin 11,12 BO1, pin 7,8 BO2
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

EDA_FLOORPLAN = {
    "board_width_mm": 69.0,
    "board_height_mm": 53.0,
}

# ============================================================
# Global Power Nets
# ============================================================
vcc = Net("VCC")
vcc.drive = POWER
gnd = Net("GND")
gnd.drive = POWER
vm  = Net("VMOTOR")
vm.drive = POWER

sda = Net("SDA")
scl = Net("SCL")
sr_data  = Net("SR_DATA")
sr_clk   = Net("SR_CLK")
sr_latch = Net("SR_LATCH")

# ============================================================
# Subcircuit: PCA9685PW PWM Driver
# ============================================================
@subcircuit
def pwm_driver(p_vcc, p_gnd, p_sda, p_scl, pwm_out):
    """PCA9685PW 16-ch 12-bit PWM over I2C (TSSOP-28)."""

    ic = Part("Driver_LED", "PCA9685PW",
              footprint="Package_SO:TSSOP-28_4.4x9.7mm_P0.65mm")
    ic.value = "PCA9685PW"

    ic["VDD"]   += p_vcc
    ic["VSS"]   += p_gnd
    ic["SDA"]   += p_sda
    ic["SCL"]   += p_scl
    ic["EXTCLK"] += p_gnd
    ic["~{OE}"] += p_gnd

    addr_nets = []
    for i in range(5):
        a = Net(f"ADDR_A{i}")
        ic[f"A{i}"] += a
        addr_nets.append(a)
    ic["A5"] += p_gnd

    for i in range(16):
        ic[f"LED{i}"] += pwm_out[i]

    c1 = Part("Device", "C", value="100nF",
              footprint="Capacitor_SMD:C_0603_1608Metric")
    c1[1] += p_vcc; c1[2] += p_gnd

    c2 = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
    c2[1] += p_vcc; c2[2] += p_gnd

    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += p_vcc; r_sda[2] += p_sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += p_vcc; r_scl[2] += p_scl

    return addr_nets


# ============================================================
# Subcircuit: 74HC595 Shift Register
# q_out_8: list of 8 nets for QA-QH (parallel outputs)
# ser_out: net connected only to QH' (serial daisy-chain output, pin 9)
# ============================================================
@subcircuit
def shift_register(p_vcc, p_gnd, ser_in, clk, latch, q_out_8, ser_out):
    """74HC595 8-bit SIPO (SOIC-16). q_out_8[0..7] = QA..QH outputs."""

    ic = Part("74xx", "74HC595",
              footprint="Package_SO:SOIC-16_3.9x9.9mm_P1.27mm")
    ic.value = "74HC595"

    ic["VCC"]      += p_vcc
    ic["GND"]      += p_gnd
    ic["SER"]      += ser_in
    ic["SRCLK"]    += clk
    ic["RCLK"]     += latch
    ic["~{OE}"]    += p_gnd    # Always enable outputs
    ic["~{SRCLR}"] += p_vcc   # Never clear

    # Connect QA through QG to q_out_8[0..6] (7 parallel outputs)
    for i, q_name in enumerate(["QA","QB","QC","QD","QE","QF","QG"]):
        ic[q_name] += q_out_8[i]

    # QH (pin 7) = bit 7 parallel output -> q_out_8[7]
    # QH' (pin 9) = serial chain output -> ser_out
    # These MUST be separate nets. Wire by pin number to avoid name conflicts.
    ic[7] += q_out_8[7]   # QH parallel output
    ic[9] += ser_out       # QH' serial output (daisy-chain)

    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
    c[1] += p_vcc; c[2] += p_gnd


# ============================================================
# Subcircuit: TB6612FNG Dual H-Bridge
# ============================================================
@subcircuit
def motor_driver(p_vcc, p_gnd, p_vm,
                 pwma, ain1, ain2,
                 pwmb, bin1, bin2,
                 stby,
                 ao1, ao2, bo1, bo2):
    """TB6612FNG dual H-bridge (SSOP-24, 1.2A/ch)."""

    ic = Part("Driver_Motor", "TB6612FNG",
              footprint="Package_SO:SSOP-24_5.3x8.2mm_P0.65mm")
    ic.value = "TB6612FNG"

    ic["VCC"]   += p_vcc
    ic["GND"]   += p_gnd
    ic["VM1"]   += p_vm
    ic["VM2"]   += p_vm
    ic["VM3"]   += p_vm
    ic["PGND1"] += p_gnd
    ic["PGND2"] += p_gnd
    ic["STBY"]  += stby
    ic["PWMA"]  += pwma
    ic["AIN1"]  += ain1
    ic["AIN2"]  += ain2
    ic["AO1"]   += ao1
    ic["AO2"]   += ao2
    ic["PWMB"]  += pwmb
    ic["BIN1"]  += bin1
    ic["BIN2"]  += bin2
    ic["BO1"]   += bo1
    ic["BO2"]   += bo2

    c_vcc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vcc[1] += p_vcc; c_vcc[2] += p_gnd

    c_vm = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vm[1] += p_vm; c_vm[2] += p_gnd

    c_bulk = Part("Device", "C", value="47uF",
                  footprint="Capacitor_SMD:C_1210_3225Metric")
    c_bulk[1] += p_vm; c_bulk[2] += p_gnd


# ============================================================
# Subcircuit: Motor Connectors (4x 2-pin headers)
# ============================================================
@subcircuit
def motor_terminals(m1a, m1b, m2a, m2b, m3a, m3b, m4a, m4b):
    """4x 2-pin 2.54mm headers for motor connections."""
    for nets, label in [
        ((m1a, m1b), "M1"),
        ((m2a, m2b), "M2"),
        ((m3a, m3b), "M3"),
        ((m4a, m4b), "M4"),
    ]:
        j = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
        j.value = label
        j.edge_preference = "right"
        j[1] += nets[0]
        j[2] += nets[1]


# ============================================================
# Subcircuit: Motor Power Input
# ============================================================
@subcircuit
def power_input_terminal(p_vm, p_gnd):
    """2-pin header for external motor power (5-12V)."""
    j = Part("Connector_Generic", "Conn_01x02",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j.value = "EXT_PWR"
    j.edge_preference = "right"
    j[1] += p_vm
    j[2] += p_gnd

    c = Part("Device", "C", value="100uF",
             footprint="Capacitor_SMD:C_1210_3225Metric")
    c[1] += p_vm; c[2] += p_gnd


# ============================================================
# Subcircuit: I2C Address Jumpers
# ============================================================
@subcircuit
def address_jumpers(addr_nets, p_vcc, p_gnd):
    """5x 3-pin solder jumpers + 10K pull-downs for I2C address."""
    for i, an in enumerate(addr_nets):
        r = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
        r[1] += an; r[2] += p_gnd

        j = Part("Connector_Generic", "Conn_01x03",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
        j.value = f"A{i}_SEL"
        j[1] += p_gnd
        j[2] += an
        j[3] += p_vcc


# ============================================================
# Subcircuit: Arduino Shield Headers (Uno R3)
# ============================================================
@subcircuit
def arduino_headers(p_vcc, p_gnd, p_sda, p_scl, p_sr_data, p_sr_clk, p_sr_latch):
    """Arduino Uno R3 stacking shield pin sockets."""

    j_pwr = Part("Connector_Generic", "Conn_01x08",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_pwr.value = "PWR_HDR"
    j_pwr.edge_preference = "bottom"
    j_pwr[1] += Net("RESET")
    j_pwr[2] += Net("HDR_3V3")
    j_pwr[3] += p_vcc
    j_pwr[4] += p_gnd
    j_pwr[5] += p_gnd
    j_pwr[6] += Net("VIN")
    j_pwr[7] += Net("HDR_NC1")
    j_pwr[8] += Net("HDR_NC2")

    j_adc = Part("Connector_Generic", "Conn_01x06",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical")
    j_adc.value = "ADC_HDR"
    j_adc.edge_preference = "bottom"
    j_adc[1] += Net("A0")
    j_adc[2] += Net("A1")
    j_adc[3] += Net("A2")
    j_adc[4] += Net("A3")
    j_adc[5] += p_sda
    j_adc[6] += p_scl

    j_dhi = Part("Connector_Generic", "Conn_01x08",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_dhi.value = "DIG_HI_HDR"
    j_dhi.edge_preference = "top"
    j_dhi[1] += Net("D8")
    j_dhi[2] += Net("D9")
    j_dhi[3] += Net("D10")
    j_dhi[4] += p_sr_data
    j_dhi[5] += Net("D12")
    j_dhi[6] += p_sr_clk
    j_dhi[7] += p_gnd
    j_dhi[8] += Net("AREF")

    j_dlo = Part("Connector_Generic", "Conn_01x08",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_dlo.value = "DIG_LO_HDR"
    j_dlo.edge_preference = "bottom"
    j_dlo[1] += Net("D0")
    j_dlo[2] += Net("D1")
    j_dlo[3] += Net("D2")
    j_dlo[4] += Net("D3")
    j_dlo[5] += Net("D4")
    j_dlo[6] += Net("D5")
    j_dlo[7] += p_sr_latch
    j_dlo[8] += Net("D7")


# ============================================================
# Subcircuit: Status LED
# ============================================================
@subcircuit
def status_led(p_vcc, p_gnd):
    """Green power-on indicator LED."""
    led = Part("Device", "LED", value="GREEN",
               footprint="LED_SMD:LED_0603_1608Metric")
    r = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
    p_vcc & r & led & p_gnd


# ============================================================
# Top-Level Circuit Assembly
# ============================================================

# Motor output nets (direct from H-bridge to connectors, no sense R for now)
m1a = Net("M1A"); m1b = Net("M1B")
m2a = Net("M2A"); m2b = Net("M2B")
m3a = Net("M3A"); m3b = Net("M3B")
m4a = Net("M4A"); m4b = Net("M4B")

# PWM outputs from PCA9685
pwm_out = [Net(f"PWM{i}") for i in range(16)]

# Shift register output nets (8 bits each: QA..QH)
# SR1 bit map: Q0=M1_AIN1, Q1=M1_AIN2, Q2=M2_BIN1, Q3=M2_BIN2,
#              Q4=STBY_DRV1, Q5=STBY_DRV2, Q6=NC, Q7=NC
# SR2 bit map: Q0=M3_AIN1, Q1=M3_AIN2, Q2=M4_BIN1, Q3=M4_BIN2,
#              Q4-Q7=NC
sr1_q = [Net(f"SR1_Q{i}") for i in range(8)]  # QA-QH (8 nets)
sr2_q = [Net(f"SR2_Q{i}") for i in range(8)]  # QA-QH (8 nets)
sr_chain = Net("SR_CHAIN")   # SR1 QH' -> SR2 SER (daisy-chain)
sr2_end  = Net("SR2_END")    # SR2 QH' (end of chain, floating NC)

# Instantiate subcircuits

# PCA9685 PWM driver
addr_nets = pwm_driver(vcc, gnd, sda, scl, pwm_out)

# 74HC595 SR #1: M1/M2 direction + STBY bits
shift_register(vcc, gnd, sr_data, sr_clk, sr_latch, sr1_q, ser_out=sr_chain)

# 74HC595 SR #2: M3/M4 direction, daisy-chained from SR1
shift_register(vcc, gnd, sr_chain, sr_clk, sr_latch, sr2_q, ser_out=sr2_end)

# TB6612FNG #1: Motors M1 (chan A) + M2 (chan B)
motor_driver(vcc, gnd, vm,
             pwm_out[0], sr1_q[0], sr1_q[1],  # M1 PWMA, AIN1, AIN2
             pwm_out[3], sr1_q[2], sr1_q[3],  # M2 PWMB, BIN1, BIN2
             sr1_q[4],                          # STBY = SR1 Q4
             m1a, m1b, m2a, m2b)

# TB6612FNG #2: Motors M3 (chan A) + M4 (chan B)
motor_driver(vcc, gnd, vm,
             pwm_out[8],  sr2_q[0], sr2_q[1],  # M3 PWMA, AIN1, AIN2
             pwm_out[11], sr2_q[2], sr2_q[3],  # M4 PWMB, BIN1, BIN2
             sr1_q[5],                           # STBY = SR1 Q5
             m3a, m3b, m4a, m4b)

# Motor connectors (4x 2-pin)
motor_terminals(m1a, m1b, m2a, m2b, m3a, m3b, m4a, m4b)

# External motor power connector
power_input_terminal(vm, gnd)

# I2C address select jumpers
address_jumpers(addr_nets, vcc, gnd)

# Arduino stacking headers
arduino_headers(vcc, gnd, sda, scl, sr_data, sr_clk, sr_latch)

# Power status LED
status_led(vcc, gnd)

# ============================================================
# Generate Schematic
# ============================================================
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)
