# Layout Intent System — New Inference Tasks

Two features for `src/skidl/layout/intent.py` and the placer. Both are inferrable from the existing netlist/circuit topology -- no user hints needed.

Run `pytest tests/unit_tests/test_layout_*.py` after each.

---

## Task 1: RF path clustering and antenna edge anchoring

**Files:** `src/skidl/layout/intent.py`, `src/skidl/layout/placer.py`, `tests/unit_tests/test_layout_intent.py`

### Problem

Coaxial/SMA antenna connectors (`Conn_Coaxial`, SMA footprints) are not recognized by the intent system. They end up mid-board with long RF traces to the radio IC. In a real board, the antenna connector must be on a board edge and the RF IC must be adjacent (<10mm trace).

Discovered on the mycelium-radio project: SMA connector placed 26mm from Si4684, mid-board. Crystal was 11mm from the IC (should be <5mm).

### What to infer (from netlist topology, not user hints)

1. **Detect antenna connectors**: Match `Conn_Coaxial` symbol name, or footprint containing `SMA`, `U.FL`, `IPEX`, `coax`. Add a new regex:
   ```python
   COAX_RE = re.compile(r"\b(coax|coaxial|sma|u\.?fl|ipex|antenna|rf.?conn)\b", re.I)
   ```

2. **Trace the RF signal path**: From the antenna connector's signal pin, follow the net to find the IC it connects to. That IC is the "RF IC". The net name often contains `ANT`, `RF`, `VHF`, `UHF`, but net-tracing is more robust than name matching.

3. **Emit constraints**:
   - `EdgeAnchor(ref=antenna_ref, edge="top")` -- antenna connectors go on a board edge (default top, since antennas typically protrude upward)
   - `FaceEdgeConstraint(ref=antenna_ref, edge="top")`
   - `NearConstraint(ref=rf_ic_ref, target_ref=antenna_ref, distance_mm=8.0)` -- RF IC close to antenna
   - For any crystal connected to the RF IC's XTAL/OSC pins: `NearConstraint(ref=xtal_ref, target_ref=rf_ic_ref, distance_mm=4.0)`

4. **Analog separation**: If a DAC, audio amp, or codec IC is present (detect from symbol name or I2S/audio net connections), emit `FarConstraint(ref=audio_ic_ref, target_ref=rf_ic_ref, distance_mm=15.0)` to reduce RF-to-analog coupling.

### How to detect the RF IC from the antenna connector

```python
def _find_rf_ic(antenna_part, circuit):
    """Follow the signal net from an antenna connector to find the RF IC."""
    signal_pin = next((p for p in antenna_part.pins if p.name in ("In", "Signal", "1")), None)
    if signal_pin is None:
        return None
    net = signal_pin.net
    if net is None:
        return None
    # Find the IC (non-passive, non-connector) on this net
    for pin in net.pins:
        part = pin.part
        if part is antenna_part:
            continue
        ref = str(getattr(part, "ref", ""))
        if ref.startswith("U"):  # IC
            return part
    return None
```

### How to find the RF IC's crystal

Follow nets from pins whose names match `XTAL`, `OSC`, `XTALI`, `XTALO`, `XIN`, `XOUT`. The part on the other end with a `Crystal` symbol or footprint is the crystal.

### Placer changes

The placer already handles `EdgeAnchor` (Layer 2) and `NearConstraint` exists in `LayoutConstraints` but may not be applied. Verify that `NearConstraint` and `FarConstraint` are consumed during placement:
- In Layer 3 (primary parts), if a part has a `NearConstraint`, use the target's placed position as the initial candidate instead of the group anchor.
- `FarConstraint`: after tentative placement, if distance to target is below threshold, shift away along the axis between them.

### Tests

```python
def test_coaxial_gets_edge_anchor():
    """Conn_Coaxial should be inferred as edge-anchored."""
    # Build minimal circuit: Conn_Coaxial -> net -> QFN IC
    # Assert EdgeAnchor emitted for the coaxial ref

def test_rf_ic_near_antenna():
    """RF IC should get NearConstraint to antenna connector."""
    # Assert NearConstraint(rf_ic, antenna, ~8mm)

def test_crystal_near_rf_ic():
    """Crystal on RF IC's XTAL pins should get NearConstraint."""
    # Assert NearConstraint(xtal, rf_ic, ~4mm)

def test_audio_ic_far_from_rf():
    """DAC/codec should get FarConstraint from RF IC."""
    # Assert FarConstraint(dac, rf_ic, ~15mm)
```

---

## Task 2: Co-locate user-facing parts (display + controls)

**Files:** `src/skidl/layout/intent.py`, `tests/unit_tests/test_layout_intent.py`

### Problem

Display connectors (FPC/FFC) and user input parts (nav switches, buttons, encoders) are placed independently. In physical products, the display and controls must be on the same face of the enclosure -- the user looks at the screen and operates controls without flipping the device. Currently the e-ink FPC can end up on the bottom edge while the nav switch lands on the left, which makes no physical sense.

### What to infer

1. **Detect display parts**: Already handled by `DISPLAY_RE` and `FFC_RE`. The existing `_mating_intent_for_part` assigns `edge_preference="top"` for displays and `edge_preference="bottom"` for FFC connectors. The FFC case is wrong when the FFC *is* the display connector -- an FPC going to an e-ink/OLED/LCD should get `edge_preference="top"`, not `"bottom"`.

2. **Fix FFC display detection**: When an FFC/FPC connector's nets include SPI signals (MOSI, CLK, CS, DC, BUSY) or display-related net names, it's a display FPC, not a generic ribbon cable.
   ```python
   DISPLAY_NET_RE = re.compile(r"\b(eink|e.ink|oled|lcd|disp|tft|epd|dc|busy)\b", re.I)
   ```
   In `_mating_intent_for_part`, when matching FFC_RE, also check if any connected net names match DISPLAY_NET_RE. If so, treat as display not generic FFC.

   **Key physical insight**: the FPC connector sits on the back of the display panel, so the cable exits *away* from the viewing face. If the user views the display from the top edge, the FPC connector is near the top edge but the cable folds back behind the display. The connector's `mating_side` should be `"cable_exit"` (not `"visible_face"`), and its edge should match the display viewing edge (default `"top"`) because the connector physically mounts on the same edge as the display -- it just faces backward. Don't invert the edge.

3. **Group display + controls on same edge**: After all individual mating intents are computed, add a post-pass in `infer_placement_intents`:
   ```python
   def _colocate_display_and_controls(plan, outline):
       """Ensure display and user-control parts share the same board edge."""
       display_refs = [m.ref for m in plan.mating_intents if m.kind in ("display", "ffc") and "display" in " ".join(m.reasons).lower()]
       control_refs = [m.ref for m in plan.mating_intents if m.kind in ("button", "encoder", "pot")]
       
       if not display_refs or not control_refs:
           return
       
       # Use the display's edge as the authority
       display_edge = next((m.edge_preference for m in plan.mating_intents if m.ref == display_refs[0]), "top")
       
       # Move controls to the same edge
       for ref in control_refs:
           for ea in plan.edge_anchors:
               if ea.ref == ref:
                   ea.edge = display_edge
           for fe in plan.face_edges:
               if fe.ref == ref:
                   fe.edge = display_edge
   ```

4. **AlignConstraint**: Emit `AlignConstraint(refs=[display_ref, *control_refs], axis="y")` (for top/bottom edges) or `axis="x"` (for left/right edges) to keep them on the same line along the edge.

### Edge case: nav switches

The `BUTTON_RE` regex matches "switch" which will catch nav switches like the JS1300. But the JS1300's metadata might say "navigation" not "button". Add to BUTTON_RE:
```python
BUTTON_RE = re.compile(r"\b(button|pushbutton|tact|switch|nav|joystick|d-pad|dpad)\b", re.I)
```

Or better, add a dedicated regex:
```python
NAV_RE = re.compile(r"\b(nav|joystick|d-pad|dpad|5.?way|4.?way)\b", re.I)
```
And treat NAV matches the same as buttons in `_mating_intent_for_part` (kind="nav_control", edge_preference="right", mating_side="user_control").

### Tests

```python
def test_display_fpc_gets_top_edge():
    """FPC connector with display nets should get top edge, not bottom."""
    # Build: FPC connector with nets named EINK_CS, EINK_DC, etc.
    # Assert edge_preference == "top", not "bottom"

def test_display_and_navswitch_same_edge():
    """Display FPC and nav switch should be co-located on same edge."""
    # Build: FPC display + nav switch part
    # Assert both have same edge in edge_anchors

def test_controls_follow_display_edge():
    """When display is top-edge, buttons/encoders should also be top-edge."""
    # Assert AlignConstraint emitted with both refs

def test_nav_switch_detected_as_control():
    """JS1300-style nav switch should match as user control."""
    # Part with "nav" or "joystick" in metadata -> kind="nav_control"
```

---

## Notes for implementation

- Both tasks only modify the **intent inference** (`intent.py`) and add test coverage. The constraint types (`NearConstraint`, `FarConstraint`, `AlignConstraint`) already exist in `constraints.py`.
- The placer (`placer.py`) may need updates to consume `NearConstraint` and `FarConstraint` if it doesn't already -- check Layer 3 placement logic.
- The `_part_text()` helper in intent.py concatenates ref, value, footprint, and description into a single string for regex matching. Custom SKIDL-tool parts may have sparse metadata, so net-name-based detection (for RF path, display nets) is more robust than part-text matching.
- Test circuits can use `Part(name=..., tool=SKIDL, pins=[...])` inline parts to avoid needing KiCad libraries in CI.
