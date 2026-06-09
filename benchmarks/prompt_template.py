"""Prompt template for SKiDL circuit generation from product descriptions."""

SYSTEM_CONTEXT = """\
You are a circuit design engineer generating SKiDL Python code from product descriptions.
SKiDL is a Python library that creates electronic circuits programmatically.

## SKiDL Basics

```python
import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# Parts need: library, device, value, footprint
r1 = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
c1 = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")

# ICs from specific libraries
mcu = Part("MCU_Microchip_SAMD", "ATSAMD21G18A-AU", footprint="Package_QFP:TQFP-48_7x7mm_P0.5mm")

# Nets
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Connections
r1[1] += vcc
r1[2] += mcu["PA00"]

# Subcircuits group parts
@subcircuit
def power_section(vin, vout, gnd):
    reg = Part("Regulator_Linear", "AP2112K-3.3", value="3.3V",
               footprint="Package_TO_SOT_SMD:SOT-23-5")
    # ...

# Generate outputs
generate_schematic()
```

## Rules
1. Every Part MUST have a footprint= parameter
2. Power nets: use VCC, VDD, +3V3, +5V, GND, VSS (standard names)
3. Decoupling caps: use value="100nF" (not "0.1uF" or "104")
4. Use @subcircuit to group functional blocks (5-15 parts each)
5. All IC pin connections must use real pin names from the KiCad symbol library
6. For unknown/complex ICs, use Part(tool=SKIDL) with explicit pin definitions
7. Output a SINGLE self-contained Python script that can be run with python3

## KiCad Symbol Libraries (available at /usr/share/kicad/symbols/)
Common libraries: Device, Connector_Generic, Connector_USB, MCU_Microchip_SAMD,
MCU_Microchip_ATSAM, MCU_Nordic_nRF, MCU_Espressif_ESP32, MCU_RaspberryPi_RP2xxx,
Regulator_Linear, Regulator_Switching, Sensor_Temperature, Sensor_Pressure,
Sensor_Humidity, Sensor_Motion, Sensor_Optical, Sensor_Current, Amplifier_Audio,
Audio, Interface_USB, Interface_UART, Memory_Flash, Battery_Management,
Power_Protection, LED, Display_Character, Switch

## Footprint Libraries (available at /usr/share/kicad/footprints/)
Common: Resistor_SMD, Capacitor_SMD, Inductor_SMD, Package_QFP, Package_SO,
Package_TO_SOT_SMD, Package_BGA, Package_DFN_QFN, Package_CSP, Connector_PinHeader_2.54mm,
Connector_USB, Crystal, LED_SMD, Button_Switch_SMD, Diode_SMD

## Critical: Pin Name Lookup
If you're not 100% sure of the pin names for an IC, define it with tool=SKIDL:
```python
ic = Part(name="MY_IC", tool=SKIDL, pins=[
    Pin(num="1", name="VCC", func=Pin.types.PWRIN),
    Pin(num="2", name="GND", func=Pin.types.PWRIN),
    Pin(num="3", name="SDA", func=Pin.types.BIDIR),
    Pin(num="4", name="SCL", func=Pin.types.INPUT),
])
```
This avoids pin name mismatches with the KiCad library.

## Footprint Validation
Before using any footprint, verify it exists on disk:
  ls /usr/share/kicad/footprints/{Library}.pretty/{Name}.kicad_mod
Common mistakes:
- Package_LGA does NOT exist in standard KiCad — use Package_DFN_QFN instead
- Button_Switch_Keyboard footprints may not exist — use Button_Switch_SMD
- SOIC-8 is SOIC-8_3.9x4.9mm_P1.27mm (not 5.23x5.23)
If unsure, run `ls /usr/share/kicad/footprints/{Library}.pretty/` to see available names.

## SKIDL-Tool Parts and ERC
Parts defined with tool=SKIDL will produce lib_symbol_issues/lib_symbol_mismatch
ERC warnings — this is expected and benign. Focus on real ERC errors like
pin_not_connected and pin_not_driven.

## When in doubt
- Use generic Part("Device", "R"/"C"/"L") for passives
- Use Part(tool=SKIDL, pins=[...]) for unfamiliar ICs
- Include bypass/decoupling caps (100nF) on every IC power pin
- Add bulk capacitors (10uF-100uF) on power input
- Include ESD protection on USB/external interfaces where appropriate
"""

GENERATION_PROMPT = """\
{system_context}

## Your Task

Generate a complete SKiDL Python script for the following product:

**Product Name:** {board_name}
**Marketing Description:**
{description}

## Requirements
1. Output a single Python file that creates the complete circuit
2. Include ALL major ICs, connectors, and functional blocks described
3. Use @subcircuit for each functional group
4. Include proper power distribution (regulators, decoupling)
5. End the script with generate_schematic() call
6. The script must be runnable with: python3 circuit.py
7. Save the file to: {output_path}

## Output Format
Write the complete Python script. After writing it, run it with python3 and report:
- Whether it parsed without syntax errors
- Whether generate_schematic() succeeded
- Any ERC warnings/errors
- Total part count

If the script fails, fix the errors and try again (up to 3 attempts).
Report your final results as a JSON object:
```json
{{
    "board_name": "{board_name}",
    "attempts": 1,
    "parse_ok": true,
    "schematic_ok": true,
    "erc_warnings": 0,
    "erc_errors": 0,
    "part_count": 42,
    "net_count": 28,
    "subcircuit_count": 5,
    "ics_generated": ["ATSAMD21G18A", "ESP32-WROOM-32", ...],
    "errors": [],
    "notes": "..."
}}
```
"""


def build_prompt(board_name, description, output_dir, enriched_description=None):
    """Build the full generation prompt for a board."""
    import os
    output_path = os.path.join(output_dir, "circuit.py")
    return GENERATION_PROMPT.format(
        system_context=SYSTEM_CONTEXT,
        board_name=board_name,
        description=enriched_description or description,
        output_path=output_path,
    )
