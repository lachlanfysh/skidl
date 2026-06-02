# SKiDL schematic placement & net-label findings — for Codex review

> Branch `feat/label-clearance-placement` on `lachlanfysh/skidl` (fork of
> `devbisme/skidl`). This documents an extended investigation into schematic
> *readability* on real boards (a Daisy-Seed drum board, "MR1", ~225 parts, 13
> sheets; plus the repo's `esp32_audio_board` example). Run env:
> `KICAD9_SYMBOL_DIR=/usr/share/kicad/symbols SKIDL_TOOL=KICAD9
> PYTHONPATH=<worktree>/src`. Placement is non-deterministic (`seed=` only
> partially pins it) — verify with ERC + structural metrics, never byte-diff.

## TL;DR — what shipped, what's blocked

| # | Item | State | Commit |
|---|---|---|---|
| A | **Dangling wire-remnant purge** (snap leaves stale router wires) | **DONE, safe** | `67545e2a` |
| B | **Stagger fan starts past the IC label extent** | **DONE** | `ebbe0d76` |
| C | **Size-aware grid floorplan of wire-connected units** (`grid_blocks`, opt-in) | **DONE, scoped** | `5cbc486e` |
| D | **Label-clearance two-pass** (reserve label space in placement) | opt-in, neutral | `b4fada6a` |
| E | **Remove redundant stub labels** (the `SW_*`-on-IC-pins clutter) | **INFEASIBLE as a label filter** | — |

Verification baseline for all: full `tests/unit_tests/ai_tests/` = **292 passed,
1 skipped, 1 failed** (only pre-existing `netlistsvg`-missing).

## Net-label *facing* — settled, NOT a bug (history clarified)

A long-standing worry was that vertical net labels faced *into* part bodies and
that the maintainer's refactor caused it. Resolved with git + render:

- Original `orient_map` (`ancestor e19d9a6a`, `master`): `{"R":180,"D":90,"L":0,"U":270}`.
- Current: `{"R":180,"D":270,"L":0,"U":90}` — **the maintainer changed it** in
  commit `4bd527dc` (2026-05-14), titled *"Fixed incorrect orientation of
  vertical net labels."*
- Rendering a single transistor locked into all four rotations: the **current
  (maintainer's) map is correct** in every orientation. Swapping back to the
  original reproduces labels rendering into the body on up-pins.

**Conclusion: the maintainer fixed vertical-label facing; it is not currently a
bug.** Do not "swap U/D" — that re-introduces the original defect.

## C — `grid_blocks` (size-aware floorplan of units)

`place_blocks` arranges connected groups with force-directed *similarity*
placement for small block counts → groups land scattered / overlapping
("dropped on", sometimes a part body mashed into a neighbour's label stack).
`group_parts()` already yields the units = connected components over **wired
(internal) nets** (label-only nets don't merge). Added an opt-in `grid_blocks`
path: shelf-pack the units by their bbox into an orderly grid.

- **Where it wins:** sheets of genuinely-independent small units (e.g. a pots +
  mux sheet — pots land in clean rows, the muxes separated, the
  previously-mashed pot moved from 11.6 mm → 34.8 mm clear). Best combined with
  `label_clearance` (Pass-2 packs using label-inclusive bboxes).
- **Where it's a no-op:** IC-fan sheets (a 74HC165 + 8 pulldowns + switches).
  There the units are large/wide and the layout is governed by snap/stagger, not
  block packing — content stays ~1.7 m tall regardless. **grid_blocks should be
  scoped to independent-unit sheets, not applied to IC-fan sheets.**

## A — dangling wire-remnant purge (the real electrical win)

Documented "wire remnants after snap": snap moves a 2-pin part by reassigning
`part.tx`, but the router's wire to the part's **old** position survives as a
short stub whose far end touches nothing → KiCad `wire_dangling`. Added a
post-emit purge in `node_to_sexp_schematic`: drop any wire segment with an
endpoint anchored to nothing (not a pin / label / junction / no_connect / other
wire endpoint), iterated. MR1 switch sheet: dangling errors ~4 → 1 (remainder a
GND power symbol). **Connectivity-safe** — the ERC-guard tests pass.

## E — why the redundant `SW_*` labels can't just be filtered out

On the switch sheet, every `SW_*` label sits exactly on a wire endpoint, so they
*look* redundant. They are not: they are the net's **connection anchor**. The
snap-staggered pulldown fan connects to the 74HC165 input pins **by net name via
the label**, not pin-to-pin. Suppressing the labels (even routed through the
existing `wired_pin_ids` skip, with the remnant purge active) produces **32
`wire_dangling` + 5 `pin_not_connected`** on the MR1 switch sheet.

Important testing note: the **full suite still passed 292/1/1** with the
suppression — its boards lack this IC-fan-by-name pattern, so the suite does
**not** guard against this real-board breakage. It was only caught by running
`kicad-cli sch erc` on the actual board. **Add an IC-fan ERC fixture.**

## The root that all roads lead to — stub-vs-route (the real #E/D fix)

The labels-are-anchors problem and the grid-can't-help-IC-fans problem are the
same root: on dense sheets, `auto_stub` **stubs the IC input pins** (labels)
because the fanned passives land far, so the cluster is connected *by net name*
rather than by wires. The principled fix is **pin-to-pin**: when snap/stagger
places the fan, **draw a wire from the IC pin to the fan junction** so the
connection is physical. Then the name-label becomes genuinely redundant and can
be dropped (E), the cluster is one wired group (so grid/placement treat it
coherently), and dangling/by-name fragility goes away.

This is the next piece of work (in progress). It lives at the
snap/stagger ↔ route ↔ emit seam (`schematics/snap.py` + `node.wires` +
`node_to_sexp_schematic`), and must be ERC-verified on an IC-fan board, not just
the suite.

## Open questions for Codex

1. Is drawing an explicit IC-pin→fan-junction wire in `_stagger_tjunctions` (so
   the stubbed IC pin becomes wire-connected, label removable) sound, or does it
   collide with the autorouter / NetTerminal model?
2. `grid_blocks` scoping: best heuristic for "independent-unit sheet" (apply
   grid) vs "IC-fan sheet" (leave to snap/stagger)?
3. Worth a small IC-fan ERC fixture in the suite so the redundant-label /
   pin-to-pin work is guarded against real-board dangling?
