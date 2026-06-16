# Layout Uplift Subagent Plan

Last updated: 2026-06-16

Current branch: `feat/overnight-product-layer`
Current hosted commit: `34bac27b` (`Improve product layout validation`)
Railway endpoint: `https://mcp-server-production-5d58.up.railway.app/mcp`

## Rules For All Agents

- Work in `/Users/lachlanfysh/eda-mcp` unless your spawned workspace says otherwise.
- Do not push to remote. Leave changes ready for the integrator to review.
- Do not use local generation/layout fallback when testing hosted MCP behavior.
- Do not print secrets. Load hosted auth from `.env` / `EDA_AUTH_TOKEN` only.
- You are not alone in the codebase. Do not revert unrelated edits or broad-refactor shared files.
- Add focused tests for every bug class you touch.
- Final report must list changed files, tests run, known residual risks, and any board previews generated.

## Wave 1 Tasks

| Lane | Status | Agent | Primary Scope | Goal | Acceptance Checks |
| --- | --- | --- | --- | --- | --- |
| Connector geometry | integrated locally | Boyle `019ecf87-2011-7ba3-8bdd-2167673376df` | `src/skidl/layout/orientation.py`, `src/skidl/layout/connector_metadata.py`, `src/skidl/layout/intent.py`, focused tests | Edge-anchored jacks, headers, USB, and terminal blocks should put their mating face on the requested board edge and face outward. | Integrated layout sweep passed. Needs hosted visual Railway run after push. |
| Board sizing | integrated locally | Heisenberg `019ecf87-2079-7772-b9e1-f955280ecc5e` | `src/skidl/layout/engine.py`, `src/skidl/layout/placer.py`, focused tests | Auto boards shrink to sensible size; fixed outlines use available space when generous. | Integrated layout sweep passed. Needs hosted visual Railway run after push. |
| Grid/UI placement | integrated locally | Chandrasekhar `019ecf87-20a8-7cf2-a81c-287f623cb806` | `src/skidl/layout/grid.py`, `src/skidl/layout/intent.py`, `src/skidl/layout/roles.py`, `src/skidl/layout/scoring.py`, focused tests | Repeated controls/LEDs/switches/jacks use strong grid/alignment constraints unless explicit floorplan overrides. | Integrated layout sweep passed. Needs hosted visual Railway run after push. |
| Passive gravity | integrated locally | Aristotle `019ecf87-20d1-71e2-8a95-2b328a07774f` | `src/skidl/layout/refinement.py`, focused tests | Passives cluster near connected IC pins/groups after mechanical constraints settle, without overlapping locked parts. | Integrated layout sweep passed after adding repeated-channel near constraints and near-aware passive gravity fallback. Needs hosted visual Railway run after push. |

## Wave 2 Tasks

| Lane | Status | Agent | Primary Scope | Goal | Acceptance Checks |
| --- | --- | --- | --- | --- | --- |
| Routing diagnosis | queued | TBD | routing feedback / exception mapper / layout quality | Classify failed traces as placement-blocked, footprint issue, congestion, router limitation, or outline-too-small. | Failed route output recommends placement fixes before outline growth when board is already oversized. |
| Front/back policy | queued | TBD | side intent, validator, preview metadata | Eurorack and double-sided boards can intentionally place controls/jacks front and power/IC/passives back. | Side-aware overlap tests; back-side THT/SMD overlaps handled correctly; preview metadata marks back-side parts. |
| Custom footprints | queued | TBD | MCP server upload/preflight path | Hosted MCP accepts project footprints/libs so MR-1 and 45lux do not fail footprint preflight. | Tests for submitted footprint payload or library path; hosted preflight reports custom footprints present. |
| Floorplan API | queued | TBD | `submit_skidl_code` envelope and docs | Agents can pass fixed positions, edge anchors, grids, sides, keepouts, outline, and later cutouts explicitly. | 45lux-style floorplan survives code-mode submission; clear docs/examples for agent authors. |

## Standard Test Boards

- MCP9808 or ADS1115 breakout with pin header and optional mounting holes.
- Simple IO / terminal block utility board.
- Daisy / ESP32 dev board carrier.
- Eurorack LFO/VCO/utility module with jacks, pots, power header.
- MR-1 / 45lux floorplan smoke tests once custom footprints are available.

Each test run should capture: `job_id`, `run_id`, terminal status, routed/manufacturable status, preview path, and concise visual defects.

## Compact Resume Checklist

1. Read this file first.
2. Check live plan state in the current chat.
3. Run `git status --short --branch`.
4. Collect completed subagent reports before editing overlapping files.
5. Integrate one lane at a time, run focused tests, then regenerate review boards against Railway.

## Integration Notes

2026-06-16 combined state after all four Wave 1 reports:

- Worktree contains Wave 1 changes plus this tracker doc.
- Combined layout sweep command:
  - `.venv/bin/python -m pytest tests/unit_tests/test_layout_*.py -q`
- Result after integration fix:
  - `323 passed, 4 skipped`
- Product/MCP focused command:
  - `.venv/bin/python -m pytest tests/product/test_mcp_server.py tests/product/test_railway.py tests/unit_tests/test_product_layer.py -q`
- Result:
  - `195 passed, 54 skipped, 1 warning`
- Integration fix:
  - Repeated-channel slot passives now emit explicit near constraints.
  - Passive gravity respects near constraints even when pad geometry is unavailable.
