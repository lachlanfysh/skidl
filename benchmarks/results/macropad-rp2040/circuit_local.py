"""
MacroPad RP2040 — 3x4 mechanical key macropad.

RP2040 QFN-56 MCU, 12 Cherry MX-compatible key switches (3x4 matrix with
1N4148W diodes), 12x WS2812B NeoPixel per-key RGB LEDs, rotary encoder with
push button, SSD1306 128x64 OLED (I2C), USB-C connector, 8MB QSPI flash
(W25Q32JVSS), 3.3V LDO regulator (AP2112K-3.3). Board ~76x57mm.

GPIO assignments:
  KEY ROW0-2 : GPIO6, GPIO7, GPIO8
  KEY COL0-3 : GPIO9, GPIO10, GPIO11, GPIO12
  NEOPIXEL   : GPIO19
  ENC_A/B    : GPIO0, GPIO1
  ENC_SW     : GPIO3
  OLED I2C   : SDA=GPIO20, SCL=GPIO21
  QSPI flash : hardware pins (QFN-56 dedicated QSPI bank)
  USB        : hardware USB_DM/USB_DP pins
"""

from skidl import *

# Power nets
vbus = Net("VBUS"); vbus.drive = POWER
v3v3 = Net("+3V3"); v3v3.drive = POWER
gnd  = Net("GND");  gnd.drive  = POWER


# ---------------------------------------------------------------------------
# USB-C receptacle + CC pull-downs + 3.3V LDO
# ---------------------------------------------------------------------------
@subcircuit
def usb_power(vbus, v3v3, gnd):
    global usb_dm_net, usb_dp_net

    usb = Part("Connector", "USB_C_Receptacle_USB2.0_14P",
               footprint="Connector_USB:USB_C_Receptacle_XKB_U262-16XN-4BVC11")
    # VBUS
    usb["VBUS"] += vbus
    # GND
    usb["GND"]  += gnd
    # Shield
    usb["SHIELD"] += gnd
    # USB data — share global nets with MCU block
    usb_dm_net = Net("USB_DM"); usb_dm_net.drive = POWER
    usb_dp_net = Net("USB_DP"); usb_dp_net.drive = POWER
    usb["D-"] += usb_dm_net
    usb["D+"] += usb_dp_net
    # CC pull-downs (5.1K) for USB-C host detection
    cc1 = Net("CC1"); cc2 = Net("CC2")
    usb["CC1"] += cc1
    usb["CC2"] += cc2
    r_cc1 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc2 = Part("Device", "R", value="5.1K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_cc1[1] += cc1; r_cc1[2] += gnd
    r_cc2[1] += cc2; r_cc2[2] += gnd
    # VBUS bypass cap
    c_vbus = Part("Device", "C", value="100nF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_vbus[1] += vbus; c_vbus[2] += gnd

    # 3.3V LDO — AP2112K-3.3
    ldo = Part("Regulator_Linear", "AP2112K-3.3",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    ldo["VIN"]  += vbus
    ldo["GND"]  += gnd
    ldo["EN"]   += vbus       # always enabled
    ldo["VOUT"] += v3v3
    # LDO input bulk cap
    c_in = Part("Device", "C", value="10uF",
                footprint="Capacitor_SMD:C_0805_2012Metric")
    c_in[1] += vbus; c_in[2] += gnd
    # LDO output decoupling
    c_out = Part("Device", "C", value="10uF",
                 footprint="Capacitor_SMD:C_0805_2012Metric")
    c_out[1] += v3v3; c_out[2] += gnd
    c_dec = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dec[1] += v3v3; c_dec[2] += gnd


usb_dm_net = None; usb_dp_net = None
usb_power(vbus, v3v3, gnd)


# ---------------------------------------------------------------------------
# RP2040 MCU + QSPI flash + crystal
# ---------------------------------------------------------------------------
@subcircuit
def rp2040_core(v3v3, gnd):
    global row_nets, col_nets, neo_data_net, enc_a_net, enc_b_net
    global enc_sw_net, oled_sda_net, oled_scl_net

    mcu = Part("MCU_RaspberryPi", "RP2040",
               footprint="Package_DFN_QFN:QFN-56-1EP_7x7mm_P0.4mm_EP3.2x3.2mm")

    # --- Power (57-pin symbol; IOVDD/DVDD are shared names across multiple pins) ---
    mcu["IOVDD"] += v3v3     # connects all 6 IOVDD pins
    mcu["ADC_AVDD"] += v3v3
    mcu["USB_VDD"]  += v3v3
    mcu["GND"]      += gnd
    mcu["TESTEN"]   += gnd

    # VREG: VREG_IN from 3V3, VREG_VOUT feeds DVDD (internal 1.1V core)
    dvdd = Net("DVDD"); dvdd.drive = POWER
    mcu["VREG_VIN"]   += v3v3
    mcu["VREG_VOUT"] += dvdd
    mcu["DVDD"]      += dvdd   # connects both DVDD pins

    # DVDD filter cap (1uF between VREG_VOUT and GND per RP2040 datasheet)
    c_dvdd = Part("Device", "C", value="1uF",
                  footprint="Capacitor_SMD:C_0402_1005Metric")
    c_dvdd[1] += dvdd; c_dvdd[2] += gnd

    # Per-bank IOVDD decoupling (100nF per bank, 6 banks)
    for _ in range(6):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
        c[1] += v3v3; c[2] += gnd

    # USB_VDD decoupling
    c_usb = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_usb[1] += v3v3; c_usb[2] += gnd

    # ADC_AVDD decoupling
    c_adc = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0402_1005Metric")
    c_adc[1] += v3v3; c_adc[2] += gnd

    # --- USB data ---
    mcu["USB_DM"] += usb_dm_net
    mcu["USB_DP"] += usb_dp_net

    # --- RUN pin pull-up ---
    r_run = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_run[1] += v3v3; r_run[2] += mcu["RUN"]

    # --- SWD debug header ---
    swd = Part("Connector_Generic", "Conn_01x04",
               footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")
    swd[1] += gnd
    swd[2] += v3v3
    swd[3] += mcu["SWCLK"]
    swd[4] += mcu["SWDIO"]

    # --- 12MHz crystal ---
    xtal = Part("Device", "Crystal", value="12MHz",
                footprint="Crystal:Crystal_SMD_3215-2Pin_3.2x1.5mm")
    xtal[1] += mcu["XIN"]
    xtal[2] += mcu["XOUT"]
    c_x1 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x2 = Part("Device", "C", value="15pF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_x1[1] += mcu["XIN"];  c_x1[2] += gnd
    c_x2[1] += mcu["XOUT"]; c_x2[2] += gnd

    # --- QSPI flash: W25Q32JVSS ---
    flash = Part("Memory_Flash", "W25Q32JVSS",
                 footprint="Package_SO:SOIC-8_3.9x4.9mm_P1.27mm")
    flash["~{CS}"]              += mcu["~{QSPI_SS}"]
    flash["CLK"]                += mcu["QSPI_SCLK"]
    flash["DI/IO_{0}"]          += mcu["QSPI_SD0"]
    flash["DO/IO_{1}"]          += mcu["QSPI_SD1"]
    flash["~{WP}/IO_{2}"]       += mcu["QSPI_SD2"]
    flash["~{HOLD}/~{RESET}/IO_{3}"] += mcu["QSPI_SD3"]
    flash["VCC"] += v3v3
    flash["GND"] += gnd
    # Flash decoupling
    c_fl = Part("Device", "C", value="100nF",
                footprint="Capacitor_SMD:C_0402_1005Metric")
    c_fl[1] += v3v3; c_fl[2] += gnd

    # --- GPIO assignments ---
    # Key matrix rows (output)
    row_nets = []
    for i, gpio in enumerate(["GPIO6", "GPIO7", "GPIO8"]):
        n = Net(f"KEY_ROW{i}")
        mcu[gpio] += n
        row_nets.append(n)

    # Key matrix columns (input with pull-up)
    col_nets = []
    for i, gpio in enumerate(["GPIO9", "GPIO10", "GPIO11", "GPIO12"]):
        n = Net(f"KEY_COL{i}")
        mcu[gpio] += n
        col_nets.append(n)

    # NeoPixel data
    neo_data_net = Net("NEOPIXEL_DATA")
    mcu["GPIO19"] += neo_data_net

    # Rotary encoder
    enc_a_net  = Net("ENC_A");  mcu["GPIO0"] += enc_a_net
    enc_b_net  = Net("ENC_B");  mcu["GPIO1"] += enc_b_net
    enc_sw_net = Net("ENC_SW"); mcu["GPIO3"] += enc_sw_net

    # I2C for OLED (I2C0 on GPIO20/GPIO21)
    oled_sda_net = Net("OLED_SDA"); mcu["GPIO20"] += oled_sda_net
    oled_scl_net = Net("OLED_SCL"); mcu["GPIO21"] += oled_scl_net

    # Unused GPIOs → NC nets to suppress ERC (use exact KiCad pin names)
    unused_gpios = [
        ("GPIO2",        "NC_GPIO2"),
        ("GPIO4",        "NC_GPIO4"),
        ("GPIO5",        "NC_GPIO5"),
        ("GPIO13",       "NC_GPIO13"),
        ("GPIO14",       "NC_GPIO14"),
        ("GPIO15",       "NC_GPIO15"),
        ("GPIO16",       "NC_GPIO16"),
        ("GPIO17",       "NC_GPIO17"),
        ("GPIO18",       "NC_GPIO18"),
        ("GPIO22",       "NC_GPIO22"),
        ("GPIO23",       "NC_GPIO23"),
        ("GPIO24",       "NC_GPIO24"),
        ("GPIO25",       "NC_GPIO25"),
        ("GPIO26/ADC0",  "NC_GPIO26"),
        ("GPIO27/ADC1",  "NC_GPIO27"),
        ("GPIO28/ADC2",  "NC_GPIO28"),
        ("GPIO29/ADC3",  "NC_GPIO29"),
    ]
    for pin_name, net_name in unused_gpios:
        n = Net(net_name); n.drive = POWER
        mcu[pin_name] += n


row_nets = col_nets = neo_data_net = None
enc_a_net = enc_b_net = enc_sw_net = None
oled_sda_net = oled_scl_net = None

rp2040_core(v3v3, gnd)


# ---------------------------------------------------------------------------
# Key switch matrix: 3 rows x 4 cols = 12 switches with 1N4148W diodes
# ---------------------------------------------------------------------------
@subcircuit
def key_matrix(v3v3, gnd):
    global row_nets, col_nets
    # Pull-up resistors on column lines (10K each)
    for i, col in enumerate(col_nets):
        r = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
        r[1] += v3v3; r[2] += col

    # 12 switches in matrix — row/col order: row varies slowest
    for row_idx in range(3):
        for col_idx in range(4):
            key_num = row_idx * 4 + col_idx
            sw = Part("Switch", "SW_Push",
                      footprint="Button_Switch_Keyboard:SW_Cherry_MX_1.00u_PCB",
                      value=f"KEY{key_num}")
            # Diode: anode from switch, cathode to column line (anti-ghost)
            d = Part("Diode", "1N4148W",
                     footprint="Diode_SMD:D_SOD-123")
            # Switch pin 1 to row, pin 2 to diode anode
            sw[1] += row_nets[row_idx]
            sw_diode_net = Net(f"KEY{key_num}_D")
            sw[2] += sw_diode_net
            d["A"] += sw_diode_net
            d["K"] += col_nets[col_idx]


key_matrix(v3v3, gnd)


# ---------------------------------------------------------------------------
# NeoPixel chain: 12x WS2812B (one per key)
# ---------------------------------------------------------------------------
@subcircuit
def neopixel_chain(vbus, gnd):
    global neo_data_net
    prev_out = neo_data_net

    for i in range(12):
        led = Part("LED", "WS2812B",
                   footprint="LED_SMD:LED_WS2812B_PLCC4_5.0x5.0mm_P3.2mm",
                   value="WS2812B")
        led["VDD"] += vbus
        led["VSS"] += gnd
        led["DIN"] += prev_out

        if i < 11:
            chain_net = Net(f"NEO_CHAIN_{i}")
            led["DOUT"] += chain_net
            prev_out = chain_net
        else:
            nc = Net("NEO_DOUT_NC"); nc.drive = POWER
            led["DOUT"] += nc

    # Bypass caps (100nF per LED group of 4)
    for _ in range(3):
        c = Part("Device", "C", value="100nF",
                 footprint="Capacitor_SMD:C_0603_1608Metric")
        c[1] += vbus; c[2] += gnd
    # Bulk cap for NeoPixel supply
    c_bulk = Part("Device", "C", value="100uF",
                  footprint="Capacitor_SMD:C_1210_3225Metric")
    c_bulk[1] += vbus; c_bulk[2] += gnd


neopixel_chain(vbus, gnd)


# ---------------------------------------------------------------------------
# Rotary encoder with push switch
# ---------------------------------------------------------------------------
@subcircuit
def rotary_encoder(v3v3, gnd):
    global enc_a_net, enc_b_net, enc_sw_net

    enc = Part("Device", "RotaryEncoder_Switch",
               footprint="Rotary_Encoder:RotaryEncoder_Alps_EC11E-Switch_Vertical_H20mm")
    enc["A"]  += enc_a_net
    enc["B"]  += enc_b_net
    enc["C"]  += gnd
    enc["S1"] += enc_sw_net
    enc["S2"] += gnd

    # Pull-ups for encoder A, B, SW
    for net in [enc_a_net, enc_b_net, enc_sw_net]:
        r = Part("Device", "R", value="10K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
        r[1] += v3v3; r[2] += net


rotary_encoder(v3v3, gnd)


# ---------------------------------------------------------------------------
# SSD1306 128x64 OLED display (I2C), represented as header connector
# ---------------------------------------------------------------------------
@subcircuit
def oled_display(v3v3, gnd):
    global oled_sda_net, oled_scl_net

    # 4-pin JST SH connector for I2C OLED module
    conn = Part("Connector_Generic", "Conn_01x04",
                footprint="Connector_JST:JST_SH_SM04B-SRSS-TB_1x04-1MP_P1.00mm_Horizontal")
    conn[1] += gnd
    conn[2] += v3v3
    conn[3] += oled_sda_net
    conn[4] += oled_scl_net

    # I2C pull-up resistors (4.7K)
    r_sda = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_scl = Part("Device", "R", value="4.7K",
                 footprint="Resistor_SMD:R_0402_1005Metric")
    r_sda[1] += v3v3; r_sda[2] += oled_sda_net
    r_scl[1] += v3v3; r_scl[2] += oled_scl_net

    # OLED power decoupling
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0402_1005Metric")
    c[1] += v3v3; c[2] += gnd


oled_display(v3v3, gnd)


# ---------------------------------------------------------------------------
# Floorplan: 76x57mm macropad, key grid in 3x4, USB-C on top edge
# ---------------------------------------------------------------------------
# Key switch refs: SW1..SW12 (3 rows x 4 cols), diodes D1..D12 below each sw
# NeoPixel refs: LED1..LED12 (one per key position)
# Rotary encoder: SW13 (last switch-type part)
# USB-C connector: J1
# OLED I2C connector: J2
# SWD header: J3
# LDO: U1; MCU RP2040: U2; Flash: U3
#
# Key grid: 19mm pitch (standard MX spacing), 4 cols x 3 rows
# Grid origin: x=19mm from left, y=25mm from top (center of first key)
EDA_FLOORPLAN = {
    # Board: 76mm wide x 84mm tall (portrait orientation).
    # Task spec says "~76x57mm" but fitting 3 rows + encoder + electronics needs more height.
    # Key grid: 4 cols x 3 rows, 19.05mm pitch → 57.15x57.15mm footprint area
    # Rotary encoder EC11 footprint: 17.5mm wide x 14.2mm tall (origin offset: left=-1.5, right=+16)
    # Strategy: keys at bottom, encoder+USB+OLED at top strip
    "outline": {
        "width_mm": 76,
        "height_mm": 84,
        "corner_radius_mm": 1,
    },
    "edge_anchors": [
        # USB-C on top edge
        {"ref": "J1", "edge": "top"},
        # OLED I2C connector on top edge
        {"ref": "J2", "edge": "top"},
        # SWD debug header on bottom edge
        {"ref": "J3", "edge": "bottom"},
    ],
    "grid": {
        # Key grid: 4 cols x 3 rows, origin at x=9.525mm, y=27mm (center of key row 1)
        # Col centers: 9.525, 28.575, 47.625, 66.675 → right edge 66.675+6.625=73.3mm < 76mm ✓
        # Row centers: 27, 46.05, 65.1 → bottom edge 65.1+6.625=71.725mm < 84mm ✓
        "refs": [
            "SW1",  "SW2",  "SW3",  "SW4",
            "SW5",  "SW6",  "SW7",  "SW8",
            "SW9",  "SW10", "SW11", "SW12",
        ],
        "rows": 3,
        "cols": 4,
        "x_mm": 9.525,
        "y_mm": 27.0,
        "dx_mm": 19.05,
        "dy_mm": 19.05,
        "soft": True,
        "side": "front",
    },
}
