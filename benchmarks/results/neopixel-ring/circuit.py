"""NeoPixel Ring (16-LED) — WS2812B addressable LED ring.

16 WS2812B LEDs daisy-chained in a ring configuration.
Each LED has a 100nF decoupling cap on VDD.
Power input via 3-pin JST connector (5V, DIN, GND).
Data output via 3-pin JST connector (5V, DOUT, GND).
"""

import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# ── Power nets ──────────────────────────────────────────────
vdd = Net("+5V")
vdd.drive = POWER
gnd = Net("GND")
gnd.drive = POWER

# ── Input connector (5V, DIN, GND) ─────────────────────────
j_in = Part(
    "Connector_Generic", "Conn_01x03",
    ref="J1",
    value="NeoPixel_In",
    footprint="Connector_JST:JST_PH_S3B-PH-K_1x03_P2.00mm_Horizontal",
)
j_in[1] += vdd      # Pin 1 = 5V
j_in[3] += gnd      # Pin 3 = GND

# ── Output connector (5V, DOUT, GND) ───────────────────────
j_out = Part(
    "Connector_Generic", "Conn_01x03",
    ref="J2",
    value="NeoPixel_Out",
    footprint="Connector_JST:JST_PH_S3B-PH-K_1x03_P2.00mm_Horizontal",
)
j_out[1] += vdd     # Pin 1 = 5V
j_out[3] += gnd     # Pin 3 = GND

# ── Bulk decoupling cap on power input ──────────────────────
c_bulk = Part(
    "Device", "C",
    ref="C1",
    value="100uF",
    footprint="Capacitor_SMD:C_0805_2012Metric",
)
c_bulk[1] += vdd
c_bulk[2] += gnd

# ── 16 WS2812B LEDs in a daisy chain ───────────────────────
NUM_LEDS = 16

# Create all LEDs and their decoupling caps
leds = []
caps = []
for i in range(NUM_LEDS):
    led = Part(
        "LED", "WS2812B",
        ref=f"D{i+1}",
        value="WS2812B",
        footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
    )
    led["VDD"] += vdd
    led["VSS"] += gnd
    leds.append(led)

    # Per-LED 100nF decoupling cap
    cap = Part(
        "Device", "C",
        ref=f"C{i+2}",
        value="100nF",
        footprint="Capacitor_SMD:C_0603_1608Metric",
    )
    cap[1] += vdd
    cap[2] += gnd
    caps.append(cap)

# ── Chain the data lines ────────────────────────────────────
# Input connector DIN → first LED DIN
data_in = Net("DIN")
j_in[2] += data_in
leds[0]["DIN"] += data_in

# Chain DOUT of LED[n] → DIN of LED[n+1]
for i in range(NUM_LEDS - 1):
    chain_net = Net(f"D{i+1}_D{i+2}")
    leds[i]["DOUT"] += chain_net
    leds[i + 1]["DIN"] += chain_net

# Last LED DOUT → output connector
data_out = Net("DOUT")
leds[-1]["DOUT"] += data_out
j_out[2] += data_out

# ── Generate schematic ─────────────────────────────────────
generate_schematic(auto_stub=True)
