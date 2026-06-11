"""Run a single board through the engine with agent-driven correction.

Called by Claude Code agents — the agent itself reviews exceptions and picks
corrections, using the Max subscription instead of OpenRouter.

Usage (from agent prompt):
    python3 -m corpus.agent_run_board --board ref-acapulcogold --telemetry telemetry/runs_agent.jsonl

The script runs the engine, prints exceptions + candidates as structured JSON,
then reads correction choices from stdin. Repeat until success or max iterations.

Protocol:
  1. Script prints JSON: {"status": "needs_review", "iteration": N, "exceptions": [...]}
  2. Agent reads, picks corrections, writes JSON line: {"choices": [{"exception_id": "...", "candidate_id": "..."}, ...]}
  3. Script applies corrections, re-runs engine, goto 1
  4. On success/terminal: {"status": "succeeded|failed|timeout", "run_id": "...", "summary": "..."}
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from mcp_server.pipeline import DesignResponse, run_pipeline
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.exceptions import DesignException, Severity
from corpus.run_corpus import (
    actionable_exceptions,
    apply_choices,
    deterministic_choices,
    load_manifest,
    load_cached_spec,
    write_final_record,
    RunnerConfig,
)


def _exc_summary(exc: DesignException) -> dict:
    return {
        "id": exc.id,
        "code": exc.code.value,
        "severity": exc.severity.value,
        "message": exc.message,
        "candidates": [
            {
                "id": c.id,
                "action": c.action.value,
                "description": c.human_summary or "",
                "confidence": c.confidence,
                "source": c.source,
                "params": c.params,
            }
            for c in exc.candidates
        ],
    }


def run_board_interactive(
    board_id: str,
    telemetry_path: str,
    max_iters: int = 10,
    timeout_s: float = 1200.0,
    artifacts_dir: str = "artifacts/agent_runs",
):
    manifest = load_manifest()
    row = next((r for r in manifest if r["board_id"] == board_id), None)
    if row is None:
        print(json.dumps({"status": "error", "message": f"Board {board_id} not found in manifest"}))
        return 1

    spec = load_cached_spec(row)
    if spec is None:
        print(json.dumps({"status": "error", "message": f"No cached spec for {board_id}"}))
        return 1

    config = RunnerConfig(
        mode="internal",
        model_tier="agent",
        telemetry=telemetry_path,
        artifacts=artifacts_dir,
        timeout_s=timeout_s,
        max_iters=max_iters,
    )

    t0 = time.monotonic()
    deadline = t0 + timeout_s
    corrections_applied: list[str] = []
    parent_run_id = None
    iteration = 0

    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            print(json.dumps({"status": "timeout", "iteration": iteration,
                              "message": "Board timeout reached"}))
            sys.stdout.flush()
            write_final_record(
                row=row, mode="agent", config=config, spec=spec,
                response=None, status="timeout",
                failure_reason="timeout", llm_stages=[],
                wall_time_s=time.monotonic() - t0,
            )
            return 0

        response = run_pipeline(
            spec,
            artifacts_dir,
            timeout_s=min(timeout_s, remaining),
            mode="internal",
            board_id=board_id,
            correction_iterations=iteration,
            corrections_applied=corrections_applied,
            parent_run_id=parent_run_id,
        )
        parent_run_id = response.run_id

        exceptions = actionable_exceptions(response)

        if response.ok or not exceptions:
            wall = time.monotonic() - t0
            print(json.dumps({
                "status": response.status,
                "iteration": iteration,
                "run_id": response.run_id,
                "message": f"Board {board_id} completed: {response.status}",
                "wall_time_s": round(wall, 1),
            }))
            sys.stdout.flush()
            write_final_record(
                row=row, mode="agent", config=config, spec=spec,
                response=response, status=response.status,
                failure_reason=None, llm_stages=[],
                wall_time_s=wall,
            )
            return 0

        if iteration >= max_iters:
            wall = time.monotonic() - t0
            print(json.dumps({
                "status": "max_iterations",
                "iteration": iteration,
                "run_id": response.run_id,
                "message": f"Hit max iterations ({max_iters})",
                "remaining_exceptions": len(exceptions),
            }))
            sys.stdout.flush()
            write_final_record(
                row=row, mode="agent", config=config, spec=spec,
                response=response, status="failed",
                failure_reason=f"max iterations ({max_iters})",
                llm_stages=[], wall_time_s=wall,
            )
            return 0

        # Auto-apply high-confidence deterministic choices first
        auto = deterministic_choices(exceptions, min_confidence=0.8)
        auto_ids = {c["exception_id"] for c in auto}
        remaining_exc = [e for e in exceptions if e.id not in auto_ids]

        if auto:
            try:
                spec, applied = apply_choices(spec, exceptions, auto)
                corrections_applied.extend(applied)
            except CorrectionError as e:
                print(json.dumps({"status": "error", "message": f"Auto-correction failed: {e}"}), flush=True)

        if not remaining_exc:
            iteration += 1
            continue

        # Present remaining exceptions for agent review
        print(json.dumps({
            "status": "needs_review",
            "iteration": iteration,
            "auto_applied": len(auto),
            "needs_review": len(remaining_exc),
            "exceptions": [_exc_summary(e) for e in remaining_exc],
        }))
        sys.stdout.flush()

        # Read agent's choices from stdin
        try:
            line = input()
            agent_response = json.loads(line)
        except (EOFError, json.JSONDecodeError) as e:
            print(json.dumps({"status": "error", "message": f"Failed to read agent response: {e}"}), flush=True)
            return 1

        choices = agent_response.get("choices", [])
        if choices:
            try:
                spec, applied = apply_choices(spec, exceptions, choices)
                corrections_applied.extend(applied)
            except CorrectionError as e:
                print(json.dumps({"status": "error", "message": f"Correction failed: {e}"}), flush=True)
                return 1

        iteration += 1


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--board", required=True)
    parser.add_argument("--telemetry", default="telemetry/runs_agent.jsonl")
    parser.add_argument("--max-iters", type=int, default=10)
    parser.add_argument("--timeout-s", type=float, default=1200.0)
    parser.add_argument("--artifacts", default="artifacts/agent_runs")
    args = parser.parse_args()

    sys.exit(run_board_interactive(
        board_id=args.board,
        telemetry_path=args.telemetry,
        max_iters=args.max_iters,
        timeout_s=args.timeout_s,
        artifacts_dir=args.artifacts,
    ))


if __name__ == "__main__":
    main()
