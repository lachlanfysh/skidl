from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from corpus.layout_patterns import analyze_pcb, read_mined_parts


PANEL_PCB = """(kicad_pcb
  (version 20240108)
  (generator "test")
  (gr_rect (start 0 0) (end 30 128.5) (layer "Edge.Cuts") (stroke (width 0.1)))
  (footprint "Connector_Audio:Thonkiconn_PJ398SM"
    (at 10 24)
    (property "Reference" "J1" (at 0 0) (effects (font (size 1 1))))
    (property "Value" "PJ398SM" (at 0 0) (effects (font (size 1 1))))
  )
  (footprint "Connector_Audio:Thonkiconn_PJ398SM"
    (at 10 48)
    (property "Reference" "J2" (at 0 0) (effects (font (size 1 1))))
    (property "Value" "PJ398SM" (at 0 0) (effects (font (size 1 1))))
  )
  (footprint "Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D"
    (at 20 24)
    (property "Reference" "RV1" (at 0 0) (effects (font (size 1 1))))
    (property "Value" "100k" (at 0 0) (effects (font (size 1 1))))
  )
  (footprint "Potentiometer_THT:Potentiometer_Alpha_RD901F-40-00D"
    (at 20 48)
    (property "Reference" "RV2" (at 0 0) (effects (font (size 1 1))))
    (property "Value" "100k" (at 0 0) (effects (font (size 1 1))))
  )
  (footprint "LED_THT:LED_D3.0mm"
    (at 15 72)
    (property "Reference" "D1" (at 0 0) (effects (font (size 1 1))))
    (property "Value" "LED" (at 0 0) (effects (font (size 1 1))))
  )
)"""


def test_read_mined_parts_classifies_panel_controls(tmp_path):
    path = tmp_path / "panel.kicad_pcb"
    path.write_text(PANEL_PCB)

    parts = {part.ref: part for part in read_mined_parts(path)}

    assert parts["J1"].kind == "panel_jack"
    assert parts["RV1"].kind == "pot"
    assert parts["D1"].kind == "led"


def test_analyze_pcb_detects_eurorack_panel_rows_and_columns(tmp_path):
    path = tmp_path / "panel.kicad_pcb"
    path.write_text(PANEL_PCB)

    pattern = analyze_pcb(path)

    assert pattern.template == "eurorack_or_tall_panel"
    assert pattern.width_mm == 30.0
    assert pattern.height_mm == 128.5
    assert pattern.kind_counts["panel_jack"] == 2
    assert pattern.kind_counts["pot"] == 2
    assert any(set(cluster.refs) == {"J1", "J2"} for cluster in pattern.panel_columns)
    assert any(set(cluster.refs) == {"RV1", "RV2"} for cluster in pattern.panel_columns)
    assert any(set(cluster.refs) == {"J1", "RV1"} for cluster in pattern.panel_rows)
    assert any(set(cluster.refs) == {"J2", "RV2"} for cluster in pattern.panel_rows)
