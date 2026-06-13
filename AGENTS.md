<!--
Migrated from CLAUDE.md for Codex on 2026-06-13.
Treat legacy references to Claude as prior-agent workflow notes unless they
specifically mention Claude CLI/auth. Keep this file in sync with CLAUDE.md
while both agent stacks are in use.
-->

# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

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

### Simulation ERC (`src/skidl/sim/`)
Schematic-stage simulation is an explicit confidence gate, not a hidden source of truth. Core SKiDL APIs are strict and rigid; agents may translate natural user intent into simulation declarations, but every inferred assumption must be visible, reviewable, and labelled with provenance.

Use the explicit harness APIs:
- `sim_source(net_or_name, voltage, ref=None, provenance="user")`
- `sim_load(net_or_name, resistance=None, current=None, ref=None, provenance="user")`
- `sim_probe(net_or_name, kind="voltage", provenance="user")`
- `sim_assert_rail(net_or_name, nominal, tolerance=0.05, provenance="user")`
- `sim_assert_node_ratio(output, input, ratio, tolerance=0.05, provenance="user")`

Rules for agents:
- Do not silently infer exact voltages/currents from ambiguous physical descriptions.
- Translate natural phrases into proposed assumptions with confidence and provenance. Example: "3xAAA" may become a suggested `VBAT` source with voltage 4.5, provenance `"user said 3xAAA"`, confidence 0.85.
- Example: "3.3V MCU" may become `sim_assert_rail("3V3", nominal=3.3, tolerance=0.05, provenance="user said 3.3V MCU")`.
- Prefer ranges or low-confidence load estimates when the datasheet is unknown; make them visible in the report.
- Never guess SPICE models for ICs, regulators, optos, sensors, MCUs, op-amps, or transistors.
- Passive R/C/L checks may be inferred from values because those are exact primitive models.
- Harness declarations are simulation-only and must not mutate schematic, PCB, or production netlist output.

When proposing assumptions, build a structured intent dict and apply it via `apply_simulation_intent()`:

```python
from skidl.sim import apply_simulation_intent

intent = {
    "version": 1,
    "sources": [
        {"net": "VBAT", "voltage": 4.5,
         "provenance": "user said 3xAAA, nominal 1.5V × 3",
         "confidence": 0.85},
    ],
    "loads": [
        {"net": "3V3", "current": 0.15,
         "provenance": "generic MCU estimate, no datasheet",
         "confidence": 0.3},
    ],
    "rail_assertions": [
        {"net": "3V3", "nominal": 3.3, "tolerance": 0.05,
         "provenance": "user said 3.3V MCU", "confidence": 0.9},
    ],
}

report = apply_simulation_intent(intent, circuit=ckt, strict=True)
```

**Intent v1 rules:**
- `version` (required): must be `1`.
- All items require `provenance` (non-empty string) and `confidence` (float, 0.0–1.0) in strict mode.
- Sources: `voltage` (float, finite). Loads: exactly one of `resistance` or `current` (positive float).
- Rail assertions: `nominal` (float), optional `tolerance` (positive float, default 0.05).
- All numeric fields must be actual numbers — not strings like `"10mA"` or `"5%"`.
- Net names must be non-empty strings.
- Unknown keys error in strict mode. Validation is transactional.

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

## Layout Tool Usage For Agents

Use the layout engine as a full pipeline with validation, not as a one-shot placement oracle. For PCB-generating tasks:
1. Build or modify the circuit with footprints on every physical part.
2. Generate/check the schematic.
3. Run `plan_layout()` or the lower-level layout pipeline.
4. Validate placement and report violations/score risks.
5. Write a KiCad PCB when requested or when a board artifact is needed for review.

Use constraints deliberately:
- `BoardOutline` for board size and shape assumptions.
- `FixedPosition` for known/manual positions.
- `EdgeAnchor` for USB, power jacks, headers, controls, or other user-facing connectors.
- `KeepOut` for mechanical, sensor, RF, mounting, or no-place regions.
- `AnchorZone` for functional regions such as sensors, power, MCU, analog, and UI.

For outline-first boards, preserve user mechanical constraints through `plan_layout()` and prefer PCB/case constraints over generic pretty placement. When the user gives real geometry, do not "improve" it away.

Always report:
- unplaced or missing parts
- overlap/outline/keepout violations
- edge connector placement quality
- decoupling cap distance from parent IC power pins
- power path/routing concerns
- high-congestion nets
- assumptions used for board size, constraints, layer count, and fixed positions

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

## Routability Testing (Freerouting CLI)

After placement and validation, test whether the board is actually routable using Freerouting headlessly. This is the final quality gate — good placement should produce a fully-routed board.

**Requirements**: Docker (preferred) or Java 21+ with Freerouting 2.0.1 JAR, plus pcbnew Python bindings (system KiCad install).

**Docker** (recommended — bundles correct JRE, respects `-mp` as hard limit):
```bash
docker run --rm -v /path/to/board:/work --entrypoint "" \
  ghcr.io/freerouting/freerouting:latest \
  java -jar /app/freerouting-executable.jar \
  -de /work/board.dsn -do /work/board.ses -mp 10 -mt 4
```

**Local JAR** (needs Java 21, Freerouting 2.0.1 — v2.2.4+ requires Java 25):
```bash
java -jar freerouting-2.0.1.jar -de board.dsn -do board.ses -mp 10 -mt 4
```

### Routability Iteration Loop

This is the core LLM-driven design loop. Each cycle takes 2-10 minutes depending on board complexity.

```
1. ROUTE — export DSN, run Freerouting, import SES
2. ANALYZE — DRC JSON → which nets failed, where are the breaks
3. DIAGNOSE — map breaks to physical constraints (bridges, congestion, layer count)
4. ADJUST — modify outline, fillets, placement, layer count, or constraints
5. RE-ROUTE — go to step 1
```

**Step 1: Route (traces first, zones after)**
```python
import pcbnew, subprocess, json, re, os
from collections import Counter

# Export — do NOT fill copper zones before export.
# Filled zones block signal routing. Route first, pour after.
board = pcbnew.LoadBoard("board.kicad_pcb")
pcbnew.ExportSpecctraDSN(board, "board.dsn")

# Route headlessly
subprocess.run(
    ["docker", "run", "--rm", "-v", f"{os.getcwd()}:/work",
     "--entrypoint", "", "ghcr.io/freerouting/freerouting:latest",
     "java", "-jar", "/app/freerouting-executable.jar",
     "-de", "/work/board.dsn", "-do", "/work/board.ses",
     "-mp", "10", "-mt", "4"],
    timeout=600,
)

# Import routed traces
board = pcbnew.LoadBoard("board.kicad_pcb")
pcbnew.ImportSpecctraSES(board, "board.ses")
pcbnew.SaveBoard("board_routed.kicad_pcb", board)
```

**Step 2: Analyze**
```python
# Run DRC to get structured failure data
subprocess.run(
    ["kicad-cli", "pcb", "drc", "--exit-code-violations",
     "-o", "drc.json", "--format", "json", "board_routed.kicad_pcb"],
    capture_output=True, timeout=30,
)

with open("drc.json") as f:
    drc = json.load(f)

# Extract failing nets and positions
unconnected = drc.get("unconnected_items", [])
nets = Counter()
for item in unconnected:
    for sub in item.get("items", []):
        m = re.search(r'\[([^\]]+)\]', sub.get("description", ""))
        if m:
            nets[m.group(1)] += 1

# Also check Y-span to identify bridge-crossing failures
# Large Y-span + unrouted = signal can't cross a physical bottleneck
```

**Step 3: Diagnose — common failure patterns:**

| Pattern | Symptom | Fix |
|---------|---------|-----|
| Power nets (VCC/GND) dominate failures | Many breaks, large Y-span | Add copper zones AFTER routing, or go 4-layer |
| Signal nets fail at bridge zones | Breaks span narrow board sections | Widen bridges, increase fillet radius on cutouts |
| ICs blocking routing channels | IC pads fill inter-cutout gaps | Increase cutout corner fillets (r=5→r=8) to taper walls |
| Local congestion near dense IC | Short Y-span breaks | Move nearby passives, widen trace/space rules |
| Everything fails | >50% unrouted | Board outline too small, or layer count insufficient |

**Step 4: Adjust — use pcbnew API to modify the board programmatically:**
```python
# Example: widen bridges by moving cutout edges
# Example: increase cutout fillet radius
# Example: add copper layer count
setup = board.GetDesignSettings()
setup.SetCopperLayerCount(4)  # 2 → 4 layers

# Example: add power zones AFTER routing is clean
zone = pcbnew.ZONE(board)
zone.SetNet(gnd_net)
zone.SetLayer(board.GetLayerID('In1.Cu'))
# ... define outline, add to board, fill
```

### Critical Rules

- **Route first, pour after.** Never fill copper zones before DSN export — filled zones consume inner layers and block signal routing. Export with empty zones, route all signals, import SES, THEN add/fill power zones in KiCad.
- **4-layer with open routing.** When using 4 layers, let Freerouting use all layers for signals. Add GND/VCC planes after routing — KiCad pours around existing traces automatically.
- **`ExportSpecctraDSN` returns `False`** if the board outline is malformed — fix outline geometry first.
- **Parse Freerouting stdout** for pass-by-pass progress: `Auto-router pass #N ... (K unrouted)`. If unrouted count plateaus for 3+ passes, stop early — more passes won't help, the board needs physical changes.
- **DRC JSON is the feedback signal.** `unconnected_items[].items[].description` contains `[NetName]` and `pos.x/y` — extract both to map failures to physical locations.
- **kicad-cli has NO specctra export** — must use pcbnew Python API (`ExportSpecctraDSN` / `ImportSpecctraSES`).
- **Typical timing:** Simple boards (<50 parts): 5-30s. Dense boards (70+ parts, constrained outline): 2-10 min per iteration.
