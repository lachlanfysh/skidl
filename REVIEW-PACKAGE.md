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

## Still open (net-label *emission* — maintainer's domain, NOT claimed fixed)

Distinct from the part-orientation fix above. On a **shared multi-pin net** (e.g. all
transistors' B pins tied to `BNET`), the net is drawn as a wire down the column and a
`global_label` is emitted at *every* tap, so:

1. **Net-label facing** — `net_label_to_sexp` faces the label purely from
   `calc_pin_dir(pin)` via `orient_map = {"R":180,"D":270,"L":0,"U":90}`. For a
   down-facing pin the label is vertical; since it sits *on* the shared wire, the wire
   runs through the text. Facing alone can't lift text off a collinear wire — needs a
   facing + offset (or label-vs-wire) strategy. **Before changing the angle map, add a
   regression test pinning emitted S-expr angles for U/D/L/R pins** (KiCad label-angle
   semantics are easy to get subtly backwards — verified by render, not assumed).
2. **Redundant labels on wired taps** — `find_overlapping_pins` suppresses labels for
   *overlapping* same-net pin clusters, but collinear wired taps aren't overlapping, so
   8 `BNET` labels get emitted where 1 would do. Aesthetic, not electrical.

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
