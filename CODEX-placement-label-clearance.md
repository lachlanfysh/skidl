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

**The bug is purely timing — the machinery to reserve label space already exists.** Pins
that are `pin.stub` *at preprocessing time* get a label bbox folded into `lbl_bbox` via
`calc_hier_label_bbox(name, orientation)` (`calc_part_bbox`, `gen_schematic.py:569-575`).
That box is **text-aware** (`lbl_len = len(name) * PIN_LABEL_FONT_SIZE + HIER_TERM_SIZE`,
per orientation, in backend units) — note it's an *approximation* of the emitted
`global_label` geometry (`net_label_to_sexp`, `sexp_schematic.py:680`), not its exact
footprint, but it's the same box the existing preprocessing already trusts. So for
*known* stubs (power nets, manual stubs) the placer already reserves name-sized label
room. (Earlier draft wrongly said `lbl_bbox` excluded labels — corrected per review.)

The gap is the stubs decided **after** preprocessing/placement, in this order
(`gen_schematic.py:700-704`):

```
node.place()                        # preprocessing (lbl_bbox) already happened upstream
_apply_deferred_stubs()             # stubs deferred-FANOUT nets directly in child sheets
_classify_and_stub_complex_nets()   # stubs by PIN-COUNT (>max_wire_pins, :315) and
                                    #   inter-pin DISTANCE (>max_wire_dist, :340), plus
                                    #   single-real-pin / small-child-sheet cases
node.route()
```

So the final stub set is only complete **after both** `_apply_deferred_stubs` *and*
`_classify_and_stub_complex_nets`. The mux/pot and `SW_n` labels behind symptoms 1–3 are
**distance-stubbed** by `_classify_and_stub_complex_nets` — i.e. decided *after* the
placement that caused the overlap, and *after* the `lbl_bbox` preprocessing that would
have reserved their space. (Note: the distance criterion lives in
`_classify_and_stub_complex_nets`, **not** in `_apply_deferred_stubs`, which stubs
deferred-fanout nets directly — corrected per review.)

Net labels stick out 5–7 mm past the body. The placer packs parts closer than that, so
adjacent parts' labels (and bodies) overlap. `deconflict_labels()`
(`decisions.py:308`) is a **post-hoc band-aid**: it nudges a label off a body *only when
its box intersects a component body*, then adds a connecting wire — and bails if the new
spot collides with another net. It cannot fix label-vs-label overlap, cannot reserve
space, and fires inconsistently (position-dependent → symptom 3).

## No new estimator needed — reuse `calc_hier_label_bbox`

Because `calc_part_bbox` already folds the **existing backend label bbox**
(`calc_hier_label_bbox`, text-aware, local-space) into `lbl_bbox` for stub pins, **the
fix is to re-run that preprocessing once the final stub set is known**, not to invent a
new label-box estimator. This sidesteps the earlier idea of reusing `label_overlap_cost`
(`place.py:532`) — which would have been a coordinate-space hazard anyway (`place_bbox`
is **local**; `label_overlap_cost` works in **placed** coords via `pin.pt * part.tx`).

Only if `calc_hier_label_bbox`'s box proves too tight in practice would we add an
explicit per-side margin (and then a shared local-space helper, used by both placement
and the orientation cost). Default: don't.

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

## Proposed plan (revised per Codex rounds 1–2)

A throwaway first placement is unavoidable: `_classify_and_stub_complex_nets` stubs on
inter-pin **distance**, which only exists after a placement. So, gated behind `auto_stub`
(or an explicit `label_clearance` option):

1. **Pass 1 — classify (full placement, not coarse).** Place as today; run
   `_apply_deferred_stubs` **and** `_classify_and_stub_complex_nets` to **freeze the
   *complete* final stub set** (both fanout and pin-count/distance stubs). Classification
   is placement-sensitive, so Pass 1 must be a real placement.
2. **Re-run the bbox preprocessing** (`calc_part_bbox` / `initialize`) with the frozen
   stub state, so every now-stub pin gets its text-aware `calc_hier_label_bbox` box
   folded into `lbl_bbox`. **This is the core of the fix** — no new estimator.
3. **Rebuild `SchNode`** from the frozen stub state, so NetTerminals/groups match the
   final labels (avoids the staleness in §"Node staleness").
4. **Pass 2 — place** the rebuilt node. `add_placement_bboxes` builds `place_bbox` from
   the now-label-inclusive `lbl_bbox`, so the force-directed placer (and the >20 grid
   path, which also reads `lbl_bbox`) reserve label room automatically. This is an
   **explicit "label-clearance placement pass" *before* routing** — *not* the ERC-retry
   loop (which runs only after an ERC failure at ~773 and is the wrong hook).
5. **Route** with the **frozen stub set**.

**Freeze the classifiers on Pass 2 (per review).** Today `_apply_deferred_stubs` *and*
`_classify_and_stub_complex_nets` run after *every* `node.place()`
(`gen_schematic.py:701-703`). If Pass 2 reuses that path unchanged, they'd re-run and
mutate the stub set *after* the label-aware bboxes were computed — undoing the freeze.
So Pass 2 must **place and route against the frozen stubs without reclassifying**
(skip both classifier calls on Pass 2), unless we deliberately want another
classify → re-preprocess → rebuild cycle (a possible but unneeded convergence loop).

Keep `deconflict_labels` as a **safety net** for residual overlaps — and **instrument how
often it fires** before deciding whether its little connecting wires can be retired.

**Cost:** one extra full placement pass + a preprocessing re-run + a node rebuild, only
when `auto_stub` is on. Accepted (per review). Could revisit a coarse Pass 1 only if it
bites.

## Resolved by review

- **Label width / granularity:** moot — `calc_hier_label_bbox` is already text-aware and
  per-orientation; re-running preprocessing folds correct per-pin boxes into `lbl_bbox`.
  No fixed margin, no new estimator unless metrics show it's too tight.
- **Double-placement cost:** accepted; full (not coarse) Pass 1; gate behind `auto_stub`
  / `label_clearance`.
- **`deconflict_labels`:** keep as safety net; instrument firing rate before retiring.
- **>20-floating path:** already reads `lbl_bbox`, so it benefits automatically; only its
  *orientation* pass (`place.py:1578-1584`) is skipped (see Q3 below).

## Open questions for Codex (round 3)

1. **Re-run mechanics:** cleanest way to re-run `calc_part_bbox`/`initialize` and rebuild
   `SchNode` mid-`gen_schematic` without UUID churn or child-node identity drift? Does
   anything cache `lbl_bbox`/`place_bbox`/terminals across the route pass or the ERC
   re-place loop (~773) that would need invalidating?
2. **Interaction with the ERC re-place loop:** that loop already re-places with rising
   `expansion_factor`. Does the new Pass-2 placement subsume it, sit before it, or need
   to re-freeze stubs on each ERC iteration?
3. **Orientation for large stub groups:** the >20-grid path skips the label-aware
   orientation pass — fold that into this change, or leave separate?
4. **Verification:** fixed seed; compare structural metrics (label-on-body count,
   label-bbox overlaps, `deconflict_labels` move count, ERC violations) before/after on
   the esp32 example + the real board — not pixels. Agree on the metric set?

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
