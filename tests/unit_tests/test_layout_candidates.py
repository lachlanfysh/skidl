from __future__ import annotations

import pytest

from skidl.layout.candidates import generate_placement_candidates
from skidl.layout.constraints import BoardOutline, EdgeAnchor, LayoutConstraints
from skidl.layout.hierarchy import PlacementGroup
from skidl.layout.intent import PlacementIntentPlan


class _Part:
    def __init__(self, ref, foot, pins=4):
        self.ref = ref
        self.foot = foot
        self.value = ""
        self.name = ""
        self.pins = [object() for _ in range(pins)]

    def __len__(self):
        return len(self.pins)


def test_generate_placement_candidates_is_deterministic_and_named():
    connector = _Part("J1", "Connector:USB", pins=16)
    group = PlacementGroup(name="", parts=[connector], adjacency={})
    constraints = LayoutConstraints(outline=BoardOutline(50.0, 30.0))
    intent = PlacementIntentPlan(
        edge_anchors=[EdgeAnchor("J1", "bottom", offset_mm=25.0)]
    )

    candidates = generate_placement_candidates(
        {None: group},
        constraints,
        {"Connector:USB": (10.0, 5.0)},
        intent_plan=intent,
    )

    assert [candidate.name for candidate in candidates[:4]] == [
        "baseline",
        "connector_edge_first",
        "power_first",
        "cluster_first",
    ]
    baseline_j1 = candidates[0].placed_parts[0]
    edge_j1 = candidates[1].placed_parts[0]
    assert baseline_j1.y_mm != pytest.approx(edge_j1.y_mm)
    assert edge_j1.y_mm + 2.5 == pytest.approx(30.0)
