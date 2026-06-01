# Label-aware orientation (implemented)

> Addendum to `ARCHITECTURE-snap-backend-split.md` (on branch `docs/snap-backend-split`).
> Implemented on branch `feat/label-aware-orientation`. Lives entirely in the
> tool-agnostic placer (`src/skidl/schematics/place.py`).

## Problem

Net-label text piled onto symbol bodies / pin letters / value text on parts placed
without wire connections — e.g. fully-stubbed transistors, where all three E/B/C net
labels stacked vertically over the symbol and the `Q_PNP_CBE` value text, unreadable.

**Root cause (verified by instrumentation):**
- `adjust_orientations` only ran on **wire-connected** part groups. **Floating parts
  (all pins on stub nets) got no orientation pass at all** — they kept whatever
  orientation they were instantiated with.
- The orientation cost was purely `net_tension` (wire-pull distance). **No term
  anywhere accounted for whether a chosen orientation makes net labels overlap.**

This is also why an earlier emit-stage deconfliction approach couldn't fix it: by emit
time placement is frozen, so the only move is translate-the-label + wire (ugly, and it
fires rarely). Orientation is a *placement-stage* decision; that's where it belongs.

## Fix

In `src/skidl/schematics/place.py` (the tool-agnostic placer):

- **`label_overlap_cost(part, **options) -> float`** — for the part's current
  orientation, estimates each stubbed/connected pin's rendered net-label box (start at
  `pin.pt * part.tx`, extend `LABEL_REACH` along the pin's outward direction, small
  perpendicular half-height) and penalizes overlap with:
  - other parts' `place_bbox` (label over a neighbour body),
  - this part's own body,
  - this part's value/ref **text band** (the key rotation discriminator — own-body
    geometry alone is rotation-invariant),
  - this part's other labels.
- **Orientation cost** in `adjust_orientations` becomes
  `net_tension(part) + W * label_overlap_cost(part)`, `W = 75`, gated behind
  `label_aware_orientation` (default **on**). `net_tension` itself is unchanged.
- **Floating parts now get an orientation pass** (closing the gap that caused the
  piling), scoped to floating parts; **opt-in for wire-connected groups**
  (`label_aware_connected`, default **off**) so it never rotates a connected part's
  orientation purely for readability and risk merging a stub label onto a live node.

Tuned constants (place-time KiCad mils, GRID=50): `W=75`, `LABEL_REACH=250`,
`LABEL_HALF_H=30`, `LABEL_BODY_MARGIN=40`, text band `±300 × ±70`.

## Verified

- **Transistor example:** E/B/C labels splay clear of the body; only the
  structurally-unavoidable 3rd label on a 3-pin part stays perpendicular, sitting in
  open space below the value text (a 3-pin part with pins 90° apart can have at most 2
  of 3 labels horizontal in any orientation — this is optimal, not a cost-function
  miss). Before/after renders confirm.
- **MR1 (~225 parts) + esp32:** 0 missing symbols.
- **ERC:** no new `multiple_net_names` / `pin_to_pin` (controlled for placement
  nondeterminism with a fixed `seed=0`; the one pre-existing MR1 `multiple_net_names`
  appears in OFF runs too).
- **Full suite:** 292 passed, 1 skipped, 1 failed (pre-existing `netlistsvg`-missing).

## Upstream framing

Additive and gated — slots into the maintainer's existing `adjust_orientations` 8-way
search as one more cost term, with `net_tension` untouched. Offered for adoption; if not
adopted upstream it runs in our flow regardless (we already set `rotate_parts=True`).
