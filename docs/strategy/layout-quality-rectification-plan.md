# Layout Quality Rectification Plan

This plan tracks the recurring layout failures exposed by the ten-board direct MCP loop. The goal is not to hand-tune those ten boards; it is to turn every repeated human-review complaint into a measurable engine contract, regression fixture, or agent-facing policy.

## Tracking Model

Create a stable `layout_quality_pack` with the current ten boards as named fixtures. The first local/offline slice is the five-board pack in `corpus.run_corpus`:

```bash
.venv/bin/python -m corpus.run_corpus \
  --product-pack five-board \
  --mode engine_only \
  --no-mcp \
  --force \
  --concurrency 1 \
  --timeout-s 60 \
  --artifacts artifacts/product_layout_quality/five-board \
  --telemetry artifacts/product_layout_quality/five-board/runs.jsonl \
  --spend-log artifacts/product_layout_quality/five-board/spend.jsonl \
  --pid-file artifacts/product_layout_quality/five-board/run_corpus.pid
```

This writes each board under its run directory with `response.json`, resolved preview paths, `board.kicad_pcb` when the engine emits one, and `layout_quality.json`. The aggregate rinse-and-repeat gate is `artifacts/product_layout_quality/five-board/product_pack_report.json`, which summarizes gate failures and issue-class tags across the pack.

The current five-board slice is:

- `mcp9808_breakout`
- `eurorack_attenuverter`
- `esp32_s3_logger`
- `headphone_line_amp`
- `solar_lipo_node`

The target ten-board set remains:

- `mcp9808_breakout`
- `eurorack_attenuverter`
- `esp32_s3_logger`
- `headphone_line_amp`
- `solar_lipo_node`
- `daisy_synth_utility`
- `rp2040_keypad`
- `usb_c_midi_trs`
- `analog_vco`
- `45lux_sensor_array`

Each fixture should produce:

- `response.json`
- `preview_top.svg/png`
- `preview_bottom.svg/png` when back-side placement exists
- `board.kicad_pcb`
- `layout_quality.json`
- a one-page human review note with accepted/open defects

Track quality by issue class, not only board pass/fail. A board can be manufacturable and still fail visual/product acceptance.

## Acceptance Gates

Every board run should report these gates explicitly:

- `schematic_ok`
- `placement_ok`
- `drc_ok`
- `manufacturable`
- `visual_review_ready`
- `product_layout_ok`

`manufacturable` means KiCad/DRC/export is clean. `product_layout_ok` means the board also satisfies human layout expectations for mechanical intent, grouping, side placement, and visual composition.

## Systemic Defects

### 1. Rich Mechanical Floorplans Are Not Preserved

Evidence:

- 45lux preserved the 4x4 sensor positions but lost the rounded rectangular cutout lattice.
- The submitted floorplan only carried `fixed_positions` and `keepouts`; it had no `cutouts`, internal `Edge.Cuts`, apertures, slots, or non-place/non-route geometry.

Fix:

- Add floorplan schema support for `cutouts` / `apertures` / `slots`.
- Represent cutouts separately from keepouts: cutouts are physical board voids and must be written to `Edge.Cuts`; keepouts are placement/routing restrictions.
- Extend previews to render internal cutouts.
- Extend validation so parts, pads, traces, and mounting hardware cannot occupy cutout geometry.
- Extend routing export/import so freerouting sees the real board shape.

Acceptance:

- 45lux preview visibly matches the reference lattice.
- Sensor grid, internal cutouts, mounting holes, and lower electronics all survive a hosted MCP run.
- No route or footprint crosses an aperture.

### 2. Board Size Policy Is Too Weak

Evidence:

- ESP32 logger, Daisy board, USB-C MIDI/TRS, and several eurorack tests leave large empty areas.
- When the outline is unspecified, the engine often grows generously but does not shrink back.
- When the outline is fixed, the engine sometimes clusters parts instead of using the available mechanical area.

Fix:

- Add board utilization metrics: occupied area, convex hull ratio, edge-device utilization, and empty-margin score.
- Distinguish three outline modes: `fixed_mechanical`, `suggested`, and `smallest_practical`.
- For `smallest_practical`, run compacting passes after placement and routing.
- For `fixed_mechanical`, spread user-facing controls, jacks, LEDs, sensors, and switches across the usable area rather than clustering them.

Acceptance:

- Small breakouts shrink unless mounting/user constraints justify the space.
- Fixed-outline boards use their meaningful area for panel/user-facing components.
- The exception mapper suggests shrink/redistribute before grow when the board is already sparse.

### 3. Mechanical Connector Orientation Still Needs First-Class Semantics

Evidence:

- Earlier runs put headers off-board or not centered.
- Horizontal audio jacks needed multiple fixes before facing outward.
- Terminal blocks, USB, TRS, JST, and pin headers each need different edge-facing semantics.

Fix:

- Build a footprint-family mechanical metadata registry:
  - mating direction
  - edge-facing side
  - cable/plug clearance
  - whether edge placement is required, preferred, or forbidden
  - normal rotation per board edge
- Infer metadata from known KiCad footprints when possible, but allow explicit agent overrides.
- Add connector-specific visual acceptance checks.

Acceptance:

- USB and terminal blocks face outward.
- Board-edge pin headers run along and are centered on the relevant edge.
- Thonkiconn/PJ398-style eurorack jacks are not forced to board edges unless the selected footprint actually requires it.

### 4. Front/Back Assembly Semantics Are Incomplete

Evidence:

- Eurorack and synth boards need front controls/jacks and rear electronics.
- Opposite-side body overlap can be legal, but through-hole pads and drilled holes still collide.
- Current previews can make back-side parts hard to reason about.

Fix:

- Promote `assembly_side` to a core layout contract.
- Validate side-aware bodies, courtyards, pads, holes, and keepouts separately.
- Add front-panel no-route or low-route zones where a human should not see long copper runs across controls.
- Render front/back previews with pale back-side outlines and clear top/bottom layer visibility.

Acceptance:

- Eurorack panels place controls/jacks on front, service electronics on back.
- Through-hole collisions are caught even when bodies are on opposite sides.
- Analog VCO no longer routes long visible traces through the control face.

### 5. Passive Placement Is Not Yet Circuit-Aware Enough

Evidence:

- Passives often float in visually arbitrary positions.
- Decaps needed repeated parent-affinity fixes.
- Board 5 and board 1 show passives electrically near but not visually composed.

Fix:

- Use pin/pad gravity after hard mechanical placement.
- Assign passive parentage by net graph, role, ref/value affinity, and physical pin distance.
- Treat each IC/regulator/module plus its local passives as a movable functional group unless mechanical constraints say otherwise.
- Route-aware refinement should nudge ICs apart when their local passives need escape room.

Acceptance:

- Decaps sit beside supply pins, not just near package origins.
- Regulator input/output caps sit on the relevant side of the regulator.
- Simple boards read as an IC plus local passive halo, not a random passive cloud.

### 6. Grid And Repetition Policy Needs To Be Explicit

Evidence:

- Keypad was good because grid intent was obvious.
- Earlier LED/switch/jack layouts were not consistently aligned.
- Eurorack controls and jacks need panel patterns, not only net-driven placement.

Fix:

- Detect repeated user-facing components and impose grid/row/column constraints.
- Separate `front_panel_grid` from generic component gridding.
- Mine existing PCB examples for common eurorack and utility-board control patterns.
- Let agent/user-supplied floorplans override inferred grids.

Acceptance:

- Switches, LEDs, keys, sensors, pots, and jacks align by default when repeated.
- Groups are arranged as groups first, then routed/refined locally.
- Human review sees intentional rows/columns even when exact spacing is not ideal.

### 7. Routing Feedback Does Not Yet Drive Placement Enough

Evidence:

- Many best boards still carry `HIGH_CONGESTION`.
- Some boards are manufacturable but have long, ugly traces.
- The engine has often treated routing failure as an outline problem instead of a placement/topology problem.

Fix:

- Feed routing congestion, unrouted nets, DRC shorts, and long power nets back into placement candidates.
- Add penalties for crossing panel/control regions.
- Prefer local reroutes and group movement before outline scaling.
- Track HPWL, congestion, unrouted count, layer usage, and front-panel crossing count per iteration.

Acceptance:

- A failed route produces targeted placement advice.
- Long visible control-face traces are scored as layout defects.
- Outline growth is suggested only when compactness and placement alternatives are exhausted.

### 8. Enrichment Can Pollute Floorplans

Evidence:

- 45lux enrichment introduced support parts that were not floorplanned.
- Earlier USB-C enrichment duplicated an existing connector.

Fix:

- Make enrichment output explicit and reviewable.
- Require every added component to receive placement intent.
- Add `allow_enrichment`, `review_enrichment`, and `no_enrichment` modes.
- Never add a connector/power block if an equivalent explicit design block exists.

Acceptance:

- Added parts appear in response telemetry with reason and suggested placement.
- Floorplanned boards do not get surprise support parts in mechanically sensitive areas.
- Agents can accept, reject, or floorplan enrichment additions deliberately.

### 9. Visual Review Artifacts Need To Become Product Outputs

Evidence:

- Human review found issues that numeric scoring missed.
- Dark KiCad renders are less useful for the hosted/beta UX than branded light 2D previews.
- Back-side parts, silk, holes, and routes need clearer styling.

Fix:

- Generate canonical light 2D previews for every run.
- Include top, bottom, and combined assembly views.
- Use black silk on light previews and keep back-side parts pale.
- Add visual issue overlays for keepouts, cutouts, unrouted nets, and DRC hotspots.

Acceptance:

- User can give useful feedback without opening KiCad.
- MCP response includes preview URLs/artifacts and a prompt for human feedback.
- The preview clearly distinguishes top copper, bottom copper, front parts, back parts, cutouts, and holes.

### 10. Test Coverage Needs Product Fixtures, Not Only Unit Tests

Evidence:

- Unit fixes helped, but board-level regressions kept reappearing in new forms.
- Several bugs only surfaced after full schematic -> placement -> writer -> DRC/routing cycles.

Fix:

- Add a product regression suite around the ten-board pack.
- Store expected metric thresholds and visual metadata, not exact pixel snapshots for every board.
- Use visual snapshots for core mechanical cases: 45lux lattice, keypad grid, eurorack front/back, edge connector orientation.

Acceptance:

- CI can catch lost cutouts, off-edge headers, wrong connector orientation, missing side semantics, and passive drift.
- Each systemic issue has at least one regression fixture before being marked closed.

## Implementation Phases

### Phase 1: Tracking And Fixture Pack

- Add `layout_quality_pack` runner for the first five boards, then expand it to all ten.
- Emit `response.json`, previews, `board.kicad_pcb` when available, and `layout_quality.json` for each board.
- Add aggregate board-level statuses, quality gates, and issue-class tags in `product_pack_report.json`.
- Preserve the current best previews as baseline review artifacts.

### Phase 2: Product Layout Scoring

- Add compactness, fixed-outline utilization, front-panel crossing, grid quality, and passive-parent distance metrics.
- Update exception mapping so sparse boards suggest shrink/redistribute, not grow.
- Make `product_layout_ok` fail even when `manufacturable` passes if these metrics are poor.

### Phase 3: Mechanical And Side Semantics

- Add footprint-family metadata for USB, TRS, Thonk/PJ398, Phoenix terminal, JST, board-edge headers, dev modules, mounting holes, pots, switches, and LEDs.
- Expand front/back side validation.
- Render side-aware previews.

### Phase 4: Circuit-Aware Grouping And Routing Feedback

- Strengthen parent/passive grouping.
- Add route-driven placement retries.
- Add no-route/avoid-route support for panel faces and cutouts.
- Re-run boards 1, 3, 5, 8, and 9 as the primary proof set.

### Phase 5: Enrichment Governance

- Make enrichment modes explicit.
- Require placement intents for generated parts.
- Add duplicate/proxy detection for common USB, power, and connector blocks.

### Phase 6: Rich Floorplan Geometry

- Add `cutouts` / `apertures` schema.
- Render cutouts in previews.
- Write internal cutouts to KiCad `Edge.Cuts`.
- Make placement and routing respect cutout geometry.
- Rebuild 45lux from the original floorplan and verify the lattice survives.

## Priority Order

1. Fixture pack plus `layout_quality.json` so progress is measurable.
2. Product layout scoring for compactness, outline utilization, and front-panel route avoidance.
3. Mechanical connector metadata.
4. Passive parent/group placement.
5. Side-aware preview and front/back assembly polish.
6. Route-feedback-to-placement loops.
7. Enrichment governance.
8. 45lux cutouts/apertures and rich mechanical floorplan preservation.

## Done Definition

This work is not done when the router says yes. It is done when:

- boards 4, 5, and 8 remain good examples,
- board 10 visibly preserves the 45lux lattice,
- board 9 stops routing through the panel face,
- board 3 shrinks or uses its outline deliberately,
- board 1 reads as a clean breakout,
- every fixture explains its remaining defects in `layout_quality.json`.
