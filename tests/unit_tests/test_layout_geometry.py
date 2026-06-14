from __future__ import annotations

import pytest
from simp_sexp import Sexp

from skidl.layout.geometry import footprint_geometry_from_sexp
from skidl.layout.writer import PlacedPart


def test_footprint_geometry_extracts_pads_bounds_and_pad_nets():
    footprint = Sexp(
        """
(footprint "Demo:USB"
  (pad "1" smd rect (at -2 0 90) (size 1 2) (layers "F.Cu" "F.Paste") (net 1 "VBUS"))
  (pad "2" smd rect (at 2 0) (size 1 2) (layers "F.Cu") (net 2 "GND"))
  (fp_rect (start -3 -2) (end 3 2) (layer "F.CrtYd"))
  (fp_line (start -2 -1) (end 2 -1) (layer "F.Fab"))
)
"""
    )

    geometry = footprint_geometry_from_sexp("Demo:USB", footprint)

    assert geometry.width_mm == pytest.approx(6.0)
    assert geometry.height_mm == pytest.approx(4.0)
    assert [pad.net_name for pad in geometry.pads] == ["VBUS", "GND"]
    assert geometry.pad_side_counts()["left"] == 1
    assert geometry.pad_side_counts()["right"] == 1


def test_footprint_geometry_transforms_bounds_and_pad_centers():
    footprint = Sexp(
        """
(footprint "Demo:Rect"
  (pad "1" smd rect (at -2 0) (size 1 1) (layers "F.Cu"))
  (pad "2" smd rect (at 2 0) (size 1 1) (layers "F.Cu"))
  (fp_rect (start -3 -1) (end 3 1) (layer "F.CrtYd"))
)
"""
    )
    geometry = footprint_geometry_from_sexp("Demo:Rect", footprint)
    placed = PlacedPart("U1", 10.0, 20.0, 90.0, "Demo:Rect")

    assert geometry.transformed_bounds(placed) == pytest.approx(
        (9.0, 17.0, 11.0, 23.0)
    )
    assert geometry.pad_world_centers(placed)["1"] == pytest.approx((10.0, 22.0))
    assert geometry.pad_world_centers(placed)["2"] == pytest.approx((10.0, 18.0))
