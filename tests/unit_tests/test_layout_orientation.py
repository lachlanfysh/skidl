from __future__ import annotations

from skidl.layout.constraints import FaceEdgeConstraint, LayoutConstraints
from skidl.layout.geometry import FootprintGeometry, PadGeometry
from skidl.layout.orientation import refine_orientations
from skidl.layout.writer import PlacedPart


class _Net:
    def __init__(self, name):
        self.name = name
        self._pins = []

    def get_pins(self):
        return self._pins


class _Pin:
    def __init__(self, part, num, net):
        self.part = part
        self.num = str(num)
        self.net = net
        net._pins.append(self)


class _Part:
    def __init__(self, ref, footprint, pins):
        self.ref = ref
        self.footprint = footprint
        self.pins = []
        for num, net in pins:
            self.pins.append(_Pin(self, num, net))

    def __len__(self):
        return len(self.pins)


class _Circuit:
    def __init__(self, parts, nets):
        self.parts = parts
        self._nets = nets

    def get_nets(self):
        return self._nets


def test_refine_orientations_rotates_pad_toward_connected_neighbor():
    sig = _Net("SIG")
    u1 = _Part("U1", "Pkg:Directional", [(1, sig)])
    j1 = _Part("J1", "Pkg:Other", [(1, sig)])
    circuit = _Circuit([u1, j1], [sig])
    placed = [
        PlacedPart("U1", 0.0, 0.0, 0.0, "Pkg:Directional"),
        PlacedPart("J1", 10.0, 0.0, 0.0, "Pkg:Other"),
    ]
    geometries = {
        "Pkg:Directional": FootprintGeometry(
            footprint="Pkg:Directional",
            pads=[PadGeometry("1", 0.0, -1.0, 0.5, 0.5)],
        )
    }

    result = refine_orientations(placed, circuit, geometries)
    refined = {part.ref: part for part in result.placed_parts}

    assert refined["U1"].rot_deg == 270.0
    assert "pad/net pressure" in result.ref_reasons["U1"][0]


def test_refine_orientations_skips_face_edge_constrained_part():
    sig = _Net("SIG")
    u1 = _Part("U1", "Pkg:Directional", [(1, sig)])
    j1 = _Part("J1", "Pkg:Other", [(1, sig)])
    circuit = _Circuit([u1, j1], [sig])
    placed = [
        PlacedPart("U1", 0.0, 0.0, 0.0, "Pkg:Directional"),
        PlacedPart("J1", 10.0, 0.0, 0.0, "Pkg:Other"),
    ]
    geometries = {
        "Pkg:Directional": FootprintGeometry(
            footprint="Pkg:Directional",
            pads=[PadGeometry("1", 0.0, -1.0, 0.5, 0.5)],
        )
    }
    constraints = LayoutConstraints(
        face_edges=[FaceEdgeConstraint("U1", "right")]
    )

    result = refine_orientations(placed, circuit, geometries, constraints=constraints)
    refined = {part.ref: part for part in result.placed_parts}

    assert refined["U1"].rot_deg == 0.0
    assert result.ref_reasons == {}
