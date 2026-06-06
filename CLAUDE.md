# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Environment
- This project uses `pip` for dependency management.
- Tests are managed using `pytest`.
- `tox` is used to run tests across multiple environments (different Python/KiCad versions).

## Common Commands
- **Run all tests (default environment)**: `pytest tests`
- **Run tests across all supported test environments**: `tox`
- **Run a specific test**: Navigate to the `tests` directory and use `pytest path/to/test.py`
- **Build the package**: `python setup.py sdist`
- **Clean build artifacts**: `rm dist/*`

## Project Architecture
SKiDL acts as an infrastructure-as-code tool for circuit design, converting Python-based
circuit descriptions into netlists for PCB layout tools (primarily KiCad).

### Key Modules
- `src/skidl/`: Contains the core package logic.
  - Core circuitry elements like `Part`, `Net`, and `Circuit` definitions.
  - Netlist generation and ERC (Electrical Rules Checking) logic.
  - `schematics`: Logic for generating schematics from SKiDL.
  - `scripts`: User-facing CLI utilities.
  - `tools`: Backend interfaces from SKiDL to various EDA packages.
- `tests/`: Extensive test suite for functionalities ranging from basic circuit construction to hierarchical schematic generation and SPICE integration.
  - `unit_tests`: Unit tests, both manually and AI-generated.
    - `ai_tests`: AI-generated unit tests.
  - `test_data`: Data (mostly part libraries) needed to run unit tests. 
  - `examples/`: Examples to test various features.

### Schematic Generation Integration
For KiCad integration (specifically KiCad 6-9), the process involves:
- Symbol definition extraction from draw commands.
- Hierarchical UUID generation and multi-file schematic output.
- Force-directed placement and routing algorithms to handle component positioning and connectivity.
- Coordinate system handling (KiCad is Y-down, requiring transformations).

### Layout Engine (`src/skidl/layout/`)
Automatic PCB part placement with decoupling cap awareness, power net detection, and validation. This is a separate pipeline from schematic generation — always run both.

Key modules:
- `hierarchy.py`: `extract_groups(circuit)` — groups parts by subcircuit
- `placer.py`: `place_parts(groups, constraints, fp_bboxes)` — 4-layer placement algorithm
- `constraints.py`: `LayoutConstraints`, `BoardOutline`, `FixedPosition`, `KeepOut`
- `writer.py`: `write_kicad_pcb(placed, circuit, fp_lib_dirs, output_path, outline)`
- `validator.py`: `validate(placed, circuit, fp_bboxes, outline)` — overlap/violation checks
- `engine.py`: `plan_layout(circuit, ...)` — high-level orchestrator combining all steps
- `roles.py`: Power/ground net detection regex, decoupling cap detection regex
- `scoring.py`: Placement quality scoring, warns if decaps are >5mm from parent IC

## Full Pipeline: Schematic → Layout → PCB

**Always generate both schematic and PCB layout.** Don't stop at `generate_schematic()`.

```python
import os
os.environ["KICAD9_SYMBOL_DIR"] = "/usr/share/kicad/symbols"

from skidl import *
set_default_tool(KICAD9)

# 1. Build circuit — every Part needs a footprint=
@subcircuit
def my_block(vcc, gnd):
    ic = Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")
    ic[1] += vcc; ic[2] += gnd
    # Decoupling cap: 100nF between VCC and GND → auto-placed near parent IC
    c = Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")
    c[1] += vcc; c[2] += gnd

vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER
my_block(vcc, gnd)

# 2. Generate schematic
generate_schematic(auto_stub=True, auto_stub_fanout=3, erc_max_iterations=8)

# 3. Generate PCB layout
from skidl.layout import (
    extract_groups, place_parts, write_kicad_pcb, validate,
    LayoutConstraints, BoardOutline, FixedPosition, derive_outline,
    load_footprint_bboxes,
)

ckt = default_circuit
fp_names = {str(p.footprint) for p in ckt.parts if getattr(p, "footprint", None)}
fp_lib_dirs = ["/usr/share/kicad/footprints"]
fp_bboxes = load_footprint_bboxes(fp_names, fp_lib_dirs)

constraints = LayoutConstraints(outline=BoardOutline(60.0, 40.0))
placed = place_parts(extract_groups(ckt), constraints, fp_bboxes)

result = validate(placed, ckt, fp_bboxes, outline=constraints.outline)
print(result.summary())

write_kicad_pcb(placed, ckt, fp_lib_dirs, "board.kicad_pcb", outline=constraints.outline)
```

## Conventions the Layout Engine Relies On

These aren't optional style choices — the placement algorithm uses them for detection and auto-placement.

### Decoupling Caps
The placer auto-detects decoupling caps and places them 1.5mm from their parent IC.

Detection criteria (`layout/roles.py`):
- **2-pin component** (capacitor)
- **Value matches** `^(100n|0\.1u)` (case-insensitive) — so `100nF`, `100n`, `0.1uF` all work
- **One pin on a power net, one on a ground net**

If you name your cap `0.1u` or `104` or `bypass` it won't be detected. Use `100nF`.

### Power Net Naming
Power and ground nets are detected by regex (`layout/roles.py`):
- **Power**: `VCC`, `VDD`, `VDDA`, `DVDD`, `AVDD`, `IOVDD`, `VBUS`, `VIN`, `VOUT`, `VBAT`, `VREF`, `+3.3V`, `+5V`, etc.
- **Ground**: `GND`, `VSS`, `DGND`, `AGND`, `GNDA`, `GNDD`

Nets named `POWER_RAIL` or `SUPPLY` won't be recognised. Stick to the standard names.

### Grid Layout Thresholds (Schematic Placer)
The schematic auto-placer (`schematics/place.py`) has two grid layout triggers:
- **Floating parts** (disconnected from other parts): grid-placed when count > `_FLOAT_GRID_THRESHOLD` (20)
- **Part blocks**: grid-placed when block count > `_ROW_PLACE_THRESHOLD` (20)

To get grid placement for a large sensor array (e.g. 16 identical sensors), put them all in **one `@subcircuit`** so the total part count exceeds 20. Splitting across multiple small subcircuits defeats this.

### Subcircuit Hierarchy
`@subcircuit` groups parts into placement groups. The layout engine:
- Places parts within the same subcircuit near each other
- Uses net adjacency within a group to determine which passives belong to which IC
- Generates one hierarchical sheet per subcircuit in the schematic

Keep subcircuits at 5-15 parts for clean schematics, but merge into larger groups (20+) when you need grid layout.

## Placement Algorithm (4 Layers)

`place_parts()` places components in priority order:

1. **Fixed positions** — parts with explicit `FixedPosition` constraints
2. **Decoupling caps** — detected by value/net pattern, placed 1.5mm right of parent IC
3. **Signal passives** — 2-pin parts stacked below their most-connected IC
4. **Remaining parts** — shelf-packed near the largest IC in each group

## Human-in-the-Loop Workflow

For production boards:
1. Generate initial PCB with layout engine
2. Open in KiCad, manually place ICs/connectors/critical parts
3. Re-read positions: `read_placed_positions("board.kicad_pcb")`
4. Feed as `FixedPosition` constraints, re-run placer for passives
5. `validate()`, adjust, repeat

## Validation and Scoring

`validate()` checks:
- **Overlaps**: part pairs whose bounding boxes intersect
- **Outline violations**: parts placed outside the board outline
- **Missing refs**: parts in circuit but not placed
- **HPWL**: half-perimeter wirelength for each net (lower = better routing)

`scoring.py` additionally warns if:
- Decoupling caps are >5mm from their parent IC
- Power nets have excessive wirelength
