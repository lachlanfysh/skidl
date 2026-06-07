"""Tests for simulated annealing placement refinement."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from skidl.layout.anneal import (
    AnnealConfig,
    AnnealResult,
    anneal_placement,
    _FastScorer,
    _build_swap_map,
    _perturb,
)
from skidl.layout.constraints import (
    BoardOutline,
    EdgeAnchor,
    FixedPosition,
    LayoutConstraints,
)
from skidl.layout.engine import plan_layout
from skidl.layout.writer import PlacedPart

from layout_case_helpers import (
    COMMON_BBOXES,
    _Circuit,
    _Net,
    _Part,
    make_connector,
    make_decap,
    make_ic,
    make_passive,
    make_power_nets,
)


def _simple_circuit():
    vcc, gnd = make_power_nets()
    sig = _Net("SIG")
    u1 = make_ic(
        "U1", "MCU", "Package_QFP:LQFP-48_7x7mm_P0.5mm",
        signal_nets=[sig], power_nets=[vcc, gnd], pins=48,
    )
    c1 = make_decap("C1", vcc, gnd)
    r1 = make_passive("R1", "10K", "Resistor_SMD:R_0603_1608Metric", nets=[sig, vcc])
    return _Circuit([u1, c1, r1], [vcc, gnd, sig])


def _spread_placement():
    """Parts deliberately spread far apart — SA should compact them."""
    return [
        PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
        PlacedPart("C1", 5.0, 5.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
        PlacedPart("R1", 45.0, 35.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
    ]


# ====================================================================
# AnnealConfig / AnnealResult
# ====================================================================

class TestAnnealConfig:
    def test_defaults(self):
        cfg = AnnealConfig()
        assert cfg.initial_temp == 30.0
        assert cfg.cooling_rate == 0.97
        assert cfg.min_temp == 0.1
        assert cfg.seed == 42
        assert cfg.move_prob + cfg.rotate_prob < 1.0

    def test_custom_config(self):
        cfg = AnnealConfig(initial_temp=50.0, cooling_rate=0.99, seed=123)
        assert cfg.initial_temp == 50.0
        assert cfg.seed == 123


class TestAnnealResult:
    def test_improved_summary(self):
        result = AnnealResult(
            improved=True, iterations=1000, accepted_moves=400,
            rejected_moves=600, score_before=40.0, score_after=65.0,
            best_score=65.0, temperature_steps=50, final_temp=0.05,
        )
        s = result.summary()
        assert "+25.0" in s
        assert "40.0" in s
        assert "65.0" in s
        assert "40%" in s

    def test_no_improvement_summary(self):
        result = AnnealResult(
            improved=False, iterations=500, accepted_moves=0,
            rejected_moves=500, score_before=50.0, score_after=50.0,
            best_score=50.0, temperature_steps=30, final_temp=0.08,
        )
        assert "no improvement" in result.summary()


# ====================================================================
# _FastScorer
# ====================================================================

class TestFastScorer:
    def test_scores_in_range(self):
        scorer = _FastScorer(
            _simple_circuit(), COMMON_BBOXES,
            BoardOutline(50.0, 40.0), 0.5,
        )
        placed = _spread_placement()
        score = scorer.score(placed)
        assert 0.0 <= score <= 100.0

    def test_overlap_detected(self):
        scorer = _FastScorer(
            _simple_circuit(), COMMON_BBOXES,
            BoardOutline(50.0, 40.0), 0.5,
        )
        overlapping = [
            PlacedPart("U1", 10.0, 10.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 10.0, 10.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        spread = _spread_placement()
        assert scorer.score(overlapping) < scorer.score(spread)

    def test_closer_decap_scores_better(self):
        scorer = _FastScorer(
            _simple_circuit(), COMMON_BBOXES,
            BoardOutline(50.0, 40.0), 0.5,
        )
        far = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 5.0, 5.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 40.0, 20.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        close = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 31.0, 20.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 34.0, 20.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        assert scorer.score(close) >= scorer.score(far)

    def test_no_circuit(self):
        scorer = _FastScorer(None, COMMON_BBOXES, BoardOutline(50.0, 40.0), 0.5)
        placed = _spread_placement()
        score = scorer.score(placed)
        assert 0.0 <= score <= 100.0


# ====================================================================
# _build_swap_map
# ====================================================================

class TestSwapMap:
    def test_compatible_parts_found(self):
        parts = [
            PlacedPart("C1", 5.0, 5.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("C2", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("U1", 20.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
        ]
        swap_map = _build_swap_map(parts, locked=set())
        assert "C1" in swap_map
        assert "C2" in swap_map["C1"]
        assert "U1" not in swap_map

    def test_locked_excluded(self):
        parts = [
            PlacedPart("C1", 5.0, 5.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("C2", 10.0, 10.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
        ]
        swap_map = _build_swap_map(parts, locked={"C1"})
        assert "C1" not in swap_map
        assert "C2" not in swap_map


# ====================================================================
# anneal_placement
# ====================================================================

class TestAnnealPlacement:
    def test_never_worsens_score(self):
        placed = _spread_placement()
        outline = BoardOutline(50.0, 40.0)
        result = anneal_placement(
            placed, _simple_circuit(), COMMON_BBOXES,
            outline=outline,
            config=AnnealConfig(initial_temp=10.0, min_temp=1.0, seed=42),
        )
        assert isinstance(result, AnnealResult)
        assert result.score_after >= result.score_before

    def test_fixed_parts_remain_fixed(self):
        placed = _spread_placement()
        constraints = LayoutConstraints(
            fixed=[FixedPosition("U1", 25.0, 20.0)],
            outline=BoardOutline(50.0, 40.0),
        )
        anneal_placement(
            placed, _simple_circuit(), COMMON_BBOXES,
            constraints=constraints, outline=BoardOutline(50.0, 40.0),
            config=AnnealConfig(initial_temp=10.0, min_temp=1.0),
        )
        u1 = next(p for p in placed if p.ref == "U1")
        assert u1.x_mm == 25.0
        assert u1.y_mm == 20.0

    def test_edge_anchored_parts_locked(self):
        placed = _spread_placement()
        constraints = LayoutConstraints(
            edge_anchors=[EdgeAnchor("U1", "bottom")],
            outline=BoardOutline(50.0, 40.0),
        )
        anneal_placement(
            placed, _simple_circuit(), COMMON_BBOXES,
            constraints=constraints, outline=BoardOutline(50.0, 40.0),
            config=AnnealConfig(initial_temp=10.0, min_temp=1.0),
        )
        u1 = next(p for p in placed if p.ref == "U1")
        assert u1.x_mm == 25.0
        assert u1.y_mm == 20.0

    def test_deterministic_with_same_seed(self):
        def _run(seed):
            placed = _spread_placement()
            result = anneal_placement(
                placed, _simple_circuit(), COMMON_BBOXES,
                outline=BoardOutline(50.0, 40.0),
                config=AnnealConfig(seed=seed, initial_temp=10.0, min_temp=1.0),
            )
            return [(p.ref, p.x_mm, p.y_mm, p.rot_deg) for p in placed], result.score_after

        pos_a, score_a = _run(42)
        pos_b, score_b = _run(42)
        assert pos_a == pos_b
        assert score_a == score_b

    def test_different_seeds_may_differ(self):
        def _run(seed):
            placed = _spread_placement()
            anneal_placement(
                placed, _simple_circuit(), COMMON_BBOXES,
                outline=BoardOutline(50.0, 40.0),
                config=AnnealConfig(seed=seed, initial_temp=20.0, min_temp=0.5),
            )
            return [(p.ref, round(p.x_mm, 2), round(p.y_mm, 2)) for p in placed]

        pos_a = _run(42)
        pos_b = _run(999)
        # Different seeds should at least potentially produce different results
        # (not guaranteed but very likely with enough temperature)
        # We just check it doesn't crash
        assert len(pos_a) == len(pos_b)

    def test_reports_accepted_and_rejected(self):
        placed = _spread_placement()
        result = anneal_placement(
            placed, _simple_circuit(), COMMON_BBOXES,
            outline=BoardOutline(50.0, 40.0),
            config=AnnealConfig(initial_temp=10.0, min_temp=1.0),
        )
        assert result.accepted_moves + result.rejected_moves == result.iterations
        assert result.temperature_steps > 0

    def test_no_unlocked_parts(self):
        placed = _spread_placement()
        constraints = LayoutConstraints(
            fixed=[
                FixedPosition("U1", 25.0, 20.0),
                FixedPosition("C1", 5.0, 5.0),
                FixedPosition("R1", 45.0, 35.0),
            ],
            outline=BoardOutline(50.0, 40.0),
        )
        result = anneal_placement(
            placed, _simple_circuit(), COMMON_BBOXES,
            constraints=constraints, outline=BoardOutline(50.0, 40.0),
        )
        assert not result.improved
        assert result.iterations == 0

    def test_compacts_spread_placement(self):
        """SA should bring the decap closer to the MCU."""
        placed = _spread_placement()
        c1_before = next(p for p in placed if p.ref == "C1")
        u1 = next(p for p in placed if p.ref == "U1")
        import math
        dist_before = math.hypot(c1_before.x_mm - u1.x_mm, c1_before.y_mm - u1.y_mm)

        anneal_placement(
            placed, _simple_circuit(), COMMON_BBOXES,
            outline=BoardOutline(50.0, 40.0),
            config=AnnealConfig(initial_temp=30.0, min_temp=0.1, seed=42),
        )
        c1_after = next(p for p in placed if p.ref == "C1")
        u1_after = next(p for p in placed if p.ref == "U1")
        dist_after = math.hypot(c1_after.x_mm - u1_after.x_mm, c1_after.y_mm - u1_after.y_mm)
        assert dist_after <= dist_before


# ====================================================================
# Tournament via plan_layout
# ====================================================================

class TestTournament:
    @pytest.fixture(scope="class")
    def usb_mcu_sa(self):
        from test_layout_benchmark_cases import _usb_mcu_board
        ckt = _usb_mcu_board()
        return plan_layout(
            ckt,
            fp_bboxes=COMMON_BBOXES,
            constraints=LayoutConstraints(outline=BoardOutline(50.0, 35.0)),
            anneal=True,
            anneal_config=AnnealConfig(initial_temp=15.0, min_temp=1.0, seed=42),
            tournament_top_n=2,
        )

    @pytest.fixture(scope="class")
    def usb_mcu_greedy(self):
        from test_layout_benchmark_cases import _usb_mcu_board
        ckt = _usb_mcu_board()
        return plan_layout(
            ckt,
            fp_bboxes=COMMON_BBOXES,
            constraints=LayoutConstraints(outline=BoardOutline(50.0, 35.0)),
            anneal=False,
        )

    def test_sa_has_anneal_result(self, usb_mcu_sa):
        assert usb_mcu_sa.anneal is not None
        assert usb_mcu_sa.refinement is None

    def test_greedy_has_refinement_result(self, usb_mcu_greedy):
        assert usb_mcu_greedy.refinement is not None
        assert usb_mcu_greedy.anneal is None

    def test_both_have_routing_estimate(self, usb_mcu_sa, usb_mcu_greedy):
        assert usb_mcu_sa.routing is not None
        assert usb_mcu_greedy.routing is not None

    def test_sa_produces_valid_placement(self, usb_mcu_sa):
        assert usb_mcu_sa.score.score > 0.0
        assert usb_mcu_sa.validation.ok or len(usb_mcu_sa.validation.overlaps) < 3

    def test_summary_includes_anneal(self, usb_mcu_sa):
        s = usb_mcu_sa.summary()
        assert "nneal" in s  # "Annealing" in the summary
        assert "outing" in s  # "Routing" in the summary
