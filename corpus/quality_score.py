"""Unified quality grading for generated circuits.

Wraps corpus.circuit_judge to produce a CircuitScore with weighted
combined score and letter grade.

Usage:
    python3 -m corpus.quality_score --gen-spec gen.json --ref-spec ref.json --board-id bme280 --model gemini-2.5-flash
    python3 -m corpus.quality_score --batch-dir generated/ --ref-dir corpus/specs/
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

from corpus.circuit_judge import DeterministicScore, score_deterministic

WEIGHT_BOM = 0.4
WEIGHT_NETLIST = 0.4
WEIGHT_STRUCTURAL = 0.2

GRADE_THRESHOLDS = [
    (0.9, "A"),
    (0.75, "B"),
    (0.5, "C"),
    (0.25, "D"),
]

QUALITY_GATES = (
    "schematic_ok",
    "placement_ok",
    "drc_ok",
    "manufacturable",
    "visual_review_ready",
    "product_layout_ok",
)


def _grade(score: float) -> str:
    for threshold, letter in GRADE_THRESHOLDS:
        if score >= threshold:
            return letter
    return "F"


@dataclass
class CircuitScore:
    board_id: str
    model: str
    deterministic: DeterministicScore
    combined_score: float
    grade: str


def score_circuit(
    gen_spec: dict,
    ref_spec: dict,
    board_id: str = "",
    model: str = "",
) -> CircuitScore:
    det = score_deterministic(gen_spec, ref_spec)
    combined = (
        WEIGHT_BOM * det.bom_score
        + WEIGHT_NETLIST * det.netlist_score
        + WEIGHT_STRUCTURAL * det.structural_score
    )
    return CircuitScore(
        board_id=board_id,
        model=model,
        deterministic=det,
        combined_score=round(combined, 4),
        grade=_grade(combined),
    )


def score_from_files(
    gen_spec_path: str | Path,
    ref_spec_path: str | Path,
    board_id: str = "",
    model: str = "",
) -> CircuitScore:
    gen_spec = json.loads(Path(gen_spec_path).read_text())
    ref_spec = json.loads(Path(ref_spec_path).read_text())
    return score_circuit(gen_spec, ref_spec, board_id=board_id, model=model)


def score_batch(
    gen_specs: list[dict],
    ref_specs: dict[str, dict],
    models: list[str] | None = None,
) -> list[CircuitScore]:
    scores = []
    for entry in gen_specs:
        bid = entry["board_id"]
        mdl = entry["model"]
        if models and mdl not in models:
            continue
        ref = ref_specs.get(bid)
        if ref is None:
            continue
        scores.append(score_circuit(entry["spec"], ref, board_id=bid, model=mdl))
    return scores


def format_report(score: CircuitScore) -> str:
    d = score.deterministic
    lines = []
    lines.append(
        f"Board: {score.board_id}  Model: {score.model}  "
        f"Grade: {score.grade} ({score.combined_score:.2f})"
    )
    lines.append(
        f"BOM: {d.bom_score:.2f} ({d.part_count_gen} gen / {d.part_count_ref} ref, "
        f"{len(d.missing_parts)} missing, {len(d.extra_parts)} extra)"
    )
    lines.append(f"Netlist: {d.netlist_score:.2f}")

    decap_total = len(d.decoupling_coverage)
    decap_ok = sum(1 for v in d.decoupling_coverage.values() if v)
    power_str = "OK" if d.power_pins_connected else "FAIL"
    floating_str = d.floating_inputs if d.floating_inputs else "none"
    decap_str = f"{decap_ok}/{decap_total} ICs" if decap_total else "n/a"
    lines.append(
        f"Structural: {d.structural_score:.2f} "
        f"(power: {power_str}, decoupling: {decap_str}, floating: {floating_str})"
    )

    if d.missing_parts:
        lines.append(f"Missing: {', '.join(d.missing_parts)}")
    if d.extra_parts:
        lines.append(f"Extra: {', '.join(d.extra_parts)}")
    if d.structural_warnings:
        for w in d.structural_warnings:
            lines.append(f"Warning: {w}")
    return "\n".join(lines)


def format_matrix(scores: list[CircuitScore]) -> str:
    if not scores:
        return ""

    boards = sorted({s.board_id for s in scores})
    models = sorted({s.model for s in scores})

    lookup: dict[tuple[str, str], CircuitScore] = {}
    for s in scores:
        lookup[(s.model, s.board_id)] = s

    col_width = max(len(b) for b in boards) + 2
    label_width = max(len(m) for m in models) + 2

    header = " " * label_width + "".join(b.ljust(col_width) for b in boards)
    lines = [header]

    for mdl in models:
        row = mdl.ljust(label_width)
        for bid in boards:
            sc = lookup.get((mdl, bid))
            if sc is None:
                cell = "-"
            else:
                cell = f"{sc.grade} {sc.combined_score:.2f}"
            row += cell.ljust(col_width)
        lines.append(row)

    return "\n".join(lines)


def quality_gate_summary(layout_quality: dict) -> dict:
    """Return stable gate and issue-class tags for a layout quality report."""
    gates = layout_quality.get("gates") if isinstance(layout_quality, dict) else {}
    issues = layout_quality.get("issues") if isinstance(layout_quality, dict) else []
    normalized_gates = {
        gate: bool(gates.get(gate))
        for gate in QUALITY_GATES
    }
    failed_gates = [
        gate
        for gate in QUALITY_GATES
        if not normalized_gates[gate]
    ]
    issue_classes = sorted({
        str(issue.get("code"))
        for issue in issues
        if isinstance(issue, dict) and issue.get("code")
    })
    return {
        "gates": normalized_gates,
        "failed_gates": failed_gates,
        "issue_classes": issue_classes,
        "issue_counts": dict(layout_quality.get("issue_counts") or {}),
    }


_BATCH_FILENAME_RE = re.compile(r"^(.+?)_([^_].+)\.json$")


def _load_batch_dir(batch_dir: Path) -> list[dict]:
    entries = []
    for f in sorted(batch_dir.glob("*.json")):
        m = _BATCH_FILENAME_RE.match(f.name)
        if not m:
            continue
        board_id, model = m.group(1), m.group(2)
        spec = json.loads(f.read_text())
        entries.append({"board_id": board_id, "model": model, "spec": spec})
    return entries


def _load_ref_dir(ref_dir: Path) -> dict[str, dict]:
    refs = {}
    for f in sorted(ref_dir.glob("*.json")):
        refs[f.stem] = json.loads(f.read_text())
    return refs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gen-spec", type=Path, help="Path to generated spec JSON")
    parser.add_argument("--ref-spec", type=Path, help="Path to reference spec JSON")
    parser.add_argument("--board-id", default="", help="Board name")
    parser.add_argument("--model", default="", help="Model name")
    parser.add_argument("--batch-dir", type=Path, help="Directory of {board}_{model}.json generated specs")
    parser.add_argument("--ref-dir", type=Path, help="Directory of reference spec JSONs")
    args = parser.parse_args(argv)

    if args.batch_dir:
        if not args.ref_dir:
            parser.error("--ref-dir required with --batch-dir")
        gen_entries = _load_batch_dir(args.batch_dir)
        ref_specs = _load_ref_dir(args.ref_dir)
        scores = score_batch(gen_entries, ref_specs)
        if not scores:
            print("No matching board/model pairs found.")
            return 1
        print(format_matrix(scores))
        return 0

    if args.gen_spec and args.ref_spec:
        sc = score_from_files(
            args.gen_spec, args.ref_spec,
            board_id=args.board_id, model=args.model,
        )
        print(format_report(sc))
        return 0

    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
