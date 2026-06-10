# eda-mcp Architecture

Internal architecture doc. Last updated 2026-06-10.


## What Is This?

eda-mcp is an agent-callable PCB generation engine. AI agents (ours or
third-party) send a JSON circuit specification over MCP (Model Context
Protocol), and the engine produces a KiCad schematic and PCB layout. No
Python, no SKiDL API knowledge, no graphical editor -- just a JSON object
describing parts, nets, and board metadata.

The key innovation is the correction loop. When the engine hits a problem --
a pin name that does not exist on the real KiCad symbol, a footprint that is
not installed, parts that overlap on the board -- it does not return a vague
error string. It returns a structured DesignException carrying machine-readable
resolution candidates. The calling agent picks a candidate by id, applies it,
and re-runs. No natural language needed for course correction; the entire
fix/retry cycle is deterministic JSON in, deterministic JSON out.

Under the hood, the engine is built on SKiDL (Dave Vandenbout's
"infrastructure as code" for electronics). SKiDL is a dependency -- it handles
symbol resolution, netlist generation, schematic rendering, and the layout
algorithm. eda-mcp wraps SKiDL in a product layer: rigid JSON input contract,
subprocess isolation, structured exceptions, telemetry, and an LLM operations
layer for translating marketing descriptions into circuit specs during
benchmark runs.


## The Pipeline

```
                                  Correction Loop
                                  (deterministic or LLM-reviewed)
                                       |
                                       v
  Marketing          LLM           JSON            5-Pass          SKiDL
  Description  --->  (OpenRouter)  CircuitSpec --> Translator  --> Circuit
  (optional)         nl_to_input   (rigid JSON)    (translator.py) (in-memory)
                     _spec()                            |
                                                       | validation
                                                       | failures?
                                                       |
                                      +-----YES--------+--------NO-------+
                                      |                                   |
                                      v                                   v
                                 DesignExceptions              generate_schematic()
                                 with Candidates                     |
                                      |                              v
                                      v                        plan_layout()
                                 Agent picks                         |
                                 candidate_id                        v
                                      |                        write_kicad_pcb()
                                      v                              |
                                 apply_candidate()                   v
                                 (mutates spec)               .kicad_sch + .kicad_pcb
                                      |                        + telemetry record
                                      v
                                 Re-enter pipeline
                                 at Translator
```

The full flow from a corpus run perspective:

```
  manifest.jsonl
       |
       v
  run_corpus.py            For each board row:
  (async workers)
       |
       +---> _spec_for_row()  --> nl_to_input_spec() or load cached JSON
       |
       +---> run_pipeline()   --> subprocess: engine_worker.py
       |         |
       |         +---> translate(spec)      [5-pass validation]
       |         +---> generate_schematic() [SKiDL schematic gen]
       |         +---> plan_layout()        [SKiDL layout engine]
       |         +---> write_kicad_pcb()    [PCB file output]
       |
       +---> actionable_exceptions()?
       |         |
       |         +--YES--> review_choices() --> apply_choices() --> loop
       |         +--NO---> score_reference() --> write_final_record()
       |
       v
  telemetry/runs.jsonl     One RunRecord per board, always written
```


## Module Map

### schemas/ -- The Contract Layer

The rigid input/output contract between the calling agent and the engine.
CircuitSpec (circuit_spec.py) defines the JSON schema: parts with reference
designators, KiCad library symbols or custom pin-defined parts, nets as lists
of "REF.PIN" endpoints, and board metadata (name, form factor, outline hint,
layer count). All validated with Pydantic; malformed input never reaches the
engine.

DesignException and Candidate (exceptions.py) define the structured error
contract. Every failure carries an ExcCode (one of 18 stable codes from
SPEC_MALFORMED through BUDGET_EXHAUSTED), a Severity (fatal/error/advisory),
and a list of resolution Candidates ordered best-first. Each Candidate has an
ActionType (one of 12 mutation verbs like REPLACE_PIN, SCALE_OUTLINE,
ACCEPT_ADVISORY), typed params, a human_summary for the reviewing agent, and
a cost_hint.

apply_candidate (corrections.py) is a pure function: deep-copy the spec,
apply one candidate's action, return the new spec. It covers all 12 action
types. The engine re-validates the result on the next pass, so a bad pick
re-raises rather than silently producing garbage.

translator.py is the 5-pass deterministic validator that converts a
CircuitSpec into a live SKiDL Circuit object. The passes run in order, each
collecting ALL errors before stopping:

  Pass 1: net pin cross-references (every "REF.PIN" names a real part ref)
  Pass 2: symbol libraries exist on disk
  Pass 3: parts exist within their libraries
  Pass 4: footprints exist on disk
  Pass 5: pins exist on resolved symbols (numbers, names, or aliases)
  Build:  only when passes 1-5 are clean

Every failure is a DesignException whose candidates are computed
deterministically using difflib closest-match lookups against real library
contents. No LLM is involved in candidate generation -- this is pure
filesystem-and-string-distance logic.

### telemetry/ -- Overnight Data Collection

Every engine run produces exactly one RunRecord (models.py) appended to a
JSONL file. The record captures: run identity (run_id, board_id, git SHA),
timing (wall time, CPU time, peak RSS), geometry features (component count,
net count, pad density), LLM stage accounting (model, tokens, cost per call),
correction history, layout quality metrics (HPWL, congestion score), and
outcome (status, failure reason, reference oracle scores).

store.py handles crash-safe persistence. atomic_append() uses a single
os.write() syscall on an O_APPEND file descriptor followed by fsync -- on a
local filesystem this guarantees concurrent writers never interleave partial
lines. The session() context manager creates a RunRecord, yields it for
filling, and always persists it in the finally block -- even on
KeyboardInterrupt. If the body raises and the caller never set an explicit
status, the record is stamped "crashed" with the exception message.

features.py extracts GeometryFeatures (component count, net count, pin count,
pad count, board area, pad density) from a spec dict and worker metrics. Pure
dict-in, model-out -- no engine imports.

### llm/ -- OpenRouter LLM Operations

Two LLM operations, both designed so mid-tier models (Llama-3.3-70B class)
succeed: rigid system prompts, JSON-only output requirements, forgiving
fence-stripping parser, and exactly one repair retry per operation.

nl_to_input_spec() (operations.py) converts marketing text into a validated
CircuitSpec. The system prompt includes the full JSON Schema, design rules
(footprint format, power net naming, decoupling cap conventions), and
worked examples loaded from the corpus. If the first completion fails
Pydantic validation, a repair message including the error text is sent for
one more attempt. If both fail, SpecParseError is raised carrying the stage
dicts for cost accounting.

review_exceptions() picks correction candidates for a set of
DesignExceptions. The prompt renders each exception and its candidates in a
structured text format and asks for a JSON array of {exception_id,
candidate_id} choices. Same one-retry pattern. If both LLM attempts produce
invalid output, the function falls back to deterministic c1 picks (the
first candidate for each exception -- the "deterministic policy fallback").

openrouter_client.py is a thin async httpx client with retry/backoff
(1s/4s/10s), defensive usage parsing, table-based cost fallback when
OpenRouter does not report credits cost, and optional SpendTracker
integration.

spend_tracker.py enforces a hard budget cap ($10 default) across all LLM
calls in a corpus run. preflight() estimates cost before each request
(conservative: chars/4 as input tokens, full max_tokens as output).
commit() records actual cost and appends a JSON line to the spend log.
Thread-safe via a lock.

config.py holds model selection. Default for both design and review is
meta-llama/llama-3.3-70b-instruct ($0.10/$0.32 per Mtok). Frontier model
(anthropic/claude-sonnet-4.5, $3/$15 per Mtok) is reserved for escalation
but not currently wired into any automatic path.

### mcp_server/ -- The MCP Tool Surface

Three MCP tools exposed via FastMCP over stdio transport:

  generate_design(input_spec, run_options, policy)
    Validates the spec, runs the full pipeline, optionally applies safe
    auto-corrections per the policy, and returns a DesignResponse with
    outputs, exceptions, and a recommended_next_tool hint.

  apply_correction(run_id, corrections)
    Loads a previous run's spec and exceptions from the RunStore, applies
    the chosen candidates, and re-runs the pipeline with the mutated spec.

  get_run_telemetry(run_id)
    Returns the persisted spec, exceptions, and response JSON for a run.

pipeline.py is the subprocess isolation layer. run_pipeline() serializes
the CircuitSpec as JSON, launches engine_worker.py as a child process
(subprocess.Popen with start_new_session=True), feeds the spec on stdin,
reads the result JSON from stdout, and enforces a timeout. If the worker
exceeds the timeout, the entire process group is killed via os.killpg(). If
the worker crashes or returns invalid JSON, structured crash/timeout
exceptions are synthesized. The worker runs in its own process to isolate
SKiDL's global state (default_circuit, tool settings) from the server
process.

engine_worker.py is the subprocess entry point. It reads a JSON envelope
from stdin, calls translate() for 5-pass validation, runs
generate_schematic() and plan_layout() from SKiDL, writes the PCB file, and
emits exactly one JSON result on stdout. Resource usage (CPU time, peak RSS)
is captured via getrusage(). All exceptions are caught and converted to
structured JSON -- the worker never prints a Python traceback to stdout.

policy.py controls how much correction work generate_design may do
internally before returning to the agent. The GeneratePolicy model defines
auto_apply level (none/advisory_only/safe), max_internal_corrections (0-8),
and stop_for categories (mechanical_constraint, bom_substitution,
unknown_pinout, etc.). The decision_kind() function classifies a set of
exceptions by the actions their candidates require, so the server knows
whether to auto-fix or surface the decision.

exception_mapper.py converts engine outcomes (layout validation results,
score warnings, worker crashes, timeouts) into DesignException objects with
appropriate candidates. It also handles waiver suppression -- advisory
exceptions whose stable key appears in spec.waivers are dropped.

runs.py is a simple run store that mirrors each run to disk as
artifacts/runs/{run_id}/spec.json, exceptions.json, response.json.

### corpus/ -- Benchmark and Reference Board Pipeline

The overnight corpus runner (run_corpus.py) executes a manifest of boards
through the full pipeline with checkpoint/resume, bounded concurrency, budget
caps, and three execution modes:

  engine_only:  cached CircuitSpec JSON -> engine (no LLM calls)
  internal:     marketing text -> nl_to_input_spec() -> engine + LLM review
  external:     same, but review prompts are framed as a third-party API consumer

The runner supports both MCP transport (MCPDesignClient, long-lived stdio
session) and direct in-process execution (DirectDesignClient, used with
--no-mcp). Checkpoint/resume works by reading completed (board_id, mode)
pairs from the telemetry JSONL.

fetch_corpus.py performs idempotent shallow clones of reference PCB
repositories (KiCad-native Arduino boards, STM32 Blue Pill, ESP32-C3 devkit,
Mutable Instruments Eurorack, VESC6, etc.) across 4 difficulty tiers.

reference_oracle.py compares generated specs against real KiCad schematics
by extracting netlists via kicad-cli and scoring BOM similarity (component
overlap) and netlist similarity (connection overlap).

build_manifest.py and kicad_to_spec.py handle manifest generation and
conversion of existing KiCad projects into CircuitSpec JSON for the
engine_only path.


## The Correction Loop

This is the core product innovation. Here is exactly how it works.

When the translator or engine hits a problem, it creates a DesignException.
The exception carries a stable ExcCode, a severity, and an ordered list of
Candidates. Each candidate is a self-contained mutation instruction: an
ActionType enum value plus a params dict. Candidates are ordered best-first;
c1 is always the deterministic-policy pick -- the cheapest fix that the
engine believes is most likely correct.

Example: the spec references pin "VBUS" on part U1, but the real KiCad
symbol only has pins named "VCC", "GND", "SDA", "SCL". The translator
creates:

```json
{
  "id": "e3",
  "code": "SPEC_UNKNOWN_PIN",
  "severity": "fatal",
  "message": "pin 'VBUS' not found on U1 (Sensor_Temperature:TMP102)",
  "subject": {"ref": "U1", "pin": "VBUS", "net": "VCC",
              "available_pins": ["GND", "SCL", "SDA", "VCC"]},
  "candidates": [
    {"id": "c1", "action": "replace_pin",
     "params": {"ref": "U1", "old": "VBUS", "new": "VCC"},
     "human_summary": "connect to pin 'VCC' of U1", "cost_hint": "free"},
    {"id": "c2", "action": "replace_pin",
     "params": {"ref": "U1", "old": "VBUS", "new": "SDA"},
     "human_summary": "connect to pin 'SDA' of U1", "cost_hint": "free"},
    {"id": "c5", "action": "remove_net_pin",
     "params": {"net": "VCC", "pin": "U1.VBUS"},
     "human_summary": "drop this endpoint from the net", "cost_hint": "free"}
  ]
}
```

To fix this, the agent (or the corpus runner's deterministic fallback)
responds with:

```json
{"exception_id": "e3", "candidate_id": "c1"}
```

apply_candidate() deep-copies the spec and applies the REPLACE_PIN action:
every occurrence of "U1.VBUS" in every net's pins list is replaced with
"U1.VCC". The mutated spec is fed back into the translator for
re-validation. If the fix was correct, pass 5 now succeeds and the build
proceeds. If it was wrong (maybe VCC was already connected), the translator
raises a new exception and the loop continues.

The correction loop has three operating modes:

1. Deterministic fallback (engine_only mode): always pick c1 for every
   exception. No LLM calls. Fast, cheap, succeeds when the engine's
   best-first ordering is correct.

2. LLM-reviewed (internal mode): send the exceptions to a mid-tier model
   via review_exceptions(). The model sees each exception's candidates
   rendered as structured text and picks one per exception. If both LLM
   attempts produce invalid output, falls back to deterministic c1.

3. Agent-driven (MCP tool surface): the calling agent receives the
   DesignResponse with exceptions, inspects them, and calls
   apply_correction() with its choices. The loop runs across MCP tool
   calls rather than within a single function.

The loop is capped at max_iters (default 8) to prevent infinite cycling.
A per-board token cap (200K) and a global spend cap ($10) provide additional
bounds in LLM modes.


## Data Flow: One Board End to End

Tracing the "feather-rp2040" board through an engine_only corpus run:

1. run_corpus.py reads manifest.jsonl, finds the feather-rp2040 row with
   a spec_path pointing to a cached CircuitSpec JSON.

2. load_cached_spec() reads and validates the JSON via Pydantic.

3. run_pipeline() is called. It creates a run_id, serializes the spec,
   and launches engine_worker.py as a subprocess.

4. The worker calls translate(spec). Pass 1-5 validate all pin references,
   libraries, parts, footprints, and pins against the real KiCad
   installation. If clean, a SKiDL Circuit is built.

5. generate_schematic() produces .kicad_sch files in the run directory.
   Auto-stub mode handles power nets; ERC runs up to 8 iterations.

6. plan_layout() places components using the 4-layer algorithm (fixed
   positions, decoupling caps, signal passives, shelf-pack remainder).

7. write_kicad_pcb() writes the .kicad_pcb file.

8. The worker collects metrics (CPU time, peak RSS, pad count, board area,
   HPWL, congestion) and emits a single JSON result on stdout.

9. pipeline.py reads the result, converts it to a DesignResponse,
   and records telemetry via session().

10. Back in run_corpus.py: if exceptions exist and correction_iterations <
    max_iters, deterministic_choices() picks c1 for each, apply_choices()
    mutates the spec, and step 3 repeats.

11. When the loop terminates (success, max iters, or no applicable
    candidates), score_reference() compares the final spec against the
    real KiCad project (if validation_mode=reference).

12. write_final_record() persists one RunRecord to telemetry/runs.jsonl
    via session() + atomic_append(). The record includes everything:
    identity, timing, geometry, LLM stages, corrections, scores, status.

What goes in: a JSON CircuitSpec (or marketing text + LLM).
What comes out: .kicad_sch, .kicad_pcb, and a RunRecord in the JSONL store.
What gets logged: every run attempt, every correction applied, every LLM
call with token counts and costs, every quality metric.


## Key Design Decisions

### Subprocess isolation

The engine worker runs in a separate process (subprocess.Popen with
start_new_session=True). This is not for security -- it is because SKiDL
uses module-level global state (default_circuit, default_tool, part caches).
Running two designs in the same process would corrupt shared state. The
subprocess boundary also provides a clean timeout mechanism: if the worker
hangs, the server kills the entire process group.

### JSON spec, not Python

The input is a rigid JSON schema, not a Python script. This matters because:
(a) the calling agent does not need to know SKiDL's API, just the JSON
contract; (b) the spec is deterministic data that can be validated, diffed,
stored, and replayed; (c) apply_candidate() can mutate the spec as data
without executing arbitrary code; (d) the correction loop stays in JSON
space end to end -- no code generation or eval anywhere.

### Mid-tier model default

Both design translation and exception review default to
meta-llama/llama-3.3-70b-instruct ($0.10/$0.32 per Mtok). The overnight
corpus run processes dozens of boards; using a frontier model would blow the
budget. The prompts are designed for mid-tier success: rigid JSON output
requirements, worked examples, one repair retry, and a deterministic c1
fallback when the model produces invalid output. The frontier model
(claude-sonnet-4.5) exists in config but is reserved for future escalation
paths.

### Checkpoint/resume

The corpus runner reads completed (board_id, mode) pairs from
telemetry/runs.jsonl and skips them. This means a crashed or timed-out run
can be restarted with the same command and it picks up where it left off.
The --force flag overrides this for re-runs.

### Atomic telemetry writes

telemetry/store.py uses O_APPEND + single os.write() + fsync for each
record. This guarantees:
- Concurrent workers never interleave partial lines
- A corrupt line costs one record, never the file (the reader skips
  unparsable lines with a warning)
- session() always writes, even on KeyboardInterrupt
- No database dependency -- just a JSONL file

### Deterministic candidate generation

The translator computes candidates using difflib.get_close_matches() against
real library/footprint/pin listings on disk. This means candidates are
always valid (they reference things that actually exist) and are computed
without any LLM involvement. The LLM's only role is choosing between
candidates -- it never invents fixes.

### Spend tracking

The SpendTracker enforces a hard budget cap ($10 default) across all LLM
calls in a corpus run. preflight() estimates cost before each request using
a conservative formula (chars/4 as input tokens, full max_tokens as output).
commit() records actual cost from the OpenRouter response. If the cap would
be exceeded, BudgetExhausted is raised and the runner degrades to
engine_only mode with cached specs.

### Waiver system

Advisory exceptions (HIGH_CONGESTION, LONG_POWER_NET) can be waived by
adding their stable key to spec.waivers. The ACCEPT_ADVISORY action type
does exactly this: it appends the waiver key to the spec, and on the next
run the exception_mapper suppresses matching advisories. This prevents the
correction loop from cycling on non-blocking quality warnings.
