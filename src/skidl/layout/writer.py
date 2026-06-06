from __future__ import annotations

import copy
import os
import uuid
from dataclasses import dataclass

from simp_sexp import Sexp

from .constraints import BoardOutline

_NAMESPACE_UUID = uuid.UUID("7026fcc6-e1a0-409e-aaf4-6a17ea82654f")

_LAYERS = [
    (0,  "F.Cu",       "signal"),
    (31, "B.Cu",       "signal"),
    (32, "B.Adhes",    "user",   "B.Adhesive"),
    (33, "F.Adhes",    "user",   "F.Adhesive"),
    (34, "B.Paste",    "user"),
    (35, "F.Paste",    "user"),
    (36, "B.SilkS",    "user",   "B.Silkscreen"),
    (37, "F.SilkS",    "user",   "F.Silkscreen"),
    (38, "B.Mask",     "user"),
    (39, "F.Mask",     "user"),
    (40, "Dwgs.User",  "user",   "User.Drawings"),
    (41, "Cmts.User",  "user",   "User.Comments"),
    (44, "Edge.Cuts",  "user"),
]


@dataclass
class PlacedPart:
    ref: str
    x_mm: float
    y_mm: float
    rot_deg: float
    footprint: str  # "Library:Name" format


def _part_uuid(part) -> str:
    return str(uuid.uuid5(_NAMESPACE_UUID, part.hiername))


def _sheet_uuid(level_name: str) -> str:
    return str(uuid.uuid5(_NAMESPACE_UUID, level_name))


def _kiid_path(part) -> str:
    hierarchy = part.hiertuple
    if len(hierarchy) <= 1:
        return f"/{_part_uuid(part)}"
    sheet_uuids = [_sheet_uuid(level) for level in hierarchy[1:]]
    return "/" + "/".join(sheet_uuids) + "/" + _part_uuid(part)


def _fp_file_path(fp_name: str, fp_lib_dirs: list[str]) -> str:
    lib, name = fp_name.split(":", 1)
    lib_dir = f"{lib}.pretty"
    file_name = f"{name}.kicad_mod"

    search_dirs = list(fp_lib_dirs)
    env_dir = os.environ.get("KICAD9_FOOTPRINT_DIR", "/usr/share/kicad/footprints")
    if env_dir:
        search_dirs.append(env_dir)

    for base in search_dirs:
        candidate = os.path.join(base, lib_dir, file_name)
        if os.path.isfile(candidate):
            return candidate

    raise FileNotFoundError(f"Footprint not found: {fp_name} (searched {search_dirs})")


def load_footprint(fp_name: str, fp_lib_dirs: list[str]) -> Sexp:
    """Load a .kicad_mod footprint file and return its S-expression."""
    path = _fp_file_path(fp_name, fp_lib_dirs)
    with open(path) as f:
        return Sexp(f.read())


def _find_child(sexp, key: str):
    for child in sexp:
        if isinstance(child, list) and len(child) > 0 and child[0] == key:
            return child
    return None


def footprint_bbox(fp_sexp: Sexp) -> tuple[float, float]:
    """Compute bounding box (width_mm, height_mm) from a footprint's pads."""
    xs: list[float] = []
    ys: list[float] = []

    for pad in fp_sexp.search("pad"):
        pad_at = _find_child(pad, "at")
        pad_size = _find_child(pad, "size")
        if pad_at is None or pad_size is None:
            continue
        px, py = float(pad_at[1]), float(pad_at[2])
        pw, ph = float(pad_size[1]), float(pad_size[2])
        xs.extend([px - pw / 2, px + pw / 2])
        ys.extend([py - ph / 2, py + ph / 2])

    if not xs:
        return (0.0, 0.0)
    return (max(xs) - min(xs), max(ys) - min(ys))


def load_footprint_bboxes(fp_names: set[str], fp_lib_dirs: list[str]) -> dict[str, tuple[float, float]]:
    """Load bounding boxes for a set of footprint names."""
    result: dict[str, tuple[float, float]] = {}
    for name in fp_names:
        try:
            fp_sexp = load_footprint(name, fp_lib_dirs)
            result[name] = footprint_bbox(fp_sexp)
        except FileNotFoundError:
            pass
    return result


def _build_net_map(circuit) -> tuple[dict[str, int], list]:
    """Return (name→code, ordered_nets) where code starts at 1."""
    nets = circuit.get_nets()
    net_map = {n.name: i + 1 for i, n in enumerate(nets)}
    return net_map, nets


def _place_footprint(fp_sexp: Sexp, pp: PlacedPart, kiid: str, net_map: dict[str, int], part) -> Sexp:
    fp = copy.deepcopy(fp_sexp)

    at_val = [pp.x_mm, pp.y_mm]
    if pp.rot_deg:
        at_val.append(pp.rot_deg)
    fp.insert(2, Sexp(["at"] + at_val))
    fp.insert(3, Sexp(["path", kiid]))

    for i, child in enumerate(fp):
        if not (isinstance(child, list) and len(child) >= 3 and child[0] == "property"):
            continue
        if child[1] == "Reference":
            fp[i][2] = pp.ref
        elif child[1] == "Value":
            fp[i][2] = getattr(part, "value", "") or pp.ref

    for pad in fp.search("pad"):
        pad_num = str(pad[1])
        net_name = None
        if part is not None:
            try:
                pins = part[pad_num]
                if pins:
                    pin = pins[0] if isinstance(pins, list) else pins
                    n = getattr(pin, "net", None)
                    if n is not None:
                        net_name = getattr(n, "name", None)
            except Exception:
                pass
        if net_name and net_name in net_map:
            pad.append(Sexp(["net", net_map[net_name], net_name]))

    return fp


def _find_circuit_part(circuit, ref: str):
    for part in circuit.parts:
        if getattr(part, "ref", None) == ref:
            return part
    return None


def write_kicad_pcb(
    placed_parts: list,
    circuit,
    fp_lib_dirs: list[str],
    output_path: str,
    outline: BoardOutline = None,
    version: int = 20240108,
):
    """Write a complete .kicad_pcb file."""
    net_map, nets = _build_net_map(circuit)

    board = Sexp(["kicad_pcb"])
    board.append(Sexp(["version", version]))
    board.append(Sexp(["generator", "skidl"]))
    board.append(Sexp(["general", ["thickness", 1.6]]))

    layers = Sexp(["layers"])
    for entry in _LAYERS:
        row = Sexp([entry[0], entry[1], entry[2]])
        if len(entry) == 4:
            row.append(entry[3])
        layers.append(row)
    board.append(layers)

    board.append(Sexp(["setup",
        ["pad_to_mask_clearance", 0],
        ["allow_soldermask_bridges_in_footprints", "no"],
    ]))

    board.append(Sexp(["net", 0, ""]))
    for net in nets:
        board.append(Sexp(["net", net_map[net.name], net.name]))

    for pp in placed_parts:
        try:
            fp_sexp = load_footprint(pp.footprint, fp_lib_dirs)
        except FileNotFoundError:
            continue

        part = _find_circuit_part(circuit, pp.ref)
        kiid = _kiid_path(part) if part is not None else f"/{uuid.uuid4()}"

        fp = _place_footprint(fp_sexp, pp, kiid, net_map, part)
        board.append(fp)

    if outline is not None:
        board.append(Sexp([
            "gr_rect",
            ["start", 0, 0],
            ["end", outline.width_mm, outline.height_mm],
            ["layer", "Edge.Cuts"],
            ["stroke", ["width", 0.1]],
        ]))

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w") as f:
        f.write(board.to_str())
        f.write("\n")
