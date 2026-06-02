# First-Pass PCB Arranger — Design Document

**Status:** Draft for human review. No build has started. Sections flagged
**[OPEN]** contain genuine uncertainty and must be resolved (or explicitly
deferred) before handing to build agents.

**Author:** design pass, grounded against pcbnew 9.0.7 on wintermute and the
SKiDL fork at `/tmp/skidl-dev`.

---

## 0. One-paragraph summary

After "Update PCB from Schematic", every footprint lands in a type-sorted blob
near the origin. This tool does a *good first pass*: it groups footprints by
schematic sheet, snaps support passives to the pads they serve, and shelf-packs
the resulting clusters into an orderly two-level grid — while honoring a
user-supplied deterministic pre-map (mechanically-fixed parts) and never
touching locked or already-hand-placed parts. The human then takes over for
real layout. First incarnation is a standalone `python3` script using the
`pcbnew` module; the algorithm is later folded into SKiDL.

---

## 1. Goals / Non-goals

### Goals
- **Board-agnostic.** Operate on any KiCad 9 `.kicad_pcb` after netlist import.
  No per-project hardcoding in the core algorithm.
- **Restore structure.** Turn the origin blob into legible clusters: one cluster
  per schematic sheet, passives hugging the part they serve, subsystems spread
  apart on a grid.
- **First pass only.** Produce a layout a human is *happy to start from*, not a
  finished board.
- **Deterministic pre-map.** Honor a user-declared, per-board map of exact
  positions/rotations for mechanically-fixed parts (pots, jacks, switches,
  encoders) and arrange everything else clear of them.
- **Idempotent + respectful.** Re-runnable mid-layout. Only moves footprints
  that are still in the unplaced blob and not locked.
- **Headlessly testable.** Load → arrange → save, then render with `kicad-cli`
  for visual inspection and compute numeric quality metrics.

### Non-goals (explicit)
- **Not a router.** No traces, no ratsnest optimization, no track length work.
- **Not a global autoplacer / optimizer.** No force-directed energy
  minimization, no simulated annealing over the whole board, no net-length
  objective. Placement is *local + relational* (passive→parent) plus
  *structural* (shelf-pack grid). Deliberately simple and predictable.
- **Not a DRC/clearance solver.** We avoid gross courtyard overlap with a cheap
  pass (§7); we do not guarantee manufacturability.
- **Not a thermal / EMI / signal-integrity tool.**
- **Not a schematic tool.** Reads schematic-derived metadata already baked into
  the board; does not parse `.kicad_sch`.

---

## 2. Architecture overview

```
                 .kicad_pcb  +  optional pre-map (Python/JSON)
                         │
                         ▼
        ┌──────────────────────────────────────────┐
        │  LOAD + CLASSIFY                           │
        │  - read footprints                         │
        │  - tag: locked / placed / unplaced-blob    │
        │  - tag: anchor / passive / other           │
        │  - build net→pads index                    │
        └──────────────────────────────────────────┘
                         │  movable set = unplaced & unlocked
                         ▼
        ┌──────────────────────────────────────────┐
        │  STAGE 0 — DETERMINISTIC PRE-MAP (Tier 1)  │
        │  apply user fixed positions/rotations.     │
        │  these become *anchors* that passives may  │
        │  latch onto, and a keep-clear zone.        │
        └──────────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────┐
        │  STAGE A — GROUP by schematic sheet        │
        │  → {sheet_key: [footprints]}               │
        └──────────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────┐
        │  STAGE B — WITHIN-GROUP RELATIONAL         │
        │  place anchors (grid), snap passives to    │
        │  served pad. emit per-group local bbox.    │
        └──────────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────┐
        │  STAGE C — TWO-LEVEL SHELF-PACK GRID       │
        │  level 1: sub-clusters within a sheet      │
        │  level 2: sheet-blocks against each other  │
        │  placed in Tier-2 region, clear of Tier 1  │
        └──────────────────────────────────────────┘
                         │
                         ▼
        ┌──────────────────────────────────────────┐
        │  WRITE-BACK + SAVE + (optional) RENDER     │
        └──────────────────────────────────────────┘
```

### Internal data model

A single in-memory record per footprint decouples the algorithm from pcbnew:

```python
@dataclass
class FP:
    ref: str                 # "U3"
    value: str               # "STM32F4"
    prefix: str              # "U"  (letters only, digits stripped)
    pad_count: int
    is_passive: bool         # ref in {R,C,L} and pad_count == 2
    is_anchor: bool          # see §3.2 classifier
    sheet_key: str           # see Stage A
    bbox_mm: tuple           # (w, h) at current orientation
    pos_mm: tuple            # current (x, y) center
    orient_deg: float
    locked: bool
    placed: bool             # already hand-placed (outside blob)
    pin_pads: dict           # net_name -> [(pad_pos_mm, pad_name), ...]
    handle: object           # the live pcbnew.FOOTPRINT (write-back only)
```

`pin_pads` is the per-footprint slice of the global net index. The global index
is `net_name -> [(fp_ref, pad_name, pad_pos_mm), ...]`, built once.

All math is in **millimetres**. pcbnew uses internal units (nm); convert at the
boundary with `pcbnew.ToMM` / `pcbnew.FromMM`. Never let nm leak into the
algorithm.

---

## 3. Load + Classify

### 3.1 pcbnew API surface (verified on 9.0.7)

```python
board = pcbnew.LoadBoard(path)
for fp in board.GetFootprints():
    fp.GetReference()            # "C10"
    fp.GetValue()                # "1uF"
    fp.GetPadCount()             # int  (or len(list(fp.Pads())))
    fp.IsLocked()                # bool
    fp.GetPosition()             # VECTOR2I (nm); .x/.y
    fp.GetOrientationDegrees()   # float
    fp.GetBoundingBox()          # BOX2I (nm); .GetWidth()/.GetHeight()
    fp.GetSheetname()            # str   ⚠ often "" — see §3.3 / [OPEN-1]
    fp.GetPath().AsString()      # "/uuid/uuid/..."  hierarchy proxy
    for pad in fp.Pads():
        pad.GetNetname()         # str   ⚠ "" if no net assigned — [OPEN-2]
        pad.GetNumber()          # "1"
        pad.GetPosition()        # VECTOR2I (nm), absolute board coords

# write-back
fp.SetPosition(pcbnew.VECTOR2I(pcbnew.FromMM(x), pcbnew.FromMM(y)))
fp.SetOrientationDegrees(deg)
pcbnew.SaveBoard(out_path, board)
```

**Bounding box choice.** `GetBoundingBox()` includes silk/text by default and
can be huge; prefer the courtyard when present:
`fp.GetCourtyard(pcbnew.F_CrtYd)` returns a `SHAPE_POLY_SET`. Fall back to
`fp.GetBoundingBox(False, False)` (no text, no invisible) when there is no
courtyard. Grounding note: on the stm32 sample, `GetCourtyard` returned a
1-outline polyset, so courtyard data is usable. **[OPEN-7]** confirm the exact
arg overload that excludes silk on 9.0.7 and the empty-courtyard fallback.

### 3.2 Anchor vs passive vs other

- **Passive:** `prefix in {R, C, L}` **and** `pad_count == 2`. (A 3-pad
  trimpot `RV` is *not* a passive here — it is mechanical/anchor-ish.)
- **Anchor:** `prefix in {U, J, Q, Y, SW, RV, K, M}` **or**
  `pad_count >= ANCHOR_PAD_MIN` (default 3). ICs, connectors, modules,
  crystals, transistors, relays.
- **Other:** everything else (test points `TP`, mounting holes `H`/`MH`,
  fiducials, single-pad parts). Gridded as their own block; never act as a
  passive parent.

These prefix sets and `ANCHOR_PAD_MIN` are **config**, not hardcoded constants,
so the tool stays board-agnostic. The Concentric memory note about confidently
asserting hardware facts applies here: treat the default prefix→role mapping as
*a starting heuristic to be reviewed*, not ground truth.

### 3.3 "Unplaced blob" detection — [OPEN-3, important]

We must distinguish *un-placed* footprints (safe to move) from *hand-placed*
ones (never move). KiCad does **not** set a reliable "placed" flag on
netlist import, so we infer it. Candidate signals, in rough priority order:

1. **Locked → never movable.** `fp.IsLocked()` is authoritative for "leave
   alone". Hard rule.
2. **Origin clustering.** On import, footprints stack with a small per-part
   offset near the board origin (and often off the drawn board outline). Heuristic:
   compute the **median nearest-neighbor spacing**; the blob is the dense
   low-spacing cluster sitting at/near `(0,0)` or just outside the Edge.Cuts
   bounding box.
3. **On-board-outline test.** If an `Edge.Cuts` outline exists, a footprint
   whose center is *inside* the outline is likely intentionally placed; one
   *outside* (the import dumping ground) is likely blob.
4. **Overlap density.** Blob footprints heavily overlap each other; placed
   footprints generally do not.

**Decision needed:** none of these is bulletproof. Proposed default policy:
> A footprint is *movable* iff it is **unlocked** AND
> (it overlaps ≥1 other footprint's courtyard at load time
>  OR it lies outside the Edge.Cuts outline
>  OR no Edge.Cuts outline exists at all).
Everything else is treated as hand-placed and frozen.

This is conservative (errs toward not moving things). It needs validation on a
*partially* hand-laid board, which neither current sample is — **[OPEN-3]**.

A `--force-all` flag (move every unlocked footprint regardless) is provided for
the clean "just imported, nothing placed yet" case, which is the common one.

---

## 4. Stage A — Group by schematic sheet

**Consumes:** classified `FP` records (movable set).
**Produces:** `{sheet_key: [FP, ...]}`.

Primary key: `fp.GetSheetname()`.

**Grounding reality check (matters a lot):** on *all four* boards I inspected on
this machine — MR1 (`mr1.kicad_pcb`, 168 fp), `mr1_layout.kicad_pcb`,
`concentric-main`, and the SKiDL `stm32` sample — **`GetSheetname()` returned
`""` for every footprint.** So we cannot rely on it being populated. However,
`GetPath().AsString()` returned a UUID path whose **segment count varies by
sheet** (stm32 distribution: depth 1×24, 2×3, 3×9, 4×15). The path encodes the
hierarchical instance.

### Grouping key resolution order
1. If `GetSheetname()` is non-empty → use it directly (cleanest, human-readable).
2. Else parse `GetPath().AsString()`: drop the **last** UUID (that is the
   footprint's own instance UUID) and use the **remaining prefix** as the sheet
   key. Footprints sharing a parent-path prefix share a sheet.
3. Else (empty path, flat design) → single group `"__root__"`.

**[OPEN-1, important]:** The path-prefix grouping is a hypothesis. Before build,
verify on a *known hierarchical* board (freshly imported, sheet names intact)
that (a) `GetSheetname` is in fact populated post-import in normal KiCad flow —
our samples may have been stripped/regenerated — and (b) the path-prefix
fallback bins footprints the same way the schematic sheets do. The stm32 sample
is the best test vehicle (it has real depth variation). If neither key works,
fall back to **connectivity grouping** (§Stage A.alt).

### Stage A.alt — connectivity fallback
If no usable sheet key exists, group by **connected components over
signal nets** (ignore power/ground rails so the whole board doesn't merge into
one blob). This is the direct PCB analogue of SKiDL's `group_parts()`
(`/tmp/skidl-dev/src/skidl/schematics/place.py:1290`), which unions part-sets
that share a net. Port that union-find pattern over the pad/net index.

**Power-rail exclusion list:** nets matching `GND`, `GNDA`, `VCC`, `VDD`,
`VSS`, `+3V3`, `+5V`, `VBUS`, `+12V`, `-12V`, etc. (regex, config). SKiDL
already knows which nets are power rails — the eventual integration (§9) gets
this for free; the standalone PoC uses the regex list.

---

## 5. Stage B — Within-group relational placement

**Consumes:** one sheet group's `FP` list + the net index.
**Produces:** final positions for that group's parts, expressed in a *local*
frame, plus the group's local bounding box (for Stage C).

### 5.1 Algorithm
1. **Anchors first.** Collect the group's anchors. Shelf-pack them into a local
   grid (the same routine as Stage C, §6) using their courtyards + a passive
   halo (extra margin around each anchor reserved for its hugging passives).
   This gives every anchor a local position before any passive moves.
2. **Bind passives to a served pad.** For each passive, determine its *parent
   pad* (§5.2). Snap the passive next to that pad with the role-specific
   geometry (§5.3).
3. **Orphan passives** (no resolvable parent, §5.4) are collected into a small
   "misc passives" sub-cluster, shelf-packed, and treated as one more block in
   this group.
4. **Multi-anchor / shared passives** resolved per §5.4.
5. Emit the union bbox of everything placed → the group block for Stage C.

> Anchors that were fixed by the Tier-1 pre-map (§8) are *already positioned*.
> They are skipped in step 1 (not re-placed) but **still act as parents** in
> step 2, so a decoupling cap hugs a deterministically-placed IC exactly as it
> would a grid-placed one. This is the key Tier-1↔Tier-2 seam.

### 5.2 Finding the served pad (netlist-derived, not hardcoded)

For a 2-pad passive with pads on nets `(netA, netB)`:

- Build, for each of its nets, the set of *other* footprints' pads on that net.
- The **parent** is chosen by scoring candidate pads:
  - prefer pads belonging to an **anchor** (high pad count) over another passive;
  - prefer a **power/IO pad** of that anchor for decoupling/bulk roles;
  - prefer the **physically nearest** candidate pad (after anchors are placed),
    breaking ties.
- The **parent pad** is the specific pad on the chosen parent that the passive
  shares a net with.

Relationships are thus *derived from the netlist topology*, satisfying
requirement 2b ("which pad shares the passive's net, not hardcoded").

### 5.3 Relational passive rules

Each rule = **detection** → **geometry** → **failure/ambiguity**. Offsets are
config defaults (mm). "Side" = which side of the parent pad we place on.

| Role | Detection | Geometry | Failure / ambiguity |
|---|---|---|---|
| **Decoupling cap** | `C`, value small (≤1µF heuristic OR not tagged bulk); one net is a power rail, the other is GND; the power-rail pad belongs to an anchor | Place on the anchor's body side, hugging the power pad. Offset = pad-to-cap-pad gap `DECAP_GAP` (≈0.5–1.0 mm). Rotate so the cap's power pad faces the IC pad (orient cap's net-A pad toward parent). Same copper side as the IC. | If multiple power pads on the same IC share this rail, assign caps round-robin to the **nearest unserved** power pad. If value unknown, still treat as decap when one net is a rail + other is GND. |
| **Bulk cap** | `C`, large value (≥10µF) OR net touches a regulator I/O pin (anchor whose ref/value looks like a regulator) | Near the regulator's IN or OUT pad, slightly larger offset `BULK_GAP`. Input bulk → IN side, output bulk → OUT side (decided by which rail net the cap sits on). | If reg I/O not identifiable, demote to decap rule, else orphan. |
| **In-line / series R** | `R`, neither net is GND, the two nets each connect onward (R sits *between* two other pads — both nets have an external pad) | Place along the line between **driver pad** (the anchor/source pad on net-A) and the **net's exit** (nearest other pad on net-B). Rotate to align the R's long axis with that line; center it at the midpoint, clamped to `SERIES_MAX_OFFSET` from the driver. | If both ends are anchors (true series between two ICs), bias toward the **higher-pad-count** anchor as driver. If it's actually a pull-up (one net is a rail), fall through to pull rule. |
| **Pull-up / pull-down R** | `R`, exactly one net is a power rail or GND, the other net is a signal that connects to an anchor pad | Adjacent to the **biased anchor pin** (the signal-side pad), short offset `PULL_GAP`, on the body side. Orientation aligns R long axis radially out from that pad. | If the signal net touches several anchor pins, pick the nearest; if none is an anchor, orphan. |
| **Crystal + load caps** | A `Y`/crystal anchor; its two oscillator pads (XIN/XOUT) carry nets; the two load caps are the `C`s sharing those nets with the crystal, other ends on GND | Tight triangular cluster: crystal centered, the two caps flanking XIN/XOUT pads at `XTAL_GAP`, GND pads facing outward (away from crystal). The whole cluster is a single sub-block. | If only one load cap found, place it and warn. If caps ambiguous (shared with other nets), require both caps to share a crystal osc net or skip. |

**Geometry primitives shared by all rules:**
- *Hug a pad*: target position = `parent_pad_pos + unit(into_parent_body) *
  (gap + own_half_extent_along_axis)`.
- *Body side*: vector from parent pad toward parent footprint center.
- *Align long axis*: set passive orientation so its pad-to-pad axis is parallel
  (series/pull) or perpendicular (decap facing) to the hug direction, snapped to
  the nearest 90° **[OPEN-5]** (see rotation question).

### 5.4 Ambiguity / failure handling (cross-cutting)
- **Cap shared between two ICs** (e.g. a coupling cap between U1 and U2): no
  single parent. Policy: treat as a **series element** (in-line R/C rule) and
  place at the midpoint between the two ICs' shared-net pads, rather than
  arbitrarily hugging one. If the two ICs are in **different sheet groups**,
  assign the cap to the group of the **nearer / higher-pad-count** IC and leave
  a note in the report. **[OPEN-4, important].**
- **Passive with no anchor on either net** (passive↔passive only): orphan →
  misc sub-cluster.
- **Net not found / empty net index** (e.g. nets stripped from the board, as in
  our samples): relational stage is a **no-op for that part**; it falls to the
  group's misc grid. The tool must degrade gracefully to "group + grid only"
  when connectivity is absent, and say so loudly in the report. **[OPEN-2].**
- Every unresolved passive is logged with the reason; the run report tallies
  resolved vs. orphaned so a human can gauge first-pass quality.

---

## 6. Stage C — Two-level shelf-pack grid

This is the **same shelf-pack math at both levels**, ported directly from
`grid_blocks` in SKiDL
(`/tmp/skidl-dev/src/skidl/schematics/place.py:1723`).

### 6.1 The shelf-pack core (ported, mm)

```python
def shelf_pack(blocks, pad):
    # blocks: list with .w, .h (mm) and a mutable .pos to set
    order = sorted(blocks, key=lambda b: (b.h, b.w), reverse=True)  # largest-first
    total_area = sum((b.w + pad) * (b.h + pad) for b in order) or 1.0
    row_limit = (total_area * 1.6) ** 0.5     # aim for ~1.6:1 landscape aspect
    x = y = row_h = 0.0
    for b in order:
        w, h = b.w + pad, b.h + pad
        if x > 0.0 and x + w > row_limit:     # start a new shelf (row)
            x, y, row_h = 0.0, y + row_h, 0.0
        b.pos = (x, y)                        # min-corner slot
        x += w
        row_h = max(row_h, h)
```

This is byte-for-byte the SKiDL schematic floorplan logic; only the bbox source
changes (footprint courtyard in mm instead of symbol `lbl_bbox`).

### 6.2 Two levels

- **Level 1 (within sheet):** the blocks are the sub-clusters produced by
  Stage B — each anchor+its-hugging-passives is one block, plus the crystal
  cluster, plus the misc-passive block. Shelf-pack them → the sheet's internal
  layout and its bounding box.
- **Level 2 (sheets against each other):** the blocks are the sheet bounding
  boxes from Level 1. Shelf-pack them → board-level arrangement.

Recursion is shallow (exactly two levels for a one-level sheet hierarchy). For
deeper schematic hierarchies the same call nests per level; **[OPEN-6]** decides
whether to recurse fully or cap at two levels for the first pass (proposed: cap
at two — group everything below the top sheet level into its top sheet for v1).

### 6.3 Placement origin / keep-clear

The Level-2 grid is translated into the **Tier-2 region**: a rectangle placed a
fixed clearance `TIER_GAP` away from the union bbox of all Tier-1
deterministic parts (and clear of any existing hand-placed parts). Default:
to the right of, or below, the deterministic zone — whichever yields the better
overall aspect. If there is no Tier-1 map and no placed parts, the grid origin
is just inside the Edge.Cuts outline (or `(0,0)` if none).

`pad` (inter-block gutter) is config: a couple of mm at Level 1, larger at
Level 2 so subsystems visibly separate.

---

## 7. Idempotency, locks, and overlap safety

- **Movable set is computed once at load** (§3.3) and is the *only* thing the
  tool ever writes. Locked parts and inferred-hand-placed parts are read (as
  potential passive parents / keep-clear obstacles) but never moved.
- **Idempotent:** re-running on an already-arranged board finds those parts now
  *placed* (outside the blob, not overlapping) → movable set shrinks toward
  empty → near no-op. Running mid-hand-layout only re-flows the parts still in
  the blob, around the work already done. Use `--force-all` only on a fresh
  import.
- **Lock semantics:** `IsLocked()` is the hard "do not move" signal and is
  always honored, independent of the blob heuristic.
- **Overlap avoidance (cheap, not DRC):** after each stage, do a courtyard
  sweep over *moved* parts; on overlap, nudge the later-placed part along the
  current shelf direction until clear. This is a local de-collision, not a
  global solver (consistent with the non-goals). **[OPEN-7]** confirm courtyard
  polyset intersection is fast enough at ~225 parts; if not, fall back to
  axis-aligned bbox overlap with a margin.
- **No writes on dry run:** `--dry-run` computes + reports metrics + optionally
  renders, without `SaveBoard`.

---

## 8. Deterministic override / pre-map (Tier 1 ↔ Tier 2)

### 8.1 What it is
A **per-board** declaration: "these specific references go at these exact
coordinates and rotations." For mechanically-constrained parts — pots, jacks,
switches, encoders, panel LEDs — whose position is dictated by the enclosure /
UI, not by the circuit. Tier 1 = these fixed parts. Tier 2 = the generalized
grid arranger handles everything else, held clear of Tier 1.

### 8.2 Declaration format
A sidecar file next to the board, `boardname.arrange.json` (or a Python dict
for the in-process API). JSON keeps it board-agnostic and human-diffable:

```json
{
  "units": "mm",
  "origin": "board",                // "board" = KiCad page coords; or "auxorigin"
  "lock_premapped": true,           // mark Tier-1 parts locked after placing
  "fixed": {
    "RV1": { "x": 20.0,  "y": 80.0, "rot": 0,   "side": "top" },
    "RV2": { "x": 40.0,  "y": 80.0, "rot": 0 },
    "J1":  { "x": 5.0,   "y": 10.0, "rot": 90,  "side": "top" },
    "SW1": { "x": 60.0,  "y": 80.0, "rot": 0 }
  },
  "keepout_extra_mm": 3.0,          // pad around Tier-1 union bbox for TIER_GAP
  "tier2_region": "auto"            // "auto" | "right" | "below" | [x,y,w,h]
}
```

Optional convenience: `"fixed_by_pattern"` accepting a ref regex → a generator
(e.g. evenly spaced pots) — deferred to v2; v1 takes explicit coords only, which
is unambiguous and reviewable.

### 8.3 How the tiers compose
1. **Apply Tier 1 first** (Stage 0). Set position + rotation for each `fixed`
   ref via `SetPosition`/`SetOrientationDegrees`. If `lock_premapped`, also lock
   them so re-runs leave them be.
2. **Tier-1 parts are registered as anchors** in the data model (with
   `placed = True`), so Stage B's passive-binding (§5) treats them as valid
   parents. **This is the crucial requirement:** a decoupling cap latches onto a
   deterministically-placed IC exactly as onto a grid-placed one — the parent
   lookup is the same net-index query; only the parent's *coordinates* differ
   (they're already final). The passive is then placed in absolute board coords
   hugging the fixed anchor, *outside* the Tier-2 grid flow.
3. **Compute the keep-clear zone** = union bbox of all Tier-1 parts (+ their
   latched passives) (+ existing hand-placed parts), expanded by
   `keepout_extra_mm`.
4. **Run Stages A–C on the remainder**, translating the Level-2 grid into the
   Tier-2 region so it never collides with Tier 1.

### 8.4 Edge cases
- A `fixed` ref that is itself a passive: allowed; it's placed exactly and is
  not re-snapped by §5.
- A `fixed` ref not present on the board: warn, skip (board-agnostic — the same
  pre-map may be reused across revisions).
- A passive whose parent is a Tier-1 anchor *and* that would land inside the
  keep-clear zone: that's fine and intended (it belongs to the fixed part); it
  is exempt from the Tier-2 keep-clear.
- Conflicting fixed coords (two parts same spot): report error, do not place
  either, abort write. **[OPEN]** or place-and-warn? Proposed: abort write,
  since a bad mechanical map is a user error worth surfacing loudly.

---

## 9. Validation plan + success metrics

### 9.1 Test vehicles
- **MR1** — the realistic stress case. *Note from grounding:* the on-disk
  `mr1.kicad_pcb` / `mr1_layout.kicad_pcb` I found are **168 footprints, already
  placed, single (empty) sheet, nets stripped**. The brief specifies a
  ~225-part, 13-sheet blob — **that artifact must be (re)generated** (fresh
  "Update PCB from Schematic" with hierarchy + nets intact). **[OPEN-8]:**
  produce the real blob board before validation; the current files validate
  only the grid/grouping-fallback paths, not the relational stage (no nets).
- **Small board** — a tg032-class single-MCU board (the SKiDL `stm32` sample,
  51 fp: 25×C, 11×R, 2×Y, 4×U, connectors, is a good stand-in *once nets are
  present*).

### 9.2 What "good" looks like (qualitative)
Rendered via `kicad-cli pcb export svg out.kicad_pcb` (or pcbnew plotting):
- Each sheet is a visibly distinct, non-overlapping cluster.
- Decoupling caps sit right next to their IC's power pads.
- Crystal + its load caps form one tight knot at the MCU osc pins.
- Subsystems are spread on a tidy landscape grid, not stacked at the origin.
- Tier-1 parts are exactly where the map says; nothing from Tier 2 intrudes on
  the keep-clear zone.

### 9.3 Numeric metrics (computed by the tool, emitted in the report)
| Metric | Definition | Target |
|---|---|---|
| **Zero courtyard overlaps** | count of moved-part courtyard intersections | **0** (hard) |
| **Passive-to-parent distance** | for each resolved support passive, center-to-parent-pad distance | median ≤ `expected_gap + tol` (e.g. ≤ 3 mm for decaps); 95th percentile bounded |
| **Passive resolution rate** | resolved passives / total passives | report; flag if < ~70% on a net-complete board |
| **Group separation** | min gap between any two sheet-cluster bounding boxes | ≥ Level-2 `pad` |
| **Group compactness** | each sheet bbox area / sum of its parts' courtyard areas | report (lower = tighter; sanity check, no hard target) |
| **Blob dispersion** | parts remaining within `R` of origin after run | **0** of the movable set |
| **Determinism** | byte-identical output across two runs, same inputs | identical (no RNG anywhere — shelf-pack is deterministic) |
| **Idempotency** | second run moves 0 parts | **0** moves |

### 9.4 Harness
- `arrange.py board.kicad_pcb [--premap map.json] [--out out.kicad_pcb]
  [--dry-run] [--force-all] [--report report.json]`.
- A pytest suite loads each test board, runs the arranger, asserts the metrics
  above on the resulting board object (no GUI), and optionally diffs the SVG
  render against a golden image for regression. Tests must run headless under
  plain `python3` (pcbnew imports fine — verified 9.0.7).

---

## 10. Open questions for human review

Numbered so review can respond pointwise. **bold = blocks build.**

- **[OPEN-1] Sheet metadata reliability.** `GetSheetname()` returned `""` on
  every sample board here. Is it actually populated in a normal post-import
  flow, or must we always rely on the `GetPath()` UUID-prefix fallback? Need one
  known-hierarchical freshly-imported board to confirm the grouping key.
- **[OPEN-2] Net availability.** Pad netnames were `""` on the samples (nets
  stripped). The entire relational stage depends on a populated net index.
  Confirm netnames survive "Update PCB from Schematic" on a real board; define
  behavior when they don't (currently: degrade to group+grid, warn).
- **[OPEN-3] "Unplaced = movable" detection.** The blob heuristic (§3.3) is the
  riskiest inference. Is `--force-all` (move every unlocked part) acceptable as
  the *default* for the common "fresh import" case, with the heuristic reserved
  for `--respect-placed` re-runs? Need a partially-hand-laid board to validate.
- **[OPEN-4] Multi-IC / cross-sheet passives.** Confirm the policy: coupling
  caps between two ICs → midpoint (series rule); cross-sheet → assign to nearer
  higher-pad-count IC's group + report. Acceptable for a first pass?
- **[OPEN-5] Rotation heuristics.** Should the first pass rotate passives at
  all, or only translate (keeping import orientation) to stay conservative?
  Proposed: snap to nearest 90° aligned to the hug axis; but free rotation may
  read cleaner for series parts. **Per CLAUDE.md, rotation/offset defaults are
  "domain values needing human review" — values below are proposals, not
  applied:** `DECAP_GAP≈0.7`, `PULL_GAP≈1.0`, `BULK_GAP≈1.5`, `XTAL_GAP≈1.0`,
  `SERIES_MAX_OFFSET≈8` mm.
- **[OPEN-6] Hierarchy depth.** Cap recursion at two levels (top sheet → parts)
  for v1, collapsing deeper sub-sheets into their top sheet? Or recurse fully?
- **[OPEN-7] Courtyard cost.** Is per-pair courtyard-polyset intersection fast
  enough at ~225 parts, or do we use bbox+margin overlap for v1? Also confirm
  the `GetBoundingBox`/`GetCourtyard` overloads that exclude silk on 9.0.7.
- **[OPEN-8] MR1 artifact.** The real ~225-part/13-sheet blob board does not
  exist on disk in usable form (the present MR1 files are placed/sheet-less/
  net-less). Who regenerates it, and is the SKiDL `stm32` sample (with nets
  re-imported) an acceptable interim relational-stage test?
- **[OPEN-9] Edge.Cuts presence.** Behavior when there's no board outline at
  all (origin fallback) vs. multiple outlines vs. outline drawn far from origin.

---

## 11. Eventual SKiDL integration (brief — do not design in detail yet)

The standalone PoC proves the *algorithm*; integration follows, pending the
SKiDL maintainer's blessing (PR #297 etiquette: credit devbisme, link the PR).

What SKiDL already provides, that the PoC re-derives heuristically:
- **Sheets / hierarchy** — SKiDL knows the true sheet tree; no
  `GetSheetname`/UUID guessing needed (resolves [OPEN-1]).
- **Connectivity groups** — `group_parts()`
  (`/tmp/skidl-dev/src/skidl/schematics/place.py:1290`) already unions
  net-connected parts; reuse for the connectivity fallback (resolves part of
  [OPEN-2]).
- **Power-net roles** — SKiDL tags power rails, so decap/bulk/pull detection
  uses real rail info instead of a name regex (resolves [OPEN-2] rail guessing).
- **Shelf-pack** — `grid_blocks` (`...place.py:1723`) is already the exact Stage
  C math; the PCB tool literally ports it to footprint bboxes, so the two stay
  in lockstep.

Integration shape (sketch only): a downstream `arrange_pcb()` capability that
consumes SKiDL's in-memory hierarchy + connectivity + rail tags and emits
footprint placements via `pcbnew`, sharing the relational + shelf-pack core with
the standalone script. The standalone tool's data model (§2) is deliberately a
thin struct so the SKiDL objects can substitute for the pcbnew-derived `FP`
records with no change to Stages B/C.
```

---

## 12. Review decisions (BINDING for build — resolved by maintainer review)

These override the corresponding [OPEN] items. Build to these.

1. **Connectivity + grouping come from the `.kicad_netlist`, NOT the board.**
   Parse the netlist (`(comp (ref ...) ... (sheetpath (names "/sheet1/")))` and
   the `(net (name ...) (node (ref ...) (pin ...)))` blocks). Join to footprints
   by **reference**. The `.kicad_pcb` is used ONLY for footprint geometry
   (courtyard/bbox) and write-back (SetPosition/orientation). This resolves
   [OPEN-1] (sheet key = netlist `sheetpath`, always present) and [OPEN-2] (net
   index from netlist, always complete). `GetSheetname()`/`pad.GetNetname()` are
   NOT relied upon. CLI takes `--netlist board.kicad_netlist` (default: sibling
   file next to the .kicad_pcb).
2. **Rotation: snap to nearest 90°, aligned to the hug axis.** Decaps face the
   served pad; series R align inline; pull-ups align radially out. Not
   translate-only, not free rotation.
3. **Gap defaults (mm), tunable via config/CLI:** DECAP_GAP 0.7, PULL_GAP 1.0,
   BULK_GAP 1.5, XTAL_GAP 1.0, SERIES_MAX_OFFSET 8. These are starting values;
   expose as overridable constants.
4. **[OPEN-3]** `--force-all` (move every unlocked footprint) is the DEFAULT.
   The blob heuristic is opt-in behind `--respect-placed`. Locked parts are
   never moved either way.
5. **[OPEN-4]** Multi-IC passive → midpoint (series rule); cross-sheet → assign
   to nearer/higher-pad-count IC's group + note in report.
6. **[OPEN-6]** Cap recursion at two levels for v1 (collapse deeper sub-sheets
   into their top sheet).
7. **[OPEN-7]** Use axis-aligned bbox+margin overlap for v1 (courtyard polyset
   deferred). Confirm the silk-excluding bbox overload at build time.
8. **[OPEN-9]** No Edge.Cuts → grid origin at (0,0)-ish; don't fail.
9. **Validation vehicle:** join the MR1 netlist (`/tmp/mr1_out/
   generate_pcb.kicad_netlist`, 223 comps, 12 sheets, full nets) to a board
   carrying the footprints; with `--force-all` the arranger re-flows them.
   Also test the small board. Emit the §9.3 metrics in a JSON report and render
   with `kicad-cli pcb export svg`.
