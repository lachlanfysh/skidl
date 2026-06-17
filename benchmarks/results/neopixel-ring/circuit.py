"""NeoPixel Ring (12-LED WS2812B) — generated via eda-mcp MCP pipeline.

12 WS2812B addressable LEDs arranged in a daisy-chain ring.
Each LED has a 100nF decoupling cap on VDD.
Input: 3-pin header (VCC/GND/DATA_IN).
Output: 3-pin header (VCC/GND/DATA_OUT) for chaining boards.
Bulk 100uF cap on power input.
5V supply, single-wire data protocol (NeoPixel/WS2812 timing).
"""

from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Data chain nets: DATA_0=input, DATA_1..DATA_12 between LEDs, DATA_12=output
data_nets = [Net(f"DATA_{i}") for i in range(13)]

# Input connector (5V, GND, DATA_IN)
j_in = Part("Connector_Generic", "Conn_01x03",
            footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
j_in.ref = "J1"
j_in[1] += vcc
j_in[2] += gnd
j_in[3] += data_nets[0]

# Output connector (5V, GND, DATA_OUT) for ring chaining
j_out = Part("Connector_Generic", "Conn_01x03",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical")
j_out.ref = "J2"
j_out[1] += vcc
j_out[2] += gnd
j_out[3] += data_nets[12]

# Bulk 100uF capacitor on power input
c_bulk = Part("Device", "C", value="100uF",
              footprint="Capacitor_SMD:C_1210_3225Metric")
c_bulk.ref = "C_BULK"
c_bulk[1] += vcc
c_bulk[2] += gnd

# 12 WS2812B LEDs in a daisy chain with 100nF decoupling cap per LED
NUM_LEDS = 12
for i in range(NUM_LEDS):
    led = Part("LED", "WS2812B",
               footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm")
    led.ref = f"LED{i+1}"
    led["VDD"] += vcc
    led["VSS"] += gnd
    led["DIN"] += data_nets[i]
    led["DOUT"] += data_nets[i+1]

    cap = Part("Device", "C", value="100nF",
               footprint="Capacitor_SMD:C_0402_1005Metric")
    cap.ref = f"C{i+1}"
    cap[1] += vcc
    cap[2] += gnd
