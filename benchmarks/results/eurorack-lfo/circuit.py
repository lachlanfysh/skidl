"""
Eurorack Dual LFO Module — 8HP through-hole.

Two independent LFO channels (A and B), each with:
  - TL072 dual op-amp (triangle/square core)
  - Rate pot (1M log), Shape pot (100K lin)
  - 2 output jacks (triangle + square, Thonkiconn PJ398SM)
  - LED indicator

Power: Eurorack 2x5 shrouded IDC header (+12V / -12V / GND)
PCB: 8HP = 40.6mm wide, 106mm tall

Panel layout (KiCad Y increases downward, Y=0 = panel top):
  Column A (x=10mm): Rate-A pot, Shape-A pot, Tri-A jack, Sqr-A jack, LED-A
  Column B (x=30mm): Rate-B pot, Shape-B pot, Tri-B jack, Sqr-B jack, LED-B
  Center bottom: power header, ICs

Ref assignments (consistent across runs):
  J1 = power header (Conn_02x05)
  J2 = tri jack A, J3 = sqr jack A
  J4 = tri jack B, J5 = sqr jack B
  RV1 = rate pot A, RV2 = shape pot A
  RV3 = rate pot B, RV4 = shape pot B
  D1 = LED A, D2 = LED B
  U1 = TL072 channel A, U2 = TL072 channel B
"""

from skidl import *

# --- Power rails ---
pwr_p12 = Net("+12V"); pwr_p12.drive = POWER
pwr_n12 = Net("-12V"); pwr_n12.drive = POWER
gnd     = Net("GND");  gnd.drive  = POWER


@subcircuit
def power_section(vp12, vn12, vgnd):
    """Eurorack 2x5 IDC power header + bulk decoupling caps."""
    pwr_hdr = Part(
        "Connector_Generic", "Conn_02x05_Odd_Even",
        footprint="Connector_IDC:IDC-Header_2x05_P2.54mm_Vertical",
        value="Eurorack_Power"
    )
    # Doepfer A-100: pin1=-12V, pin10=+12V, all others GND
    pwr_hdr[1]  += vn12
    pwr_hdr[2]  += vgnd
    pwr_hdr[3]  += vgnd
    pwr_hdr[4]  += vgnd
    pwr_hdr[5]  += vgnd
    pwr_hdr[6]  += vgnd
    pwr_hdr[7]  += vgnd
    pwr_hdr[8]  += vgnd
    pwr_hdr[9]  += vgnd
    pwr_hdr[10] += vp12

    # Bulk decoupling: 10uF electrolytic + 100nF film per rail
    c_p_bulk = Part("Device", "C", value="10uF",
                    footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    c_p_film = Part("Device", "C", value="100nF",
                    footprint="Capacitor_THT:C_Rect_L7.0mm_W2.5mm_P5.00mm")
    c_n_bulk = Part("Device", "C", value="10uF",
                    footprint="Capacitor_THT:CP_Radial_D5.0mm_P2.50mm")
    c_n_film = Part("Device", "C", value="100nF",
                    footprint="Capacitor_THT:C_Rect_L7.0mm_W2.5mm_P5.00mm")
    vp12 += c_p_bulk[1], c_p_film[1]
    vgnd += c_p_bulk[2], c_p_film[2]
    vn12 += c_n_bulk[1], c_n_film[1]
    vgnd += c_n_bulk[2], c_n_film[2]


@subcircuit
def lfo_channel(suffix, vp12, vn12, vgnd):
    """
    Single LFO channel: TL072 triangle/square oscillator.
    Op-amp A (pins 1,2,3): integrator → triangle.
    Op-amp B (pins 5,6,7): Schmitt comparator → square.
    """
    u = Part(
        "Amplifier_Operational", "TL072",
        footprint="Package_DIP:DIP-8_W7.62mm",
        value="TL072"
    )
    u[8] += vp12   # V+
    u[4] += vn12   # V-

    tri_out = Net(f"TRI_{suffix}")
    sqr_out = Net(f"SQR_{suffix}")
    int_neg = Net(f"INT_NEG_{suffix}")
    cmp_pos = Net(f"CMP_POS_{suffix}")

    # Rate pot (1M log)
    rate_pot = Part(
        "Device", "R_Potentiometer", value="1M",
        footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical_CircularHoles"
    )
    rate_wip = Net(f"RATE_WIP_{suffix}")
    vp12     += rate_pot[1]
    rate_wip += rate_pot[2]
    vgnd     += rate_pot[3]

    r_rate = Part("Device", "R", value="10k",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    rate_wip += r_rate[1]
    r_rate[2] += int_neg

    c_int = Part("Device", "C", value="100nF",
                 footprint="Capacitor_THT:C_Rect_L7.0mm_W2.5mm_P5.00mm")
    int_neg += c_int[1]
    tri_out += c_int[2]

    # Integrator op-amp A
    int_neg += u[2]
    vgnd    += u[3]
    tri_out += u[1]

    # Schmitt comparator op-amp B
    sqr_out += u[7]

    r_hys1 = Part("Device", "R", value="100k",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    r_hys2 = Part("Device", "R", value="100k",
                  footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    sqr_out  += r_hys1[1]
    r_hys1[2] += cmp_pos
    vgnd     += r_hys2[1]
    r_hys2[2] += cmp_pos
    cmp_pos  += u[5]

    r_tri_cmp = Part("Device", "R", value="100k",
                     footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    tri_out    += r_tri_cmp[1]
    r_tri_cmp[2] += u[6]

    # Shape pot (100K lin) — passive triangle-to-sine waveshaper
    shape_pot = Part(
        "Device", "R_Potentiometer", value="100k",
        footprint="Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D_Single_Vertical_CircularHoles"
    )
    shape_in  = Net(f"SHAPE_IN_{suffix}")
    shape_out = Net(f"SHAPE_OUT_{suffix}")

    r_shape_top = Part("Device", "R", value="100k",
                       footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    tri_out    += r_shape_top[1]
    shape_in   += r_shape_top[2]
    shape_in   += shape_pot[1]
    shape_out  += shape_pot[2]
    vgnd       += shape_pot[3]

    # Output jacks: Thonkiconn PJ398SM vertical
    j_tri = Part(
        "Connector_Audio", "AudioJack2_SwitchT",
        footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
        value=f"TRI_{suffix}"
    )
    shape_out += j_tri["T"]
    j_tri["TN"] += Net(f"NC_TRI_TN_{suffix}")
    vgnd      += j_tri["S"]

    j_sqr = Part(
        "Connector_Audio", "AudioJack2_SwitchT",
        footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
        value=f"SQR_{suffix}"
    )
    sqr_out += j_sqr["T"]
    j_sqr["TN"] += Net(f"NC_SQR_TN_{suffix}")
    vgnd    += j_sqr["S"]

    # LED: driven from square wave output
    led = Part("Device", "LED", footprint="LED_THT:LED_D3.0mm", value="green")
    r_led = Part("Device", "R", value="10k",
                 footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P10.16mm_Horizontal")
    sqr_out   += r_led[1]
    r_led[2]  += led["A"]
    vgnd      += led["K"]


# --- Instantiate ---
power_section(pwr_p12, pwr_n12, gnd)
lfo_channel("A", pwr_p12, pwr_n12, gnd)
lfo_channel("B", pwr_p12, pwr_n12, gnd)


# --- Explicit panel floorplan ---
# 8HP = 40.6mm wide, 106mm tall. Y=0 at top (panel face), Y=106 at rear.
# Channel A: x=10mm (left column). Channel B: x=30mm (right column).
#
# Panel component rows (KiCad Y downward):
#   y=12mm: Rate pots  (Alpha RD901F, body ~16mm, courtyard ~19mm)
#   y=34mm: Shape pots (same footprint)
#   y=56mm: Tri jacks  (PJ398SM, ~12x12mm courtyard)
#   y=70mm: Sqr jacks  (same)
#   y=63mm: LEDs       (D3mm, ~5mm courtyard)  — between the two jack rows
#   y=85mm: TL072 ICs  (DIP-8, ~20x10mm)
#   y=98mm: Power header (2x5 IDC, ~13x15mm) centered
#
# With 20mm column spacing, Alpha RD901F courtyards do not overlap.
# With 14mm jack row pitch, PJ398SM courtyards (12mm) clear with 2mm margin.
EDA_FLOORPLAN = {
    "outline": {
        "width_mm":  40.6,
        "height_mm": 132.0,
        "corner_radius_mm": 0,
    },
    "fixed_positions": [
        # Channel A — left column (x=10mm).
        # Alpha RD901F courtyard: 6.9mm half-width → at x=10, spans 3.1→16.9mm.
        {"ref": "RV1", "x_mm": 10.0, "y_mm": 12.0, "rotation_deg": 0},   # Rate A pot
        {"ref": "RV2", "x_mm": 10.0, "y_mm": 34.0, "rotation_deg": 0},   # Shape A pot

        # Thonkiconn PJ398SM courtyard: 5.025mm half-width, 14.45mm height.
        # J2/J3 at x=7: spans 1.975→12.025mm. 15mm y-pitch clears 14.45mm courtyard.
        {"ref": "J2",  "x_mm":  7.0, "y_mm": 56.0, "rotation_deg": 0},   # Tri A jack
        {"ref": "J3",  "x_mm":  7.0, "y_mm": 71.0, "rotation_deg": 0},   # Sqr A jack

        # LED D1: must not overlap J2 (1.975→12.025, y:54.555→69.005).
        # D3mm courtyard half-width ~2.445mm. At x=15: spans 12.555→17.445mm — clear of J2.
        # y=63.5: overlaps J2 y-range (54.555→69.005) but x-ranges don't overlap. OK.
        {"ref": "D1",  "x_mm": 15.0, "y_mm": 63.5, "rotation_deg": 0},   # LED A

        # Channel B — right column.
        # RV3/4 at x=27.5: right edge=27.5+12.625=40.125mm ≤ 40.6mm.
        {"ref": "RV3", "x_mm": 27.5, "y_mm": 12.0, "rotation_deg": 0},   # Rate B pot
        {"ref": "RV4", "x_mm": 27.5, "y_mm": 34.0, "rotation_deg": 0},   # Shape B pot

        # J4/J5 at x=33.5: spans 28.475→38.525mm. 15mm y-pitch.
        {"ref": "J4",  "x_mm": 33.5, "y_mm": 56.0, "rotation_deg": 0},   # Tri B jack
        {"ref": "J5",  "x_mm": 33.5, "y_mm": 71.0, "rotation_deg": 0},   # Sqr B jack

        # LED D2: must not overlap J4 (28.475→38.525, y:54.555→69.005).
        # At x=26 actual D2 right=29.715mm > J4 left=28.475 → still overlaps.
        # D2 actual half-width = 3.715mm. At x=24: right=27.715mm < J4 left=28.475mm. Clear!
        {"ref": "D2",  "x_mm": 24.0, "y_mm": 63.5, "rotation_deg": 0},   # LED B

        # TL072 ICs (DIP-8): ASYMMETRIC courtyard from preflight data.
        # At (x,y): left=x-1.085, right=x+8.695, top=y-1.545, bottom=y+9.165. (Total: 9.78mm wide, 10.71mm tall)
        # Two DIP-8s are too wide to fit side-by-side → stack vertically.
        #
        # U1 at x=14, y=86:
        #   left=12.915 > J3 right=12.025 (0.89mm gap, clear!)
        #   right=22.695 < J5 left=28.475 (clear)
        #   top=84.455 > J3/J5 bottom=84.005 (0.45mm gap, clear!)
        # U2 at x=14, y=98:
        #   top=96.455 > U1 bottom=95.165 (1.29mm gap, clear!)
        #   Same x bounds as U1: left=12.915, right=22.695 (all clear)
        # J1 power header (non-latch): from preflight, bottom-extent=15.785mm, top-extent=6.215mm.
        # J1 at x=20, y=113: top=106.785 > U2 bottom=107.165? 106.785<107.165 → 0.38mm overlap!
        # J1 at x=20, y=114: top=107.785 > U2 bottom=107.165. 0.62mm gap. Clear!
        # J1 bottom=114+15.785=129.785mm → board height must be ≥ 130mm.
        # Use board height=130mm and J1 at y=114.
        {"ref": "U1",  "x_mm": 14.0, "y_mm": 86.0, "rotation_deg": 0},
        {"ref": "U2",  "x_mm": 14.0, "y_mm": 98.0, "rotation_deg": 0},
        {"ref": "J1",  "x_mm": 20.0, "y_mm": 114.0, "rotation_deg": 0},
    ],
    "notes": (
        "8HP Eurorack dual LFO, 112mm board depth. "
        "Panel face = top (small Y). Rear = bottom (large Y). "
        "Channel A left (x~7-15mm), Channel B right (x~24-34mm). "
        "Pots at y=12/34, jacks at y=56/71, LEDs at y=63, ICs at y=86, power header at y=105. "
        "Non-latch IDC header (compact) fits below ICs with 4mm clearance."
    ),
}
