"""Mine placement patterns from KiCad PCB files.

The circuit oracle scores schematic/netlist similarity. This module looks at
physical layout idioms instead: panel rows/columns, pot/jack/LED spacing,
edge connector use, and broad board templates.

Usage:
    python3 -m corpus.layout_patterns corpus/sources --json
    python3 -m corpus.layout_patterns board.kicad_pcb other_board.kicad_pcb
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from simp_sexp import Sexp


@dataclass
class MinedPart:
    ref: str
    footprint: str
    value: str
    x_mm: float
    y_mm: float
    rot_deg: float
    kind: str


@dataclass
class AxisCluster:
    axis: str
    center_mm: float
    refs: list[str]
    kinds: dict[str, int]


@dataclass
class KindPattern:
    count: int
    rows: list[AxisCluster] = field(default_factory=list)
    columns: list[AxisCluster] = field(default_factory=list)
    x_pitch_mm: float | None = None
    y_pitch_mm: float | None = None


@dataclass
class BoardPattern:
    path: str
    width_mm: float | None
    height_mm: float | None
    template: str
    ui_part_count: int
    kind_counts: dict[str, int]
    kinds: dict[str, KindPattern]
    panel_rows: list[AxisCluster] = field(default_factory=list)
    panel_columns: list[AxisCluster] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _find_child(sexp, key: str):
    for child in sexp:
        if isinstance(child, list) and child and child[0] == key:
            return child
    return None


def _is_on_layer(sexp, layer_name: str) -> bool:
    layer = _find_child(sexp, "layer")
    return layer is not None and len(layer) > 1 and str(layer[1]) == layer_name


def _point(child) -> tuple[float, float] | None:
    if child is None or len(child) < 3:
        return None
    return _number(child[1]), _number(child[2])


def _outline_size(board) -> tuple[float | None, float | None]:
    for rect in board.search("gr_rect"):
        if not _is_on_layer(rect, "Edge.Cuts"):
            continue
        start = _point(_find_child(rect, "start"))
        end = _point(_find_child(rect, "end"))
        if start is None or end is None:
            continue
        return round(abs(end[0] - start[0]), 3), round(abs(end[1] - start[1]), 3)

    xs: list[float] = []
    ys: list[float] = []
    for line in board.search("gr_line"):
        if not _is_on_layer(line, "Edge.Cuts"):
            continue
        for key in ("start", "end"):
            point = _point(_find_child(line, key))
            if point is not None:
                xs.append(point[0])
                ys.append(point[1])
    if xs and ys:
        return round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3)
    return None, None


def _property(fp, name: str) -> str:
    for child in fp:
        if (
            isinstance(child, list)
            and len(child) >= 3
            and child[0] == "property"
            and str(child[1]) == name
        ):
            return str(child[2])
    return ""


def _fp_text(fp, name: str) -> str:
    for child in fp:
        if (
            isinstance(child, list)
            and len(child) >= 3
            and child[0] == "fp_text"
            and str(child[1]) == name
        ):
            return str(child[2])
    return ""


def _number(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _classify(ref: str, footprint: str, value: str) -> str:
    text = f"{ref} {footprint} {value}".lower()
    prefix = re.match(r"[a-zA-Z]+", ref or "")
    prefix = prefix.group(0).upper() if prefix else ""

    if any(token in text for token in ("thonk", "pj398", "pj301", "eurorack_jack")):
        return "panel_jack"
    if "3.5mm" in text and any(
        token in text for token in ("horizontal", "rightangle", "right_angle", "pj320")
    ):
        return "edge_audio_jack"
    if any(token in text for token in ("potentiometer", "pot_", "alpha", "bourns", "songhuei")):
        return "pot"
    if prefix == "RV":
        return "pot"
    if prefix in {"D", "LED"} or "led" in text:
        return "led"
    if prefix == "SW" or any(token in text for token in ("switch", "button", "tactile")):
        return "switch"
    if "mountinghole" in text or "mounting_hole" in text:
        return "mounting_hole"
    if any(token in text for token in ("box_header", "idc-header", "2x05", "2x5")):
        return "eurorack_power"
    if prefix == "J" or "connector" in text:
        return "connector"
    return "other"


def read_mined_parts(pcb_path: str | Path) -> list[MinedPart]:
    """Return placed footprints with basic UI/mechanical classification."""
    pcb_path = Path(pcb_path)
    board = Sexp(pcb_path.read_text(errors="replace"))
    parts: list[MinedPart] = []
    ref_counts: dict[str, int] = {}

    for fp in list(board.search("footprint")) + list(board.search("module")):
        if len(fp) < 2:
            continue
        at = _find_child(fp, "at")
        if at is None or len(at) < 3:
            continue
        ref = _property(fp, "Reference") or _fp_text(fp, "reference")
        if not ref or ref.startswith("#"):
            continue
        footprint = str(fp[1])
        value = _property(fp, "Value") or _fp_text(fp, "value")
        x = _number(at[1])
        y = _number(at[2])
        rot = _number(at[3]) if len(at) > 3 else 0.0
        if abs(x) < 0.001 and abs(y) < 0.001:
            continue
        ref_counts[ref] = ref_counts.get(ref, 0) + 1
        if ref_counts[ref] > 1:
            ref = f"{ref}#{ref_counts[ref]}"
        parts.append(
            MinedPart(
                ref=ref,
                footprint=footprint,
                value=value,
                x_mm=x,
                y_mm=y,
                rot_deg=rot,
                kind=_classify(ref, footprint, value),
            )
        )
    return parts


def _kind_counts(parts: list[MinedPart]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for part in parts:
        counts[part.kind] = counts.get(part.kind, 0) + 1
    return dict(sorted(counts.items()))


def _cluster(parts: list[MinedPart], axis: str, tolerance_mm: float) -> list[AxisCluster]:
    key = (lambda p: p.x_mm) if axis == "x" else (lambda p: p.y_mm)
    ordered = sorted(parts, key=lambda part: (key(part), part.ref))
    clusters: list[list[MinedPart]] = []
    centers: list[float] = []
    for part in ordered:
        coord = key(part)
        if not clusters or abs(coord - centers[-1]) > tolerance_mm:
            clusters.append([part])
            centers.append(coord)
            continue
        clusters[-1].append(part)
        centers[-1] = sum(key(p) for p in clusters[-1]) / len(clusters[-1])

    result = []
    for center, cluster_parts in zip(centers, clusters):
        if len(cluster_parts) < 2:
            continue
        result.append(
            AxisCluster(
                axis=axis,
                center_mm=round(center, 3),
                refs=[part.ref for part in sorted(cluster_parts, key=lambda p: p.ref)],
                kinds=_kind_counts(cluster_parts),
            )
        )
    return result


def _pitch(clusters: list[AxisCluster]) -> float | None:
    centers = sorted(cluster.center_mm for cluster in clusters)
    if len(centers) < 2:
        return None
    deltas = [round(centers[idx + 1] - centers[idx], 3) for idx in range(len(centers) - 1)]
    return round(sum(deltas) / len(deltas), 3)


def _ui_parts(parts: list[MinedPart]) -> list[MinedPart]:
    return [
        part
        for part in parts
        if part.kind in {"panel_jack", "edge_audio_jack", "pot", "led", "switch"}
    ]


def _template(width: float | None, height: float | None, parts: list[MinedPart]) -> str:
    counts = _kind_counts(parts)
    panel_count = sum(counts.get(kind, 0) for kind in ("panel_jack", "pot", "led", "switch"))
    if width and height and height >= width * 1.6 and panel_count >= 2:
        return "eurorack_or_tall_panel"
    if counts.get("edge_audio_jack", 0) >= 2:
        return "edge_audio_io"
    if counts.get("pot", 0) + counts.get("switch", 0) >= 2:
        return "front_panel_controls"
    if counts.get("connector", 0) >= 2:
        return "connector_breakout"
    return "general_pcb"


def analyze_pcb(pcb_path: str | Path, tolerance_mm: float = 2.0) -> BoardPattern:
    """Analyze a KiCad PCB and return row/column/layout pattern hints."""
    pcb_path = Path(pcb_path)
    board = Sexp(pcb_path.read_text(errors="replace"))
    parts = read_mined_parts(pcb_path)
    width, height = _outline_size(board)
    ui = _ui_parts(parts)
    counts = _kind_counts(parts)

    kinds: dict[str, KindPattern] = {}
    for kind in sorted({part.kind for part in ui}):
        kind_parts = [part for part in ui if part.kind == kind]
        rows = _cluster(kind_parts, "y", tolerance_mm)
        columns = _cluster(kind_parts, "x", tolerance_mm)
        kinds[kind] = KindPattern(
            count=len(kind_parts),
            rows=rows,
            columns=columns,
            x_pitch_mm=_pitch(columns),
            y_pitch_mm=_pitch(rows),
        )

    panel_parts = [
        part for part in ui if part.kind in {"panel_jack", "pot", "led", "switch"}
    ]
    panel_rows = _cluster(panel_parts, "y", tolerance_mm)
    panel_columns = _cluster(panel_parts, "x", tolerance_mm)

    notes = []
    if panel_parts and not panel_rows and not panel_columns:
        notes.append("panel UI exists but no repeated rows/columns were detected")
    if counts.get("eurorack_power", 0) and panel_parts:
        notes.append("eurorack power and panel UI present")

    return BoardPattern(
        path=str(pcb_path),
        width_mm=width,
        height_mm=height,
        template=_template(width, height, parts),
        ui_part_count=len(ui),
        kind_counts=counts,
        kinds=kinds,
        panel_rows=panel_rows,
        panel_columns=panel_columns,
        notes=notes,
    )


def _paths_from_args(paths: list[Path]) -> list[Path]:
    pcbs: list[Path] = []
    for path in paths:
        if path.is_dir():
            pcbs.extend(sorted(path.rglob("*.kicad_pcb")))
        elif path.suffix == ".kicad_pcb":
            pcbs.append(path)
    return pcbs


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", type=Path, help="KiCad PCB files or directories")
    parser.add_argument("--tolerance-mm", type=float, default=2.0)
    parser.add_argument("--json", action="store_true", help="Emit full JSON")
    parser.add_argument("--limit", type=int, default=0, help="Limit number of boards")
    args = parser.parse_args(argv)

    pcbs = _paths_from_args(args.paths)
    if args.limit:
        pcbs = pcbs[: args.limit]
    patterns = [analyze_pcb(path, tolerance_mm=args.tolerance_mm) for path in pcbs]

    if args.json:
        print(json.dumps([asdict(pattern) for pattern in patterns], indent=2))
        return 0

    for pattern in patterns:
        size = (
            f"{pattern.width_mm}x{pattern.height_mm}mm"
            if pattern.width_mm and pattern.height_mm
            else "unknown size"
        )
        print(f"{pattern.path}: {pattern.template}, {size}, UI={pattern.ui_part_count}")
        for kind, kp in pattern.kinds.items():
            print(
                f"  {kind}: {kp.count}, rows={len(kp.rows)}, "
                f"columns={len(kp.columns)}, x_pitch={kp.x_pitch_mm}, y_pitch={kp.y_pitch_mm}"
            )
        for note in pattern.notes:
            print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
