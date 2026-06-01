# Codex design brief — reserve net-label space during placement

> **Ask:** review and help design a fix so the force-directed placer reserves room
> for **net labels**, not just part bodies + routing channels. This is the root cause
> behind most of the "messy schematic" symptoms below. It lives in the placer
> (`src/skidl/schematics/place.py`), which is the maintainer's core routine — so this
> is a *design* brief, not a finished patch. We want the approach vetted before coding.
>
> Repo: `lachlanfysh/skidl` (fork of `devbisme/skidl`), branch `review/full-package`.
> Run env: `KICAD9_SYMBOL_DIR=/usr/share/kicad/symbols SKIDL_TOOL=KICAD9
> PYTHONPATH=<worktree>/src`. Placement is non-deterministic (`random.seed(None)`) —
> verify with a fixed seed + structural metrics, never byte-diff.

## Symptoms (observed on a real ~225-part board + a stress test)

Six observations, which collapse into **three mechanisms**. Only **Mechanism A** is in
scope here; B and C are noted so they're not conflated.

1. **A pot body mashed into an IC's labels.** A potentiometer (`RV4 "Depth"`) was
   placed 11.6 mm from a 74HC4051 mux (`U2`); the mux's right-side `POT_*` net labels
   and the pot's own wiper label overlap in the gap. No false net (different nets) —
   just unreadable. → **Mechanism A.**
2. **Dense label pile at IC pins.** 8 `SW_*` labels land on a 74HC165's D0–D7 pins
   (2.54 mm pitch); the label flags are taller than the pitch, so they collide, and the
   post-hoc deconflictor shoves them sideways into a staggered pull-down cluster. →
   **Mechanism A.**
3. **Inconsistent deconfliction across identical parts.** Of 4 identical TLC59xx LED
   drivers, 2 render with clean left-side labels and 2 get nudged-off-body + wired —
   purely because of where each landed relative to neighbors. → **Mechanism A.**
4. **Vertical GND power symbols mis-oriented** — separate bug, see "Out of scope (B)".
5. **A manually-stubbed multi-pin net still routes a bus** — separate, see "(C)".
6. `SW_*` labels exist at all — **by design** (auto_stub's "wire-near, label-far"
   partial stubbing; one stub label per net, the rest wired locally). Not a bug.

## Mechanism A — root cause

`add_placement_bboxes()` (`place.py:123`) builds each part's `place_bbox` (used by the
force-directed placer and all overlap tests) as:

```
place_bbox = lbl_bbox                       # symbol body + ref/value text ONLY
           + routing padding per side        # 1 GRID channel per CONNECTED, NON-STUB pin
                                             #   (× expansion_factor)
```

The hole is **specifically the stubs decided *after* this point**, not all labels:

- Pins that are **already `pin.stub` at preprocessing** (power nets, manual stubs) *do*
  get label space — `calc_hier_label_bbox(pin.net.name, pin.orientation)` is folded into
  `lbl_bbox` (`gen_schematic.py:571-575`). So `lbl_bbox` already reserves room for
  *known* stub labels. (Earlier draft of this brief wrongly said it didn't — corrected
  per review.)
- **Fanout/selective stubs are decided *after* placement** (`_apply_deferred_stubs`), so
  those pins are *not* stubbed when `lbl_bbox`/`place_bbox` are built → they reserve no
  label space → their labels land in whatever the placer packed next to them. This is
  the actual gap behind symptoms 1–3 (mux/pot, dense IC pins, LED inconsistency).
- The routing-padding loop also *excludes* stub pins (`if pin.stub is False ...`), and
  the fixed label box (`LABEL_REACH`) may be under-sized for long net names. Secondary,
  but relevant.

Net labels stick out 5–7 mm past the body. The placer packs parts closer than that, so
adjacent parts' labels (and bodies) overlap. `deconflict_labels()`
(`decisions.py:308`) is a **post-hoc band-aid**: it nudges a label off a body *only when
its box intersects a component body*, then adds a connecting wire — and bails if the new
spot collides with another net. It cannot fix label-vs-label overlap, cannot reserve
space, and fires inconsistently (position-dependent → symptom 3).

## Reusable prior art — but mind the coordinate space

We already have a per-pin net-label box estimator in the same file, added for a
label-aware *orientation* cost:

- `label_overlap_cost(part, **options)` (`place.py:532`) and its helper that builds a
  label box from the pin anchor outward along the pin's render direction —
  `LABEL_REACH = 250` mil, `LABEL_HALF_H = 30` mil (`place.py:451`). `GRID = 50` mil.

**Coordinate-space caveat (per review):** `place_bbox`/`lbl_bbox` are **local** part
geometry (later transformed by `part.tx`), whereas `label_overlap_cost` builds its boxes
in **current placed** coordinates (`pin.pt * part.tx`). Don't reuse it directly — first
**extract a shared local-space label-bbox helper** (anchor at `pin.pt`, extend along the
pin's *local* orientation), then call it from *both* `add_placement_bboxes` (local) and
`label_overlap_cost` (compose with `part.tx`). (Caveat: `LABEL_REACH` is fixed; real
label width scales with net-name length — see open questions.)

## The timing constraint (critical)

`node.place()` runs **before** `_apply_deferred_stubs()`:

```
gen_schematic (gen_schematic.py):
  687  auto_stub_nets(...)          # power + manual stubs marked here (pin.stub=True)
  700  node.place(...)              # <-- placement happens HERE
  702  _apply_deferred_stubs(...)   # fanout-deferred stubs marked AFTER placement
  704  node.route(...)
  ...  (700–704 repeat in an ERC-retry loop at 773, with rising expansion_factor)
```

So at placement time, **fanout-deferred stub pins are not yet `pin.stub=True`** — the
placer can't simply read `pin.stub` to know which pins will be labeled. (Power-net and
manual stubs *are* known pre-place.) The mux/pot labels in symptoms 1–3 are largely
deferred stubs, i.e. decided *after* the placement that caused the overlap.

**Node staleness (per review):** stub state isn't just a per-pin flag — `SchNode`
construction *adds/omits NetTerminals based on `net.stub`/`pin.stub`*
(`sch_node.py:155, 184`). So once `_apply_deferred_stubs` mutates stub state, the
existing node has **stale terminals/groups**. A second placement on the same node is
unsafe; the node must be **rebuilt** from the frozen final stub state. (Today's single
place→stub→route path tolerates the staleness only because stubbing merely leaves a
terminal unwired — re-placing is a different matter.)

## Proposed plan (revised per Codex round 1)

A throwaway first placement is unavoidable: deferred stubbing depends on inter-pin
**distance**, which only exists after a placement. So:

1. **Pass 1 — classify.** Place (as today) just to get distances; run
   `_apply_deferred_stubs` to **freeze the final stub set**.
2. **Rebuild `SchNode`** from that frozen stub state, so NetTerminals/groups match the
   final labels (avoids the staleness above).
3. **Pass 2 — label-aware place.** Place the rebuilt node with `add_placement_bboxes`
   expanded by the **shared local-space label-bbox helper** for every pin that is now a
   stub. This is an **explicit new "label-clearance placement pass" *before* routing** —
   *not* the ERC-retry loop. (The normal flow is place→stub→classify→route at
   `gen_schematic.py:699-704`; the ERC loop at ~773 runs *only after an ERC failure* and
   is the wrong hook for this.)
4. **Route** the label-aware placement.

Keep `deconflict_labels` as a **safety net** for residual overlaps (don't remove it
yet). Start with a **fixed per-side label margin** (simple, deterministic); add
text-measured per-net width later once behavior is proven.

Cost: one extra full placement pass. Placement is the expensive step, so flag this as a
tradeoff — could explore a coarse/cheap Pass 1 if it bites.

## Open questions for Codex (round 2)

1. **Double-placement cost:** the plan needs Pass 1 (classify) + Pass 2 (label-aware).
   Acceptable, or should Pass 1 be a coarse/cheap placement used only to estimate
   distances for stub classification? Anything that makes rebuilding `SchNode`
   mid-`gen_schematic` awkward (UUID stability, child-node identity, the ERC loop that
   itself re-places at ~773)?
2. **Label width:** `LABEL_REACH` is fixed (250 mil); real `global_label` width depends
   on net-name length. Fixed margin first (agreed); for the later text-measured version,
   is a backend call from the local-space helper acceptable, or measure during the
   per-pin pass that already knows the net name?
3. **Granularity:** expand per-side by the max label extent on that side (matches the
   existing routing-padding model), or a uniform margin?
4. **`deconflict_labels` as safety net:** agreed to keep for now. Any case where reserved
   space + remaining deconfliction *fight* (e.g. space reserved one way, deconfliction
   nudges another)?
5. **>20-floating-parts grid path** (`place_floating_parts`, `place.py:1512-1526`): it
   **already calls `add_placement_bboxes` before grid spacing**, so expanded bboxes flow
   through automatically — good. What it still skips is the **orientation pass**
   (`place.py:1578-1584`), so large stub groups won't get label-aware *rotation*. Worth
   addressing in the same change or separately?
6. **Sheet growth / non-determinism:** acceptable to spread parts out more? Suggested
   verification: fixed seed, compare structural metrics (label-on-body count,
   label-bbox overlaps, deconfliction-move count, ERC violations) before/after on the
   esp32 example + the real board, not pixels.

## Out of scope here (separate fixes, documented so they're not conflated)

- **(B) Power-symbol rotation** — `_power_symbol_to_sexp` (`sexp_schematic.py:79`)
  hardcodes `angle = 0` ("we don't rotate"); GND symbols on upward-facing pins render
  mis-oriented. Net labels rotate via `calc_pin_dir`; power symbols should too. Small,
  independent, render-verify all four pin directions.
- **(C) Manual-stub bus** — wire-emission at `sexp_schematic.py:1092` skips only on
  `net._stub`, while sibling paths (lines 883, 1246) check `net.stub` *or* `net._stub`.
  So a manually `net.stub=True` multi-pin net still routes a bus *and* labels. One-line
  consistency fix; only affects manual stubbing (not auto_stub boards).
- **Net-label facing** — investigated, **not a bug**; the existing `orient_map` renders
  correctly in all four orientations (a U/D swap was tried and reverted).
