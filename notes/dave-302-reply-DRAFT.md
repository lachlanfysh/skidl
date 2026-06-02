Good timing — I was just prepping the message below when your question came through.

Mostly yes — and it maps onto the refactor. The label and wire *decisions* are now tool-agnostic in `schematics/decisions.py`, the snap geometry in `schematics/snap.py`, so the two-pin snapping places cleanly with no overlaps. The one piece that stays backend-side is the label's angle and justify — that's how KiCad justifies the text in the s-expr, not a placement call — and that's where the worst of it came from: the vertical labels' justify was pushing them back over their own body. Fixed in the kicad9 emit. It's done and running on your own two test cases:

**Your 8-transistor rotation/mirror test:**

![Transistor rotation/mirror test — net labels face away from the body in every orientation incl. vertical/mirrored, and sit on their pins with no stray wires](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/transistor-onpin.png)

Labels face away from the body in every rotation and mirror, sit on their pins, no stray cross-sheet wires.

**`esp32_audio_board` (MCU region):**

![esp32_audio_board MCU region — U3 renders fully, decoupling caps fan to VCC, EN pull-up + RC cap tee cleanly off EN, labels face away](https://raw.githubusercontent.com/lachlanfysh/skidl/feat/label-clearance-placement/notes/renders/esp32-wide.png)

U3 renders fully, the decoupling caps fan to VCC, and the EN pull-up and reset cap tee off the EN pin cleanly.

The small fixes are below, one per commit. The bigger structural change — pulling the snap/label/wire work out of the backend into tool-agnostic decisions in `schematics/`, behind a thin interface — is laid out in the architecture doc I linked last time: [ARCHITECTURE-snap-backend-split.md](https://github.com/lachlanfysh/skidl/blob/docs/snap-backend-split/ARCHITECTURE-snap-backend-split.md). That's the part I'd most want your read on before I open anything.

I've held off opening any PRs so you can get your head around it at your own pace — but it's all ready as isolated commits against `development`, so just say the word and how you'd like them grouped.

---

Each fix is its own commit, if it helps to see them mapped to what you flagged:

| Finding | Fix |
|---|---|
| net-label orientation (both branches) — `D`/`U` swapped in `orient_map` (`4bd527dc` flipped the test-correct `b3728d27` values) | `d9c9b55b` (derive `(angle, justify)` from one table keyed on `calc_pin_dir`) + regression test `41c5649a` |
| stub-net labels dragged off their pins with long connecting wires | `59f892a9` — `deconflict_labels` was wire-relocating stub labels off a *neighbour's* body; since a stub net is connected by **name**, skip it in deconfliction (builds on your `2c7935b1` own-body guard) |
| missing/unknown components (esp32) | `33afc28d` (emit power-symbol defs from emitted instances) |
| wire remnants after stubs | `67545e2a` (dangling-wire purge) |
| wires over the VBUS power symbol | `82e64115` (drop power-bus segments crossing a part body) |
| power-symbol `angle=0` | `e080dd73` (shared angle table) |

Two additive placement bits beyond your findings:

| Feature | Commit |
|---|---|
| single-pin fan teeing — a pull-up + RC cap on one IC pin tee off cleanly instead of the cap landing on the body (**shown in the esp32 render**, the EN pin) | `846d8999` (one IC pin with ≥2 two-pin parts now staggers; any pin, not just EN) |
| spacing independent groups apart on dense sheets — opt-in (**not used in either render above**) | `5cbc486e` (`grid_blocks=True`, size-aware shelf-pack; default off) |

On `grid_blocks` — that overlaps the inter-group placement @rhaingenix is working in #306. I flagged the idea over there; what I landed on is a good deal simpler — it leans on SKiDL's existing `place_blocks` rather than their primitives — but credit to #306 for working the same ground. Happy to converge the two rather than ship competing versions — whichever shape you'd rather have in SKiDL.
