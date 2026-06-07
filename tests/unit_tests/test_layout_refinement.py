"""Tests for Phase 6: deterministic local placement refinement."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from skidl.layout.constraints import (
    BoardOutline,
    FixedPosition,
    KeepOut,
    LayoutConstraints,
)
from skidl.layout.refinement import (
    RefinementResult,
    refine_placement,
    _compatible_for_swap,
    _try_move,
    _try_rotation,
)
from skidl.layout.writer import PlacedPart

from layout_case_helpers import (
    COMMON_BBOXES,
    _Circuit,
    _Net,
    _Part,
    make_decap,
    make_ic,
    make_passive,
    make_power_nets,
)


def _simple_circuit():
    vcc, gnd = make_power_nets()
    sig = _Net("SIG")
    u1 = make_ic("U1", "MCU", "Package_QFP:LQFP-48_7x7mm_P0.5mm",
                 signal_nets=[sig], power_nets=[vcc, gnd], pins=48)
    c1 = make_decap("C1", vcc, gnd)
    r1 = make_passive("R1", "10K", "Resistor_SMD:R_0603_1608Metric", nets=[sig, vcc])
    return _Circuit([u1, c1, r1], [vcc, gnd, sig])


def test_refinement_never_worsens_score():
    placed = [
        PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
        PlacedPart("C1", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
        PlacedPart("R1", 40.0, 30.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
    ]
    outline = BoardOutline(50.0, 40.0)
    result = refine_placement(
        placed, _simple_circuit(), COMMON_BBOXES,
        outline=outline, max_iterations=3,
    )
    assert isinstance(result, RefinementResult)
    assert result.score_after >= result.score_before


def test_fixed_parts_remain_fixed():
    placed = [
        PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
        PlacedPart("C1", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
        PlacedPart("R1", 40.0, 30.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
    ]
    constraints = LayoutConstraints(
        fixed=[FixedPosition("U1", 25.0, 20.0)],
        outline=BoardOutline(50.0, 40.0),
    )
    refine_placement(
        placed, _simple_circuit(), COMMON_BBOXES,
        constraints=constraints, outline=BoardOutline(50.0, 40.0),
        max_iterations=3,
    )
    u1 = next(p for p in placed if p.ref == "U1")
    assert u1.x_mm == 25.0
    assert u1.y_mm == 20.0
    assert u1.rot_deg == 0.0


def test_output_is_deterministic():
    def _run():
        placed = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 40.0, 30.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        result = refine_placement(
            placed, _simple_circuit(), COMMON_BBOXES,
            outline=BoardOutline(50.0, 40.0), max_iterations=3,
        )
        return [(p.ref, p.x_mm, p.y_mm, p.rot_deg) for p in placed], result.score_after

    positions_a, score_a = _run()
    positions_b, score_b = _run()
    assert positions_a == positions_b
    assert score_a == score_b


def test_refinement_result_summary():
    result = RefinementResult(
        improved=True, iterations=2,
        score_before=40.0, score_after=55.0,
        moves=["C1: moved/rotated (+15.0)"],
    )
    summary = result.summary()
    assert "+15.0" in summary
    assert "40.0" in summary
    assert "55.0" in summary

    no_improve = RefinementResult(
        improved=False, iterations=3,
        score_before=50.0, score_after=50.0,
    )
    assert "no improvement" in no_improve.summary()


def test_try_move_respects_outline():
    part = PlacedPart("R1", 1.0, 1.0, 0.0, "Resistor_SMD:R_0603_1608Metric")
    outline = BoardOutline(10.0, 10.0)
    result = _try_move(part, -5.0, 0.0, COMMON_BBOXES, [], outline)
    assert result is not None
    assert result.x_mm >= outline.x_min


def test_try_move_rejects_overlap():
    part = PlacedPart("R1", 5.0, 5.0, 0.0, "Resistor_SMD:R_0603_1608Metric")
    occupied = [(6.0, 5.0, 1.7, 0.9)]
    result = _try_move(part, 1.0, 0.0, COMMON_BBOXES, occupied, None)
    assert result is None


def test_try_rotation_generates_candidates():
    part = PlacedPart("R1", 5.0, 5.0, 0.0, "Resistor_SMD:R_0603_1608Metric")
    rotated = _try_rotation(part)
    assert len(rotated) == 3
    angles = {r.rot_deg for r in rotated}
    assert angles == {90.0, 180.0, 270.0}
    for r in rotated:
        assert r.x_mm == 5.0
        assert r.y_mm == 5.0


def test_compatible_for_swap():
    a = PlacedPart("C1", 5.0, 5.0, 0.0, "Capacitor_SMD:C_0603_1608Metric")
    b = PlacedPart("C2", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric")
    c = PlacedPart("U1", 20.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm")
    assert _compatible_for_swap(a, b)
    assert not _compatible_for_swap(a, c)


def test_zero_iterations_returns_unchanged():
    placed = [
        PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
        PlacedPart("C1", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
        PlacedPart("R1", 40.0, 30.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
    ]
    original = [(p.ref, p.x_mm, p.y_mm) for p in placed]
    result = refine_placement(
        placed, _simple_circuit(), COMMON_BBOXES,
        outline=BoardOutline(50.0, 40.0), max_iterations=0,
    )
    assert not result.improved
    assert [(p.ref, p.x_mm, p.y_mm) for p in placed] == original
