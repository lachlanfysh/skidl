# Layout Engine — Outline & Validation Tasks

Three self-contained tasks for Codex. Each task lists the files to modify, what to do, and tests to add. Run `pytest tests/unit_tests/test_layout_*.py` after each.

---

## Task 1: Outline violation checking in validator

**Files:** `src/skidl/layout/validator.py`, `tests/unit_tests/test_layout_validator.py`

### What to do

Add a new field `outline_violations` to `ValidationResult` and a checker function `_check_outline_violations`. A part violates the outline if any edge of its bounding box extends beyond `[0, 0] → [outline.width_mm, outline.height_mm]`.

**In `validator.py`:**

1. Add to `ValidationResult`:
   ```python
   outline_violations: list[str] = field(default_factory=list)  # refs outside outline
   ```

2. Update `ValidationResult.ok` to also fail on outline violations:
   ```python
   @property
   def ok(self) -> bool:
       return not self.overlaps and not self.missing_refs and not self.outline_violations
   ```

3. Update `summary()` to report outline violations (LOUD, same style as overlaps):
   ```python
   if self.outline_violations:
       lines.append(f"OUTSIDE OUTLINE ({len(self.outline_violations)}):")
       for ref in self.outline_violations[:20]:
           lines.append(f"  {ref}")
   ```

4. Add checker function:
   ```python
   def _check_outline_violations(
       placed: list[PlacedPart],
       fp_bboxes: dict[str, tuple[float, float]],
       outline,  # BoardOutline or None
   ) -> list[str]:
       if outline is None:
           return []
       violations = []
       for pp in placed:
           w, h = fp_bboxes.get(pp.footprint, (2.0, 2.0))
           if (pp.x_mm - w / 2 < 0 or pp.y_mm - h / 2 < 0 or
                   pp.x_mm + w / 2 > outline.width_mm or pp.y_mm + h / 2 > outline.height_mm):
               violations.append(pp.ref)
       return violations
   ```

5. Update `validate()` signature to accept an optional `outline` parameter:
   ```python
   def validate(
       placed_parts: list[PlacedPart],
       circuit,
       fp_bboxes: dict[str, tuple[float, float]],
       clearance_mm: float = 0.5,
       outline=None,  # BoardOutline or None
   ) -> ValidationResult:
   ```
   And call the checker:
   ```python
   result.outline_violations = _check_outline_violations(placed_parts, fp_bboxes, outline)
   ```

**Important:** The `outline` parameter must be optional with default `None`. All existing tests pass `validate(parts, circuit, bboxes)` without an outline — they must continue to work unchanged.

### Tests to add in `test_layout_validator.py`

Import `BoardOutline` from `skidl.layout.constraints`.

```python
def test_outline_violation_detected():
    outline = BoardOutline(50.0, 50.0)
    parts = [
        PlacedPart("R1", 25.0, 25.0, 0.0, "Resistor_SMD:R_0805"),  # inside
        PlacedPart("R2", 51.0, 25.0, 0.0, "Resistor_SMD:R_0805"),  # outside right
    ]
    result = validate(parts, None, BBOXES_0805, outline=outline)
    assert "R2" in result.outline_violations
    assert "R1" not in result.outline_violations
    assert result.ok is False


def test_outline_violation_negative():
    outline = BoardOutline(50.0, 50.0)
    parts = [PlacedPart("R1", -5.0, 25.0, 0.0, "Resistor_SMD:R_0805")]
    result = validate(parts, None, BBOXES_0805, outline=outline)
    assert "R1" in result.outline_violations


def test_no_outline_no_violations():
    parts = [PlacedPart("R1", 999.0, 999.0, 0.0, "Resistor_SMD:R_0805")]
    result = validate(parts, None, BBOXES_0805)
    assert result.outline_violations == []


def test_outline_violation_in_summary():
    result = ValidationResult(placed_parts=2, total_parts=2, outline_violations=["R2"])
    s = result.summary()
    assert "OUTSIDE OUTLINE" in s
    assert "R2" in s
```

---

## Task 2: Auto-derived outline when none provided

**Files:** `src/skidl/layout/placer.py`, `tests/unit_tests/test_layout_placer.py`

### What to do

Add a function `derive_outline` that computes a `BoardOutline` from a list of `PlacedPart` plus a margin. This is called *after* placement when no outline was provided, to suggest a board size.

**In `placer.py`:**

1. Import `BoardOutline` (already importing from `.constraints`):
   ```python
   from .constraints import LayoutConstraints, FixedPosition, KeepOut, BoardOutline
   ```

2. Add function after `place_parts`:
   ```python
   def derive_outline(
       placed_parts: list[PlacedPart],
       fp_bboxes: dict[str, tuple[float, float]],
       margin_mm: float = 3.0,
   ) -> BoardOutline:
       """Compute a BoardOutline that encloses all placed parts plus margin.

       Returns a minimal rectangle. Useful when no outline was provided upfront.
       """
       if not placed_parts:
           return BoardOutline(50.0, 50.0)

       x_min = float("inf")
       y_min = float("inf")
       x_max = float("-inf")
       y_max = float("-inf")
       for pp in placed_parts:
           w, h = fp_bboxes.get(pp.footprint, (2.0, 2.0))
           x_min = min(x_min, pp.x_mm - w / 2)
           y_min = min(y_min, pp.y_mm - h / 2)
           x_max = max(x_max, pp.x_mm + w / 2)
           y_max = max(y_max, pp.y_mm + h / 2)

       return BoardOutline(
           width_mm=x_max - x_min + 2 * margin_mm,
           height_mm=y_max - y_min + 2 * margin_mm,
       )
   ```

3. Export from `__init__.py` — add `derive_outline` to the placer imports:
   ```python
   from .placer import place_parts, derive_outline
   ```

### Tests to add in `test_layout_placer.py`

Import `derive_outline` and `BoardOutline`:
```python
from skidl.layout.placer import place_parts, _overlaps, derive_outline
from skidl.layout.constraints import LayoutConstraints, FixedPosition, BoardOutline
```

```python
def test_derive_outline_encloses_parts():
    parts = [
        PlacedPart("R1", 10.0, 20.0, 0.0, "Resistor_SMD:R_0805_2012Metric"),
        PlacedPart("R2", 50.0, 60.0, 0.0, "Resistor_SMD:R_0805_2012Metric"),
    ]
    outline = derive_outline(parts, _FP_BBOXES, margin_mm=5.0)
    assert isinstance(outline, BoardOutline)
    # R1 left edge at 10-1=9, R2 right edge at 50+1=51 → width = 42 + 2*5 = 52
    assert outline.width_mm >= 52.0
    # R1 top edge at 20-0.625=19.375, R2 bottom at 60+0.625=60.625 → h = 41.25 + 2*5 = 51.25
    assert outline.height_mm >= 51.0


def test_derive_outline_empty_fallback():
    outline = derive_outline([], _FP_BBOXES)
    assert outline.width_mm == 50.0
    assert outline.height_mm == 50.0


def test_derive_outline_single_part():
    parts = [PlacedPart("R1", 25.0, 25.0, 0.0, "Resistor_SMD:R_0805_2012Metric")]
    outline = derive_outline(parts, _FP_BBOXES, margin_mm=3.0)
    # Part bbox 2.0×1.25, plus 2*3mm margin
    assert outline.width_mm == pytest.approx(8.0, abs=0.1)
    assert outline.height_mm == pytest.approx(7.25, abs=0.1)
```

---

## Task 3: Polygon-ready outline dataclass

**Files:** `src/skidl/layout/constraints.py`, `tests/unit_tests/test_layout_constraints.py` (new file)

### What to do

Replace `BoardOutline(width_mm, height_mm)` with a polygon-aware version that still supports the simple `(width, height)` rectangle case. The key idea: store vertices as a list of `(x, y)` tuples, but keep `width_mm` and `height_mm` as computed properties for backward compatibility.

**In `constraints.py`:**

Replace the `BoardOutline` class:
```python
@dataclass
class BoardOutline:
    vertices: list[tuple[float, float]] = field(default_factory=list)

    def __init__(self, width_mm: float = 0.0, height_mm: float = 0.0,
                 vertices: list[tuple[float, float]] | None = None):
        if vertices is not None:
            self.vertices = list(vertices)
        elif width_mm > 0 and height_mm > 0:
            self.vertices = [
                (0.0, 0.0),
                (width_mm, 0.0),
                (width_mm, height_mm),
                (0.0, height_mm),
            ]
        else:
            self.vertices = []

    @property
    def width_mm(self) -> float:
        if not self.vertices:
            return 0.0
        xs = [v[0] for v in self.vertices]
        return max(xs) - min(xs)

    @property
    def height_mm(self) -> float:
        if not self.vertices:
            return 0.0
        ys = [v[1] for v in self.vertices]
        return max(ys) - min(ys)
```

**Critical backward compatibility:** Every existing use of `BoardOutline(100.0, 80.0)` and `outline.width_mm` / `outline.height_mm` must continue to work unchanged. The `derive_outline` function from Task 2 returns `BoardOutline(width_mm=..., height_mm=...)` — that must also still work.

### Tests — create `tests/unit_tests/test_layout_constraints.py`

```python
from __future__ import annotations

from skidl.layout.constraints import BoardOutline, FixedPosition, LayoutConstraints


def test_rectangle_shorthand():
    outline = BoardOutline(100.0, 80.0)
    assert outline.width_mm == 100.0
    assert outline.height_mm == 80.0
    assert len(outline.vertices) == 4


def test_rectangle_keyword_args():
    outline = BoardOutline(width_mm=100.0, height_mm=80.0)
    assert outline.width_mm == 100.0
    assert outline.height_mm == 80.0


def test_polygon_vertices():
    verts = [(0, 0), (100, 0), (100, 50), (50, 80), (0, 50)]
    outline = BoardOutline(vertices=verts)
    assert outline.width_mm == 100.0
    assert outline.height_mm == 80.0
    assert len(outline.vertices) == 5


def test_empty_outline():
    outline = BoardOutline()
    assert outline.width_mm == 0.0
    assert outline.height_mm == 0.0
    assert outline.vertices == []


def test_vertices_override_dimensions():
    """When vertices are provided, width_mm/height_mm positional args are ignored."""
    verts = [(0, 0), (200, 0), (200, 150), (0, 150)]
    outline = BoardOutline(50, 50, vertices=verts)
    assert outline.width_mm == 200.0
    assert outline.height_mm == 150.0
```

---

## Execution order

Tasks 1 and 2 are independent — can be done in parallel.
Task 3 depends on neither but changes the `BoardOutline` class, so do it last and re-run all 55+ tests to confirm backward compat.

## Validation

After all three tasks:
```bash
pytest tests/unit_tests/test_layout_*.py -v
```
All tests (existing 55 + new ~12) must pass.
