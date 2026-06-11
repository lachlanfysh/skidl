"""Build regression coefficients from telemetry for the complexity estimator.

Reads runs.jsonl, computes simple models for:
  - P(layout_issue | component_count, pin_count)
  - P(timeout | component_count, pin_count)
  - cpu_time_s ~ component_count
  - cost percentiles by geometry bucket

Output: a Python dict literal for hardcoding into schemas/estimator.py.
No external deps — pure stdlib math on ~170 data points.

Usage:
    python3 -m telemetry.regression                    # default: telemetry/runs.jsonl
    python3 -m telemetry.regression telemetry/runs_b.jsonl
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path


def _load(path: str | Path) -> list[dict]:
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


def _logistic_regression_1d(xs: list[float], ys: list[float], lr: float = 0.01, epochs: int = 2000) -> tuple[float, float]:
    """Fit P(y=1) = sigmoid(w*x + b). Returns (w, b)."""
    w, b = 0.0, 0.0
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    for _ in range(epochs):
        dw, db = 0.0, 0.0
        for x, y in zip(xs, ys):
            z = w * x + b
            z = max(-500, min(500, z))
            p = 1.0 / (1.0 + math.exp(-z))
            err = p - y
            dw += err * x
            db += err
        w -= lr * dw / n
        b -= lr * db / n
    return w, b


def _linear_regression(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Fit y = slope*x + intercept. Returns (slope, intercept)."""
    n = len(xs)
    if n < 2:
        return 0.0, sum(ys) / max(n, 1)
    mx = sum(xs) / n
    my = sum(ys) / n
    num = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return 0.0, my
    slope = num / den
    intercept = my - slope * mx
    return slope, intercept


def _percentiles(values: list[float]) -> dict[str, float]:
    if not values:
        return {"p50": 0, "p75": 0, "p90": 0, "p95": 0, "max": 0}
    s = sorted(values)
    def p(pct: float) -> float:
        idx = int(len(s) * pct / 100)
        return s[min(idx, len(s) - 1)]
    return {"p50": p(50), "p75": p(75), "p90": p(90), "p95": p(95), "max": max(s)}


def build_coefficients(runs_path: str | Path | None = None) -> dict:
    path = Path(runs_path) if runs_path else Path("telemetry/runs.jsonl")
    records = _load(path)

    with_geometry = [
        r for r in records
        if (r.get("geometry") or {}).get("component_count", 0) > 0
    ]

    if len(with_geometry) < 10:
        print(f"WARNING: only {len(with_geometry)} records with geometry — coefficients will be noisy", file=sys.stderr)

    # Features
    parts = [r["geometry"]["component_count"] for r in with_geometry]
    pins = [r["geometry"]["pin_count"] for r in with_geometry]

    # Targets — only train layout/timeout on boards that got past translation
    reached_engine = [
        r for r in with_geometry
        if not any(c.startswith("SPEC_") for c in r.get("exceptions_raised", []))
        or r["status"] in ("succeeded", "succeeded_with_warnings", "timeout", "crashed")
    ]
    re_parts = [r["geometry"]["component_count"] for r in reached_engine]
    had_layout = [1.0 if any(c in ("LAYOUT_OVERLAP", "LAYOUT_OUTLINE_VIOLATION", "LAYOUT_KEEPOUT") for c in r.get("exceptions_raised", [])) else 0.0 for r in reached_engine]
    timed_out = [1.0 if r["status"] == "timeout" else 0.0 for r in reached_engine]
    cpu_times = [r.get("cpu_time_s", 0) for r in with_geometry]

    # Logistic: P(layout_issue | component_count) — only boards that reached layout
    w_layout, b_layout = _logistic_regression_1d(re_parts, had_layout)

    # Logistic: P(timeout | component_count) — only boards that reached engine
    w_timeout, b_timeout = _logistic_regression_1d(re_parts, timed_out)

    # Linear: cpu_time ~ component_count (only for boards that actually ran)
    ran_parts = [p for p, c in zip(parts, cpu_times) if c > 0.5]
    ran_cpu = [c for c in cpu_times if c > 0.5]
    cpu_slope, cpu_intercept = _linear_regression(ran_parts, ran_cpu)

    # Cost percentiles by geometry bucket
    cost_buckets: dict[str, list[float]] = {"small": [], "medium": [], "large": [], "xlarge": []}
    for r in with_geometry:
        cost = r.get("total_cost_usd", 0)
        pc = r["geometry"]["component_count"]
        if pc < 10:
            cost_buckets["small"].append(cost)
        elif pc < 25:
            cost_buckets["medium"].append(cost)
        elif pc < 50:
            cost_buckets["large"].append(cost)
        else:
            cost_buckets["xlarge"].append(cost)

    # Success rate by bucket
    success_buckets: dict[str, tuple[int, int]] = {}
    for bucket_name, lo, hi in [("small", 0, 10), ("medium", 10, 25), ("large", 25, 50), ("xlarge", 50, 10000)]:
        bucket_recs = [r for r, p in zip(with_geometry, parts) if lo <= p < hi]
        ok = sum(1 for r in bucket_recs if r["status"] in ("succeeded", "succeeded_with_warnings"))
        success_buckets[bucket_name] = (ok, len(bucket_recs))

    coefficients = {
        "data_source": str(path),
        "record_count": len(with_geometry),
        "layout_issue_logistic": {"w": round(w_layout, 6), "b": round(b_layout, 4)},
        "timeout_logistic": {"w": round(w_timeout, 6), "b": round(b_timeout, 4)},
        "cpu_time_linear": {"slope": round(cpu_slope, 4), "intercept": round(cpu_intercept, 4)},
        "cost_percentiles": {k: _percentiles(v) for k, v in cost_buckets.items()},
        "success_rate": {k: {"ok": ok, "total": total, "rate": round(ok / total, 3) if total else 0} for k, (ok, total) in success_buckets.items()},
    }
    return coefficients


def print_coefficients(runs_path: str | Path | None = None) -> None:
    coefficients = build_coefficients(runs_path)
    print("_REGRESSION_COEFFICIENTS = ", end="")
    print(json.dumps(coefficients, indent=2))


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else None
    print_coefficients(path)


if __name__ == "__main__":
    main()
