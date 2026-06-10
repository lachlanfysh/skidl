# Overnight: MCP Tool Wrapper + Cycle Telemetry + Corpus Run

## Context

The operator is turning this SKiDL fork's deterministic EDA engine (schematic gen + layout) into a sellable, agent-callable product: an MCP server exposes the engine; customers' agents drive design generation and course-correct by picking among structured exception candidates. Tonight is step 1 of 3: build the local tool surface + telemetry, run a board corpus through the loop, produce the per-board cost/difficulty dataset that seeds the pricing model. Remote deployment is out of scope but everything must be transport-agnostic.

**Decision priority on any tradeoff:** (1) telemetry data integrity, (2) mid-tier model operability, (3) transport-agnostic code, (4) corpus completion.

**Decisions locked with operator:**
- `input_spec` = **JSON circuit spec** + deterministic JSON→Circuit translator; translation errors become structured exceptions with candidate fixes (e.g. unknown pin → list of the symbol's actual pin names)
- Corpus: all 50 Adafruit boards (internal quality-gate validation) + KiCad-native reference repos (sabogalc/KiCad-Arduino-Boards etc.) with a new netlist/BOM reference oracle; Tier 2-4 (eurorack/VESC/Olimex/Antmicro/CERN) fetched + indexed only
- Caps: MAX_TOTAL_SPEND_USD=$10, MAX_RUNTIME_HOURS=7; per board: 8 correction iterations / 300s wall / 200k tokens
- **OPENROUTER_API_KEY: not set.** Operator provides it at launch time — pause and ask before LAUNCH. Runner must also support engine-only stub mode (LLM stages nulled) so the night degrades gracefully.
- Default models: mid-tier (llama-3.1-70b-instruct class) for ALL stages — tonight tests whether the tool surface works at the tier it must scale at. Frontier comparison subset only if >$3 budget remains.

## Discovery ground truth (verified, file:line)

- `plan_layout() -> LayoutResult` at `src/skidl/layout/engine.py:148`; LayoutResult has `.ok`, `.to_dict()`, validation/score/report/candidates all structured. ~6 placement candidates scored per run.
- ERC loop in `src/skidl/tools/kicad9/gen_schematic.py:703-776`; `_run_erc` (:118) wrappable for iteration counting; `classify_erc_errors` (:190); default `erc_max_iterations=3` — pass 8 explicitly.
- Circuit state is process-global (`builtins.default_circuit`, SchLib cache, unique-name heap — `src/skidl/circuit.py:120-213`).
- Safe lib probing: `SchLib(lib).get_parts_by_name(name, allow_failure=True, partial_parse=True)` (`src/skidl/schlib.py:309`); after `part.parse()`, iterate `part.pins` for num/name/aliases.
- Custom-part helpers `make_pin` + `add_skidl_draw_cmds` proven in `benchmarks/results/als-pt19-light-sensor/circuit.py:26-90` — copy into translator.
- `validate_footprints` (`src/skidl/layout/writer.py:194`); pad counting via `load_footprint` pad nodes.
- **No reference-diff oracle exists; no LLM client exists; `mcp` pkg not installed** (pip install needed). kicad-cli 9.0.9 present; `kicad-cli sch export netlist --format kicadsexpr` works. Python 3.12, pydantic 2.12, httpx 0.28, 8 cores, 138GB free.
- Existing assets: 2 manifests (50 boards), 50 generated `circuit.py` files, `prompt_template.py` SYSTEM_CONTEXT, exec-and-harvest pattern in `run_layout.py` files.
- Dirty tree: 13 regenerated benchmark boards + 3 engine files uncommitted — commit first.

## Architecture decisions

**Subprocess-per-run** (`python -m mcp_server.engine_worker`, spec JSON on stdin, result JSON on stdout):
- Exact per-run `cpu_time_s`/`peak_rss_mb` via `getrusage(RUSAGE_SELF)` (P1)
- Reliable 300s watchdog: `Popen(start_new_session=True)` + `communicate(timeout)` + `os.killpg(SIGKILL)` — `signal.alarm` is unreliable across the ERC loop's nested `subprocess.run` calls
- Full state isolation (global default_circuit etc.); crash = structured `ENGINE_CRASH`, not a dead server
- Cost ~1-2s/spawn; SchLib pickle cache persists on disk across processes

**Build `corpus/circuit_to_spec.py` early** (highest-value de-risk): exec existing 50 benchmark circuits (strip generate_* lines — pattern from `run_layout.py:6-11`), walk `builtins.default_circuit`, emit JSON specs. Yields: (a) ground-truth specs for engine-only mode, (b) 2 worked examples for the NL prompt, (c) translator round-trip test.

**Telemetry JSONL append**: single `os.write` on `O_APPEND|O_CREAT` fd + fsync (atomic at 5-20KB record sizes, multi-process safe); tolerant reader skips bad trailing line.

**Async httpx + asyncio runner** (the `mcp` python client is async-native; `Semaphore(2)` = the worker pool).

## Module specs (new top-level dirs — NOT in src/skidl)

### `schemas/`
- `circuit_spec.py` — pydantic: `CircuitSpec{schema_version:"1", board:{name, form_factor?, outline_hint_mm?, layers:2}, parts:[{ref, lib?, part?, value?, footprint, pins?:[{num,name,func}], group?}], nets:[{name, power:false, stub:false, pins:["REF.PIN"]}], waivers:[]}`. lib=None ⇒ custom part with explicit pins. Multi-unit parts out of scope v1.
- `translator.py` — `translate(spec, sym_dirs, fp_dirs) -> TranslationResult{circuit|None, exceptions[]}`. Validation passes (collect ALL errors per pass, never fail-fast): pydantic → libs → parts → footprints → pins → build. Candidates via `difflib.get_close_matches` (libs: dir listing; parts: lib part names; pins: the symbol's actual pin list; footprints: same-.pretty listing). Build inside `with Circuit(name=...) as ckt`: nets first (drive=POWER, stubs), `@subcircuit` closure per `group`, custom parts via copied `make_pin`/`add_skidl_draw_cmds`, then `part[pin] += net`.
- `exceptions.py` — `DesignException{id, code, severity(fatal|error|advisory), message, subject, candidates:[{id:"c1.."、action, params, human_summary, cost_hint}], retry_hint}`. Codes: SPEC_MALFORMED/UNKNOWN_LIB/UNKNOWN_PART/UNKNOWN_PIN/BAD_FOOTPRINT, FOOTPRINT_MISSING, SCH_PLACEMENT_FAILURE/ROUTING_FAILURE, ERC_PIN_NOT_CONNECTED/PIN_NOT_DRIVEN/REAL_ERROR, LAYOUT_OVERLAP/OUTLINE_VIOLATION/KEEPOUT/MISSING_REF, HIGH_CONGESTION, LONG_POWER_NET, ENGINE_TIMEOUT/CRASH, BUDGET_EXHAUSTED.
- `corrections.py` — `apply_candidate(spec, exc, cand) -> new spec` (pure, deep-copy). Actions: replace_lib/part/pin/footprint, remove_part, remove_net_pin, stub_net, set_form_factor, set_outline, scale_outline (sides × √factor), accept_advisory (append to waivers), regenerate.

### `telemetry/` (keystone — tests FIRST, no transport imports)
- `models.py` — `RunRecord{run_id, parent_run_id, board_id, git_sha, timestamps, wall_time_s, tier, source, difficulty_axis, nl_source, mode(internal|external|engine_only), model_tier, geometry:{component_count, net_count, pin_count, pad_count, layer_count, board_area_mm2, pad_density_per_cm2}, correction_iterations, candidates_scored, erc_iterations, schematic_retries, exceptions_raised:[codes], corrections_applied:[actions], llm_stages:[{stage, model, tokens_in, tokens_out, latency_s, cost_usd}], total_cost_usd, cpu_time_s, peak_rss_mb, status, validation_mode(internal|reference|none), layout_score, total_hpwl_mm, congestion_score, bom_match_score?, netlist_match_score?, failure_reason?}`
- `store.py` — `atomic_append`; `session(board_id, mode) -> ctxmgr` writing record in `finally` even on raise.
- `features.py` — geometry extraction from spec + worker metrics.
- Tests: round-trip; crash-in-session still writes; 3-process concurrent append.

### `llm/`
- `openrouter_client.py` — async httpx; `complete(...) -> LLMResponse{text, tokens_in, tokens_out, latency_s, cost_usd, model}`; usage from response (verify field names against live docs at build time), fallback PRICE_TABLE; retry 429/5xx backoff 1/4/10s ×3.
- `spend_tracker.py` — threading.Lock; pre-flight estimate veto vs cap; BudgetExhausted sentinel; `telemetry/spend_log.jsonl`.
- `operations.py` — `nl_to_input_spec` (schema + KiCad guidance from prompt_template.py + 2 extracted worked examples; json_schema response_format w/ json_object fallback; one repair retry); `review_exceptions` → bare `{exception_id, candidate_id}` pairs (mid-tier friendly), validated, one repair retry; `external_agent_review` same interface, prompt = third-party agent reading the MCP tool docs verbatim.
- `config.py` — DESIGN_MODEL/REVIEW_MODEL/FRONTIER_MODEL env vars; verify availability+pricing via `/api/v1/models` at build time.

### `mcp_server/`
- `engine_worker.py` — stdin spec → translate → `generate_schematic(filepath=out_dir, retries=2, auto_stub=True, erc_max_iterations=8)` → residual ERC re-check (`_run_erc` + `classify_erc_errors`) → `plan_layout(ckt, constraints w/ form_factor)` → `write_kicad_pcb` → one-line JSON {status, exceptions, outputs, layout.to_dict(), metrics{cpu, rss, erc_iterations, candidates_scored, pad/pin counts}}. Wrap `_run_erc` (monkeypatch, no src edits) for the counter. cwd=`artifacts/runs/{run_id}/`; `rt_logger.stop_file_output()`.
- `pipeline.py` — **transport-agnostic core**: `run_pipeline(spec, out_dir, timeout_s=300) -> DesignResponse` (spawn worker, watchdog, map exceptions). MCP server, runner `--no-mcp` fallback, and tests all import this.
- `exception_mapper.py` — signal→exception table incl. candidates (overlaps → scale_outline 1.25 / set_form_factor / accept_advisory; unknown pin → actual pin list; missing_refs → replace_footprint/remove_part; etc.). Applies spec.waivers.
- `runs.py` — RunStore: in-memory + `artifacts/runs/{run_id}/{spec,exceptions,response}.json` (crash recovery).
- `server.py` — FastMCP stdio: `generate_design(input_spec)`, `apply_correction(run_id, corrections:[{exception_id, candidate_id}])` (bad id → error listing valid ids + retry_hint), `get_run_telemetry(run_id)`. Every field described; worked example in tool docstring; `MCP_TRANSPORT` env read but only stdio wired.

### `corpus/`
- `fetch_corpus.py` — idempotent shallow clones into `corpus/sources/`; Tier 1 refs: sabogalc/KiCad-Arduino-Boards (+ Easyduino-equivalent found at runtime via web search); Tier 2-4 fetch+index only (pichenettes/eurorack, coriolisinstruments/EurorackModules, VESC repos, OLIMEX, antmicro, ohwr.org picks).
- `build_manifest.py` — normalize both Adafruit manifests + cloned refs → `corpus/manifest.jsonl` {board_id (= results/ slug, canonical), tier, source, difficulty_axis, nl_source, description, reference_project_path?, validation_mode(internal|reference|indexed_only)}.
- `circuit_to_spec.py` — per Architecture; CLI `--all` → `corpus/specs/{slug}.json` + round-trip report (accept ≥40/50).
- `reference_oracle.py` — `kicad-cli sch export netlist --format kicadsexpr` + simp_sexp parse; `bom_match_score` (component-key multiset overlap; passives keyed (prefix, value, size), ICs by name); `netlist_match_score` (nets as multisets of (component_key, pin_label), greedy best-Jaccard matching). Crude but rankable.
- `run_corpus.py` — asyncio, Semaphore(2); per board: telemetry session → spec (cached/engine-only or nl_to_input_spec) → MCP client `generate_design` → correction loop ≤8 iters (review_exceptions / external_agent_review / deterministic-c1 in engine-only), per-board 300s/200k caps → scores → record. Checkpoint/resume: skip (board_id, mode) with terminal status in runs.jsonl. Stop new boards at MAX_RUNTIME_HOURS−0.25; BudgetExhausted degrades to engine_only. `--no-mcp` direct-pipeline fallback. PID file.
- `launch.sh` — nohup, PID file, log tail hint.

### `analysis/report.py`
Reads only `telemetry/runs.jsonl` → `docs/MORNING_REPORT.md`: per-tier cost distributions (p50/p90/p99 + named tail boards), difficulty-axis slices, mode comparison, failure taxonomy by code, mid-tier vs frontier verdict if both exist, nl_source sensitivity, corpus coverage, honest gaps.

### docs
`docs/DISCOVERY.md` (Phase 0 findings incl. chosen models + rates), `docs/EXCEPTION_GAPS.md` (engine errors with no clean mapping).

## Night timeline (cut order at bottom)

| Phase | Window | Deliverable + acceptance | Commit |
|---|---|---|---|
| 0 | 0:00-0:15 | Commit dirty tree; branch `feat/overnight-product-layer`; pip install mcp; dir skeleton; docs/DISCOVERY.md; .gitignore artifacts/ + telemetry/*.jsonl + corpus/sources/ | `phase0: discovery` |
| 1 | 0:15-1:15 | schemas/ — ads1115 hand spec translates clean; 5 seeded-error specs → expected codes+candidates; apply_candidate tests | `phase1a: schemas` |
| 2 | 1:15-2:00 | circuit_to_spec + 50 specs — ≥40/50 round-trip; pick 2 worked examples | `phase1b: spec extraction` |
| 3 | 2:00-3:00 | telemetry/ (tests first) + mcp_server/ — pytest green; worker smoke via echo-pipe; MCP stdio client produces .kicad_sch+.kicad_pcb for ads1115 | `phase2: telemetry+mcp` |
| 4 | 3:00-3:45 | llm/ — mocked tests; live smoke if key present | `phase1.5: llm layer` |
| 5 | 3:45-4:30 | corpus fetch + manifest + oracle — oracle self-compare =1.0, mutated <1.0 | `phase3a: corpus+oracle` |
| 6 | 4:30-5:00 | runner + engine-only dry-run 3 boards → 3 well-formed RunRecords | `phase3b: runner` |
| **LAUNCH** | ~5:00 | **Ask operator for OPENROUTER_API_KEY.** Run order: (a) engine_only × 50 ($0 baseline), (b) internal mode × corpus, (c) external mode subset, (d) frontier subset if >$3 left. If no key: launch engine-only; LLM modes resume later via checkpoint | — |
| 7 | 5:00-5:30 | analysis/report.py (renders from partial runs.jsonl); final commit | `phase4: morning report` |

**Cut order if behind:** frontier subset → external mode → Tier 2-4 fetch → netlist_match_score (keep bom) → concurrency (serial) → reference boards (keep 50 Adafruit engine_only + internal).

## Verification
1. Per-phase acceptance tests above; new tests in `tests/product/` run via pytest.
2. End-to-end before launch: MCP stdio client → generate_design(ads1115 spec) → exceptions/outputs/telemetry row; apply_correction with a seeded bad pin → fixed rerun.
3. Crash-safety: kill -9 the worker mid-run → RunRecord still written with status=crashed.
4. `python -m analysis.report` renders from whatever runs.jsonl exists.
5. Morning: runs.jsonl row count ≥ boards attempted; no unparsable rows except possibly trailing.

## Risks
1. Mid-tier JSON garbage from nl_to_input_spec → schema-in-prompt + real worked examples + 1 repair retry; cached specs guarantee loop-half of dataset regardless.
2. Engine wall-time blowups (clue/grand-central class) → 300s SIGKILL; timeout is data.
3. OpenRouter cost field drift → fallback price table; log both.
4. MCP SDK friction overnight → pipeline.py import-callable; `--no-mcp` flag, identical telemetry.
5. SchLib pickle-cache races → already try/except'd upstream; concurrency ≤2.
6. Budget blowout → pre-flight veto + engine-only baseline runs first ($0 at risk until loop starts).
