"""FastMCP stdio server exposing EDA design generation tools."""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from mcp_server.pipeline import run_pipeline
from mcp_server.runs import RunStore
from schemas.corrections import apply_candidate


ARTIFACT_ROOT = Path(os.environ.get("EDA_MCP_ARTIFACT_DIR", "artifacts/runs"))
STORE = RunStore(ARTIFACT_ROOT)
mcp = FastMCP("eda-mcp")


@mcp.tool()
def generate_design(input_spec: dict, run_options: dict | None = None) -> dict:
    """Generate schematic/layout/PCB artifacts from a CircuitSpec JSON object."""

    opts = run_options or {}
    out_dir = Path(opts.get("out_dir") or ARTIFACT_ROOT)
    response = run_pipeline(
        input_spec,
        out_dir,
        timeout_s=float(opts.get("timeout_s", 300)),
        mode=str(opts.get("mode", "engine_only")),
        run_id=opts.get("run_id"),
        board_id=opts.get("board_id"),
        telemetry_path=opts.get("telemetry_path"),
        record_telemetry=bool(opts.get("record_telemetry", True)),
        record_fields=opts.get("record_fields"),
        validation_mode=opts.get("validation_mode"),
        model_tier=opts.get("model_tier"),
        correction_iterations=int(opts.get("correction_iterations", 0) or 0),
        corrections_applied=opts.get("corrections_applied"),
        llm_stages=opts.get("llm_stages"),
        parent_run_id=opts.get("parent_run_id"),
        bom_match_score=opts.get("bom_match_score"),
        netlist_match_score=opts.get("netlist_match_score"),
    )
    return response.model_dump(mode="json")


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
