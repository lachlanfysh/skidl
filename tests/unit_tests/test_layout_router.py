"""Tests for global routing estimation."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))

from skidl.layout.constraints import BoardOutline, LayoutConstraints
from skidl.layout.router import (
    RoutingEstimate,
    _rmst_edges,
    _trace_l_path,
    estimate_routing,
    rmst_length,
)
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


# ====================================================================
# RMST
# ====================================================================

class TestRMST:
    def test_empty(self):
        assert rmst_length([]) == 0.0

    def test_single_point(self):
        assert rmst_length([(5.0, 5.0)]) == 0.0

    def test_two_points(self):
        length = rmst_length([(0.0, 0.0), (3.0, 4.0)])
        assert abs(length - 7.0) < 0.01  # manhattan: |3|+|4| = 7

    def test_three_points_triangle(self):
        points = [(0.0, 0.0), (10.0, 0.0), (5.0, 5.0)]
        length = rmst_length(points)
        # MST connects all 3 with minimum total manhattan distance
        assert length > 0
        # Should be less than sum of all pairwise distances
        assert length < 30.0

    def test_collinear_points(self):
        points = [(0.0, 0.0), (5.0, 0.0), (10.0, 0.0)]
        length = rmst_length(points)
        assert abs(length - 10.0) < 0.01  # span of the line

    def test_edges_count(self):
        points = [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)]
        edges = _rmst_edges(points)
        assert len(edges) == 3  # n-1 edges for n points

    def test_four_point_square(self):
        points = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
        length = rmst_length(points)
        assert abs(length - 30.0) < 0.01  # MST of a square = 3 sides


# ====================================================================
# Grid tracing
# ====================================================================

class TestGridTracing:
    def test_horizontal_trace(self):
        grid = [[0] * 10 for _ in range(10)]
        _trace_l_path((0.0, 0.0), (5.0, 0.0), grid, 0.0, 0.0, 1.0)
        assert grid[0][0] > 0
        assert grid[0][5] > 0

    def test_vertical_trace(self):
        grid = [[0] * 10 for _ in range(10)]
        _trace_l_path((0.0, 0.0), (0.0, 5.0), grid, 0.0, 0.0, 1.0)
        assert grid[0][0] > 0
        assert grid[5][0] > 0

    def test_l_shape_trace(self):
        grid = [[0] * 10 for _ in range(10)]
        _trace_l_path((0.0, 0.0), (5.0, 5.0), grid, 0.0, 0.0, 1.0)
        # Horizontal leg at row 0
        assert grid[0][0] > 0
        assert grid[0][3] > 0
        # Vertical leg at col 5
        assert grid[3][5] > 0
        assert grid[5][5] > 0

    def test_no_double_count_at_corner(self):
        grid = [[0] * 10 for _ in range(10)]
        _trace_l_path((0.0, 0.0), (3.0, 3.0), grid, 0.0, 0.0, 1.0)
        # Corner cell (row=0, col=3) should be counted once, not twice
        assert grid[0][3] == 1


# ====================================================================
# estimate_routing
# ====================================================================

class TestEstimateRouting:
    def test_basic_estimate(self):
        placed = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 27.0, 20.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 30.0, 22.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        outline = BoardOutline(50.0, 40.0)
        result = estimate_routing(placed, _simple_circuit(), COMMON_BBOXES, outline)
        assert isinstance(result, RoutingEstimate)
        assert result.total_rmst_mm > 0
        assert result.grid_rows > 0
        assert result.grid_cols > 0

    def test_no_outline(self):
        placed = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
        ]
        result = estimate_routing(placed, _simple_circuit(), COMMON_BBOXES, None)
        assert result.total_rmst_mm == 0.0
        assert result.overflow_cells == 0

    def test_no_circuit(self):
        placed = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
        ]
        outline = BoardOutline(50.0, 40.0)
        result = estimate_routing(placed, None, COMMON_BBOXES, outline)
        assert result.total_rmst_mm == 0.0

    def test_compact_less_congested(self):
        """Parts close together should have less routing congestion."""
        compact = [
            PlacedPart("U1", 20.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 22.0, 20.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 24.0, 20.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        spread = [
            PlacedPart("U1", 5.0, 5.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 45.0, 5.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 25.0, 35.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        outline = BoardOutline(50.0, 40.0)
        ckt = _simple_circuit()
        r_compact = estimate_routing(compact, ckt, COMMON_BBOXES, outline)
        r_spread = estimate_routing(spread, ckt, COMMON_BBOXES, outline)
        assert r_compact.total_rmst_mm < r_spread.total_rmst_mm

    def test_4layer_higher_capacity(self):
        placed = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 27.0, 20.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 30.0, 22.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        outline = BoardOutline(50.0, 40.0)
        ckt = _simple_circuit()
        r_2layer = estimate_routing(placed, ckt, COMMON_BBOXES, outline, board_layers=2)
        r_4layer = estimate_routing(placed, ckt, COMMON_BBOXES, outline, board_layers=4)
        assert r_4layer.capacity_per_cell > r_2layer.capacity_per_cell

    def test_blocked_cells_reported(self):
        placed = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 27.0, 20.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
            PlacedPart("R1", 30.0, 22.0, 0.0, "Resistor_SMD:R_0603_1608Metric"),
        ]
        outline = BoardOutline(50.0, 40.0)
        result = estimate_routing(placed, _simple_circuit(), COMMON_BBOXES, outline)
        assert result.blocked_cells > 0

    def test_congestion_penalty_capped(self):
        result = RoutingEstimate(
            total_rmst_mm=100.0, overflow_cells=100,
            max_demand=20, avg_demand=5.0, blocked_cells=50,
            cell_size_mm=1.0, grid_rows=40, grid_cols=50,
            capacity_per_cell=6,
        )
        assert result.congestion_penalty == 15.0  # capped at 15

    def test_summary_readable(self):
        placed = [
            PlacedPart("U1", 25.0, 20.0, 0.0, "Package_QFP:LQFP-48_7x7mm_P0.5mm"),
            PlacedPart("C1", 27.0, 20.0, 0.0, "Capacitor_SMD:C_0603_1608Metric"),
        ]
        outline = BoardOutline(50.0, 40.0)
        result = estimate_routing(placed, _simple_circuit(), COMMON_BBOXES, outline)
        s = result.summary()
        assert "RMST" in s
        assert "grid" in s
        assert "Demand" in s
