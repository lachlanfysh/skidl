"""
BNO055 9-DOF IMU Breakout Board
Bosch BNO055 smart 9-DOF sensor with on-chip sensor fusion.
Accelerometer, magnetometer, gyroscope. Outputs quaternions, Euler angles,
linear acceleration. I2C interface, 3.3V, 32.768kHz crystal. Breakout with header.

Generated via eda-mcp server (run_id: 3774d4844986)
"""

from skidl import *

vin = Net("VIN"); vin.drive = POWER
v33 = Net("3V3"); v33.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
sda = Net("SDA")
scl = Net("SCL")
n_int = Net("INT")
n_rst = Net("RST")
xin32  = Net("XTAL_IN")
xout32 = Net("XTAL_OUT")

@subcircuit
def power_reg(vin, v33, gnd):
    """AP2112K-3.3 LDO: 5V VIN -> 3.3V out, 600mA"""
    ldo  = Part("Regulator_Linear", "AP2112K-3.3", footprint="Package_TO_SOT_SMD:SOT-23-5")
    cin  = Part("Device", "C", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")
    cout = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    vin += ldo["VIN"], ldo["EN"], cin[1]
    gnd += ldo["GND"], cin[2], cout[2]
    v33 += ldo["VOUT"], cout[1]

@subcircuit
def imu_sensor(v33, gnd, sda, scl, n_int, n_rst, xin32, xout32):
    """BNO055 LGA-28: 9-DOF sensor fusion IC, I2C mode (PS0=PS1=GND)"""
    imu  = Part("Sensor_Motion", "BNO055", footprint="Package_LGA:LGA-28_5.2x3.8mm_P0.5mm")
    c1   = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    c2   = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    ccap = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    # Power: VDD and VDDIO both on 3.3V
    v33 += imu["VDD"], imu["VDDIO"], c1[1], c2[1]
    gnd += imu["GND"], imu["GNDIO"], c1[2], c2[2]
    # CAP pin internal regulator bypass
    imu["CAP"] += ccap[1]; gnd += ccap[2]
    # I2C protocol mode: PS0=GND, PS1=GND
    gnd += imu["PS0"], imu["PS1"]
    # I2C bus (COM0=SDA, COM3=SCL in I2C mode)
    sda    += imu["COM0"]
    scl    += imu["COM3"]
    imu["COM1"] += NC; imu["COM2"] += NC
    # Interrupt and reset
    n_int  += imu["INT"]
    n_rst  += imu["~{RESET}"]
    # Unused pins
    imu["BL_IND"] += NC; imu["~{BOOT_LOAD_PIN}"] += NC
    # 32.768kHz crystal oscillator pins
    xin32  += imu["XIN32"]
    xout32 += imu["XOUT32"]

@subcircuit
def crystal_osc(gnd, xin32, xout32):
    """32.768kHz crystal with 12pF load caps for BNO055 RTC"""
    xtal = Part("Device", "Crystal", value="32.768kHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    cl1  = Part("Device", "C", value="12pF", footprint="Capacitor_SMD:C_0402_1005Metric")
    cl2  = Part("Device", "C", value="12pF", footprint="Capacitor_SMD:C_0402_1005Metric")
    xin32  += xtal[1], cl1[1]; gnd += cl1[2]
    xout32 += xtal[2], cl2[1]; gnd += cl2[2]

@subcircuit
def i2c_and_header(vin, v33, gnd, sda, scl, n_int, n_rst):
    """I2C pull-ups, RESET pull-up, 8-pin breakout header"""
    # 4.7K I2C pull-ups on SDA and SCL
    r_sda = Part("Device", "R", value="4.7K", footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl = Part("Device", "R", value="4.7K", footprint="Resistor_SMD:R_0603_1608Metric")
    # 10K RESET pull-up
    r_rst = Part("Device", "R", value="10K",  footprint="Resistor_SMD:R_0603_1608Metric")
    v33 += r_sda[1], r_scl[1], r_rst[1]
    sda   += r_sda[2]; scl += r_scl[2]; n_rst += r_rst[2]
    # 8-pin breakout header: VIN, 3V3, GND, SDA, SCL, INT, RST, GND
    hdr = Part("Connector_Generic", "Conn_01x08",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
    vin   += hdr[1]
    v33   += hdr[2]
    gnd   += hdr[3], hdr[8]
    sda   += hdr[4]
    scl   += hdr[5]
    n_int += hdr[6]
    n_rst += hdr[7]

# Instantiate all blocks
power_reg(vin, v33, gnd)
imu_sensor(v33, gnd, sda, scl, n_int, n_rst, xin32, xout32)
crystal_osc(gnd, xin32, xout32)
i2c_and_header(vin, v33, gnd, sda, scl, n_int, n_rst)
