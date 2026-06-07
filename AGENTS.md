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
- Translate natural phrases into proposed assumptions with confidence and provenance. Example: "3xAAA" may become a suggested `VBAT` source with nominal 4.5V and range 3.0V-4.8V, provenance `"user said 3xAAA"`, confidence medium.
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
        {
            "net": "VBAT",
            "voltage": 4.5,
            "provenance": "user said 3xAAA, nominal 1.5V × 3",
            "confidence": 0.85,
        },
    ],
    "loads": [
        {
            "net": "3V3",
            "current": 0.15,
            "provenance": "generic MCU estimate, no datasheet",
            "confidence": 0.3,
        },
    ],
    "rail_assertions": [
        {
            "net": "3V3",
            "nominal": 3.3,
            "tolerance": 0.05,
            "provenance": "user said 3.3V MCU",
            "confidence": 0.9,
        },
    ],
    "ratio_assertions": [
        {
            "output_net": "VOUT",
            "input_net": "VIN",
            "ratio": 0.5,
            "tolerance": 0.05,
            "provenance": "computed from divider R values",
            "confidence": 0.95,
        },
    ],
    "probes": [
        {"net": "VBAT", "provenance": "user", "confidence": 1.0},
    ],
}

report = apply_simulation_intent(intent, circuit=ckt, strict=True)
print(report.summary())
```

**Intent v1 schema rules:**
- `version` (required): must be `1`.
- All items require `provenance` (non-empty string) and `confidence` (float, 0.0–1.0) in strict mode (default).
- Loads: exactly one of `resistance` (ohms, positive float) or `current` (amps, positive float).
- Sources: `voltage` (float, finite).
- Rail assertions: `nominal` (float), optional `tolerance` (positive float, default 0.05).
- Ratio assertions: `output_net`, `input_net`, `ratio` (float), optional `tolerance`.
- All numeric fields must be actual numbers — not strings like `"10mA"` or `"5%"`.
- Unknown keys are errors in strict mode, warnings in non-strict.
- Validation is transactional: all items valid or none applied.

### Layout Engine (`src/skidl/layout/`)
Automatic PCB part placement with decoupling cap awareness, power net detection, and validation. This is a separate pipeline from schematic generation — always run both.

Key modules:
- `hierarchy.py`: `extract_groups(circuit)` — groups parts by subcircuit
- `placer.py`: `place_parts(groups, constraints, fp_bboxes)` — placement algorithm
- `constraints.py`: `LayoutConstraints`, `BoardOutline`, `FixedPosition`, `KeepOut`, `EdgeAnchor`, `AnchorZone`
- `writer.py`: `write_kicad_pcb(placed, circuit, fp_lib_dirs, output_path, outline)`
- `validator.py`: `validate(placed, circuit, fp_bboxes, outline)` — overlap/violation checks
- `engine.py`: `plan_layout(circuit, ...)` — high-level orchestrator combining all steps
- `roles.py`: Power/ground net detection and decoupling cap detection helpers
- `scoring.py`: Placement quality scoring, including decap and power-net risks

## Full Pipeline: Schematic → Layout → PCB

For PCB-generating tasks, use the full flow:
1. Build or modify the circuit with footprints on every physical part.
2. Generate/check the schematic.
3. Run `plan_layout()` or the lower-level layout pipeline.
4. Validate placement and report violations/score risks.
5. Write a KiCad PCB when requested or when a board artifact is needed for review.

Do not stop at `generate_schematic()` when the task asks for a board, PCB, or layout.

## Layout Tool Usage For Agents

Use the layout engine as a full pipeline with validation, not as a one-shot placement oracle.

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

## Layout Conventions

These conventions are detection inputs, not cosmetic preferences:

### Decoupling Caps
The placer detects decoupling caps using:
- 2-pin capacitor
- value matching `^(100n|0\.1u)` case-insensitively
- one pin on a power net and one on a ground net

Prefer values like `100nF`, `100n`, or `0.1uF` when the capacitor is intended as local decoupling.

### Power Net Naming
Power and ground nets are regex-detected. Prefer standard names:
- Power: `VCC`, `VDD`, `VDDA`, `DVDD`, `AVDD`, `IOVDD`, `VBUS`, `VIN`, `VOUT`, `VBAT`, `VREF`, `+3.3V`, `+5V`
- Ground: `GND`, `VSS`, `DGND`, `AGND`, `GNDA`, `GNDD`

### Subcircuit Hierarchy
`@subcircuit` groups parts into placement groups. The layout engine places parts in the same subcircuit near each other and uses net adjacency within a group to associate passives with ICs. For large repeated arrays, keep enough related parts in one subcircuit for grid/array placement to trigger.
