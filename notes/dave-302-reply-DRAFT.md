Following up on my last comment — the net-label orientation I corrected above is now fixed and test-covered, and I've closed out the rest of what you flagged. I re-ran your own two test cases on my branch:

**Your transistor rotation/mirror test:**

[transistor render]

Labels face away from the body in every rotation and mirror, including the vertical pins. The cause was the `D`/`U` swap in `orient_map` — `4bd527dc` flipped the earlier, test-correct values from `b3728d27`, so up/down-pin labels pointed inward. Fixed by deriving `(angle, justify)` from one table keyed on `calc_pin_dir`, plus a render-free regression test that *fails* on the swap so it can't silently regress. So the `development` net-label issue you said you'd need to fix — that's done.

**esp32_audio_board (MCU sheet):**

[esp32 render]

`U3` renders fully — the missing/unknown components were power-symbol defs not being emitted from the flattened-sheet instances; fixed. Decaps fan to VCC.

The rest of your list, each as its own isolated commit:

| Your finding | Fix |
|---|---|
| stub-label orientation | same orientation fix |
| missing/unknown components (esp32) | `33afc28d` (emit power-symbol defs from emitted instances) |
| wire remnants after stubs | `67545e2a` (dangling-wire purge) |
| wires over the VBUS power symbol | `82e64115` (drop power-bus segments crossing a part body) |
| power-symbol `angle=0` (the one I flagged) | `e080dd73` (shared angle table) |

On the placement front you flagged as the deeper issue last time, two concrete moves:

**EN-pin teeing** (`846d8999`): the snap now staggers a *single* IC pin's fan, so an MCU EN pull-up + RC-reset cap tee cleanly off the pin instead of the cap landing on the IC body — same mechanism as the switch-input fans, generalised to the one-pin case. (Visible on the esp32 render above.)

**`grid_blocks`** — an opt-in shelf-pack of the wired-connected groups, for the scatter where independent units land mashed/overlapping:

[grid_blocks before/after render]

On MR1's pots sheet the previously-mashed RV4 went 11.6 mm → 34.8 mm clear. It's deliberately scoped: a **no-op on IC-fan sheets**, and it doesn't touch shared-net stress cases (e.g. your transistor test, where all 8 transistors share `ENET`/`BNET`/`CNET` — that's an inherent routing tangle, not a placement one, which is why orientation reads clean there but the wiring doesn't). So it's a targeted win, not a universal placer. It's reachable today via `generate_schematic(grid_blocks=True)` — worth enabling by default?

On delivery: rather than my whole branch (it started from the v3 base and has since diverged from your `development`), I'd rather send each fix as a standalone patch you apply onto `development` however fits your refactor — orientation + its regression test first, since that's the one you were about to tackle yourself. Small separate PRs against `development`, or patches another way?
