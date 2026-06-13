"""Map engine outcomes into structured DesignException objects."""

from __future__ import annotations

from typing import Iterable

from schemas.circuit_spec import CircuitSpec
from schemas.exceptions import ActionType, Candidate, DesignException, ExcCode, Severity


def suppress_waived(
    exceptions: Iterable[DesignException],
    spec: CircuitSpec,
) -> list[DesignException]:
    """Drop advisory exceptions whose stable waiver key is in the spec."""

    waived = set(spec.waivers or [])
    return [
        exc
        for exc in exceptions
        if not (exc.severity == Severity.ADVISORY and exc.waiver_key() in waived)
    ]


def _candidate(
    cid: str,
    action: ActionType,
    params: dict,
    summary: str,
    cost_hint: str = "free",
) -> Candidate:
    return Candidate(
        id=cid,
        action=action,
        params=params,
        human_summary=summary,
        cost_hint=cost_hint,
    )


def timeout_exception(timeout_s: float) -> DesignException:
    return DesignException(
        id="e-timeout",
        code=ExcCode.ENGINE_TIMEOUT,
        severity=Severity.FATAL,
        message=f"engine worker exceeded timeout ({timeout_s:.1f}s)",
        subject={"timeout_s": timeout_s},
        candidates=[
            _candidate(
                "c1",
                ActionType.REGENERATE,
                {},
                "retry unchanged with a larger timeout or after reducing complexity",
                "cheap",
            )
        ],
    )


def crash_exception(
    message: str,
    stderr: str = "",
    *,
    stage: str = "",
    artifact_keys: list[str] | None = None,
) -> DesignException:
    text = f"{message}\n{stderr}"
    if "TerminalClashException" in text:
        subject = {
            "message": message,
            "stage": "schematic_routing",
            "exception": "TerminalClashException",
        }
        if stderr:
            subject["stderr_tail"] = stderr[-4000:]
        if artifact_keys:
            subject["partial_artifacts"] = sorted(artifact_keys)
        return DesignException(
            id="e-sch-route",
            code=ExcCode.SCH_ROUTING_FAILURE,
            severity=Severity.FATAL,
            message=(
                "schematic auto-router failed while rendering the circuit "
                "(TerminalClashException)"
            ),
            subject=subject,
            candidates=[
                _candidate(
                    "c1",
                    ActionType.REGENERATE,
                    {},
                    "retry unchanged; this is a schematic rendering failure, not PCB layout feedback",
                    "cheap",
                )
            ],
            retry_hint=(
                "Retry once unchanged. If it repeats, treat it as a schematic "
                "renderer limitation; preserve the circuit and report the "
                "stderr_tail rather than rewriting unrelated circuitry."
            ),
        )

    if stage == "after_pcb_write" and artifact_keys:
        subject = {
            "message": message,
            "stage": stage,
            "partial_artifacts": sorted(artifact_keys),
        }
        if stderr:
            subject["stderr_tail"] = stderr[-4000:]
        return DesignException(
            id="e-post-artifact-failure",
            code=ExcCode.POST_ARTIFACT_FAILURE,
            severity=Severity.ERROR,
            message=(
                f"{message}; KiCad artifacts were produced before backend "
                "finalization failed"
            ),
            subject=subject,
            candidates=[
                _candidate(
                    "c1",
                    ActionType.REGENERATE,
                    {},
                    "retry unchanged if the fetched KiCad artifacts are unusable",
                    "cheap",
                )
            ],
            retry_hint=(
                "Fetch the run artifacts and inspect the generated schematic/PCB "
                "before changing the circuit. If the files are usable, treat this "
                "as a backend finalization issue rather than circuit feedback."
            ),
        )

    subject = {"message": message}
    if stderr:
        subject["stderr_tail"] = stderr[-4000:]
    if stage:
        subject["stage"] = stage
    if artifact_keys:
        subject["partial_artifacts"] = sorted(artifact_keys)
    return DesignException(
        id="e-crash",
        code=ExcCode.ENGINE_CRASH,
        severity=Severity.FATAL,
        message=message,
        subject=subject,
        candidates=[
            _candidate(
                "c1",
                ActionType.REGENERATE,
                {},
                "retry unchanged; this is a service/backend failure, not circuit feedback",
                "cheap",
            )
        ],
        retry_hint=(
            "Retry once unchanged. If it repeats, treat it as a backend issue; "
            "inspect stderr_tail and any partial_artifacts rather than rewriting "
            "the circuit."
        ),
    )


def spec_malformed_exception(message: str) -> DesignException:
    return DesignException(
        id="e-spec",
        code=ExcCode.SPEC_MALFORMED,
        severity=Severity.FATAL,
        message=message,
        subject={},
        candidates=[],
        retry_hint="submit a complete CircuitSpec JSON object",
    )


def layout_exceptions(layout_result) -> list[DesignException]:
    """Convert a layout result's validation/score signals into exceptions."""

    out: list[DesignException] = []
    validation = getattr(layout_result, "validation", None)
    score = getattr(layout_result, "score", None)
    outline = getattr(layout_result, "outline", None)

    if validation is not None:
        overlaps = getattr(validation, "overlaps", []) or []
        if overlaps:
            pairs = [list(pair) for pair in overlaps]
            n = len(pairs)
            # Scale factor proportional to how many overlaps — more overlaps = bigger jump
            factor = min(1.25 + 0.05 * (n - 1), 2.0)
            scale_params: dict = {"area_factor": factor}
            if outline is not None:
                scale_params["base_w_mm"] = getattr(outline, "width_mm", 50.0)
                scale_params["base_h_mm"] = getattr(outline, "height_mm", 50.0)
            out.append(
                DesignException(
                    id="e-layout-overlap",
                    code=ExcCode.LAYOUT_OVERLAP,
                    severity=Severity.ERROR,
                    message=f"{n} placement overlap(s): {', '.join(f'{p[0]}/{p[1]}' for p in pairs[:5])}"
                            + (f" and {n-5} more" if n > 5 else ""),
                    subject={"pairs": pairs, "count": n},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.SCALE_OUTLINE,
                            scale_params,
                            f"increase board area by {int((factor-1)*100)}% ({n} overlaps) and re-run",
                            "cheap",
                        ),
                        _candidate(
                            "c2",
                            ActionType.REGENERATE,
                            {},
                            "retry placement unchanged",
                            "free",
                        ),
                    ],
                )
            )

        outline_viols = getattr(validation, "outline_violations", []) or []
        if outline_viols:
            n = len(outline_viols)
            factor = min(1.25 + 0.05 * (n - 1), 2.0)
            params = {"area_factor": factor}
            if outline is not None:
                params.update(
                    {
                        "base_w_mm": getattr(outline, "width_mm", 50.0),
                        "base_h_mm": getattr(outline, "height_mm", 50.0),
                    }
                )
            out.append(
                DesignException(
                    id="e-layout-outline",
                    code=ExcCode.LAYOUT_OUTLINE_VIOLATION,
                    severity=Severity.ERROR,
                    message=f"{n} part(s) outside board outline: {', '.join(outline_viols[:5])}"
                            + (f" and {n-5} more" if n > 5 else ""),
                    subject={"refs": outline_viols, "count": n},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.SCALE_OUTLINE,
                            params,
                            f"grow board outline ({n} violations) and re-run",
                            "cheap",
                        ),
                        _candidate(
                            "c2",
                            ActionType.REGENERATE,
                            {},
                            "retry placement unchanged",
                            "free",
                        ),
                    ],
                )
            )

        for idx, ref in enumerate(
            getattr(validation, "keepout_violations", []) or [], start=1
        ):
            out.append(
                DesignException(
                    id=f"e-layout-keepout-{idx}",
                    code=ExcCode.LAYOUT_KEEPOUT,
                    severity=Severity.ERROR,
                    message=f"{ref} violates a keepout region",
                    subject={"ref": ref},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.REGENERATE,
                            {},
                            "retry placement unchanged",
                            "free",
                        )
                    ],
                )
            )

        for idx, ref in enumerate(getattr(validation, "missing_refs", []) or [], start=1):
            out.append(
                DesignException(
                    id=f"e-layout-missing-{idx}",
                    code=ExcCode.LAYOUT_MISSING_REF,
                    severity=Severity.ERROR,
                    message=f"{ref} was not placed on the PCB",
                    subject={"ref": ref},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.REGENERATE,
                            {},
                            "retry layout generation unchanged",
                            "free",
                        ),
                        _candidate(
                            "c2",
                            ActionType.REMOVE_PART,
                            {"ref": ref},
                            f"remove {ref} and all of its net endpoints",
                            "expensive",
                        ),
                    ],
                )
            )

    if score is not None:
        congestion = float(getattr(score, "congestion_score", 0.0) or 0.0)
        if congestion >= 40.0:
            out.append(
                DesignException(
                    id="e-high-congestion",
                    code=ExcCode.HIGH_CONGESTION,
                    severity=Severity.ADVISORY,
                    message=f"layout congestion is high ({congestion:.1f})",
                    subject={"congestion_score": congestion},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.ACCEPT_ADVISORY,
                            {},
                            "accept this congestion advisory for the current spec",
                        ),
                        _candidate(
                            "c2",
                            ActionType.SCALE_OUTLINE,
                            {"area_factor": 1.2},
                            "increase board area by 20% and re-run placement",
                            "cheap",
                        ),
                    ],
                )
            )

        for idx, warning in enumerate(getattr(score, "warnings", []) or [], start=1):
            if "power" not in warning.lower() and "decoupling" not in warning.lower():
                continue
            out.append(
                DesignException(
                    id=f"e-long-power-net-{idx}",
                    code=ExcCode.LONG_POWER_NET,
                    severity=Severity.ADVISORY,
                    message=warning,
                    subject={"warning": warning},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.ACCEPT_ADVISORY,
                            {},
                            "accept this power/layout advisory for the current spec",
                        )
                    ],
                )
            )

    return out


def payload_exceptions(payload: dict, spec: CircuitSpec | None) -> list[DesignException]:
    """Validate/suppress exception dicts returned by the worker."""

    exceptions = [
        exc if isinstance(exc, DesignException) else DesignException.model_validate(exc)
        for exc in payload.get("exceptions", [])
    ]
    if spec is not None:
        return suppress_waived(exceptions, spec)
    return exceptions
