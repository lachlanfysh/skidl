# eda-mcp Architecture

Internal reference doc. Last updated 2026-06-13.

---

## What Is This?

You give an AI agent a product description like "an I2C temperature sensor breakout" and it designs you a PCB. Not a picture of one - an actual KiCad schematic, routed board, and manufacturing bundle you could inspect in KiCad and send to a fab house.

eda-mcp is the engine that makes this work. The public MCP surface now accepts SKiDL Python code through `submit_skidl_code()`. The agent writes real SKiDL (`Part()`, `Net()`, pin connections), while the server owns schematic generation, placement, routing, DRC, artifact packaging, and manufacturing output. When something goes wrong - a pin name doesn't exist, parts overlap on the board, a footprint isn't installed - it doesn't just fail. It returns structured exceptions and candidate fixes the agent can use to edit the SKiDL code and resubmit.

CircuitSpec JSON still exists, but it is now a legacy/internal surface: useful for corpus runners, deterministic translator tests, and compatibility checks. It should not be the default agent UX. New clients should use `search_kicad()` -> `submit_skidl_code()` -> `get_job()` -> `get_run()`.

---

## The Big Picture

```mermaid
flowchart LR
    A["Product Description"] --> B["Agent plans BOM\nand calls search_kicad()"]
    B --> C["SKiDL Python\nPart(), Net(), pins"]
    C --> D["submit_skidl_code()"]
    D --> E["Isolated Worker\nruns user code"]
    E --> F["SKiDL Circuit"]
    F --> G["Design Review\n+ Enrichment"]
    G -->|exceptions| H["DesignExceptions\n+ Candidates"]
    H --> I{"Agent decision"}
    I --> J["Edit SKiDL code\nand resubmit"]
    J --> D
    G -->|clean| K["Schematic Gen\n(.kicad_sch)"]
    K --> L["Layout + Routing\n+ DRC"]
    L --> M["KiCad + Manufacturing\nArtifacts"]
    M --> N["Telemetry Record"]
    O["Legacy JSON CircuitSpec"] -. internal/corpus .-> F
```

---

## Processing Stages — What Happens At Each Step

### Stage 1: From Words to SKiDL

```mermaid
flowchart TD
    A["Design request:\n'I2C ADC breakout with header'"] --> B["Agent searches KiCad/LCSC\nfor symbols, footprints, stock"]
    B --> C["SKiDL Python code"]
    C --> D["Parts, nets, connectors,\npower rails, decoupling"]
```

**What it does:** The agent turns intent into executable SKiDL. It should search before coding, use exact KiCad library/part/footprint names, set power rails to `POWER`, include connectors, and add enough passives to make the board physically and electrically plausible.

**What can go wrong:** The agent picks a pin name that doesn't exist, uses a footprint from a library that isn't installed, forgets a connector, or writes invalid SKiDL. That's fine - the pipeline returns structured exceptions and the agent edits the code.

### Stage 2: Validation And Circuit Extraction

```mermaid
flowchart TD
    A["SKiDL Python"] --> B["Worker executes in\nisolated subprocess"]
    B --> C["SKiDL Circuit"]
    C --> D["Check symbols,\nfootprints, pins,\ndesign policy"]
    D -->|all clean| E["Generate artifacts"]
    B & D -->|problems found| F["DesignExceptions\nwith fix candidates"]
```

**What it does:** The worker runs user code out-of-process, extracts the SKiDL circuit, checks it against installed KiCad resources and product policy, and maps failures into structured exceptions.

**Why it matters:** The agent said pin "VDD" but the real symbol calls it "IOVDD"? The engine can return valid pin names and ranked candidates. No AI is needed for that part - it is filesystem lookups, SKiDL/KiCad introspection, and deterministic policy.

### Stage 3: Schematic Generation

```mermaid
flowchart TD
    A["SKiDL Circuit\n(in-memory)"] --> B["Place symbols on sheets\n(force-directed algorithm)"]
    B --> C["Route wires between pins"]
    C --> D["Run ERC\n(Electrical Rules Check)"]
    D -->|errors| E["Auto-fix common issues\n(stub unconnected pins,\nadd power flags)"]
    E --> D
    D -->|clean or max retries| F[".kicad_sch files\n(one per subcircuit)"]
```

**What it does:** Converts the validated circuit into actual KiCad schematic files. Components are placed using a force-directed algorithm (things that connect pull toward each other), wires are routed, and KiCad's built-in electrical rules check runs up to 8 iterations with auto-fixes for common nuisance errors.

### Stage 4: PCB Layout

```mermaid
flowchart TD
    A["SKiDL Circuit +\nFootprint data"] --> B["Layer 1: Fixed positions\n(user-specified)"]
    B --> C["Layer 2: Decoupling caps\n(1.5mm from their IC)"]
    C --> D["Layer 3: Signal passives\n(stacked below connected IC)"]
    D --> E["Layer 4: Everything else\n(shelf-packed by group)"]
    E --> F["Validate: overlaps?\noutside board edge?\nmissing parts?"]
    F --> G[".kicad_pcb file"]
```

**What it does:** Places physical components on the board in priority order. Decoupling capacitors get placed right next to their IC (the engine detects them by value pattern — "100nF" — and power net connections). Parts in the same subcircuit group cluster together. Validation checks for overlaps and boundary violations.

### Stage 5: Telemetry

Every single run — success, failure, timeout, crash — produces exactly one record in `telemetry/runs.jsonl`. The record captures everything: how long it took, how much it cost, how many corrections were needed, layout quality metrics, what went wrong if it failed. This is crash-safe (writes even on KeyboardInterrupt) and concurrent-safe (atomic append).

---

## The Correction Loop - The Core Innovation

This is what makes eda-mcp a product, not just a script.

```mermaid
flowchart TD
    A["SKiDL Python"] --> B["Worker + Engine"]
    B -->|success| C["Schematic + Routed PCB\n+ Manufacturing bundle"]
    B -->|exceptions| D["DesignExceptions\neach with ranked Candidates"]
    D --> E{"Agent decision"}
    E --> F["Edit code:\nfix pin, part, footprint,\noutline, connector, policy"]
    F --> A
    G["Legacy CircuitSpec"] -.-> H["apply_candidate()\nmutates JSON spec"]
    H -.-> B
```

**How an exception looks:**

The spec says `U1.VBUS` but the real chip only has pins `VCC, GND, SDA, SCL`. The engine returns:

| Candidate | Action | Fix |
|-----------|--------|-----|
| c1 | replace_pin | Change `U1.VBUS` to `U1.VCC` |
| c2 | replace_pin | Change `U1.VBUS` to `U1.SDA` |
| c3 | remove_net_pin | Drop this connection entirely |

For SKiDL Python runs, the agent edits the code (for example replacing `u1["VBUS"]` with `u1["VCC"]`) and submits again. For legacy CircuitSpec runs, `apply_correction()` can still mutate JSON specs directly, but that path is no longer the public product surface.

**The 12 correction actions:**

| Action | What it does | Example |
|--------|-------------|---------|
| replace_lib | Swap a symbol library | `Sensor` -> `Sensor_Temperature` |
| replace_part | Swap a part name | `MCP9808` -> `MCP9808_MSOP` |
| replace_pin | Swap a pin reference | `U1.VDD` -> `U1.IOVDD` |
| replace_footprint | Swap a footprint | `R_0603_bad` -> `R_0603_1608Metric` |
| remove_part | Delete a component + clean nets | Remove `U3` and all its connections |
| remove_net_pin | Drop one pin from a net | Remove `U1.NC` from net `SDA` |
| stub_net | Mark net as label-only | Don't route `ALERT` as a wire |
| set_form_factor | Apply standard board shape | `feather` (51x23mm) |
| set_outline | Set custom board size | 60mm x 40mm |
| scale_outline | Grow the board | Area x 1.44 (sides x 1.2) |
| accept_advisory | Waive a warning | "Yes I know congestion is high" |
| regenerate | Just try again | Same spec, fresh run |

---

## Module Map

```mermaid
graph TD
    subgraph "Contract Layer"
        S0["SKiDL Python\nPublic MCP input"]
        S1["schemas/circuit_spec.py\nLegacy/internal JSON IR"]
        S2["schemas/translator.py\n5-pass validator"]
        S3["schemas/exceptions.py\n18 error codes + candidates"]
        S4["schemas/corrections.py\n12 mutation actions"]
    end

    subgraph "Engine (SKiDL dependency)"
        E1["generate_schematic()"]
        E2["plan_layout()"]
        E3["write_kicad_pcb()"]
    end

    subgraph "Product Surface"
        M0["mcp_server/server_http.py\nsubmit_skidl_code + async jobs"]
        M1["mcp_server/server.py\nLegacy stdio JSON tools"]
        M2["mcp_server/pipeline.py\nSubprocess isolation + watchdog"]
        M3["mcp_server/engine_worker.py\nThe actual subprocess"]
        M4["mcp_server/policy.py\nAuto-correction rules"]
    end

    subgraph "LLM Layer"
        L1["llm/operations.py\nspec generation + review"]
        L2["llm/openrouter_client.py\nAsync HTTP + retry"]
        L3["llm/spend_tracker.py\n$10 hard cap"]
    end

    subgraph "Data Collection"
        T1["telemetry/store.py\nCrash-safe JSONL writes"]
        T2["telemetry/models.py\nRunRecord (40 fields)"]
        T3["corpus/run_corpus.py\nOvernight benchmark runner"]
    end

    M1 --> M2 --> M3
    M3 --> S2 --> E1 --> E2 --> E3
    S2 -->|errors| S3
    S3 -->|fix| S4 --> S1
    L1 --> L2
    L2 --> L3
    T3 --> M2
    T3 --> L1
    M3 --> T1
```

---

## Key Design Decisions

**Why a subprocess per run?**
SKiDL uses global state (there's one "default circuit" shared across the whole Python process). Running two boards at once would corrupt them both. The subprocess boundary also gives us a clean kill switch — if a board hangs, we SIGKILL the entire process group. Clean, reliable, no leaked state.

**Why JSON, not Python?**
The original SKiDL workflow is Python scripts. But you can't safely let an AI agent write and execute arbitrary Python. JSON specs are pure data: validatable, diffable, storable, replayable. The correction loop stays in JSON end-to-end — no code generation or eval anywhere.

**Why Llama 70B, not Claude/GPT-4?**
Cost. The overnight run processes 50+ boards with multiple correction iterations each. At $0.10/$0.32 per million tokens, Llama 3.3 70B keeps the whole run under $10. The prompts are designed for mid-tier success: rigid schemas, worked examples, one repair retry, and a deterministic fallback when the model produces garbage.

**Why atomic JSONL, not a database?**
One file, no dependencies, crash-safe by construction. Each record is written with a single `os.write()` syscall on an `O_APPEND` file descriptor. If the process dies mid-run, you lose at most one partial line at the end — the tolerant reader skips it. No Postgres, no SQLite, no migration scripts.

**Why candidates, not error messages?**
A traditional engine returns "pin VBUS not found" and the agent has to figure out what to do. eda-mcp returns "pin VBUS not found, here are the 4 real pins on this chip, pick one." The agent's job reduces from "understand electronics and debug the problem" to "pick option 1, 2, or 3." A Llama 70B can do that. A frontier model isn't needed.

---

## Overnight Corpus Run

The corpus runner drives 50 Adafruit benchmark boards + 10 reference designs through the pipeline in three phases:

```mermaid
flowchart LR
    A["Phase A\nengine_only\n50 boards, $0\n~2 hours"] --> B["Phase B\ninternal mode\n50 boards, LLM\n~4 hours, $8 cap"]
    B --> C["Phase C\nexternal mode\n10 board subset\n~1 hour, $10 cap"]
    C --> D["telemetry/runs.jsonl\n100+ records\ncost + quality data"]
```

**Phase A** uses pre-extracted JSON specs (ground truth from existing benchmark circuits). This measures: "how well does the engine perform with perfect input?"

**Phase B** starts from marketing descriptions, has the LLM generate specs from scratch, and uses LLM-reviewed corrections. This measures: "how well does the full product work end-to-end, and what does it cost?"

**Phase C** reframes the review prompts as a third-party agent consuming the API. This measures: "does the MCP tool surface work for external consumers?"

The morning data seeds the pricing model: per-board cost distribution, which boards are hard, where mid-tier models fail, and whether the correction loop converges.
