# Schematic Generation: Backend Split — Design Proposal

> A proposal for moving the snap feature's decision logic out of the KiCad-9
> backend (`tools/kicad9/sexp_schematic.py`) into the tool-agnostic layer
> (`src/skidl/schematics/`), in line with the thin / cross-backend direction of
> the `development` refactor. On `lachlanfysh/skidl` (a fork). Self-contained.

---

## 0. TL;DR

SKiDL generates KiCad schematics. A "snap" feature (place 2-pin parts onto IC pins,
draw connecting wires, suppress redundant labels) was added in PR #302. The upstream
maintainer refactored the KiCad backend toward a thin, tooling-agnostic design and wants
the snap feature's logic moved **out** of the KiCad-specific backend file
(`tools/kicad9/sexp_schematic.py`) into the tool-agnostic layer (`src/skidl/schematics/`).

The proposal: split the feature into **decisions** (tool-agnostic, move out) and
**primitives** (tool-specific, stay in backend), connected by a small **backend interface**
(dependency inversion). The agnostic layer never imports KiCad; the KiCad coordinate
convention never leaves the KiCad backend.

The one architectural subtlety this document exists to pin down: the geometric decisions
(are two pins co-linear? do they overlap after a snap?) **must be computed in KiCad render
space**, because KiCad's Y-flip / mirror / rotate convention changes which pins actually
line up vs. how they look in SKiDL's abstract coordinate space. That render transform is
irreducibly tool-specific. So it cannot be *relocated* to the agnostic layer — it must be
*exposed* through an interface the agnostic layer calls.

---

## 1. Context

The snap feature (PR #302) currently carries its decision and emission logic in
the KiCad-9 backend. This proposal relocates the tool-agnostic decisions into
`src/skidl/schematics/` behind a small backend interface, so the KiCad
coordinate convention and S-expression syntax stay in the backend while the
decision logic becomes reusable across tools.

## 2. The three layers (target architecture)

```
┌─────────────────────────────────────────────────────────────────────┐
│  src/skidl/schematics/   (TOOL-AGNOSTIC — shared by kicad6/7/8/9)     │
│                                                                       │
│   • orchestration:  place → defer-stub → route → snap → decide → emit │
│   • snap.py:        geometry placement (move 2-pin parts onto pins)   │
│   • decisions.py:   overlap clustering, power-bus runs, label dedup   │
│                     (produce abstract data: pin-id sets, segments)    │
│                                                                       │
│        depends ONLY on the SchematicBackend interface (§3)            │
└───────────────────────────────┬───────────────────────────────────────┘
                                 │  interface (Protocol / ABC)
                                 ▼
┌─────────────────────────────────────────────────────────────────────┐
│  src/skidl/tools/kicad9/  (TOOL-SPECIFIC — one impl per backend)      │
│                                                                       │
│   GEOMETRY primitives:   pin_render_pos, pin_render_dir,             │
│                          is_power_net_name        (KiCad convention)  │
│   EMISSION primitives:   emit_wire, emit_label, emit_no_connect,      │
│                          emit_power_symbol, emit_part, emit_junction  │
│                          (already exist in sexp_schematic.py)         │
└─────────────────────────────────────────────────────────────────────┘
```

**Rule:** the agnostic layer *decides*; the backend *measures* (geometry) and *writes*
(emission). KiCad coordinate math and KiCad S-expression syntax both stay in the backend.

---

## 3. The interface

The complete dependency surface the agnostic decision layer needs from a backend. Small and
nameable — five queries (three measures, one transform-solve, one box-size) plus a capability
flag, on top of the emit primitives that already exist in `sexp_schematic.py`.

```python
# src/skidl/schematics/backend.py  (NEW — interface definition, tool-agnostic)

from typing import Protocol, Optional
from skidl.geometry import Tx

PinDir = str   # one of "U" | "D" | "L" | "R", in RENDER space
Element = object  # opaque backend element (KiCad: an Sexp). Agnostic layer never inspects it.


class SchematicBackend(Protocol):
    # ---- GEOMETRY (tool-specific; the part that CANNOT be made agnostic) ----

    def pin_render_pos(self, pin, sheet_tx: Tx) -> tuple[float, float]:
        """(x, y) in mm where the tool will actually DRAW this pin,
        accounting for the tool's own flip/mirror/rotate convention.
        KiCad impl == current `_kicad_pin_pos`."""

    def pin_render_dir(self, pin, sheet_tx: Tx) -> PinDir:
        """Which way the pin points AFTER the tool's transform.
        Takes `sheet_tx` because the sheet transform's Y-flip can flip U<->D —
        direction must be computed in the SAME render space (and ideally the
        same cached transform) as `pin_render_pos`.
        KiCad impl == current `calc_pin_dir`, EXTENDED to fold in sheet_tx.
        (The present `calc_pin_dir` ignores sheet_tx; the D<->U orientation bug
        is plausibly a manual compensation for exactly that omission. See §6.)"""

    def is_power_net_name(self, name: str) -> bool:
        """True if `name` matches a tool power-symbol (e.g. KiCad's power lib).
        KiCad impl == `name in _get_power_symbol_names()`."""

    def solve_snap_tx(self, part, my_pin, target_render_xy: tuple[float, float],
                      extend_dir: PinDir, sheet_tx: Tx):
        """Return a new `part.tx` such that `my_pin` will RENDER at
        `target_render_xy`, extending in `extend_dir`. This is the INVERSE of
        `pin_render_pos`: a measure query can't produce it (see §6). The
        agnostic snap layer decides the relationship (which part attaches to
        which pin, in which direction); the backend solves the transform.
        KiCad impl == current `_compute_snap_tx`, made render-space-aware."""

    def label_bbox(self, text: str) -> tuple[float, float]:
        """(width, height) in mm of a rendered net-label box for `text`.
        Tool/font-specific. Needed if label deconfliction moves to the agnostic
        layer (it must know box sizes to detect overlap). See §7 Q4."""

    # capability gate — backends that don't implement geometry disable snap
    # EXPLICITLY (not a silent no-op). See §7 Q7.
    supports_snap: bool

    # ---- EMISSION (tool-specific; already implemented in sexp_schematic.py) ----

    def emit_wire(self, x1: float, y1: float, x2: float, y2: float,
                  *, net_name: Optional[str] = None) -> Element: ...

    def emit_label(self, pin, sheet_tx: Tx, *,
                   at: Optional[tuple[float, float]] = None,
                   angle: Optional[float] = None,
                   force: bool = False) -> Optional[Element]:
        """Emit a net label / power symbol for `pin`.
        `at`/`angle` override computed position/orientation — supplied by the
        agnostic deconflict pass once it has resolved a final placement.
        If `at` is None the backend computes position itself. `force=True` emits
        even for non-stubbed pins (the forced-power-label case, see
        `label_candidate_pins`)."""

    def emit_no_connect(self, x: float, y: float) -> Element: ...

    def emit_power_symbol(self, pin, net_name: str, sheet_tx: Tx) -> Optional[Element]: ...

    def emit_part(self, part, sheet_tx: Tx, uuid_path: str) -> Element: ...

    def emit_junction(self, x: float, y: float) -> Element: ...
```

### The agnostic decision functions (relocated out of `sexp_schematic.py`)

```python
# src/skidl/schematics/decisions.py  (NEW — relocated decision logic, tool-agnostic)

def find_overlapping_pins(node, backend: SchematicBackend, sheet_tx) -> set[int]:
    """Pins that physically coincide after snap (dist < 0.01mm in RENDER space)
    → their labels are redundant; return id(pin) set to suppress.
    Relocated from `_find_wireable_nets` (which emits nothing — pure decision)."""

def find_power_bus_runs(node, backend, sheet_tx, max_gap_mm=10.0
                        ) -> tuple[list[tuple[float,float,float,float]], set[int]]:
    """Co-linear runs of 3+ power pins → (wire segments, pins to skip).
    Relocated DECISION half of `_gen_power_bus_wires`; emission via backend.emit_wire."""

@dataclass
class LabelPlacement:
    anchor_xy: tuple[float, float]   # electrical connection point — FIXED on the pin
    text_xy:   tuple[float, float]   # where the label text renders — may be nudged
    deconflictable: bool             # False for power symbols (anchor == connection)

def deconflict_labels(placements: dict, node, backend, sheet_tx) -> dict:
    """Detect overlap of label boxes against component bodies; for
    `deconflictable` entries only, nudge `text_xy` to clear the overlap while
    leaving `anchor_xy` on the pin. Uses `backend.label_bbox(text)` for box size.
    Caller emits a short connecting wire when `text_xy != anchor_xy` so the
    nudge does not break connectivity. Power symbols are left pinned.
    Relocated DECISION half of `_deconflict_labels`."""
```

### Orchestration (agnostic; replaces the tail of `node_to_sexp_schematic`)

```python
def render_node(node, backend, sheet_tx, uuid_path):
    """SINGLE rendering core. Called by BOTH the root and child paths.
    Today these are two divergent code paths (`write_top_schematic` vs
    `node_to_sexp_schematic`); the root path is missing power-bus / T-junction /
    power-cap handling entirely (see §5). Unifying them here is part of the
    migration, not an afterthought."""
    elements = []

    # parts
    elements += [backend.emit_part(p, sheet_tx, uuid_path) for p in real_parts(node)]

    # ---- wires: ALL segment sources normalized to render-mm first ----
    # router wires (node.wires) are pre-transform Segments in PLACEMENT
    # coords; today wire_to_sexp() applies sheet_tx at emit time. Snap/bus
    # segments are computed in render-mm. To make emit_wire() transform-free we
    # normalize EVERY source into render-mm up front — router wires + junctions
    # included, not just the snap artifacts.
    # not just a transform — it also SPLITS wires at junctions (the current
    # wire_to_sexp(..., junctions=...) behavior) so KiCad connectivity is correct.
    router_segs = split_router_segments_to_render_mm(node.wires, node.junctions, sheet_tx)
    # snap artifacts default to empty when snap is disabled / never ran.
    # These are initialized on SchNode (or read via getattr with a default), so
    # backends with supports_snap=False don't hit AttributeError.
    snap_segs   = getattr(node, "snap_wires", [])           # render-mm
    snap_skip   = getattr(node, "snap_suppressed_pins", set())
    bus_segs, bus_skip = find_power_bus_runs(node, backend, sheet_tx)  # render-mm
    for seg in router_segs + snap_segs + bus_segs:
        elements.append(backend.emit_wire(*seg))
    elements += [backend.emit_junction(x, y)
                 for x, y in junctions_to_render_mm(node.junctions, sheet_tx)]

    # ---- decide label suppression ----
    suppress  = find_overlapping_pins(node, backend, sheet_tx)
    suppress |= snap_skip | bus_skip

    # ---- candidate pins for labels ----
    # NOT just stubbed pins: also the FORCED power case — connected 2-pin
    # power-net pins that are *not* stubbed still get a power symbol/label in the
    # current code. Dropping them here would make power symbols disappear.
    candidates = label_candidate_pins(node, backend)   # stubbed ∪ forced-power
    candidates = [p for p in candidates if id(p) not in suppress]

    # ---- decide label PLACEMENTS (deconflict BEFORE emit; see §7 Q3) ----
    # in KiCad the label/power-symbol `at` IS the electrical connection point.
    # The ANCHOR must stay on the pin; only the TEXT may be nudged, and only for
    # labels that can be wire-backed. Power symbols are NOT anchor-deconflictable
    # (moving them disconnects the net) → pinned: text_xy == anchor_xy.
    placements = {}
    for pin in candidates:
        anchor = backend.pin_render_pos(pin, sheet_tx)
        placements[pin] = LabelPlacement(
            anchor_xy=anchor, text_xy=anchor,
            deconflictable=not backend.is_power_net_name(pin.net.name),
        )
    placements = deconflict_labels(placements, node, backend, sheet_tx)  # nudges text_xy only

    # ---- emit labels (+ a short wire whenever text was moved off the anchor) ----
    for pin, pl in placements.items():
        el = backend.emit_label(pin, sheet_tx, at=pl.text_xy, force=is_forced(pin))
        if el: elements.append(el)
        if pl.text_xy != pl.anchor_xy:                # anchor moved → keep connectivity
            elements.append(backend.emit_wire(*pl.anchor_xy, *pl.text_xy))
    for x, y in nc_pin_positions(node, backend, sheet_tx):
        elements.append(backend.emit_no_connect(x, y))

    return elements
```

Note the precedent: `node.snap_wires` / `node.snap_suppressed_pins` is **exactly the pattern
already in the code today** as `node._tjunction_wires` / `node._tjunction_suppressed_pins` —
computed upstream in the snap phase, attached to the node as abstract data, emitted blindly by
the backend. This proposal generalizes that one working pattern to the remaining emitters,
and (critically) routes **router wires and junctions through the same render-mm
normalization** so there is one coordinate space, not two (§6).

---

## 4. Message / control flow

```mermaid
sequenceDiagram
    autonumber
    participant Caller
    participant Orch as Orchestrator<br/>(schematics/, agnostic)
    participant Dec as Decisions + Snap<br/>(schematics/, agnostic)
    participant BE as Backend<br/>(tools/kicad9/, tool-specific)
    participant Node as SchNode (state)

    Caller->>Orch: generate_schematic(circuit)
    Orch->>Node: place()
    Orch->>Node: apply_deferred_stubs()
    Orch->>Node: route()
    Note over Orch,Node: ordering is intrinsic: snap must run AFTER route

    Orch->>Dec: snap_two_pin_parts(node, backend)
    Dec->>BE: pin_render_pos / pin_render_dir   (measure geometry)
    BE-->>Dec: render-space positions & dirs
    Dec->>BE: solve_snap_tx(part, target, dir)  (solve new part.tx)
    BE-->>Dec: part.tx (renders pin at target)
    Dec->>Node: purge stale node.wires for moved parts; rerun junction cleanup
    Dec->>Node: attach node.snap_wires, node.snap_suppressed_pins (render-mm + id set)

    Orch->>Dec: find_overlapping_pins(node, backend)
    Dec->>BE: pin_render_pos (query)
    BE-->>Dec: positions
    Dec-->>Orch: suppressed pin-id set

    Orch->>Dec: find_power_bus_runs(node, backend)
    Dec->>BE: pin_render_pos + is_power_net_name (query)
    BE-->>Dec: positions, power flags
    Dec-->>Orch: wire segments + skip set

    Orch->>BE: emit_part / emit_wire / emit_label / emit_no_connect
    Note over Orch,BE: agnostic layer hands abstract data; backend writes S-expressions
    BE-->>Orch: opaque Elements
    Orch->>Node: write .kicad_sch
```

**The single boundary crossing that matters:** every time the agnostic layer needs to know
*where something is*, it asks the backend (`pin_render_pos`). It never computes a KiCad
coordinate itself. That is the whole point — and the line we should not cross by "moving the
render math out."

---

## 5. Migration mapping (current → target)

| Current function (in `sexp_schematic.py`) | ~Lines | Nature | Target |
|---|---|---|---|
| `_find_wireable_nets` | ~109 | pure decision (emits nothing) | **→ `schematics/decisions.py`** |
| `_gen_power_bus_wires` | ~113 | decision (~95) + emit (~18) | decision **→ schematics/**; emit via `backend.emit_wire` |
| `_deconflict_labels` (+`_label_dir`) | ~103 | decision + Sexp mutation; **moves `global_label` anchor = latent disconnection bug** | decision **→ schematics/**, restructured to nudge `text_xy` only + wire-back (Q3) |
| `_gen_no_connect_flags` | ~32 | trivial detect + emit | detect → schematics/; emit via `backend.emit_no_connect` |
| `_kicad_pin_pos` | ~29 | KiCad render geometry | **STAYS** → `backend.pin_render_pos` |
| `calc_pin_dir` | ~30 | KiCad render geometry | **STAYS** → `backend.pin_render_dir` |
| `_get_power_symbol_names` + power helpers | ~80 | KiCad power lib | **STAYS** → `backend.is_power_net_name` + emit |
| `wire_to_sexp`, `net_label_to_sexp`, `junction_to_sexp`, `part_to_sexp`, `hierarchical_label_to_sexp` | (pre-existing) | KiCad emission primitives | **STAY** (already in `development`) |
| snap relationships: `_snap_two_pin_parts`, `_stagger_tjunctions`, `_pre_shift_ics` | ~430 | decide which part attaches where, in which direction | **→ `schematics/snap.py`** |
| `_compute_snap_tx` | ~30 | **solves a new `part.tx`** from a target + direction | **STAYS / backend** → `backend.solve_snap_tx` (P1b — inverse of `pin_render_pos`, can't be a measure query) |
| root vs child render paths: `write_top_schematic` + tail of `node_to_sexp_schematic` | (dup) | duplicated emit logic; **root path lacks bus/T-junction/power-cap** | **unify** → single `render_node()` core |
| router wires/junctions: `node.wires`, `node.junctions` via `wire_to_sexp`/`junction_to_sexp` | — | transformed **and junction-split** at emit time | `split_router_segments_to_render_mm` in orchestration — must preserve junction-splitting |

> **P1b (snap coordinate-space tension).** Today snap aligns pins in SKiDL *abstract*
> placement space (`target = pin.pt * part.tx`), while overlap/co-linear decisions run in
> KiCad *render* space (`_kicad_pin_pos`). These can disagree for mirrored/rotated parts —
> snap "aligns" two pins that then don't coincide on screen, so `find_overlapping_pins` misses
> them. Resolution: snap should target render space and solve `part.tx` via
> `backend.solve_snap_tx`, so placement and detection share one space.

> **P2a (root path is the bigger gap).** `write_top_schematic` re-implements part/wire/
> junction/label/NC emission for root-level parts but calls **only** `_find_wireable_nets` —
> it never runs `_gen_power_bus_wires`, `_tjunction_wires`, or `_power_cap_wires`. So a 2-pin
> part snapped onto a *root-level* IC currently gets no connecting wire. Unifying both paths
> on `render_node()` fixes this as a side effect; the migration must do it deliberately.

**Net effect:** of the ~500 disputed lines in `sexp_schematic.py`, roughly **half are
decisions that move out**, and the irreducible tool-specific remainder is **~50–80 lines**
(`pin_render_pos`, `pin_render_dir`, `is_power_net_name`) plus the emission primitives that
already exist on `development`. The backend ends up *thin and tool-agnostic-friendly*, which
is the maintainer's stated goal.

**Alignment with maintainer's roadmap:** `development` already has a commit
*"Propagated corrections … to KiCad 6, 7, and 8."* — he is actively pursuing cross-backend
support. This interface is precisely what lets kicad6/7/8 reuse the decision logic for free,
so the split *serves* his agenda rather than fighting it.

---

## 6. Coordinate-space contract (the crux for review)

There are currently **three** different pin-position computations in `sexp_schematic.py` that
must agree but are derived independently:

| Consumer | Computation |
|---|---|
| `part_to_sexp` (the symbol itself) | KiCad renders from `origin + angle + mirror` |
| wire emitters (`_find_wireable_nets`, `_gen_power_bus_wires`) | `_kicad_pin_pos` analytic reconstruction (`analyze_transform`, `theta=-angle`, Y-flip, mirror) |
| label / NC emitters | `pin_pt * part_tx * tx` (a third path) |

When these disagree for a mirrored/rotated part, you get a mis-oriented label **and** a wire
endpoint that misses the pin. The orientation bug and the wire-remnant class are the same
divergence seen from two angles.

**Proposed contract:** the agnostic layer works in a single coordinate space — *render mm as
returned by `backend.pin_render_pos`*. All decisions, all emitted segments, are expressed in
that space. The backend's `emit_wire(x1,y1,x2,y2)` takes those coordinates directly (no
further transform). This collapses the three computations into **one source of truth**
(`pin_render_pos`) and structurally eliminates the divergence.

**The normalization surface is bigger than the snap artifacts.** Today there are at
least three coordinate conventions in play at emit time:
- `node.wires` / `node.junctions` (router output) — pre-transform `Segment`s in *placement*
  coords, transformed by `wire_to_sexp`/`junction_to_sexp` at emit;
- `_tjunction_wires` / `_power_cap_wires` — pre-transform *mils*, transformed at emit;
- `_find_wireable_nets` / `_gen_power_bus_wires` — post-transform *mm* (`_kicad_pin_pos`).

The proposal standardizes **all** of them on render-mm in the orchestration layer
(`split_router_segments_to_render_mm`, `junctions_to_render_mm`), so `emit_wire`/`emit_junction`
are transform-free. Router wires are the largest surface, not the snap artifacts — easy to
miss because the doc's first draft only called out `_tjunction_wires`.

**Snap placement must join the same space.** Snap currently solves `part.tx` in
abstract placement space, but overlap/co-linearity are judged in render space. The two must
share one space or snap-created overlaps go undetected. Resolution: `backend.solve_snap_tx`
targets render coordinates (the inverse direction of `pin_render_pos`), so snap and detection
agree. This is the same abstract≠render divergence as the orientation bug — fixing the
coordinate contract fixes both.

---

## 7. Resolved design decisions

Listed with the decision and rationale for each.

1. **Interface mechanics → `Protocol` + explicit backend object.** Structural `Protocol` (no
   forced inheritance for existing tool modules) plus a concrete `backend` handle threaded
   through `place/route/snap/emit`. The agnostic functions stop reaching module-level KiCad
   helpers and take `backend` as a parameter.

2. **Coordinate space → normalize to render-mm, for every segment source.** One source of
   truth (`pin_render_pos`). Crucially this includes `node.wires` router segments and
   `node.junctions`, not only the snap artifacts — router wires are the largest surface.
   `emit_wire`/`emit_junction` become transform-free.

3. **Label deconfliction → pre-emission, and anchor-preserving.** `deconflict_labels`
   returns final placements (agnostic); the backend emits once at the resolved position. No
   more mutating built `Sexp` `at` coords. **Critically: it nudges `text_xy` only — never the
   `anchor_xy`, which is the net's connection point.** Power symbols are pinned
   (`deconflictable=False`); any text nudge that leaves the anchor is backed by a short
   connecting wire so connectivity is preserved. (Today's `_deconflict_labels` moves the
   `global_label` anchor directly — a latent disconnection bug this design fixes.)

4. **Geometry surface → add `label_bbox(text)`.** Deconfliction needs rendered label box
   sizes to detect overlap, and that's font/tool-specific. Surface is now: `pin_render_pos`,
   `pin_render_dir`, `is_power_net_name`, `solve_snap_tx`, `label_bbox` + emit primitives.

5. **Stale-wire purge → snap owns it; mutation stays in placement space.** When snap
   moves a part, it purges/reroutes the affected `node.wires` entries, then junction/cleanup
   re-runs so no remnant survives (the wire-remnant fix). **Space discipline:** snap may
   *measure* in render space (via `pin_render_pos`) to decide *what* to purge, but `node.wires`
   are router-owned **placement-space** segments until `split_router_segments_to_render_mm`
   normalizes them at emit. So either mutate `node.wires` in placement space, or don't write
   them back at all — emit separate render-mm snap segments and record which router segments to
   drop (by id/net). Do not mutate `node.wires` with render-mm values.

6. **Performance → cache render geometry in a `RenderContext`, invalidated on snap.**
   Memoize `pin_render_pos`/`pin_render_dir` in a context the orchestration owns. **The key
   cannot be just `(pin, sheet_tx)`** — snap mutates `part.tx` mid-pipeline (and snap itself
   queries geometry to measure), so a position cached pre-snap is stale post-snap. Resolution:
   key on `(id(pin), part.tx_version, sheet_tx)` (bump a per-part version on `part.tx`
   mutation), **or** simply don't populate the cache until snap has finalized all `part.tx`
   — snap's own measurements stay uncached. Latter is simplest; former is safer if other
   phases also move parts.

7. **Backend rollout → explicit capability flag, not silent no-op.** `backend.supports_snap`.
   Ship kicad9 first; other backends that haven't implemented the geometry interface report
   `supports_snap = False` and the snap phase is skipped *explicitly* (logged), so it's never
   ambiguous whether snap ran.

8. **Label candidate set → `label_candidate_pins()`, not `stubbed_pins()`.** The
   candidate set is stubbed pins **∪** the forced-power case (connected 2-pin power-net pins
   that aren't stubbed still get a power symbol today). A literal `stubbed_pins()` loop would
   make those power symbols vanish. `is_forced(pin)` flags which need `force=True` at emit.

9. **`emit_label` signature → carries `at`/`angle`.** Pre-emission deconfliction
   resolves final position/orientation, so the backend must accept overrides rather than
   recomputing. `at=None` falls back to backend-computed placement (the no-deconflict path).

10. **Snap node-artifacts → defaulted.** `node.snap_wires` / `node.snap_suppressed_pins`
    are initialized on `SchNode` (or read via `getattr(node, ..., empty)`), so `render_node`
    is safe when `supports_snap = False` and snap never populated them.

### Resolved with implementation strategy

- **`solve_snap_tx` → feasible; implement as discrete selection + translation, gate on a
  round-trip test.** This is *not* an arbitrary inverse problem. Snap only ever needs one of a
  small discrete set of symbol orientations/mirrors, plus a translation. The backend:
  1. picks the discrete orientation/mirror that makes `other_pin` extend in the requested
     render direction;
  2. computes where `my_pin` renders under that orientation at a fresh origin;
  3. solves the translation delta so `pin_render_pos(my_pin, sheet_tx) == target_render_xy`.

  **Implementation rule:** test it as a round trip — for each mirror/rotation case, call
  `solve_snap_tx`, assign the returned `part.tx`, then assert `pin_render_pos(my_pin, sheet_tx)`
  lands on target within tolerance. If any KiCad transform case is degenerate, the fallback is
  to **skip snap for that part** (it keeps its label), never to approximate and risk a
  disconnect. So this is an implementation spike, not an architectural unknown.

- **`render_node` unification → do it, behind golden fixtures, with the change declared.** The
  current split root/child behavior is accidental divergence, not a useful contract; unifying
  the renderer is the cleaner long-term move and fixes the real root-level bus/T-junction/
  power-cap omission. De-risk by: (a) committing golden fixtures for both a root case and a
  child case so the diff is visible and deliberate; (b) stating in the PR, explicitly,
  *"this intentionally changes root-level output to match child-sheet behavior for
  bus/T-junction/power-cap wires."* A deliberate, declared output change.

---

## 8. Alternatives considered

- **A. Status quo (feature lives in `sexp_schematic.py`).** Works, but the backend is
  fat and kicad9-only; conflicts with the maintainer's thin/cross-backend goal. Rejected.
- **B. Move the render math itself into `schematics/`.** Would make the agnostic layer
  hardcode KiCad's coordinate convention → not actually agnostic. The opposite of the goal.
  **Rejected** — this is the inversion trap §3/§6 exist to prevent.
- **C. Interface / dependency inversion (this proposal).** Decisions agnostic; geometry +
  emission behind a small backend interface. Thin backend, reusable decisions, single
  coordinate source of truth. **Proposed.**

---
