Following up on my last comment — the net-label orientation I corrected above is now fixed and test-covered, and I've closed out the rest of what you flagged. I re-ran your own two test cases on my branch:

**Your transistor rotation/mirror test:**

![Transistor rotation/mirror test — net labels face away from the body in every orientation incl. vertical/mirrored](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/transistor-orientation.png)

Labels face away from the body in every rotation and mirror, including the vertical pins. The cause was the `D`/`U` swap in `orient_map` — `4bd527dc` flipped the earlier, test-correct values from `b3728d27`, so up/down-pin labels pointed inward. Fixed by deriving `(angle, justify)` from one table keyed on `calc_pin_dir`, plus a render-free regression test that *fails* on the swap so it can't silently regress. So the `development` net-label issue you said you'd need to fix — that's done.

**esp32_audio_board (MCU sheet):**

![esp32_audio_board MCU region — U3 renders fully, EN pull-up + RC cap tee cleanly off the EN pin, decaps fan to VCC](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/esp32-en.png)

`U3` renders fully — the missing/unknown components were power-symbol defs not being emitted from the flattened-sheet instances; fixed. Decaps fan to VCC.

The rest of your list, each as its own isolated commit:

| Your finding | Fix |
|---|---|
| stub-label orientation | same orientation fix |
| missing/unknown components (esp32) | `33afc28d` (emit power-symbol defs from emitted instances) |
| wire remnants after stubs | `67545e2a` (dangling-wire purge) |
| wires over the VBUS power symbol | `82e64115` (drop power-bus segments crossing a part body) |
| power-symbol `angle=0` (the one I flagged) | `e080dd73` (shared angle table) |

On the placement front you flagged as the deeper issue, two things:

**EN-pin teeing** (`846d8999`): the snap now staggers a *single* IC pin's fan, so on `esp32_audio_board` the EN pull-up + RC-reset cap tee cleanly off the EN pin (visible on the render above) instead of the cap landing on the U3 body — same mechanism as a switch-input fan, generalised to the one-pin case.

**`grid_blocks`** (opt-in): a shelf-pack of the wired-connected groups for sheets where independent units scatter/overlap — `generate_schematic(grid_blocks=True)`, and a **no-op on IC-fan sheets** so it's low-risk. It doesn't touch shared-net cases (your transistor test shares `ENET`/`BNET`/`CNET` across all 8, so that's an inherent routing tangle, not a placement one — orientation reads clean there even though the shared-net wiring doesn't). Happy to show it on one of your example designs if it's a direction you'd want — possible candidate for a default.

On delivery: rather than my whole branch (it diverged from your `development`), I'd start with **one small PR — the net-label orientation fix + its regression test — onto `development`**, since that's the one you said you'd need to do anyway. If that lands cleanly I'll follow with the power-symbol and snap/wire fixes grouped by concern. Does that split work, or would you rather take them another way?
