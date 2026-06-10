# Run Report: runs_v1

**143 records**


## Status Summary

- **failed**: 50 (35%)
- **timeout**: 43 (30%)
- **succeeded_with_warnings**: 34 (24%)
- **succeeded**: 16 (11%)

**Overall success rate: 35%**

## Per-Mode Breakdown

### engine_only (68 boards)
- Success: 12/68 (18%)
- Wall time: p50=2.4s, p90=4.8s, max=4.8s
- Correction iterations: avg=3.4, max=8
### external (10 boards)
- Success: 7/10 (70%)
- Cost: $0.0140 total, $0.0014/board avg
  - p50=$0.0014, p90=$0.0018, p99=$0.0018
- Correction iterations: avg=2.9, max=4
### internal (65 boards)
- Success: 31/65 (48%)
- Cost: $0.1499 total, $0.0023/board avg
  - p50=$0.0021, p90=$0.0038, p99=$0.0064
- Correction iterations: avg=5.0, max=8

## Failure Taxonomy

- **43x** per-board timeout exceeded (300.0s)
- **22x** correction loop hit max_iters=8
- **15x** no cached spec_path for engine-only run
- **3x** correction loop hit max_iters=4
- **2x** 1 placement overlap(s): R1/R2
- **1x** placed parts overlap: R1 and R2
- **1x** KiCad symbol library 'Bosch' does not exist
- **1x** footprint 'Package_DIP:Rotary_Encoder_5mm_P1.27mm' does not exist on disk
- **1x** footprint 'Display_TFT:TFT_3.2inch_320x240' does not exist on disk; footprint 'S
- **1x** KiCad symbol library 'FTDI' does not exist
- **1x** footprint 'Display_TFT:IPS_240x135_1.54inch' does not exist on disk
- **1x** footprint 'Package_SMD:RV0603T10K' does not exist on disk
- **1x** KiCad symbol library 'IC_IR2103' does not exist

### Exception codes in failed runs
- LAYOUT_OVERLAP: 305
- LONG_POWER_NET: 171
- LAYOUT_OUTLINE_VIOLATION: 71
- HIGH_CONGESTION: 55
- SPEC_UNKNOWN_PIN: 35
- SPEC_BAD_FOOTPRINT: 5
- SPEC_UNKNOWN_LIB: 3

## By Difficulty Axis

- ****: 0/3 (0%)
- **analog_mixed**: 20/33 (61%)
- **digital**: 30/101 (30%)
- **high_complexity**: 0/4 (0%)
- **high_power**: 0/2 (0%)

## By Tier

- **Tier 0**: 0/3 (0%)
- **Tier 1**: 28/53 (53%)
- **Tier 2**: 16/39 (41%)
- **Tier 3**: 4/30 (13%)
- **Tier 4**: 2/18 (11%)

## Hardest Failures (most iterations, still failed)

- **bmp180-barometer**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **lsm303-compass-accelerometer**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **trinket**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **ds3231-rtc**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **max31865-rtd-amplifier**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **4-channel-level-shifter**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **feather-m0-basic-proto**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **vs1053**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **circuit-playground-express**: 8 iters, status=failed, reason=correction loop hit max_iters=8
- **feather-esp32-s3**: 8 iters, status=failed, reason=correction loop hit max_iters=8

## LLM Rescue (succeeded after corrections)

**43 boards rescued by correction loop**

- **max31856-thermocouple**: 8 iters, wall=0.0s, cost=$0.0024
- **bme680-air-quality**: 7 iters, wall=0.0s, cost=$0.0014
- **qt-py-samd21**: 7 iters, wall=0.0s, cost=$0.0028
- **max31865-rtd-amplifier**: 6 iters, wall=0.0s, cost=$0.0032
- **ref-stm32-bluepill-kicad-stm32-bluepill**: 6 iters, wall=0.0s, cost=$0.0015
- **si5351a**: 5 iters, wall=0.0s, cost=$0.0064
- **ref-kicad-arduino-boards-arduino-uno-r4-minima**: 5 iters, wall=0.0s, cost=$0.0022
- **ads1115-adc**: 4 iters, wall=0.0s, cost=$0.0000
- **max31856-thermocouple**: 4 iters, wall=0.0s, cost=$0.0000
- **feather-nrf52840-sense**: 4 iters, wall=0.0s, cost=$0.0042
