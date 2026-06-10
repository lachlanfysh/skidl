# eda-mcp

Agent-callable PCB generation engine via MCP.

AI agents send a JSON circuit specification; the engine generates KiCad
schematics and PCB layouts. Structured exceptions with machine-readable
correction candidates let the agent fix and retry without natural language.


## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for the full pipeline,
module map, correction loop mechanics, and design decisions.


## Quick Start

Requirements: Python 3.10+, KiCad 9 (for symbol/footprint libraries),
system packages for pcbnew bindings.

```bash
# Install dependencies (editable install of the SKiDL engine)
pip install -e .

# Verify the schemas and translator
pytest tests/product/test_schemas.py -v

# Run the full product test suite
pytest tests/product/ -v

# Run the telemetry tests
pytest tests/product/test_telemetry.py -v

# Run the LLM operations tests (mocked, no API key needed)
pytest tests/product/test_llm.py -v
```

### Corpus Run

The corpus runner processes a manifest of boards through the pipeline with
checkpoint/resume and bounded concurrency.

```bash
# Engine-only mode (no LLM, uses cached specs)
python3 -m corpus.run_corpus --mode engine_only --no-mcp

# With LLM translation and review (needs OPENROUTER_API_KEY)
export OPENROUTER_API_KEY=sk-or-...
python3 -m corpus.run_corpus --mode internal --no-mcp --max-total-spend-usd 5

# Resume a stopped run (skips completed boards automatically)
python3 -m corpus.run_corpus --mode engine_only --no-mcp

# Force re-run of all boards
python3 -m corpus.run_corpus --mode engine_only --no-mcp --force
```

Results land in `telemetry/runs.jsonl` (one JSON record per board).


## Status

Overnight corpus run in progress. The morning data in
`telemetry/runs.jsonl` shows per-board pass/fail rates, correction loop
iterations, layout quality scores (HPWL, congestion), LLM token usage and
cost, and reference oracle similarity scores where available. This is the
primary feedback signal for engine improvements.


## Repo Layout

```
schemas/          JSON contract: CircuitSpec, DesignException, corrections
mcp_server/       MCP tool surface (generate_design, apply_correction)
llm/              OpenRouter client, design translation, exception review
telemetry/        RunRecord models, atomic JSONL store, geometry features
corpus/           Manifest runner, reference oracle, source fetcher
src/skidl/        SKiDL engine (dependency, not the product)
tests/product/    Product-layer test suite
```


## Not Open Source

This repo is private/proprietary. SKiDL (in `src/skidl/`) is MIT-licensed
upstream; everything else in this repo is not.
