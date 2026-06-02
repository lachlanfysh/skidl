Following up on my last comment — the net-label orientation I corrected above is now fixed and test-covered, I've closed out the rest of what you flagged, and I tightened up the label placement on top. Re-ran your own two test cases.

**Your 8-transistor rotation/mirror test:**

![Transistor rotation/mirror test — net labels face away from the body in every orientation incl. vertical/mirrored, and sit on their pins with no stray wires](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/transistor-onpin.png)

Labels face away from the body in every rotation and mirror **and** sit on their pins — no stray cross-sheet wires. (Two things were wrong: the orientation, and stub-net labels being dragged off-pin — both fixed, see below.)

**`esp32_audio_board` (MCU region):**

![esp32_audio_board MCU region — U3 renders fully, decoupling caps fan to VCC, EN pull-up + RC cap tee cleanly off EN, labels face away](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/esp32-wide.png)

`U3` renders fully (missing/unknown components were power-symbol defs not emitted from the flattened-sheet instances — fixed), decoupling caps fan to VCC, the EN pull-up + RC cap tee cleanly off the EN pin, labels face away.

Each finding → its own isolated commit:

| Finding | Fix |
|---|---|
| net-label orientation (both branches) — `D`/`U` swapped in `orient_map` (`4bd527dc` flipped the test-correct `b3728d27` values) | `d9c9b55b` (derive `(angle, justify)` from one table keyed on `calc_pin_dir`) + regression test `41c5649a` |
| stub-net labels dragged off their pins with long connecting wires | `59f892a9` — `deconflict_labels` was wire-relocating stub labels off a *neighbour's* body; since a stub net is connected by **name**, skip it in deconfliction (builds on your `2c7935b1` own-body guard) |
| missing/unknown components (esp32) | `33afc28d` (emit power-symbol defs from emitted instances) |
| wire remnants after stubs | `67545e2a` (dangling-wire purge) |
| wires over the VBUS power symbol | `82e64115` (drop power-bus segments crossing a part body) |
| power-symbol `angle=0` | `e080dd73` (shared angle table) |

Two smaller placement bits: **EN-pin teeing** (`846d8999` — a single IC pin's fan now staggers, so the EN pull-up + RC cap tee cleanly instead of the cap landing on the body), and an opt-in **`grid_blocks`** shelf-pack for spacing independent groups apart on dense sheets (`generate_schematic(grid_blocks=True)`, no-op on IC-fan sheets) — happy to demo on a design of yours if it's a direction you'd want.

On delivery: rather than my whole branch (it diverged from your `development`), I'd start with one small PR — the net-label orientation fix + its regression test — onto `development`, since that's the one you said you'd need to do anyway. If it lands cleanly I'll follow with the deconflict, power-symbol and snap/wire fixes grouped. Sound right, or take them another way?
