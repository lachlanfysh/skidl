from skidl import *

# Power rails
vin = Net("VIN"); vin.drive = POWER        # 5V input
vcc3v3 = Net("VCC"); vcc3v3.drive = POWER  # 3.3V digital/IO/analog rail
vcc1v8 = Net("CVDD"); vcc1v8.drive = POWER # 1.8V core rail for VS1053
gnd = Net("GND"); gnd.drive = POWER

# 3.3V LDO (AMS1117-3.3, SOT-223, 1A)
u_3v3 = Part("Regulator_Linear", "AMS1117-3.3",
             footprint="Package_TO_SOT_SMD:SOT-223-3_TabPin2")
vin += u_3v3["VI"]
gnd += u_3v3["GND"]
vcc3v3 += u_3v3["VO"]

# 1.8V LDO for VS1053 core (MIC5365-1.8YC5, SOT-353, 150mA)
u_1v8 = Part("Regulator_Linear", "MIC5365-1.8YC5",
             footprint="Package_TO_SOT_SMD:SOT-353_SC-70-5")
vcc3v3 += u_1v8["VIN"]
vcc3v3 += u_1v8["EN"]   # EN tied high = always on
gnd += u_1v8["GND"]
vcc1v8 += u_1v8["VOUT"]
u_1v8["NC"] += NC()

# VS1053B audio codec (EasyEDA/LCSC C9922, LQFP-48)
vs = Part("C9922", "VS1053B",
          footprint="Package_QFP:LQFP-48_7x7mm_P0.5mm")

# Core power (1.8V) — pins 5,7,24,31
vcc1v8 += vs["CVDD0"], vs["CVDD1"], vs["CVDD2"], vs["CVDD3"]
# IO power (3.3V) — pins 6,14,19
vcc3v3 += vs["IOVDD0"], vs["IOVDD1"], vs["IOVDD2"]
# Analog power (3.3V) — pins 38,43,45
vcc3v3 += vs["AVDD0"], vs["AVDD1"], vs["AVDD2"]
# Digital ground — pins 4,16,20,21,22,35
gnd += vs["DGND0"], vs["DGND1"], vs["DGND2"], vs["DGND3"], vs["DGND4"], vs["GND"]
# Analog ground — pins 37,40,41,47
gnd += vs["AGND0"], vs["AGND1"], vs["AGND2"], vs["AGND3"]

# XTEST: pull to GND for normal operation (pin 32)
gnd += vs["XTEST"]

# VCO filter cap (pin 15)
vco_net = Net("VCO_CAP")
vs["VCO"] += vco_net
c_vco = Part("Device", "C", value="470nF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
vco_net += c_vco[1]
gnd += c_vco[2]

# RCAP: output resistance cap (pin 44) — 10K to GND
rcap_net = Net("RCAP")
vs["RCAP"] += rcap_net
r_rcap = Part("Device", "R", value="10K",
              footprint="Resistor_SMD:R_0603_1608Metric")
rcap_net += r_rcap[1]
gnd += r_rcap[2]

# GBUF: buffer output (pin 42) — 33 ohm to GND
gbuf_net = Net("GBUF")
vs["GBUF"] += gbuf_net
r_gbuf = Part("Device", "R", value="33",
              footprint="Resistor_SMD:R_0603_1608Metric")
gbuf_net += r_gbuf[1]
gnd += r_gbuf[2]

# 12.288 MHz crystal (pins 17=XTALO, 18=XTALI)
xtal = Part("Device", "Crystal",
            footprint="Crystal:Crystal_SMD_HC49-SD")
xtal.value = "12.288MHz"

xtali_net = Net("XTALI")
xtalo_net = Net("XTALO")
vs["XTALI"] += xtali_net
vs["XTALO"] += xtalo_net
xtal[1] += xtali_net
xtal[2] += xtalo_net

# Crystal load caps (18pF)
c_xi = Part("Device", "C", value="18pF",
            footprint="Capacitor_SMD:C_0402_1005Metric")
c_xo = Part("Device", "C", value="18pF",
            footprint="Capacitor_SMD:C_0402_1005Metric")
xtali_net += c_xi[1]; gnd += c_xi[2]
xtalo_net += c_xo[1]; gnd += c_xo[2]

# SPI interface signals
spi_sck  = Net("SPI_SCK")
spi_mosi = Net("SPI_MOSI")
spi_miso = Net("SPI_MISO")
vs_xcs   = Net("VS_XCS")    # VS1053 SPI control chip select (pin 23)
vs_xdcs  = Net("VS_XDCS")   # VS1053 SPI data chip select / BSYNC (pin 13)
vs_dreq  = Net("VS_DREQ")   # data request output (pin 8)
vs_xrst  = Net("VS_XRST")   # reset active-low (pin 3)
midi_rx  = Net("MIDI_RX")   # UART RX for MIDI (pin 26)
midi_tx  = Net("MIDI_TX")   # UART TX (pin 27)

vs["SCLK"] += spi_sck
vs["SI"]   += spi_mosi
vs["SO"]   += spi_miso
vs["XCS"]  += vs_xcs
vs["XDCSBSYNC1"] += vs_xdcs
vs["DREQ"] += vs_dreq
vs["XRESET"] += vs_xrst
vs["RX"]   += midi_rx
vs["TX"]   += midi_tx

# XRESET pull-up resistor
r_xrst = Part("Device", "R", value="10K",
              footprint="Resistor_SMD:R_0603_1608Metric")
vcc3v3 += r_xrst[1]
vs_xrst += r_xrst[2]

# GPIO signals (pins 33,34,9,10,36,11,12,25)
gpio0     = Net("GPIO0")
gpio1     = Net("GPIO1")
gpio2     = Net("GPIO2")
gpio3     = Net("GPIO3")
gpio4     = Net("GPIO4")
gpio6     = Net("GPIO6")
gpio7     = Net("GPIO7")
gpio_mclk = Net("GPIO_MCLK")

vs["GPIO0"]          += gpio0
vs["GPIO1"]          += gpio1
vs["GPIO2DCLK1"]     += gpio2
vs["GPIO3SDATA1"]    += gpio3
vs["GPIO4I2S-LROUT3"]  += gpio4
vs["GPIO6I2S_SCLK3"]   += gpio6
vs["GPIO7I2S_SDATA3"]  += gpio7
vs["GPIOI2S_MCLK3"]    += gpio_mclk

# MIC input pins — tie MICPLINE1 and LINE2 to AGND for breakout
gnd += vs["MICPLINE1"], vs["MICN"], vs["LINE2"]

# SPI header — 10 pin (3V3, GND, SCK, MOSI, MISO, XCS, XDCS, DREQ, XRST, MIDI_RX)
j_spi = Part("Connector", "Conn_01x10_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
j_spi.edge_preference = "right"
vcc3v3  += j_spi["Pin_1"]
gnd     += j_spi["Pin_2"]
spi_sck  += j_spi["Pin_3"]
spi_mosi += j_spi["Pin_4"]
spi_miso += j_spi["Pin_5"]
vs_xcs   += j_spi["Pin_6"]
vs_xdcs  += j_spi["Pin_7"]
vs_dreq  += j_spi["Pin_8"]
vs_xrst  += j_spi["Pin_9"]
midi_rx  += j_spi["Pin_10"]

# GPIO header — 10 pin (GPIO0..7, MIDI_TX, GND)
j_gpio = Part("Connector", "Conn_01x10_Pin",
              footprint="Connector_PinHeader_2.54mm:PinHeader_1x10_P2.54mm_Vertical")
j_gpio.edge_preference = "right"
gpio0     += j_gpio["Pin_1"]
gpio1     += j_gpio["Pin_2"]
gpio2     += j_gpio["Pin_3"]
gpio3     += j_gpio["Pin_4"]
gpio4     += j_gpio["Pin_5"]
gpio6     += j_gpio["Pin_6"]
gpio7     += j_gpio["Pin_7"]
gpio_mclk += j_gpio["Pin_8"]
midi_tx  += j_gpio["Pin_9"]
gnd      += j_gpio["Pin_10"]

# 3.5mm stereo headphone jack
# CUI SJ1-3513N footprint uses pad names T/R/S — matches AudioJack3 symbol pin numbers
j_hp = Part("Connector_Audio", "AudioJack3",
            footprint="Connector_Audio:Jack_3.5mm_CUI_SJ1-3513N_Horizontal")
j_hp.edge_preference = "bottom"

# Line output coupling caps (100uF — SMD 1206)
audio_left_raw  = Net("AUDIO_LEFT")
audio_right_raw = Net("AUDIO_RIGHT")
vs["LEFT"]  += audio_left_raw
vs["RIGHT"] += audio_right_raw

c_left = Part("Device", "C", value="100uF",
              footprint="Capacitor_SMD:C_1206_3216Metric")
c_right = Part("Device", "C", value="100uF",
               footprint="Capacitor_SMD:C_1206_3216Metric")
audio_left_raw  += c_left[1];  c_left[2]  += j_hp["T"]   # Tip = Left
audio_right_raw += c_right[1]; c_right[2] += j_hp["R"]   # Ring = Right
gnd += j_hp["S"]                                           # Sleeve = GND

# MicroSD socket (SPI mode) — Hirose DM3D-SF has 11 pads; use Micro_SD_Card_Det2 (11 pins)
sd = Part("Connector", "Micro_SD_Card_Det2",
          footprint="Connector_Card:microSD_HC_Hirose_DM3D-SF")

sd_clk  = Net("SD_CLK")
sd_cmd  = Net("SD_CMD")   # MOSI in SPI mode
sd_dat0 = Net("SD_DAT0")  # MISO in SPI mode
sd_cs   = Net("SD_CS")    # CS (DAT3/CD in SPI mode)
sd_dat1 = Net("SD_DAT1")
sd_dat2 = Net("SD_DAT2")

vcc3v3 += sd["VDD"]
gnd    += sd["VSS"]
gnd    += sd["SHIELD"]
# Card detect pins: DET_A and DET_B are normally-closed switch; tie both to GND
gnd    += sd["DET_A"]
gnd    += sd["DET_B"]

# SD shares the SPI bus with VS1053
spi_sck  += sd_clk;  sd["CLK"]     += sd_clk
spi_mosi += sd_cmd;  sd["CMD"]     += sd_cmd
spi_miso += sd_dat0; sd["DAT0"]    += sd_dat0
sd_cs    += sd["DAT3/CD"]

# SD unused data lines pulled high
sd["DAT1"] += sd_dat1
sd["DAT2"] += sd_dat2
r_sd1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
r_sd2 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
vcc3v3 += r_sd1[1]; sd_dat1 += r_sd1[2]
vcc3v3 += r_sd2[1]; sd_dat2 += r_sd2[2]

# SD CS on its own GPIO2 line (or bring out on separate header pin)
gpio2 += sd_cs

# 5V input power connector
j_pwr = Part("Connector", "Conn_01x02_Pin",
             footprint="Connector_PinHeader_2.54mm:PinHeader_1x02_P2.54mm_Vertical")
j_pwr.edge_preference = "top"
vin += j_pwr["Pin_1"]
gnd += j_pwr["Pin_2"]

# --- Decoupling caps ---

# VIN 10uF bulk
c_vin = Part("Device", "C", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")
vin += c_vin[1]; gnd += c_vin[2]

# 3.3V rail: 10uF bulk + 100nF x4
c_3v3_b = Part("Device", "C", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")
vcc3v3 += c_3v3_b[1]; gnd += c_3v3_b[2]
for _ in range(4):
    c = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    vcc3v3 += c[1]; gnd += c[2]

# 1.8V CVDD rail: 10uF bulk + 100nF x4
c_1v8_b = Part("Device", "C", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")
vcc1v8 += c_1v8_b[1]; gnd += c_1v8_b[2]
for _ in range(4):
    c = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    vcc1v8 += c[1]; gnd += c[2]

# Larger board outline to reduce congestion on dense LQFP-48
EDA_FLOORPLAN = {
    "outline": {"width_mm": 90, "height_mm": 80, "corner_radius_mm": 2},
    "edge_anchors": [
        {"ref": "J3", "edge": "bottom"},
        {"ref": "J5", "edge": "top"},
    ],
}
