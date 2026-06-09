"""Regression tests for the 6-chunk sim & layout polish."""

import json
import unittest.mock as mock

import pytest


class TestSpatialGrid:
    def test_no_overlaps_returns_empty(self):
        from skidl.layout.spatial import SpatialGrid

        grid = SpatialGrid(cell_size_mm=5.0)
        grid.insert("R1", 0.0, 0.0, 2.0, 1.0)
        grid.insert("R2", 20.0, 20.0, 2.0, 1.0)
        grid.insert("R3", 40.0, 40.0, 2.0, 1.0)
        assert grid.all_overlapping_pairs(clearance=0.5) == []

    def test_overlapping_pair_detected(self):
        from skidl.layout.spatial import SpatialGrid

        grid = SpatialGrid(cell_size_mm=5.0)
        grid.insert("R1", 10.0, 10.0, 2.0, 1.0)
        grid.insert("R2", 11.0, 10.0, 2.0, 1.0)
        pairs = grid.all_overlapping_pairs(clearance=0.5)
        assert ("R1", "R2") in pairs

    def test_check_any_overlap(self):
        from skidl.layout.spatial import SpatialGrid

        grid = SpatialGrid(cell_size_mm=5.0)
        grid.insert("R1", 10.0, 10.0, 2.0, 1.0)
        assert grid.check_any_overlap(10.5, 10.0, 2.0, 1.0, 0.5)
        assert not grid.check_any_overlap(100.0, 100.0, 2.0, 1.0, 0.5)

    def test_matches_brute_force(self):
        from skidl.layout.spatial import SpatialGrid

        rects = [
            ("A", 0.0, 0.0, 3.0, 3.0),
            ("B", 2.0, 0.0, 3.0, 3.0),
            ("C", 10.0, 10.0, 2.0, 2.0),
            ("D", 11.0, 10.0, 2.0, 2.0),
            ("E", 50.0, 50.0, 1.0, 1.0),
        ]
        grid = SpatialGrid(cell_size_mm=5.0)
        for key, x, y, w, h in rects:
            grid.insert(key, x, y, w, h)

        grid_pairs = set(grid.all_overlapping_pairs(clearance=0.0))

        brute_pairs = set()
        for i, (k1, x1, y1, w1, h1) in enumerate(rects):
            for k2, x2, y2, w2, h2 in rects[i + 1 :]:
                if (
                    abs(x1 - x2) < (w1 + w2) / 2
                    and abs(y1 - y2) < (h1 + h2) / 2
                ):
                    brute_pairs.add((min(k1, k2), max(k1, k2)))

        assert grid_pairs == brute_pairs


class TestPowerSymbolFallback:
    def test_fallback_populates_common_symbols(self):
        from simp_sexp import Sexp

        with mock.patch("skidl.schlib.SchLib", side_effect=Exception("no libs")):
            import importlib
            import skidl.tools.kicad9.sexp_schematic as mod

            importlib.reload(mod)
            mod.init_power_symbol_data()

            assert "GND" in mod.pwr_symbol_names
            assert "VCC" in mod.pwr_symbol_names
            assert "+3V3" in mod.pwr_symbol_names
            assert len(mod.pwr_symbol_sexp_dict) == 6
            for name, sexp in mod.pwr_symbol_sexp_dict.items():
                assert isinstance(sexp, Sexp), f"{name} should be Sexp"

    def test_extract_power_lib_symbol_with_fallback(self):
        with mock.patch("skidl.schlib.SchLib", side_effect=Exception("no libs")):
            import importlib
            import skidl.tools.kicad9.sexp_schematic as mod

            importlib.reload(mod)
            mod.init_power_symbol_data()
            sym = mod._extract_power_lib_symbol("GND")
            assert sym is not None
            assert sym[1] == "power:GND"


class TestRoutabilityFeedback:
    def test_completion_pct(self):
        from skidl.layout.routability import RoutabilityFeedback

        rf = RoutabilityFeedback(unrouted_count=5, total_nets=100)
        assert rf.completion_pct == 95.0

        rf_zero = RoutabilityFeedback(unrouted_count=0, total_nets=0)
        assert rf_zero.completion_pct == 100.0

    def test_to_dict_serializable(self):
        from skidl.layout.routability import RoutabilityFeedback

        rf = RoutabilityFeedback(
            unrouted_count=3,
            total_nets=50,
            unrouted_nets=["VCC", "GND"],
            source="freerouting",
        )
        d = rf.to_dict()
        assert json.dumps(d)
        assert d["completion_pct"] == 94.0
        assert d["unrouted_nets"] == ["VCC", "GND"]

    def test_routability_propagates_to_top_risks(self):
        from skidl.layout.report import PlacementReport
        from skidl.layout.routability import RoutabilityFeedback

        rf = RoutabilityFeedback(
            unrouted_count=2,
            total_nets=10,
            unrouted_nets=["SDA", "SCL"],
        )
        report = PlacementReport(selected="test", routability=rf)
        risks = report.top_risks()
        assert any("SDA" in r for r in risks)
        assert any("SCL" in r for r in risks)

    def test_routability_in_placement_report_to_dict(self):
        from skidl.layout.report import PlacementReport
        from skidl.layout.routability import RoutabilityFeedback

        rf = RoutabilityFeedback(unrouted_count=1, total_nets=10)
        report = PlacementReport(selected="test", routability=rf)
        d = report.to_dict()
        assert "routability" in d
        assert d["routability"]["unrouted_count"] == 1


class TestToDict:
    def test_layout_score_to_dict_roundtrips(self):
        from skidl.layout.scoring import LayoutScore

        score = LayoutScore(
            score=85.0,
            total_hpwl_mm=120.5,
            overlap_count=2,
            warnings=["test"],
            congestion_regions=["region1"],
        )
        d = score.to_dict()
        assert json.dumps(d)
        assert d["score"] == 85.0
        assert d["overlap_count"] == 2
        assert d["ok"] is False

    def test_placement_report_includes_part_reasons_and_net_explanations(self):
        from skidl.layout.report import NetExplanation, PlacementReport

        report = PlacementReport(
            selected="test",
            part_reasons={"R1": ["near U1"], "C1": ["decoupling for U1"]},
            net_explanations={
                "VCC": NetExplanation(
                    name="VCC", hpwl_mm=45.0, risks=["long route"]
                )
            },
        )
        d = report.to_dict()
        assert json.dumps(d)
        assert "part_reasons" in d
        assert d["part_reasons"]["R1"] == ["near U1"]
        assert "net_explanations" in d
        assert d["net_explanations"]["VCC"]["hpwl_mm"] == 45.0

    def test_layout_feedback_report_to_dict(self):
        from skidl.sim.layout_feedback import (
            LayoutFeedbackReport,
            PlacementSuggestion,
        )

        report = LayoutFeedbackReport(
            suggestions=[
                PlacementSuggestion(
                    severity="warning", ref="C1", message="too far"
                )
            ],
            sim_penalty=3.0,
            decoupling_analyzed=True,
        )
        d = report.to_dict()
        assert json.dumps(d)
        assert d["sim_penalty"] == 3.0
        assert len(d["suggestions"]) == 1
        assert d["suggestions"][0]["ref"] == "C1"


class TestCandidateScorecard:
    def test_scorecard_sorts_valid_above_invalid(self):
        from skidl.layout.report import build_placement_report
        from skidl.layout.scoring import LayoutScore
        from skidl.layout.validator import ValidationResult

        valid_score = LayoutScore(score=60.0, total_hpwl_mm=50.0)
        invalid_score = LayoutScore(
            score=90.0, total_hpwl_mm=30.0, overlap_count=3
        )

        candidate_scores = {
            "valid_one": valid_score,
            "invalid_high": invalid_score,
        }
        candidate_validations = {
            "valid_one": ValidationResult(placed_parts=5),
            "invalid_high": ValidationResult(
                placed_parts=5, overlaps=[("A", "B"), ("C", "D"), ("E", "F")]
            ),
        }

        selected = mock.MagicMock()
        selected.name = "valid_one"
        selected.reasons = ["test"]
        selected.ref_reasons = {}

        power_plan = mock.MagicMock()
        power_plan.corridors = []
        power_plan.topology.chains = []
        power_plan.warnings = []

        report = build_placement_report(
            selected, candidate_scores, candidate_validations, power_plan
        )
        assert report.candidates[0].name == "valid_one"
        assert "best valid candidate" in report.reasons[-1]

    def test_scorecard_reason_when_none_valid(self):
        from skidl.layout.report import build_placement_report
        from skidl.layout.scoring import LayoutScore
        from skidl.layout.validator import ValidationResult

        candidate_scores = {
            "a": LayoutScore(score=50.0, overlap_count=1),
            "b": LayoutScore(score=30.0, overlap_count=2),
        }
        candidate_validations = {
            "a": ValidationResult(placed_parts=3, overlaps=[("X", "Y")]),
            "b": ValidationResult(
                placed_parts=3, overlaps=[("X", "Y"), ("Z", "W")]
            ),
        }

        selected = mock.MagicMock()
        selected.name = "a"
        selected.reasons = []
        selected.ref_reasons = {}

        power_plan = mock.MagicMock()
        power_plan.corridors = []
        power_plan.topology.chains = []
        power_plan.warnings = []

        report = build_placement_report(
            selected, candidate_scores, candidate_validations, power_plan
        )
        assert "none fully valid" in report.reasons[-1]
