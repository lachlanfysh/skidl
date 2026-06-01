# Snap Backend Split + Bug Fixes — Final Review Package

> One-stop entry point for a final review. This `review/full-package` branch
> integrates **all** the work (refactor + bug fixes + orientation) on the common
> base (the maintainer's `development`), verified together. Repo:
> `lachlanfysh/skidl` (fork of `devbisme/skidl`).

## What this is

Adds a "snap" schematic feature to SKiDL's KiCad-9 backend (place 2-pin parts onto
IC pins, cleaner label/power handling) AND fixes two bugs in the maintainer's
`development` branch found along the way. Built against the design in
`ARCHITECTURE-snap-backend-split.md` (5 prior review rounds).

## The pieces (and where each lives)

| Piece | Files | Status |
|---|---|---|
| **Power-symbol bug fix** | `tools/kicad9/sexp_schematic.py` (`_power_lib_ids_in_elements`) | verified; isolated PR-ready branch `fix/power-symbol-defs` |
| **Snap feature** | `schematics/snap.py` (geometry) | verified, v3 output parity |
| **Architecture refactor** | `schematics/decisions.py`, `schematics/backend.py`, `tools/kicad9/backend.py` | decisions relocated behind `SchematicBackend` iface; output-preserving |
| **Label-aware *part* orientation** | `schematics/place.py` (`label_overlap_cost`) | verified; rotates parts so net-labels don't pile on bodies/value text |

## Verification (this combined branch)

- **Full suite:** `pytest tests/unit_tests/ai_tests/` → **292 passed, 1 skipped, 1
  failed** (only pre-existing `test_generate_svg`, missing `netlistsvg` binary).
- **esp32 parity:** 6 sheets / 43 wires / 37 labels / **0 missing** — unchanged
  before/after the refactor (refactor is pure restructure).
- **MR1 (~225-part real board):** 0 missing symbols.
- **Orientation:** transistor net-labels splay clear of bodies (8 perpendicular
  labels = structural optimum for 3-pin parts). See
  `review-artifacts/orientation_fix_before_after.png`.
- **No new ERC shorts** (controlled for placement nondeterminism with fixed seed).

## sexp_schematic.py footprint vs the maintainer's `development`

`+206 / −16` (down from `+633` before the refactor; his refactor preserved, −16).
Everything remaining there is the `Kicad9Backend` adapter + KiCad render-space
geometry + S-expr emission — all backend-by-design per the architecture doc. No
decision logic and no duplicated geometry remain in the backend (verified at AST
level: `schematics/decisions.py` and `schematics/backend.py` import nothing from
`tools/kicad9`).

## Two bugs in `development` (diagnosed + fixed)

1. **Missing/unknown components** — power-symbol *instances* emitted on auto-stubbed
   child sheets with no matching `lib_symbols` *definition* (defs scoped from
   `node.wires`, instances from pins). Fixed by deriving per-sheet defs from emitted
   instances.
2. **Net-label piling (part-orientation fix)** — root cause: `adjust_orientations`
   only ran on wire-connected groups (**floating parts got no orientation pass**) and
   scored orientation by `net_tension` only (**no label-overlap term**). Fixed by
   adding `label_overlap_cost` to the orientation search + an orientation pass for
   floating parts. Additive, gated (`label_aware_orientation`), `net_tension`
   untouched. See `ORIENTATION-label-aware.md`.

   **Scope/honesty:** this fixes which way the *part* is rotated (so labels splay off
   the body). It does **not** change which way an emitted `global_label` *faces*, and
   the floating-parts orientation pass is **skipped for groups >20 under `auto_stub`**
   (`place_floating_parts` grid-places and returns early). See "Still open" below.

## Deliberately deferred (output-changing → need golden fixtures + maintainer sign-off)

- `render_node` root/child unification (root sheet would gain bus/T-junction handling).
- `solve_snap_tx` in true render-space (P1b; fixes a latent transform bug). Currently
  raises `NotImplementedError` — snap solves in placement space via `schematics.snap`.

## Net-label facing — investigated, NOT a bug

A reviewer flagged `BNET` labels looking "rotated wrong, wire running through them."
Investigated to root: **the per-label facing is correct.** A single transistor locked
into all four rotations renders clean in every case — B-down → label below, B-up →
label above, B-left/right → label to the side — no body-crossing. The `orient_map`
(`{"R":180,"D":270,"L":0,"U":90}`) keyed on `calc_pin_dir` already produces the right
angle, and `deconflict_labels` (which derives its move direction from that angle) moves
each label *away* from the body. A naive `U`/`D` swap was tried and **reverted** — it
breaks the cases that currently work. No code change here.

## Still open (net-label *emission* on shared nets — maintainer's domain)

The reviewer's artifact is real but lives one level up: on a **shared net spanning many
discrete parts** (e.g. 8 separate transistors' B pins tied to `BNET`), the net is drawn
as a **bus wire down the column with a `global_label` emitted at every tap** — so the
bus runs through the (individually correct) labels, and two taps even land at identical
coordinates.

- **This is specific to the multi-part signal bus.** The single-context bus cases work
  well and are untouched: IC pins fanning to a bus (through passives), LED-driver chains
  — `find_power_bus_runs` already suppresses *its* tap labels, so those look clean.
- **Fix shape, if pursued (lowest-risk first):** extend the existing wired-pin label
  suppression — on a net whose pins are already joined by an emitted wire, keep one
  naming label (+ any cross-sheet terminal) and drop the redundant taps. This changes
  **no routing**, so it cannot regress the IC/LED/power buses; it only removes labels
  whose connectivity the wire already provides. Needs ERC verification before adoption.
- **Deeper question (left for the maintainer):** whether a bus *should* be drawn across
  many independent parts at all, or whether such stub nets should stay label-only. That
  one is a genuine net-strategy call and a bridge too far to decide here.

## Known limitation (architectural, not a bug)

Off-pin net labels joined by a short wire come from the routed-`NetTerminal` design:
terminals sit at the routing-channel edge so the autorouter can reach them; anchoring
them on-pin **breaks routing** (verified: 14 routing failures). True on-pin labels
require a stub-vs-route net-selection strategy — a separate design decision.

## Docs in this package
- `ARCHITECTURE-snap-backend-split.md` — the design (interface, decisions, deferred items)
- `STATUS-snap-implementation.md` — what's live vs deferred, interface honesty notes
- `ORIENTATION-label-aware.md` — the orientation fix
- `review-artifacts/` — before/after renders

## Review questions
1. Is the `SchematicBackend` interface surface right, and the decision/emission split clean?
2. Is the label-overlap cost (additive, gated, floating-parts-scoped) a sound shape to
   offer upstream into `adjust_orientations`?
3. Anything in the remaining `sexp_schematic.py` +206 that should still move out?
