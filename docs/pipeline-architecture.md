# Engine Pipeline Architecture

## Implementation Status

**Implemented** — all stages below are live in `engine_worker.py`.

```
engine_worker.run()
  1. validate spec                      [Pydantic]
  2. translate(spec) → SKiDL circuit    [lib/pin/footprint errors]
  3. design_review(spec)                [bulk caps, connectors, power rails]
  4. generate_schematic()               [ERC: floating pins, undriven inputs]
  5. plan_layout()                      [placement: force-directed + SA]
  6. write_kicad_pcb()                  [write PCB file]
  7. layout_exceptions()                [overlaps, outline violations]
  8. route(pcb_path)                    [Freerouting via bundled JAR]
  9. drc(routed_pcb_path)              [kicad-cli DRC → clearance exceptions]
  10. collect results                    [metrics + manufacturable flag]
```

## Architecture Decisions

### Single unified enrich-and-review stage

Enrichment (`schemas/enrichment.py`) and design review are **one conceptual
stage** with two output channels:

- **Apply**: obvious passives — 100nF decaps, I2C pull-ups, reset circuits,
  USB-C CC resistors, crystal load caps. No engineering judgment needed.
  Rules A1–A15, B1–B4 in enrichment.py.
- **Surface**: judgment calls — bulk caps (value depends on load), missing
  connectors, power rail flags, marketing-text features. Returned as
  `DesignException` objects with `add_parts` candidates.

Enrichment runs **pre-engine** (in the LLM pipeline, before the spec reaches
the worker). Design review runs **in the engine** (stage 3, after translate,
before schematic generation). They share detection helpers from
`schemas/enrichment.py` — no duplication.

The boundary isn't confidence — it's whether there's a meaningful choice.
A 100nF decap has no alternative. A bulk cap value depends on the load.

### Worker stays deterministic

`engine_worker.run()` is a single deterministic pass. It does not retry or
auto-correct. The **job/server layer** (`worker.py`) owns the policy loop:
retries, auto-corrections, stopping for agent decisions, telemetry, and
parent run linkage.

### Routing unavailable = error, not advisory

If routing tools aren't available (no Freerouting JAR, no Java), the engine
returns `ROUTE_UNAVAILABLE` with severity **error** — the board is not
manufacturable. The `manufacturable` field in metrics is `false`.

This prevents false success: an unrouted board should never look like a
completed deliverable to the agent.

### Routing deployment: bundled JAR

Freerouting runs as a local JAR (not Docker-in-Docker). The Railway Docker
image bundles:
```dockerfile
RUN apt-get install -y openjdk-21-jre-headless
COPY freerouting-2.0.1.jar /opt/freerouting/
```
The engine calls `java -jar /opt/freerouting/freerouting-2.0.1.jar` directly.

### DRC exit code handling

`kicad-cli pcb drc --exit-code-violations` returns nonzero for both
"report with violations" and "tool failure". The engine distinguishes:
- **JSON file exists + nonzero exit** → parse violations from report
- **No JSON file + nonzero exit** → `DRC_TOOL_FAILURE` exception (advisory)
- **JSON parse failure** → `DRC_TOOL_FAILURE` exception (advisory)

### Timeout budget

Routing is the expensive stage. The engine allocates time from a shared
budget passed via `route_timeout_s` in the envelope (default 120s). DRC
has a fixed 30s timeout. The outer `timeout_s` from run_options covers
the full pipeline; if the whole pipeline exceeds it, the process is killed
by the pipeline runner.

## Exception Codes

### Design completeness (stage 3)

| Code | Severity | Meaning |
|------|----------|---------|
| `DESIGN_MISSING_BULK_CAP` | advisory | Power rail has no bulk cap (10uF+) |
| `DESIGN_NO_CONNECTOR` | error | Board has no connectors |
| `DESIGN_NO_POWER_RAIL` | error | No power or ground rail defined |
| `DESIGN_POWER_FLAG` | advisory | Net looks like power but power=true not set |
| `DESIGN_MISSING_FEATURE` | advisory | Marketing text mentions feature not in spec |

### Routing (stage 8)

| Code | Severity | Meaning |
|------|----------|---------|
| `ROUTE_UNCONNECTED` | error | Nets Freerouting couldn't route |
| `ROUTE_CONGESTION` | advisory | Routing succeeded but congestion high |
| `ROUTE_TIMEOUT` | error | Freerouting exceeded time limit |
| `ROUTE_UNAVAILABLE` | error | Routing tools not found — board unrouted |

### DRC (stage 9)

| Code | Severity | Meaning |
|------|----------|---------|
| `DRC_CLEARANCE` | error | Trace/pad clearance violation |
| `DRC_UNCONNECTED` | error | Net endpoint not connected after routing |
| `DRC_SHORT` | error | Unintended connection between nets |
| `DRC_COURTYARD` | advisory | Component courtyard overlap |
| `DRC_TOOL_FAILURE` | advisory | kicad-cli DRC failed to run |

## Candidate Actions

### `add_parts` (design completeness)

Injects parts and net connections into the spec:
```json
{
  "action": "add_parts",
  "params": {
    "parts": [{"ref": "C5", "lib": "Device", "part": "C", "value": "10uF",
               "footprint": "Capacitor_SMD:C_0805_2012Metric"}],
    "net_connections": [
      {"net": "VCC", "pin": "C5.1"},
      {"net": "GND", "pin": "C5.2"}
    ]
  }
}
```

### `set_layers` (routing)

Changes copper layer count:
```json
{"action": "set_layers", "params": {"layers": 4}}
```

### `scale_outline` (routing/DRC)

Enlarges the board:
```json
{"action": "scale_outline", "params": {"area_factor": 1.3}}
```

## Redundancy Removal (completed)

- **`schemas/review.py`** — deleted. Structural checks moved into
  `design_review_exceptions()` in `enrichment.py`. Marketing cross-ref
  available via the same function's `marketing_text` parameter.
- **`review_design` MCP tool** — removed from `server_http.py`.
- **Detection logic** — `enrichment.py` helpers are reused directly.
  No duplication between enrichment and design review.

## Agent Experience

```
agent: submit_design(spec)
agent: get_job() → failed, exceptions:
  e1: DESIGN_MISSING_BULK_CAP on VCC (advisory, candidate: add 10uF cap)
  e2: ROUTE_UNCONNECTED: net SCL has 1 break (error, candidate: scale_outline 1.2x)
agent: apply_correction(run_id, [{e1, c1}, {e2, c1}])
agent: get_job() → succeeded, clean DRC, manufacturable=true
agent: get_run() → downloads ROUTED .kicad_pcb with all traces
user: opens in KiCad, board is ready for manufacturing review
```

The agent never needs to know about routing, DRC, or design rules — the engine
handles it and surfaces problems through the same correction model. The agent's
job is intent ("build me an INA219 breakout"), not engineering.
