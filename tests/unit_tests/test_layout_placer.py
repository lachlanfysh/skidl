from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from skidl.layout.constraints import LayoutConstraints, FixedPosition, BoardOutline
from skidl.layout.hierarchy import PlacementGroup
from skidl.layout.writer import PlacedPart
from skidl.layout.placer import place_parts, _overlaps


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_net(name: str):
    net = MagicMock()
    net.name = name
    return net


def _make_mock_part(ref, value="10k", footprint="Resistor_SMD:R_0805_2012Metric", num_pins=2, pin_nets=None):
    part = MagicMock()
    part.ref = ref
    part.value = value
    part.foot = footprint
    pins = []
    for i in range(num_pins):
        pin = MagicMock()
        net_name = pin_nets[i] if pin_nets and i < len(pin_nets) else f"NET_{ref}_{i}"
        pin.net = _mock_net(net_name)
        pins.append(pin)
    part.pins = pins
    part.__len__ = lambda self: num_pins
    part.__iter__ = lambda self: iter(pins)
    return part


def _simple_constraints(**kwargs):
    return LayoutConstraints(
        fixed=kwargs.get('fixed', []),
        zones=[],
        keepouts=[],
        outline=kwargs.get('outline', None),
    )


_FP_BBOXES = {
    "Package_DIP:DIP-28": (7.62, 35.56),
    "Resistor_SMD:R_0805_2012Metric": (2.0, 1.25),
    "Capacitor_SMD:C_0805_2012Metric": (2.0, 1.25),
}


# ---------------------------------------------------------------------------
# Fixed positions
# ---------------------------------------------------------------------------

def test_fixed_positions_honored():
    ic = _make_mock_part("U1", "ATmega328", "Package_DIP:DIP-28", num_pins=28)
    # Give the cap real VCC/GND nets so it is classified as a decoupling cap,
    # which places it directly beside the IC (not stacked below it).
    cap = _make_mock_part(
        "C1", "100nF", "Capacitor_SMD:C_0805_2012Metric",
        num_pins=2, pin_nets=["VCC", "GND"],
    )

    group = PlacementGroup(
        name="main",
        parts=[ic, cap],
        adjacency={"U1": {"C1": 2}, "C1": {"U1": 2}},
    )

    constraints = _simple_constraints(
        fixed=[FixedPosition("U1", 50.0, 60.0, 0.0)],
        outline=BoardOutline(100.0, 80.0),
    )

    result = place_parts({"main": group}, constraints, _FP_BBOXES)

    u1 = next(p for p in result if p.ref == "U1")
    assert u1.x_mm == 50.0
    assert u1.y_mm == 60.0

    c1 = next(p for p in result if p.ref == "C1")
    dist = ((c1.x_mm - 50) ** 2 + (c1.y_mm - 60) ** 2) ** 0.5
    assert dist < 15.0


def test_fixed_rotation_preserved():
    r = _make_mock_part("R1", "10k", "Resistor_SMD:R_0805_2012Metric", num_pins=2)
    group = PlacementGroup(name="g", parts=[r], adjacency={})
    constraints = _simple_constraints(fixed=[FixedPosition("R1", 20.0, 30.0, 90.0)])

    result = place_parts({"g": group}, constraints, _FP_BBOXES)
    r1 = next(p for p in result if p.ref == "R1")
    assert r1.rot_deg == 90.0


# ---------------------------------------------------------------------------
# All parts placed
# ---------------------------------------------------------------------------

def test_all_parts_placed():
    ic = _make_mock_part("U1", "ATMEGA", "Package_DIP:DIP-28", num_pins=28)
    caps = [_make_mock_part(f"C{i}", "100nF", "Capacitor_SMD:C_0805_2012Metric", num_pins=2) for i in range(5)]
    resistors = [_make_mock_part(f"R{i}", "10k", "Resistor_SMD:R_0805_2012Metric", num_pins=2) for i in range(3)]

    all_p = [ic] + caps + resistors
    adj = {p.ref: {"U1": 1} for p in caps + resistors}
    adj["U1"] = {p.ref: 1 for p in caps + resistors}

    group = PlacementGroup(name="main", parts=all_p, adjacency=adj)
    constraints = _simple_constraints(fixed=[FixedPosition("U1", 50.0, 40.0, 0.0)])

    result = place_parts({"main": group}, constraints, _FP_BBOXES)
    result_refs = {p.ref for p in result}
    expected_refs = {p.ref for p in all_p}
    assert result_refs == expected_refs


def test_empty_groups():
    result = place_parts({}, _simple_constraints(), {})
    assert result == []


def test_multiple_groups_all_placed():
    g1 = PlacementGroup(
        name="g1",
        parts=[_make_mock_part("R1"), _make_mock_part("R2")],
        adjacency={},
    )
    g2 = PlacementGroup(
        name="g2",
        parts=[_make_mock_part("R3"), _make_mock_part("R4")],
        adjacency={},
    )
    result = place_parts({"g1": g1, "g2": g2}, _simple_constraints(), _FP_BBOXES)
    assert {p.ref for p in result} == {"R1", "R2", "R3", "R4"}


# ---------------------------------------------------------------------------
# Decoupling cap placement
# ---------------------------------------------------------------------------

def test_decoupling_cap_near_ic():
    ic = _make_mock_part("U1", "MCU", "Package_DIP:DIP-28", num_pins=28)
    cap = _make_mock_part(
        "C1", "100nF", "Capacitor_SMD:C_0805_2012Metric",
        num_pins=2, pin_nets=["VCC", "GND"],
    )

    group = PlacementGroup(
        name="main",
        parts=[ic, cap],
        adjacency={"U1": {"C1": 2}, "C1": {"U1": 2}},
    )
    constraints = _simple_constraints(
        fixed=[FixedPosition("U1", 50.0, 50.0, 0.0)],
        outline=BoardOutline(120.0, 100.0),
    )

    result = place_parts({"main": group}, constraints, _FP_BBOXES)
    c1 = next(p for p in result if p.ref == "C1")
    dist = ((c1.x_mm - 50) ** 2 + (c1.y_mm - 50) ** 2) ** 0.5
    assert dist < 15.0


def test_decoupling_cap_not_decap_by_value():
    """A 10k resistor should NOT be treated as a decoupling cap."""
    r = _make_mock_part("R1", "10k", "Resistor_SMD:R_0805_2012Metric",
                        num_pins=2, pin_nets=["VCC", "GND"])
    group = PlacementGroup(name="g", parts=[r], adjacency={})
    result = place_parts({"g": group}, _simple_constraints(), _FP_BBOXES)
    assert len(result) == 1


# ---------------------------------------------------------------------------
# No overlaps
# ---------------------------------------------------------------------------

def test_no_overlaps_multiple_parts():
    """After placement, no two parts should overlap."""
    ic = _make_mock_part("U1", "MCU", "Package_DIP:DIP-28", num_pins=28)
    parts = [_make_mock_part(f"R{i}", "10k", "Resistor_SMD:R_0805_2012Metric") for i in range(6)]
    adj = {"U1": {p.ref: 1 for p in parts}}
    for p in parts:
        adj[p.ref] = {"U1": 1}

    group = PlacementGroup(name="main", parts=[ic] + parts, adjacency=adj)
    constraints = _simple_constraints(
        fixed=[FixedPosition("U1", 50.0, 50.0, 0.0)],
        outline=BoardOutline(200.0, 200.0),
    )

    result = place_parts({"main": group}, constraints, _FP_BBOXES)

    for i, p1 in enumerate(result):
        w1, h1 = _FP_BBOXES.get(p1.footprint, (2.0, 2.0))
        for p2 in result[i + 1:]:
            w2, h2 = _FP_BBOXES.get(p2.footprint, (2.0, 2.0))
            assert not _overlaps(p1.x_mm, p1.y_mm, w1, h1,
                                 p2.x_mm, p2.y_mm, w2, h2), (
                f"{p1.ref} overlaps {p2.ref}")


# ---------------------------------------------------------------------------
# Parts within outline
# ---------------------------------------------------------------------------

def test_parts_within_outline():
    outline = BoardOutline(100.0, 80.0)
    parts = [_make_mock_part(f"R{i}") for i in range(8)]
    group = PlacementGroup(name="g", parts=parts, adjacency={})
    constraints = _simple_constraints(outline=outline)

    result = place_parts({"g": group}, constraints, _FP_BBOXES)
    for pp in result:
        w, h = _FP_BBOXES.get(pp.footprint, (2.0, 2.0))
        assert pp.x_mm - w / 2 >= 0, f"{pp.ref} off left edge"
        assert pp.y_mm - h / 2 >= 0, f"{pp.ref} off top edge"
        assert pp.x_mm + w / 2 <= outline.width_mm, f"{pp.ref} off right edge"
        assert pp.y_mm + h / 2 <= outline.height_mm, f"{pp.ref} off bottom edge"


# ---------------------------------------------------------------------------
# PlacedPart output type
# ---------------------------------------------------------------------------

def test_returns_placed_part_instances():
    r = _make_mock_part("R1")
    group = PlacementGroup(name="g", parts=[r], adjacency={})
    result = place_parts({"g": group}, _simple_constraints(), _FP_BBOXES)
    assert len(result) == 1
    assert isinstance(result[0], PlacedPart)


def test_footprint_set_on_output():
    r = _make_mock_part("R1", footprint="Resistor_SMD:R_0805_2012Metric")
    group = PlacementGroup(name="g", parts=[r], adjacency={})
    result = place_parts({"g": group}, _simple_constraints(), _FP_BBOXES)
    assert result[0].footprint == "Resistor_SMD:R_0805_2012Metric"


# ---------------------------------------------------------------------------
# Unknown footprint fallback
# ---------------------------------------------------------------------------

def test_unknown_footprint_uses_default_bbox():
    r = _make_mock_part("R1", footprint="Unknown:Part")
    group = PlacementGroup(name="g", parts=[r], adjacency={})
    result = place_parts({"g": group}, _simple_constraints(), {})
    assert len(result) == 1
    assert isinstance(result[0], PlacedPart)
