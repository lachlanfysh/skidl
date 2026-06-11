"""Tests for the complexity estimator pre-run gate."""

import os

import pytest

os.environ.setdefault("KICAD9_SYMBOL_DIR", "/usr/share/kicad/symbols")

from schemas.circuit_spec import CircuitSpec
from schemas.estimator import (
    ComplexityEstimate,
    DecisionPrediction,
    _geometry_bucket,
    _sigmoid,
    estimate_complexity,
)

SYM_DIR = os.environ["KICAD9_SYMBOL_DIR"]
FP_DIR = "/usr/share/kicad/footprints"
needs_kicad = pytest.mark.skipif(
    not os.path.isdir(SYM_DIR), reason="KiCad symbol libraries not installed"
)


def mk_spec(parts, nets, board=None):
    return CircuitSpec.model_validate(
        {"board": board or {"name": "test-board"}, "parts": parts, "nets": nets}
    )


CLEAN_PARTS = [
    {"ref": "R1", "lib": "Device", "part": "R", "value": "10K",
     "footprint": "Resistor_SMD:R_0603_1608Metric"},
    {"ref": "R2", "lib": "Device", "part": "R", "value": "4.7K",
     "footprint": "Resistor_SMD:R_0603_1608Metric"},
    {"ref": "C1", "lib": "Device", "part": "C", "value": "100nF",
     "footprint": "Capacitor_SMD:C_0603_1608Metric"},
]
CLEAN_NETS = [
    {"name": "VCC", "power": True, "pins": ["R1.1", "C1.1"]},
    {"name": "GND", "power": True, "pins": ["R2.2", "C1.2"]},
    {"name": "SIG", "pins": ["R1.2", "R2.1"]},
]


class TestGeometryBucket:
    def test_small(self):
        assert _geometry_bucket(5) == "small"

    def test_medium(self):
        assert _geometry_bucket(15) == "medium"

    def test_large(self):
        assert _geometry_bucket(30) == "large"

    def test_xlarge(self):
        assert _geometry_bucket(75) == "xlarge"

    def test_boundaries(self):
        assert _geometry_bucket(9) == "small"
        assert _geometry_bucket(10) == "medium"
        assert _geometry_bucket(24) == "medium"
        assert _geometry_bucket(25) == "large"
        assert _geometry_bucket(49) == "large"
        assert _geometry_bucket(50) == "xlarge"


class TestSigmoid:
    def test_zero(self):
        assert _sigmoid(0) == pytest.approx(0.5)

    def test_large_positive(self):
        assert _sigmoid(100) == pytest.approx(1.0, abs=1e-6)

    def test_large_negative(self):
        assert _sigmoid(-100) == pytest.approx(0.0, abs=1e-6)

    def test_clamp(self):
        assert _sigmoid(1000) == pytest.approx(1.0)
        assert _sigmoid(-1000) == pytest.approx(0.0)


class TestDecisionPrediction:
    def test_total(self):
        d = DecisionPrediction(footprint=2, pin=1, library=0, part=1, net_ref=0)
        assert d.total == 4

    def test_zero_total(self):
        d = DecisionPrediction()
        assert d.total == 0


@needs_kicad
class TestEstimateComplexity:
    def test_clean_spec_zero_decisions(self):
        spec = mk_spec(CLEAN_PARTS, CLEAN_NETS)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert isinstance(result, ComplexityEstimate)
        assert result.decisions_predicted.total == 0
        assert result.auto_fixable == 0
        assert result.needs_review == 0

    def test_clean_spec_simple_tier(self):
        spec = mk_spec(CLEAN_PARTS, CLEAN_NETS)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert result.complexity_tier == "simple"

    def test_bad_footprint_counted(self):
        parts = [
            {"ref": "R1", "lib": "Device", "part": "R", "value": "10K",
             "footprint": "NonExistent:Fake_Package"},
        ]
        nets = [{"name": "SIG", "pins": ["R1.1", "R1.2"]}]
        spec = mk_spec(parts, nets)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert result.decisions_predicted.footprint >= 1

    def test_bad_library_counted(self):
        parts = [
            {"ref": "U1", "lib": "FakeLibrary_DoesNotExist", "part": "FakePart",
             "footprint": "Resistor_SMD:R_0603_1608Metric"},
        ]
        nets = [{"name": "SIG", "pins": ["U1.1"]}]
        spec = mk_spec(parts, nets)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert result.decisions_predicted.library >= 1

    def test_large_board_ambitious_tier(self):
        parts = [
            {"ref": f"R{i}", "lib": "Device", "part": "R", "value": "10K",
             "footprint": "Resistor_SMD:R_0603_1608Metric"}
            for i in range(1, 65)
        ]
        nets = [
            {"name": f"N{i}", "pins": [f"R{i}.1", f"R{i}.2"]}
            for i in range(1, 65)
        ]
        spec = mk_spec(parts, nets)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert result.complexity_tier == "ambitious"

    def test_runtime_prediction_fields(self):
        spec = mk_spec(CLEAN_PARTS, CLEAN_NETS)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        rt = result.runtime_prediction
        assert 0.0 <= rt.timeout_probability <= 1.0
        assert 0.0 <= rt.success_probability <= 1.0
        assert rt.confidence in ("statistical", "insufficient_data")
        assert rt.basis_run_count >= 0

    def test_cost_range_sane(self):
        spec = mk_spec(CLEAN_PARTS, CLEAN_NETS)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert result.estimated_cost.min_usd <= result.estimated_cost.expected_usd
        assert result.estimated_cost.expected_usd <= result.estimated_cost.max_usd

    def test_time_range_sane(self):
        spec = mk_spec(CLEAN_PARTS, CLEAN_NETS)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert result.estimated_wall_time.min_s <= result.estimated_wall_time.expected_s
        assert result.estimated_wall_time.expected_s <= result.estimated_wall_time.max_s
        assert result.estimated_wall_time.min_s > 0

    def test_does_not_mutate_input_spec(self):
        spec = mk_spec(CLEAN_PARTS, CLEAN_NETS)
        original_parts = [p.model_dump() for p in spec.parts]
        estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        after_parts = [p.model_dump() for p in spec.parts]
        assert original_parts == after_parts

    def test_warnings_list(self):
        spec = mk_spec(CLEAN_PARTS, CLEAN_NETS)
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert isinstance(result.warnings, list)


@needs_kicad
class TestEstimatorOnCorpus:
    """Smoke tests against real corpus specs."""

    @pytest.fixture(params=["ref-distortion-01-bmp", "ref-led-3mm-rgb-wire"])
    def corpus_spec(self, request):
        path = f"corpus/specs/{request.param}.json"
        if not os.path.exists(path):
            pytest.skip(f"Corpus spec {path} not found")
        return CircuitSpec.model_validate_json(open(path).read()), request.param

    def test_returns_valid_estimate(self, corpus_spec):
        spec, name = corpus_spec
        result = estimate_complexity(spec, sym_dir=SYM_DIR, fp_dirs=[FP_DIR])
        assert isinstance(result, ComplexityEstimate)
        assert result.complexity_tier in ("simple", "moderate", "complex", "ambitious")
        assert result.decisions_predicted.total >= 0
