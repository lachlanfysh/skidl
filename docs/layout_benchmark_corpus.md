# Layout Benchmark Corpus

Reusable mock-circuit test cases for the SKiDL PCB layout engine.
No KiCad installation needed — circuits are built from lightweight mock objects.

## Running

```bash
# All benchmark invariant tests
pytest tests/unit_tests/test_layout_benchmark_cases.py -v

# All diagnostic / threshold tests
pytest tests/unit_tests/test_layout_benchmark_diagnostics.py -v

# Both together
pytest -k layout_benchmark -v

# Stop on first failure (useful during placer development)
pytest -k layout_benchmark --maxfail=1 -v
```

## Test Cases

### 1. USB-Powered MCU Board (15 parts)

**Circuit:** STM32 (LQFP-48) + USB-C connector + SOT-23-5 LDO regulator +
8 MHz crystal + 3 decoupling caps + bulk input cap + USB series resistors +
crystal load caps + SWD debug header (2x5 pin header).

**Board:** 50 x 35 mm, 2-layer default.

**Invariants protected:**
- USB connector inferred as edge connector with bottom-edge anchor
- Crystal has `crystal_network` intent
- Debug header has `test_debug` intent
- Decoupling caps placed within 25 mm of MCU
- Multiple placement candidates generated (baseline, connector_edge_first, etc.)
- 4-layer scoring produces >= as many power planes as 2-layer

**Known weaknesses (as of initial corpus):**
- Score ~49/100 — passives scatter because the placer doesn't weight
  adjacency strongly enough against spiral search
- Decap C2 sometimes lands 19 mm from U1 (goal: <5 mm)
- `repeated_channel_array` candidate sometimes wins even though this
  board has no repeated channels — the channel detector doesn't penalise
  false positives

### 2. Repeated Sensor/Channel Array (18 parts)

**Circuit:** TCA9548A I2C mux (SOIC-16) + 4x DFN-8 sensors on
CH0–CH3 nets + per-channel I2C pull-ups + per-sensor decoupling caps.

**Board:** 80 x 40 mm.

**Invariants protected:**
- Mux detected as `mux_bank_controller` intent
- Channel nets (CH0_SDA, CH0_SCL, etc.) trigger repeated-channel detection
- Per-sensor decaps placed within 20 mm of their sensor
- All 18 parts placed with no outline violations

**Known weaknesses:**
- Score ~62/100 — good clustering but sensors don't form a linear array
- Channel ordering not enforced (U2 for CH0 may end up right of U3 for CH1)
- Pull-up resistors don't cluster near their channel's sensor

### 3. Power-Heavy Board (15 parts)

**Circuit:** JST battery connector + MCP73831 charger (SOT-23-5) +
LDO regulator (MSOP-8) + TQFP-32 load MCU + bulk electrolytics (6.3 mm) +
1206 output cap + feedback divider + charge status LED + inductor.

**Board:** 45 x 30 mm.

**Invariants protected:**
- JST connector has `power_input` and `jst` mating intent
- Regulator and inductor have `power_cluster` intent
- GND power plan has a ground strategy (pour/plane)
- 4-layer board produces `plane` strategy for GND
- Score above floor (catches broken placer output)

**Known weaknesses:**
- Score ~58/100 — bulk electrolytic caps are large (6.3 x 7.7 mm) and
  the placer doesn't prioritise placing them near the regulator output
- Feedback divider (R1/R2) should be near the regulator FB pin but
  currently scatters with other passives
- Charger → regulator → load power chain not placed in thermal/layout
  sequence

### 4. Board UI — Button/LED/Display/Pot (12 parts)

**Circuit:** ATtiny84 (SOIC-16) + 2 tact buttons + 2 LEDs + SSD1306
OLED display (27 x 19.5 mm) + Bourns 3362P trim pot + LED current
limiting resistors + button pull-ups + decoupling cap.

**Board:** 60 x 45 mm.

**Invariants protected:**
- Buttons have `board_ui` and `button` mating intent (`user_control` side)
- LEDs have `led` mating intent (`visible_face` side)
- Display has `display` mating intent
- Potentiometer has `user_control` mating intent
- Face-edge constraints generated for >= 3 parts
- Report summary mentions UI / mating / face-edge content

**Known weaknesses:**
- Score ~40/100 — lowest of the corpus; the OLED display (27 x 19.5 mm)
  dominates the board and the placer doesn't reserve space for it well
- 1 overlap currently — display collides with other parts because its
  bounding box is large relative to the 60 x 45 mm board
- UI parts should cluster on one edge but currently spread across the board
- No panel-alignment logic (buttons and pots should align for physical
  panel cutouts)

### 5. RF Module — ESP32 with Antenna Keepout (11 parts)

**Circuit:** ESP32-WROOM-32E (18 x 25.5 mm) + chip antenna + 2 decoupling
caps + bulk input cap + UART header (1x4) + GPIO header (1x6) +
pull-up resistors + power LED + LED resistor.

**Board:** 55 x 35 mm, with 15 x 15 mm keepout zone in top-right corner
(antenna clearance area).

**Invariants protected:**
- ESP32 or antenna detected as `rf_module` intent
- No keepout violations (parts stay out of antenna zone)
- Connectors have edge intent
- Decaps within 25 mm of ESP32
- Score above floor

**Known weaknesses:**
- Score ~61–75/100 (varies by run) — best of the corpus because the
  ESP32 is large and dominates placement
- Decaps sometimes land 19–24 mm from the ESP32 (goal: <5 mm)
- Antenna keepout is user-specified, not auto-inferred from the RF module
  — intent system doesn't yet generate keepout zones
- Connector warnings about distance to board edge appear even when
  connectors are edge-anchored

## File Layout

```
tests/unit_tests/
  layout_case_helpers.py              # Mock primitives + COMMON_BBOXES + CaseScoreSummary
  test_layout_benchmark_cases.py      # 5 cases, 41 invariant tests
  test_layout_benchmark_diagnostics.py # 33 diagnostic / threshold tests
docs/
  layout_benchmark_corpus.md          # This file
```

## Design Principles

1. **No KiCad dependency** — mock `_Part`/`_Net`/`_Circuit` with just the
   attributes the layout engine reads (`ref`, `footprint`, `value`, `name`,
   `pins`, `pins[n].net`).

2. **Invariant assertions, not coordinate snapshots** — tests check "all parts
   placed", "no outline violations", "decaps within N mm of parent IC",
   "intent kind X inferred". This survives placer algorithm changes.

3. **Broad score floors, not exact targets** — score floors are deliberately
   low (15–20) to catch regressions where the placer produces garbage.
   Raise them as the placer improves.

4. **Realistic board topologies** — each case models a real PCB archetype
   (USB peripheral, sensor array, battery-powered, UI panel, RF module)
   with realistic footprint sizes and net connectivity.

5. **Separate from implementation** — all files live in `tests/` and `docs/`.
   No changes to `src/skidl/layout/` needed.

## Adding a New Case

1. Add a `_my_board()` function to `test_layout_benchmark_cases.py` that
   returns a `_Circuit` using helpers from `layout_case_helpers.py`.
2. Add a `TestMyBoard` class with invariant tests.
3. Add a fixture in `test_layout_benchmark_diagnostics.py` and threshold
   tests.
4. Document the case in this file.
5. Run `pytest -k layout_benchmark -v` to verify.
