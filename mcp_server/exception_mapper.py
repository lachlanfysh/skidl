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


def crash_exception(message: str, stderr: str = "") -> DesignException:
    subject = {"message": message}
    if stderr:
        subject["stderr_tail"] = stderr[-4000:]
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
                "retry unchanged; if it repeats, inspect the worker stderr",
                "cheap",
            )
        ],
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
        for idx, pair in enumerate(getattr(validation, "overlaps", []) or [], start=1):
            refs = list(pair)
            out.append(
                DesignException(
                    id=f"e-layout-overlap-{idx}",
                    code=ExcCode.LAYOUT_OVERLAP,
                    severity=Severity.ERROR,
                    message=f"placed parts overlap: {refs[0]} and {refs[1]}",
                    subject={"pair": refs},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.SCALE_OUTLINE,
                            {"area_factor": 1.25},
                            "increase board area by 25% and re-run placement",
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
            getattr(validation, "outline_violations", []) or [], start=1
        ):
            params = {"area_factor": 1.25}
            if outline is not None:
                params.update(
                    {
                        "base_w_mm": getattr(outline, "width_mm", 50.0),
                        "base_h_mm": getattr(outline, "height_mm", 50.0),
                    }
                )
            out.append(
                DesignException(
                    id=f"e-layout-outline-{idx}",
                    code=ExcCode.LAYOUT_OUTLINE_VIOLATION,
                    severity=Severity.ERROR,
                    message=f"{ref} is outside the board outline",
                    subject={"ref": ref},
                    candidates=[
                        _candidate(
                            "c1",
                            ActionType.SCALE_OUTLINE,
                            params,
                            "grow the board outline and re-run placement",
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


def payload_exceptions(payload: dict, spec: CircuitSpec) -> list[DesignException]:
    """Validate/suppress exception dicts returned by the worker."""

    exceptions = [
        exc if isinstance(exc, DesignException) else DesignException.model_validate(exc)
        for exc in payload.get("exceptions", [])
    ]
    return suppress_waived(exceptions, spec)
