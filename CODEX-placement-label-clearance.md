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

Two problems:

- The comment says "including any net labels" but **`lbl_bbox` does not include net
  labels** — those are emitted later at the pin, extending ~`LABEL_REACH` mil outward.
  So a part's footprint in the placer is body + routing channels, with **zero room for
  the net-label flags that will be drawn on its stub pins.**
- The padding loop *excludes* stub pins (`if pin.stub is False and pin.is_connected()`),
  so the pins that will actually get **labels** (stubs) contribute **no** clearance at
  all. Exactly backwards for label spacing.

Net labels stick out 5–7 mm past the body. The placer packs parts closer than that, so
adjacent parts' labels (and bodies) overlap. `deconflict_labels()`
(`decisions.py:308`) is a **post-hoc band-aid**: it nudges a label off a body *only when
its box intersects a component body*, then adds a connecting wire — and bails if the new
spot collides with another net. It cannot fix label-vs-label overlap, cannot reserve
space, and fires inconsistently (position-dependent → symptom 3).

## Reusable prior art

We already have a per-pin net-label box estimator in the same file, added for a
label-aware *orientation* cost:

- `label_overlap_cost(part, **options)` (`place.py:532`) and its helper that builds a
  label box from the pin anchor outward along the pin's render direction —
  `LABEL_REACH = 250` mil, `LABEL_HALF_H = 30` mil (`place.py:451`). `GRID = 50` mil.

The same geometry can feed a placement-bbox expansion. (Caveat: `LABEL_REACH` is a fixed
length; real label width scales with net-name string length — see open questions.)

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

## Proposed direction (for Codex to vet / refine)

Expand `place_bbox` to reserve net-label space on each pin's outward side, reusing the
`label_overlap_cost` box geometry. Candidate strategies for the timing problem:

- **(a) Conservative** — reserve label clearance for *every connected pin that could be
  stubbed* (not just already-stub pins). Over-reserves → larger sheets, but simple and
  deterministic.
- **(b) Two-pass / reuse the retry loop** — place once, run `_apply_deferred_stubs`, then
  re-place with label-aware bboxes now that stub state is known. The 700→773 loop and
  the `expansion_factor` mechanism already re-place; this could hook the same machinery
  (a "label_expansion" pass) rather than add a new loop.
- **(c) Predict** — approximate which nets will be deferred-stubbed before placement
  (fanout is known pre-place; distance is not). Fragile; least preferred.

We lean toward **(b)** — it reuses existing re-place infrastructure and only pays the
cost when labels actually exist — but want Codex's read.

## Open questions for Codex

1. **Timing:** is (b) sound — re-place after `_apply_deferred_stubs` using label-aware
   bboxes? Does anything downstream assume `place_bbox` is stable across the route pass?
   Is there a cleaner hook than the ERC-retry loop?
2. **Label width:** `LABEL_REACH` is fixed (250 mil); real `global_label` width depends
   on net-name length. Reserve a fixed channel (simple, sometimes too small/large) or
   measure per-net text width (accurate, tool-specific — would need a backend call)?
3. **Granularity:** expand per-side by the max label extent on that side, or a uniform
   margin? Per-side matches the routing-padding model already there.
4. **Interaction with `deconflict_labels`:** if placement reserves space, deconfliction
   should fire far less. Keep it as a safety net, or does reserved space make it
   redundant (and its band-aid wires removable)?
5. **The >20-floating-parts grid path** (`place_floating_parts`, `place.py:1516`) skips
   force-directed placement and grid-places — it would also need label-aware spacing, or
   it'll stay cramped for large stub groups.
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
