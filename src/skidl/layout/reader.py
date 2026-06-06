from __future__ import annotations

from simp_sexp import Sexp

from .constraints import FixedPosition

_ORIGIN_EPSILON = 0.001


def _find_child(sexp, key: str):
    for child in sexp:
        if isinstance(child, list) and len(child) > 0 and child[0] == key:
            return child
    return None


def _find_children(sexp, key: str):
    return [child for child in sexp if isinstance(child, list) and len(child) > 0 and child[0] == key]


def _fp_reference(fp_sexp) -> str | None:
    for child in fp_sexp:
        if (
            isinstance(child, list)
            and len(child) >= 3
            and child[0] == "property"
            and child[1] == "Reference"
        ):
            return str(child[2])
    return None


def read_placed_positions(pcb_path: str) -> list:
    """Parse .kicad_pcb, extract positions of non-origin footprints as FixedPosition objects.

    Parts at (at 0 0) or (at 0 0 0) are treated as "unplaced" (KiCad dumps them at origin).
    Returns only parts that have been deliberately placed (not at origin).
    """
    with open(pcb_path) as f:
        board = Sexp(f.read())

    result = []
    for fp in board.search("footprint"):
        at = _find_child(fp, "at")
        if at is None:
            continue

        x = float(at[1])
        y = float(at[2])
        angle = float(at[3]) if len(at) > 3 else 0.0

        if abs(x) < _ORIGIN_EPSILON and abs(y) < _ORIGIN_EPSILON:
            continue

        ref = _fp_reference(fp)
        if ref is None:
            continue

        result.append(FixedPosition(ref=ref, x_mm=x, y_mm=y, rot_deg=angle))

    return result


def read_footprint_bboxes(pcb_path: str) -> dict:
    """Extract footprint bounding boxes from placed parts in existing board.

    Returns dict mapping footprint_name → (width_mm, height_mm).
    Bounding box is computed from pad extents in each footprint.
    """
    with open(pcb_path) as f:
        board = Sexp(f.read())

    bboxes: dict[str, tuple[float, float]] = {}

    for fp in board.search("footprint"):
        fp_name = str(fp[1])
        if fp_name in bboxes:
            continue

        pads = _find_children(fp, "pad")
        if not pads:
            continue

        xs: list[float] = []
        ys: list[float] = []

        for pad in pads:
            pad_at = _find_child(pad, "at")
            pad_size = _find_child(pad, "size")
            if pad_at is None or pad_size is None:
                continue

            px, py = float(pad_at[1]), float(pad_at[2])
            pw, ph = float(pad_size[1]), float(pad_size[2])

            xs.extend([px - pw / 2, px + pw / 2])
            ys.extend([py - ph / 2, py + ph / 2])

        if xs and ys:
            bboxes[fp_name] = (max(xs) - min(xs), max(ys) - min(ys))

    return bboxes
