#!/usr/bin/env python3
"""First-pass PCB arranger (PoC).

Implements DESIGN.md (with Section 12 BINDING decisions). Turns the post-import
origin blob into legible clusters: group by schematic sheet (from the netlist),
snap support passives to the pad they serve, and shelf-pack subsystems into a
two-level grid. Honours a deterministic pre-map and never moves locked parts.

The .kicad_netlist supplies connectivity + sheet grouping (joined by reference).
The .kicad_pcb supplies footprint geometry (courtyard/bbox) and is the write-back
target only. GetSheetname()/pad.GetNetname() are NOT relied upon.

Usage:
  arrange.py board.kicad_pcb [--netlist X.kicad_netlist] [--premap map.json]
      [--out out.kicad_pcb] [--dry-run] [--force-all|--respect-placed]
      [--report report.json]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass, field

import pcbnew

from netlist import parse_netlist

# ----------------------------------------------------------------------------
# Config defaults (DESIGN.md S12.3). Overridable constants. mm everywhere.
# ----------------------------------------------------------------------------
DECAP_GAP = 0.7
PULL_GAP = 1.0
BULK_GAP = 1.5
XTAL_GAP = 1.0
SERIES_MAX_OFFSET = 8.0

L1_PAD = 2.0     # inter-block gutter within a sheet (Level 1)
L2_PAD = 8.0     # gutter between sheet blocks (Level 2), larger so they separate
TIER_GAP = 5.0   # clearance between Tier-1 keep-clear zone and Tier-2 grid
PASSIVE_HALO = 4.0   # reserve around an anchor for its hugging passives
TEXT_GUTTER = 0.6    # extra de-collision spacing so silk/ref labels don't touch

ANCHOR_PAD_MIN = 3
PASSIVE_PREFIXES = {"R", "C", "L"}
ANCHOR_PREFIXES = {"U", "J", "Q", "Y", "SW", "RV", "K", "M"}

# Power-rail name regex (used to detect decap/pull roles). Covers GND/AGND,
# VCC/VDD/VSS/VEE families, named rails (VBUS/VBAT/VIN/VOUT/VSYS/VREF/...),
# and numeric rails (+3V3, +5V, -12V, 3V3, 5V).
POWER_RE = re.compile(
    r"^(GND[A-Z0-9]*|[AD]GND|VCC.*|VDD.*|VSS.*|VEE.*|"
    r"V(BUS|BAT|IN|OUT|SYS|REF|DDA?|DD3V3|33|50)\w*|"
    r"[+-]?\d+V\d*|[+-]\d+V\d*|\+?\d+VA?)$",
    re.IGNORECASE,
)

BULK_VALUE_UF = 10.0
DECAP_VALUE_UF = 1.0


# ----------------------------------------------------------------------------
# Internal data model (DESIGN.md S2): thin struct decoupled from pcbnew.
# ----------------------------------------------------------------------------
@dataclass
class FP:
    ref: str
    value: str
    prefix: str
    pad_count: int
    is_passive: bool
    is_anchor: bool
    sheet_key: str
    bbox_mm: tuple          # (w, h) at current orientation
    pos_mm: tuple           # current (x, y) center
    orient_deg: float
    locked: bool
    placed: bool            # already hand-placed / tier-1 fixed (don't grid-flow)
    movable: bool           # this run is allowed to move it
    # net_name -> [(pad_pos_mm, pad_name)] for this footprint's own pads
    pin_pads: dict = field(default_factory=dict)
    handle: object = None   # live pcbnew.FOOTPRINT (write-back only)
    base_orient_deg: float = 0.0  # orientation at which bbox_mm was measured
    parent_ref: str = None  # anchor this passive hugs (exempt from de-collision)

    @property
    def w(self):
        # bbox_mm is measured at base_orient_deg; swap W/H for odd-90 deltas.
        if int(round((self.orient_deg - self.base_orient_deg) / 90.0)) % 2:
            return self.bbox_mm[1]
        return self.bbox_mm[0]

    @property
    def h(self):
        if int(round((self.orient_deg - self.base_orient_deg) / 90.0)) % 2:
            return self.bbox_mm[0]
        return self.bbox_mm[1]


def prefix_of(ref):
    m = re.match(r"^([A-Za-z#]+)", ref)
    if not m:
        return ref
    return m.group(1).lstrip("#")


def parse_value_uf(value):
    """Best-effort capacitor value -> microfarads. None if unparseable."""
    if not value:
        return None
    v = value.strip().lower().replace("µ", "u")
    m = re.match(r"([\d.]+)\s*(p|n|u|m|f)?f?", v)
    if not m:
        return None
    try:
        num = float(m.group(1))
    except ValueError:
        return None
    unit = m.group(2)
    scale = {"p": 1e-6, "n": 1e-3, "u": 1.0, "m": 1e3, "f": 1e-6}
    if unit in scale:
        # 'f' alone ambiguous; treat trailing 'f' as farad only if huge -> ignore
        return num * scale.get(unit, 1.0)
    return None


# ----------------------------------------------------------------------------
# Geometry helpers
# ----------------------------------------------------------------------------
def courtyard_or_bbox_mm(fp_handle):
    """(w, h) in mm. Prefer courtyard, fall back to no-text/no-invisible bbox."""
    try:
        cy = fp_handle.GetCourtyard(pcbnew.F_CrtYd)
        if cy and cy.OutlineCount() > 0:
            bb = cy.BBox()
            w, h = pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight())
            if w > 0 and h > 0:
                return (w, h)
    except Exception:
        pass
    bb = fp_handle.GetBoundingBox(False, False)  # no text, no invisible (S12.7)
    return (pcbnew.ToMM(bb.GetWidth()), pcbnew.ToMM(bb.GetHeight()))


def is_power_net(name):
    if not name:
        return False
    return bool(POWER_RE.match(name.strip()))


def is_gnd_net(name):
    return bool(name) and bool(re.match(r"^[AD]?GND", name.strip(), re.IGNORECASE))


# ----------------------------------------------------------------------------
# Load + Classify (DESIGN.md S3) with netlist join (S12.1)
# ----------------------------------------------------------------------------
def load_and_classify(board, nl, force_all, respect_placed, report):
    fps = {}
    matched = 0
    board_refs = []
    # Global net index from NETLIST: net_name -> [(ref, pin, pad_pos_mm)]
    # pad positions filled after we read pad geometry.
    net_index = {}

    # First pass: build FP records, read geometry + pad positions.
    pad_pos = {}  # (ref, pin) -> (x, y) mm  -- from the BOARD geometry
    for h in board.GetFootprints():
        ref = h.GetReference()
        board_refs.append(ref)
        nlc = nl.comps.get(ref)
        if nlc is not None:
            matched += 1
        value = (nlc.value if nlc else "") or h.GetValue()
        sheet = nlc.sheet if nlc else "__unmatched__"
        prefix = prefix_of(ref)
        pad_count = h.GetPadCount()
        is_passive = prefix in PASSIVE_PREFIXES and pad_count == 2
        is_anchor = (prefix in ANCHOR_PREFIXES or pad_count >= ANCHOR_PAD_MIN) and not is_passive

        pos = h.GetPosition()
        pos_mm = (pcbnew.ToMM(pos.x), pcbnew.ToMM(pos.y))
        bbox = courtyard_or_bbox_mm(h)

        fp = FP(
            ref=ref, value=value, prefix=prefix, pad_count=pad_count,
            is_passive=is_passive, is_anchor=is_anchor, sheet_key=sheet,
            bbox_mm=bbox, pos_mm=pos_mm, orient_deg=h.GetOrientationDegrees(),
            locked=h.IsLocked(), placed=False, movable=False, handle=h,
            base_orient_deg=h.GetOrientationDegrees(),
        )
        fps[ref] = fp

        for pad in h.Pads():
            pn = pad.GetNumber()
            pp = pad.GetPosition()
            ppmm = (pcbnew.ToMM(pp.x), pcbnew.ToMM(pp.y))
            # offset of the pad from the footprint center, in the footprint's
            # own (current/base) frame — lets us recompute the pad position
            # after the anchor is moved/rotated by the placer.
            off = (ppmm[0] - pos_mm[0], ppmm[1] - pos_mm[1])
            pad_pos[(ref, pn)] = (ppmm, off)

    # Build net index from the NETLIST connectivity, attaching board pad
    # geometry: (ref, pin, abs_pad_pos_mm, pad_offset_from_center_mm).
    for net in nl.nets:
        entries = []
        for (ref, pin) in net.nodes:
            if ref in fps and (ref, pin) in pad_pos:
                ppmm, off = pad_pos[(ref, pin)]
                entries.append((ref, pin, ppmm, off))
        if entries:
            net_index[net.name] = entries

    # Per-footprint pin_pads slice: net_name -> [(pad_pos_mm, pad_name)]
    for name, entries in net_index.items():
        for (ref, pin, pp, off) in entries:
            fps[ref].pin_pads.setdefault(name, []).append((pp, pin))

    # Movability (S12.4): --force-all default => every unlocked fp movable.
    for fp in fps.values():
        if fp.locked:
            fp.movable = False
            fp.placed = True
            continue
        if respect_placed:
            fp.movable = blob_movable(fp, fps, board)
            fp.placed = not fp.movable
        else:
            fp.movable = True  # force-all
            fp.placed = False

    report["join"] = {
        "board_footprints": len(board_refs),
        "netlist_components": len(nl.comps),
        "matched": matched,
        "join_rate": round(matched / max(1, len(board_refs)), 4),
        "unmatched_board_refs": sorted(r for r in board_refs if r not in nl.comps),
    }
    report["net_index_nets"] = len(net_index)
    return fps, net_index


def _bbox_overlap(a, b, margin=0.0):
    ax, ay = a.pos_mm
    bx, by = b.pos_mm
    return (abs(ax - bx) * 2 < (a.w + b.w + 2 * margin)) and (
        abs(ay - by) * 2 < (a.h + b.h + 2 * margin)
    )


def blob_movable(fp, fps, board):
    """--respect-placed heuristic (S3.3): movable iff unlocked AND
    (overlaps another fp's bbox OR lies outside the Edge.Cuts outline).

    Deviation from S3.3's literal "no Edge.Cuts -> always movable": when there
    is no outline we fall back to the overlap-density signal (S3.3 candidate
    #4) so that an already-arranged (non-overlapping) board is recognised as
    placed. This is what makes --respect-placed idempotent on outline-less
    boards; without it the heuristic could never converge. Flagged for review.
    """
    edges = board.GetBoardEdgesBoundingBox()
    has_edges = edges.GetWidth() > 0 and edges.GetHeight() > 0
    if has_edges:
        cx, cy = fp.pos_mm
        inside = (
            pcbnew.ToMM(edges.GetX()) <= cx <= pcbnew.ToMM(edges.GetX() + edges.GetWidth())
            and pcbnew.ToMM(edges.GetY()) <= cy <= pcbnew.ToMM(edges.GetY() + edges.GetHeight())
        )
        if not inside:
            return True
    # Overlap-density signal (always evaluated; sole signal when no outline).
    # A small passive sitting inside a much larger anchor's courtyard is a
    # legitimate hug (decap on an IC), NOT blob density — so don't treat a
    # passive's overlap with a >=4x-larger anchor as a "move me" signal.
    for other in fps.values():
        if other is fp:
            continue
        if not _bbox_overlap(fp, other):
            continue
        if fp.is_passive and other.is_anchor:
            if other.w * other.h >= 4.0 * fp.w * fp.h:
                continue  # intentional hug, not blob
        return True
    return False


# ----------------------------------------------------------------------------
# Stage 0 — deterministic pre-map (DESIGN.md S8)
# ----------------------------------------------------------------------------
def apply_premap(fps, premap, report):
    if not premap:
        return [], None
    fixed = premap.get("fixed", {})
    lock_premapped = premap.get("lock_premapped", True)
    placed_refs = []
    positions = {}
    # conflict detection
    seen = {}
    conflicts = []
    for ref, spec in fixed.items():
        key = (round(spec["x"], 3), round(spec["y"], 3))
        if key in seen:
            conflicts.append((ref, seen[key]))
        seen[key] = ref
    if conflicts:
        report["premap_conflicts"] = conflicts
        raise SystemExit(f"Pre-map conflict: parts share coordinates: {conflicts}")

    for ref, spec in fixed.items():
        fp = fps.get(ref)
        if fp is None:
            report.setdefault("premap_missing", []).append(ref)
            continue
        x, y = float(spec["x"]), float(spec["y"])
        rot = float(spec.get("rot", 0))
        fp.pos_mm = (x, y)
        fp.orient_deg = rot
        fp.placed = True
        fp.movable = False  # tier-1 parts are fixed, not grid-flowed
        if lock_premapped:
            fp.locked = True
        placed_refs.append(ref)
        positions[ref] = (x, y, rot)

    keepout_extra = float(premap.get("keepout_extra_mm", 3.0))
    report["premap_applied"] = placed_refs
    return placed_refs, keepout_extra


# ----------------------------------------------------------------------------
# Stage A — group by schematic sheet (netlist sheetpath, S12.1)
# ----------------------------------------------------------------------------
TP_REHOME_MISC = "__tp_misc__"


def rehome_test_points(fps, net_index, report):
    """Feature A: re-home single-pad probes (prefix TP, 1 pad) from their
    declared netlist sheet to the group that OWNS the net the probe taps.

    Owner selection: the sheet of the net's highest-pad-count anchor owner;
    if the net has no anchor owner, the highest-pad-count *any* other owner;
    if the net is truly isolated (the TP is its only node), drop it into a
    small misc group so it doesn't form a spurious TP block.

    Mutates fp.sheet_key in place. Records a re-home tally in the report.
    """
    rehomed = {}          # tp_ref -> destination sheet_key
    counts = {}           # destination -> count
    isolated = 0
    for ref, fp in fps.items():
        if fp.prefix != "TP" or fp.pad_count != 1:
            continue
        nets = list(fp.pin_pads.keys())
        if not nets:
            # No net at all -> misc.
            fp.sheet_key = TP_REHOME_MISC
            rehomed[ref] = TP_REHOME_MISC
            counts[TP_REHOME_MISC] = counts.get(TP_REHOME_MISC, 0) + 1
            isolated += 1
            continue
        net = nets[0]
        owners = [(o, fps[o]) for (o, pin, pos, off) in net_index.get(net, [])
                  if o != ref and o in fps]
        if not owners:
            fp.sheet_key = TP_REHOME_MISC
            rehomed[ref] = TP_REHOME_MISC
            counts[TP_REHOME_MISC] = counts.get(TP_REHOME_MISC, 0) + 1
            isolated += 1
            continue
        anchors = [(o, f) for (o, f) in owners if f.is_anchor]
        pool = anchors if anchors else owners
        # highest-pad-count owner; tie-break deterministically by ref.
        pick_ref, pick_fp = max(pool, key=lambda t: (t[1].pad_count, t[0]))
        dest = pick_fp.sheet_key
        if dest != fp.sheet_key:
            rehomed[ref] = dest
        fp.sheet_key = dest
        counts[dest] = counts.get(dest, 0) + 1

    report["tp_rehome"] = {
        "rehomed_count": sum(1 for d in rehomed.values() if d != TP_REHOME_MISC),
        "isolated_to_misc": isolated,
        "destinations": counts,
    }
    return rehomed


def group_by_sheet(fps):
    groups = {}
    for fp in fps.values():
        if not fp.movable:
            continue
        groups.setdefault(fp.sheet_key, []).append(fp)
    return groups


# ----------------------------------------------------------------------------
# Feature B — silk label toggle (hand-solder ergonomics).
# ----------------------------------------------------------------------------
def apply_silk_mode(board, mode, report):
    """Put either the Reference (default, matches KiCad) or the Value on the
    silkscreen for EVERY footprint, moving the other to F.Fab.

    mode == "value": Reference -> *.Fab, Value -> *.SilkS (show 100k, CD74HC4051,
                     "Daisy Seed", etc. for hand-soldered kits).
    mode == "ref":   Reference -> *.SilkS, Value -> *.Fab (restore KiCad default).

    - Per-footprint side aware: a footprint on the back uses B.SilkS / B.Fab.
    - Fallback: in value mode, if a footprint's Value is blank, leave the
      Reference on silk (don't blank the silk).
    - Idempotent and reversible: running "ref" after "value" restores the
      original layer assignment exactly.
    Generalizes MR1's silk_show_value() (which only handled R/C/CP) to all
    footprints; board-agnostic.
    """
    counts = {"value_on_silk": 0, "ref_on_silk": 0, "blank_value_kept_ref": 0}
    for fp in board.GetFootprints():
        back = fp.IsFlipped()
        silk = pcbnew.B_SilkS if back else pcbnew.F_SilkS
        fab = pcbnew.B_Fab if back else pcbnew.F_Fab
        ref_txt = fp.Reference()
        val_txt = fp.Value()
        if mode == "value" and (fp.GetValue() or "").strip():
            ref_txt.SetLayer(fab)
            val_txt.SetLayer(silk)
            val_txt.SetVisible(True)
            counts["value_on_silk"] += 1
        else:
            # "ref" mode, or value-mode fallback when Value is blank.
            ref_txt.SetLayer(silk)
            ref_txt.SetVisible(True)
            val_txt.SetLayer(fab)
            counts["ref_on_silk"] += 1
            if mode == "value":
                counts["blank_value_kept_ref"] += 1
    report["silk"] = {"mode": mode, **counts}
    return counts


# ----------------------------------------------------------------------------
# Shelf-pack core (DESIGN.md S6.1) — ported verbatim from SKiDL grid_blocks.
# ----------------------------------------------------------------------------
@dataclass
class Block:
    w: float
    h: float
    pos: tuple = (0.0, 0.0)   # min-corner slot assigned by shelf_pack
    payload: object = None


def shelf_pack(blocks, pad):
    """Largest-first shelf packing into rows under a ~1.6:1 landscape target.
    Sets each block's .pos to its min-corner slot. Returns packed (w, h)."""
    if not blocks:
        return (0.0, 0.0)
    order = sorted(blocks, key=lambda b: (b.h, b.w), reverse=True)  # largest-first
    total_area = sum((b.w + pad) * (b.h + pad) for b in order) or 1.0
    row_limit = (total_area * 1.6) ** 0.5
    x = y = row_h = 0.0
    max_x = 0.0
    for b in order:
        w, h = b.w + pad, b.h + pad
        if x > 0.0 and x + w > row_limit:
            x, y, row_h = 0.0, y + row_h, 0.0
        b.pos = (x, y)
        x += w
        row_h = max(row_h, h)
        max_x = max(max_x, x)
    return (max_x, y + row_h)


# ----------------------------------------------------------------------------
# Stage B — within-group relational passive placement (DESIGN.md S5)
# ----------------------------------------------------------------------------
def _snap90(deg):
    return float(round(deg / 90.0) * 90.0 % 360.0)


def _vec(a, b):
    return (b[0] - a[0], b[1] - a[1])


def _norm(v):
    m = math.hypot(v[0], v[1])
    if m < 1e-9:
        return (1.0, 0.0)
    return (v[0] / m, v[1] / m)


def _angle_deg(v):
    return math.degrees(math.atan2(v[1], v[0]))


def classify_passive_role(fp, net_index, anchors_by_ref):
    """Return (role, info). role in {decap, bulk, series, pull, None}."""
    if not fp.is_passive or not fp.pin_pads:
        return (None, {})
    nets = list(fp.pin_pads.keys())
    if len(nets) < 2:
        return (None, {})
    netA, netB = nets[0], nets[1]
    powA, powB = is_power_net(netA), is_power_net(netB)
    gndA, gndB = is_gnd_net(netA), is_gnd_net(netB)
    # A "supply" net is a power rail that is NOT ground. GND matches
    # is_power_net (it is a power net) but must never be chosen as the rail to
    # hug — decaps/bulk hug the *supply* pad, with GND as the return.
    supA, supB = powA and not gndA, powB and not gndB

    if fp.prefix == "C":
        cval = parse_value_uf(fp.value)
        # Need a supply on one net and GND on the other (typical decap/bulk).
        if (supA and gndB) or (supB and gndA):
            rail = netA if supA else netB
            gnd = netB if supA else netA
            # bulk: large value; otherwise decap (incl. unknown value).
            if cval is not None and cval >= BULK_VALUE_UF:
                return ("bulk", {"rail": rail, "gnd": gnd})
            return ("decap", {"rail": rail, "gnd": gnd})
        # cap between two ICs -> series (handled below)
    if fp.prefix == "R":
        # pull: exactly one net is power/gnd, other is a signal touching an anchor
        a_is_rail = powA or gndA
        b_is_rail = powB or gndB
        if a_is_rail != b_is_rail:
            signal = netB if a_is_rail else netA
            rail = netA if a_is_rail else netB
            return ("pull", {"signal": signal, "rail": rail})

    # series: neither net is GND and both nets connect onward to other pads
    if not (gndA or gndB):
        extA = [e for e in net_index.get(netA, []) if e[0] != fp.ref]
        extB = [e for e in net_index.get(netB, []) if e[0] != fp.ref]
        if extA and extB:
            return ("series", {"netA": netA, "netB": netB})
    return (None, {})


def _rotate(off, deg):
    r = math.radians(deg)
    c, s = math.cos(r), math.sin(r)
    return (off[0] * c - off[1] * s, off[0] * s + off[1] * c)


def current_pad_pos(parent_fp, off):
    """Pad position AFTER the parent has been (re)placed/rotated, computed from
    the pad's offset from the footprint center. The net index stores the pad
    offset in the footprint's base frame; we re-apply the parent's current
    position and rotation delta so passives hug the GRIDDED pad, not the
    original origin-blob pad."""
    delta = parent_fp.orient_deg - parent_fp.base_orient_deg
    ro = _rotate(off, delta) if abs(delta) > 1e-9 else off
    return (parent_fp.pos_mm[0] + ro[0], parent_fp.pos_mm[1] + ro[1])


def _other_pads_on_net(net_index, net_name, exclude_ref):
    # entries are (ref, pin, abs_pad_pos, pad_offset)
    return [e for e in net_index.get(net_name, []) if e[0] != exclude_ref]


def _nearest_anchor_pad(candidates, fps, anchor_only=True):
    """candidates: list of (ref, pin, abs_pos, offset). Return
    (ref, pin, current_pad_pos, parent_fp) preferring anchors / high pad count.
    Pad position is recomputed from the parent's CURRENT placement."""
    best = None
    best_score = None
    for (ref, pin, pos, off) in candidates:
        other = fps.get(ref)
        if other is None:
            continue
        if anchor_only and not other.is_anchor:
            continue
        score = other.pad_count
        if best_score is None or score > best_score:
            best_score = score
            best = (ref, pin, current_pad_pos(other, off), other)
    return best


def place_passive_relational(fp, role, info, net_index, fps):
    """Compute absolute pos + orient for a passive given its role.
    Returns (placed_bool, parent_pad_pos or None)."""
    half_long = max(fp.w, fp.h) / 2.0

    if role in ("decap", "bulk", "pull"):
        rail_or_sig = info.get("rail") if role in ("decap", "bulk") else info.get("signal")
        gap = {"decap": DECAP_GAP, "bulk": BULK_GAP, "pull": PULL_GAP}[role]
        # candidate pads: for decap/bulk, the rail-side pads on an anchor;
        # for pull, the signal-side pads on an anchor.
        cands = _other_pads_on_net(net_index, rail_or_sig, fp.ref)
        parent = _nearest_anchor_pad(cands, fps, anchor_only=True)
        if parent is None:
            return (False, None)
        pref, ppin, ppos, panchor = parent
        fp.parent_ref = pref
        # body side = vector from parent pad toward parent center
        body = _norm(_vec(ppos, panchor.pos_mm))
        target = (ppos[0] + body[0] * (gap + half_long), ppos[1] + body[1] * (gap + half_long))
        # orient: long axis aligned to hug direction, snapped to 90
        ang = _snap90(_angle_deg(body))
        fp.pos_mm = target
        fp.orient_deg = ang
        return (True, ppos)

    if role == "series":
        netA, netB = info["netA"], info["netB"]
        candA = _other_pads_on_net(net_index, netA, fp.ref)
        candB = _other_pads_on_net(net_index, netB, fp.ref)
        # bias driver toward the higher-pad-count anchor on netA; if none is an
        # anchor, fall back to the first pad on netA.
        drv = _nearest_anchor_pad(candA, fps, anchor_only=True)
        if drv is None and candA:
            r, p, ap, off = candA[0]
            pf = fps.get(r)
            drv = (r, p, current_pad_pos(pf, off) if pf else ap, pf)
        # exit pad: prefer an anchor on netB, else nearest other pad.
        exitp = _nearest_anchor_pad(candB, fps, anchor_only=True)
        if exitp is None and candB:
            r, p, ap, off = candB[0]
            pf = fps.get(r)
            exitp = (r, p, current_pad_pos(pf, off) if pf else ap, pf)
        if drv is None or exitp is None:
            return (False, None)
        dpos = drv[2]
        epos = exitp[2]
        mid = ((dpos[0] + epos[0]) / 2.0, (dpos[1] + epos[1]) / 2.0)
        # clamp to SERIES_MAX_OFFSET from driver
        dv = _vec(dpos, mid)
        dist = math.hypot(*dv)
        if dist > SERIES_MAX_OFFSET:
            u = _norm(dv)
            mid = (dpos[0] + u[0] * SERIES_MAX_OFFSET, dpos[1] + u[1] * SERIES_MAX_OFFSET)
        ang = _snap90(_angle_deg(_vec(dpos, epos)))
        fp.pos_mm = mid
        fp.orient_deg = ang
        return (True, dpos)

    return (False, None)


# ----------------------------------------------------------------------------
# Within-group layout: anchors gridded, passives hugged, orphans gridded.
# Produces a group block (w,h) and absolute placement of all parts in group.
# ----------------------------------------------------------------------------
def layout_group(group_fps, net_index, fps, report_passives):
    anchors = [f for f in group_fps if f.is_anchor and f.movable]
    passives = [f for f in group_fps if f.is_passive and f.movable]
    others = [f for f in group_fps if not f.is_anchor and not f.is_passive and f.movable]

    # 1. Shelf-pack anchors into a local grid with a passive halo.
    # Only reserve the halo when the group actually has passives to hug; a
    # singleton connector/module with no passives gets no wasted reserve and
    # its block shrinks to the part's real extent.
    halo = PASSIVE_HALO if passives else 0.0
    anchor_blocks = [Block(w=a.w + halo, h=a.h + halo, payload=a) for a in anchors]
    shelf_pack(anchor_blocks, L1_PAD)
    # place anchors absolutely in local frame (we'll translate whole group later)
    for blk in anchor_blocks:
        a = blk.payload
        # center of slot
        a.pos_mm = (blk.pos[0] + blk.w / 2.0, blk.pos[1] + blk.h / 2.0)

    # 2. Bind passives relationally (anchors now have local positions).
    resolved = 0
    orphans = []

    # 2a. Crystal + load-cap clusters first (S5.3 row 5). A crystal (prefix Y)
    # anchor's two oscillator nets each carry one load cap to GND; place the
    # two caps flanking the crystal as one tight knot. Caps handled here are
    # removed from the general passive sweep below.
    xtal_handled = set()  # set of refs
    for a in anchors:
        if a.prefix != "Y":
            continue
        osc_nets = [n for n in a.pin_pads.keys() if not is_gnd_net(n) and not is_power_net(n)]
        cap_for_net = {}
        for n in osc_nets:
            for p in passives:
                if p.prefix != "C" or p.ref in xtal_handled:
                    continue
                pnets = list(p.pin_pads.keys())
                if n in pnets and any(is_gnd_net(o) for o in pnets):
                    cap_for_net[n] = p
                    break
        if len(cap_for_net) >= 1:
            ax, ay = a.pos_mm
            side = 1
            for n, cap in cap_for_net.items():
                # flank the crystal: cap offset along x by half-widths + gap
                off = (a.w / 2.0 + XTAL_GAP + cap.w / 2.0) * side
                cap.pos_mm = (ax + off, ay)
                cap.orient_deg = _snap90(0.0 if side > 0 else 180.0)
                xtal_handled.add(cap.ref)
                resolved += 1
                report_passives["by_role"].setdefault("crystal", 0)
                report_passives["by_role"]["crystal"] += 1
                report_passives["distances"].append(round(abs(off), 3))
                side = -side

    for p in passives:
        if p.ref in xtal_handled:
            continue
        role, info = classify_passive_role(p, net_index, None)
        if role is None:
            orphans.append(p)
            report_passives["orphan_reasons"].setdefault("no_role_or_no_net", 0)
            report_passives["orphan_reasons"]["no_role_or_no_net"] += 1
            continue
        ok, parent_pad = place_passive_relational(p, role, info, net_index, fps)
        if ok:
            resolved += 1
            report_passives["by_role"].setdefault(role, 0)
            report_passives["by_role"][role] += 1
            # record distance for metrics
            d = math.hypot(p.pos_mm[0] - parent_pad[0], p.pos_mm[1] - parent_pad[1])
            report_passives["distances"].append(round(d, 3))
        else:
            orphans.append(p)
            report_passives["orphan_reasons"].setdefault("unresolved_" + role, 0)
            report_passives["orphan_reasons"]["unresolved_" + role] += 1

    # 3. Orphans + others -> misc sub-cluster grid, appended to local frame.
    misc = orphans + others
    if misc:
        misc_blocks = [Block(w=m.w, h=m.h, payload=m) for m in misc]
        mw, mh = shelf_pack(misc_blocks, L1_PAD)
        # place misc grid below the anchors' local extent
        local_max_y = 0.0
        for a in anchors:
            local_max_y = max(local_max_y, a.pos_mm[1] + a.h / 2.0)
        oy = local_max_y + L1_PAD
        for blk in misc_blocks:
            m = blk.payload
            m.pos_mm = (blk.pos[0] + blk.w / 2.0, blk.pos[1] + oy + blk.h / 2.0)

    # 4. De-collide WITHIN the group (local frame) before measuring the block
    #    bbox, so the block fully encloses the spread-out parts. Stage C then
    #    packs blocks that already account for de-collision — parts can never
    #    leak across the Level-2 gutter into a neighbouring sheet.
    placed = anchors + [p for p in passives if p not in orphans] + misc
    if not placed:
        return (0.0, 0.0), 0, len(passives)
    decollide(placed, margin=0.3 + TEXT_GUTTER)

    # 5. Compute group local bbox (union of all placed group parts).
    minx = min(f.pos_mm[0] - f.w / 2 for f in placed)
    miny = min(f.pos_mm[1] - f.h / 2 for f in placed)
    maxx = max(f.pos_mm[0] + f.w / 2 for f in placed)
    maxy = max(f.pos_mm[1] + f.h / 2 for f in placed)
    # normalize group to local origin
    for f in placed:
        f.pos_mm = (f.pos_mm[0] - minx, f.pos_mm[1] - miny)
    return (maxx - minx, maxy - miny), resolved, len(passives)


# ----------------------------------------------------------------------------
# Stage C — two-level shelf-pack + write-back
# ----------------------------------------------------------------------------
def arrange(board_path, netlist_path, premap_path, out_path, dry_run,
            force_all, respect_placed, report_path, tp_rehome=True,
            silk_mode=None):
    report = {"design": "first-pass PCB arranger PoC"}
    nl = parse_netlist(netlist_path)
    board = pcbnew.LoadBoard(board_path)

    fps, net_index = load_and_classify(board, nl, force_all, respect_placed, report)

    # Feature A: re-home test points to the net they tap (before grouping).
    if tp_rehome:
        rehome_test_points(fps, net_index, report)

    premap = None
    if premap_path:
        premap = json.load(open(premap_path))
    premap_refs, keepout_extra = apply_premap(fps, premap, report) if premap else ([], 3.0)

    # Tier-1 keep-clear zone (union bbox of fixed parts), expanded.
    tier1_bbox = None
    if premap_refs:
        xs0 = [fps[r].pos_mm[0] - fps[r].w / 2 for r in premap_refs if r in fps]
        ys0 = [fps[r].pos_mm[1] - fps[r].h / 2 for r in premap_refs if r in fps]
        xs1 = [fps[r].pos_mm[0] + fps[r].w / 2 for r in premap_refs if r in fps]
        ys1 = [fps[r].pos_mm[1] + fps[r].h / 2 for r in premap_refs if r in fps]
        if xs0:
            tier1_bbox = (min(xs0) - keepout_extra, min(ys0) - keepout_extra,
                          max(xs1) + keepout_extra, max(ys1) + keepout_extra)

    # Stage A
    groups = group_by_sheet(fps)
    report["groups"] = {k: len(v) for k, v in sorted(groups.items())}

    # Stage B per group
    report_passives = {"by_role": {}, "orphan_reasons": {}, "distances": []}
    group_blocks = []
    total_resolved = total_passives = 0
    for sheet, gfps in sorted(groups.items()):
        (gw, gh), res, tot = layout_group(gfps, net_index, fps, report_passives)
        total_resolved += res
        total_passives += tot
        if gw > 0 and gh > 0:
            group_blocks.append(Block(w=gw, h=gh, payload=(sheet, gfps)))

    # Stage C level 2: shelf-pack the sheet blocks.
    shelf_pack(group_blocks, L2_PAD)

    # Determine Tier-2 grid origin (S6.3 / S12.8).
    if tier1_bbox is not None:
        ox = tier1_bbox[2] + TIER_GAP   # to the right of tier-1 zone
        oy = tier1_bbox[1]
    else:
        edges = board.GetBoardEdgesBoundingBox()
        if edges.GetWidth() > 0 and edges.GetHeight() > 0:
            ox = pcbnew.ToMM(edges.GetX())
            oy = pcbnew.ToMM(edges.GetY())
        else:
            ox = oy = 0.0  # no Edge.Cuts -> origin-ish, don't fail

    # Translate each group block (and its parts) into the level-2 slot + origin.
    moves = 0
    sheet_bboxes = {}
    for blk in group_blocks:
        sheet, gfps = blk.payload
        gx = ox + blk.pos[0]
        gy = oy + blk.pos[1]
        sheet_bboxes[sheet] = (gx, gy, blk.w, blk.h)
        for f in gfps:
            if not f.movable:
                continue
            f.pos_mm = (gx + f.pos_mm[0], gy + f.pos_mm[1])

    # De-collision already ran per group inside layout_group (local frame,
    # before block bbox measurement), so blocks enclose their parts and the
    # Level-2 shelf-pack keeps sheets separated. Nothing to do globally here.

    # Write-back to the live board (unless dry-run).
    moved_refs = []
    for f in fps.values():
        h = f.handle
        if f.movable:
            cur = h.GetPosition()
            new = pcbnew.VECTOR2I(pcbnew.FromMM(f.pos_mm[0]), pcbnew.FromMM(f.pos_mm[1]))
            if cur.x != new.x or cur.y != new.y or abs(h.GetOrientationDegrees() - f.orient_deg) > 1e-6:
                moved_refs.append(f.ref)
            h.SetPosition(new)
            h.SetOrientationDegrees(f.orient_deg)
        elif f.ref in premap_refs:
            # tier-1 fixed parts: apply their fixed coords/rot
            h.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(f.pos_mm[0]), pcbnew.FromMM(f.pos_mm[1])))
            h.SetOrientationDegrees(f.orient_deg)
            if f.locked:
                h.SetLocked(True)

    moves = len(moved_refs)

    # ---- metrics (S9.3) ----
    metrics = compute_metrics(fps, net_index, sheet_bboxes, report_passives,
                              total_resolved, total_passives)
    metrics["moves"] = moves
    metrics["moved_refs_count"] = len(moved_refs)
    report["metrics"] = metrics
    report["passives"] = {
        "by_role": report_passives["by_role"],
        "orphan_reasons": report_passives["orphan_reasons"],
        "resolved": total_resolved,
        "total": total_passives,
    }
    report["sheet_bboxes"] = {k: [round(x, 2) for x in v] for k, v in sheet_bboxes.items()}

    # Feature B: silk label mode (orthogonal to placement). Default None = leave
    # the board's existing silk assignment untouched.
    if silk_mode in ("value", "ref"):
        apply_silk_mode(board, silk_mode, report)

    if not dry_run:
        pcbnew.SaveBoard(out_path, board)
        report["out"] = out_path
    else:
        report["out"] = None

    if report_path:
        json.dump(report, open(report_path, "w"), indent=2)
    return report


def decollide(moved, margin=0.3, max_iter=200):
    """Cheap local de-collision: nudge overlapping parts apart on their
    smaller-overlap axis. Not a global solver (non-goal). bbox+margin overlap.

    Anchors are the structural skeleton (shelf-packed, already separated) and
    are treated as FIXED obstacles: when an anchor collides with a passive, the
    passive is pushed — never the anchor. This prevents the sweep from shoving
    a large IC away from its hugging decoupling caps (which would break the
    relational placement). Anchor-vs-anchor pairs are left alone.
    """
    order = sorted(moved, key=lambda f: (f.pos_mm[1], f.pos_mm[0]))

    def push(target, anchor_ref, dx, dy):
        target.pos_mm = (target.pos_mm[0] + dx, target.pos_mm[1] + dy)

    for _ in range(max_iter):
        moved_any = False
        for i in range(len(order)):
            for j in range(i + 1, len(order)):
                a, b = order[i], order[j]
                if not _bbox_overlap(a, b, margin):
                    continue
                # A hugging passive is intentionally close to (often atop) its
                # parent anchor's pad — don't push it out of the parent body.
                if a.parent_ref == b.ref or b.parent_ref == a.ref:
                    continue
                # Decide who moves: never move an anchor against a non-anchor.
                if a.is_anchor and b.is_anchor:
                    continue  # both structural; shelf-pack already spaced them
                siblings = (a.parent_ref is not None and a.parent_ref == b.parent_ref)
                if a.is_anchor and not b.is_anchor:
                    mover, fixed = b, a
                elif b.is_anchor and not a.is_anchor:
                    mover, fixed = a, b
                else:
                    mover, fixed = b, a  # two passives: move the later one
                overlap_x = (a.w + b.w + 2 * margin) / 2 - abs(a.pos_mm[0] - b.pos_mm[0])
                overlap_y = (a.h + b.h + 2 * margin) / 2 - abs(a.pos_mm[1] - b.pos_mm[1])
                if siblings:
                    # Two passives hugging the SAME parent: pushing either
                    # toward the parent is pointless (parent is exempt), so
                    # spread them tangentially along the LARGER-overlap axis
                    # (the axis where they're stacked), away from each other.
                    if overlap_x >= overlap_y:
                        sign = 1.0 if mover.pos_mm[0] >= fixed.pos_mm[0] else -1.0
                        mover.pos_mm = (mover.pos_mm[0] + sign * (overlap_x + 1e-3), mover.pos_mm[1])
                    else:
                        sign = 1.0 if mover.pos_mm[1] >= fixed.pos_mm[1] else -1.0
                        mover.pos_mm = (mover.pos_mm[0], mover.pos_mm[1] + sign * (overlap_y + 1e-3))
                    moved_any = True
                    continue
                # push mover away from fixed along the smaller-overlap axis
                if overlap_x <= overlap_y:
                    sign = 1.0 if mover.pos_mm[0] >= fixed.pos_mm[0] else -1.0
                    mover.pos_mm = (mover.pos_mm[0] + sign * (overlap_x + 1e-3), mover.pos_mm[1])
                else:
                    sign = 1.0 if mover.pos_mm[1] >= fixed.pos_mm[1] else -1.0
                    mover.pos_mm = (mover.pos_mm[0], mover.pos_mm[1] + sign * (overlap_y + 1e-3))
                moved_any = True
        if not moved_any:
            break
    return order


def compute_metrics(fps, net_index, sheet_bboxes, report_passives,
                    resolved, total_passives):
    moved = [f for f in fps.values() if f.movable]
    # Courtyard overlaps among moved parts. A hugging passive intentionally
    # sits on/beside its parent anchor's pad (often inside the anchor
    # courtyard) — those are by-design and counted separately, not as faults.
    overlaps = 0
    intentional = 0
    for i in range(len(moved)):
        for j in range(i + 1, len(moved)):
            a, b = moved[i], moved[j]
            if not _bbox_overlap(a, b, margin=0.0):
                continue
            if a.parent_ref == b.ref or b.parent_ref == a.ref:
                intentional += 1
            else:
                overlaps += 1
    # blob dispersion: moved parts within R of origin (0,0)
    R = 5.0
    near_origin = sum(1 for f in moved if math.hypot(*f.pos_mm) < R)
    # group separation: min gap between any two sheet bboxes
    keys = list(sheet_bboxes.keys())
    min_gap = None
    for i in range(len(keys)):
        for j in range(i + 1, len(keys)):
            g = _rect_gap(sheet_bboxes[keys[i]], sheet_bboxes[keys[j]])
            min_gap = g if min_gap is None else min(min_gap, g)
    dists = report_passives["distances"]
    dists_sorted = sorted(dists)
    median = dists_sorted[len(dists_sorted) // 2] if dists_sorted else None
    p95 = dists_sorted[int(len(dists_sorted) * 0.95)] if dists_sorted else None
    return {
        "moved_parts": len(moved),
        "bbox_overlaps_among_moved": overlaps,
        "intentional_passive_parent_overlaps": intentional,
        "blob_within_origin_R": near_origin,
        "blob_R_mm": R,
        "group_separation_min_gap_mm": round(min_gap, 3) if min_gap is not None else None,
        "passive_resolution_rate": round(resolved / total_passives, 4) if total_passives else None,
        "passive_dist_median_mm": median,
        "passive_dist_p95_mm": p95,
    }


def _rect_gap(a, b):
    """Min gap between two axis-aligned rects (x,y,w,h). 0 if overlapping."""
    ax0, ay0, aw, ah = a
    bx0, by0, bw, bh = b
    ax1, ay1 = ax0 + aw, ay0 + ah
    bx1, by1 = bx0 + bw, by0 + bh
    dx = max(bx0 - ax1, ax0 - bx1, 0.0)
    dy = max(by0 - ay1, ay0 - by1, 0.0)
    return math.hypot(dx, dy)


def main(argv=None):
    ap = argparse.ArgumentParser(description="First-pass PCB arranger (PoC).")
    ap.add_argument("board")
    ap.add_argument("--netlist", default=None)
    ap.add_argument("--premap", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--force-all", action="store_true", default=True)
    g.add_argument("--respect-placed", action="store_true")
    ap.add_argument("--report", default=None)
    tg = ap.add_mutually_exclusive_group()
    tg.add_argument("--tp-rehome", dest="tp_rehome", action="store_true",
                    default=True, help="re-home single-pad test points to the "
                    "sheet of the net they tap (default on)")
    tg.add_argument("--no-tp-rehome", dest="tp_rehome", action="store_false",
                    help="keep test points in their declared netlist sheet")
    ap.add_argument("--silk", choices=("value", "ref"), default=None,
                    help="silk label mode: 'value' shows part values on silk "
                    "(hand-solder kits), 'ref' restores KiCad default. "
                    "Omitted = leave silk untouched.")
    args = ap.parse_args(argv)

    netlist_path = args.netlist
    if netlist_path is None:
        base = os.path.splitext(args.board)[0]
        cand = base + ".kicad_netlist"
        if os.path.exists(cand):
            netlist_path = cand
        else:
            ap.error("no --netlist given and no sibling .kicad_netlist found")

    out_path = args.out or (os.path.splitext(args.board)[0] + "_arranged.kicad_pcb")
    respect = args.respect_placed
    force_all = not respect

    report = arrange(
        args.board, netlist_path, args.premap, out_path,
        args.dry_run, force_all, respect, args.report,
        tp_rehome=args.tp_rehome, silk_mode=args.silk,
    )
    print(json.dumps(report.get("metrics", {}), indent=2))
    print("join:", report.get("join", {}).get("join_rate"),
          f"({report['join']['matched']}/{report['join']['board_footprints']})")
    if "tp_rehome" in report:
        print("tp_rehome:", report["tp_rehome"])
    if "silk" in report:
        print("silk:", report["silk"])
    if not args.dry_run:
        print("wrote:", out_path)
    return report


if __name__ == "__main__":
    main()
