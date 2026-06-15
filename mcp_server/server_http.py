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
import re
from copy import deepcopy

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
        "PCB design service. Use search_kicad() to find correct library names, "
        "part names, and footprints. Write SKiDL Python code and submit via "
        "submit_skidl_code(). The server handles schematic generation, PCB "
        "layout, autorouting, and DRC. Poll get_job() until done, then "
        "get_run() for artifacts. Show preview_2d_top.png to the human when "
        "available; if they give visual/design feedback, record it with "
        "submit_human_feedback() before editing and resubmitting. If a run "
        "returns exceptions, edit the SKiDL code using the structured "
        "exception details and resubmit. "
        "Do all design submissions through submit_skidl_code(); read "
        "eda://guide/skidl for conventions."
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
            + "\n\nHint: CircuitSpec JSON is legacy/internal. New MCP clients "
            "should write SKiDL Python and call submit_skidl_code()."
        )


# ── Tools ──────────────────────────────────────────────────────────────


async def submit_design(
    input_spec: dict,
    run_options: dict | None = None,
    policy: dict | None = None,
) -> dict:
    """Legacy/internal: submit a CircuitSpec JSON design job.

    New agent-facing clients should use submit_skidl_code() instead. The
    CircuitSpec JSON surface remains for corpus runners, compatibility tests,
    and internal debugging while the product converges on SKiDL Python at the
    MCP boundary.

    Returns {job_id, status:"queued"} immediately.

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
    CircuitSpec is legacy/internal. New MCP clients should use
    submit_skidl_code() and eda://guide/skidl.

    run_options (all optional): {"timeout_s": 300} engine wall-clock limit;
    {"route_timeout_s": 120} Freerouting-only limit — raise to 300-900
    after ROUTE_TIMEOUT; {"board_id": "..."} telemetry label.

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
        "deprecated": True,
        "hint": (
            "Legacy CircuitSpec job queued. Prefer submit_skidl_code() for "
            "new MCP clients. Poll with get_job(job_id) every 10s until "
            "status is 'succeeded' or 'failed'."
        ),
    }


@mcp.tool()
async def submit_skidl_code(
    code: str,
    board_name: str = "board",
    outline_mm: list[float] | None = None,
    corner_radius_mm: float | None = None,
    kicad_version: str = "9",
    design_intent: str = "",
    run_options: dict | None = None,
) -> dict:
    """Submit SKiDL Python code to generate a PCB design.

    Write standard SKiDL Python: Part(), Net(), pin connections. The server
    handles the full pipeline (schematic, PCB layout, routing, DRC) — do NOT
    call generate_schematic() or generate_netlist() in your code. There is no
    global connect() helper; connect with `net += pin1, pin2` or `pin += net`.

    A run is only clean when routing, DRC, and manufacturing export all pass.
    Clean runs include Gerbers, drill files, BOM CSV, and CPL (pick-and-place)
    CSV — everything needed for JLCPCB ordering. These are bundled in the
    output zip alongside the KiCad source files.

    To include LCSC part numbers in the BOM (for JLCPCB assembly), set
    part.lcsc on each Part: `u1.lcsc = "C160404"`. Use search_kicad() to
    find LCSC variants with pricing and stock.

    Example code:
        from skidl import *

        vcc = Net("VCC"); vcc.drive = POWER
        gnd = Net("GND"); gnd.drive = POWER

        u1 = Part("Sensor_Temperature", "TMP117xxDRV",
                   footprint="Package_SON:WSON-6-1EP_2x2mm_P0.65mm_EP1x1.6mm")
        u1.lcsc = "C160404"
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
    - Connections: use `net += pin1, pin2` or `pin += net`. Do not write
      `connect(pin1, pin2)`, and do not put a function call on the left side
      of `+=`.
    - Use standard power names: VCC, VDD, 3V3, 5V, VBUS, GND, AGND.
    - Use @subcircuit to group functional blocks for cleaner schematics.
    Read resource eda://guide/skidl for a quick reference.

    code: Python source defining a SKiDL circuit.
    board_name: name for output files (default "board").
    outline_mm: [width, height] in mm. Omit to auto-size from parts.
    corner_radius_mm: optional rectangular outline corner radius in mm. Omit
      to use the engine's product-board default. Set 0 for square corners.
      For Eurorack/panel/mechanical boards, ask or infer deliberately; obvious
      Eurorack context defaults square unless this is explicit.
    kicad_version: target KiCad version for output format ("9" or "10").
    design_intent: optional original user/design request. Include it so the
      server can warn when the code appears to omit requested features such as
      USB-C, STEMMA/Qwiic, I2C/SPI, LiPo charging, regulators, or shunts.
    run_options: {"timeout_s": 300, "route_timeout_s": 120,
      "assembly_policy": "single_sided"} — raise timeout_s for complex
      boards, route_timeout_s after ROUTE_TIMEOUT. Choose assembly_policy
      up front: "single_sided" avoids automatic rear-side SMD placement and
      is the cost-preserving default; "double_sided" allows front-panel
      controls with rear electronics when the human accepts the extra
      fabrication/assembly cost. For Eurorack, ask/decide whether this is a
      single-board dual-sided module or a two-board panel/main-board stack.
      If double_sided is chosen, set `part.assembly_side = "front"` or
      `"back"` on parts where the side matters; do not use the back merely
      as a squeezing/autorouting escape hatch.
      For layout overlap/outline/congestion feedback, prefer improving
      grouping, connector choices, and outline_mm before resubmitting.

    Returns: {"job_id": "...", "status": "queued"}. Poll get_job(job_id).
    """
    if not code or not code.strip():
        raise ValueError("code must be non-empty SKiDL Python source.")

    opts = dict(run_options or {})
    job_spec = {
        "_mode": "skidl_python",
        "code": code,
        "board_name": board_name or "board",
        "outline_mm": outline_mm,
        "corner_radius_mm": corner_radius_mm,
        "assembly_policy": opts.get("assembly_policy"),
        "design_intent": design_intent or "",
        "kicad_version": kicad_version or "9",
    }
    job_id = await db.create_job(job_spec, opts)
    return {
        "job_id": job_id,
        "status": "queued",
        "hint": (
            "Job queued. Poll with get_job(job_id) every 10s until "
            "status is 'succeeded' or 'failed'. If your code has errors "
            "(wrong lib/part/pin names), you'll get a clear message — "
            "fix and resubmit. When the board passes routing, DRC, and "
            "manufacturing export, Gerbers, BOM, and CPL are included in "
            "the output zip."
        ),
    }


@mcp.tool()
async def get_job(job_id: str) -> dict:
    """Poll a submitted job. Returns status and a compact finished result.

    status values: "queued" (waiting for a worker), "running",
    "succeeded", "failed", "timeout". Poll every 5-15s while queued/running.

    When finished, the "result" field contains:
    - run_id: pass to get_run() for artifacts
    - ok: true if all quality gates passed
    - exceptions: list of structured problems, each with resolution
      candidates (see eda://guide/exceptions). Empty list = clean run.
    - decision_required + decision_kind: set when the engine stopped and
      needs you to edit the SKiDL code and resubmit.
    - summary, metrics, layout: quality data (placement score, HPWL, ERC)

    If the job crashed, "error" holds the traceback message and result may
    be null. A failed/timeout status with exceptions is normal — that is
    the correction loop, not a malfunction; inspect the candidates.
    """
    job = deepcopy(await db.get_job(job_id))

    # Trim response size — full spec and verbose layout are available via get_run.
    result = job.get("result")
    if isinstance(result, dict):
        _compact_job_result_for_agent(result)

    job["hint"] = _get_job_hint(job)
    job.pop("spec", None)
    return job


def _trim_agent_value(value, *, max_str: int = 800, max_items: int = 24, depth: int = 0):
    """Trim nested values so get_job remains a control response."""

    if isinstance(value, str):
        if len(value) <= max_str:
            return value
        return value[:max_str] + f"\n... ({len(value) - max_str} chars omitted)"
    if depth >= 4:
        return str(value)[:max_str]
    if isinstance(value, list):
        items = [
            _trim_agent_value(item, max_str=max_str, max_items=max_items, depth=depth + 1)
            for item in value[:max_items]
        ]
        if len(value) > max_items:
            items.append(f"... ({len(value) - max_items} more items)")
        return items
    if isinstance(value, dict):
        out = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= max_items:
                out["_truncated_keys"] = len(value) - max_items
                break
            out[key] = _trim_agent_value(
                item,
                max_str=max_str,
                max_items=max_items,
                depth=depth + 1,
            )
        return out
    return value


def _compact_candidate_for_agent(candidate: dict) -> dict:
    return {
        key: _trim_agent_value(candidate.get(key), max_str=500, max_items=12)
        for key in ("id", "action", "params", "human_summary", "confidence")
        if key in candidate
    }


def _compact_exception_for_agent(exc: dict) -> dict:
    compact = {
        key: _trim_agent_value(exc.get(key), max_str=500, max_items=12)
        for key in ("id", "code", "severity", "message", "subject", "retry_hint")
        if key in exc
    }
    candidates = exc.get("candidates")
    if isinstance(candidates, list):
        compact["candidates"] = [
            _compact_candidate_for_agent(c)
            for c in candidates[:4]
            if isinstance(c, dict)
        ]
        if len(candidates) > 4:
            compact["candidates_truncated"] = len(candidates) - 4
    return compact


def _compact_job_result_for_agent(result: dict) -> None:
    """Mutate a finished job result into a compact agent-control packet."""

    result.pop("spec", None)
    result.pop("_artifact_paths", None)
    result.pop("stderr", None)
    if "summary" in result:
        result["summary"] = _trim_agent_value(result["summary"], max_str=1200)

    layout = result.get("layout")
    if isinstance(layout, dict):
        score = layout.get("score")
        result["layout"] = {"ok": layout.get("ok"), "score": score}
    elif isinstance(layout, str) and len(layout) > 2000:
        result["layout"] = layout[:2000] + "\n... (use get_run for full data)"

    exceptions = result.get("exceptions")
    if isinstance(exceptions, list):
        compact_exceptions = [
            _compact_exception_for_agent(exc)
            for exc in exceptions[:5]
            if isinstance(exc, dict)
        ]
        result["exception_codes"] = [
            exc.get("code")
            for exc in exceptions
            if isinstance(exc, dict) and exc.get("code")
        ]
        result["top_exception"] = compact_exceptions[0] if compact_exceptions else None
        result["exceptions"] = compact_exceptions
        if len(exceptions) > len(compact_exceptions):
            result["exceptions_truncated"] = len(exceptions) - len(compact_exceptions)


def _get_job_hint(job: dict) -> str:
    """Context-sensitive hint based on job state."""
    status = job.get("status", "")
    spec = job.get("spec") or {}
    is_skidl_python = isinstance(spec, dict) and spec.get("_mode") == "skidl_python"
    if status in ("queued", "running"):
        return "Still processing. Poll again in 10s."

    result = job.get("result")
    if not isinstance(result, dict):
        if job.get("error"):
            if is_skidl_python:
                return (
                    "Job crashed. Check your SKiDL code, read resource "
                    "eda://guide/skidl for conventions, then fix and resubmit "
                    "with submit_skidl_code()."
                )
            return (
                "Legacy CircuitSpec job crashed. Check your spec for issues, "
                "then fix and resubmit. New clients should use submit_skidl_code()."
            )
        return "Job finished with no result. Resubmit."

    exceptions = result.get("exceptions", [])
    decision_required = result.get("decision_required", False)
    run_id = result.get("run_id", "")

    if not exceptions:
        mfg = (result.get("outputs") or {}).get("manufacturing")
        mfg_note = ""
        if mfg:
            mfg_note = (
                " Manufacturing files (Gerbers, BOM, CPL) are included "
                "in the zip — ready for JLCPCB upload."
            )
        return (
            f"Clean run — no issues. Fetch your KiCad files "
            f"with get_run('{run_id}').{mfg_note}"
        )

    exc_codes = [e.get("code", "") for e in exceptions if isinstance(e, dict)]
    has_pin_errors = any("PIN" in c for c in exc_codes)
    has_footprint_errors = any("FOOTPRINT" in c or "BAD_FOOTPRINT" in c for c in exc_codes)
    has_lib_errors = any("LIB" in c or "PART" in c for c in exc_codes)
    has_code_errors = "CODE_EXEC_ERROR" in exc_codes
    has_engine_failure = any(c in {"ENGINE_CRASH", "ENGINE_TIMEOUT"} for c in exc_codes)
    has_tool_failure = any(
        c in {
            "ROUTE_UNAVAILABLE",
            "DRC_TOOL_FAILURE",
            "MANUFACTURING_OUTPUT_FAILURE",
            "POST_ARTIFACT_FAILURE",
        }
        for c in exc_codes
    )
    has_no_candidates = any(
        isinstance(e, dict) and not e.get("candidates")
        for e in exceptions
    )

    if decision_required:
        if has_engine_failure:
            return (
                "Backend engine failure, not circuit feedback. Retry once unchanged; "
                f"if it repeats, fetch get_run('{run_id}') to inspect stderr_tail "
                "and any partial_artifacts, then report the service failure."
            )
        if has_code_errors and is_skidl_python:
            first = next(
                (e for e in exceptions if isinstance(e, dict)
                 and e.get("code") == "CODE_EXEC_ERROR"),
                {},
            )
            subject = first.get("subject") if isinstance(first, dict) else {}
            if not isinstance(subject, dict):
                subject = {}
            line = subject.get("line")
            line_text = subject.get("line_text")
            available = subject.get("available_pins") or []
            suggestions = subject.get("suggested_pins") or []
            details = []
            if line:
                details.append(f"line {line}")
            if line_text:
                details.append(f"`{line_text}`")
            if suggestions:
                details.append(f"suggested pins: {', '.join(map(str, suggestions[:8]))}")
            elif available:
                details.append(f"available pins: {', '.join(map(str, available[:12]))}")
            suffix = f" ({'; '.join(details)})" if details else ""
            return (
                f"SKiDL code execution error{suffix}. Edit the SKiDL source "
                "and resubmit with submit_skidl_code()."
            )
        if has_tool_failure:
            return (
                "Manufacturing is incomplete: do not call this board "
                "manufacturable or complete. Inspect the exception subjects "
                f"and fetch get_run('{run_id}') for generated artifacts. If "
                "congestion, long power nets, outline issues, or DRC errors "
                "are present, revise the board size, layer count, edge "
                "placement, or part choices before retrying; otherwise report "
                "the routing/export tool failure."
            )
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
        if is_skidl_python:
            if has_no_candidates:
                parts.append(
                    "No machine-applicable code patch is available; use the "
                    "exception message, subject, and retry_hint to edit the "
                    "SKiDL code, then resubmit with submit_skidl_code()"
                )
            else:
                parts.append(
                    "Use the candidates as repair guidance, edit the SKiDL code, "
                    "and resubmit with submit_skidl_code()"
                )
        else:
            if has_no_candidates:
                parts.append(
                    "At least one exception has no machine candidate. Edit the "
                    "legacy CircuitSpec manually using the exception retry_hint, "
                    "or switch to submit_skidl_code() for the Python-first flow"
                )
            else:
                parts.append(
                    "Pick candidate fixes and call apply_correction(run_id, corrections). "
                    "This path is legacy CircuitSpec JSON; new clients should prefer "
                    "submit_skidl_code(). Read resource eda://guide/exceptions for "
                    "the full correction model"
                )
        return ". ".join(parts) + "."

    if is_skidl_python:
        return (
            f"Run finished with {len(exceptions)} exception(s). Inspect the "
            "candidates, edit the SKiDL code, and resubmit with submit_skidl_code(); "
            f"or get_run('{run_id}') if the results are acceptable. "
            "Read resource eda://guide/exceptions for correction guidance."
        )

    return (
        f"Run finished with {len(exceptions)} exception(s). "
        f"Inspect the candidates and fix this legacy internal CircuitSpec run, or "
        f"get_run('{run_id}') if the results are acceptable. "
        f"Read resource eda://guide/exceptions for correction guidance."
    )


async def estimate_complexity(input_spec: dict) -> dict:
    """Legacy/internal pre-flight estimate for a CircuitSpec.

    Predicts: complexity_tier (simple|moderate|complex|ambitious), expected
    decision count, how many will auto-fix vs need review, runtime/timeout
    risk, and warnings (e.g. unknown footprints, board too dense).

    Use it to choose run_options before legacy submit_design(): an "ambitious"
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
            f"CircuitSpec JSON is legacy/internal; new clients should use "
            f"submit_skidl_code()"
        )
    elif warnings:
        parts.append(
            f"{len(warnings)} warning(s) — review but not blocking"
        )

    if tier in ("complex", "ambitious"):
        parts.append(
            f"Complexity: {tier}. Use timeout_s=900 or higher in run_options "
            f"when you submit"
        )

    if not issues:
        parts.append(
            "Legacy CircuitSpec looks valid. New MCP clients should usually "
            "write SKiDL Python and submit with submit_skidl_code(); the engine "
            "runs design review, enrichment, routing, and DRC automatically"
        )

    return ". ".join(parts) + "."


async def apply_correction(run_id: str, corrections: list[dict]) -> dict:
    """Legacy CircuitSpec-only: apply chosen candidates and submit a new job.

    Do not use this for submit_skidl_code() runs. For SKiDL Python runs,
    read the structured exception/candidate details, edit the Python source,
    and resubmit with submit_skidl_code().

    For legacy CircuitSpec runs, this is the iteration step. When get_job() returns
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
    saved_spec = run_data["spec"]
    if isinstance(saved_spec, dict) and saved_spec.get("_mode") == "skidl_python":
        raise ValueError(
            "apply_correction only supports legacy CircuitSpec JSON runs. "
            "This run came from submit_skidl_code(); edit the SKiDL Python "
            "source using the returned exceptions/candidates and resubmit "
            "with submit_skidl_code()."
        )
    spec = CircuitSpec.model_validate(saved_spec)
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
            "Legacy CircuitSpec corrections applied, new job queued. "
            "Poll get_job(job_id) every 10s for results. New clients should "
            "prefer the submit_skidl_code edit/resubmit loop."
        ),
    }


@mcp.tool()
async def get_run(run_id: str) -> dict:
    """Fetch full run data: spec, exceptions, response, feedback, and artifacts.

    artifacts contains .kicad_sch and .kicad_pcb file contents. It may also
    contain preview_2d_top.png (base64 flat 2D board preview for human review),
    preview_top.svg (2D vector source), preview_assembly.svg (side-aware
    front/back placement mockup), and preview_top.png (base64 KiCad 3D render
    when available). When the board passes routing, DRC, and
    manufacturing export, files also include:
    bom.csv (JLCPCB BOM with LCSC part numbers), cpl.csv (pick-and-place),
    and Gerber/drill files in the zip.

    If custom LCSC parts, multi-sheet schematics, or manufacturing files
    are present, artifacts includes _board.zip — a base64-encoded zip with
    everything needed: KiCad source, custom libraries, 3D models, project
    file, Gerbers, drill files, BOM, and CPL. Upload the gerbers/ folder
    directly to JLCPCB for ordering.

    Human review loop: when preview_2d_top.png is present, show it to the
    human. If they say what should change, call submit_human_feedback() with
    their comments before editing/resubmitting. Prior feedback for this run is
    returned in the feedback field.

    Note: run data expires ~48h after completion.
    """
    run_data = await db.load_run(run_id)
    run_data["feedback"] = await db.list_run_feedback(run_data["run_id"])
    artifacts = run_data.get("artifacts") or {}
    file_types = [k.rsplit(".", 1)[-1] for k in artifacts if "." in k and not k.startswith("_")]
    has_zip = "_board.zip" in artifacts
    has_mfg = "bom.csv" in artifacts or any(
        k.endswith(".gbr") for k in artifacts
    )
    has_preview = any(
        name in artifacts
        for name in (
            "preview_2d_top.png",
            "preview_assembly.svg",
            "preview_top.svg",
            "preview_top.png",
        )
    )
    preview_note = ""
    if has_preview:
        preview_note = (
            " Human-review previews are included: show preview_2d_top.png first "
            "for the flat 2D board view, use preview_assembly.svg when "
            "front/back assembly side assumptions matter, preview_top.svg as "
            "the KiCad vector source, or preview_top.png for the KiCad 3D render "
            "when present. "
            "After the human reviews the image, call submit_human_feedback() "
            "before revising and resubmitting."
        )
    if artifacts:
        if has_zip:
            mfg_note = ""
            if has_mfg:
                mfg_note = (
                    " Manufacturing files included: upload the gerbers/ "
                    "folder + bom.csv + cpl.csv to JLCPCB for ordering."
                )
            run_data["hint"] = (
                f"Run data retrieved with {len(artifacts) - 1} artifact(s) "
                f"({', '.join(f'.{t}' for t in sorted(set(file_types)))}). "
                f"Use the _board.zip artifact (base64-encoded) for a "
                f"self-contained KiCad project — includes schematic sheets, "
                f"custom libraries, 3D models, and project config.{preview_note}{mfg_note}"
            )
        else:
            run_data["hint"] = (
                f"Run data retrieved with {len(artifacts)} artifact(s) "
                f"({', '.join(f'.{t}' for t in sorted(set(file_types)))}). "
                f"Write these files to disk — they're complete KiCad files "
                f"you can open directly.{preview_note}"
            )
    else:
        run_data["hint"] = "Run data retrieved but no artifacts were generated."
    return run_data


def _clean_feedback_labels(labels: list[str] | None) -> list[str]:
    if not labels:
        return []
    cleaned: list[str] = []
    seen: set[str] = set()
    for label in labels:
        text = re.sub(r"[^a-z0-9_.:-]+", "-", str(label).strip().lower()).strip("-")
        if not text or text in seen:
            continue
        cleaned.append(text[:64])
        seen.add(text)
        if len(cleaned) >= 12:
            break
    return cleaned


@mcp.tool()
async def submit_human_feedback(
    run_id: str,
    feedback: str,
    target_artifact: str = "preview_2d_top.png",
    labels: list[str] | None = None,
    suggested_action: str = "",
    source: str = "human_via_agent",
    metadata: dict | None = None,
) -> dict:
    """Record human review feedback for a generated PCB run.

    Use this after get_run() and after showing the human preview_2d_top.png
    (preferred), preview_assembly.svg, preview_top.svg, or preview_top.png. This is the ask-human
    turn: preserve what the human said before you revise SKiDL code and
    resubmit a new run.

    Examples of good feedback:
    - "Header should be centered on the bottom edge and rotated 180 degrees."
    - "Mounting holes should be in the corners; this board is much too large."
    - "For Eurorack, keep jacks in two aligned rows and do not round corners."

    Parameters:
    - run_id: run being reviewed, from get_job()/get_run().
    - feedback: human's natural-language visual/design feedback.
    - target_artifact: artifact shown to the human, usually preview_2d_top.png
      or preview_assembly.svg when assembly side/front-back placement matters.
    - labels: optional short tags such as ["placement", "silkscreen", "outline"].
    - suggested_action: optional agent interpretation of the next edit.
    - metadata: optional small structured context; do not include secrets.

    Returns the stored feedback and a next-step hint. This tool records review
    data; it does not automatically modify the design.
    """
    text = str(feedback or "").strip()
    if not text:
        raise ValueError("feedback must be non-empty.")
    if len(text) > 4000:
        raise ValueError("feedback must be 4000 characters or fewer.")

    run_data = await db.load_run(run_id)
    artifacts = run_data.get("artifacts") or {}
    artifact = str(target_artifact or "").strip() or "preview_2d_top.png"
    cleaned_labels = _clean_feedback_labels(labels)
    action = str(suggested_action or "").strip()
    if len(action) > 1000:
        raise ValueError("suggested_action must be 1000 characters or fewer.")

    structured = {
        "labels": cleaned_labels,
        "suggested_action": action,
        "artifact_exists": artifact in artifacts,
        "available_preview_artifacts": [
            name for name in (
                "preview_2d_top.png",
                "preview_assembly.svg",
                "preview_top.svg",
                "preview_top.png",
            )
            if name in artifacts
        ],
    }
    if isinstance(metadata, dict) and metadata:
        structured["metadata"] = _trim_agent_value(
            metadata,
            max_str=500,
            max_items=20,
        )

    entry = await db.add_run_feedback(
        run_data["run_id"],
        feedback=text,
        artifact=artifact,
        source=str(source or "human_via_agent").strip() or "human_via_agent",
        structured=structured,
    )

    warning = None
    if artifact not in artifacts:
        warning = (
            f"Artifact {artifact!r} is not present on this run. "
            "Feedback was still recorded; verify the run/artifact pairing."
        )

    return {
        "status": "recorded",
        "feedback": entry,
        "warning": warning,
        "next_step": (
            "Use this human feedback to edit the SKiDL source or run options, "
            "then submit a new job with submit_skidl_code(). Keep the previous "
            "run_id in your own notes so the before/after loop is traceable."
        ),
    }


# ── Library search ────────────────────────────────────────────────────


def _lcsc_variants(query: str, limit: int = 6) -> list[dict]:
    """Query LCSC/JLC for package variants of a part, with pricing and stock."""
    try:
        from corpus.jlc.lookup import JLCLookup
        from corpus.jlc.footprint_resolver import footprint_from_package
    except ImportError:
        return []

    jlc = JLCLookup(use_api=False)
    parts = jlc.search_by_mfr(query, limit=limit)
    if not parts:
        return []

    seen_packages: set[str] = set()
    variants: list[dict] = []
    for p in parts:
        if p.package in seen_packages:
            continue
        seen_packages.add(p.package)
        kicad_fp = footprint_from_package(p.package)
        entry: dict = {
            "lcsc": p.lcsc,
            "mfr": p.mfr,
            "package": p.package,
            "stock": p.stock,
            "price_usd": round(p.price, 3),
            "basic_part": p.basic,
        }
        if kicad_fp:
            entry["suggested_footprint"] = kicad_fp
        variants.append(entry)
    return variants


def _search_design_notes(query: str, symbols: list, lcsc: list[dict]) -> list[str]:
    """Return agent-facing design notes for common misleading searches."""
    query_lower = query.lower()
    notes: list[str] = []

    if "keypad" in query_lower or "sw_matrix" in query_lower:
        notes.append(
            "Keypad matrices usually do not have a single KiCad symbol such as "
            "SW_Matrix_4x4. Model an onboard keypad as individual "
            'Part("Switch", "SW_Push", ...) switches wired between ROWx and COLx '
            "nets, or model an off-board keypad as a "
            'Part("Connector_Generic", "Conn_01x08", ...) row/column connector.'
        )

    if "testpoint" in query_lower or "test point" in query_lower or "test pad" in query_lower:
        notes.append(
            "Electrical test pads use the Connector:TestPoint symbol with a "
            "TestPoint:* footprint. Do not use Part(\"TestPoint\", ...); "
            'use Part("Connector", "TestPoint", '
            'footprint="TestPoint:TestPoint_Pad_D1.5mm").'
        )

    if re.search(r"\b(bme280|bmp280|bme680|bosch|lga[-_\s]*8)\b", query_lower):
        notes.append(
            "Bosch environmental sensors such as BME280/BMP280 use Bosch LGA "
            "packages. Search footprints for \"BME280 Bosch LGA-8\" or use the "
            "exact footprint returned by search_kicad/convert_lcsc rather than "
            "guessing a generic Package_LGA name."
        )
        notes.append(
            "BME280/BMP280 symbols commonly use SPI-style pin names in both SPI "
            "and I2C designs: SDI is I2C SDA, SCK is I2C SCL, SDO selects the "
            "I2C address, and CSB should be tied high for I2C mode."
        )

    if re.search(r"\b(opto|optocoupler|opto[-_\s]*isolator|6n13[78])\b", query_lower):
        notes.append(
            "KiCad optocoupler/opto-isolator symbols are usually in the "
            "Isolator library. For MIDI input stages, search specific parts "
            "such as search_kicad(\"6N138\", detail=true) and copy the returned "
            "Part(...) usage."
        )

    if re.search(r"\b(relay|ec2-|g5v|g6k|tx2)\b", query_lower):
        notes.append(
            "Relay symbols often expose numeric package pins only. Use "
            "search_kicad(relay_part, detail=true) before wiring and connect "
            "the exact numeric coil/contact pins returned by the symbol."
        )

    if re.search(r"\bstm32|atmega|rp2040|nrf52|samd\b", query_lower):
        has_module = any(getattr(sym, "lib", "") == "MCU_Module" for sym in symbols)
        if has_module or lcsc:
            notes.append(
                "For a custom PCB around the chip, prefer a bare MCU symbol or "
                "convert_lcsc() for the exact stocked part. MCU_Module/NUCLEO/Pico "
                "symbols represent whole development boards with board-header "
                "pins, not necessarily the MCU's raw package pins. Use module "
                "symbols only when you intend to mount that module/dev board."
            )
        if "stm32" in query_lower:
            notes.append(
                "STM32 manufacturer order codes may map to KiCad package-family "
                "symbols, for example an exact ...T6 stocked part can use a "
                "matching ...Tx KiCad symbol while the exact order code is kept "
                "through convert_lcsc()/BOM metadata."
            )

    return notes


@mcp.tool()
async def search_kicad(query: str, detail: bool = False) -> dict:
    """Search KiCad symbol libraries and footprints by name or description.

    Call this BEFORE writing SKiDL code to find the correct library name,
    part name, and footprint for any component. Saves multiple submit/fail
    cycles from guessing library names.

    Also returns LCSC/JLCPCB sourcing data: package variants, stock, unit
    price, and suggested KiCad footprint for each variant. Use this to pick
    the right footprint (cost vs size vs hand-solderability).

    Examples:
        search_kicad("MCP9808")       -> symbol + MSOP-8 ($1.69) / DFN-8 ($2.36)
        search_kicad("STM32F405")     -> symbol + LQFP-64 / UFQFPN-64 variants
        search_kicad("USB-C receptacle") -> Connector : USB_C_Receptacle_... with Connector_USB footprints
        search_kicad("ATmega328P")    -> TQFP-32 / VQFN-32 / DIP-28 with prices
        search_kicad("IS31FL3731", detail=True) -> pin list + QFN-28 / SSOP-28

    query: Part number, IC name, function description, or footprint name.
      For electromechanical parts, include the decision terms you need:
      "3.5mm mono switched right angle through hole jack", "TRS vertical
      SMD jack", "edge-facing USB-C receptacle", "2-pin 5.08mm screw terminal".
    detail: If true, returns pin lists for the top symbol matches. The
      pin_detail field is the top match; pin_details contains several
      part-specific pin lists so you can inspect the exact symbol you choose.
    """
    from llm.kicad_index import (
        get_symbol_detail,
        search_footprints,
        search_symbols,
    )

    symbols = search_symbols(query, limit=8)
    footprints = search_footprints(query, limit=5)

    result: dict = {"symbols": [], "footprints": footprints}
    for sym in symbols:
        entry = {
            "library": sym.lib,
            "part": sym.name,
            "description": sym.description,
            "default_footprint": sym.footprint,
            "pin_count": sym.pin_count,
            "usage": f'Part("{sym.lib}", "{sym.name}", footprint="{sym.footprint}")'
            if sym.footprint
            else f'Part("{sym.lib}", "{sym.name}", footprint="...")',
        }
        result["symbols"].append(entry)

    if detail and symbols:
        pin_details = []
        for sym in symbols[:5]:
            det = get_symbol_detail(sym.lib, sym.name)
            if not det:
                continue
            pin_details.append({
                "part": f"{det.lib}:{det.name}",
                "footprint": det.footprint,
                "pins": [
                    {"num": p.num, "name": p.name, "type": p.func}
                    for p in det.pins
                ],
            })
        if pin_details:
            result["pin_detail"] = pin_details[0]
            result["pin_details"] = pin_details

    lcsc = _lcsc_variants(query)
    design_notes = _search_design_notes(query, symbols, lcsc)
    if design_notes:
        result["design_notes"] = design_notes
    if lcsc:
        result["lcsc_variants"] = lcsc
        has_kicad_sym = len(result["symbols"]) > 0
        if has_kicad_sym:
            result["hint"] = (
                "Use the 'usage' field directly in your SKiDL code. "
                "Set detail=true for pin names. "
                "lcsc_variants shows available packages with pricing — "
                "pick suggested_footprint for the package you want."
            )
        else:
            result["hint"] = (
                "No KiCad symbol found, but LCSC parts exist. "
                "Call convert_lcsc(lcsc='CXXXXXX') with any lcsc ID below "
                "to generate a KiCad symbol+footprint from EasyEDA, then "
                "use the returned Part() call in your SKiDL code."
            )
    else:
        result["hint"] = (
            "Use the 'usage' field directly in your SKiDL code. "
            "Set detail=true to see pin names for wiring. "
            "USB symbols are usually in the Connector symbol library; "
            "Connector_USB is a footprint library. "
            "For jacks/connectors, decide orientation, mounting, switching, "
            "and mono/stereo/TRS before cycling through footprints; read "
            "eda://guide/parts for the checklist."
        )
    if design_notes:
        result["hint"] += " " + " ".join(design_notes)
    return result


EASYEDA_CACHE = os.path.join(os.path.dirname(__file__), "..", "corpus", "jlc", "easyeda_cache")


def _pin_details_from_sym_file(sym_file: str, symbol: str | None) -> list[dict]:
    """Extract pin details from a generated one-file KiCad symbol library."""
    if not sym_file or not os.path.exists(sym_file):
        return []
    try:
        from simp_sexp import Sexp
    except ImportError:
        return []

    try:
        content = open(sym_file, encoding="utf-8", errors="replace").read()
        lib_sexp = Sexp(content)
        symbols = lib_sexp.search("/kicad_symbol_lib/symbol", ignore_case=True)
    except Exception:
        return []
    if not symbols:
        return []

    by_name = {s[1]: s for s in symbols if len(s) > 1 and isinstance(s[1], str)}
    sym = by_name.get(symbol) if symbol else None
    if sym is None:
        sym = next(
            (
                s for s in symbols
                if len(s) > 1 and isinstance(s[1], str) and ":" not in s[1]
            ),
            symbols[0],
        )

    extends = sym.search("/symbol/extends", ignore_case=True)
    pin_source = sym
    if extends:
        parent = by_name.get(extends[0][1])
        if parent is not None:
            pin_source = parent

    pin_type_map = {
        "input": "input", "output": "output", "bidirectional": "bidirectional",
        "tri_state": "tristate", "passive": "passive", "power_in": "power_in",
        "power_out": "power_out", "open_collector": "output",
        "open_emitter": "output", "free": "unspecified",
        "unspecified": "unspecified", "no_connect": "no_connect",
    }
    pins: list[dict] = []
    seen: set[str] = set()
    pin_sources = [pin_source] + (pin_source.search("/symbol/symbol", ignore_case=True) or [])
    for source in pin_sources:
        for pin in source.search("/symbol/pin", ignore_case=True):
            try:
                pin_name_node = pin.search("/pin/name")
                pin_num_node = pin.search("/pin/number")
                if not pin_name_node or not pin_num_node:
                    continue
                num = str(pin_num_node[0][1])
                if num in seen:
                    continue
                seen.add(num)
                pins.append({
                    "num": num,
                    "name": str(pin_name_node[0][1]),
                    "type": pin_type_map.get(str(pin[1]).lower(), "unspecified"),
                })
            except Exception:
                continue
    return pins


def _augment_converted_meta(meta: dict) -> dict:
    """Add parsed pin details to converted EasyEDA metadata when possible."""
    if not isinstance(meta, dict) or meta.get("pin_detail"):
        return meta
    pins = _pin_details_from_sym_file(meta.get("sym_file", ""), meta.get("symbol"))
    if not pins:
        return meta
    augmented = dict(meta)
    augmented["pin_detail"] = {
        "part": f"{meta.get('library')}:{meta.get('symbol')}",
        "footprint": meta.get("footprint"),
        "pins": pins,
    }
    return augmented


async def _convert_easyeda(lcsc: str) -> dict | None:
    """Convert LCSC part via easyeda2kicad. Checks DB cache → disk cache → API."""
    import subprocess

    lcsc = lcsc.upper()
    if not lcsc.startswith("C"):
        lcsc = f"C{lcsc}"

    # 1. DB cache (persists across deploys)
    try:
        row = await db.fetchrow(
            "SELECT meta, sym_data, fp_data FROM converted_parts WHERE lcsc = $1",
            lcsc,
        )
        if row:
            meta = json.loads(row["meta"]) if isinstance(row["meta"], str) else row["meta"]
            # Restore files to disk cache if missing
            cache_dir = os.path.join(EASYEDA_CACHE, lcsc)
            sym_file = meta.get("sym_file", "")
            if sym_file and not os.path.exists(sym_file) and row["sym_data"]:
                os.makedirs(cache_dir, exist_ok=True)
                with open(sym_file, "wb") as f:
                    f.write(row["sym_data"])
            fp_dir = meta.get("fp_dir")
            if fp_dir and not os.path.isdir(fp_dir) and row["fp_data"]:
                os.makedirs(fp_dir, exist_ok=True)
                with open(os.path.join(fp_dir, meta["footprint"].split(":")[-1] + ".kicad_mod"), "wb") as f:
                    f.write(row["fp_data"])
            return _augment_converted_meta(meta)
    except Exception:
        pass

    # 2. Disk cache
    cache_dir = os.path.join(EASYEDA_CACHE, lcsc)
    meta_file = os.path.join(cache_dir, "meta.json")

    if os.path.exists(meta_file):
        try:
            with open(meta_file) as f:
                return _augment_converted_meta(json.loads(f.read()))
        except (json.JSONDecodeError, OSError):
            pass

    # 3. Convert via easyeda2kicad API
    os.makedirs(cache_dir, exist_ok=True)
    output_prefix = os.path.join(cache_dir, lcsc)

    try:
        proc = await asyncio.get_event_loop().run_in_executor(
            None,
            lambda: subprocess.run(
                ["easyeda2kicad", "--full", f"--lcsc_id={lcsc}",
                 f"--output={output_prefix}"],
                capture_output=True, text=True, timeout=30,
            ),
        )
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return None

    sym_file = f"{output_prefix}.kicad_sym"
    pretty_dir = f"{output_prefix}.pretty"

    if not os.path.exists(sym_file):
        return None

    sym_name = None
    with open(sym_file) as f:
        for line in f:
            if "(symbol " in line and ":" not in line:
                parts = line.strip().split('"')
                if len(parts) >= 2:
                    sym_name = parts[1]
                    break

    fp_name = None
    fp_file = None
    if os.path.isdir(pretty_dir):
        mods = [fn for fn in os.listdir(pretty_dir) if fn.endswith(".kicad_mod")]
        if mods:
            fp_name = mods[0][:-9]
            fp_file = os.path.join(pretty_dir, mods[0])

    step_file = None
    shapes_dir = f"{output_prefix}.3dshapes"
    if os.path.isdir(shapes_dir):
        steps = [fn for fn in os.listdir(shapes_dir) if fn.endswith(".step")]
        if steps:
            step_file = os.path.join(shapes_dir, steps[0])

    lib_name = lcsc
    meta = {
        "lcsc": lcsc,
        "library": lib_name,
        "symbol": sym_name,
        "footprint": f"{lib_name}:{fp_name}" if fp_name else None,
        "sym_file": sym_file,
        "fp_dir": pretty_dir if fp_name else None,
    }

    with open(meta_file, "w") as f:
        f.write(json.dumps(meta, indent=2))

    # 4. Persist to DB for cross-deploy cache
    try:
        sym_data = open(sym_file, "rb").read() if os.path.exists(sym_file) else None
        fp_data = open(fp_file, "rb").read() if fp_file and os.path.exists(fp_file) else None
        step_data = open(step_file, "rb").read() if step_file and os.path.exists(step_file) else None
        await db.execute(
            """INSERT INTO converted_parts (lcsc, sym_data, fp_data, step_data, meta)
               VALUES ($1, $2, $3, $4, $5)
               ON CONFLICT (lcsc) DO UPDATE SET
                 sym_data = EXCLUDED.sym_data,
                 fp_data = EXCLUDED.fp_data,
                 step_data = EXCLUDED.step_data,
                 meta = EXCLUDED.meta""",
            lcsc, sym_data, fp_data, step_data, json.dumps(meta),
        )
    except Exception:
        pass

    return _augment_converted_meta(meta)


@mcp.tool()
async def convert_lcsc(lcsc: str) -> dict:
    """Convert an LCSC/JLCPCB part to a KiCad symbol+footprint.

    Use this when search_kicad() finds LCSC parts but no KiCad symbol.
    Converts the EasyEDA component to KiCad format so you can use it
    in your SKiDL code. The generated library is automatically available
    to submit_skidl_code().

    lcsc: LCSC part number, e.g. "C191206" or "C14877"

    Returns the Part() call to use in your SKiDL code, with correct
    library name, part name, and footprint.
    """
    meta = await _convert_easyeda(lcsc)

    if meta is None:
        return {
            "ok": False,
            "error": f"Failed to convert {lcsc}. The part may not exist on LCSC.",
            "hint": "Try a different LCSC part number from search_kicad() results.",
        }

    sym = meta.get("symbol", "Unknown")
    fp = meta.get("footprint")
    lib = meta.get("library")

    usage = f'Part("{lib}", "{sym}", footprint="{fp}")' if fp else f'Part("{lib}", "{sym}")'

    result = {
        "ok": True,
        "lcsc": meta["lcsc"],
        "library": lib,
        "symbol": sym,
        "footprint": fp,
        "usage": usage,
        "hint": "Use the 'usage' field in your SKiDL code. The library is auto-loaded.",
    }
    if meta.get("pin_detail"):
        result["pin_detail"] = meta["pin_detail"]
        result["hint"] = (
            "Use the 'usage' field in your SKiDL code. The library is "
            "auto-loaded. Use pin_detail.pins for exact converted-symbol pin "
            "names; do not assume MCU/module functional aliases exist."
        )
    return result


# ── Resources: deep reference an agent reads on demand ─────────────────


def circuit_spec_schema() -> str:
    return json.dumps(CircuitSpec.model_json_schema(), indent=2)


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


@mcp.resource(
    "eda://guide/parts",
    name="Part choice guide",
    description="How to choose ambiguous electromechanical parts such as audio jacks, USB connectors, screw terminals, switches, and headers.",
    mime_type="text/markdown",
)
def parts_guide() -> str:
    return PARTS_GUIDE


PARTS_GUIDE = """\
# Part choice guide

Some parts are not just electrical symbols. Their footprint is a product
decision. Before cycling through footprints, decide the mechanical variant and
then search with those words.

## 3.5mm audio and synth jacks

Ask or decide these points before choosing a jack:

- **Mono/TS vs stereo/TRS**: mono CV/gate usually needs TS; MIDI TRS and
  stereo audio need TRS.
- **Switched vs unswitched**: switched jacks add normalling pins. Use them
  only when the design needs detect/normalling; otherwise they add routing
  and pin-name confusion.
- **Mounting**: through-hole is stronger and easier to hand solder; SMD is
  lower profile but mechanically weaker unless the footprint has support tabs.
- **Orientation**: horizontal/right-angle is edge-facing for enclosures;
  vertical points out of the board face and is usually the right choice for
  Eurorack/control-panel PCBs that sit behind a panel.
- **Panel/edge constraint**: jacks, USB-C, switches, pots, and screw terminals
  normally belong on a board edge. Keep mating parts on the same edge when
  the product has a panel.
- **Sleeve/ground**: connect sleeve to circuit GND unless the request calls
  for isolated/chassis grounding. Shield/chassis pins may be separate.

Search examples:

```
search_kicad("3.5mm TRS unswitched right angle through hole jack", detail=true)
search_kicad("3.5mm mono switched right angle jack footprint")
search_kicad("AudioJack3 right angle through hole")
search_kicad("PJ-320A 3.5mm TRS jack")
```

Common KiCad symbol patterns:

- `Connector_Audio:AudioJack2` or similar: mono/TS.
- `Connector_Audio:AudioJack3`: TRS stereo or TRS MIDI.
- Symbols with switch pins expose extra contacts. Inspect with
  `detail=true` and wire only the required contacts.
- Simple unswitched plug/jack symbols usually expose `T`, `R`, and `S`.
  Switched or dual jack symbols may expose `T1`, `T2`, `TN`, `TN1`, `R1`,
  `RN1`, `S1`, `SN1`, etc. `N` pins are normalled/switched contacts, not
  the main plug contact. If you do not need normalling/detect, choose the
  simpler unswitched symbol to reduce routing and pin-name ambiguity.
- MIDI DIN is a circular DIN connector. Do not substitute DIN41612 footprints;
  those are backplane/card connectors, not 5-pin MIDI panel connectors.

If the request does not specify jack style, make the choice visible in your
final report. For enclosure-edge products, prefer edge-facing through-hole
jacks unless there is a reason to choose SMD or vertical.

For Eurorack or synth module boards with a Eurorack/Doepfer/IDC power header,
prefer Thonkiconn/PJ398-style vertical panel jacks. Do not choose horizontal
PJ320/right-angle edge jacks for Eurorack unless the human explicitly asks for
edge-mounted jacks or a non-standard mechanical stack.

For Eurorack panel/control boards, decide the assembly stack before submitting:
single-board Eurorack often wants front-facing jacks/pots/switches/LEDs and
rear-facing power/electronics, but that is a double-sided assembly choice and
can add cost. If the human prefers single-sided assembly, keep SMD/electronics
on one side or model a two-board panel/main-board stack. State this choice in
`run_options.assembly_policy` and your final report. When double-sided is
chosen, set `part.assembly_side = "front"` for panel-facing controls/jacks/LEDs
and `"back"` for rear-facing power/electronics where the side matters.

## USB-C receptacles

Decide whether the board needs:

- power-only sink, USB 2.0 data, or USB-PD/CC controller support
- through-hole shell tabs versus pure SMD
- edge-facing horizontal connector versus vertical connector
- 6-pin/power-only, 16-pin USB2, or 24-pin full-featured connector

For a 5V sink board, include 5.1K pull-downs on CC1 and CC2, VBUS bulk
capacitance, and ESD/TVS protection when requested. Search with terms like
`USB_C_Receptacle USB2.0 16P` or use `convert_lcsc()` for a specific stocked
connector. KiCad USB connector symbols normally use symbol library `Connector`
with `USB_C_Receptacle_*` / `USB_B_*` part names; `Connector_USB` is a
footprint library, not the symbol library passed as the first Part() argument.

## Screw terminals and headers

- Screw terminals are usually edge-facing and through-hole.
- Pitch matters: 3.5mm, 3.81mm, and 5.08mm are not interchangeable.
- A pin header footprint is not a symbol. Use
  `Part("Connector_Generic", "Conn_01xNN", footprint="Connector_PinHeader_...")`.

## Plug-in dev modules and sockets

- Treat plug-in boards such as Daisy Seed, Pico, Feather, and Arduino as
  module sockets, not as bare MCU ICs and not as generic edge headers.
- Daisy Seed should normally be represented as:
  `Part("Connector_Generic", "Conn_02x20_Counter_Clockwise", value="Daisy Seed", footprint="Module:Electrosmith_Daisy_Seed")`.
- Daisy Seed is an internal socketed module. Do not force it to a board edge
  unless the human explicitly asks for that mechanical layout.
- Daisy Seed VIN is pin 39 and GND is pin 40. AGND is pin 20. Pins 21 and 38
  are Daisy 3.3V regulator outputs; do not drive them from an external 3.3V
  rail unless the human explicitly confirms the power architecture.
- Daisy audio pins are fixed-function codec pins: 16/17 are audio inputs and
  18/19 are audio outputs. USB D-/D+ are pins 36/37. MIDI UART designs usually
  use the exposed UART pins, with any required opto/level shifting off-module.

## Keypads and switch matrices

- KiCad usually does not provide one ready-made `SW_Matrix_4x4` symbol.
- For an onboard keypad, instantiate one `Part("Switch", "SW_Push", ...)`
  per key and wire each switch between a ROWx net and a COLx net.
- For an off-board membrane/keypad, use a row/column connector such as
  `Part("Connector_Generic", "Conn_01x08", ...)` for 4 rows + 4 columns.
- Search `keypad switch` or `SW_Push`, not only `keypad matrix`, when you
  need the primitive switch symbol.

## Mounting holes and test points

- Mounting holes are mechanical parts. Use `Mechanical:MountingHole` or
  `Mechanical:MountingHole_Pad`, not `Device:TestPoint`.
- Plain screw holes usually have no electrical net:
  `Part("Mechanical", "MountingHole", footprint="MountingHole:MountingHole_3.2mm_M3")`.
- Use plated/padded mounting holes only when the hole should connect to a
  net such as chassis, shield, or GND:
  `Part("Mechanical", "MountingHole_Pad", footprint="MountingHole:MountingHole_3.2mm_M3_Pad_TopBottom")`.
- Electrical probe pads are `Connector:TestPoint` with a `TestPoint:*`
  footprint, then connect pin 1 to the measured net:
  `tp = Part("Connector", "TestPoint", footprint="TestPoint:TestPoint_Pad_D1.5mm")`.

Search examples:

```
search_kicad("Mechanical MountingHole M3", detail=true)
search_kicad("Connector TestPoint pad", detail=true)
search_kicad("TestPoint_Pad_D1.5mm footprint")
```

## When to ask the human

Ask before committing when the mechanical choice affects the product:

- panel-facing versus board-facing controls/connectors
- vertical versus right-angle jacks
- switched/normalling audio jacks
- unusual pitches or enclosure-driven connector locations
- isolated/chassis ground requirements

If you cannot ask, choose a conservative default and state it explicitly.
"""


CIRCUIT_SPEC_GUIDE = """\
# Writing a CircuitSpec (Legacy/Internal)

CircuitSpec JSON is no longer the preferred public MCP surface. Agents should
write SKiDL Python and call `submit_skidl_code()`; use `eda://guide/skidl` for
that workflow. This reference remains for internal runners, compatibility
tests, and debugging the translator/correction machinery.

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
| corner_radius_mm | number | optional corner radius for rectangular outlines. Omit for the product-board default, set `0` for square corners |
| layers | int | copper layers, 2 (default) or 4. Use 4 for dense boards |

**Choosing board size:** Most boards should omit `form_factor` and set
`outline_hint_mm` instead. A "compact" sensor breakout might be `[25, 20]`,
a medium MCU board `[50, 40]`. Do NOT use descriptive words like "compact"
or "small" — these are not valid form_factor values.

**Corner radius:** Most product boards should use modest rounded corners,
especially when mounting holes are present. Eurorack/front-panel modules are a
special mechanical/aesthetic case: ask the user or set `corner_radius_mm: 0`
unless the panel/PCB should explicitly be rounded.

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
  `Connector` (USB_C_Receptacle_..., TestPoint), `Connector_JST` (for Qwiic/STEMMA QT)
- FETs/transistors: `Transistor_FET`, `Transistor_BJT`
- Power: `Regulator_Linear`, `Regulator_Switching`
- RF: `RF_Module`

Wrong: `"lib": "Bosch"`, `"lib": "MOSFET"`, `"lib": "TI"`.
Right: `"lib": "Sensor_Pressure"`, `"lib": "Transistor_FET"`, `"lib": "Analog_ADC"`.

**Connectors (common mistake):** For pin headers use `lib: "Connector_Generic"`,
`part: "Conn_01x06"` (not "PinHeader_1x06"). USB-C/Micro-B connector symbols
usually use `lib: "Connector"` with a `Connector_USB:*` footprint. For screw
terminals use `part: "Screw_Terminal_01x02"` etc.

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
There is no global `connect()` helper.

## Step 1: Search before you code

**Always call search_kicad() first** to find correct library names, part
names, and footprints. Don't guess — KiCad library names are not obvious.

```
search_kicad("BME280")       -> Sensor_Humidity : BME280
search_kicad("STM32F405")    -> MCU_ST_STM32F4 : STM32F405RGT6
search_kicad("USB-C")        -> Connector : USB_C_Receptacle_... + Connector_USB footprints
search_kicad("level shifter") -> finds TXS0102, TXB0104, etc.
```

Use `detail=true` to see pin names for wiring: `search_kicad("MCP9808", detail=true)`

## Step 2: Write SKiDL code

```python
from skidl import *

# Power rails — always set drive = POWER
vcc = Net("VCC"); vcc.drive = POWER
gnd = Net("GND"); gnd.drive = POWER

# Parts — use exact names from search_kicad results
u1 = Part("Analog_ADC", "ADS1115IDGS",
          footprint="Package_SO:TSSOP-10_3x3mm_P0.5mm")
c1 = Part("Device", "C", value="100nF",
          footprint="Capacitor_SMD:C_0603_1608Metric")

# Connect by pin name (ICs) or number (passives)
vcc += u1["VDD"], c1[1]
gnd += u1["GND"], c1[2]
u1["ADDR"] += gnd
```

Connection syntax rules:
- Use `net += pin1, pin2` to join many endpoints to one named net.
- Use `pin += net` for a single pin-to-net connection.
- Do not use a global `connect()` function; SKiDL does not define one here.
- Do not put a function call or temporary expression on the left side of `+=`.
- Do not use `Net("X") + part["PIN"]`; create `x = Net("X")`, then `x += part["PIN"]`.

## KiCad library names (NOT manufacturer names)

Common libraries — use search_kicad() for anything not listed here:

| Category | Library name | Example parts |
|----------|-------------|---------------|
| Passives | `Device` | R, C, C_Polarized, L, LED, D_Schottky |
| Controls | `Device` | R_Potentiometer, R_Potentiometer_Dual |
| Switches | `Switch` | SW_Push, SW_Reed, SW_SPST |
| Sensors | `Sensor_Temperature` | BME280, TMP117, MCP9808, LM75 |
| Sensors | `Sensor_Humidity` | BME280, SHT3x, HDC1080 |
| ADCs | `Analog_ADC` | ADS1115, MCP3008, ADS1015 |
| Regulators | `Regulator_Linear` | AP2112K, AMS1117, MCP1700 |
| Regulators | `Regulator_Switching` | TPS63000, LM2596, MP2307 |
| MCUs | `MCU_ST_STM32F4` | STM32F405RGT6 |
| MCUs | `MCU_Microchip_ATmega` | ATmega328P-AU |
| MCUs | `MCU_RaspberryPi` | RP2040 |
| MCUs | `MCU_Nordic_nRF52` | nRF52840-QIAA |
| Connectors | `Connector_Generic` | Conn_01x04, Conn_01x06, Conn_02x05 |
| USB | `Connector` | USB_C_Receptacle_USB2.0_16P |
| Audio | `Connector_Audio` | AudioJack3 |
| Transistors | `Transistor_FET` | BSS138, IRLML6244 |
| Op-amps | `Amplifier_Operational` | LM358, MCP6001 |
| Battery mgmt | `Battery_Management` | MCP73831-2-OT |
| Clock/timer | `Timer` | NE555, DS3231M |

## Key rules

- **Search first**: `search_kicad("your part")` before writing any Part() call.
- Ambiguous mechanical parts: read `eda://guide/parts` before choosing
  jacks, USB connectors, screw terminals, switches, pots, or panel parts.
- `lib` is a KiCad symbol library, NOT a manufacturer (not "Bosch", "Microchip").
- Every Part needs `footprint="Library:Name"` — get it from search_kicad().
- Connectors: `Part("Connector_Generic", "Conn_01x06", ...)` not "PinHeader_1x06".
- USB connectors use symbol library `Connector` with a `Connector_USB:*`
  footprint; `Connector_USB` is the footprint library, not the symbol library.
- FFC/FPC display connectors normally use a generic symbol such as
  `Connector_Generic:Conn_01xNN` with a `Connector_FFC-FPC:*` footprint.
  `Connector_FFC-FPC` is a footprint library, not a symbol library.
- Keypads/matrices: do not invent `SW_Matrix_4x4`. Use individual
  `Switch:SW_Push` parts wired between row/column nets, or a
  `Connector_Generic:Conn_01xNN` for an off-board keypad.
- Custom MCU board vs dev board: `MCU_Module` symbols such as NUCLEO,
  Feather, Pico, or Arduino represent whole modules with header pins. For a
  custom PCB around the chip, use a bare MCU symbol or `convert_lcsc()`.
- Electrosmith Daisy Seed is a socketed module: use
  `Connector_Generic:Conn_02x20_Counter_Clockwise` with footprint
  `Module:Electrosmith_Daisy_Seed`, and treat it as an internal module socket.
- Connections: use `net += pin1, pin2` or `pin += net`; no global `connect()`.
- Decoupling caps: value="100nF" wired power-to-ground = auto-placed near parent IC.
- Standard power names: VCC, VDD, 3V3, 5V, VBUS, VBAT, GND, AGND.
- Pin names on ICs may differ — use `search_kicad("part", detail=true)` to check.
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

- For address selection: use pull-up/pull-down resistors on ADDR pins.
- Include bulk caps (10uF) on power rails and 100nF decoupling per IC.
- Count connector pins carefully: VCC + GND + SDA + SCL + A0 + A1 = 6 pins.
- If a Part() fails, search_kicad() the part number to find the right library.
"""


WORKFLOW_GUIDE = """\
# Design workflow

## Step 0 — Plan your design

Write SKiDL Python code and submit via `submit_skidl_code()`.
Read `eda://guide/skidl` for the API reference.

Think through the full BOM before coding:
- Every IC with its function (sensor, regulator, MCU, etc.)
- Every passive: 100nF decoupling per IC power pin, 10uF bulk caps
  on each power rail, pull-ups, shunt resistors, LED limiters
- Every connector: count pins by listing signals (VCC, GND, SDA,
  SCL, A0, A1 = 6 pins, not 4)
- Address selection hardware (solder jumpers, pull-downs)

## Step 1 — Submit and iterate

Every design succeeds if you persist. The engine gives you exact errors
and suggested fixes at every step — edit the SKiDL source, resubmit, and the
board converges to a manufacturable PCB. First-pass submissions typically need
3-8 correction rounds for library names, pin names, and footprints. This is
normal and expected — each round gets closer.

```
submit_skidl_code(code, board_name)   -> job_id
        |
get_job(job_id)  ... poll every 5-15s ...  -> result when finished
        |
  result.exceptions empty?
        |-- yes -> get_run(result.run_id) -> artifacts. Done!
        |-- no  -> read the error, fix your code, resubmit
```

The engine pipeline runs: enrich (auto-adds decoupling caps, pull-ups) →
translate → design review → schematic + ERC → placement → routing
(Freerouting) → DRC (kicad-cli). Problems at any stage come back as
structured exceptions with candidates — you never need to inspect
intermediate artifacts or call external tools. The final .kicad_pcb
is a routed board when routing succeeds.

## Polling

- queued -> running transition is usually <5s when workers are free.
- Simple boards (<20 parts): done in 10-60s. Dense boards: minutes; raise
  timeout_s when a design has many ICs, connectors, or tight routing.
  If the exception is ROUTE_TIMEOUT, retry the same code first with
  route_timeout_s=300-900 before redesigning the board.
- status "failed" or "timeout" with exceptions attached is the NORMAL
  correction path, not an outage. Only a null result + error string means
  the job crashed.

## run_options

| key | default | when to change |
|---|---|---|
| timeout_s | 300 | raise to 600-1500 for dense boards or slow autorouting |
| route_timeout_s | 120 | raise to 300-900 after ROUTE_TIMEOUT; must stay below timeout_s |
| assembly_policy | "single_sided" | set "double_sided" only after deciding the human accepts rear-side assembly cost |
| board_id | none | telemetry label for tracking related runs |

Choose `assembly_policy` before submitting. The default `"single_sided"` keeps
SMD/electronics on the front where possible and avoids using the back as an
easy placement escape hatch. `"double_sided"` allows front-panel controls with
rear electronics, which is often mechanically right for a single-board
Eurorack module but can add fabrication/assembly cost. If a Eurorack design
asks for single-sided assembly, either keep the board genuinely single-sided or
model a two-board panel/main-board stack rather than silently moving SMD parts
to the back.

`assembly_policy` is the board-level cost/permission gate. It is not the whole
floorplan. If `"double_sided"` is chosen, be opinionated about sides by setting
`part.assembly_side = "front"` or `"back"` for mechanically meaningful parts.
For Eurorack, strongly prefer front controls/jacks/LEDs and rear
power/electronics on single-board modules. For other boards, use the back only
when it serves a real mechanical, connector, thermal, shielding, or packaging
reason; otherwise keep the design single-sided and cheaper.

## Layout feedback

- LAYOUT_OVERLAP, LAYOUT_OUTLINE_VIOLATION, and HIGH_CONGESTION are usually
  placement/constraint feedback, not schematic failures.
- When `get_run()` returns `preview_2d_top.png`, show it to the human before
  claiming the board is visually acceptable. If front/back assembly side matters,
  also show `preview_assembly.svg`. If they give feedback, immediately
  call `submit_human_feedback(run_id, feedback, target_artifact="preview_2d_top.png")`
  to record the review turn, then edit the SKiDL source or run options and
  resubmit.
- Before blindly enlarging the board, edit the SKiDL source so related parts
  are in the same `@subcircuit`, decoupling caps sit with their IC, connector
  footprint style matches the product, and panel/edge parts are deliberate.
- If the physical request is simply too dense, resubmit with a larger
  `outline_mm=[width, height]`.

## Iterating - KEEP GOING UNTIL SUCCEEDED

- For SKiDL Python runs, edit the source code using the structured
  exceptions/candidates and call `submit_skidl_code()` again.
- **Do not stop after a fixed number of rounds.** Keep editing/resubmitting
  until get_job() returns status "succeeded". Library
  mismatches, pin name errors, and footprint fixes are normal — each
  correction gets you closer. The engine tells you exactly what's wrong
  and suggests fixes; address them all and resubmit.
- If the same exception recurs with the same candidate, pick a different
  candidate instead of repeating. If no candidates work, try a different
  lib/part/footprint from the error's `suggested_fix` or `available_pins`.
- The server auto-enriches the design with decoupling caps, I2C pull-ups,
  and other standard passives — you don't need to include those.
- Run data expires ~48h after completion — fetch artifacts promptly.

## Important tips

- **After corrections succeed**, call `get_run(run_id)` to fetch your
  .kicad_pcb and .kicad_sch artifacts. The run_id comes from the last
  get_job() result. You can also pass a job_id to get_run().
- **Pin names on ICs often differ from net names.** BME280 uses SDI/SCK
  (not SDA/SCL). When you get SPEC_UNKNOWN_PIN errors, check the
  `available_pins` list in the exception — it shows the real pin names.
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
        "SCH_ROUTING_FAILURE": "schematic wiring/rendering failed; retry once, then treat repeated failures as a renderer limitation",
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
        "ROUTE_TIMEOUT": "error: Freerouting exceeded route_timeout_s; retry unchanged with a larger route_timeout_s before changing the circuit",
        "ROUTE_UNAVAILABLE": "manufacturing gate failed: routing/export tooling did not produce routed manufacturing outputs",
        "DRC_CLEARANCE": "error: trace/pad clearance violation",
        "DRC_UNCONNECTED": "error: net endpoint not connected after routing",
        "DRC_SHORT": "error: unintended connection between nets",
        "DRC_COURTYARD": "advisory: component courtyard overlap",
        "DRC_TOOL_FAILURE": "tooling error: DRC tool failed to run",
        "MANUFACTURING_OUTPUT_FAILURE": "error: Gerbers, drill, BOM, or CPL export did not complete",
        "POST_ARTIFACT_FAILURE": "error: schematic/PCB artifacts exist, but backend finalization failed; fetch artifacts before redesigning",
        "ENGINE_TIMEOUT": "engine hit timeout_s — raise it or simplify",
        "ENGINE_CRASH": "backend worker crashed; retry once, then treat as service failure",
        "CODE_EXEC_ERROR": "SKiDL Python code raised an error; inspect subject.line, line_text, available_pins",
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
        "For SKiDL submissions, candidates are repair guidance. Edit the SKiDL source",
        "using the exception message, subject, candidate summary, and retry_hint;",
        "then call `submit_skidl_code()` again.",
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
        "- Higher confidence means the fix is usually mechanical; lower",
        "  confidence means the engine wants judgment (often JLC part",
        "  substitutions or pin guesses).",
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
        "- code_authoring_error — submitted SKiDL Python failed during execution",
        "- tool_unavailable — router/DRC tooling was missing or failed",
        "- quality_advisory — only advisories remain; waive or fix",
        "- correction_choice — general fix selection",
        "- no_candidate — at least one exception has no machine fix;",
        "  edit the SKiDL source and submit_skidl_code() again",
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
