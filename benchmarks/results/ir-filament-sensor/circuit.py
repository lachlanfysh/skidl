from skidl import *

# Power rails
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# --- Photointerrupter (ITR9608-F) ---
# Pin 1=Anode (LED), 2=Cathode (LED), 3=Collector (phototransistor), 4=Emitter
u1 = Part("Sensor_Proximity", "ITR9608-F",
          footprint="OptoDevice:Everlight_ITR9608-F",
          value="ITR9608-F")
u1.lcsc = "C110233"

# LED current limiting resistor: 5V, ~10mA => ~330R
r_led = Part("Device", "R", value="330R",
             footprint="Resistor_SMD:R_0603_1608Metric")

# Phototransistor pull-up resistor: collector load
r_pt = Part("Device", "R", value="10K",
            footprint="Resistor_SMD:R_0603_1608Metric")

# IR LED: VCC -> R_led -> Anode, Cathode -> GND
ir_anode = Net("IR_ANODE")
vcc += r_led[1]
r_led[2] += ir_anode
ir_anode += u1[1]   # LED Anode
u1[2] += gnd        # LED Cathode

# Phototransistor: Collector -> R_pt -> VCC; Emitter -> GND; sensor_raw at collector
sensor_raw = Net("SENSOR_RAW")
vcc += r_pt[1]
r_pt[2] += sensor_raw
sensor_raw += u1[3]  # Collector
u1[4] += gnd         # Emitter

# --- LM393 Dual Comparator (use one channel) ---
# Pins: unit A: IN+=3, IN-=2, OUT=1; unit B: IN+=5, IN-=6, OUT=7; V+=8, V-=4
u2 = Part("Comparator", "LM393",
          footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm",
          value="LM393")
u2.lcsc = "C67470"

# Decoupling cap for LM393
c_byp = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
vcc += u2[8], c_byp[1]
gnd += u2[4], c_byp[2]

# --- Sensitivity trimmer (voltage divider for threshold) ---
# 10K trim pot: pin1=VCC end, pin3=GND end, pin2=wiper=threshold
rv1 = Part("Device", "R_Potentiometer_Trim", value="10K",
           footprint="Potentiometer_SMD:Potentiometer_Bourns_3214W_Vertical")
threshold = Net("THRESHOLD")
vcc += rv1[1]
rv1[2] += threshold
rv1[3] += gnd

# Comparator A: + input = sensor_raw, - input = threshold, output = SIGNAL_RAW
signal_raw = Net("SIGNAL_RAW")
sensor_raw += u2[3]   # IN+ (non-inverting)
threshold += u2[2]    # IN- (inverting)
signal_raw += u2[1]   # Open-collector output

# Pull-up resistor on comparator output (open-collector)
r_pull = Part("Device", "R", value="10K",
              footprint="Resistor_SMD:R_0603_1608Metric")
vcc += r_pull[1]
r_pull[2] += signal_raw

# Tie unused comparator B inputs to known state
u2[5] += gnd   # IN+ B to GND
u2[6] += vcc   # IN- B to VCC  (output stays low, no floating)
# Leave output B unconnected (open-collector, inactive)

# --- Output header: VCC / GND / SIGNAL ---
j1 = Part("Connector_Generic", "Conn_01x03",
          footprint="Connector_PinHeader_2.54mm:PinHeader_1x03_P2.54mm_Vertical",
          value="SIGNAL_OUT")
vcc += j1[1]
gnd += j1[2]
signal_raw += j1[3]

# --- Power LED ---
r_pwr = Part("Device", "R", value="1K",
             footprint="Resistor_SMD:R_0603_1608Metric")
led_pwr = Part("Device", "LED", value="GREEN",
               footprint="LED_SMD:LED_0603_1608Metric")
vcc += r_pwr[1]
r_pwr[2] += led_pwr["A"]
led_pwr["K"] += gnd

# --- Status LED (output-driven via signal, lights when filament absent) ---
r_stat = Part("Device", "R", value="1K",
              footprint="Resistor_SMD:R_0603_1608Metric")
led_stat = Part("Device", "LED", value="RED",
                footprint="LED_SMD:LED_0603_1608Metric")
signal_raw += r_stat[1]
r_stat[2] += led_stat["A"]
led_stat["K"] += gnd

# --- Bulk decoupling cap ---
c_bulk = Part("Device", "C", value="10uF",
              footprint="Capacitor_SMD:C_0805_2012Metric")
vcc += c_bulk[1]
gnd += c_bulk[2]
