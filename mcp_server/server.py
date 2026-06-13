"""FastMCP stdio server exposing EDA design generation tools."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server.pipeline import run_pipeline
from mcp_server.policy import (
    auto_corrections,
    correction_history_keys,
    decision_kind,
    normalize_policy,
)
from mcp_server.runs import RunStore
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.estimator import estimate_complexity as _estimate_complexity


ARTIFACT_ROOT = Path(os.environ.get("EDA_MCP_ARTIFACT_DIR", "artifacts/runs"))
STORE = RunStore(ARTIFACT_ROOT)
mcp = FastMCP("eda-mcp")


def _pipeline_kwargs(opts: dict, out_dir: Path) -> dict:
    return {
        "timeout_s": float(opts.get("timeout_s", 300)),
        "route_timeout_s": float(opts.get("route_timeout_s", 120)),
        "mode": str(opts.get("mode", "engine_only")),
        "board_id": opts.get("board_id"),
        "telemetry_path": opts.get("telemetry_path"),
        "record_telemetry": bool(opts.get("record_telemetry", True)),
        "record_fields": opts.get("record_fields"),
        "validation_mode": opts.get("validation_mode"),
        "model_tier": opts.get("model_tier"),
        "bom_match_score": opts.get("bom_match_score"),
        "netlist_match_score": opts.get("netlist_match_score"),
    }


def _apply_choices(
    spec: CircuitSpec,
    exceptions,
    choices: list[dict],
) -> tuple[CircuitSpec, list[dict], list[str]]:
    by_exc = {exc.id: exc for exc in exceptions}
    updated = spec
    applied: list[dict] = []
    telemetry_actions: list[str] = []

    for choice in choices:
        exc_id = choice.get("exception_id")
        cand_id = choice.get("candidate_id")
        exc = by_exc.get(exc_id)
        if exc is None:
            raise CorrectionError(f"unknown exception_id {exc_id!r}")
        cand = next((candidate for candidate in exc.candidates if candidate.id == cand_id), None)
        if cand is None:
            raise CorrectionError(f"unknown candidate_id {cand_id!r} for {exc_id!r}")
        updated = apply_candidate(updated, exc, cand)
        applied.append(
            {
                "exception_id": exc.id,
                "candidate_id": cand.id,
                "code": exc.code.value,
                "action": cand.action.value,
                "human_summary": cand.human_summary,
            }
        )
        telemetry_actions.append(f"{exc.code.value}:{cand.action.value}:{cand.id}")
    return updated, applied, telemetry_actions


def _decorate_response(response, *, policy, spec, out_dir: Path, corrections: list[dict]) -> dict:
    response.policy = policy.model_dump(mode="json")
    response.corrections_applied = corrections
    if response.exceptions:
        response.decision_required = True
        response.decision_kind = decision_kind(response.exceptions)
        response.recommended_next_tool = "apply_correction"
        if not response.summary:
            response.summary = "Agent decision required before continuing."
    else:
        response.decision_required = False
        response.decision_kind = ""
        response.recommended_next_tool = ""

    RunStore(out_dir).save(response.run_id, spec, response.exceptions, response)
    return response.model_dump(mode="json")


@mcp.tool()
def generate_design(
    input_spec: dict,
    run_options: dict | None = None,
    policy: dict | None = None,
) -> dict:
    """Generate schematic/layout/PCB artifacts from a CircuitSpec JSON object."""

    opts = run_options or {}
    out_dir = Path(opts.get("out_dir") or ARTIFACT_ROOT)
    generate_policy = normalize_policy(policy)
    spec = CircuitSpec.model_validate(input_spec)
    base_kwargs = _pipeline_kwargs(opts, out_dir)
    corrections: list[dict] = []
    correction_strings: list[str] = []
    history: set[str] = set()

    response = run_pipeline(
        spec,
        out_dir,
        run_id=opts.get("run_id"),
        correction_iterations=int(opts.get("correction_iterations", 0) or 0),
        corrections_applied=list(opts.get("corrections_applied") or []),
        llm_stages=opts.get("llm_stages"),
        parent_run_id=opts.get("parent_run_id"),
        **base_kwargs,
    )

    for iteration in range(generate_policy.max_internal_corrections):
        if not response.exceptions:
            break
        kind = decision_kind(response.exceptions)
        if kind in set(generate_policy.stop_for):
            break
        choices = auto_corrections(response.exceptions, generate_policy, history)
        if not choices:
            break
        history.update(correction_history_keys(response.exceptions, choices))
        spec, applied, telemetry_actions = _apply_choices(spec, response.exceptions, choices)
        corrections.extend(applied)
        correction_strings.extend(telemetry_actions)
        response = run_pipeline(
            spec,
            out_dir,
            correction_iterations=int(opts.get("correction_iterations", 0) or 0) + iteration + 1,
            corrections_applied=list(opts.get("corrections_applied") or []) + correction_strings,
            llm_stages=opts.get("llm_stages"),
            parent_run_id=response.run_id,
            **base_kwargs,
        )

    return _decorate_response(
        response,
        policy=generate_policy,
        spec=spec,
        out_dir=out_dir,
        corrections=corrections,
    )


@mcp.tool()
def estimate_complexity(input_spec: dict) -> dict:
    """Pre-run complexity estimate — predicts decisions, cost, and success probability.

    Call before generate_design() to gauge how much work a board will need.
    Fast (<2s), no side effects, no cost.
    """
    spec = CircuitSpec.model_validate(input_spec)
    return _estimate_complexity(spec).model_dump(mode="json")


@mcp.tool()
def apply_correction(run_id: str, corrections: list[dict]) -> dict:
    """Apply selected exception candidates from a previous run and regenerate."""

    spec = STORE.load_spec(run_id)
    exceptions = {exc.id: exc for exc in STORE.load_exceptions(run_id)}
    updated = spec

    for correction in corrections:
        exc_id = correction.get("exception_id")
        cand_id = correction.get("candidate_id")
        if exc_id not in exceptions:
            raise ValueError(f"unknown exception_id {exc_id!r} for run {run_id}")
        exc = exceptions[exc_id]
        candidates = {cand.id: cand for cand in exc.candidates}
        if cand_id not in candidates:
            raise ValueError(
                f"unknown candidate_id {cand_id!r} for exception {exc_id!r}"
            )
        updated = apply_candidate(updated, exc, candidates[cand_id])

    response = run_pipeline(updated, ARTIFACT_ROOT)
    return response.model_dump(mode="json")


@mcp.tool()
def get_run_telemetry(run_id: str) -> dict:
    """Return persisted spec, exceptions, and response JSON for a run."""

    return STORE.load(run_id)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
