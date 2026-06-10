# Phase 0 Discovery — Overnight Product Layer Build

Date: 2026-06-10 ~20:00 AEST. Branch: `feat/overnight-product-layer` (from `feat/adafruit-benchmark` @ 353667d4).

## Engine ground truth

| Question | Answer |
|---|---|
| Engine entry points | `circuit.generate_schematic(filepath, retries=2, auto_stub=True, erc_max_iterations=N)` (`src/skidl/circuit.py:1274`, writes `.kicad_sch`); `plan_layout(circuit, ...) -> LayoutResult` (`src/skidl/layout/engine.py:148`); `write_kicad_pcb(placed, circuit, fp_lib_dirs, path, outline)` (`src/skidl/layout/writer.py`) |
| Input format | SKiDL Python script building a `Circuit` in-process. **No JSON spec existed** — built tonight as `schemas/circuit_spec.py` + deterministic translator |
| Exception surface | Engine returns structured results, rarely raises: `ValidationResult` (overlaps/outline/keepout/missing), `LayoutScore` (.ok, congestion, warnings), `PlacementReport.top_risks()`. `PlacementFailure`/`RoutingFailure` raised by schematic gen. ERC via kicad-cli parse: `(error_type, ref, pin)` tuples, `classify_erc_errors` splits real vs SKIDL-tool noise |
| Existing Adafruit harness | `benchmarks/`: 2 manifests (50 boards, marketing descriptions), 50 generated `circuit.py`, internal quality-gate scoring only. **No reference-diff oracle existed** — references are Eagle format; KiCad-native reference repos fetched tonight instead |
| LLM client code | **None existed.** Built tonight in `llm/` |
| Circuit state | Process-global (`builtins.default_circuit`, SchLib cache, unique-name heap) → **subprocess-per-run** architecture |

## Environment

- Python 3.12.3, pydantic 2.12.5, httpx 0.28.1, mcp 1.27.2 (installed tonight, `--user --break-system-packages`)
- kicad-cli 9.0.9; `kicad-cli sch export netlist --format kicadsexpr` confirmed
- KiCad libs: `/usr/share/kicad/symbols`, `/usr/share/kicad/footprints`
- 8 cores, 15GB RAM, 138GB free
- OPENROUTER_API_KEY: **not set at build time** — provided at launch; runner supports engine-only stub mode

## Deviations from the original plan

1. **No existing reference-diff step** (plan assumed one). Tier 1 Adafruit validation = internal quality gates (`validation_mode=internal`). Reference oracle built fresh for KiCad-native repos (`validation_mode=reference`).
2. **No existing NL→input stage** — the benchmark used Claude agents writing SKiDL Python. Tonight's `input_spec` is a new JSON circuit spec; `corpus/circuit_to_spec.py` extracts ground-truth specs from the 50 existing circuits for engine-only mode + worked examples.
3. **Engine not thread-safe** (global state) → engine runs subprocess-per-run, which also gives exact cpu/RSS telemetry and a reliable SIGKILL watchdog.
4. `erc_max_iterations` defaults to 3, not 8 — worker passes 8 explicitly.

## Models chosen (filled at LLM-layer build)

- DESIGN_MODEL / REVIEW_MODEL: see `llm/config.py` (verified against OpenRouter /api/v1/models at build time)
- Pricing table: see `llm/config.py` PRICE_TABLE
