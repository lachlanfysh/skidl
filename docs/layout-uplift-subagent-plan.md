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
| Routing diagnosis | integrated locally | Peirce `019ecfa8-f0cd-7411-85ba-2cc483c8b902` | routing feedback / exception mapper / layout quality | Classify failed traces as placement-blocked, footprint issue, congestion, router limitation, or outline-too-small. | Integrated product/layout tests passed. Needs hosted visual Railway run after push. |
| Front/back policy | integrated locally | Ptolemy `019ecfa8-f10c-7ef2-81cb-bf318fd5befb` | side intent, validator, preview metadata | Eurorack and double-sided boards can intentionally place controls/jacks front and power/IC/passives back. | Integrated product/layout tests passed. Needs hosted visual Railway run after push. |
| Custom footprints | integrated locally | Noether `019ecfa8-f142-7c70-9fae-fe842b6dcaae` | MCP server upload/preflight path | Hosted MCP accepts project footprints/libs so MR-1 and 45lux do not fail footprint preflight. | Integrated product/layout tests passed. Needs hosted visual Railway run after push. |
| Floorplan API | integrated locally | Zeno `019ecfa8-f179-7fa0-a994-e735921e8490` | `submit_skidl_code` envelope and docs | Agents can pass fixed positions, edge anchors, grids, sides, keepouts, outline, and later cutouts explicitly. | Integrated product/layout tests passed. Needs hosted visual Railway run after push. |

## Standard Test Boards

- MCP9808 or ADS1115 breakout with pin header and optional mounting holes.
- Simple IO / terminal block utility board.
- Daisy / ESP32 dev board carrier.
- Eurorack LFO/VCO/utility module with jacks, pots, power header.
- MR-1 / 45lux floorplan smoke tests once custom footprints are available.

Each test run should capture: `job_id`, `run_id`, terminal status, routed/manufacturable status, preview path, and concise visual defects.

## Hosted Bug Queue

- Mycelium hosted submissions can spin without producing a preview or terminal result:
  - Full board job `ba9ef6f36af5`, `timeout_s=900`, still reported `running`.
  - Reduced core job `5170e4a63245`, `timeout_s=300`, still reported `running`.
  - Important clue: the reduced job strips display/nav/BT/extras, so if it also hangs this is likely service-side placement/runtime or custom-part SKiDL-shape handling, not just board complexity.
  - Next checks: inspect Railway worker logs by job id, confirm watchdog behavior, ensure claimed jobs always emit either a `run_id` result or a structured timeout/crash exception, and add a regression test for a worker/pipeline hang path.
  - Follow-up after Wave 2 deploy: both jobs became terminal.
    - Full board `ba9ef6f36af5` -> `failed`, no `run_id`, error `worker lost while job was running; resubmit the design`.
    - Reduced core `5170e4a63245` -> `failed`, run `ddc3d32fa1bd`, placement-review feedback with overlaps, outline violations, high congestion, and long power nets.
  - Refined bug: reduced job now produces useful circuit/layout feedback, but full-board worker loss still needs a structured exception/result and better log correlation.
  - Fix prepared: stale/lost jobs now become top-level `status="crashed"` with a structured `ENGINE_CRASH` result, `stage="worker_lost"`, a `regenerate` candidate, and a hint that no run artifacts were produced.
  - Hosted verification boundary: no public MCP/admin endpoint safely creates a stale running job, and production DB helper calls are global queue mutations. Current proof is the DB-level product test plus deployed code status. A hosted `get_job` proof requires a staging DB or a restricted sentinel-job admin endpoint.

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

2026-06-16 hosted Wave 1 smoke:

- Railway deployed commit:
  - `366f009e` (`Track Wave 2 layout agents`)
- Direct MCP tools call succeeded.
- Placement-review smoke:
  - job `a02a4c768fb1`
  - run `08675b9e3471`
  - board `wave1_mcp9808_smoke_retry`
  - result `succeeded` / response status `succeeded_with_warnings`
  - placement score `94.56`
  - artifacts included PCB, schematic, and previews.
- Useful residual issue from smoke:
  - `EDGE_ANCHOR_OFF_EDGE` warned for `J1` bottom at `9.89mm` despite reported board margins of `3mm`.
  - Likely quality-report false positive from measuring footprint origin instead of mating face / edge geometry.
  - Sent to Peirce (`019ecfa8-f0cd-7411-85ba-2cc483c8b902`) for Wave 2 routing/layout quality diagnosis.

2026-06-16 integrated Wave 2 verification:

- Wave 2 implementation agents all reported complete:
  - Peirce: routing failure diagnosis and edge-anchor false-positive suppression.
  - Ptolemy: explicit front/back side policy and validator coverage.
  - Noether: hosted custom footprint payload validation and handoff.
  - Zeno: richer `EDA_FLOORPLAN` contract using the existing code-mode mechanism.
- Product/MCP focused command:
  - `.venv/bin/python -m pytest tests/product/test_mcp_server.py tests/product/test_railway.py tests/unit_tests/test_product_layer.py -q`
- Result:
  - `211 passed, 55 skipped, 1 warning`
- Full layout sweep command:
  - `.venv/bin/python -m pytest tests/unit_tests/test_layout_*.py -q`
- Result:
  - `324 passed, 4 skipped`
- Syntax/checks:
  - `git diff --check` clean
  - `.venv/bin/python -m py_compile mcp_server/engine_worker.py mcp_server/exception_mapper.py mcp_server/layout_quality.py mcp_server/server_http.py tests/product/test_mcp_server.py`
- Remaining verification needed:
  - Commit/push Wave 2.
  - Confirm Railway deploys the Wave 2 commit.
  - Run hosted review boards for custom footprints, explicit floorplan preservation, side policy, and routing diagnosis.

2026-06-16 hosted Wave 2 smoke:

- Railway deployed commit:
  - `305b5148` (`Integrate Wave 2 MCP layout contracts`)
- Deployment state:
  - `worker` deployment `f44e8a40-4848-4791-8fa3-f7d432b6b474` -> `SUCCESS`, running.
  - `mcp-server` deployment `d6c5511b-4120-4a62-8ab4-6f9195ac2f41` -> `SUCCESS`, running.
- Direct MCP tools call succeeded; hosted tools: `submit_skidl_code`, `get_job`, `get_run`, `submit_human_feedback`, `search_kicad`, `convert_lcsc`.
- MCP9808 edge-anchor placement smoke:
  - job `69c4dddcdadb`
  - run `598dc57750ca`
  - status `succeeded`
  - stored run status `succeeded_with_warnings`
  - placement score `90.44`
  - no edge-anchor false-positive warning in placement score
  - artifacts included PCB, schematic, and previews
- Explicit `EDA_FLOORPLAN` placement smoke:
  - job `cedd87015056`
  - run `dac9edcbfe78`
  - status `succeeded`
  - stored run status `succeeded_with_warnings`
  - placement score `85.66`
  - floorplan metadata survived into `get_run`: `grids=1`, `grid_fixed_positions=3`, `fixed_positions=4`, `edge_anchors=1`, `keepouts=1`
  - `D1/D2/D3` preserved as front-side grid positions; `U1` preserved on `back`
- Inline custom footprint placement smoke:
  - job `29fd14ca8339`
  - run `f523ceafb570`
  - status `succeeded`
  - stored run status `succeeded_with_warnings`
  - placement score `98.73`
  - `get_run` layout reported `inline_footprints={"count": 1, "footprints": ["CustomLib:Tiny_2Pad"]}`
- Remaining hosted gaps:
  - Need hosted confirmation that full-board worker-loss handling produces the new structured `ENGINE_CRASH` packet after deploy; safe production probe is not currently exposed.

2026-06-16 hosted routing-diagnosis smoke:

- Initial bad-footprint hosted smoke before classifier fix:
  - job `4c5a2a4ac305`
  - run `17f8ade1f927`
  - status `failed`
  - exceptions `DRC_UNCONNECTED`, `DRC_SHORT`
  - diagnosis incorrectly came back `congestion_router_limitation` even though the DRC short was two pads on the same submitted custom footprint (`REF**`).
- Classifier fix:
  - commit `570d545d` (`Classify same-footprint DRC failures as footprint issues`)
  - detects KiCad DRC examples where multiple conflicting pads are reported on the same footprint token, including placeholder `REF**`.
  - local tests:
    - `.venv/bin/python -m pytest tests/product/test_mcp_server.py -q` -> `100 passed, 13 skipped`
    - `.venv/bin/python -m pytest tests/product/test_mcp_server.py tests/product/test_railway.py tests/unit_tests/test_product_layer.py -q` -> `214 passed, 55 skipped, 1 warning`
- Hosted confirmation after deploy:
  - job `5889de9103fa`
  - run `af5261730bd4`
  - status `failed`
  - exceptions `DRC_UNCONNECTED`, `DRC_SHORT`
  - both exceptions now include `routing_diagnosis=footprint_issue`
  - evidence hotspot: `{"ref": "REF**", "source": "same_footprint_drc_example", "placeholder_ref": true}`
  - outline growth candidate confidence demoted to `0.2` behind regenerate/footprint-focused repair.
