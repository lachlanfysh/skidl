from skidl import *

vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

@subcircuit
def timer_block(vcc, gnd):
    u1 = Part("Timer", "NE555P", footprint="Package_DIP:DIP-8_W7.62mm")

    r1 = Part("Device", "R", value="1K", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r2 = Part("Device", "R", value="10K", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")

    rv1 = Part("Device", "R_Potentiometer", value="100K", footprint="Potentiometer_THT:Potentiometer_Bourns_3386P_Vertical")

    c1 = Part("Device", "C_Polarized", value="10uF", footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
    c2 = Part("Device", "C", value="100nF", footprint="Capacitor_THT:C_Disc_D3.0mm_W1.6mm_P2.50mm")
    c_bulk = Part("Device", "C_Polarized", value="10uF", footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
    c3 = Part("Device", "C", value="10nF", footprint="Capacitor_THT:C_Disc_D3.0mm_W1.6mm_P2.50mm")

    d1 = Part("Device", "LED", footprint="LED_THT:LED_D5.0mm")
    r3 = Part("Device", "R", value="330", footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")

    vcc += u1["VCC"]
    gnd += u1["GND"]
    vcc += u1["R"]

    vcc += c2[1]; gnd += c2[2]
    vcc += c_bulk[1]; gnd += c_bulk[2]

    u1["CV"] += c3[1]; gnd += c3[2]

    dis_node = Net("DIS_NODE")
    thr_node = Net("THR_NODE")
    pot_wiper = Net("POT_WIPER")

    vcc += r1[1]
    dis_node += r1[2]

    dis_node += rv1[1]
    pot_wiper += rv1[2]
    pot_wiper += rv1[3]

    pot_wiper += r2[1]
    thr_node += r2[2]

    dis_node += u1["DIS"]
    thr_node += u1["THR"], u1["TR"]

    thr_node += c1[1]
    gnd += c1[2]

    output = Net("OUTPUT")
    output += u1["Q"]
    output += r3[1]
    d1["A"] += r3[2]
    d1["K"] += gnd

timer_block(vcc, gnd)

# 2-pin header for power input (tight silkscreen, fully contained on board)
j1 = Part("Connector_Generic", "Conn_01x02", footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
j1.edge_preference = "left"
vcc += j1[1]
gnd += j1[2]

EDA_FLOORPLAN = {
    "outline": {"width_mm": 60, "height_mm": 45, "corner_radius_mm": 2},
}
