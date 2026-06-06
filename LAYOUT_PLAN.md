# PCB Layout Engine for SKiDL

## Context

SKiDL currently generates PCBs via `kinet2pcb` (an external dependency that requires `pcbnew`). This plan adds a native PCB layout engine to SKiDL that:

1. **Extracts hierarchy groups** from a live SKiDL `Circuit` using the existing `part.node.hiertuple` / `pin.net.pins` APIs
2. **Reads back human-placed positions** from an existing `.kicad_pcb` (ICs, connectors, switches — the stuff humans are good at placing)
3. **Auto-places remaining passives** near their parent ICs using connectivity-aware algorithms
4. **Writes `.kicad_pcb` S-expressions directly** — no `pcbnew` dependency — using `simp_sexp.Sexp`
5. **Validates** with overlap detection and loudly flags unavoidable overlaps

The workflow is **human-in-the-loop**: human places ICs and major components → engine squeezes passives into sensible positions → engine flags overlaps that need human attention. This reduces pressure on any automated floorplanning being "right" — the human makes the hard decisions, the automation handles the tedious ones.

The immediate use case is MR1 (~136 hand-placed parts + ~87 unplaced passives), but the engine is general-purpose and lives in `src/skidl/layout/`.

## Architecture

```
                    Circuit (live SKiDL object)
                         │
                    ┌────▼────┐
                    │hierarchy│  extract_groups(circuit) → dict[str, PlacementGroup]
                    └────┬────┘  uses part.node.hiertuple, pin.net.pins
                         │
              ┌──────────▼──────────┐
              │    constraints      │  FixedPosition, AnchorZone, KeepOut, BoardOutline
              │  (pure data, no IO) │  user provides OR reader.py extracts from existing .kicad_pcb
              └──────────┬──────────┘
                         │
                    ┌────▼────┐
                    │ placer  │  place_parts(groups, constraints, fp_bboxes) → positions
                    └────┬────┘  ONLY places unplaced parts; decap→IC, signal R/C→signal pin
                         │
                    ┌────▼────┐
                    │ writer  │  write_kicad_pcb(positions, circuit, fp_libs) → .kicad_pcb
                    └────┬────┘  S-expr via simp_sexp, footprint from .kicad_mod files
                         │
                    ┌────▼─────┐
                    │validator │  LOUDLY flag overlaps, report HPWL, count parts
                    └──────────┘
```

### Iterative workflow
```
1. Human places ICs/connectors in KiCad (or provides FixedPositions in code)
2. reader.py reads existing .kicad_pcb → extracts positions as FixedPositions
3. placer.py places remaining passives near their parent ICs
4. writer.py writes new .kicad_pcb with ALL parts
5. validator.py flags overlaps → human adjusts → re-run from step 2
```

## Modules to Create

All in `src/skidl/layout/`.

### 1. `hierarchy.py` (~150 lines)

Extracts subcircuit groups from a live Circuit.

```python
@dataclass
class PlacementGroup:
    name: str                           # subcircuit name from hiertuple
    parts: list[Part]                   # parts in this group
    adjacency: dict[str, set[str]]      # ref → set of refs sharing nets

def extract_groups(circuit) -> dict[str, PlacementGroup]:
    """Group parts by their immediate subcircuit node (part.node)."""
```

**Key SKiDL APIs used:**
- `part.node.hiertuple` → tuple of hierarchy path names (defined in `node.py:297-308`)
- `part.node` → the Node this part belongs to (gives subcircuit identity)
- `pin.net.pins` → all pins on same net (via `net.py:1290-1297` → `get_pins()`)
- `circuit.parts` → flat list of all parts
- `circuit.root` → root Node (the hierarchy tree is `root.children[*].children[*]...`)

Adjacency is built by iterating each net's pins and recording which parts share that net.

### 2. `constraints.py` (~80 lines)

Pure data classes, no I/O, no KiCad dependency.

```python
@dataclass
class FixedPosition:
    ref: str              # e.g. "SW1"
    x_mm: float
    y_mm: float
    rot_deg: float = 0.0

@dataclass  
class AnchorZone:
    group_name: str       # subcircuit name to anchor passives within
    x_min: float; y_min: float; x_max: float; y_max: float

@dataclass
class KeepOut:
    x_min: float; y_min: float; x_max: float; y_max: float

@dataclass
class BoardOutline:
    width_mm: float
    height_mm: float

@dataclass
class LayoutConstraints:
    fixed: list[FixedPosition]
    zones: list[AnchorZone]
    keepouts: list[KeepOut]
    outline: BoardOutline
```

### 3. `placer.py` (~250 lines)

The core algorithm. Takes hierarchy groups + constraints + footprint bounding boxes → positions for all parts.

```python
@dataclass
class PlacedPart:
    ref: str
    x_mm: float
    y_mm: float
    rot_deg: float
    footprint: str

def place_parts(
    groups: dict[str, PlacementGroup],
    constraints: LayoutConstraints,
    fp_bboxes: dict[str, tuple[float, float]],  # footprint → (width_mm, height_mm)
) -> list[PlacedPart]:
```

**Placement strategy (in order):**
1. Place all `FixedPosition` parts first
2. For each remaining part, find which placed part it shares the most nets with (from adjacency)
3. **Decoupling caps** (value matches `100n|100nF|0.1u|0.1uF`, one pin on VCC/VDD, one on GND): place within 5mm of parent IC, oriented to minimize loop area
4. **Signal passives** (series R, coupling C connected to exactly 2 nets): place near the signal pin they connect to, offset perpendicular to IC body
5. **Remaining**: group by subcircuit, shelf-pack near the subcircuit's anchor (largest IC)
6. Greedy overlap avoidance: spiral search for clear position near target
7. Optional HPWL refinement: swap adjacent pairs if total wirelength improves

### 4. `writer.py` (~300 lines)

Writes `.kicad_pcb` S-expression file directly. Uses `simp_sexp.Sexp` (same as schematic/netlist writers throughout SKiDL).

```python
def write_kicad_pcb(
    placed_parts: list[PlacedPart],
    circuit: Circuit,
    fp_lib_dirs: list[str],
    output_path: str,
    version: int = 20240108,
):
```

**Key responsibilities:**
- Parse `.kicad_mod` footprint files from library dirs (these are S-expressions — parse with `simp_sexp`)
- For each placed part: read its footprint S-expr, inject `(at X Y angle)`, inject `(path "/<sheet_uuid>/<part_uuid>")`
- **UUID generation**: Reuse the exact same `uuid.uuid5(namespace_uuid, ...)` scheme from `gen_netlist.py:19-20` and `sexp_schematic.py:34` so PCB ↔ schematic cross-reference works:
  - `namespace_uuid = uuid.UUID("7026fcc6-e1a0-409e-aaf4-6a17ea82654f")`
  - Sheet UUID from hierarchy level names: `uuid.uuid5(namespace_uuid, level)` for each level
  - Part UUID: `uuid.uuid5(namespace_uuid, part.hiername)`
  - KIID path: `"/{sheet_uuid_1}/{sheet_uuid_2}/.../{part_uuid}"`
- Write board header (version, generator, layers, setup), net declarations, all footprints, board outline
- Net declarations from `circuit.get_nets()`, matching net codes from `gen_netlist.py`

**Footprint loading from `.kicad_mod`:**
- Each `.kicad_mod` file is a single S-expression `(footprint "name" ...)`
- Parse with `simp_sexp.Sexp`, extract pad extents for bounding box, keep full S-expr for injection into board file
- Search order: user-provided `fp_lib_dirs`, then `KICAD9_FOOTPRINT_DIR` env var, then standard paths

### 5. `validator.py` (~100 lines)

Post-placement validation.

```python
@dataclass
class ValidationResult:
    overlaps: list[tuple[str, str]]          # pairs of overlapping refs
    worst_hpwl_nets: list[tuple[str, float]] # (net_name, hpwl_mm) top 10
    missing_refs: list[str]                  # refs in netlist but not placed
    total_parts: int
    placed_parts: int

def validate(
    placed_parts: list[PlacedPart],
    circuit: Circuit,
    fp_bboxes: dict[str, tuple[float, float]],
) -> ValidationResult:

def run_kicad_drc(pcb_path: str) -> str | None:
    """Shell out to kicad-cli pcb drc, return report path or None."""
```

### 6. `reader.py` (~120 lines)

Reads an existing `.kicad_pcb` to extract human-placed positions as `FixedPosition` constraints. This is the key human-in-the-loop enabler.

```python
def read_placed_positions(pcb_path: str) -> list[FixedPosition]:
    """Parse .kicad_pcb, extract (at X Y angle) from each footprint → FixedPosition per ref."""

def read_footprint_bboxes(pcb_path: str) -> dict[str, tuple[float, float]]:
    """Extract footprint bounding boxes from placed parts in existing board."""
```

**How it works:**
- Parse `.kicad_pcb` with `simp_sexp.Sexp`
- For each `(footprint ...)` block: extract `(fp_name ...)`, `(at X Y angle)`, reference from `(property "Reference" "U1" ...)`
- Parts at `(at 0 0)` are treated as "unplaced" (KiCad dumps unplaced parts at origin)
- Returns only parts that are NOT at origin — these are the human-placed ones
- Human workflow: place ICs in KiCad → run reader → feed positions as constraints → placer fills in passives

### 7. `__init__.py`

Exports the public API: `extract_groups`, `LayoutConstraints`, `FixedPosition`, `AnchorZone`, `KeepOut`, `BoardOutline`, `place_parts`, `write_kicad_pcb`, `validate`, `read_placed_positions`.

## Integration with Existing SKiDL

The layout engine does **not** modify any existing gen_pcb path. It's a new parallel capability:

**Workflow A — human places ICs first (primary, MR1 workflow):**
```python
from skidl.layout import (
    extract_groups, place_parts, write_kicad_pcb, validate,
    read_placed_positions, LayoutConstraints, BoardOutline
)

# 1. Build circuit, generate initial .kicad_pcb (even with parts at origin)
# 2. Human opens in KiCad, places ICs/connectors/switches, saves
# 3. Read back what human placed:
fixed = read_placed_positions("board.kicad_pcb")  # only non-origin parts
groups = extract_groups(circuit)
constraints = LayoutConstraints(fixed=fixed, zones=[], keepouts=[], outline=BoardOutline(300, 100))
placed = place_parts(groups, constraints, fp_bboxes)
write_kicad_pcb(placed, circuit, fp_lib_dirs=[...], output_path="board.kicad_pcb")
result = validate(placed, circuit, fp_bboxes)
# 4. Human reviews, adjusts, re-runs from step 3 if needed
```

**Workflow B — all positions in code (scripted):**
```python
constraints = LayoutConstraints(
    fixed=[FixedPosition("U1", 50, 60), FixedPosition("SW1", 30, 130), ...],
    zones=[], keepouts=[], outline=BoardOutline(300, 100)
)
# same pipeline from here
```

A future PR could wire this into `circuit.generate_pcb()` as an alternative to `kinet2pcb`.

## Implementation Order

Dependencies between modules dictate order:

```
constraints.py ──────────────┐
reader.py ───────────────────┤
                             ▼
hierarchy.py ──────────► placer.py ──► writer.py ──► validator.py
                             ▲
                       fp_bboxes from
                       writer's .kicad_mod parser
```

**Phase 1** (no inter-module deps — parallel):
- `constraints.py` — pure dataclasses, no deps
- `hierarchy.py` — depends only on SKiDL core (`circuit.parts`, `part.node`, `pin.net.pins`)
- `reader.py` — reads .kicad_pcb S-expr, depends on `simp_sexp` + `constraints` dataclasses
- `writer.py` footprint parser (the `.kicad_mod` → bounding box extraction, separate from board writing)

**Phase 2** (needs Phase 1):
- `placer.py` — needs hierarchy groups, constraints (from reader or code), and footprint bounding boxes

**Phase 3** (needs Phase 2):
- `writer.py` board writing — needs placed parts + footprint data
- `validator.py` — needs placed parts + footprint bounding boxes

**Phase 4**: Integration test with a real circuit

## Files to Create

| File | Lines (est.) | Dependencies |
|------|-------------|--------------|
| `src/skidl/layout/__init__.py` | ~20 | — |
| `src/skidl/layout/constraints.py` | ~80 | — |
| `src/skidl/layout/hierarchy.py` | ~150 | `skidl.circuit`, `skidl.part`, `skidl.net` |
| `src/skidl/layout/reader.py` | ~120 | `simp_sexp`, `constraints` |
| `src/skidl/layout/placer.py` | ~250 | `constraints`, `hierarchy` |
| `src/skidl/layout/writer.py` | ~300 | `simp_sexp`, `constraints`, uuid scheme from `gen_netlist.py` |
| `src/skidl/layout/validator.py` | ~100 | — |
| `tests/unit_tests/test_layout_hierarchy.py` | ~100 | — |
| `tests/unit_tests/test_layout_reader.py` | ~80 | — |
| `tests/unit_tests/test_layout_placer.py` | ~120 | — |
| `tests/unit_tests/test_layout_writer.py` | ~100 | — |
| `tests/unit_tests/test_layout_validator.py` | ~60 | — |

No existing files are modified.

## Testing Strategy

**Unit tests (no KiCad needed):**
- `test_layout_hierarchy.py`: Build a small circuit with `@subcircuit` decorators, verify `extract_groups` returns correct group names, part counts, and adjacency edges (e.g., 100nF cap is adjacent to its IC)
- `test_layout_placer.py`: Feed mock groups with known connectivity, verify:
  - No bounding-box overlaps in output
  - Decoupling caps placed within 5mm of parent IC
  - All parts from input appear in output
  - Fixed-position parts remain at their specified coordinates
- `test_layout_writer.py`: Generate a small `.kicad_pcb`, verify:
  - Output parses as valid S-expression
  - Footprint count matches input part count
  - Net declarations match circuit nets
  - KIID paths use correct UUID scheme (cross-check with `gen_netlist.py` functions)
- `test_layout_validator.py`: Test overlap detection with known-overlapping and non-overlapping positions

**Integration test (needs KiCad CLI):**
- Full pipeline on a multi-subcircuit test circuit
- `kicad-cli pcb drc` passes
- Skip gracefully if `kicad-cli` not available

## Verification

After implementation:
1. `pytest tests/unit_tests/test_layout_*.py` — all pass
2. `pytest tests/` — existing tests unaffected (no files modified)
3. Manual: create a test script that builds a small circuit, runs the full pipeline, opens the `.kicad_pcb` output
