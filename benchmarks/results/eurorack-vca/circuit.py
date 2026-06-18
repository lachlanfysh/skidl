"""
Eurorack Linear VCA — 4HP (20.32mm x 101.6mm), through-hole.
Uses LM13700 dual OTA (OTA-A half only, pins 1-8).

Design notes:
- 4HP PCB (20.32mm) is too narrow for any panel-mount or trimmer pot alongside
  DIP-16 + 3x PJ398SM jacks + IDC header. CV input is direct (no attenuator).
  For attenuation, use an external attenuverter module upstream.
- Signal in, CV in, audio out via Thonkiconn PJ398SM jacks (stacked vertically).
- Power: Eurorack 2x5 shrouded IDC header (+12V/-12V/GND).

LM13700 DIP-16 pinout (OTA-A half used):
  1=IABC  2=DIODE_BIAS  3=+IN  4=-IN  5=OTA_OUT
  6=V-    7=BUF_IN_A   8=BUF_OUT_A   9=BUF_OUT_B  10=BUF_IN_B
  11=V+  12=OTA_OUT_B  13=-IN_B  14=+IN_B  15=DIODE_BIAS_B  16=IABC_B

Footprint bounds (from prior server output, used for EDA_FLOORPLAN):
  PJ398SM jack: 10.05mm wide × 14.45mm tall (centered origin)
  DIP-16 U1:    top=origin-1.545mm, bottom=origin+19.325mm (20.87mm total)
  IDC 2x5 J4:   top=origin-5.625mm, bottom=origin+15.785mm (21.41mm total)
"""

from skidl import *

# --- Power rails ---
vp12 = Net("+12V");  vp12.drive = POWER
vm12 = Net("-12V");  vm12.drive = POWER
gnd  = Net("GND");   gnd.drive  = POWER

# --- Signal / CV nets ---
sig_in    = Net("SIG_IN")
sig_ac    = Net("SIG_AC")
cv_in     = Net("CV_IN")
cv_bias   = Net("CV_BIAS")
ota_out   = Net("OTA_OUT")
audio_out = Net("AUDIO_OUT")

# ── Panel jacks (all centered at x≈10.16, stacked vertically) ────────────────

j_sig = Part("Connector_Audio", "AudioJack2_SwitchT",
             footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
             value="SIG_IN")
j_sig["T"] += sig_in
j_sig["S"] += gnd
j_sig["TN"] += gnd   # sleeve normalised to GND when unplugged

j_cv = Part("Connector_Audio", "AudioJack2_SwitchT",
            footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
            value="CV_IN")
j_cv["T"] += cv_in
j_cv["S"] += gnd
j_cv["TN"] += gnd

j_out = Part("Connector_Audio", "AudioJack2_SwitchT",
             footprint="Connector_Audio:Jack_3.5mm_QingPu_WQP-PJ398SM_Vertical_CircularHoles",
             value="OUT")
j_out["T"] += audio_out
j_out["S"] += gnd
j_out["TN"] += gnd

# ── CV signal chain ───────────────────────────────────────────────────────────

# CV input protection resistor (10K, vertical mount for narrow PCB)
r_cv = Part("Device", "R", value="10K",
            footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical")
r_cv[1] += cv_in
r_cv[2] += cv_bias

# IABC static bias resistor from +12V (33K, sets max transconductance)
r_iabc = Part("Device", "R", value="33K",
              footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical")
r_iabc[1] += vp12
r_iabc[2] += cv_bias

# ── LM13700 OTA-A VCA ────────────────────────────────────────────────────────

u1 = Part("Amplifier_Operational", "LM13700",
          footprint="Package_DIP:DIP-16_W7.62mm",
          value="LM13700")

u1[11] += vp12    # V+
u1[6]  += vm12    # V-
u1[3]  += sig_ac  # +IN: AC-coupled audio
u1[4]  += gnd     # -IN: via bias resistor
u1[1]  += cv_bias # IABC: CV-controlled transconductance
u1[2]  += cv_bias # DIODE_BIAS: shared with IABC for linearization
u1[5]  += ota_out # current output

# -IN bias resistor (100K, matched to input impedance, vertical)
r_neg = Part("Device", "R", value="100K",
             footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical")
r_neg[1] += u1[4]
r_neg[2] += gnd

# OTA load resistor: converts output current to voltage (10K, vertical)
r_load = Part("Device", "R", value="10K",
              footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical")
r_load[1] += ota_out
r_load[2] += gnd

# Unused OTA-B: tie all inputs to GND to prevent oscillation
u1[16] += gnd; u1[15] += gnd; u1[14] += gnd; u1[13] += gnd
u1[7]  += gnd   # BUF_IN_A
u1[10] += gnd   # BUF_IN_B
# Outputs pins 8, 9, 12 left floating (safe)

# ── Input signal chain ────────────────────────────────────────────────────────

# Input termination resistor (100K, sets input impedance, vertical)
r_in = Part("Device", "R", value="100K",
            footprint="Resistor_THT:R_Axial_DIN0207_L6.3mm_D2.5mm_P2.54mm_Vertical")
r_in[1] += sig_ac
r_in[2] += gnd

# AC coupling cap — blocks DC before OTA +IN (1uF disc, 5mm pitch)
c_in_ac = Part("Device", "C", value="1uF",
               footprint="Capacitor_THT:C_Disc_D4.3mm_W1.9mm_P5.00mm")
c_in_ac[1] += sig_in
c_in_ac[2] += sig_ac

# ── Output signal chain ───────────────────────────────────────────────────────

# Output AC coupling cap (1uF disc)
c_out_ac = Part("Device", "C", value="1uF",
                footprint="Capacitor_THT:C_Disc_D4.3mm_W1.9mm_P5.00mm")
c_out_ac[1] += ota_out
c_out_ac[2] += audio_out

# ── Power supply decoupling ───────────────────────────────────────────────────

# Eurorack 2x5 IDC shrouded power header (J4 per auto-assigned ref)
j_pwr = Part("Connector_Generic", "Conn_02x05_Counter_Clockwise",
             footprint="Connector_IDC:IDC-Header_2x05_P2.54mm_Vertical",
             value="PWR_2x5")
j_pwr[1] += vp12; j_pwr[3] += vp12; j_pwr[5] += vp12
j_pwr[7] += gnd;  j_pwr[9] += gnd
j_pwr[2] += vm12; j_pwr[4] += vm12; j_pwr[6] += vm12
j_pwr[8] += gnd;  j_pwr[10] += gnd

# Bulk filter electrolytics (D6.3mm radial — slim enough for 20mm PCB)
c_bulk_p = Part("Device", "C", value="10uF",
                footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
c_bulk_p[1] += vp12
c_bulk_p[2] += gnd

c_bulk_m = Part("Device", "C", value="10uF",
                footprint="Capacitor_THT:CP_Radial_D6.3mm_P2.50mm")
c_bulk_m[1] += gnd
c_bulk_m[2] += vm12

# 100nF ceramic bypass caps — must sit close to U1 V+ (pin 11) and V- (pin 6)
c_byp_p = Part("Device", "C", value="100nF",
               footprint="Capacitor_THT:C_Disc_D4.3mm_W1.9mm_P5.00mm")
c_byp_p[1] += vp12
c_byp_p[2] += gnd

c_byp_m = Part("Device", "C", value="100nF",
               footprint="Capacitor_THT:C_Disc_D4.3mm_W1.9mm_P5.00mm")
c_byp_m[1] += gnd
c_byp_m[2] += vm12

# ── EDA Floorplan ─────────────────────────────────────────────────────────────
# No pot (RV1 removed) — only 16 parts, all smaller than the previous attempt.
#
# KiCad auto-assigns refs by instantiation order:
#   J1=sig in jack  J2=CV jack  J3=output jack  J4=power IDC header
#
# Verified footprint bounds (from prior server run output):
#   Jack PJ398SM:  ±7.225mm Y from origin = 14.45mm total; 10.05mm wide
#   DIP-16 U1:     top = origin-1.545mm, bottom = origin+19.325mm
#   IDC 2x5 J4:    top = origin-5.625mm, bottom = origin+15.785mm
#
# Board 20.32mm × 101.6mm. Layout from top:
#   J1 y=10.0: spans 2.775–17.225mm
#   J2 y=26.0: spans 18.775–33.225mm  (0.55mm gap below J1)
#   J3 y=42.0: spans 34.775–49.225mm  (1.55mm gap below J2)
#   [gap 49.225–57.455 = 8.23mm for passives]
#   U1 y=59.0: spans 57.455–78.325mm
#   [gap 78.325–79.375 = 1.05mm]
#   J4 y=85.0: spans 79.375–100.785mm  (fits within 101.6mm)

EDA_FLOORPLAN = {
    "outline": {"width_mm": 20.32, "height_mm": 101.6},
    # KiCad refs: J1=SIG_IN jack, J2=CV_IN jack, J3=OUT jack, J4=power IDC
    # Fix only the large mechanically-constrained parts.
    # Passives (resistors/caps) auto-placed alongside in remaining space.
    #
    # Jack (PJ398SM) actual courtyard bounds (from server data):
    #   top = origin - 1.445mm, bottom = origin + 13.005mm (14.45mm total)
    #   So J1@y=10: y=8.555-23.005; J2@y=26: y=24.555-39.005; J3@y=42: y=40.555-55.005
    # DIP-16 U1: top=origin-1.545mm, bottom=origin+19.325mm
    # IDC J4: top=origin-5.625mm, bottom=origin+15.785mm
    #
    # Total height needed: J1 top(8.555) + J2(16mm) + J3(16mm) + gap + U1(20.87) + gap + J4(21.41)
    # = 8.555 to 23.005 | 24.555 to 39.005 | 40.555 to 55.005 | 57.455 to 78.325 | 79.375 to 100.785
    # Total span = 100.785mm — fits in 101.6mm!
    # Passive gap: only tiny spaces beside jacks (x<5.135 or x>15.185)
    # Passive disc caps (7.15mm wide) and resistors (5.14mm wide) cannot fit beside jacks
    # but fit beside U1 in the left zone x<9.075

    "fixed_positions": [
        # Panel jacks — origin is 1.445mm from top of courtyard, 13.005mm from bottom
        {"ref": "J1", "x_mm": 10.16, "y_mm": 10.0, "rotation_deg": 0},
        {"ref": "J2", "x_mm": 10.16, "y_mm": 26.0, "rotation_deg": 0},
        {"ref": "J3", "x_mm": 10.16, "y_mm": 42.0, "rotation_deg": 0},
        # U1 at y=59: top=57.455, bottom=78.325; gap from J3=2.45mm
        {"ref": "U1", "x_mm": 10.16, "y_mm": 59.0, "rotation_deg": 0},
        # J4 at y=85: top=79.375, bottom=100.785; gap from U1=1.05mm
        {"ref": "J4", "x_mm": 10.16, "y_mm": 85.0, "rotation_deg": 0},
    ],
}
