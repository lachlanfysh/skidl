# Snap Backend Split — Implementation Status

> Companion to `ARCHITECTURE-snap-backend-split.md` (the design). This records what is
> **built and verified** vs. what remains, for a second-opinion review (Codex) and to
> anchor the conversation with the upstream maintainer (devbisme).
> Repo: `lachlanfysh/skidl` (fork). Not yet PR'd upstream.

## TL;DR

The design doc's dependency-inversion architecture is **implemented and verified**:
decision logic now lives in a tool-agnostic layer (`src/skidl/schematics/`) behind a
`SchematicBackend` interface; KiCad emission + render-space geometry stay in
`tools/kicad9/`. It is a **pure restructure on top of a working snap feature** — output is
preserved (suite green, esp32 parity, MR1 0-missing). Two doc items are deliberately
deferred because they *change* output (need golden-file sign-off).

## Branches (on `lachlanfysh/skidl`)

| Branch | Contents |
|---|---|
| `docs/snap-backend-split` | the design doc (5 Codex review rounds) |
| `fix/dev-base-bugs` | working snap feature ported onto maintainer's `development` + bug fixes |
| `refactor/snap-backend-interface` | **this** — the architecture refactor on top of `fix/dev-base-bugs` |

## What was built (in order)

1. **Power-symbol "unknown component" fix** — maintainer's `development` emitted power-symbol
   *instances* on auto-stubbed child sheets without matching `lib_symbols` *definitions*
   (defs were scoped from `node.wires`, instances from pins). Fixed by deriving per-sheet
   defs from emitted instances (`_power_lib_ids_in_elements`).
2. **Snap feature port** — geometry → `schematics/snap.py`; emitters initially in backend.
3. **Deferred fanout-stubbing** — restored (high-fanout nets marked `_deferred_stub`, stubbed
   after placement) so dense boards still group/route like the reference; immediate stubbing
   had scattered parts and broken routing.
4. **Anchor-preserving label deconfliction** — spread labels off bodies with a connecting
   wire (anchor = connection point); collision-safe + grid-snapped + on-grid-anchor-gated to
   avoid net shorts / off-grid warnings at dense scale.
5. **Power-bus crossing fix** — `_gen_power_bus_wires` drew bus segments through component
   bodies; split runs at body-crossing segments → those pins use power symbols.
6. **Architecture refactor (this branch)** — relocated decisions to `schematics/` behind the
   `SchematicBackend` interface (below).

## The architecture, as implemented

```
src/skidl/schematics/          (TOOL-AGNOSTIC — no import of tools/kicad9, verified)
  backend.py   (185 ln)  SchematicBackend Protocol, LabelPlacement, RenderContext
  decisions.py (432 ln)  find_overlapping_pins, find_power_bus_runs,
                         deconflict_labels, find_no_connect_pins
                         + pure geometry (_seg_crosses_box, _part_render_bbox)
  snap.py      (538 ln)  snap placement geometry (snap_two_pin_parts, stagger, pre-shift)
        │ depends only on the SchematicBackend interface
        ▼
src/skidl/tools/kicad9/        (TOOL-SPECIFIC)
  sexp_schematic.py      Kicad9Backend (adapter) + emission primitives
                         + render-space geometry (_kicad_pin_pos, render_xy,
                           pin_orientation_to_angle) + lib_symbol emission
```

`sexp_schematic.py` delegates to the agnostic `decisions` module at 8 call sites.

### `sexp_schematic.py` delta vs maintainer's `development`: **+206 / −16**
(down from +633/−16 before the refactor — ~430 lines of decisions moved out). Every
remaining addition is justified as backend-only per the doc:

| Added to backend | Justification |
|---|---|
| `Kicad9Backend` | concrete interface impl — definitionally tool-layer |
| `_kicad_pin_pos`, `render_xy`, `pin_orientation_to_angle` | KiCad render-space geometry (the doc keeps the coordinate convention in the backend, exposed as the geometry oracle) |
| `_power_lib_ids_in_elements` | KiCad `lib_symbols` emission (missing-symbol fix) |
| `apply_label_deconfliction` | emission half (Sexp mutation); decision half is in `decisions.py` |
| `need_quote` / `need_quote_alternate` | KiCad S-expr serialization |

No decision logic and no duplicated geometry remain in the backend (verified).

## Verification

| Gate | Result |
|---|---|
| Full `ai_tests` suite | 292 passed, 1 skipped, 1 failed (pre-existing `test_generate_svg`, missing `netlistsvg` binary) — no regressions |
| esp32 parity (pre- vs post-refactor) | 6 sheets / 43 wires / 37 labels / 0 missing — exact on structural counts |
| MR1 (Son-of-ER-1, ~225 parts) | 0 missing symbols, generates clean |
| Agnostic-layer import check | `schematics/{decisions,backend}.py` import nothing from `tools/kicad9` |
| Deconfliction byte-identity | 200/200 randomized differential test (old vs relocated) identical |

**Note on parity method:** generation is **non-deterministic run-to-run** (placement uses
randomness; root sheet UUID changes), so byte-for-byte file diffs are *not* a valid parity
metric. Parity was verified via stable structural invariants (counts, missing-defs) + a
differential unit test on the riskiest (Sexp-mutating) path.

## Deliberately NOT done (output-changing → out of scope for a pure restructure)

1. **`render_node` root/child unification (doc §5 P2a)** — would add bus/T-junction/power-cap
   handling to the currently-bare root sheet. A real improvement but a *visible output change*;
   needs golden fixtures + maintainer sign-off.
2. **`solve_snap_tx` in true render-space (doc P1b)** — currently delegates to the existing
   placement-space `_compute_snap_tx`. Moving to render-space fixes a latent transform bug but
   changes output. Deferred for the same reason.

## Known-architectural limitations (not bugs, surfaced during MR1 review)

- **Off-pin net labels with a connecting wire** — inherent to the routed-`NetTerminal`
  design: terminals must sit at the routing-channel edge so the autorouter can reach them.
  Anchoring them on-pin **breaks routing** (proven: 14 routing failures). True on-pin labels
  require *stubbing* the net (direct label) instead of routing it — a stub-vs-route
  net-selection strategy, i.e. a design decision, not a placement patch.
- **Snap-crammed passives** (e.g. op-amp feedback, OLED Pin-1 decoupling) — pre-existing in
  the maintainer's reference; electrically correct (ERC-clean, no shorts), visually tight.
  Needs a snap clearance margin (affects all snapped designs → broad re-verification).

## Questions for review

1. Is the interface surface right? (`pin_render_pos`, `pin_render_dir`, `is_power_net_name`,
   `solve_snap_tx`, `label_bbox` + emit primitives.) Anything missing or over-broad?
2. `render_node` unification — worth doing now behind golden fixtures, or leave to the
   maintainer's net-label-strategy work?
3. The stub-vs-route net selection (which nets get direct on-pin labels vs routed terminals)
   is the lever behind both the off-pin labels and the crossing wires. Is reworking that
   strategy in scope, or maintainer territory?
