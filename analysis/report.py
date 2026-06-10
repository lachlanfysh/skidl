"""Generate a morning report from telemetry runs.

Usage:
    python3 -m analysis.report                          # default: telemetry/runs.jsonl
    python3 -m analysis.report telemetry/runs_a.jsonl   # specific file
    python3 -m analysis.report --compare telemetry/runs_a.jsonl telemetry/runs_b.jsonl
"""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def load_records(path: str | Path) -> list[dict]:
    records = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def pct(n: int, total: int) -> str:
    return f"{100 * n / total:.0f}%" if total else "0%"


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    idx = int(len(s) * p / 100)
    return s[min(idx, len(s) - 1)]


def report_section(title: str) -> str:
    return f"\n## {title}\n"


def single_run_report(records: list[dict], label: str = "") -> str:
    out: list[str] = []
    header = f"# Run Report{f': {label}' if label else ''}"
    out.append(header)
    out.append(f"\n**{len(records)} records**\n")

    by_mode: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_mode[r.get("mode", "unknown")].append(r)

    # Overall status
    out.append(report_section("Status Summary"))
    status_counts = Counter(r["status"] for r in records)
    total = len(records)
    for status, count in status_counts.most_common():
        out.append(f"- **{status}**: {count} ({pct(count, total)})")

    success_count = sum(1 for r in records if r["status"] in ("succeeded", "succeeded_with_warnings"))
    out.append(f"\n**Overall success rate: {pct(success_count, total)}**")

    # Per-mode breakdown
    out.append(report_section("Per-Mode Breakdown"))
    for mode, recs in sorted(by_mode.items()):
        ok = sum(1 for r in recs if r["status"] in ("succeeded", "succeeded_with_warnings"))
        out.append(f"### {mode} ({len(recs)} boards)")
        out.append(f"- Success: {ok}/{len(recs)} ({pct(ok, len(recs))})")

        costs = [r["total_cost_usd"] for r in recs if r.get("total_cost_usd", 0) > 0]
        if costs:
            out.append(f"- Cost: ${sum(costs):.4f} total, ${sum(costs)/len(recs):.4f}/board avg")
            out.append(f"  - p50=${percentile(costs, 50):.4f}, p90=${percentile(costs, 90):.4f}, p99=${percentile(costs, 99):.4f}")

        walls = [r["wall_time_s"] for r in recs if r.get("wall_time_s", 0) > 1]
        if walls:
            out.append(f"- Wall time: p50={percentile(walls, 50):.1f}s, p90={percentile(walls, 90):.1f}s, max={max(walls):.1f}s")

        iters = [r["correction_iterations"] for r in recs if r.get("correction_iterations", 0) > 0]
        if iters:
            out.append(f"- Correction iterations: avg={sum(iters)/len(iters):.1f}, max={max(iters)}")

    # Failure taxonomy
    failed = [r for r in records if r["status"] not in ("succeeded", "succeeded_with_warnings")]
    if failed:
        out.append(report_section("Failure Taxonomy"))
        reason_counts: Counter[str] = Counter()
        for r in failed:
            reason = r.get("failure_reason") or "unknown"
            short = reason.split("\n")[0][:80]
            reason_counts[short] += 1
        for reason, count in reason_counts.most_common(15):
            out.append(f"- **{count}x** {reason}")

        out.append("\n### Exception codes in failed runs")
        exc_counts: Counter[str] = Counter()
        for r in failed:
            for code in r.get("exceptions_raised", []):
                exc_counts[code] += 1
        for code, count in exc_counts.most_common(15):
            out.append(f"- {code}: {count}")

    # Difficulty axis
    out.append(report_section("By Difficulty Axis"))
    by_axis: dict[str, list[dict]] = defaultdict(list)
    for r in records:
        by_axis[r.get("difficulty_axis", "unknown")].append(r)
    for axis, recs in sorted(by_axis.items()):
        ok = sum(1 for r in recs if r["status"] in ("succeeded", "succeeded_with_warnings"))
        out.append(f"- **{axis}**: {ok}/{len(recs)} ({pct(ok, len(recs))})")

    # Tier breakdown
    out.append(report_section("By Tier"))
    by_tier: dict[int, list[dict]] = defaultdict(list)
    for r in records:
        by_tier[r.get("tier", 0)].append(r)
    for tier, recs in sorted(by_tier.items()):
        ok = sum(1 for r in recs if r["status"] in ("succeeded", "succeeded_with_warnings"))
        out.append(f"- **Tier {tier}**: {ok}/{len(recs)} ({pct(ok, len(recs))})")

    # Worst boards (most iterations, still failed)
    failed_boards = [
        r for r in records
        if r["status"] not in ("succeeded", "succeeded_with_warnings")
        and r.get("correction_iterations", 0) > 0
    ]
    if failed_boards:
        out.append(report_section("Hardest Failures (most iterations, still failed)"))
        failed_boards.sort(key=lambda r: r.get("correction_iterations", 0), reverse=True)
        for r in failed_boards[:10]:
            out.append(
                f"- **{r['board_id']}**: {r['correction_iterations']} iters, "
                f"status={r['status']}, reason={str(r.get('failure_reason',''))[:60]}"
            )

    # LLM rescue stats
    llm_rescued = [
        r for r in records
        if r["status"] in ("succeeded", "succeeded_with_warnings")
        and r.get("correction_iterations", 0) > 0
    ]
    if llm_rescued:
        out.append(report_section("LLM Rescue (succeeded after corrections)"))
        out.append(f"**{len(llm_rescued)} boards rescued by correction loop**\n")
        for r in sorted(llm_rescued, key=lambda r: r["correction_iterations"], reverse=True)[:10]:
            out.append(
                f"- **{r['board_id']}**: {r['correction_iterations']} iters, "
                f"wall={r.get('wall_time_s', 0):.1f}s, cost=${r.get('total_cost_usd', 0):.4f}"
            )

    return "\n".join(out)


def comparison_report(records_a: list[dict], records_b: list[dict], label_a: str, label_b: str) -> str:
    out: list[str] = []
    out.append(f"# Comparison: {label_a} vs {label_b}\n")

    def board_status(records: list[dict], mode: str | None = None) -> dict[str, str]:
        result = {}
        for r in records:
            if mode and r.get("mode") != mode:
                continue
            result[r["board_id"]] = r["status"]
        return result

    for mode in ("engine_only", "internal", "external"):
        a = board_status(records_a, mode)
        b = board_status(records_b, mode)
        common = set(a) & set(b)
        if not common:
            continue

        out.append(report_section(f"Mode: {mode} ({len(common)} common boards)"))

        ok_a = sum(1 for k in common if a[k] in ("succeeded", "succeeded_with_warnings"))
        ok_b = sum(1 for k in common if b[k] in ("succeeded", "succeeded_with_warnings"))
        out.append(f"- {label_a}: {ok_a}/{len(common)} ({pct(ok_a, len(common))})")
        out.append(f"- {label_b}: {ok_b}/{len(common)} ({pct(ok_b, len(common))})")

        same = sum(1 for k in common if a[k] == b[k])
        diff = len(common) - same
        out.append(f"- Same outcome: {same}, Different: {diff}")

        if diff > 0:
            out.append(f"\n### Differing outcomes")
            for k in sorted(common):
                if a[k] != b[k]:
                    out.append(f"- **{k}**: {a[k]} -> {b[k]}")

        a_only_ok = [k for k in common if a[k] in ("succeeded", "succeeded_with_warnings") and b[k] not in ("succeeded", "succeeded_with_warnings")]
        b_only_ok = [k for k in common if b[k] in ("succeeded", "succeeded_with_warnings") and a[k] not in ("succeeded", "succeeded_with_warnings")]
        if a_only_ok:
            out.append(f"\n### Only succeeded in {label_a}: {', '.join(sorted(a_only_ok))}")
        if b_only_ok:
            out.append(f"\n### Only succeeded in {label_b}: {', '.join(sorted(b_only_ok))}")

    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    args = argv or sys.argv[1:]

    if "--compare" in args:
        args.remove("--compare")
        if len(args) < 2:
            print("Usage: --compare FILE_A FILE_B", file=sys.stderr)
            return 1
        path_a, path_b = Path(args[0]), Path(args[1])
        records_a = load_records(path_a)
        records_b = load_records(path_b)
        report = single_run_report(records_a, path_a.stem)
        report += "\n\n---\n\n"
        report += single_run_report(records_b, path_b.stem)
        report += "\n\n---\n\n"
        report += comparison_report(records_a, records_b, path_a.stem, path_b.stem)
    else:
        path = Path(args[0]) if args else Path("telemetry/runs.jsonl")
        if not path.exists():
            print(f"No telemetry at {path}", file=sys.stderr)
            return 1
        records = load_records(path)
        report = single_run_report(records, path.stem)

    print(report)

    out_path = Path("docs/MORNING_REPORT.md")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report + "\n")
    print(f"\nWrote {out_path}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
