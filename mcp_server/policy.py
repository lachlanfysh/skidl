"""Agent-facing generation policy for MCP design runs.

The policy layer decides which fixes are safe enough for the server to apply
without another agent/user turn, and which failures should be surfaced as a
decision at the MCP boundary.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator

from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity


AutoApply = Literal["none", "advisory_only", "safe"]


class GeneratePolicy(BaseModel):
    """Policy for how much correction work generate_design may do internally."""

    auto_apply: AutoApply = Field(
        default="none",
        description="none | advisory_only | safe",
    )
    max_internal_corrections: int = Field(
        default=0,
        ge=0,
        le=8,
        description="Maximum correction iterations generate_design may perform before returning.",
    )
    stop_for: list[str] = Field(
        default_factory=lambda: [
            "mechanical_constraint",
            "bom_substitution",
            "unknown_pinout",
            "manufacturing_order",
            "high_cost_change",
        ],
        description="Decision kinds that should always be returned to the agent.",
    )
    require_user_for_manufacture: bool = Field(
        default=True,
        description="Manufacturing/ordering tools must stop for explicit user approval.",
    )

    @field_validator("stop_for")
    @classmethod
    def _dedupe_stop_for(cls, values: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for value in values:
            if value not in seen:
                out.append(value)
                seen.add(value)
        return out


def normalize_policy(policy: dict | GeneratePolicy | None) -> GeneratePolicy:
    if isinstance(policy, GeneratePolicy):
        return policy
    return GeneratePolicy.model_validate(policy or {})


def _choice_key(exc: DesignException, cand: Candidate) -> str:
    return f"{exc.id}:{cand.id}:{cand.action.value}"


def _candidate_allowed(
    exc: DesignException,
    cand: Candidate,
    policy: GeneratePolicy,
) -> bool:
    if policy.auto_apply == "none":
        return False
    if policy.auto_apply == "advisory_only":
        return exc.severity == Severity.ADVISORY and cand.action == ActionType.ACCEPT_ADVISORY
    if cand.action == ActionType.ACCEPT_ADVISORY:
        return exc.severity == Severity.ADVISORY
    if cand.action == ActionType.REGENERATE:
        return exc.code in {
            ExcCode.SCH_PLACEMENT_FAILURE,
            ExcCode.SCH_ROUTING_FAILURE,
            ExcCode.LAYOUT_KEEPOUT,
            ExcCode.LAYOUT_MISSING_REF,
            ExcCode.ENGINE_CRASH,
        }
    return False


def auto_corrections(
    exceptions: list[DesignException],
    policy: GeneratePolicy,
    history: set[str] | None = None,
) -> list[dict]:
    """Return correction choices only if every current exception is auto-fixable."""

    history = history or set()
    choices: list[dict] = []
    for exc in exceptions:
        pick = None
        for cand in exc.candidates:
            key = _choice_key(exc, cand)
            if key in history:
                continue
            if _candidate_allowed(exc, cand, policy):
                pick = cand
                break
        if pick is None:
            return []
        choices.append({"exception_id": exc.id, "candidate_id": pick.id})
    return choices


def correction_history_keys(
    exceptions: list[DesignException],
    choices: list[dict],
) -> list[str]:
    by_exc = {exc.id: exc for exc in exceptions}
    keys: list[str] = []
    for choice in choices:
        exc = by_exc.get(choice.get("exception_id"))
        if exc is None:
            continue
        cand = next((c for c in exc.candidates if c.id == choice.get("candidate_id")), None)
        if cand is not None:
            keys.append(_choice_key(exc, cand))
    return keys


def decision_kind(exceptions: list[DesignException]) -> str:
    actions = {
        cand.action
        for exc in exceptions
        for cand in exc.candidates
    }
    codes = {exc.code for exc in exceptions}
    if not exceptions:
        return ""
    if any(not exc.candidates for exc in exceptions):
        return "no_candidate"
    if actions & {ActionType.SET_FORM_FACTOR, ActionType.SET_OUTLINE, ActionType.SCALE_OUTLINE}:
        return "mechanical_constraint"
    if actions & {ActionType.REPLACE_LIB, ActionType.REPLACE_PART, ActionType.REPLACE_FOOTPRINT}:
        return "bom_substitution"
    if actions & {ActionType.REPLACE_PIN, ActionType.REMOVE_NET_PIN, ActionType.STUB_NET}:
        return "unknown_pinout"
    if codes & {ExcCode.ENGINE_CRASH, ExcCode.ENGINE_TIMEOUT}:
        return "engine_failure"
    if all(exc.severity == Severity.ADVISORY for exc in exceptions):
        return "quality_advisory"
    return "correction_choice"
