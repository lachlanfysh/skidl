"""Telemetry models for the overnight PCB-engine product layer.

Every engine run — internal benchmark, external customer job, or bare
engine-only invocation — produces exactly one RunRecord appended to a
JSONL store. These models are the single source of truth for run-level
analytics: cost, latency, geometry complexity, failure taxonomy.

Pure stdlib + pydantic. No transport- or host-specific imports.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

# The status a RunRecord is born with. session() uses this to detect
# whether the body ever set an explicit outcome before it crashed.
DEFAULT_STATUS = "succeeded"

Mode = Literal["internal", "external", "engine_only"]
Status = Literal[
    "succeeded",
    "succeeded_with_warnings",
    "failed",
    "crashed",
    "timeout",
    "cap_exceeded",
    "skipped_budget",
    "skipped_time",
]
ValidationMode = Literal["internal", "reference", "none"]


class LLMStage(BaseModel):
    """One LLM call within a run (design translation, review, etc.)."""

    stage: str = Field(
        description='Pipeline stage, e.g. "design_nl_to_input" | "review_internal" | "review_external"'
    )
    model: str = Field(description="Model identifier used for this stage")
    tokens_in: int = Field(default=0, description="Prompt tokens")
    tokens_out: int = Field(default=0, description="Completion tokens")
    latency_s: float = Field(default=0.0, description="Wall-clock latency of the call")
    cost_usd: float = Field(default=0.0, description="Cost of this call in USD")


class GeometryFeatures(BaseModel):
    """Board complexity features extracted from the spec and worker metrics."""

    component_count: int = 0
    net_count: int = 0
    pin_count: int = 0
    pad_count: int = 0
    layer_count: int = 0
    board_area_mm2: float = 0.0
    pad_density_per_cm2: float = 0.0


class RunRecord(BaseModel):
    """One engine run, end to end. Appended as a single JSONL line."""

    run_id: str = Field(description="Unique id for this run (uuid4 hex prefix)")
    parent_run_id: Optional[str] = Field(
        default=None, description="Run this one was retried/forked from"
    )
    board_id: str = Field(description="Stable id of the board/design under test")
    git_sha: str = Field(default="", description="Short git SHA of the engine tree")
    started_at: str = Field(description="ISO-8601 start timestamp")
    finished_at: Optional[str] = Field(default=None, description="ISO-8601 end timestamp")
    wall_time_s: float = Field(default=0.0, description="finished_at - started_at, seconds")

    tier: int = Field(default=0, description="Benchmark difficulty tier")
    source: str = Field(default="", description="Where the design came from (benchmark suite, customer, ...)")
    difficulty_axis: str = Field(default="", description="Which complexity axis this run stresses")
    nl_source: str = Field(default="", description="Provenance of the natural-language input")
    mode: Mode = Field(description="internal | external | engine_only")
    model_tier: str = Field(default="mid", description="LLM capability tier used for this run")

    geometry: Optional[GeometryFeatures] = Field(
        default=None, description="Board complexity features, if extracted"
    )

    correction_iterations: int = 0
    candidates_scored: int = 0
    erc_iterations: int = 0
    schematic_retries: int = 0
    exceptions_raised: list[str] = Field(
        default_factory=list, description="DesignException codes raised during the run"
    )
    corrections_applied: list[str] = Field(
        default_factory=list, description="Correction action types applied"
    )

    llm_stages: list[LLMStage] = Field(default_factory=list)
    total_cost_usd: float = Field(default=0.0, description="Sum of llm_stages cost_usd")
    total_tokens: int = Field(default=0, description="Sum of llm_stages tokens_in + tokens_out")
    cpu_time_s: float = 0.0
    peak_rss_mb: float = 0.0

    status: Status = Field(
        default=DEFAULT_STATUS,
        description="Run outcome; session() overwrites the default with 'crashed' on escape",
    )
    validation_mode: ValidationMode = Field(
        default="none", description="internal | reference | none"
    )
    layout_score: Optional[float] = None
    total_hpwl_mm: Optional[float] = None
    congestion_score: Optional[float] = None
    bom_match_score: Optional[float] = None
    netlist_match_score: Optional[float] = None
    failure_reason: Optional[str] = None

    decisions_remaining: int = Field(
        default=0,
        description="Count of unresolved exceptions needing human/LLM decision. 0 = done.",
    )
    decision_breakdown: dict[str, int] = Field(
        default_factory=dict,
        description="Unresolved decisions by type: {footprint: N, pin: N, layout: N, ...}",
    )

    def finalize(self) -> "RunRecord":
        """Compute derived totals before the record is persisted.

        - total_cost_usd / total_tokens from llm_stages
        - wall_time_s from started_at -> finished_at (left untouched if
          either timestamp is missing or unparsable)
        """
        self.total_cost_usd = sum(s.cost_usd for s in self.llm_stages)
        self.total_tokens = sum(s.tokens_in + s.tokens_out for s in self.llm_stages)
        if self.wall_time_s == 0.0 and self.started_at and self.finished_at:
            try:
                t0 = datetime.fromisoformat(self.started_at)
                t1 = datetime.fromisoformat(self.finished_at)
                self.wall_time_s = (t1 - t0).total_seconds()
            except (ValueError, TypeError):
                pass
        return self
