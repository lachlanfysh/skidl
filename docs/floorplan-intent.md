# Code-Mode Floorplan Intent

Use the existing `EDA_FLOORPLAN` global in submitted SKiDL code when a human,
reference PCB, enclosure, panel, sensor lattice, or module outline provides
mechanical intent. Define it after refs exist and submit the code with
`submit_skidl_code()`.

Compact agent-facing example:

```python
EDA_FLOORPLAN = {
    "outline": {"width_mm": 120, "height_mm": 180, "corner_radius_mm": 2},
    "grid": {
        "refs": ["U_S00", "U_S01", "U_S10", "U_S11"],
        "rows": 2,
        "cols": 2,
        "x_mm": 18,
        "y_mm": 36,
        "dx_mm": 22,
        "dy_mm": 24,
        "side": "front",
    },
    "fixed_positions": [
        {"ref": "U_MCU", "x_mm": 60, "y_mm": 155, "side": "back"},
        {"ref": "H1", "x_mm": 8, "y_mm": 8},
    ],
    "edge_anchors": [
        {"ref": "J_USB", "edge": "bottom", "offset_mm": 60, "side": "front"}
    ],
    "align": [{"refs": ["LED1", "LED2", "LED3"], "axis": "y"}],
    "distribute": [{"refs": ["LED1", "LED2", "LED3"], "axis": "x"}],
    "assembly_sides": {"U_REG": "back", "J_BAT": "back"},
    "keepouts": [{"x_min": 0, "y_min": 0, "x_max": 120, "y_max": 8}],
}
```

`grid` expands into fixed positions plus row/column align and distribute
constraints when origin and pitch are supplied. Use `fixed_positions` for exact
mechanical coordinates from real floorplans such as 45lux sensor lattices,
MR-1 controls, displays, mounting holes, batteries, and daughterboards. Use
`edge_anchors` for cable-facing connectors whose exact origin should remain
footprint-aware. Use `assembly_sides` or per-entry `side` for front/back policy.

`cutouts`, `apertures`, and `slots` are accepted only as intent metadata today;
they are not yet emitted as internal `Edge.Cuts` geometry. Keep them in the
floorplan so later cutout support can preserve the original mechanical record.
