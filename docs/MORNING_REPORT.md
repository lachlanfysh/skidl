# Run Report: runs_a

**173 records**


## Status Summary

- **failed**: 125 (72%)
- **succeeded**: 25 (14%)
- **timeout**: 15 (9%)
- **crashed**: 6 (3%)
- **succeeded_with_warnings**: 2 (1%)

**Overall success rate: 16%**

## Per-Mode Breakdown

### engine_only (173 boards)
- Success: 27/173 (16%)
- Correction iterations: avg=2.0, max=7

## Failure Taxonomy

- **15x** per-board timeout exceeded (300.0s)
- **10x** no cached spec_path for engine-only run
- **10x** correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVERLAP repeated 4 time
- **6x** KeyError: 'pop from an empty set'
- **6x** footprint 'FreeModular:THONKICONN' does not exist on disk
- **5x** footprint 'Connector_Thonk:ThonkiconnJack' does not exist on disk
- **4x** footprint 'pedals:Pot_Underside' does not exist on disk
- **4x** footprint 'pedals:Pot_Underside' does not exist on disk; footprint 'pedals:SWITC
- **4x** footprint 'PCM_marbastlib-choc:LED_choc_6028R' does not exist on disk; footprint
- **3x** footprint 'Potentiometer_Thonk:AlphaPot9ShaftD' does not exist on disk
- **3x** footprint 'Switch:MTS-102_MTS-103_MTS-112_MTS-113_MTS-123' does not exist on dis
- **3x** footprint 'winterbloom:AudioJack_WQP518MA' does not exist on disk; footprint 'wi
- **3x** footprint 'KiCad-SSD1306-0.91-OLED-4pin-128x32.pretty-master:SSD1306-0.91-OLED-4
- **2x** footprint 'local:PEC11R-4220F-N0024' does not exist on disk
- **2x** footprint 'local:PEC11R-4220F-S0024' does not exist on disk

### Exception codes in failed runs
- SPEC_BAD_FOOTPRINT: 446
- SPEC_UNKNOWN_PIN: 68
- LAYOUT_OVERLAP: 24
- LONG_POWER_NET: 21
- LAYOUT_OUTLINE_VIOLATION: 19
- HIGH_CONGESTION: 17
- ENGINE_CRASH: 6

## By Difficulty Axis

- **digital**: 27/173 (16%)

## By Tier

- **Tier 1**: 27/173 (16%)

## Hardest Failures (most iterations, still failed)

- **ref-buffered-multiple-smd-main**: 7 iters, status=failed, reason=correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVE
- **ref-envelope-follower-main**: 7 iters, status=failed, reason=correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVE
- **ref-rectifier-main**: 7 iters, status=failed, reason=correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVE
- **ref-envelope-01-mutronv**: 6 iters, status=failed, reason=correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVE
- **ref-hagiwo-sync-lfo-main**: 6 iters, status=timeout, reason=per-board timeout exceeded (300.0s)
- **ref-mixer**: 6 iters, status=failed, reason=correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVE
- **ref-white-noise**: 6 iters, status=failed, reason=correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVE
- **ref-mult**: 5 iters, status=timeout, reason=per-board timeout exceeded (300.0s)
- **ref-slimline-clock-divider-smd-main**: 5 iters, status=timeout, reason=per-board timeout exceeded (300.0s)
- **ref-stomp-chargepump**: 5 iters, status=failed, reason=correction loop stalled: LAYOUT_OUTLINE_VIOLATION|LAYOUT_OVE

## LLM Rescue (succeeded after corrections)

**8 boards rescued by correction loop**

- **ref-ck-d6r-black-manual**: 1 iters, wall=0.0s, cost=$0.0000
- **ref-ck-d6r-black-wire**: 1 iters, wall=0.0s, cost=$0.0000
- **ref-dailywell-2ms1-manual**: 1 iters, wall=0.0s, cost=$0.0000
- **ref-dailywell-2ms1-wire**: 1 iters, wall=0.0s, cost=$0.0000
- **ref-dailywell-2ms3-manual**: 1 iters, wall=0.0s, cost=$0.0000
- **ref-dailywell-2ms3-wire**: 1 iters, wall=0.0s, cost=$0.0000
- **ref-er-display-spi-128x64-manual**: 1 iters, wall=0.0s, cost=$0.0000
- **ref-tiliqua-panel**: 1 iters, wall=0.0s, cost=$0.0000
