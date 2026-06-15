from __future__ import annotations

import pytest

from skidl.layout.candidates import PlacementCandidate
from skidl.layout.constraints import BoardOutline, EdgeAnchor, FixedPosition, LayoutConstraints
from skidl.layout.geometry import FootprintGeometry, PadGeometry
from skidl.layout.refinement import _is_better, refine_candidate_placement, refine_placement
from skidl.layout.scoring import LayoutScore, score_placement
from skidl.layout.writer import PlacedPart


class _Net:
    def __init__(self, name):
        self.name = name
        self._pins = []

    def get_pins(self):
        return self._pins


class _Pin:
    def __init__(self, part, net):
        self.part = part
        self.net = net
        net._pins.append(self)


class _Part:
    def __init__(self, ref, footprint, name="", nets=None, pins=2):
        self.ref = ref
        self.footprint = footprint
        self.name = name
        self.value = ""
        self.pins = []
        for net in nets or []:
            self.pins.append(_Pin(self, net))
        while len(self.pins) < pins:
            self.pins.append(_Pin(self, _Net(f"{ref}_N{len(self.pins)}")))

    def __len__(self):
        return len(self.pins)


class _Circuit:
    def __init__(self, parts, nets):
        self.parts = parts
        self._nets = nets

    def get_nets(self):
        return self._nets


BBOXES = {
    "Package_QFP:MCU": (8.0, 8.0),
    "Connector:USB": (10.0, 5.0),
    "Package:Long": (16.0, 4.0),
    "Resistor_SMD:R_0603": (1.6, 0.8),
}


def _connected_circuit():
    sig = _Net("SIG")
    u1 = _Part("U1", "Package_QFP:MCU", nets=[sig], pins=8)
    u2 = _Part("U2", "Package_QFP:MCU", nets=[sig], pins=8)
    return _Circuit([u1, u2], [sig])


def _signature(placed_parts):
    return [
        (
            part.ref,
            round(part.x_mm, 4),
            round(part.y_mm, 4),
            round(part.rot_deg, 4),
        )
        for part in placed_parts
    ]


def test_refinement_moves_unlocked_part_when_score_improves():
    circuit = _connected_circuit()
    constraints = LayoutConstraints(
        outline=BoardOutline(100.0, 50.0),
        fixed=[FixedPosition("U1", 10.0, 10.0)],
    )
    placed = [
        PlacedPart("U1", 10.0, 10.0, 0.0, "Package_QFP:MCU"),
        PlacedPart("U2", 80.0, 10.0, 0.0, "Package_QFP:MCU"),
    ]
    candidate = PlacementCandidate(
        name="test",
        placed_parts=placed,
        constraints=constraints,
    )
    before = score_placement(
        candidate.placed_parts,
        circuit,
        BBOXES,
        outline=constraints.outline,
    )

    result = refine_candidate_placement(candidate, circuit, BBOXES)
    after = score_placement(
        candidate.placed_parts,
        circuit,
        BBOXES,
        outline=constraints.outline,
    )
    by_ref = {part.ref: part for part in candidate.placed_parts}

    assert result.accepted_moves >= 1
    assert after.score > before.score
    assert by_ref["U1"].x_mm == pytest.approx(10.0)
    assert by_ref["U1"].y_mm == pytest.approx(10.0)
    assert by_ref["U2"].x_mm < 80.0
    assert "local refinement accepted" in "; ".join(candidate.reasons)
    assert "connected-net centroid" in "; ".join(candidate.ref_reasons["U2"])


def test_refinement_preserves_edge_anchor_positions():
    vbus = _Net("VBUS")
    j1 = _Part("J1", "Connector:USB", name="USB connector", nets=[vbus], pins=4)
    u1 = _Part("U1", "Package_QFP:MCU", nets=[vbus], pins=8)
    circuit = _Circuit([j1, u1], [vbus])
    constraints = LayoutConstraints(
        outline=BoardOutline(100.0, 60.0),
        edge_anchors=[EdgeAnchor("J1", "bottom", offset_mm=50.0, rot_deg=180.0)],
    )
    placed = [
        PlacedPart("J1", 50.0, 57.5, 180.0, "Connector:USB"),
        PlacedPart("U1", 10.0, 10.0, 0.0, "Package_QFP:MCU"),
    ]

    result = refine_placement(placed, circuit, BBOXES, constraints=constraints)
    by_ref = {part.ref: part for part in result.placed_parts}

    assert by_ref["J1"].x_mm == pytest.approx(50.0)
    assert by_ref["J1"].y_mm == pytest.approx(57.5)
    assert by_ref["J1"].rot_deg == pytest.approx(180.0)


def test_refinement_uses_pad_aware_pin_gravity_for_signal_passive():
    sig = _Net("GPIO1")
    vcc = _Net("3V3")
    u1 = _Part("U1", "Package_QFP:MCU", nets=[sig, vcc], pins=8)
    r1 = _Part("R1", "Resistor_SMD:R_0603", nets=[sig, vcc], pins=2)
    circuit = _Circuit([u1, r1], [sig, vcc])
    constraints = LayoutConstraints(
        outline=BoardOutline(80.0, 40.0),
        fixed=[FixedPosition("U1", 20.0, 20.0)],
    )
    geometries = {
        "Package_QFP:MCU": FootprintGeometry(
            footprint="Package_QFP:MCU",
            courtyard_bounds=(-4.0, -4.0, 4.0, 4.0),
            pads=[
                PadGeometry("1", 4.0, -1.0, 0.5, 0.5),
                PadGeometry("2", 4.0, 1.0, 0.5, 0.5),
            ],
        ),
        "Resistor_SMD:R_0603": FootprintGeometry(
            footprint="Resistor_SMD:R_0603",
            courtyard_bounds=(-0.8, -0.4, 0.8, 0.4),
            pads=[
                PadGeometry("1", -0.45, 0.0, 0.4, 0.5),
                PadGeometry("2", 0.45, 0.0, 0.4, 0.5),
            ],
        ),
    }
    placed = [
        PlacedPart("U1", 20.0, 20.0, 0.0, "Package_QFP:MCU"),
        PlacedPart("R1", 70.0, 30.0, 0.0, "Resistor_SMD:R_0603"),
    ]

    result = refine_placement(
        placed,
        circuit,
        BBOXES,
        constraints=constraints,
        fp_geometries=geometries,
    )
    by_ref = {part.ref: part for part in result.placed_parts}

    assert result.accepted_moves >= 1
    assert by_ref["U1"].x_mm == pytest.approx(20.0)
    assert by_ref["R1"].x_mm < 70.0
    assert abs(by_ref["R1"].x_mm - 24.0) < 12.0
    assert "passive pin gravity" in "; ".join(result.ref_reasons["R1"])


def test_refinement_better_gate_prioritizes_hard_violations():
    assert _is_better(
        LayoutScore(score=60.0, overlap_count=2),
        LayoutScore(score=55.0, overlap_count=1),
    )
    assert not _is_better(
        LayoutScore(score=60.0, overlap_count=1),
        LayoutScore(score=90.0, overlap_count=2),
    )
    assert _is_better(
        LayoutScore(score=60.0, overlap_count=1),
        LayoutScore(score=65.0, overlap_count=1),
    )


def test_refinement_legalizes_overlap_without_net_centroid():
    u1 = _Part("U1", "Package_QFP:MCU", pins=8)
    u2 = _Part("U2", "Package_QFP:MCU", pins=8)
    circuit = _Circuit([u1, u2], [])
    constraints = LayoutConstraints(
        outline=BoardOutline(60.0, 40.0),
        fixed=[FixedPosition("U1", 20.0, 20.0)],
    )
    placed = [
        PlacedPart("U1", 20.0, 20.0, 0.0, "Package_QFP:MCU"),
        PlacedPart("U2", 20.0, 20.0, 0.0, "Package_QFP:MCU"),
    ]

    result = refine_placement(placed, circuit, BBOXES, constraints=constraints)
    by_ref = {part.ref: part for part in result.placed_parts}
    score = score_placement(
        result.placed_parts,
        circuit,
        BBOXES,
        outline=constraints.outline,
    )

    assert result.accepted_moves >= 1
    assert score.overlap_count == 0
    assert by_ref["U1"].x_mm == pytest.approx(20.0)
    assert by_ref["U1"].y_mm == pytest.approx(20.0)
    assert by_ref["U2"].x_mm != pytest.approx(20.0) or by_ref["U2"].y_mm != pytest.approx(20.0)
    assert "legalized overlap" in "; ".join(result.ref_reasons["U2"])


def test_refinement_legalizes_multiple_independent_overlaps():
    parts = [_Part(ref, "Package_QFP:MCU", pins=8) for ref in ("U1", "U2", "U3", "U4")]
    circuit = _Circuit(parts, [])
    constraints = LayoutConstraints(
        outline=BoardOutline(80.0, 50.0),
        fixed=[FixedPosition("U1", 20.0, 20.0)],
    )
    placed = [
        PlacedPart(part.ref, 20.0, 20.0, 0.0, "Package_QFP:MCU")
        for part in parts
    ]

    result = refine_placement(placed, circuit, BBOXES, constraints=constraints)
    score = score_placement(
        result.placed_parts,
        circuit,
        BBOXES,
        outline=constraints.outline,
    )

    assert result.accepted_moves >= 3
    assert score.overlap_count == 0


def test_refinement_legalizes_more_than_sixteen_overlaps_by_default():
    pair_count = 18
    parts = []
    placed = []
    fixed = []
    for idx in range(pair_count):
        x = 15.0 + (idx % 6) * 25.0
        y = 15.0 + (idx // 6) * 25.0
        fixed_ref = f"U{idx}"
        movable_ref = f"C{idx}"
        parts.extend([
            _Part(fixed_ref, "Package_QFP:MCU", pins=8),
            _Part(movable_ref, "Package_QFP:MCU", pins=8),
        ])
        placed.extend([
            PlacedPart(fixed_ref, x, y, 0.0, "Package_QFP:MCU"),
            PlacedPart(movable_ref, x, y, 0.0, "Package_QFP:MCU"),
        ])
        fixed.append(FixedPosition(fixed_ref, x, y))

    circuit = _Circuit(parts, [])
    constraints = LayoutConstraints(
        outline=BoardOutline(180.0, 100.0),
        fixed=fixed,
    )

    result = refine_placement(placed, circuit, BBOXES, constraints=constraints)
    score = score_placement(
        result.placed_parts,
        circuit,
        BBOXES,
        outline=constraints.outline,
    )

    assert result.accepted_moves >= pair_count
    assert score.overlap_count == 0


def test_refinement_can_rotate_geometry_into_outline():
    circuit = _Circuit([_Part("U1", "Package:Long", pins=8)], [])
    constraints = LayoutConstraints(outline=BoardOutline(20.0, 20.0))
    placed = [PlacedPart("U1", 18.0, 10.0, 0.0, "Package:Long")]
    geometries = {
        "Package:Long": FootprintGeometry(
            footprint="Package:Long",
            body_bounds=(-8.0, -2.0, 8.0, 2.0),
        )
    }

    result = refine_placement(
        placed,
        circuit,
        BBOXES,
        constraints=constraints,
        fp_geometries=geometries,
    )

    assert result.accepted_rotations == 1
    assert result.placed_parts[0].rot_deg == pytest.approx(90.0)
    assert result.final_score > result.start_score


def test_refinement_is_deterministic():
    circuit = _connected_circuit()
    constraints = LayoutConstraints(outline=BoardOutline(100.0, 50.0))
    placed = [
        PlacedPart("U1", 10.0, 10.0, 0.0, "Package_QFP:MCU"),
        PlacedPart("U2", 80.0, 10.0, 0.0, "Package_QFP:MCU"),
    ]

    first = refine_placement(placed, circuit, BBOXES, constraints=constraints)
    second = refine_placement(placed, circuit, BBOXES, constraints=constraints)

    assert _signature(first.placed_parts) == _signature(second.placed_parts)
    assert first.final_score == pytest.approx(second.final_score)
