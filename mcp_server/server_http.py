"""Async MCP tools for Railway-hosted HTTP server.

Provides submit/poll/fetch pattern instead of synchronous blocking tools.
The existing server.py stays for local stdio development.

Agent UX: tool docstrings are the primary documentation an agent sees via
tools/list — each one teaches its part of the workflow. Deep reference
(full spec schema, exception codes, worked example) is exposed as MCP
resources so agents can read them on demand without bloating every listing.
"""

from __future__ import annotations

import asyncio
import json
import os

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.db import DB
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.estimator import estimate_complexity as _estimate_complexity
from schemas.exceptions import ActionType, DesignException, ExcCode

mcp = FastMCP(
    "eda-mcp",
    instructions=(
        "PCB design generation service. Workflow: build a CircuitSpec JSON "
        "(read resource eda://guide/circuit-spec for the format), optionally "
        "estimate_complexity() to gauge difficulty, then submit_design() -> "
        "poll get_job() until finished -> if exceptions are returned, pick "
        "candidate fixes and apply_correction() to iterate. Fetch final "
        "KiCad artifacts with get_run(). Read eda://guide/workflow for the "
        "full loop and eda://guide/exceptions for the correction model."
    ),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
db = DB()


# ── Tools ──────────────────────────────────────────────────────────────


@mcp.tool()
async def submit_design(
    input_spec: dict,
    run_options: dict | None = None,
    policy: dict | None = None,
) -> dict:
    """Submit a PCB design job. Returns {job_id, status:"queued"} immediately.

    The engine (schematic generation + PCB placement + ERC) runs on a worker;
    poll get_job(job_id) every 5-15s until status is succeeded/failed/timeout.
    Simple boards finish in ~10-60s, dense boards can take minutes.

    input_spec is a CircuitSpec JSON object. Minimal example:
    {
      "board": {"name": "blinky"},
      "parts": [
        {"ref": "U1", "lib": "Analog_ADC", "part": "ADS1115IDGS",
         "footprint": "Package_SO:TSSOP-10_3x3mm_P0.5mm"},
        {"ref": "C1", "lib": "Device", "part": "C", "value": "100nF",
         "footprint": "Capacitor_SMD:C_0603_1608Metric"}
      ],
      "nets": [
        {"name": "VCC", "power": true, "pins": ["U1.VDD", "C1.1"]},
        {"name": "GND", "power": true, "pins": ["U1.GND", "C1.2"]}
      ]
    }

    Rules that matter:
    - Every part needs footprint as "Library:Name" (KiCad footprint id).
    - Library parts: set lib+part (KiCad symbol library/symbol). Custom
      parts: lib=null and define pins=[{num,name,func}] explicitly.
    - Net pins are "REF.PIN" strings; PIN is a pin number or pin name.
    - Power/ground nets: set power=true and use standard names (VCC, VDD,
      3V3, 5V, VBAT, GND, AGND...) — placement quality depends on it.
    - Decoupling caps: value "100nF" wired power-to-ground is auto-detected
      and placed 1.5mm from its IC. Other values/names are not detected.
    - Optional board fields: form_factor (feather, qt_py, metro...),
      outline_hint_mm [w,h], layers (2 or 4).
    Full schema: read resource eda://schema/circuit-spec.

    run_options (all optional): {"timeout_s": 300} engine wall-clock limit —
    raise to 600-1500 for dense boards; {"board_id": "..."} telemetry label.

    policy controls server-side auto-correction between iterations
    (default: none — every exception comes back to you):
    {"auto_apply": "none"|"advisory_only"|"safe",
     "max_internal_corrections": 0-8}
    With auto_apply="safe", the server retries placement failures and waives
    advisories by itself, and only surfaces decisions that need judgment
    (BOM substitutions, outline changes, unknown pinouts).

    Returns: {"job_id": "...", "status": "queued"}. Invalid specs fail
    validation here (immediately) with a Pydantic error message.
    """
    spec = CircuitSpec.model_validate(input_spec)
    job_id = await db.create_job(
        spec.model_dump(mode="json"),
        run_options,
        policy,
    )
    return {"job_id": job_id, "status": "queued"}


@mcp.tool()
async def get_job(job_id: str) -> dict:
    """Poll a submitted job. Returns status and, when finished, the full result.

    status values: "queued" (waiting for a worker), "running",
    "succeeded", "failed", "timeout". Poll every 5-15s while queued/running.

    When finished, the "result" field contains:
    - run_id: pass to get_run() for artifacts, or apply_correction() to fix
    - ok: true if all quality gates passed
    - exceptions: list of structured problems, each with resolution
      candidates (see eda://guide/exceptions). Empty list = clean run.
    - decision_required + decision_kind: set when the engine stopped and
      needs you to choose a fix via apply_correction()
    - summary, metrics, layout: quality data (placement score, HPWL, ERC)

    If the job crashed, "error" holds the traceback message and result may
    be null. A failed/timeout status with exceptions is normal — that is
    the correction loop, not a malfunction; inspect the candidates.
    """
    return await db.get_job(job_id)


@mcp.tool()
async def estimate_complexity(input_spec: dict) -> dict:
    """Pre-flight estimate for a CircuitSpec — fast (<2s), free, no side effects.

    Predicts: complexity_tier (simple|moderate|complex|ambitious), expected
    decision count, how many will auto-fix vs need review, runtime/timeout
    risk, and warnings (e.g. unknown footprints, board too dense).

    Use it to choose run_options before submit_design(): an "ambitious"
    tier suggests timeout_s of 900+, and its warnings often identify spec
    problems you can fix before spending a run. Also returns
    remapped_footprints — substitutions the engine will make automatically.
    """
    spec = CircuitSpec.model_validate(input_spec)
    result = await asyncio.to_thread(
        lambda: _estimate_complexity(spec).model_dump(mode="json")
    )
    return result


@mcp.tool()
async def apply_correction(run_id: str, corrections: list[dict]) -> dict:
    """Apply chosen exception candidates from a finished run; submits a new job.

    This is the iteration step of the design loop. When get_job() returns
    exceptions, each one carries candidates — machine-applicable fixes with
    ids c1, c2... and a human_summary. You select by id; you never describe
    the fix in prose:

      apply_correction(run_id="abc123", corrections=[
        {"exception_id": "e1", "candidate_id": "c2"},
        {"exception_id": "e2", "candidate_id": "c1"}
      ])

    The server loads that run's spec, applies the selected mutations
    (footprint swaps, outline changes, net stubs, advisory waivers...),
    and enqueues a fresh job. Returns {job_id, status:"queued",
    parent_run_id} — poll get_job(job_id) as usual.

    Selection guidance: candidates are ordered best-first and carry a
    confidence score; c1 is the deterministic policy's pick. Prefer it
    unless its human_summary conflicts with the user's actual intent
    (e.g. it swaps a part the user explicitly chose). You may fix several
    exceptions in one call. Errors if an id doesn't exist in that run.
    """
    run_data = await db.load_run(run_id)
    spec = CircuitSpec.model_validate(run_data["spec"])
    exceptions = [DesignException.model_validate(e) for e in run_data["exceptions"]]
    by_exc = {exc.id: exc for exc in exceptions}

    for correction in corrections:
        exc_id = correction.get("exception_id")
        cand_id = correction.get("candidate_id")
        if exc_id not in by_exc:
            raise ValueError(f"unknown exception_id {exc_id!r} for run {run_id}")
        exc = by_exc[exc_id]
        cand = next((c for c in exc.candidates if c.id == cand_id), None)
        if cand is None:
            raise ValueError(f"unknown candidate_id {cand_id!r} for exception {exc_id!r}")
        spec = apply_candidate(spec, exc, cand)

    prev_response = run_data.get("response") or {}
    parent_run_id = prev_response.get("run_id", run_id)

    job_id = await db.create_job(
        spec.model_dump(mode="json"),
        {"parent_run_id": parent_run_id},
        parent_job_id=run_data.get("job_id"),
    )
    return {"job_id": job_id, "status": "queued", "parent_run_id": parent_run_id}


@mcp.tool()
async def get_run(run_id: str) -> dict:
    """Fetch full run data: spec, exceptions, response, and KiCad artifacts.

    artifacts is {filename: file_content} for every generated .kicad_sch
    (schematic) and .kicad_pcb (board) — complete KiCad 9 files you can
    write to disk and open directly. The spec field is the exact CircuitSpec
    that produced this run (after any corrections), useful as the base for
    manual edits. Note: run data expires ~48h after completion.
    """
    return await db.load_run(run_id)


# ── Resources: deep reference an agent reads on demand ─────────────────


@mcp.resource(
    "eda://schema/circuit-spec",
    name="CircuitSpec JSON Schema",
    description="Authoritative JSON Schema for the input_spec argument of submit_design/estimate_complexity, generated from the live Pydantic model.",
    mime_type="application/json",
)
def circuit_spec_schema() -> str:
    return json.dumps(CircuitSpec.model_json_schema(), indent=2)


@mcp.resource(
    "eda://guide/circuit-spec",
    name="CircuitSpec authoring guide",
    description="How to write a CircuitSpec: parts, nets, footprints, power rails, decoupling conventions, form factors, and a complete worked example.",
    mime_type="text/markdown",
)
def circuit_spec_guide() -> str:
    return CIRCUIT_SPEC_GUIDE


@mcp.resource(
    "eda://guide/workflow",
    name="Design workflow guide",
    description="The full submit -> poll -> inspect -> correct -> fetch loop, with policy options and polling guidance.",
    mime_type="text/markdown",
)
def workflow_guide() -> str:
    return WORKFLOW_GUIDE


@mcp.resource(
    "eda://guide/exceptions",
    name="Exception and correction reference",
    description="Every exception code, severity, and correction action the engine can return, and how to choose candidates.",
    mime_type="text/markdown",
)
def exceptions_guide() -> str:
    return EXCEPTIONS_GUIDE


CIRCUIT_SPEC_GUIDE = """\
# Writing a CircuitSpec

A CircuitSpec is pure JSON data — never code. It fully describes a board:
parts, electrical connections, and board metadata.

## Top level

```json
{
  "board":  { ... },     // required: name + optional physical hints
  "parts":  [ ... ],     // required: every component
  "nets":   [ ... ],     // required: every electrical connection
  "waivers": []          // optional: advisory waiver keys (set via corrections)
}
```

## board

| field | type | notes |
|---|---|---|
| name | str, required | used for output file naming |
| form_factor | str | fixes the outline: `feather`, `qt_py`, `metro`, `metro_mini`, `trinket`, `itsybitsy`, `shield_uno` |
| outline_hint_mm | [w, h] | board size hint when no form_factor applies |
| layers | int | copper layers, 2 (default) or 4. Use 4 for dense boards |

## parts

Two kinds of part, distinguished by `lib`:

**Library part** — a KiCad symbol; `lib` + `part` required, `pins` forbidden:
```json
{"ref": "U1", "lib": "RF_Module", "part": "ESP32-S3-WROOM-1",
 "value": null, "footprint": "RF_Module:ESP32-S3-WROOM-1"}
```

**Custom part** — no KiCad symbol exists; `lib` null, `pins` required:
```json
{"ref": "J5", "lib": null, "footprint": "MyLib:POGO-4",
 "pins": [
   {"num": "1", "name": "VBUS", "func": "power_in"},
   {"num": "2", "name": "D-",   "func": "bidirectional"},
   {"num": "3", "name": "D+",   "func": "bidirectional"},
   {"num": "4", "name": "GND",  "func": "power_in"}
 ]}
```
Pin `func` values: power_in, power_out, input, output, bidirectional,
tristate, passive, unspecified, no_connect.

Other part fields:
- `value` — "10K", "100nF" etc. Matters for passives.
- `footprint` — required for every part, "Library:Name" format
  (e.g. "Resistor_SMD:R_0603_1608Metric"). The engine validates these and
  proposes substitutions when unknown.
- `group` — functional block name ("power", "mcu", "sensors"). Parts in a
  group are placed together and get their own schematic sheet. Use it for
  any board over ~10 parts.

## nets

```json
{"name": "SDA", "pins": ["U1.GPIO8", "U2.SDA", "R3.1"]}
{"name": "GND", "power": true, "pins": ["U1.GND", "C1.2", "C2.2"]}
```

- Pins are "REF.PIN"; PIN can be a pin **number** ("R3.1") or pin **name**
  ("U1.VDD"). Names are safer for ICs, numbers for passives.
- `power: true` for every supply/ground rail — sets drive strength and
  enables power-aware placement.
- **Use standard rail names**: VCC, VDD, VDDA, 3V3, 5V, VBUS, VIN, VBAT,
  VREF / GND, AGND, DGND, VSS. Non-standard names ("POWER_RAIL") defeat
  power-net detection and hurt placement quality.
- `stub: true` renders the net as named labels instead of routed wires in
  the schematic — good for high-fanout nets (resets, chip selects).

## Conventions the placer rewards

- **Decoupling caps**: a 2-pin part with value matching `100nF`/`0.1uF`,
  one pin on a power net and one on ground, is auto-detected and placed
  1.5mm from its IC. Name the value "100nF" — "104" or "bypass" defeat it.
- One decoupling cap per IC power pin is good practice; the engine flags
  ICs whose decaps ended up >5mm away.

## Complete worked example (I2C sensor board)

```json
{
  "board": {"name": "tmp117-breakout", "outline_hint_mm": [25, 20]},
  "parts": [
    {"ref": "U1", "lib": "Sensor_Temperature", "part": "TMP117xxDRV",
     "footprint": "Package_SON:WSON-6-1EP_2x2mm_P0.65mm_EP1x1.6mm", "group": "sensor"},
    {"ref": "C1", "lib": "Device", "part": "C", "value": "100nF",
     "footprint": "Capacitor_SMD:C_0603_1608Metric", "group": "sensor"},
    {"ref": "R1", "lib": "Device", "part": "R", "value": "10K",
     "footprint": "Resistor_SMD:R_0603_1608Metric", "group": "sensor"},
    {"ref": "R2", "lib": "Device", "part": "R", "value": "10K",
     "footprint": "Resistor_SMD:R_0603_1608Metric", "group": "sensor"},
    {"ref": "J1", "lib": "Connector_Generic", "part": "Conn_01x04",
     "footprint": "Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical",
     "group": "io"}
  ],
  "nets": [
    {"name": "3V3", "power": true,
     "pins": ["U1.V+", "C1.1", "R1.1", "R2.1", "J1.1"]},
    {"name": "GND", "power": true, "pins": ["U1.GND", "C1.2", "J1.4"]},
    {"name": "SDA", "pins": ["U1.SDA", "R1.2", "J1.2"]},
    {"name": "SCL", "pins": ["U1.SCL", "R2.2", "J1.3"]}
  ]
}
```
"""


WORKFLOW_GUIDE = """\
# Design workflow

```
estimate_complexity(spec)        # optional pre-flight, <2s
        |
submit_design(spec, run_options, policy)   -> job_id
        |
get_job(job_id)  ... poll every 5-15s ...  -> result when finished
        |
  result.exceptions empty?
        |-- yes -> get_run(result.run_id) -> write artifacts to disk. Done.
        |-- no  -> read each exception's candidates
                   apply_correction(run_id, [{exception_id, candidate_id}, ...])
                   -> new job_id -> poll again (loop)
```

## Polling

- queued -> running transition is usually <5s when workers are free.
- Simple boards (<20 parts): done in 10-60s. Dense boards: minutes.
  estimate_complexity's runtime_prediction tells you what to expect.
- status "failed" or "timeout" with exceptions attached is the NORMAL
  correction path, not an outage. Only a null result + error string means
  the job crashed.

## run_options

| key | default | when to change |
|---|---|---|
| timeout_s | 300 | raise to 600-1500 for boards estimate_complexity calls complex/ambitious |
| board_id | none | telemetry label for tracking related runs |

## policy (server-side auto-correction)

Default is `{"auto_apply": "none", "max_internal_corrections": 0}` — every
exception comes back to you. To let the server iterate by itself:

```json
{"auto_apply": "safe", "max_internal_corrections": 4}
```

- `advisory_only` — server waives advisory-severity findings, returns
  everything else.
- `safe` — server additionally retries placement/routing failures and
  regenerates after crashes. It still stops for anything needing judgment:
  BOM substitutions, mechanical/outline changes, unknown pinouts
  (the `stop_for` decision kinds).

## Iterating

- Each apply_correction() produces a child job linked to its parent; the
  spec it mutates is the parent run's spec, so corrections compound across
  iterations.
- Two or three correction rounds are typical for a first-pass spec with
  guessed footprints. If the same exception recurs with the same candidate,
  pick a different candidate instead of repeating.
- Run data expires ~48h after completion — fetch artifacts promptly.
"""


def _exceptions_guide() -> str:
    code_docs = {
        "SPEC_MALFORMED": "spec failed validation/translation",
        "SPEC_UNKNOWN_LIB": "KiCad symbol library not found",
        "SPEC_UNKNOWN_PART": "symbol not found in its library",
        "SPEC_UNKNOWN_PIN": "a net references a pin the part doesn't have",
        "SPEC_BAD_FOOTPRINT": "footprint id malformed or unknown",
        "FOOTPRINT_MISSING": "footprint not found in KiCad libraries at layout time",
        "SCH_PLACEMENT_FAILURE": "schematic placer could not place cleanly",
        "SCH_ROUTING_FAILURE": "schematic wiring failed",
        "ERC_PIN_NOT_CONNECTED": "ERC: pin left floating",
        "ERC_PIN_NOT_DRIVEN": "ERC: input pin has no driver",
        "ERC_REAL_ERROR": "ERC: electrical conflict (e.g. two outputs tied)",
        "LAYOUT_OVERLAP": "two footprints overlap on the board",
        "LAYOUT_OUTLINE_VIOLATION": "part placed outside the board outline",
        "LAYOUT_KEEPOUT": "part placed inside a keepout region",
        "LAYOUT_MISSING_REF": "a part was never placed",
        "HIGH_CONGESTION": "advisory: routing congestion hotspot",
        "LONG_POWER_NET": "advisory: power net wirelength is excessive",
        "ENGINE_TIMEOUT": "engine hit timeout_s — raise it or simplify",
        "ENGINE_CRASH": "engine crashed; usually retry (regenerate)",
        "BUDGET_EXHAUSTED": "correction budget used up without convergence",
    }
    action_docs = {
        "replace_lib": "swap symbol library (params: ref|'*', old, new)",
        "replace_part": "swap the symbol (params: ref, new)",
        "replace_pin": "rename a pin reference in nets (params: ref, old, new)",
        "replace_footprint": "swap a footprint everywhere it's used (params: old, new)",
        "remove_part": "delete the part (params: ref)",
        "remove_net_pin": "drop one endpoint from a net (params: net, pin)",
        "stub_net": "render net as labels instead of wires (params: net)",
        "set_form_factor": "adopt a standard board outline (params: name)",
        "set_outline": "set explicit board size (params: w_mm, h_mm)",
        "scale_outline": "grow the board area (params: area_factor)",
        "accept_advisory": "waive this advisory finding permanently",
        "regenerate": "rerun unchanged (placement has randomness)",
    }
    lines = [
        "# Exceptions and corrections",
        "",
        "Every engine problem is a DesignException:",
        "",
        "```json",
        '{"id": "e1", "code": "SPEC_BAD_FOOTPRINT", "severity": "error",',
        ' "message": "footprint Resistor_SMD:R_0603 not found",',
        ' "subject": {"ref": "R1", "footprint": "Resistor_SMD:R_0603"},',
        ' "candidates": [',
        '   {"id": "c1", "action": "replace_footprint",',
        '    "params": {"old": "Resistor_SMD:R_0603", "new": "Resistor_SMD:R_0603_1608Metric"},',
        '    "human_summary": "Use the full metric footprint name",',
        '    "confidence": 0.95, "cost_hint": "free"}',
        " ]}",
        "```",
        "",
        "You resolve it by id: `apply_correction(run_id, [{\"exception_id\": \"e1\", \"candidate_id\": \"c1\"}])`.",
        "Never re-describe a fix in natural language — pick a candidate.",
        "",
        "## Severities",
        "",
        "- **fatal** — no outputs were produced; must fix to proceed.",
        "- **error** — outputs exist but a quality gate failed.",
        "- **advisory** — informational; waivable with accept_advisory.",
        "",
        "## Choosing candidates",
        "",
        "- Candidates are ordered best-first; c1 is the deterministic pick.",
        "- confidence >= 0.8 means a policy of auto_apply='safe' would have",
        "  taken it without asking. Lower confidence = the engine wants your",
        "  judgment (often JLC part substitutions or pin guesses).",
        "- Check human_summary against the user's intent before accepting",
        "  BOM substitutions (replace_part / replace_footprint).",
        "- If the same exception+candidate pair recurs after applying it,",
        "  try a different candidate — repetition will not converge.",
        "",
        "## decision_kind (why the run stopped)",
        "",
        "- mechanical_constraint — outline/form-factor change proposed",
        "- bom_substitution — a part/footprint swap needs approval",
        "- unknown_pinout — pin mapping is uncertain",
        "- engine_failure — crash or timeout",
        "- quality_advisory — only advisories remain; waive or fix",
        "- correction_choice — general fix selection",
        "- no_candidate — at least one exception has no machine fix;",
        "  edit the spec manually and submit_design again",
        "",
        "## Exception codes",
        "",
    ]
    for code in ExcCode:
        lines.append(f"- `{code.value}` — {code_docs.get(code.value, '')}")
    lines += ["", "## Correction actions", ""]
    for action in ActionType:
        lines.append(f"- `{action.value}` — {action_docs.get(action.value, '')}")
    lines.append("")
    return "\n".join(lines)


EXCEPTIONS_GUIDE = _exceptions_guide()
