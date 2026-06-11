"""Pre-run complexity estimator — predicts decisions, cost, and success probability.

Given a CircuitSpec, runs static validation (no engine) to count how many
human/LLM decisions the board will need. Returns a ComplexityEstimate that
the MCP server surfaces before committing to a full run.

Fast (<2s) — no engine work, no heavy API calls.
"""

from __future__ import annotations

import math
import os
from typing import Optional

from pydantic import BaseModel, Field

from .circuit_spec import CircuitSpec
from .exceptions import ExcCode, Severity
from .translator import (
    DEFAULT_FP_DIR,
    DEFAULT_SYM_DIR,
    TranslationResult,
    _footprint_exists,
    remap_footprints,
    translate,
)

try:
    from telemetry.features import extract_geometry
    from telemetry.models import GeometryFeatures
except ImportError:
    extract_geometry = None
    GeometryFeatures = None


class DecisionPrediction(BaseModel):
    footprint: int = 0
    pin: int = 0
    library: int = 0
    part: int = 0
    net_ref: int = 0

    @property
    def total(self) -> int:
        return self.footprint + self.pin + self.library + self.part + self.net_ref


class RuntimePrediction(BaseModel):
    layout_issues_expected: int = 0
    timeout_probability: float = 0.0
    success_probability: float = 0.0
    confidence: str = "statistical"
    basis_run_count: int = 0


class CostRange(BaseModel):
    min_usd: float = 0.0
    max_usd: float = 0.0
    expected_usd: float = 0.0


class TimeRange(BaseModel):
    min_s: float = 0.0
    max_s: float = 0.0
    expected_s: float = 0.0


class ComplexityEstimate(BaseModel):
    decisions_predicted: DecisionPrediction
    auto_fixable: int = Field(default=0, description="High-confidence candidates (>=0.8) that auto-apply")
    needs_review: int = Field(default=0, description="Low-confidence candidates (<0.8) needing LLM/human review")
    runtime_prediction: RuntimePrediction
    estimated_cost: CostRange
    estimated_wall_time: TimeRange
    complexity_tier: str = Field(description="simple | moderate | complex | ambitious")
    geometry: Optional[dict] = None
    warnings: list[str] = Field(default_factory=list)
    remapped_footprints: dict[str, str] = Field(default_factory=dict)


# From telemetry/regression.py run on telemetry/runs_a.jsonl (163 records, 2026-06-11)
_COEFFICIENTS = {
    "timeout_logistic": {"w": 0.027, "b": -2.63},
    "cpu_time_linear": {"slope": 0.5, "intercept": 3.0},
    "success_rate": {
        "small": {"rate": 0.57, "total": 42},
        "medium": {"rate": 0.06, "total": 17},
        "large": {"rate": 0.03, "total": 39},
        "xlarge": {"rate": 0.02, "total": 65},
    },
}

_EXC_CODE_TO_DECISION = {
    ExcCode.SPEC_BAD_FOOTPRINT: "footprint",
    ExcCode.SPEC_UNKNOWN_PIN: "pin",
    ExcCode.SPEC_UNKNOWN_PART: "part",
    ExcCode.SPEC_UNKNOWN_LIB: "library",
    ExcCode.SPEC_MALFORMED: "net_ref",
}


def _sigmoid(x: float) -> float:
    x = max(-500, min(500, x))
    return 1.0 / (1.0 + math.exp(-x))


def _geometry_bucket(component_count: int) -> str:
    if component_count < 10:
        return "small"
    elif component_count < 25:
        return "medium"
    elif component_count < 50:
        return "large"
    return "xlarge"


def _count_from_exceptions(result: TranslationResult) -> tuple[DecisionPrediction, int, int]:
    """Count decisions, auto_fixable, and needs_review from translation exceptions."""
    pred = DecisionPrediction()
    auto_fixable = 0
    needs_review = 0

    for exc in result.exceptions:
        if exc.severity == Severity.ADVISORY:
            continue
        dtype = _EXC_CODE_TO_DECISION.get(exc.code)
        if dtype:
            setattr(pred, dtype, getattr(pred, dtype) + 1)

        for cand in exc.candidates:
            if cand.confidence >= 0.8:
                auto_fixable += 1
                break
            else:
                needs_review += 1
                break

    return pred, auto_fixable, needs_review


def estimate_complexity(
    spec: CircuitSpec,
    sym_dir: str | None = None,
    fp_dirs: list[str] | None = None,
    include_jlc: bool = True,
) -> ComplexityEstimate:
    """Estimate design complexity before committing to a full engine run.

    Runs static validation passes (no engine) to predict:
    - How many decisions the board will need
    - Which can auto-fix vs need human/LLM review
    - Estimated cost, time, and success probability
    """
    sym_dir = sym_dir or os.environ.get("KICAD9_SYMBOL_DIR", DEFAULT_SYM_DIR)
    fp_dirs = fp_dirs or [os.environ.get("KICAD9_FOOTPRINT_DIR", DEFAULT_FP_DIR)]

    spec_copy = spec.model_copy(deep=True)

    # Extract geometry pre-run
    geometry_dict = None
    if extract_geometry is not None:
        geo = extract_geometry(spec_copy.model_dump(mode="json"), {})
        geometry_dict = geo.model_dump()
    component_count = len(spec_copy.parts)
    pin_count = sum(len(n.pins) for n in spec_copy.nets)

    # Run remap pass on the copy
    remapped = remap_footprints(spec_copy, fp_dirs)

    # Run translate() — it short-circuits at the first failing pass, which
    # tells us the FIRST category of decisions needed
    result = translate(spec_copy, sym_dir, fp_dirs)
    decisions, auto_fixable, needs_review = _count_from_exceptions(result)

    # If translation succeeded (all passes clean), decisions are 0
    # If it failed, the exceptions tell us what's needed

    # Predict runtime issues from geometry
    bucket = _geometry_bucket(component_count)
    rates = _COEFFICIENTS["success_rate"].get(bucket, {"rate": 0.0, "total": 0})
    timeout_coeff = _COEFFICIENTS["timeout_logistic"]
    timeout_prob = _sigmoid(timeout_coeff["w"] * component_count + timeout_coeff["b"])

    # Layout issues: rough estimate from telemetry — boards that reach layout
    # have ~15% layout issue rate regardless of size (data is sparse)
    layout_expected = 1 if component_count > 20 else 0

    runtime = RuntimePrediction(
        layout_issues_expected=layout_expected,
        timeout_probability=round(timeout_prob, 3),
        success_probability=round(rates["rate"], 3),
        confidence="statistical" if rates["total"] >= 10 else "insufficient_data",
        basis_run_count=rates["total"],
    )

    # Estimate cost and time
    cpu_coeff = _COEFFICIENTS["cpu_time_linear"]
    est_cpu = max(1.0, cpu_coeff["slope"] * component_count + cpu_coeff["intercept"])
    iters_estimate = max(1, decisions.total)
    est_wall = est_cpu * iters_estimate

    # LLM cost: ~$0.001 per review decision (mid-tier), ~$0.01 per decision (frontier)
    review_cost = needs_review * 0.003
    estimated_cost = CostRange(
        min_usd=0.0,
        max_usd=round(review_cost * 3, 4),
        expected_usd=round(review_cost, 4),
    )
    estimated_time = TimeRange(
        min_s=round(est_cpu, 1),
        max_s=round(est_wall * 2, 1),
        expected_s=round(est_wall, 1),
    )

    # Classify tier
    total_decisions = decisions.total + layout_expected
    if total_decisions == 0 and component_count < 15:
        tier = "simple"
    elif total_decisions <= 3 and component_count < 30:
        tier = "moderate"
    elif total_decisions <= 10 and component_count < 60:
        tier = "complex"
    else:
        tier = "ambitious"

    # Generate warnings
    warnings = _generate_warnings(
        decisions, auto_fixable, needs_review, component_count, pin_count,
        remapped, rates, timeout_prob,
    )

    return ComplexityEstimate(
        decisions_predicted=decisions,
        auto_fixable=auto_fixable,
        needs_review=needs_review,
        runtime_prediction=runtime,
        estimated_cost=estimated_cost,
        estimated_wall_time=estimated_time,
        complexity_tier=tier,
        geometry=geometry_dict,
        warnings=warnings,
        remapped_footprints=remapped,
    )


def _generate_warnings(
    decisions: DecisionPrediction,
    auto_fixable: int,
    needs_review: int,
    component_count: int,
    pin_count: int,
    remapped: dict[str, str],
    success_rates: dict,
    timeout_prob: float,
) -> list[str]:
    warnings: list[str] = []

    if remapped:
        warnings.append(f"{len(remapped)} footprint(s) auto-remapped to standard KiCad equivalents")

    if decisions.footprint > 0:
        if auto_fixable > 0:
            warnings.append(
                f"{decisions.footprint} missing footprint(s) — {auto_fixable} can auto-fix, "
                f"{needs_review} need review"
            )
        else:
            warnings.append(f"{decisions.footprint} missing footprint(s) need manual selection")

    if decisions.pin > 0:
        warnings.append(f"{decisions.pin} pin mismatch(es) detected — review required")

    if decisions.library > 0:
        warnings.append(f"{decisions.library} unknown symbol library/libraries")

    if decisions.part > 0:
        warnings.append(f"{decisions.part} unknown symbol(s) within their libraries")

    rate = success_rates.get("rate", 0)
    total = success_rates.get("total", 0)
    if total >= 10 and rate < 0.2:
        warnings.append(
            f"Boards with {component_count} components had {rate:.0%} engine-only success "
            f"rate in telemetry (n={total}) — LLM correction loop recommended"
        )

    if timeout_prob > 0.3:
        warnings.append(
            f"{timeout_prob:.0%} probability of exceeding default timeout — "
            f"consider allowing extra time"
        )

    return warnings
