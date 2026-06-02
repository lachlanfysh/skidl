Following up on my last comment — the net-label orientation I corrected above is now fixed and test-covered, and I've closed out the rest of what you flagged. Re-ran your test cases; here's `esp32_audio_board` (MCU region):

![esp32_audio_board MCU region — U3 renders fully, EN pull-up + RC cap tee cleanly off EN, decaps fan to VCC, every net label faces away from the body](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/esp32-wide.png)

`U3` renders fully (the missing/unknown components were power-symbol defs not being emitted from the flattened-sheet instances — fixed), the decoupling caps fan to VCC, the EN pull-up + RC cap tee cleanly off the EN pin, and every net label faces *away* from the body. Your 8-transistor rotation/mirror test passes the same way (labels correct in every rotation and mirror — shown in the grid_blocks comparison below).

Orientation root cause: the `D`/`U` values in `orient_map` were swapped — `4bd527dc` flipped the earlier, test-correct values from `b3728d27`, so up/down-pin labels pointed inward. Fixed by deriving `(angle, justify)` from one table keyed on `calc_pin_dir`, plus a render-free regression test that *fails* on the swap so it can't silently regress.

The rest of your list, each as its own isolated commit:

| Your finding | Fix |
|---|---|
| net-label orientation (both branches) | `d9c9b55b` + regression test `41c5649a` |
| stub-label orientation | same fix |
| missing/unknown components (esp32) | `33afc28d` (emit power-symbol defs from emitted instances) |
| wire remnants after stubs | `67545e2a` (dangling-wire purge) |
| wires over the VBUS power symbol | `82e64115` (drop power-bus segments crossing a part body) |
| power-symbol `angle=0` | `e080dd73` (shared angle table) |

On placement, two things:

**EN-pin teeing** (`846d8999`): the snap now staggers a single IC pin's fan, so the EN pull-up + RC cap tee cleanly off the pin (visible above) rather than the cap landing on the IC body.

**`grid_blocks`** — an opt-in shelf-pack of the wired-connected groups. On your transistor test, off vs on:

![Transistor test, grid_blocks off vs on — labels face away in both (orientation is independent); grid_blocks just orders the parts, the cross-wires are the shared ENET/BNET/CNET in both](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/transistor-grid-beforeafter.png)

Labels face away in both (orientation is independent of this) — grid_blocks just *orders* the parts; the cross-wires are your shared `ENET`/`BNET`/`CNET`, the same in both. It's a no-op on IC-fan sheets and reachable via `generate_schematic(grid_blocks=True)`. I think it's a reasonable default for tidying final placement — happy to run it on other designs of yours.

On delivery: rather than my whole branch (it diverged from your `development`), I'd start with one small PR — the net-label orientation fix + its regression test — onto `development`, since that's the one you said you'd need to do anyway. If that lands cleanly I'll follow with the power-symbol and snap/wire fixes grouped. Sound right, or take them another way?
