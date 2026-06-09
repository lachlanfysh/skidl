"""
Motor Shield V2 for Arduino
===========================
Upgraded motor shield using TB6612 MOSFET drivers with 1.2A per channel
(3A peak). PCA9685 PWM driver chip handles all motor controls over I2C.
Drives up to 4 DC motors or 2 stepper motors. Stackable with 5 address
select pins (up to 32 shields). Polarity protection FET on power pins.
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ============================================================
# Global Power Nets
# ============================================================
vcc = Net("VCC")        # 5V logic from Arduino
vcc.drive = POWER
gnd = Net("GND")        # Common ground
gnd.drive = POWER
vm = Net("VMOTOR")      # Motor supply voltage (post-protection FET)
vm.drive = POWER
vm_in = Net("VM_IN")    # Motor supply input (pre-protection FET)

# I2C bus
sda = Net("SDA")
scl = Net("SCL")

# ============================================================
# Subcircuit: PWM Driver (PCA9685BS)
# ============================================================
@subcircuit
def pwm_driver(vcc, gnd, sda, scl):
    """PCA9685 16-channel PWM driver with I2C interface."""

    pwm = Part("Driver_LED", "PCA9685BS",
               footprint="Package_DFN_QFN:QFN-28-1EP_6x6mm_P0.65mm_EP4.25x4.25mm")

    # Power connections
    pwm["VDD"] += vcc
    # PCA9685BS has two VSS pins (pin 11, 29)
    pwm["VSS"] += gnd

    # I2C bus
    pwm["SDA"] += sda
    pwm["SCL"] += scl

    # External clock not used - tie to ground
    pwm["EXTCLK"] += gnd

    # Output Enable - active low, tie to ground to always enable
    pwm["~{OE}"] += gnd

    # Address select pins (directly exposed as nets for jumpers)
    # PCA9685 has A0-A5 for up to 62 addresses
    # Motor Shield V2 exposes A0-A4 as solder jumpers (5 pins = 32 addresses)
    addr_nets = []
    for i in range(5):
        addr_net = Net(f"ADDR_A{i}")
        pwm[f"A{i}"] += addr_net
        addr_nets.append(addr_net)

    # A5 tied to ground (not user-configurable)
    pwm["A5"] += gnd

    # PWM outputs - named for motor driver connections
    # TB6612 #1: Motor 1 (PWM0-2) and Motor 2 (PWM3-5)
    # TB6612 #2: Motor 3 (PWM6-8) and Motor 4 (PWM9-11)
    pwm_nets = []
    for i in range(16):
        net = Net(f"PWM{i}")
        pwm[f"LED{i}"] += net
        pwm_nets.append(net)

    # Decoupling capacitors for PCA9685
    c_dec1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec1[1] += vcc
    c_dec1[2] += gnd

    c_dec2 = Part("Device", "C", value="10uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_dec2[1] += vcc
    c_dec2[2] += gnd

    # I2C pull-up resistors (4.7K to VCC)
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vcc
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vcc
    r_scl[2] += scl

    return pwm_nets, addr_nets


# ============================================================
# Subcircuit: Motor Driver (TB6612FNG)
# ============================================================
@subcircuit
def motor_driver(vcc, gnd, vm, pwm_a, ain1, ain2, pwm_b, bin1, bin2,
                 mot_a1, mot_a2, mot_b1, mot_b2):
    """TB6612FNG dual H-bridge motor driver."""

    drv = Part("Driver_Motor", "TB6612FNG",
               footprint="Package_SO:SSOP-24_5.3x8.2mm_P0.65mm")

    # Logic power
    drv["VCC"] += vcc
    drv["GND"] += gnd

    # Motor power
    drv["VM1"] += vm
    drv["VM2"] += vm
    drv["VM3"] += vm
    drv["PGND1"] += gnd
    drv["PGND2"] += gnd

    # Standby - tie high to enable
    drv["STBY"] += vcc

    # Channel A control inputs
    drv["PWMA"] += pwm_a
    drv["AIN1"] += ain1
    drv["AIN2"] += ain2

    # Channel B control inputs
    drv["PWMB"] += pwm_b
    drv["BIN1"] += bin1
    drv["BIN2"] += bin2

    # Motor outputs
    drv["AO1"] += mot_a1
    drv["AO2"] += mot_a2
    drv["BO1"] += mot_b1
    drv["BO2"] += mot_b2

    # Decoupling caps for motor driver
    c_logic = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_logic[1] += vcc
    c_logic[2] += gnd

    c_motor = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
    c_motor[1] += vm
    c_motor[2] += gnd

    # Bulk cap on motor supply
    c_bulk = Part("Device", "C", value="47uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vm
    c_bulk[2] += gnd


# ============================================================
# Subcircuit: Polarity Protection
# ============================================================
@subcircuit
def polarity_protection(vin, vout, gnd):
    """P-channel MOSFET reverse polarity protection."""

    q = Part("Transistor_FET", "Q_PMOS_GDS",
             footprint="Package_TO_SOT_SMD:SOT-23-3")
    q.value = "Si2301"

    # Gate to ground (turns on when VIN is positive)
    q["G"] += gnd
    # Drain to output (motor voltage bus)
    q["D"] += vout
    # Source to input (external power)
    q["S"] += vin

    # Gate-source resistor (pull-down to ensure MOSFET stays off when unpowered)
    r_gs = Part("Device", "R", value="10K",
                footprint="Resistor_SMD:R_0603_1608Metric")
    r_gs[1] += vin
    r_gs[2] += gnd


# ============================================================
# Subcircuit: Address Select Jumpers
# ============================================================
@subcircuit
def address_jumpers(addr_nets, gnd):
    """5 address select solder jumpers (A0-A4) with pull-down resistors."""

    for i, addr_net in enumerate(addr_nets):
        # Pull-down resistor (default address = all low)
        r = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0603_1608Metric")
        r[1] += addr_net
        r[2] += gnd

        # 2-pin header for solder jumper
        j = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
        j.value = f"ADDR_A{i}"
        j[1] += addr_net
        j[2] += vcc  # Bridge to VCC to set address bit high


# ============================================================
# Subcircuit: Arduino Headers
# ============================================================
@subcircuit
def arduino_headers(vcc, gnd, sda, scl):
    """Arduino Uno R3 shield headers (stackable pin sockets)."""

    # Power header (8-pin)
    j_pwr = Part("Connector_Generic", "Conn_01x08",
                 footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_pwr.value = "PWR_HDR"
    j_pwr[1] += Net("RESET_HDR")   # RESET
    j_pwr[2] += Net("3V3_HDR")     # 3.3V
    j_pwr[3] += vcc                # 5V
    j_pwr[4] += gnd                # GND
    j_pwr[5] += gnd                # GND
    j_pwr[6] += Net("VIN_HDR")     # VIN
    j_pwr[7] += Net("NC_A6")       # NC/A6
    j_pwr[8] += Net("NC_A7")       # NC/A7

    # Analog header (6-pin)
    j_analog = Part("Connector_Generic", "Conn_01x06",
                    footprint="Connector_PinSocket_2.54mm:PinSocket_1x06_P2.54mm_Vertical")
    j_analog.value = "ANALOG_HDR"
    j_analog[1] += Net("A0_HDR")
    j_analog[2] += Net("A1_HDR")
    j_analog[3] += Net("A2_HDR")
    j_analog[4] += Net("A3_HDR")
    j_analog[5] += sda             # A4/SDA
    j_analog[6] += scl             # A5/SCL

    # Digital header high (8-pin: D8-D13, GND, AREF)
    j_dig_hi = Part("Connector_Generic", "Conn_01x08",
                    footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_dig_hi.value = "DIG_HI_HDR"
    j_dig_hi[1] += Net("D8_HDR")
    j_dig_hi[2] += Net("D9_HDR")
    j_dig_hi[3] += Net("D10_HDR")
    j_dig_hi[4] += Net("D11_HDR")
    j_dig_hi[5] += Net("D12_HDR")
    j_dig_hi[6] += Net("D13_HDR")
    j_dig_hi[7] += gnd             # GND
    j_dig_hi[8] += Net("AREF_HDR") # AREF

    # Digital header low (8-pin: D0-D7)
    j_dig_lo = Part("Connector_Generic", "Conn_01x08",
                    footprint="Connector_PinSocket_2.54mm:PinSocket_1x08_P2.54mm_Vertical")
    j_dig_lo.value = "DIG_LO_HDR"
    j_dig_lo[1] += Net("D0_HDR")
    j_dig_lo[2] += Net("D1_HDR")
    j_dig_lo[3] += Net("D2_HDR")
    j_dig_lo[4] += Net("D3_HDR")
    j_dig_lo[5] += Net("D4_HDR")
    j_dig_lo[6] += Net("D5_HDR")
    j_dig_lo[7] += Net("D6_HDR")
    j_dig_lo[8] += Net("D7_HDR")


# ============================================================
# Subcircuit: Motor Terminal Blocks
# ============================================================
@subcircuit
def motor_terminals(mot1_a, mot1_b, mot2_a, mot2_b, mot3_a, mot3_b, mot4_a, mot4_b):
    """Terminal blocks for motor connections (2 pins per motor)."""

    # Motor 1
    j_m1 = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_m1.value = "M1"
    j_m1[1] += mot1_a
    j_m1[2] += mot1_b

    # Motor 2
    j_m2 = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_m2.value = "M2"
    j_m2[1] += mot2_a
    j_m2[2] += mot2_b

    # Motor 3
    j_m3 = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_m3.value = "M3"
    j_m3[1] += mot3_a
    j_m3[2] += mot3_b

    # Motor 4
    j_m4 = Part("Connector_Generic", "Conn_01x02",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_m4.value = "M4"
    j_m4[1] += mot4_a
    j_m4[2] += mot4_b


# ============================================================
# Subcircuit: Power Input Terminal Block
# ============================================================
@subcircuit
def power_input(vm_in, gnd):
    """External motor power input terminal block with bulk cap."""

    j_pwr = Part("Connector_Generic", "Conn_01x02",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
    j_pwr.value = "EXT_PWR"
    j_pwr[1] += vm_in
    j_pwr[2] += gnd

    # Bulk electrolytic on motor power input
    c_bulk = Part("Device", "C", value="100uF",
                  footprint="Capacitor_SMD:C_0805_2012Metric")
    c_bulk[1] += vm_in
    c_bulk[2] += gnd


# ============================================================
# Subcircuit: Status LED
# ============================================================
@subcircuit
def status_led(vcc, gnd):
    """Power indicator LED."""

    led = Part("Device", "LED", value="GREEN",
               footprint="LED_SMD:LED_0603_1608Metric")
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_SMD:R_0603_1608Metric")

    vcc & r_led & led & gnd


# ============================================================
# Build Circuit
# ============================================================

# Motor output nets
m1_a = Net("M1A")
m1_b = Net("M1B")
m2_a = Net("M2A")
m2_b = Net("M2B")
m3_a = Net("M3A")
m3_b = Net("M3B")
m4_a = Net("M4A")
m4_b = Net("M4B")

# PWM driver
pwm_nets, addr_nets = pwm_driver(vcc, gnd, sda, scl)

# Motor driver 1 (Motors 1 and 2)
# PWM0 = M1 speed, PWM1 = M1 AIN2, PWM2 = M1 AIN1
# PWM3 = M2 speed, PWM4 = M2 BIN1, PWM5 = M2 BIN2
motor_driver(vcc, gnd, vm,
             pwm_nets[0], pwm_nets[2], pwm_nets[1],    # Motor 1: PWM, AIN1, AIN2
             pwm_nets[3], pwm_nets[4], pwm_nets[5],    # Motor 2: PWM, BIN1, BIN2
             m1_a, m1_b, m2_a, m2_b)

# Motor driver 2 (Motors 3 and 4)
# PWM6 = M3 speed, PWM7 = M3 AIN2, PWM8 = M3 AIN1
# PWM9 = M4 speed, PWM10 = M4 BIN1, PWM11 = M4 BIN2
motor_driver(vcc, gnd, vm,
             pwm_nets[6], pwm_nets[8], pwm_nets[7],    # Motor 3: PWM, AIN1, AIN2
             pwm_nets[9], pwm_nets[10], pwm_nets[11],  # Motor 4: PWM, BIN1, BIN2
             m3_a, m3_b, m4_a, m4_b)

# Polarity protection (between external power and motor bus)
polarity_protection(vm_in, vm, gnd)

# Address select jumpers
address_jumpers(addr_nets, gnd)

# Arduino shield headers
arduino_headers(vcc, gnd, sda, scl)

# Motor terminal blocks
motor_terminals(m1_a, m1_b, m2_a, m2_b, m3_a, m3_b, m4_a, m4_b)

# External power input
power_input(vm_in, gnd)

# Power LED
status_led(vcc, gnd)

# ============================================================
# Generate Schematic
# ============================================================
generate_schematic(auto_stub=True)
