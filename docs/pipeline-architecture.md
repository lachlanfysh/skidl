# Engine Pipeline Architecture

## Current State

```
engine_worker.run()
  1. validate spec (Pydantic)
  2. translate(spec) → SKiDL circuit    [catches lib/pin/footprint errors]
  3. generate_schematic()               [ERC: floating pins, undriven inputs]
  4. plan_layout()                      [placement: force-directed + SA]
  5. write_kicad_pcb()                  [write unrouted PCB]
  6. layout_exceptions()                [overlaps, outline violations]
  STOP — returns unrouted board
```

**What's missing:** routing, DRC, design completeness checks, sim. The board
artifact is unrouted and unvalidated beyond placement. Agents get no feedback
on missing bulk caps, unroutable nets, or clearance violations.

**Redundancy introduced:** `schemas/review.py` (the `review_design` MCP tool)
reimplements checks that belong in the pipeline — bulk cap detection, I2C
pull-up checks, marketing cross-referencing. These duplicate logic already in
`schemas/enrichment.py` and planned for `src/skidl/sim/`.

## Proposed Pipeline

```
engine_worker.run()
  1. validate spec                      [existing — Pydantic]
  2. translate(spec) → SKiDL circuit    [existing — lib/pin/footprint errors]
  3. enrich(circuit)                    [existing — add missing passives]
  4. design_review(spec, circuit)       [NEW — completeness checks → advisory exceptions]
  5. generate_schematic()               [existing — ERC]
  6. plan_layout()                      [existing — placement]
  7. write_kicad_pcb()                  [existing — write PCB]
  8. layout_exceptions()                [existing — overlaps, outline]
  9. route(pcb_path)                    [NEW — Freerouting via Docker subprocess]
 10. drc(routed_pcb_path)              [NEW — kicad-cli pcb drc → clearance exceptions]
 11. collect results                    [existing — metrics, artifacts, exceptions]
```

Every stage that fails produces `DesignException` objects with candidates.
The worker's auto-correction loop (policy-driven) handles fixable issues.
Unfixable issues go back to the agent via get_job().

### Stage 4: design_review (completeness)

**Purpose:** Catch missing components that enrichment didn't add and ERC
won't flag. A circuit without a bulk cap is electrically valid — ERC passes —
but it's bad engineering.

**Implementation:** Move the check logic from `schemas/review.py` into the
engine pipeline as a post-enrich, pre-schematic stage. The checks become
exception producers, not a separate MCP tool.

Checks:
- IC power pins without decoupling caps → `DESIGN_MISSING_DECAP` (advisory)
- Power rails without bulk caps → `DESIGN_MISSING_BULK_CAP` (advisory)
- I2C nets without pull-ups → `DESIGN_MISSING_PULLUP` (advisory)
- Address pins floating → `DESIGN_FLOATING_ADDR` (advisory)
- Reset lines without pull-ups → `DESIGN_MISSING_RESET_PULLUP` (advisory)
- Open-drain outputs without pull-ups → `DESIGN_MISSING_OD_PULLUP` (advisory)
- USB-C without CC pull-downs → `DESIGN_MISSING_CC` (warning)
- No connectors → `DESIGN_NO_CONNECTOR` (error)
- Marketing text cross-reference mismatches → `DESIGN_MISSING_FEATURE` (advisory)

Severity: mostly advisory (waivable). Missing connectors = error. Missing
USB-C CC resistors = warning (functional failure).

Candidates: each exception carries an `add_parts` candidate that injects the
missing component(s) — same as enrichment would, but surfaced as a choice
rather than silently applied. This lets the agent (or auto_apply policy)
decide.

### Stage 9: route

**Purpose:** Produce a routed PCB. An unrouted board is not a deliverable.

**Implementation:**
```python
def route_pcb(pcb_path: str, timeout_s: float = 120) -> RouteResult:
    """Export DSN, run Freerouting, import SES, return result."""
    dsn_path = pcb_path.replace(".kicad_pcb", ".dsn")
    ses_path = pcb_path.replace(".kicad_pcb", ".ses")

    # Export DSN via pcbnew Python API
    import pcbnew
    board = pcbnew.LoadBoard(pcb_path)
    pcbnew.ExportSpecctraDSN(board, dsn_path)

    # Route via Freerouting (Docker preferred, local JAR fallback)
    # Docker: bundles correct JRE, respects -mp as hard limit
    subprocess.run([
        "docker", "run", "--rm", "-v", f"{work_dir}:/work",
        "--entrypoint", "", "ghcr.io/freerouting/freerouting:latest",
        "java", "-jar", "/app/freerouting-executable.jar",
        "-de", "/work/board.dsn", "-do", "/work/board.ses",
        "-mp", "10", "-mt", "4"
    ], timeout=timeout_s)

    # Import routed traces
    board = pcbnew.LoadBoard(pcb_path)
    pcbnew.ImportSpecctraSES(board, ses_path)
    pcbnew.SaveBoard(pcb_path, board)  # overwrite with routed version

    return RouteResult(...)
```

**Exceptions produced:**
- `ROUTE_UNCONNECTED` (error) — nets that Freerouting couldn't route.
  Subject: net name + endpoints. Candidates: `scale_outline`,
  `set_outline`, or `regenerate` (placement has randomness).
- `ROUTE_CONGESTION` (advisory) — routing succeeded but congestion is high.
- `ROUTE_TIMEOUT` (error) — Freerouting hit pass limit without converging.
  Candidate: increase layer count, enlarge board.

**Runtime:** 5-30s for simple boards, 2-10 min for dense boards. Runs on
Railway worker (Docker available). The timeout_s from run_options applies
to the full pipeline including routing.

**Fallback when Docker unavailable:** Skip routing, return unrouted PCB with
an advisory exception noting routing was skipped. Local development (stdio
server) may not have Docker.

### Stage 10: drc

**Purpose:** Catch clearance violations, unconnected items, and other
physical errors that only show up after routing.

**Implementation:**
```python
def run_drc(pcb_path: str) -> list[DesignException]:
    """Run kicad-cli DRC and parse the JSON report."""
    drc_json = pcb_path.replace(".kicad_pcb", "_drc.json")
    subprocess.run([
        "kicad-cli", "pcb", "drc",
        "--exit-code-violations",
        "-o", drc_json,
        "--format", "json",
        pcb_path,
    ], capture_output=True, timeout=30)

    with open(drc_json) as f:
        report = json.load(f)

    # Map DRC violations to DesignException objects
    return drc_to_exceptions(report)
```

**Exceptions produced:**
- `DRC_CLEARANCE` (error) — trace-to-trace or trace-to-pad clearance violation
- `DRC_UNCONNECTED` (error) — net endpoint not connected after routing
- `DRC_SHORT` (error) — unintended connection between nets
- `DRC_COURTYARD` (advisory) — component courtyard overlap

**Runtime:** <5s. kicad-cli is already in the Docker image.

## Reducing Redundancy

### What to remove

1. **`schemas/review.py`** — delete entirely. Its checks move into the
   pipeline's design_review stage (stage 4) as exception producers.

2. **`review_design` MCP tool** — remove from `server_http.py`. The agent
   no longer needs a pre-submission review tool because the engine catches
   everything post-submission. The correction loop handles it.

3. **Duplicate detection logic** — `review.py` reimplements helpers from
   `enrichment.py` (`_has_cap_on_net_pair`, `_has_resistor_to_power`, etc.).
   The pipeline stage reuses enrichment helpers directly — no duplication.

### What stays

- **`schemas/enrichment.py`** — stays as-is. Enrichment silently adds
  obvious passives (100nF decaps, pull-ups). The design_review stage runs
  AFTER enrichment and catches things enrichment chose not to add (bulk caps,
  marketing-text mismatches, connector presence).

- **`estimate_complexity` MCP tool** — stays. It's pre-submission validation
  of spec structure (footprints, libraries, pin names), not design review.
  Fast and free, catches formatting errors before spending a run.

- **Progressive disclosure hints** — stay. They guide the agent through the
  workflow (poll, correct, fetch) but no longer point to a review_design tool.

### New exception codes

Add to `schemas/exceptions.py`:

```python
class ExcCode(str, Enum):
    # ... existing codes ...

    # Design completeness (stage 4)
    DESIGN_MISSING_DECAP = "DESIGN_MISSING_DECAP"
    DESIGN_MISSING_BULK_CAP = "DESIGN_MISSING_BULK_CAP"
    DESIGN_MISSING_PULLUP = "DESIGN_MISSING_PULLUP"
    DESIGN_FLOATING_ADDR = "DESIGN_FLOATING_ADDR"
    DESIGN_MISSING_RESET_PULLUP = "DESIGN_MISSING_RESET_PULLUP"
    DESIGN_MISSING_OD_PULLUP = "DESIGN_MISSING_OD_PULLUP"
    DESIGN_MISSING_CC = "DESIGN_MISSING_CC"
    DESIGN_NO_CONNECTOR = "DESIGN_NO_CONNECTOR"
    DESIGN_MISSING_FEATURE = "DESIGN_MISSING_FEATURE"

    # Routing (stage 9)
    ROUTE_UNCONNECTED = "ROUTE_UNCONNECTED"
    ROUTE_CONGESTION = "ROUTE_CONGESTION"
    ROUTE_TIMEOUT = "ROUTE_TIMEOUT"

    # DRC (stage 10)
    DRC_CLEARANCE = "DRC_CLEARANCE"
    DRC_UNCONNECTED = "DRC_UNCONNECTED"
    DRC_SHORT = "DRC_SHORT"
    DRC_COURTYARD = "DRC_COURTYARD"
```

### Candidate actions for new exceptions

Design completeness exceptions carry `add_parts` candidates:
```json
{
  "id": "c1",
  "action": "add_parts",
  "params": {
    "parts": [{"ref": "C5", "lib": "Device", "part": "C", "value": "10uF",
               "footprint": "Capacitor_SMD:C_0805_2012Metric"}],
    "net_connections": [{"net": "VCC", "pin": "C5.1"}, {"net": "GND", "pin": "C5.2"}]
  },
  "human_summary": "Add 10uF bulk capacitor on VCC rail",
  "confidence": 0.9
}
```

Routing exceptions carry `scale_outline` or `set_layers` candidates:
```json
{
  "id": "c1",
  "action": "scale_outline",
  "params": {"area_factor": 1.3},
  "human_summary": "Enlarge board 30% to provide routing space",
  "confidence": 0.7
}
```

## Work Plan

### Phase 1: Design review in pipeline (half day)

Files touched:
- `schemas/exceptions.py` — add DESIGN_* exception codes
- `schemas/corrections.py` — add `add_parts` action type
- `mcp_server/exception_mapper.py` — add `design_review_exceptions()` function
  using enrichment.py helpers (no duplication)
- `mcp_server/engine_worker.py` — call design_review after translate+enrich,
  before schematic generation. Append exceptions to result.
- `mcp_server/server_http.py` — remove `review_design` tool, update hints
- `schemas/review.py` — delete

Test: Run Opus agent on ina219. Engine should return DESIGN_MISSING_BULK_CAP
advisory. Agent applies the `add_parts` candidate. Re-run succeeds with the
bulk cap included.

### Phase 2: Routing in pipeline (half day)

Files touched:
- `mcp_server/engine_worker.py` — add `_route_pcb()` after `write_kicad_pcb()`
- `mcp_server/exception_mapper.py` — add `route_exceptions()` to parse
  Freerouting output and map unrouted nets to ROUTE_* exceptions
- `schemas/exceptions.py` — add ROUTE_* codes
- `Dockerfile` — ensure Freerouting Docker image is available (or bundle
  freerouting JAR + JRE in the worker image)

Dependency: pcbnew Python bindings for DSN export/SES import. The Railway
Docker image already has KiCad installed — verify pcbnew is importable.

Test: Submit ina219 spec. get_job returns a routed .kicad_pcb. Open in KiCad
— traces visible. Submit a deliberately-too-small board — get ROUTE_UNCONNECTED
exceptions with scale_outline candidates.

### Phase 3: DRC in pipeline (2 hours)

Files touched:
- `mcp_server/engine_worker.py` — add `_run_drc()` after routing
- `mcp_server/exception_mapper.py` — add `drc_exceptions()` to parse
  kicad-cli JSON output
- `schemas/exceptions.py` — add DRC_* codes

Test: Submit a board, get_job returns clean DRC. Deliberately create a tight
board — get DRC_CLEARANCE exceptions.

### Phase 4: Cleanup and benchmark (2 hours)

- Delete `schemas/review.py`
- Remove `review_design` MCP tool
- Update `eda://guide/exceptions` resource text with new exception codes
- Update `eda://guide/workflow` to mention routing as a pipeline stage
- Re-run probe9 benchmark with Opus/Sonnet
- Compare grades before/after: expect D→C lift from design review,
  plus routed board artifacts

### Docker / Railway considerations

- **Freerouting:** Two options:
  a. Docker-in-Docker on Railway worker (cleanest, but needs privileged mode)
  b. Bundle freerouting-2.0.1.jar + JRE 21 in the worker Docker image (no DinD)

  Option (b) is simpler for Railway. Add to Dockerfile:
  ```dockerfile
  RUN apt-get install -y openjdk-21-jre-headless
  COPY freerouting-2.0.1.jar /opt/freerouting/
  ```
  Then route with: `java -jar /opt/freerouting/freerouting-2.0.1.jar -de ... -do ...`

- **pcbnew bindings:** Already available — the Docker image has KiCad 9
  installed for schematic generation. Verify with `python3 -c "import pcbnew"`.

- **kicad-cli:** Already in the Docker image (comes with KiCad).

- **Timeout budget:** The existing `timeout_s` (default 300s) covers the full
  pipeline. Routing is the expensive stage — for complex boards, the agent
  should set timeout_s=900+ (the estimate_complexity hint already suggests this).
  Internally, allocate: translate+schematic+layout = first 60s,
  routing = remaining budget minus 30s, DRC = last 30s.

## Agent Experience After Changes

Before (current):
```
agent: submit_design(spec)
agent: get_job() → succeeded, 2 advisories (congestion, decap distance)
agent: get_run() → downloads unrouted .kicad_pcb
user: opens in KiCad, routes manually, discovers 3 unrouted nets
```

After (proposed):
```
agent: submit_design(spec)
agent: get_job() → failed, exceptions:
  e1: DESIGN_MISSING_BULK_CAP on VCC (advisory, candidate: add 10uF cap)
  e2: ROUTE_UNCONNECTED: net SCL has 1 break (error, candidate: scale_outline 1.2x)
agent: apply_correction(run_id, [{e1, c1}, {e2, c1}])
agent: get_job() → succeeded, clean DRC
agent: get_run() → downloads ROUTED .kicad_pcb with all traces
user: opens in KiCad, board is ready for manufacturing review
```

The agent never needs to know about routing, DRC, or design rules — the engine
handles it and surfaces problems through the same correction model. The agent's
job is intent ("build me an INA219 breakout"), not engineering.
