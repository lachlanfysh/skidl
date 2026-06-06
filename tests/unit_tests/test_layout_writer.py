from __future__ import annotations

import os
import pytest
from simp_sexp import Sexp

from skidl.layout.writer import (
    PlacedPart,
    footprint_bbox,
    load_footprint,
    load_footprint_bboxes,
    write_kicad_pcb,
)
from skidl.layout.constraints import BoardOutline

_FP_DIRS: list[str] = []
_KICAD_FP = os.environ.get("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
_HAVE_KICAD_FP = os.path.isdir(_KICAD_FP)

requires_kicad_fp = pytest.mark.skipif(
    not _HAVE_KICAD_FP, reason="KiCad footprint libraries not installed"
)


# ---------------------------------------------------------------------------
# Minimal mock objects
# ---------------------------------------------------------------------------

class _MockNet:
    def __init__(self, name):
        self.name = name


class _MockCircuit:
    def __init__(self, nets=None):
        self._nets = nets or []
        self.parts = []

    def get_nets(self):
        return self._nets


# ---------------------------------------------------------------------------
# load_footprint
# ---------------------------------------------------------------------------

@requires_kicad_fp
def test_load_footprint_returns_sexp():
    fp = load_footprint("Resistor_SMD:R_0805_2012Metric", _FP_DIRS)
    assert isinstance(fp, Sexp)
    assert fp[0] == "footprint"


@requires_kicad_fp
def test_load_footprint_has_pads():
    fp = load_footprint("Resistor_SMD:R_0805_2012Metric", _FP_DIRS)
    pads = list(fp.search("pad"))
    assert len(pads) >= 2


def test_load_footprint_not_found_raises():
    with pytest.raises(FileNotFoundError):
        load_footprint("NonExistent:NoSuchPart", ["/tmp/no_such_dir"])


# ---------------------------------------------------------------------------
# footprint_bbox
# ---------------------------------------------------------------------------

@requires_kicad_fp
def test_footprint_bbox_positive_dimensions():
    fp = load_footprint("Resistor_SMD:R_0805_2012Metric", _FP_DIRS)
    w, h = footprint_bbox(fp)
    assert w > 0
    assert h > 0


def test_footprint_bbox_empty_returns_zero():
    fp = Sexp("(footprint NoName)")
    w, h = footprint_bbox(fp)
    assert w == 0.0
    assert h == 0.0


def test_footprint_bbox_single_pad():
    src = '(footprint "X" (pad "1" smd (at 1.0 2.0) (size 1.0 2.0)))'
    fp = Sexp(src)
    w, h = footprint_bbox(fp)
    assert w == pytest.approx(1.0)
    assert h == pytest.approx(2.0)


def test_footprint_bbox_two_pads():
    src = (
        '(footprint "X"'
        '  (pad "1" smd (at -1.0 0) (size 0.5 0.5))'
        '  (pad "2" smd (at  1.0 0) (size 0.5 0.5))'
        ")"
    )
    fp = Sexp(src)
    w, h = footprint_bbox(fp)
    # x spans from -1.25 to 1.25 → 2.5
    assert w == pytest.approx(2.5)
    assert h == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# load_footprint_bboxes
# ---------------------------------------------------------------------------

@requires_kicad_fp
def test_load_footprint_bboxes_known():
    names = {"Resistor_SMD:R_0805_2012Metric", "Capacitor_SMD:C_0201_0603Metric"}
    bboxes = load_footprint_bboxes(names, _FP_DIRS)
    for name in names:
        assert name in bboxes
        w, h = bboxes[name]
        assert w > 0 and h > 0


def test_load_footprint_bboxes_missing_skipped():
    names = {"NonExistent:Foo"}
    bboxes = load_footprint_bboxes(names, ["/tmp/no_such_dir"])
    assert "NonExistent:Foo" not in bboxes


# ---------------------------------------------------------------------------
# write_kicad_pcb
# ---------------------------------------------------------------------------

def _make_minimal_fp_lib(tmp_path: "Path") -> str:
    """Create a minimal .kicad_mod file in a temp lib dir, return lib root."""
    lib_dir = tmp_path / "TestLib.pretty"
    lib_dir.mkdir()
    mod = lib_dir / "R_Test.kicad_mod"
    mod.write_text(
        '(footprint "R_Test"\n'
        '  (layer "F.Cu")\n'
        '  (property "Reference" "REF**" (at 0 -2) (layer "F.SilkS"))\n'
        '  (property "Value" "R_Test" (at 0 2) (layer "F.Fab"))\n'
        '  (pad "1" smd (at -0.5 0) (size 0.6 1.0) (layers "F.Cu"))\n'
        '  (pad "2" smd (at  0.5 0) (size 0.6 1.0) (layers "F.Cu"))\n'
        ")\n"
    )
    return str(tmp_path)


def test_write_minimal_pcb_creates_file(tmp_path):
    lib_root = _make_minimal_fp_lib(tmp_path)
    circuit = _MockCircuit(nets=[_MockNet("VCC"), _MockNet("GND")])

    parts = [
        PlacedPart(ref="R1", x_mm=10.0, y_mm=20.0, rot_deg=0.0, footprint="TestLib:R_Test"),
        PlacedPart(ref="R2", x_mm=30.0, y_mm=20.0, rot_deg=90.0, footprint="TestLib:R_Test"),
    ]

    out = str(tmp_path / "out" / "board.kicad_pcb")
    write_kicad_pcb(parts, circuit, [lib_root], out)

    assert os.path.isfile(out)


def test_write_minimal_pcb_valid_sexp(tmp_path):
    lib_root = _make_minimal_fp_lib(tmp_path)
    circuit = _MockCircuit(nets=[_MockNet("VCC")])

    parts = [PlacedPart(ref="R1", x_mm=5.0, y_mm=5.0, rot_deg=0.0, footprint="TestLib:R_Test")]
    out = str(tmp_path / "board.kicad_pcb")
    write_kicad_pcb(parts, circuit, [lib_root], out)

    with open(out) as f:
        content = f.read()

    board = Sexp(content)
    assert board[0] == "kicad_pcb"


def test_write_minimal_pcb_footprint_count(tmp_path):
    lib_root = _make_minimal_fp_lib(tmp_path)
    circuit = _MockCircuit()

    parts = [
        PlacedPart(ref="R1", x_mm=10.0, y_mm=10.0, rot_deg=0.0, footprint="TestLib:R_Test"),
        PlacedPart(ref="R2", x_mm=20.0, y_mm=10.0, rot_deg=0.0, footprint="TestLib:R_Test"),
    ]
    out = str(tmp_path / "board.kicad_pcb")
    write_kicad_pcb(parts, circuit, [lib_root], out)

    with open(out) as f:
        board = Sexp(f.read())

    footprints = list(board.search("footprint"))
    assert len(footprints) == 2


def test_write_minimal_pcb_net_declarations(tmp_path):
    lib_root = _make_minimal_fp_lib(tmp_path)
    nets = [_MockNet("VCC"), _MockNet("GND"), _MockNet("SIG")]
    circuit = _MockCircuit(nets=nets)

    parts = [PlacedPart(ref="R1", x_mm=5.0, y_mm=5.0, rot_deg=0.0, footprint="TestLib:R_Test")]
    out = str(tmp_path / "board.kicad_pcb")
    write_kicad_pcb(parts, circuit, [lib_root], out)

    with open(out) as f:
        board = Sexp(f.read())

    net_nodes = [child for child in board if isinstance(child, list) and child[0] == "net"]
    net_names = [str(n[2]) for n in net_nodes if len(n) > 2]
    assert "VCC" in net_names
    assert "GND" in net_names
    assert "SIG" in net_names


def test_write_minimal_pcb_with_outline(tmp_path):
    lib_root = _make_minimal_fp_lib(tmp_path)
    circuit = _MockCircuit()
    parts = [PlacedPart(ref="R1", x_mm=5.0, y_mm=5.0, rot_deg=0.0, footprint="TestLib:R_Test")]
    out = str(tmp_path / "board.kicad_pcb")
    write_kicad_pcb(parts, circuit, [lib_root], out, outline=BoardOutline(100.0, 80.0))

    with open(out) as f:
        board = Sexp(f.read())

    rects = list(board.search("gr_rect"))
    assert len(rects) == 1
    end = next(c for c in rects[0] if isinstance(c, list) and c[0] == "end")
    assert float(end[1]) == pytest.approx(100.0)
    assert float(end[2]) == pytest.approx(80.0)


def test_write_missing_footprint_skipped(tmp_path):
    circuit = _MockCircuit()
    parts = [
        PlacedPart(ref="U1", x_mm=5.0, y_mm=5.0, rot_deg=0.0, footprint="NoLib:NoFP"),
    ]
    out = str(tmp_path / "board.kicad_pcb")
    write_kicad_pcb(parts, circuit, [], out, strict_missing_footprints=False)

    with open(out) as f:
        board = Sexp(f.read())

    footprints = list(board.search("footprint"))
    assert len(footprints) == 0


def test_write_missing_footprint_raises_by_default(tmp_path):
    circuit = _MockCircuit()
    parts = [
        PlacedPart(ref="U1", x_mm=5.0, y_mm=5.0, rot_deg=0.0, footprint="NoLib:NoFP"),
    ]
    out = str(tmp_path / "board.kicad_pcb")

    with pytest.raises(FileNotFoundError, match="INCOMPLETE PCB"):
        write_kicad_pcb(parts, circuit, [], out)

    assert not os.path.exists(out)


def test_write_polygon_outline_as_edge_lines(tmp_path):
    lib_root = _make_minimal_fp_lib(tmp_path)
    circuit = _MockCircuit()
    parts = [
        PlacedPart(
            ref="R1",
            x_mm=5.0,
            y_mm=5.0,
            rot_deg=0.0,
            footprint="TestLib:R_Test",
        )
    ]
    outline = BoardOutline(vertices=[(0, 0), (30, 0), (25, 20), (0, 20)])
    out = str(tmp_path / "board.kicad_pcb")

    write_kicad_pcb(parts, circuit, [lib_root], out, outline=outline)

    with open(out) as f:
        board = Sexp(f.read())

    assert list(board.search("gr_rect")) == []
    assert len(list(board.search("gr_line"))) == 4
