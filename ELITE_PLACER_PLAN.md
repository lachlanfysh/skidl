# Elite PCB Placer Implementation Plan

This is the live handoff plan for continuing the layout placer work on
`codex/layout-outline-tasks`. It is intentionally committed separately from
implementation stages so another agent can resume at a clean boundary.

## Current Baseline

Already implemented:

- Footprint geometry model with pads, bounds, transformed world coordinates,
  pad nets, and pad-side summaries.
- Intent inference for connectors, power parts, decaps, mux/channel patterns,
  RF-like modules, crystals, debug/UI parts, and repeated channels.
- Multi-candidate placement runner with scorecard/report output.
- Keepouts, polygon outline validation, soft constraints, optional Shapely
  containment acceleration, and optional-backend detection.
- Pad/net-pressure orientation refinement for unlocked parts.
- Actual-pin-aware decap inference and refinement when footprint pad geometry is
  available.
- Coarse power topology chains for source/protection/conversion/storage/load
  ordering, candidate biasing, and report summaries.
- Channel slot metadata for repeated structures, including shared controller
  refs and per-channel placement zones.
- Deterministic congestion heatmap scoring/reporting for net spans, pin escape
  density, keepouts, and power corridors.
- Deterministic score-gated local refinement for candidate moves, geometry
  rotations, and compatible swaps while preserving fixed/edge refs and decaps.
- Structured `PlacementReport` helpers for `part(ref)`, `net(name)`, and
  `top_risks()`.
- Power route intents and reserved power corridor summaries.
- Per-part candidate placement reasons in `PlacementReport`.

Keep `AGENTS.md` untouched unless the user explicitly asks. It is currently an
untracked local instruction file.

## Stage Discipline

Each stage should be committed and pushed independently:

1. Update this plan if scope changes.
2. Implement one phase or a coherent slice of one phase.
3. Add/adjust focused tests.
4. Run:

   ```bash
   /private/tmp/skidl-layout-venv/bin/python -m pytest tests/unit_tests/test_layout_*.py -q
   ```

5. Commit with a phase-specific message.
6. Push `codex/layout-outline-tasks`.

## Phase 1: Mechanical Truth / Mating Intent

Goal: stop treating connector, UI, and mechanical parts like generic
rectangles.

Implementation tasks:

- Add a `MatingIntent` dataclass with:
  - `ref`
  - `kind`: `usb`, `barrel`, `jst`, `header`, `ffc`, `button`, `led`,
    `display`, `pot`, `encoder`, `generic_connector`
  - `edge_preference`
  - `mating_side`
  - `allowed_rotations`
  - `confidence`
  - `reasons`
- Add `mating_intents` to `PlacementIntentPlan`.
- Infer mating intent from ref/name/value/footprint text and existing roles.
- Convert mating intent into edge anchors and/or face-edge constraints where
  confidence is high.
- Ensure generic pad-pressure rotation never overrides mechanical mating intent.
- Report mating decisions in per-part placement reasons.

Tests:

- USB is inferred as a bottom-edge mechanical connector with a constrained
  mating side.
- Header/JST/barrel-like parts get plausible connector intent.
- Button/LED/display-like parts get board UI mating intent.
- Orientation refinement skips mechanically constrained refs.

## Phase 2: Decaps By Actual Pins

Goal: place decaps near the relevant IC/regulator power pins instead of near
the parent package center.

Status: implemented. `src/skidl/layout/decaps.py` infers cap-to-parent pad
targets from SKiDL pin nets plus footprint geometry, refines candidate
placements near the actual power/GND pad side, rotates caps toward parent pads,
and records per-cap report reasons. Bbox-only boards still use the existing
placement fallback.

Implementation tasks:

- Build a helper mapping SKiDL pin numbers and footprint pad numbers to nets and
  local/world coordinates.
- Add `DecapPlacementIntent` with cap ref, parent ref, supply net, ground net,
  target power pin coordinates, and target ground pin coordinates.
- Infer decap parent from shared supply/ground nets and nearest/strongest role.
- Add a placement/refinement pass that:
  - places the cap near the actual parent power/GND pads,
  - chooses the side of the package where those pins live,
  - rotates the cap so matching pads face the parent pins when possible.
- Extend reporting with cap-to-parent-pin distance.

Tests:

- A cap near an IC with asymmetric VDD/GND pads moves near the actual VDD/GND
  side, not the package center.
- Multiple decaps distribute across multiple power pins.
- Regulator input/output caps go to the input/output side when pad mapping is
  available.

## Phase 3: Power Topology Graph

Goal: understand mandatory high-current chains before placement/routing.

Status: implemented. `PowerTopology` / `PowerChain` infer coarse directed
chains from connector-like sources, protection parts, regulators, storage caps,
and downstream loads. `power_topology_first` adds chain-aware placement
constraints, `PowerRoutePlan` carries the topology, and placement reports
summarize inferred chain order.

Implementation tasks:

- Add `PowerTopology` / `PowerChain` data structures:
  - sources: USB, battery, barrel, terminal
  - protection: fuse, TVS, reverse diode
  - conversion: charger, buck, boost, LDO
  - storage: bulk caps
  - loads: ICs/modules/connectors
- Infer directed chains from shared nets and roles.
- Add a `power_topology_first` candidate.
- Place source/protection/regulator/bulk caps in physical order.
- Reserve corridors based on the chain, not only HPWL.
- Penalize broken or blocked high-current corridors more on 2-layer boards.

Tests:

- USB/LDO/caps form an ordered chain.
- 2-layer boards score broken/wide corridors worse than 4-layer boards.
- Power report identifies source, conversion, bulk storage, and load refs.

## Phase 4: Channel Slot Model

Goal: make repeated structures look intentional and routable.

Status: implemented. Repeated-channel intent now distinguishes shared/controller
refs from per-channel slots, categorizes slot refs by role, and the
`repeated_channel_array` candidate adds per-slot zones plus controller bank
zones while preserving the older ordered-distribution behavior.

Implementation tasks:

- Expand repeated-channel intent to distinguish:
  - per-channel refs,
  - shared controller refs,
  - per-channel passives/connectors/sensors,
  - shared bus/backbone nets.
- Add `ChannelSlot` with slot index, bbox, ordered refs, and local constraints.
- Put mux/controller at bank edge or centerline.
- Keep per-channel passives inside their slot.
- Preserve channel order and symmetry.

Tests:

- Channel refs stay ordered.
- Passives follow their channel, not the global nearest IC.
- Mux faces the channel bank/backbone.

## Phase 5: Congestion Heatmap

Goal: score whether a placement is likely to route before autorouting exists.

Status: implemented. `build_congestion_map()` creates a deterministic grid
from net spans, pin escape density, power corridors, and keepouts. Scoring uses
peak/average congestion with 4-layer relief, and reports include top congested
regions with contributing reasons.

Implementation tasks:

- Add a deterministic board grid model.
- Add demand from net bounding boxes, pin density, power corridors, keepouts, and
  connector escapes.
- Score peak congestion, average congestion, blocked power corridors, and
  connector escape crowding.
- Report top congested regions and contributing nets.

Tests:

- Dense crossed placement scores worse than separated placement.
- Keepouts and power corridors increase congestion pressure.
- 4-layer boards reduce congestion penalty.

## Phase 6: Local Refinement

Goal: improve candidates after first placement without adding required heavy
dependencies.

Status: implemented. `refine_placement()` runs a bounded deterministic local
search over unlocked candidate refs, tries small connected-net moves, geometry
rotations, and compatible same-footprint swaps, and accepts only normal
scorecard improvements that do not add hard violations. Fixed refs, edge
anchors, face-edge rotations, zones, outlines, keepouts, and dedicated decap
placements are preserved.

Implementation tasks:

- Add a deterministic refinement loop for small moves, rotations, and compatible
  swaps.
- Preserve fixed, edge, mechanical, keepout, outline, and zone constraints.
- Accept only score improvements.
- Leave hooks for SciPy/OR-Tools later.

Tests:

- Refinement never worsens score.
- Locked and mechanically constrained parts remain fixed.
- Output is deterministic across runs.

## Phase 7: Golden Board Corpus

Goal: stop optimizing only toy examples.

Add real-ish fixtures:

- USB-powered MCU board.
- Sensor array with muxes.
- Guitar pedal style IO/pots/jacks/analog path.
- Devboard with headers/buttons/LEDs/regulator.
- RF module board with antenna keepout.

For each:

- Generate PCB.
- Validate placement.
- Run KiCad DRC when available.
- Assert score/report invariants.
- Optionally save a screenshot artifact for review.

## Phase 8: Explainability Per Net And Part

Goal: make the placer useful as a design reviewer.

Status: partially implemented. `PlacementReport.part(ref)` explains per-part
placement reasons, matching warnings, and hard violations.
`PlacementReport.net(name)` explains HPWL, power corridors, congestion regions,
and risk notes for a named net. `PlacementReport.top_risks()` produces a
prioritized list from hard violations, warnings, net risks, and congestion
hotspots. Deeper next-action wording and richer per-net attribution remain.

Implementation tasks:

- Add `report.part(ref)`, `report.net(name)`, and `report.top_risks()`.
- Explain connector edge/orientation quality.
- Explain decap distance to actual parent pin.
- Explain power corridor feasibility.
- Explain congestion hotspots and the nets causing them.

Definition of done:

- A user can ask "why is this here?" and get a concrete answer.
- A risky board explains the next physical fix, not just a score.
