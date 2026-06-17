from skidl import *

set_default_tool(KICAD9)

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# I2C bus
sda = Net("SDA")
scl = Net("SCL")

# Address select net (AD pin)
ad_net = Net("AD")

# SDB (shutdown bar) - active high, tied to VCC to keep chip enabled
sdb_net = Net("SDB")
sdb_net += vcc

# LED matrix outputs: 9 column-anodes (CA) and 9 column-cathodes/rows (CB)
ca = [Net(f"CA{i}") for i in range(1, 10)]
cb = [Net(f"CB{i}") for i in range(1, 10)]

# Ancillary IC nets
in_net = Net("IN")        # current reference input (sets max LED current via R_EXT)
intb_net = Net("INTB")   # interrupt bar output (open-drain, active low)
cfilt_net = Net("CFILT") # internal charge pump filter cap pin


@subcircuit
def is31fl3731_core(vcc, gnd, sda, scl, ad_net, sdb_net, ca, cb, in_net, intb_net, cfilt_net):
    # IS31FL3731 in SSOP-28 (hand-solderable variant)
    u1 = Part("Driver_LED", "IS31FL3731-SA",
              footprint="Package_SO:SSOP-28_5.3x10.2mm_P0.65mm")
    u1["VCC"] += vcc
    u1["GND"] += gnd
    u1["SDA"] += sda
    u1["SCL"] += scl
    u1["AD"] += ad_net
    u1["~{SDB}"] += sdb_net
    u1["~{INTB}"] += intb_net
    u1["IN"] += in_net
    u1["C_FILT"] += cfilt_net

    for i, net in enumerate(ca, 1):
        u1[f"CA{i}"] += net

    for i, net in enumerate(cb, 1):
        u1[f"CB{i}"] += net

    # R_EXT: 10k sets ~10mA max per LED channel
    r_ext = Part("Device", "R", value="10k",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_ext[1] += in_net
    r_ext[2] += gnd

    # C_FILT: 220nF charge pump filter cap
    c_filt = Part("Device", "C", value="220nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_filt[1] += cfilt_net
    c_filt[2] += gnd

    # VCC decoupling: 100nF (auto-placed near IC by layout engine)
    c_dec1 = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0603_1608Metric")
    c_dec1[1] += vcc
    c_dec1[2] += gnd

    # Bulk capacitor: 10uF
    c_bulk = Part("Device", "C_Polarized", value="10uF",
                  footprint="Capacitor_SMD:CP_Elec_5x5.3")
    c_bulk[1] += vcc
    c_bulk[2] += gnd


@subcircuit
def i2c_pullups(vcc, sda, scl):
    # 4.7k pull-ups for I2C bus (3.3V operation)
    r_sda = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_sda[1] += vcc
    r_sda[2] += sda

    r_scl = Part("Device", "R", value="4.7k",
                 footprint="Resistor_SMD:R_0603_1608Metric")
    r_scl[1] += vcc
    r_scl[2] += scl


@subcircuit
def address_select(vcc, gnd, ad_net):
    # 3-position solder jumper for I2C address selection
    # IS31FL3731 AD pin: GND=0x74, float=0x75, VCC=0x76
    # Default (bridged pins 1-2): AD tied to GND, address 0x74
    jp1 = Part("Jumper", "SolderJumper_3_Bridged12",
               footprint="Jumper:SolderJumper-3_P1.3mm_Bridged12_Pad1.0x1.5mm")
    jp1[1] += gnd
    jp1[2] += ad_net
    jp1[3] += vcc


@subcircuit
def stemma_qt_connectors(vcc, gnd, sda, scl):
    # Two STEMMA QT / Qwiic JST SH 4-pin connectors for solderless chaining
    # Standard Qwiic pinout: 1=GND, 2=3.3V, 3=SDA, 4=SCL
    qt1 = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    qt1[1] += gnd
    qt1[2] += vcc
    qt1[3] += sda
    qt1[4] += scl

    qt2 = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    qt2[1] += gnd
    qt2[2] += vcc
    qt2[3] += sda
    qt2[4] += scl


@subcircuit
def led_matrix_header(ca, cb):
    # 2x9 pin header for LED matrix connections (18 pins: CA1-CA9 + CB1-CB9)
    # Odd pins = CA (anodes), even pins = CB (cathodes/row lines)
    hdr = Part("Connector_Generic", "Conn_02x09_Odd_Even",
               footprint="Connector_PinHeader_2.54mm:PinHeader_2x09_P2.54mm_Vertical")
    for i in range(1, 10):
        hdr[2*i - 1] += ca[i-1]
        hdr[2*i]     += cb[i-1]


# Instantiate all blocks
is31fl3731_core(vcc, gnd, sda, scl, ad_net, sdb_net, ca, cb, in_net, intb_net, cfilt_net)
i2c_pullups(vcc, sda, scl)
address_select(vcc, gnd, ad_net)
stemma_qt_connectors(vcc, gnd, sda, scl)
led_matrix_header(ca, cb)
