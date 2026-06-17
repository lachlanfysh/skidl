"""
Arduino 4-Channel Relay Shield
- 4x SPDT relays (SANYOU SRD-05VDC-SL-C)
- BC547/2N2222 NPN transistor drivers with 1K base resistors
- 1N4007 flyback diodes across relay coils
- Status LEDs with 1K series resistors
- Arduino stacking headers (2x 1x8, 2x 1x6)
- 3-pin screw terminals for NO/COM/NC relay outputs
- 5V power from Arduino 5V header pin
- Board size: 120x100mm to accommodate all THT components
"""
from skidl import *

# Power nets
vcc5v = Net("VCC5V")
vcc5v.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# Control signal nets (from Arduino digital pins D4-D7)
ctrl = [Net(f"CTRL{i+1}") for i in range(4)]


@subcircuit
def relay_channel(vin, gnd_net, ctrl_sig, ch_num):
    """Single relay channel: NPN driver + flyback diode + status LED + screw terminal."""

    # NPN transistor (TO-92 through-hole, 2N2222 equivalent)
    q = Part("Transistor_BJT", "BC547",
             footprint="Package_TO_SOT_THT:TO-92_Inline",
             value="2N2222")

    # 1K base resistor
    rb = Part("Device", "R", value="1K",
              footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")

    # SPDT relay - SRD-05VDC-SL-C
    # Pins: 1=coil+, 2=coil-, 3=COM, 4=NC, 5=NO
    rl = Part("Relay", "SANYOU_SRD_Form_C",
              footprint="Relay_THT:Relay_SPDT_SANYOU_SRD_Series_Form_C")

    # 1N4007 flyback diode across coil
    d_fly = Part("Diode", "1N4007",
                 footprint="Diode_THT:D_DO-41_SOD81_P10.16mm_Horizontal")

    # Status LED (3mm red LED)
    led = Part("Device", "LED", value="RED",
               footprint="LED_THT:LED_D3.0mm")

    # 1K LED series resistor
    r_led = Part("Device", "R", value="1K",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")

    # 3-pin output header: NO / COM / NC (pin header, compact silkscreen avoids Edge.Cuts DRC)
    screw = Part("Connector", "Conn_01x03_Pin",
                 footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")

    # Internal net for transistor collector / relay coil low side
    coil_lo = Net(f"COIL{ch_num}_LO")

    # Relay contact nets
    relay_com = Net(f"R{ch_num}_COM")
    relay_no  = Net(f"R{ch_num}_NO")
    relay_nc  = Net(f"R{ch_num}_NC")

    # Base drive: control signal -> 1K resistor -> transistor base
    ctrl_sig += rb[1]
    rb[2]    += q["B"]

    # Transistor: emitter to GND, collector drives relay coil low side
    q["E"] += gnd_net
    q["C"] += coil_lo

    # Relay coil: pin1 (coil+) to VCC, pin2 (coil-) to transistor collector
    vin     += rl[1]
    coil_lo += rl[2]

    # Flyback diode: anode at coil- (coil_lo), cathode at VCC
    d_fly["A"] += coil_lo
    d_fly["K"] += vin

    # LED indicator: VCC -> 1K resistor -> LED anode -> LED cathode -> coil_lo
    # LED lights when transistor is ON (coil_lo pulled low)
    vin         += r_led[1]
    r_led[2]    += led["A"]
    led["K"]    += coil_lo

    # Relay contacts to screw terminal: pin1=NO, pin2=COM, pin3=NC
    relay_no  += rl[5]
    relay_com += rl[3]
    relay_nc  += rl[4]

    screw[1] += relay_no
    screw[2] += relay_com
    screw[3] += relay_nc


# ---- Arduino stacking headers ----

# Power header (1x8): IOREF, RST, 3.3V, 5V, GND, GND, VIN, NC
h_pwr = Part("Connector", "Conn_01x08_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")

ioref        = Net("IOREF")
rst          = Net("RESET")
v33          = Net("3V3")
v5           = Net("5V")
vin_arduino  = Net("VIN")

ioref       += h_pwr[1]
rst         += h_pwr[2]
v33         += h_pwr[3]
v5          += h_pwr[4]
gnd         += h_pwr[5]
gnd         += h_pwr[6]
vin_arduino += h_pwr[7]
# h_pwr[8] is NC

# 5V from Arduino 5V pin powers the relay coils
vcc5v += v5

# Digital low header (1x8): D0-D7
h_dig_lo = Part("Connector", "Conn_01x08_Pin",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")

d0 = Net("D0"); d1 = Net("D1"); d2 = Net("D2"); d3 = Net("D3")
d4 = Net("D4"); d5 = Net("D5"); d6 = Net("D6"); d7 = Net("D7")

h_dig_lo[1] += d0; h_dig_lo[2] += d1; h_dig_lo[3] += d2; h_dig_lo[4] += d3
h_dig_lo[5] += d4; h_dig_lo[6] += d5; h_dig_lo[7] += d6; h_dig_lo[8] += d7

# Relay control from D4-D7
ctrl[0] += d4
ctrl[1] += d5
ctrl[2] += d6
ctrl[3] += d7

# Digital high header (1x8): D8-D13 + AREF + GND
h_dig_hi = Part("Connector", "Conn_01x08_Pin",
                footprint="Connector_PinHeader_2.54mm:PinHeader_1x08_P2.54mm_Vertical")

d8 = Net("D8"); d9 = Net("D9"); d10 = Net("D10")
d11 = Net("D11"); d12 = Net("D12"); d13 = Net("D13")
aref = Net("AREF")

h_dig_hi[1] += d8;   h_dig_hi[2] += d9;   h_dig_hi[3] += d10;  h_dig_hi[4] += d11
h_dig_hi[5] += d12;  h_dig_hi[6] += d13;  h_dig_hi[7] += aref; h_dig_hi[8] += gnd

# Analog header (1x6): A0-A5
h_ana = Part("Connector", "Conn_01x06_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")

a0 = Net("A0"); a1 = Net("A1"); a2 = Net("A2")
a3 = Net("A3"); a4 = Net("A4"); a5 = Net("A5")

h_ana[1] += a0; h_ana[2] += a1; h_ana[3] += a2
h_ana[4] += a3; h_ana[5] += a4; h_ana[6] += a5

# Communication header (1x6): SDA, SCL, AREF, GND, 5V, 3V3
h_com = Part("Connector", "Conn_01x06_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x06_P2.54mm_Vertical")

sda = Net("SDA"); scl = Net("SCL")

h_com[1] += sda;  h_com[2] += scl;  h_com[3] += aref
h_com[4] += gnd;  h_com[5] += v5;   h_com[6] += v33

# ---- Instantiate 4 relay channels ----
for i in range(4):
    relay_channel(vcc5v, gnd, ctrl[i], i + 1)

# ---- Bulk decoupling capacitor ----
c_bulk = Part("Device", "C", value="100nF",
              footprint="Capacitor_THT:C_Disc_D5.0mm_W2.5mm_P5.00mm")
c_bulk[1] += vcc5v
c_bulk[2] += gnd

# ---- Board floorplan ----
# 120x100mm comfortably fits all THT parts (compact_outline was 128x95mm at 100x80 so we need more)
# Relays in 2x2 grid center, headers bottom, screw terminals top edge

EDA_FLOORPLAN = {
    "outline": {
        "width_mm": 130.0,
        "height_mm": 100.0,
        "corner_radius_mm": 0
    }
}
