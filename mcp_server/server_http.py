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
import logging
import os

from pydantic import ValidationError

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from mcp_server.db import DB

logger = logging.getLogger("eda-mcp")
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.estimator import estimate_complexity as _estimate_complexity
from schemas.exceptions import ActionType, DesignException, ExcCode

mcp = FastMCP(
    "eda-mcp",
    instructions=(
        "PCB design generation service. Two input modes:\n"
        "1. PREFERRED: Write SKiDL Python code (from skidl import *, Part(), "
        "Net(), @subcircuit) and submit via submit_skidl_code(). You likely "
        "know the SKiDL API from training data — use it directly.\n"
        "2. Alternative: Write CircuitSpec JSON and submit via submit_design(). "
        "Read eda://guide/circuit-spec first.\n"
        "Both modes produce KiCad schematics, PCB layout, autorouting, and DRC. "
        "Poll get_job() until done, then get_run() for artifacts."
    ),
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)
db = DB()


def _validate_spec(input_spec: dict | str) -> CircuitSpec:
    """Validate input_spec and raise ValueError with clean, actionable messages."""
    if isinstance(input_spec, str):
        try:
            input_spec = json.loads(input_spec)
        except (json.JSONDecodeError, TypeError):
            raise ValueError(
                "input_spec must be a JSON object, not a string. "
                "Pass it as a dict/object, not a JSON-encoded string."
            )
    try:
        return CircuitSpec.model_validate(input_spec)
    except ValidationError as exc:
        lines = []
        for err in exc.errors():
            loc = " → ".join(str(l) for l in err["loc"])
            msg = err["msg"]
            for prefix in ("Value error, ",):
                if msg.startswith(prefix):
                    msg = msg[len(prefix):]
            ctx = err.get("ctx", {})
            fix = ""
            if "expected" in ctx:
                fix = f" Expected: {ctx['expected']}."
            if err["type"] == "missing":
                fix = " This field is required."
            lines.append(f"• {loc}: {msg}{fix}")
        raise ValueError(
            "Spec validation failed — fix these and resubmit:\n"
            + "\n".join(lines)
            + "\n\nHint: Read resource eda://guide/circuit-spec for the "
            "full format reference, valid field values, and a worked example."
        )


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
      "board": {"name": "blinky", "outline_hint_mm": [30, 25]},
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
    - board: set outline_hint_mm: [width, height] in mm for board size.
      Do NOT set form_factor unless targeting a specific Adafruit dev board
      shape (feather, qt_py, metro etc). Most boards just need outline_hint_mm.
    - lib must be a KiCad symbol library (Device, Sensor_Temperature,
      Connector_Generic, Transistor_FET...), NOT a manufacturer name.
    - Every part needs footprint as "Library:Name" (KiCad footprint id).
    - Connectors: lib="Connector_Generic", part="Conn_01x04" (not PinHeader).
    - Net pins are "REF.PIN" strings; PIN is a pin number or pin name.
    - Power/ground nets: set power=true and use standard names (VCC, VDD,
      3V3, 5V, VBAT, GND, AGND...) — placement quality depends on it.
    - Decoupling caps: value "100nF" wired power-to-ground is auto-detected
      and placed 1.5mm from its IC. Other values/names are not detected.
    Read resource eda://guide/circuit-spec for full docs and worked example.

    run_options (all optional): {"timeout_s": 300} engine wall-clock limit —
    raise to 600-1500 for dense boards; {"board_id": "..."} telemetry label.

    policy controls server-side auto-correction between iterations
    (default: none — every exception comes back to you):
    {"auto_apply": "none"|"advisory_only"|"safe",
     "max_internal_corrections": 0-8}
    With auto_apply="safe", the server retries placement failures and waives
    advisories by itself, and only surfaces decisions that need judgment
    (BOM substitutions, outline changes, unknown pinouts).

    Returns: {"job_id": "...", "status": "queued"}. Invalid specs return
    a clear validation error immediately — read it, fix the fields, resubmit.
    """
    spec = _validate_spec(input_spec)
    job_id = await db.create_job(
        spec.model_dump(mode="json"),
        run_options,
        policy,
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "hint": (
            "Job queued. Poll with get_job(job_id) every 10s until "
            "status is 'succeeded' or 'failed'."
        ),
    }


@mcp.tool()
async def submit_skidl_code(
    code: str,
    board_name: str = "board",
    outline_mm: list[float] | None = None,
    run_options: dict | None = None,
) -> dict:
    """Submit SKiDL Python code to generate a PCB design.

    Write standard SKiDL Python: Part(), Net(), pin connections. The server
    handles the full pipeline (schematic, PCB layout, routing, DRC) — do NOT
    call generate_schematic() or generate_netlist() in your code.

    Example code:
        from skidl import *
        set_default_tool(KICAD9)

        vcc = Net("VCC"); vcc.drive = POWER
        gnd = Net("GND"); gnd.drive = POWER

        u1 = Part("Sensor_Temperature", "TMP117xxDRV",
                   footprint="Package_SON:WSON-6-1EP_2x2mm_P0.65mm_EP1x1.6mm")
        c1 = Part("Device", "C", value="100nF",
                   footprint="Capacitor_SMD:C_0603_1608Metric")
        r1 = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
        r2 = Part("Device", "R", value="10K",
                   footprint="Resistor_SMD:R_0603_1608Metric")
        j1 = Part("Connector_Generic", "Conn_01x04",
                   footprint="Connector_PinHeader_2.54mm:PinHeader_1x04_P2.54mm_Vertical")

        vcc += u1["V+"], c1[1], r1[1], r2[1], j1[1]
        gnd += u1["GND"], c1[2], j1[4]
        sda = Net("SDA"); sda += u1["SDA"], r1[2], j1[2]
        scl = Net("SCL"); scl += u1["SCL"], r2[2], j1[3]

    Key rules:
    - lib must be a KiCad symbol library (Device, Sensor_Temperature,
      Connector_Generic, Analog_ADC...), NOT a manufacturer name.
    - Every Part needs footprint="Library:Name" (KiCad footprint id).
    - Power nets: set net.drive = POWER on every supply/ground rail.
    - Decoupling caps: value="100nF" between power and ground per IC.
    - Connectors: Part("Connector_Generic", "Conn_01x06", ...).
    - Use standard power names: VCC, VDD, 3V3, 5V, VBUS, GND, AGND.
    - Use @subcircuit to group functional blocks for cleaner schematics.
    Read resource eda://guide/skidl for a quick reference.

    code: Python source defining a SKiDL circuit.
    board_name: name for output files (default "board").
    outline_mm: [width, height] in mm. Omit to auto-size from parts.
    run_options: {"timeout_s": 300} — raise for complex boards.

    Returns: {"job_id": "...", "status": "queued"}. Poll get_job(job_id).
    """
    if not code or not code.strip():
        raise ValueError("code must be non-empty SKiDL Python source.")

    job_spec = {
        "_mode": "skidl_python",
        "code": code,
        "board_name": board_name or "board",
        "outline_mm": outline_mm,
    }
    opts = dict(run_options or {})
    job_id = await db.create_job(job_spec, opts)
    return {
        "job_id": job_id,
        "status": "queued",
        "hint": (
            "Job queued. Poll with get_job(job_id) every 10s until "
            "status is 'succeeded' or 'failed'. If your code has errors "
            "(wrong lib/part/pin names), you'll get a clear message — "
            "fix and resubmit."
        ),
    }


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
    job = await db.get_job(job_id)

    # Trim response size — full spec and verbose layout are available via get_run.
    result = job.get("result")
    if isinstance(result, dict):
        result.pop("spec", None)
        result.pop("_artifact_paths", None)
        result.pop("stderr", None)
        # Keep layout.score summary, drop verbose report/candidates
        layout = result.get("layout")
        if isinstance(layout, dict):
            score = layout.get("score")
            result["layout"] = {"ok": layout.get("ok"), "score": score}
        elif isinstance(layout, str) and len(layout) > 2000:
            result["layout"] = layout[:2000] + "\n... (use get_run for full data)"

    job["hint"] = _get_job_hint(job)
    return job


def _get_job_hint(job: dict) -> str:
    """Context-sensitive hint based on job state."""
    status = job.get("status", "")
    if status in ("queued", "running"):
        return "Still processing. Poll again in 10s."

    result = job.get("result")
    if not isinstance(result, dict):
        if job.get("error"):
            return (
                "Job crashed. Check your spec for issues — read resource "
                "eda://guide/circuit-spec for the format reference, then "
                "fix and resubmit with submit_design()."
            )
        return "Job finished with no result. Resubmit."

    exceptions = result.get("exceptions", [])
    decision_required = result.get("decision_required", False)
    run_id = result.get("run_id", "")

    if not exceptions:
        return (
            f"Clean run — no issues. Fetch your KiCad schematic and PCB "
            f"files with get_run('{run_id}')."
        )

    exc_codes = [e.get("code", "") for e in exceptions if isinstance(e, dict)]
    has_pin_errors = any("PIN" in c for c in exc_codes)
    has_footprint_errors = any("FOOTPRINT" in c or "BAD_FOOTPRINT" in c for c in exc_codes)
    has_lib_errors = any("LIB" in c or "PART" in c for c in exc_codes)

    if decision_required:
        parts = []
        if has_pin_errors:
            parts.append(
                "Pin errors found — check the 'available_pins' in each "
                "exception to see valid pin names"
            )
        if has_footprint_errors:
            parts.append(
                "Footprint issues — candidates offer substitutions"
            )
        if has_lib_errors:
            parts.append(
                "Library/part not found — check lib is a KiCad library name "
                "(e.g. 'Sensor_Temperature'), not a manufacturer"
            )
        parts.append(
            "Pick candidate fixes and call apply_correction(run_id, corrections). "
            "Read resource eda://guide/exceptions for the full correction model"
        )
        return ". ".join(parts) + "."

    return (
        f"Run finished with {len(exceptions)} exception(s). "
        f"Inspect the candidates and apply_correction() to fix, or "
        f"get_run('{run_id}') if the results are acceptable. "
        f"Read resource eda://guide/exceptions for correction guidance."
    )


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
    spec = _validate_spec(input_spec)
    result = await asyncio.to_thread(
        lambda: _estimate_complexity(spec).model_dump(mode="json")
    )
    if result.get("spec_issues"):
        logger.info(
            "estimate_complexity spec_issues board=%s: %s",
            spec.board.name,
            json.dumps(result["spec_issues"]),
        )
    try:
        await db.append_estimate(spec.board.name, result)
    except Exception:
        pass

    result["hint"] = _estimate_hint(result)
    return result


def _estimate_hint(result: dict) -> str:
    """Context-sensitive hint for estimate_complexity response."""
    tier = result.get("complexity_tier", "simple")
    issues = result.get("spec_issues", [])
    warnings = result.get("warnings", [])

    parts = []

    if issues:
        parts.append(
            f"Found {len(issues)} spec issue(s) — fix before submitting. "
            f"Read resource eda://guide/circuit-spec for format reference"
        )
    elif warnings:
        parts.append(
            f"{len(warnings)} warning(s) — review but not blocking"
        )

    if tier in ("complex", "ambitious"):
        parts.append(
            f"Complexity: {tier}. Use timeout_s=900 or higher in run_options "
            f"when you submit_design()"
        )

    if not issues:
        parts.append(
            "Spec looks valid. Submit with submit_design() — the engine runs "
            "design review, enrichment, routing, and DRC automatically"
        )

    return ". ".join(parts) + "."


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
            raise ValueError(
                f"exception_id {exc_id!r} not found in run {run_id}. "
                f"Available exception IDs: {list(by_exc.keys())}"
            )
        exc = by_exc[exc_id]
        cand = next((c for c in exc.candidates if c.id == cand_id), None)
        if cand is None:
            raise ValueError(
                f"candidate_id {cand_id!r} not found for exception {exc_id!r}. "
                f"Available candidates: {[c.id for c in exc.candidates]}"
            )
        logger.info(
            "apply_correction run=%s exc=%s cand=%s action=%s params=%s",
            run_id, exc_id, cand_id, cand.action, json.dumps(cand.params),
        )
        spec = apply_candidate(spec, exc, cand)

    prev_response = run_data.get("response") or {}
    parent_run_id = prev_response.get("run_id", run_id)

    job_id = await db.create_job(
        spec.model_dump(mode="json"),
        {"parent_run_id": parent_run_id},
        parent_job_id=run_data.get("job_id"),
    )
    return {
        "job_id": job_id,
        "status": "queued",
        "parent_run_id": parent_run_id,
        "hint": (
            "Corrections applied, new job queued. "
            "Poll get_job(job_id) every 10s for results."
        ),
    }


@mcp.tool()
async def get_run(run_id: str) -> dict:
    """Fetch full run data: spec, exceptions, response, and KiCad artifacts.

    artifacts is {filename: file_content} for every generated .kicad_sch
    (schematic) and .kicad_pcb (board) — complete KiCad 9 files you can
    write to disk and open directly. The spec field is the exact CircuitSpec
    that produced this run (after any corrections), useful as the base for
    manual edits. Note: run data expires ~48h after completion.
    """
    run_data = await db.load_run(run_id)
    artifacts = run_data.get("artifacts") or {}
    file_types = [k.rsplit(".", 1)[-1] for k in artifacts if "." in k]
    if artifacts:
        run_data["hint"] = (
            f"Run data retrieved with {len(artifacts)} artifact(s) "
            f"({', '.join(f'.{t}' for t in sorted(set(file_types)))}). "
            f"Write these files to disk — they're complete KiCad 9 files "
            f"you can open directly."
        )
    else:
        run_data["hint"] = "Run data retrieved but no artifacts were generated."
    return run_data


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
    "eda://guide/skidl",
    name="SKiDL Python quick reference",
    description="How to write SKiDL Python code for submit_skidl_code(): Part(), Net(), @subcircuit, power conventions, footprint format.",
    mime_type="text/markdown",
)
def skidl_guide() -> str:
    return SKIDL_GUIDE


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
| form_factor | str | ONLY these exact values: `feather`, `qt_py`, `metro`, `metro_mini`, `trinket`, `itsybitsy`, `shield_uno`. Omit for custom boards |
| outline_hint_mm | [w, h] | board size in mm when form_factor is omitted. **Use this for most boards** — form_factor is only for Adafruit-compatible dev boards |
| layers | int | copper layers, 2 (default) or 4. Use 4 for dense boards |

**Choosing board size:** Most boards should omit `form_factor` and set
`outline_hint_mm` instead. A "compact" sensor breakout might be `[25, 20]`,
a medium MCU board `[50, 40]`. Do NOT use descriptive words like "compact"
or "small" — these are not valid form_factor values.

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

**`lib` must be a KiCad symbol library name, NOT a manufacturer.** Common ones:
- Passives: `Device` (R, C, L, LED, D, D_Zener)
- Sensors: `Sensor_Temperature`, `Sensor_Humidity`, `Sensor_Pressure`,
  `Sensor_Motion`, `Sensor_Optical`
- ICs: `Analog_ADC`, `Analog_DAC`, `Interface_I2C`, `Interface_SPI`
- MCUs: `MCU_Microchip`, `MCU_Nordic`, `MCU_RaspberryPi`, `MCU_ST`
- Connectors: `Connector_Generic` (Conn_01x04, Conn_01x06, Conn_02x05...),
  `Connector_USB`, `Connector_JST` (for Qwiic/STEMMA QT)
- FETs/transistors: `Transistor_FET`, `Transistor_BJT`
- Power: `Regulator_Linear`, `Regulator_Switching`
- RF: `RF_Module`

Wrong: `"lib": "Bosch"`, `"lib": "MOSFET"`, `"lib": "TI"`.
Right: `"lib": "Sensor_Pressure"`, `"lib": "Transistor_FET"`, `"lib": "Analog_ADC"`.

**Connectors (common mistake):** For pin headers use `lib: "Connector_Generic"`,
`part: "Conn_01x06"` (not "PinHeader_1x06"). For screw terminals use
`part: "Screw_Terminal_01x02"` etc.

Other part fields:
- `value` — "10K", "100nF" etc. Matters for passives.
- `footprint` — required for every part, "Library:Name" format
  (e.g. "Resistor_SMD:R_0603_1608Metric"). The engine validates these and
  proposes substitutions when unknown. Don't guess — if unsure, use a
  reasonable default and the engine will suggest corrections.
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
- **Pin headers as edge connectors**: parts with "Connector" in the
  reference or name are auto-placed on the board edge. The placer adds
  0.5mm inset from the edge. If your header doesn't need edge placement,
  set a `group` on it to keep it with its functional block instead.

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


SKIDL_GUIDE = """\
# SKiDL Python quick reference

Write standard SKiDL Python code. The server handles schematic generation,
PCB layout, autorouting, and DRC — do NOT call generate_schematic() etc.

## Template

```python
from skidl import *
set_default_tool(KICAD9)

# Power rails — always set drive = POWER
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Parts — every part needs footprint="Library:Name"
u1 = Part("Analog_ADC", "ADS1115IDGS",
          footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm")
c1 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0603_1608Metric")

# Connect by pin name (ICs) or number (passives)
vcc += u1["VDD"], c1[1]
gnd += u1["GND"], c1[2]
```

## Key rules

- `lib` is a KiCad symbol library name, NOT a manufacturer:
  Device, Sensor_Temperature, Connector_Generic, Analog_ADC,
  Regulator_Linear, Transistor_FET, MCU_Microchip, RF_Module, etc.
- Connectors: `Part("Connector_Generic", "Conn_01x06", ...)`
  not "PinHeader_1x06"
- Decoupling caps: value="100nF" wired power-to-ground = auto-placed
  near parent IC. One per IC power pin. "104" or "bypass" won't work.
- Standard power names: VCC, VDD, 3V3, 5V, VBUS, VBAT, GND, AGND
- Use @subcircuit for functional blocks:

```python
@subcircuit
def sensor_block(vcc, gnd, sda, scl):
    u = Part("Sensor_Temperature", "TMP117xxDRV",
             footprint="Package_SON:WSON-6-1EP_2x2mm_P0.65mm_EP1x1.6mm")
    c = Part("Device", "C", value="100nF",
             footprint="Capacitor_SMD:C_0603_1608Metric")
    vcc += u["V+"], c[1]
    gnd += u["GND"], c[2]
    sda += u["SDA"]
    scl += u["SCL"]
```

## Common passives

| Component | Example |
|-----------|---------|
| Resistor | `Part("Device", "R", value="10K", footprint="Resistor_SMD:R_0603_1608Metric")` |
| Capacitor | `Part("Device", "C", value="100nF", footprint="Capacitor_SMD:C_0603_1608Metric")` |
| Bulk cap | `Part("Device", "C_Polarized", value="10uF", footprint="Capacitor_SMD:C_0805_2012Metric")` |
| LED | `Part("Device", "LED", footprint="LED_SMD:LED_0603_1608Metric")` |
| Diode | `Part("Device", "D_Schottky", footprint="Diode_SMD:D_SOD-123")` |

## Tips

- Pin names on ICs may differ from net names — BME280 uses SDI/SCK,
  not SDA/SCL. Check the KiCad library if unsure.
- For address selection: use pull-up/pull-down resistors on ADDR pins.
- Include bulk caps (10uF) on power rails and 100nF decoupling per IC.
- Count connector pins carefully: VCC + GND + SDA + SCL + A0 + A1 = 6 pins.
"""


WORKFLOW_GUIDE = """\
# Design workflow

## Step 0 — Choose your input mode

**Option A (preferred): SKiDL Python** — write `from skidl import *`,
define Parts and Nets, submit via `submit_skidl_code()`. You likely know
the API already. Read `eda://guide/skidl` for conventions.

**Option B: CircuitSpec JSON** — write a JSON spec and submit via
`submit_design()`. Read `eda://guide/circuit-spec` for the format.

For either mode, think through the full BOM before coding:
- Every IC with its function (sensor, regulator, MCU, etc.)
- Every passive: 100nF decoupling per IC power pin, 10uF bulk caps
  on each power rail, pull-ups, shunt resistors, LED limiters
- Every connector: count pins by listing signals (VCC, GND, SDA,
  SCL, A0, A1 = 6 pins, not 4)
- Address selection hardware (solder jumpers, pull-downs)

## Step 1 — Submit and iterate

Every design succeeds if you persist. The engine gives you exact errors
and suggested fixes at every step — apply them, resubmit, and the board
converges to a manufacturable PCB. First-pass specs typically need 3-8
correction rounds for library names, pin names, and footprints. This is
normal and expected — each round gets closer.

```
submit_design(spec, run_options, policy)   -> job_id
        |
get_job(job_id)  ... poll every 5-15s ...  -> result when finished
        |
  result.exceptions empty?
        |-- yes -> get_run(result.run_id) -> artifacts. Done!
        |-- no  -> apply_correction(run_id, [{exception_id, candidate_id}, ...])
                   -> new job_id -> poll again (keep going until succeeded)
```

The engine pipeline runs: enrich (auto-adds decoupling caps, pull-ups) →
translate → design review → schematic + ERC → placement → routing
(Freerouting) → DRC (kicad-cli). Problems at any stage come back as
structured exceptions with candidates — you never need to inspect
intermediate artifacts or call external tools. The final .kicad_pcb
is a routed board when routing succeeds.

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

## Iterating — KEEP GOING UNTIL SUCCEEDED

- Each apply_correction() produces a child job linked to its parent; the
  spec it mutates is the parent run's spec, so corrections compound across
  iterations.
- **Do not stop after a fixed number of rounds.** Keep applying corrections
  and resubmitting until get_job() returns status "succeeded". Library
  mismatches, pin name errors, and footprint fixes are normal — each
  correction gets you closer. The engine tells you exactly what's wrong
  and suggests fixes; apply them all and resubmit.
- If the same exception recurs with the same candidate, pick a different
  candidate instead of repeating. If no candidates work, try a different
  lib/part/footprint from the error's `suggested_fix` or `available_pins`.
- The server auto-enriches your spec with decoupling caps, I2C pull-ups,
  and other standard passives — you don't need to include those.
- Run data expires ~48h after completion — fetch artifacts promptly.

## Important tips

- **After corrections succeed**, call `get_run(run_id)` to fetch your
  .kicad_pcb and .kicad_sch artifacts. The run_id comes from the last
  get_job() result. You can also pass a job_id to get_run().
- **Pin names on ICs often differ from net names.** BME280 uses SDI/SCK
  (not SDA/SCL). When you get SPEC_UNKNOWN_PIN errors, check the
  `available_pins` list in the exception — it shows the real pin names.
- **auto_apply: "safe"** handles placement retries and advisory waivers
  but NOT library or footprint swaps (those are always manual decisions,
  even at high confidence).
- **Exception IDs (e1, e2...) are per-run.** After apply_correction()
  creates a new job, poll the NEW job and use IDs from its exceptions,
  not from the previous run.
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
        "DESIGN_MISSING_BULK_CAP": "advisory: power rail has no bulk capacitor (10uF+)",
        "DESIGN_NO_CONNECTOR": "error: board has no connectors",
        "DESIGN_NO_POWER_RAIL": "error: no power or ground rail defined",
        "DESIGN_POWER_FLAG": "advisory: net looks like power but power=true not set",
        "DESIGN_MISSING_FEATURE": "advisory: marketing text mentions a feature not in spec",
        "ROUTE_UNCONNECTED": "error: nets that could not be routed",
        "ROUTE_CONGESTION": "advisory: routing succeeded but congestion is high",
        "ROUTE_TIMEOUT": "error: Freerouting exceeded time limit",
        "ROUTE_UNAVAILABLE": "error: routing tools not available — board is unrouted",
        "DRC_CLEARANCE": "error: trace/pad clearance violation",
        "DRC_UNCONNECTED": "error: net endpoint not connected after routing",
        "DRC_SHORT": "error: unintended connection between nets",
        "DRC_COURTYARD": "advisory: component courtyard overlap",
        "DRC_TOOL_FAILURE": "advisory: DRC tool failed to run",
        "ENGINE_TIMEOUT": "engine hit timeout_s — raise it or simplify",
        "ENGINE_CRASH": "engine crashed; usually retry (regenerate)",
        "CODE_EXEC_ERROR": "SKiDL Python code raised an error during execution",
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
        "add_parts": "inject parts and net connections (params: parts[], net_connections[])",
        "set_layers": "change copper layer count (params: layers)",
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
