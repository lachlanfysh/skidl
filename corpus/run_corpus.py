"""Run the manifest corpus through the design-generation loop.

Phase 3b runner:
  - bounded async board execution
  - checkpoint/resume from telemetry/runs.jsonl
  - cached-spec engine-only mode plus LLM internal/external modes
  - deterministic c1 fallback when LLM review is unavailable
  - one final board-level telemetry record per run

Default usage:
    python3 -m corpus.run_corpus --mode engine_only --no-mcp
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from collections import deque
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from corpus.reference_oracle import (
    OracleError,
    _find_root_schematic,
    bom_match_score,
    extract_netlist,
    netlist_match_score,
    spec_netlist,
)
from llm.openrouter_client import BudgetExhausted, LLMUnavailable
from llm.operations import (
    SpecParseError,
    external_agent_review,
    nl_to_input_spec,
    review_exceptions,
)
from llm.spend_tracker import SpendTracker
from mcp_server.pipeline import DesignResponse, run_pipeline
from schemas.circuit_spec import CircuitSpec
from schemas.corrections import CorrectionError, apply_candidate
from schemas.exceptions import DesignException, Severity
from telemetry.features import extract_geometry
from telemetry.models import LLMStage
from telemetry.store import read_records, session


REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = REPO_ROOT / "corpus" / "manifest.jsonl"
DEFAULT_ARTIFACTS = REPO_ROOT / "artifacts" / "runs"
DEFAULT_TELEMETRY = REPO_ROOT / "telemetry" / "runs.jsonl"
DEFAULT_SPEND_LOG = REPO_ROOT / "artifacts" / "llm_spend.jsonl"
DEFAULT_PID_FILE = REPO_ROOT / "artifacts" / "run_corpus.pid"
TERMINAL_STATUSES = {
    "succeeded",
    "succeeded_with_warnings",
    "failed",
    "crashed",
    "timeout",
    "cap_exceeded",
    "skipped_budget",
    "skipped_time",
}


@dataclass
class RunnerConfig:
    manifest: Path = DEFAULT_MANIFEST
    artifacts: Path = DEFAULT_ARTIFACTS
    telemetry: Path = DEFAULT_TELEMETRY
    spend_log: Path = DEFAULT_SPEND_LOG
    pid_file: Path = DEFAULT_PID_FILE
    mode: str = "engine_only"
    model_tier: str = "mid"
    limit: int | None = None
    boards: set[str] = field(default_factory=set)
    validation_modes: set[str] = field(default_factory=set)
    timeout_s: float = 1200.0
    max_iters: int = 8
    concurrency: int = 2
    max_runtime_hours: float = 8.0
    max_total_spend_usd: float = 10.0
    max_board_tokens: int = 200_000
    no_mcp: bool = False
    force: bool = False


@dataclass
class BoardResult:
    board_id: str
    mode: str
    status: str
    run_id: str = ""
    failure_reason: str = ""


class DirectDesignClient:
    """In-process direct-pipeline fallback for corpus execution."""

    async def __aenter__(self) -> "DirectDesignClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def generate_design(self, spec: dict, run_options: dict) -> dict:
        response = await asyncio.to_thread(
            run_pipeline,
            spec,
            run_options["out_dir"],
            timeout_s=run_options["timeout_s"],
            mode=run_options["mode"],
            run_id=run_options.get("run_id"),
            board_id=run_options.get("board_id"),
            telemetry_path=run_options.get("telemetry_path"),
            record_telemetry=run_options.get("record_telemetry", False),
            record_fields=run_options.get("record_fields"),
            validation_mode=run_options.get("validation_mode"),
            model_tier=run_options.get("model_tier"),
            correction_iterations=run_options.get("correction_iterations", 0),
            corrections_applied=run_options.get("corrections_applied"),
            llm_stages=run_options.get("llm_stages"),
            parent_run_id=run_options.get("parent_run_id"),
            bom_match_score=run_options.get("bom_match_score"),
            netlist_match_score=run_options.get("netlist_match_score"),
        )
        return response.model_dump(mode="json")


class MCPDesignClient:
    """Long-lived stdio MCP client for generate_design calls."""

    def __init__(self, config: RunnerConfig):
        self.config = config
        self._stack: AsyncExitStack | None = None
        self._session = None

    async def __aenter__(self) -> "MCPDesignClient":
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        env = os.environ.copy()
        env["EDA_MCP_ARTIFACT_DIR"] = str(self.config.artifacts)
        env["PYTHONPATH"] = os.pathsep.join(
            [str(REPO_ROOT), str(REPO_ROOT / "src"), env.get("PYTHONPATH", "")]
        )
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "mcp_server.server"],
            env=env,
        )
        self._stack = AsyncExitStack()
        try:
            read, write = await self._stack.enter_async_context(stdio_client(params))
            self._session = await self._stack.enter_async_context(ClientSession(read, write))
            await self._session.initialize()
            return self
        except BaseException:
            await self._stack.aclose()
            self._stack = None
            raise

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._stack is not None:
            await self._stack.__aexit__(exc_type, exc, tb)
            self._stack = None

    async def generate_design(self, spec: dict, run_options: dict) -> dict:
        result = await self._session.call_tool(
            "generate_design",
            arguments={"input_spec": spec, "run_options": run_options},
        )
        return _tool_result_to_dict(result)


def _tool_result_to_dict(result: Any) -> dict:
    structured = getattr(result, "structuredContent", None)
    if structured is None:
        structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured
    for item in getattr(result, "content", []) or []:
        text = getattr(item, "text", None)
        if text:
            return json.loads(text)
    if isinstance(result, dict):
        return result
    raise ValueError("MCP tool result did not contain JSON content")


def _path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else REPO_ROOT / path


def _validation_mode(value: object) -> str:
    if value in {"internal", "reference", "none"}:
        return str(value)
    return "none"


def _stage_models(stages: list[dict | LLMStage]) -> list[LLMStage]:
    return [
        stage if isinstance(stage, LLMStage) else LLMStage.model_validate(stage)
        for stage in stages
    ]


def _token_count(stages: list[dict | LLMStage]) -> int:
    total = 0
    for stage in _stage_models(stages):
        total += stage.tokens_in + stage.tokens_out
    return total


def load_manifest(path: str | Path = DEFAULT_MANIFEST) -> list[dict]:
    rows: list[dict] = []
    path = _path(path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        for lineno, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if not row.get("board_id"):
                raise ValueError(f"{path}:{lineno}: manifest row missing board_id")
            rows.append(row)
    return rows


def completed_keys(telemetry_path: str | Path, mode: str) -> set[tuple[str, str]]:
    records = read_records(_path(telemetry_path))
    return {
        (record.board_id, record.mode)
        for record in records
        if record.mode == mode and record.status in TERMINAL_STATUSES
    }


def cached_spec_path(row: dict) -> Path | None:
    spec_path = row.get("spec_path")
    if not spec_path:
        return None
    path = _path(spec_path)
    return path if path.is_file() else None


def load_cached_spec(row: dict) -> CircuitSpec | None:
    path = cached_spec_path(row)
    if path is None:
        return None
    return CircuitSpec.model_validate_json(path.read_text())


_EXC_CODE_TO_DECISION_TYPE = {
    "SPEC_BAD_FOOTPRINT": "footprint",
    "SPEC_UNKNOWN_PIN": "pin",
    "SPEC_UNKNOWN_PART": "part",
    "SPEC_UNKNOWN_LIB": "library",
    "LAYOUT_OVERLAP": "layout",
    "LAYOUT_OUTLINE_VIOLATION": "layout",
    "LAYOUT_KEEPOUT": "layout",
    "HIGH_CONGESTION": "layout",
    "ERC_PIN_NOT_CONNECTED": "erc",
    "ERC_PIN_NOT_DRIVEN": "erc",
    "ERC_REAL_ERROR": "erc",
}


def _count_decisions(exceptions: list[DesignException]) -> tuple[int, dict[str, int]]:
    breakdown: dict[str, int] = {}
    for exc in exceptions:
        if exc.severity.value == "advisory":
            continue
        dtype = _EXC_CODE_TO_DECISION_TYPE.get(exc.code.value, "other")
        breakdown[dtype] = breakdown.get(dtype, 0) + 1
    return sum(breakdown.values()), breakdown


def deterministic_choices(exceptions: list[DesignException], min_confidence: float = 0.8) -> list[dict]:
    choices = []
    for exc in exceptions:
        if exc.candidates:
            best = exc.candidates[0]
            if getattr(best, "confidence", 0.9) >= min_confidence:
                choices.append(
                    {"exception_id": exc.id, "candidate_id": best.id}
                )
    return choices


def actionable_exceptions(response: DesignResponse) -> list[DesignException]:
    return [
        exc
        for exc in response.exceptions
        if exc.severity in {Severity.FATAL, Severity.ERROR}
        and exc.candidates
        and exc.code.value not in ("ENGINE_CRASH", "ENGINE_TIMEOUT")
    ]


def apply_choices(
    spec: CircuitSpec,
    exceptions: list[DesignException],
    choices: list[dict],
) -> tuple[CircuitSpec, list[str]]:
    by_exc = {exc.id: exc for exc in exceptions}
    updated = spec
    applied: list[str] = []
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
        applied.append(f"{exc.code.value}:{cand.action.value}:{cand.id}")
    return updated, applied


def _oracle_spec_dict(spec: CircuitSpec) -> dict:
    data = spec.model_dump(mode="json")
    for net in data.get("nets", []):
        nodes = []
        for pin_ref in net.get("pins", []):
            if "." not in pin_ref:
                continue
            ref, pin = pin_ref.split(".", 1)
            nodes.append({"ref": ref, "pin": pin})
        net["nodes"] = nodes
    return data


def score_reference(spec: CircuitSpec, row: dict) -> tuple[float | None, float | None]:
    if row.get("validation_mode") != "reference" or not row.get("reference_project_path"):
        return None, None
    try:
        project_dir = _path(row["reference_project_path"])
        ref = extract_netlist(_find_root_schematic(project_dir))
        gen = spec_netlist(_oracle_spec_dict(spec))
        return bom_match_score(gen, ref), netlist_match_score(gen, ref)
    except (OracleError, OSError, ValueError, subprocess.SubprocessError):
        return None, None


async def score_reference_async(
    spec: CircuitSpec,
    row: dict,
) -> tuple[float | None, float | None]:
    return await asyncio.to_thread(score_reference, spec, row)


def _failure_from_response(response: DesignResponse | None) -> str | None:
    if response is None or not response.exceptions:
        return None
    return "; ".join(exc.message for exc in response.exceptions[:3])


def write_final_record(
    *,
    row: dict,
    mode: str,
    config: RunnerConfig,
    spec: CircuitSpec | None,
    response: DesignResponse | None,
    status: str,
    failure_reason: str | None = None,
    llm_stages: list[dict | LLMStage] | None = None,
    correction_iterations: int = 0,
    corrections_applied: list[str] | None = None,
    bom_score: float | None = None,
    netlist_score: float | None = None,
    wall_time_s: float | None = None,
) -> str:
    run_id = response.run_id if response is not None else uuid.uuid4().hex[:12]
    fields = {
        "tier": int(row.get("tier", 0) or 0),
        "source": str(row.get("source", "") or ""),
        "difficulty_axis": str(row.get("difficulty_axis", "") or ""),
        "nl_source": str(row.get("nl_source", "") or ""),
        "model_tier": config.model_tier,
        "validation_mode": _validation_mode(row.get("validation_mode")),
        "correction_iterations": int(correction_iterations),
        "corrections_applied": list(corrections_applied or []),
        "llm_stages": _stage_models(llm_stages or []),
    }
    if bom_score is not None:
        fields["bom_match_score"] = float(bom_score)
    if netlist_score is not None:
        fields["netlist_match_score"] = float(netlist_score)

    with session(
        str(row["board_id"]),
        mode,
        run_id=run_id,
        path=config.telemetry,
        **fields,
    ) as record:
        if spec is not None:
            metrics = response.metrics if response is not None else {}
            record.geometry = extract_geometry(spec.model_dump(mode="json"), metrics)
        if response is not None:
            record.cpu_time_s = float(response.metrics.get("cpu_time_s", 0.0) or 0.0)
            record.peak_rss_mb = float(response.metrics.get("peak_rss_mb", 0.0) or 0.0)
            record.layout_score = response.metrics.get("layout_score")
            record.total_hpwl_mm = response.metrics.get("total_hpwl_mm")
            record.congestion_score = response.metrics.get("congestion_score")
            record.candidates_scored = int(response.metrics.get("candidates_scored", 0) or 0)
            record.erc_iterations = int(response.metrics.get("erc_iterations", 0) or 0)
            record.schematic_retries = int(response.metrics.get("schematic_retries", 0) or 0)
            record.exceptions_raised = [exc.code.value for exc in response.exceptions]
            record.decisions_remaining, record.decision_breakdown = _count_decisions(
                response.exceptions
            )
        record.status = status
        record.failure_reason = failure_reason or _failure_from_response(response)
        if wall_time_s is not None:
            record.wall_time_s = wall_time_s
    return run_id


async def _spec_for_row(
    row: dict,
    requested_mode: str,
    config: RunnerConfig,
    spend_tracker: SpendTracker,
) -> tuple[CircuitSpec | None, str, list[dict], str | None, str]:
    """Return (spec, actual_mode, stages, failure_reason, status_if_terminal)."""
    if requested_mode == "engine_only":
        spec = load_cached_spec(row)
        if spec is None:
            return None, "engine_only", [], "no cached spec_path for engine-only run", "failed"
        return spec, "engine_only", [], None, ""

    if not os.environ.get("OPENROUTER_API_KEY"):
        spec = load_cached_spec(row)
        if spec is None:
            return None, "engine_only", [], "OPENROUTER_API_KEY unset and no cached spec_path", "failed"
        return spec, "engine_only", [], None, ""

    if row.get("nl_source") == "reversed":
        spec = load_cached_spec(row)
        if spec is None:
            return None, "engine_only", [], "reversed board has no cached spec", "failed"
        return spec, requested_mode, [], None, ""

    stages: list[dict] = []
    try:
        spec, stages = await nl_to_input_spec(
            str(row.get("description", "")),
            str(row["board_id"]),
            spend_tracker=spend_tracker,
        )
    except BudgetExhausted as exc:
        spec = load_cached_spec(row)
        if spec is None:
            return None, requested_mode, stages, str(exc), "skipped_budget"
        return spec, "engine_only", stages, None, ""
    except SpecParseError as exc:
        return None, requested_mode, exc.stages, str(exc), "failed"
    except LLMUnavailable as exc:
        spec = load_cached_spec(row)
        if spec is None:
            return None, "engine_only", stages, str(exc), "failed"
        return spec, "engine_only", stages, None, ""

    if _token_count(stages) > config.max_board_tokens:
        return spec, requested_mode, stages, "board token cap exceeded", "cap_exceeded"
    return spec, requested_mode, stages, None, ""


async def _review_choices(
    *,
    mode: str,
    row: dict,
    spec: CircuitSpec,
    exceptions: list[DesignException],
    history: list[str],
    spend_tracker: SpendTracker,
) -> tuple[list[dict], list[dict], str, bool]:
    if mode == "engine_only":
        return deterministic_choices(exceptions), [], mode, False

    board_context = {
        "board_id": row["board_id"],
        "tier": row.get("tier"),
        "source": row.get("source"),
        "difficulty_axis": row.get("difficulty_axis"),
        "validation_mode": row.get("validation_mode"),
        "part_count": len(spec.parts),
        "net_count": len(spec.nets),
    }
    try:
        if mode == "external":
            choices, stages, _fallback = await external_agent_review(
                [exc.model_dump(mode="json") for exc in exceptions],
                board_context,
                history,
                spend_tracker=spend_tracker,
            )
        else:
            choices, stages, _fallback = await review_exceptions(
                [exc.model_dump(mode="json") for exc in exceptions],
                board_context,
                history,
                spend_tracker=spend_tracker,
            )
        return choices, stages, mode, False
    except BudgetExhausted:
        return deterministic_choices(exceptions), [], "engine_only", True
    except LLMUnavailable:
        return deterministic_choices(exceptions), [], "engine_only", True


async def run_board(
    row: dict,
    config: RunnerConfig,
    client: DirectDesignClient | MCPDesignClient,
    spend_tracker: SpendTracker,
) -> BoardResult:
    import time as _time
    board_t0 = _time.monotonic()
    loop = asyncio.get_running_loop()
    board_deadline = loop.time() + config.timeout_s
    board_id = str(row["board_id"])
    spec: CircuitSpec | None = None
    response: DesignResponse | None = None
    mode = config.mode
    stages: list[dict | LLMStage] = []
    corrections: list[str] = []
    correction_iterations = 0
    failure_reason: str | None = None
    status = "failed"

    try:
        async with asyncio.timeout_at(board_deadline):
            spec, mode, stages, failure_reason, terminal_status = await _spec_for_row(
                row,
                config.mode,
                config,
                spend_tracker,
            )
            if terminal_status:
                run_id = write_final_record(
                    row=row,
                    mode=mode,
                    config=config,
                    spec=spec,
                    response=None,
                    status=terminal_status,
                    failure_reason=failure_reason,
                    llm_stages=stages,
                    wall_time_s=_time.monotonic() - board_t0,
                )
                return BoardResult(board_id, mode, terminal_status, run_id, failure_reason or "")

            parent_run_id: str | None = None
            prev_exc_signature: str | None = None
            stall_count = 0

            while True:
                remaining = board_deadline - loop.time()
                if remaining <= 0:
                    status = "timeout"
                    failure_reason = "per-board timeout reached before next engine attempt"
                    break

                run_options = {
                    "out_dir": str(config.artifacts),
                    "timeout_s": min(config.timeout_s, remaining),
                    "mode": mode,
                    "board_id": board_id,
                    "telemetry_path": str(config.telemetry),
                    "record_telemetry": False,
                    "record_fields": row,
                    "validation_mode": _validation_mode(row.get("validation_mode")),
                    "model_tier": config.model_tier,
                    "correction_iterations": correction_iterations,
                    "corrections_applied": corrections,
                    "llm_stages": [
                        stage.model_dump(mode="json") if isinstance(stage, LLMStage) else stage
                        for stage in stages
                    ],
                    "parent_run_id": parent_run_id,
                }
                response = DesignResponse.model_validate(
                    await client.generate_design(spec.model_dump(mode="json"), run_options)
                )
                parent_run_id = response.run_id

                exceptions = actionable_exceptions(response)
                if response.ok or not exceptions:
                    status = response.status
                    failure_reason = _failure_from_response(response)
                    break

                # Detect stalled correction loops (same exceptions repeating)
                exc_sig = "|".join(sorted(e.code.value for e in exceptions))
                if exc_sig == prev_exc_signature:
                    stall_count += 1
                else:
                    stall_count = 0
                prev_exc_signature = exc_sig
                if stall_count >= 3:
                    status = "failed"
                    failure_reason = f"correction loop stalled: {exc_sig} repeated {stall_count+1} times"
                    break

                if correction_iterations >= config.max_iters:
                    status = "failed"
                    failure_reason = f"correction loop hit max_iters={config.max_iters}"
                    break

                choices, review_stages, next_mode, degraded = await _review_choices(
                    mode=mode,
                    row=row,
                    spec=spec,
                    exceptions=exceptions,
                    history=corrections,
                    spend_tracker=spend_tracker,
                )
                mode = next_mode
                stages.extend(review_stages)
                if degraded:
                    corrections.append("mode_degraded:engine_only")
                if _token_count(stages) > config.max_board_tokens:
                    status = "cap_exceeded"
                    failure_reason = "board token cap exceeded"
                    break
                try:
                    spec, applied = apply_choices(spec, exceptions, choices)
                except CorrectionError as exc:
                    status = "failed"
                    failure_reason = f"correction application failed: {exc}"
                    break
                if not applied:
                    status = "failed"
                    failure_reason = "no applicable correction candidates"
                    break
                corrections.extend(applied)
                correction_iterations += 1

            bom_score, netlist_score = (
                await score_reference_async(spec, row)
                if spec is not None
                else (None, None)
            )
    except TimeoutError:
        status = "timeout"
        failure_reason = f"per-board timeout exceeded ({config.timeout_s:.1f}s)"
        bom_score, netlist_score = None, None

    if spec is None:
        run_id = write_final_record(
            row=row,
            mode=mode,
            config=config,
            spec=None,
            response=None,
            status=status,
            failure_reason=failure_reason,
            llm_stages=stages,
            correction_iterations=correction_iterations,
            corrections_applied=corrections,
            wall_time_s=_time.monotonic() - board_t0,
        )
        return BoardResult(board_id, mode, status, run_id, failure_reason or "")

    run_id = write_final_record(
        row=row,
        mode=mode,
        config=config,
        spec=spec,
        response=response,
        status=status,
        failure_reason=failure_reason,
        llm_stages=stages,
        correction_iterations=correction_iterations,
        corrections_applied=corrections,
        bom_score=bom_score,
        netlist_score=netlist_score,
        wall_time_s=_time.monotonic() - board_t0,
    )
    return BoardResult(board_id, mode, status, run_id, failure_reason or "")


def select_rows(rows: list[dict], config: RunnerConfig) -> list[dict]:
    selected = [
        row
        for row in rows
        if (not config.boards or str(row["board_id"]) in config.boards)
        and (not config.validation_modes or row.get("validation_mode") in config.validation_modes)
    ]
    if not config.force:
        done = completed_keys(config.telemetry, config.mode)
        selected = [
            row
            for row in selected
            if (str(row["board_id"]), config.mode) not in done
        ]
    if config.limit is not None:
        selected = selected[: config.limit]
    return selected


def _client_for(config: RunnerConfig):
    if config.no_mcp:
        return DirectDesignClient()
    return MCPDesignClient(config)


@asynccontextmanager
async def open_client(config: RunnerConfig):
    client = _client_for(config)
    try:
        active = await client.__aenter__()
    except Exception as exc:
        if config.no_mcp:
            raise
        print(
            f"MCP client failed ({type(exc).__name__}: {exc}); falling back to direct pipeline.",
            file=sys.stderr,
            flush=True,
        )
        client = DirectDesignClient()
        active = await client.__aenter__()

    try:
        yield active
    except BaseException as exc:
        await client.__aexit__(type(exc), exc, exc.__traceback__)
        raise
    else:
        await client.__aexit__(None, None, None)


async def run_manifest(config: RunnerConfig) -> list[BoardResult]:
    rows = select_rows(load_manifest(config.manifest), config)
    config.artifacts.mkdir(parents=True, exist_ok=True)
    config.telemetry.parent.mkdir(parents=True, exist_ok=True)
    config.spend_log.parent.mkdir(parents=True, exist_ok=True)

    queue = deque(rows)
    lock = asyncio.Lock()
    results: list[BoardResult] = []
    worker_failures: list[BoardResult] = []
    spend_tracker = SpendTracker(config.max_total_spend_usd, str(config.spend_log))
    stop_after_s = max(0.0, config.max_runtime_hours * 3600.0 - 15.0 * 60.0)
    stop_at = time.monotonic() + stop_after_s

    async def next_row() -> dict | None:
        async with lock:
            if time.monotonic() >= stop_at:
                return None
            if not queue:
                return None
            return queue.popleft()

    async def worker(worker_id: int) -> None:
        try:
            async with open_client(config) as client:
                while True:
                    row = await next_row()
                    if row is None:
                        return
                    board_id = row["board_id"]
                    print(
                        f"[worker {worker_id}] start {board_id} mode={config.mode}",
                        flush=True,
                    )
                    try:
                        result = await run_board(row, config, client, spend_tracker)
                    except Exception as exc:
                        run_id = write_final_record(
                            row=row,
                            mode=config.mode,
                            config=config,
                            spec=None,
                            response=None,
                            status="crashed",
                            failure_reason=f"{type(exc).__name__}: {exc}",
                        )
                        result = BoardResult(
                            str(board_id),
                            config.mode,
                            "crashed",
                            run_id,
                            f"{type(exc).__name__}: {exc}",
                        )
                    results.append(result)
                    print(
                        f"[worker {worker_id}] done  {board_id} "
                        f"mode={result.mode} status={result.status} run={result.run_id}",
                        flush=True,
                    )
        except Exception as exc:
            worker_failures.append(
                BoardResult(
                    f"worker-{worker_id}",
                    config.mode,
                    "crashed",
                    failure_reason=f"{type(exc).__name__}: {exc}",
                )
            )
            print(
                f"[worker {worker_id}] client failed: {type(exc).__name__}: {exc}",
                file=sys.stderr,
                flush=True,
            )

    if not rows:
        print("No manifest rows to run after filters/checkpoint.", flush=True)
        return []

    print(
        f"Running {len(rows)} board(s), mode={config.mode}, "
        f"concurrency={config.concurrency}, mcp={not config.no_mcp}",
        flush=True,
    )
    workers = [
        asyncio.create_task(worker(i + 1))
        for i in range(max(1, int(config.concurrency)))
    ]
    await asyncio.gather(*workers)
    results.extend(worker_failures)
    if queue:
        skipped = len(queue)
        print(
            f"Stopped before starting {skipped} board(s): runtime reserve reached.",
            flush=True,
        )
    return results


def write_pid_file(path: str | Path) -> None:
    path = _path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{os.getpid()}\n")


def parse_args(argv: list[str] | None = None) -> RunnerConfig:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--artifacts", type=Path, default=DEFAULT_ARTIFACTS)
    parser.add_argument("--telemetry", type=Path, default=DEFAULT_TELEMETRY)
    parser.add_argument("--spend-log", type=Path, default=DEFAULT_SPEND_LOG)
    parser.add_argument("--pid-file", type=Path, default=DEFAULT_PID_FILE)
    parser.add_argument("--mode", choices=["engine_only", "internal", "external"], default="engine_only")
    parser.add_argument("--model-tier", default="mid")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--board", action="append", default=[])
    parser.add_argument("--validation-mode", action="append", default=[],
                        help="Filter by validation_mode (internal, reference, indexed_only)")
    parser.add_argument("--timeout-s", type=float, default=1200.0)
    parser.add_argument("--max-iters", type=int, default=8)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--max-runtime-hours",
        type=float,
        default=float(os.environ.get("MAX_RUNTIME_HOURS", "8")),
    )
    parser.add_argument(
        "--max-total-spend-usd",
        type=float,
        default=float(os.environ.get("MAX_TOTAL_SPEND_USD", "10")),
    )
    parser.add_argument("--max-board-tokens", type=int, default=200_000)
    parser.add_argument("--no-mcp", action="store_true", help="Use direct run_pipeline instead of MCP stdio")
    parser.add_argument("--force", action="store_true", help="Ignore checkpointed terminal rows")
    args = parser.parse_args(argv)
    return RunnerConfig(
        manifest=_path(args.manifest),
        artifacts=_path(args.artifacts),
        telemetry=_path(args.telemetry),
        spend_log=_path(args.spend_log),
        pid_file=_path(args.pid_file),
        mode=args.mode,
        model_tier=args.model_tier,
        limit=args.limit,
        boards=set(args.board),
        validation_modes=set(args.validation_mode),
        timeout_s=args.timeout_s,
        max_iters=args.max_iters,
        concurrency=max(1, args.concurrency),
        max_runtime_hours=args.max_runtime_hours,
        max_total_spend_usd=args.max_total_spend_usd,
        max_board_tokens=args.max_board_tokens,
        no_mcp=args.no_mcp,
        force=args.force,
    )


def main(argv: list[str] | None = None) -> int:
    config = parse_args(argv)
    write_pid_file(config.pid_file)
    results = asyncio.run(run_manifest(config))
    failed = [result for result in results if result.status not in {"succeeded", "succeeded_with_warnings"}]
    print(
        f"Finished {len(results)} board(s): {len(results) - len(failed)} ok, {len(failed)} not-ok. "
        f"telemetry={config.telemetry}",
        flush=True,
    )
    return 1 if any(result.status == "crashed" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
