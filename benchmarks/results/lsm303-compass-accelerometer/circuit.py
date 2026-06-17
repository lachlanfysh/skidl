"""
Adafruit LSM303 Compass/Accelerometer Breakout
LSM303AGR triple-axis accelerometer + magnetometer, I2C, 5V-tolerant.
3.3V AP2112K LDO regulator, BSS138 bidirectional I2C level shifter, 8-pin header.

Generated via eda-mcp server (job: 28838df81749, run_id: ca4ff0e7451a)
Sensor: LSM303AGRTR (LCSC C126671), Package_LGA:Kionix_LGA-12_2x2mm_P0.5mm_LayoutBorder2x4y
Board: 36x24mm, layout score: 59.2/100
"""

from skidl import *

vin  = Net("VIN"); vin.drive = POWER
v33  = Net("3V3"); v33.drive = POWER
gnd  = Net("GND"); gnd.drive = POWER

# I2C level-shifted nets (5V side = header, 3.3V side = sensor)
sda_5v  = Net("SDA")
scl_5v  = Net("SCL")
sda_33  = Net("SDA_33")
scl_33  = Net("SCL_33")

drdy = Net("DRDY")   # INT_MAG/DRDY (magnetometer data ready)
int1 = Net("INT1")   # INT_1_XL (accelerometer interrupt 1)
int2 = Net("INT2")   # INT_2_XL (accelerometer interrupt 2)


@subcircuit
def ldo_3v3(vin_net, vout_net, gnd_net):
    """AP2112K-3.3 LDO: 600mA, SOT-23-5. EN tied to VIN for always-on."""
    u = Part("Regulator_Linear", "AP2112K-3.3",
             footprint="Package_TO_SOT_SMD:SOT-23-5")
    u["VIN"] += vin_net
    u["EN"]  += vin_net
    u["GND"] += gnd_net
    u["VOUT"] += vout_net

    cin = Part("Device", "C", value="1uF",
               footprint="Capacitor_SMD:C_0603_1608Metric")
    cin[1] += vin_net; cin[2] += gnd_net

    cout = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    cout[1] += vout_net; cout[2] += gnd_net


@subcircuit
def i2c_shifter(sda_lv, scl_lv, sda_hv, scl_hv, v_low, v_high, gnd_net):
    """BSS138 bidirectional open-drain I2C level shifter (3.3V <-> 5V).
    Gate tied to low-voltage rail. Pull-ups on both sides of each line."""
    q_sda = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_sda["G"] += v_low
    q_sda["S"] += sda_lv
    q_sda["D"] += sda_hv

    r_sda_lo = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda_lo[1] += v_low; r_sda_lo[2] += sda_lv

    r_sda_hi = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda_hi[1] += v_high; r_sda_hi[2] += sda_hv

    q_scl = Part("Transistor_FET", "BSS138",
                 footprint="Package_TO_SOT_SMD:SOT-23")
    q_scl["G"] += v_low
    q_scl["S"] += scl_lv
    q_scl["D"] += scl_hv

    r_scl_lo = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl_lo[1] += v_low; r_scl_lo[2] += scl_lv

    r_scl_hi = Part("Device", "R", value="4.7K",
                    footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl_hi[1] += v_high; r_scl_hi[2] += scl_hv


@subcircuit
def lsm303agr(sda_net, scl_net, drdy_net, int1_net, int2_net, vdd_net, gnd_net):
    """LSM303AGRTR (LCSC C126671) 12-pin LGA 2x2mm 0.5mm pitch.
    I2C mode: CS_XL and CS_MAG tied to VDD.
    C1 pin (internal LDO bypass) gets 100nF cap per datasheet.
    Kionix footprint variant includes routing border for trace escape from fine-pitch pads.
    """
    u = Part("C126671", "LSM303AGRTR",
             footprint="Package_LGA:Kionix_LGA-12_2x2mm_P0.5mm_LayoutBorder2x4y")

    u["SCL/SPC"]     += scl_net
    u["SDA/SDI/SDO"] += sda_net

    # I2C mode: tie CS pins to VDD
    u["CS_XL"]  += vdd_net
    u["CS_MAG"] += vdd_net

    # Power (GND matches both pin 6 and pin 8 in the LGA-12 package)
    u["Vdd"]    += vdd_net
    u["Vdd_IO"] += vdd_net
    u["GND"]    += gnd_net

    u["INT_MAG/DRDY"] += drdy_net
    u["INT_1_XL"]     += int1_net
    u["INT_2_XL"]     += int2_net

    # Internal LDO bypass cap per LSM303AGR datasheet
    c1_net = Net("C1_BYPASS")
    u["C1"] += c1_net
    c_c1 = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0603_1608Metric")
    c_c1[1] += c1_net; c_c1[2] += gnd_net

    c_vdd1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd1[1] += vdd_net; c_vdd1[2] += gnd_net

    c_vdd2 = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_vdd2[1] += vdd_net; c_vdd2[2] += gnd_net


@subcircuit
def breakout_header(vin_net, v33_net, gnd_net,
                    sda_net, scl_net, drdy_net, int1_net, int2_net):
    """1x8 2.54mm pin header: VIN, 3V3, GND, SCL, SDA, DRDY, INT1, INT2"""
    hdr = Part("Connector_Generic", "Conn_01x08",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")
    hdr[1] += vin_net
    hdr[2] += v33_net
    hdr[3] += gnd_net
    hdr[4] += scl_net
    hdr[5] += sda_net
    hdr[6] += drdy_net
    hdr[7] += int1_net
    hdr[8] += int2_net


# Instantiate all blocks
ldo_3v3(vin, v33, gnd)
i2c_shifter(sda_33, scl_33, sda_5v, scl_5v, v33, vin, gnd)
lsm303agr(sda_33, scl_33, drdy, int1, int2, v33, gnd)
breakout_header(vin, v33, gnd, sda_5v, scl_5v, drdy, int1, int2)

EDA_FLOORPLAN = {
    "outline": {"width_mm": 36, "height_mm": 24, "corner_radius_mm": 1.0},
}
