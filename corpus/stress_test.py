"""Stress test: blast NL→spec translation at OpenRouter with configurable concurrency.

Dual purpose: find worker bottlenecks AND collect quality data across models.

Usage:
    # Phase 1: single worker
    python3 -m corpus.stress_test --concurrency 1 --spend-cap 5.0

    # Phase 2: many workers
    python3 -m corpus.stress_test --concurrency 8 --spend-cap 5.0
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from llm.openrouter_client import complete, LLMUnavailable, BudgetExhausted
from llm.operations import nl_to_input_spec, SpecParseError
from llm.spend_tracker import SpendTracker
from corpus.circuit_judge import score_deterministic
from corpus.quality_score import _grade
from schemas.enrichment import enrich

REPO_ROOT = Path(__file__).resolve().parent.parent

MODELS = [
    "meta-llama/llama-3.3-70b-instruct",
    "google/gemini-2.5-flash",
    "qwen/qwen3-235b-a22b",
    "openai/gpt-4.1-mini",
    "google/gemini-2.5-pro",
]


@dataclass
class RunResult:
    board_id: str
    model: str
    ok: bool
    spec: dict | None = None
    cost_usd: float = 0.0
    latency_s: float = 0.0
    tokens: int = 0
    error: str = ""
    bom_score: float | None = None
    netlist_score: float | None = None
    grade: str | None = None
    enriched_bom_score: float | None = None
    enriched_grade: str | None = None
    enrichment_actions: int = 0
    stages: list[dict] | None = None
    enriched_spec: dict | None = None
    enrichment_log: list[dict] | None = None
    missing_parts: list[str] | None = None
    extra_parts: list[str] | None = None


@dataclass
class StressMetrics:
    results: list[RunResult] = field(default_factory=list)
    start_time: float = 0.0
    end_time: float = 0.0

    @property
    def total_cost(self):
        return sum(r.cost_usd for r in self.results)

    @property
    def success_rate(self):
        if not self.results:
            return 0
        return sum(1 for r in self.results if r.ok) / len(self.results)

    @property
    def wall_time(self):
        return self.end_time - self.start_time

    @property
    def throughput(self):
        if self.wall_time <= 0:
            return 0
        return len(self.results) / self.wall_time


def _load_manifest():
    manifest = REPO_ROOT / "corpus" / "manifest.jsonl"
    boards = []
    with open(manifest) as f:
        for line in f:
            if not line.strip():
                continue
            d = json.loads(line)
            if d.get("nl_source") == "marketing" and d.get("description"):
                boards.append(d)
    return boards


def _load_ref_specs():
    specs_dir = REPO_ROOT / "corpus" / "specs"
    refs = {}
    for f in specs_dir.glob("*.json"):
        if f.stem.startswith("ref-"):
            continue
        refs[f.stem] = json.loads(f.read_text())
    return refs


async def _run_one(
    board: dict,
    model: str,
    spend_tracker: SpendTracker,
    ref_specs: dict,
    sem: asyncio.Semaphore,
) -> RunResult:
    board_id = board["board_id"]
    description = board["description"]
    t0 = time.monotonic()

    async with sem:
        try:
            spec, stages = await nl_to_input_spec(
                description, board_id, model=model, spend_tracker=spend_tracker,
            )
            latency = time.monotonic() - t0
            cost = sum(s.get("cost_usd", 0) for s in stages)
            tokens = sum(s.get("tokens_in", 0) + s.get("tokens_out", 0) for s in stages)
            spec_dict = spec.model_dump(mode="json")

            result = RunResult(
                board_id=board_id, model=model, ok=True,
                spec=spec_dict, cost_usd=cost, latency_s=latency, tokens=tokens,
                stages=stages,
            )

            ref = ref_specs.get(board_id)
            if ref:
                det = score_deterministic(spec_dict, ref)
                combined = 0.4 * det.bom_score + 0.4 * det.netlist_score + 0.2 * det.structural_score
                result.bom_score = det.bom_score
                result.netlist_score = det.netlist_score
                result.grade = _grade(combined)
                result.missing_parts = det.missing_parts
                result.extra_parts = det.extra_parts

                enriched_spec, enrichment_actions = enrich(spec_dict)
                if enrichment_actions:
                    det_e = score_deterministic(enriched_spec, ref)
                    combined_e = 0.4 * det_e.bom_score + 0.4 * det_e.netlist_score + 0.2 * det_e.structural_score
                    result.enriched_bom_score = det_e.bom_score
                    result.enriched_grade = _grade(combined_e)
                    result.enrichment_actions = len(enrichment_actions)
                    result.enriched_spec = enriched_spec
                    result.enrichment_log = enrichment_actions

            return result

        except (SpecParseError, LLMUnavailable, BudgetExhausted) as e:
            latency = time.monotonic() - t0
            stages = getattr(e, "stages", None) or []
            return RunResult(
                board_id=board_id, model=model, ok=False,
                latency_s=latency, error=str(e)[:500],
                stages=stages,
            )
        except Exception as e:
            latency = time.monotonic() - t0
            return RunResult(
                board_id=board_id, model=model, ok=False,
                latency_s=latency, error=f"{type(e).__name__}: {str(e)[:300]}",
            )


def _print_live(idx: int, total: int, result: RunResult, cumulative_cost: float):
    model_short = result.model.split("/")[-1][:20]
    grade_str = result.grade or "ERR"
    bom_str = f"{result.bom_score:.2f}" if result.bom_score is not None else "-.--"
    status = "OK" if result.ok else "FAIL"
    enrich_str = ""
    if result.enriched_grade and result.enriched_grade != result.grade:
        enrich_str = f" ->{result.enriched_grade}(+{result.enrichment_actions})"
    print(
        f"[{idx:>4}/{total}] {result.board_id:30s} {model_short:20s} "
        f"{status:4s} {grade_str:>2s}{enrich_str} bom={bom_str} "
        f"{result.latency_s:5.1f}s ${result.cost_usd:.4f}  "
        f"(total: ${cumulative_cost:.3f})",
        flush=True,
    )


async def run_stress(
    concurrency: int,
    spend_cap: float,
    models: list[str],
    board_filter: str | None = None,
    limit: int | None = None,
):
    boards = _load_manifest()
    if board_filter == "probe9":
        probe_ids = {"bme280", "mcp9808", "ina219", "ads1115-adc", "max98357-i2s-amp",
                     "feather-rp2040", "feather-esp32-s3", "clue-nrf52840", "grand-central"}
        boards = [b for b in boards if b["board_id"] in probe_ids]
    elif board_filter:
        boards = [b for b in boards if board_filter in b["board_id"]]
    ref_specs = _load_ref_specs()

    # Build work items: every board x every model
    work = []
    for model in models:
        for board in boards:
            work.append((board, model))
    if limit:
        work = work[:limit]

    print(f"Stress test: {len(work)} runs, concurrency={concurrency}, cap=${spend_cap:.2f}")
    print(f"Models: {[m.split('/')[-1] for m in models]}")
    print(f"Boards: {len(boards)}, ref specs: {len(ref_specs)}")
    print("-" * 100)

    spend_tracker = SpendTracker(
        cap_usd=spend_cap,
        log_path=str(REPO_ROOT / "artifacts" / "stress_spend.jsonl"),
    )
    sem = asyncio.Semaphore(concurrency)
    metrics = StressMetrics(start_time=time.monotonic())

    completed = 0
    total = len(work)

    async def _run_and_report(board, model):
        nonlocal completed
        result = await _run_one(board, model, spend_tracker, ref_specs, sem)
        metrics.results.append(result)
        completed += 1
        _print_live(completed, total, result, metrics.total_cost)
        if metrics.total_cost >= spend_cap:
            raise BudgetExhausted(f"Hit ${spend_cap} cap")
        return result

    tasks = [asyncio.create_task(_run_and_report(b, m)) for b, m in work]

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    except BudgetExhausted:
        for t in tasks:
            if not t.done():
                t.cancel()

    metrics.end_time = time.monotonic()
    return metrics


def print_report(metrics: StressMetrics, concurrency: int):
    print("\n" + "=" * 100)
    print(f"STRESS TEST COMPLETE — concurrency={concurrency}")
    print(f"  Wall time: {metrics.wall_time:.1f}s")
    print(f"  Total runs: {len(metrics.results)}")
    print(f"  Success rate: {metrics.success_rate:.1%}")
    print(f"  Total cost: ${metrics.total_cost:.4f}")
    print(f"  Throughput: {metrics.throughput:.2f} runs/s")
    print(f"  Avg latency: {sum(r.latency_s for r in metrics.results) / max(len(metrics.results), 1):.1f}s")

    # Per-model breakdown
    by_model: dict[str, list[RunResult]] = {}
    for r in metrics.results:
        by_model.setdefault(r.model, []).append(r)

    print(f"\n{'Model':<40} {'Runs':>5} {'OK%':>5} {'Avg lat':>8} {'Cost':>8} {'Avg BOM':>8} {'Grades':>15}")
    print("-" * 95)
    for model in sorted(by_model):
        runs = by_model[model]
        ok = sum(1 for r in runs if r.ok)
        avg_lat = sum(r.latency_s for r in runs) / len(runs)
        cost = sum(r.cost_usd for r in runs)
        scored = [r for r in runs if r.bom_score is not None]
        avg_bom = sum(r.bom_score for r in scored) / len(scored) if scored else 0
        grades = Counter(r.grade for r in runs if r.grade)
        grade_str = " ".join(f"{g}:{c}" for g, c in sorted(grades.items()))
        short = model.split("/")[-1]
        # Enrichment stats
        enriched_scored = [r for r in runs if r.enriched_bom_score is not None]
        avg_e_bom = sum(r.enriched_bom_score for r in enriched_scored) / len(enriched_scored) if enriched_scored else 0
        e_grades = Counter(r.enriched_grade for r in runs if r.enriched_grade)
        e_grade_str = " ".join(f"{g}:{c}" for g, c in sorted(e_grades.items()))
        print(f"{short:<40} {len(runs):>5} {ok/len(runs):>5.0%} {avg_lat:>7.1f}s ${cost:>7.4f} {avg_bom:>8.3f} {grade_str:>15}")
        if enriched_scored:
            avg_lift = avg_e_bom - avg_bom
            print(f"{'  + enriched':<40} {'':>5} {'':>5} {'':>8} {'':>8} {avg_e_bom:>8.3f} {e_grade_str:>15}  (bom +{avg_lift:+.3f})")

    # Per-board breakdown (top/bottom)
    by_board: dict[str, list[RunResult]] = {}
    for r in metrics.results:
        if r.bom_score is not None:
            by_board.setdefault(r.board_id, []).append(r)

    if by_board:
        board_avgs = {
            b: sum(r.bom_score for r in rs) / len(rs)
            for b, rs in by_board.items()
        }
        sorted_boards = sorted(board_avgs.items(), key=lambda x: x[1], reverse=True)
        print(f"\nTop 5 boards (avg BOM):")
        for b, avg in sorted_boards[:5]:
            print(f"  {b:<30} {avg:.3f}")
        print(f"Bottom 5 boards:")
        for b, avg in sorted_boards[-5:]:
            print(f"  {b:<30} {avg:.3f}")

    # Save results — full detail per run + summary
    out_dir = REPO_ROOT / "artifacts" / "stress_results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    run_dir = out_dir / f"stress-c{concurrency}-{ts}"
    run_dir.mkdir(parents=True, exist_ok=True)

    for i, r in enumerate(metrics.results):
        model_short = r.model.split("/")[-1]
        run_file = run_dir / f"{r.board_id}__{model_short}.json"
        run_data = {
            "board_id": r.board_id,
            "model": r.model,
            "ok": r.ok,
            "cost_usd": r.cost_usd,
            "latency_s": r.latency_s,
            "tokens": r.tokens,
            "bom_score": r.bom_score,
            "netlist_score": r.netlist_score,
            "grade": r.grade,
            "missing_parts": r.missing_parts,
            "extra_parts": r.extra_parts,
            "enriched_bom_score": r.enriched_bom_score,
            "enriched_grade": r.enriched_grade,
            "enrichment_actions": r.enrichment_actions,
            "enrichment_log": r.enrichment_log,
            "error": r.error,
            "spec": r.spec,
            "enriched_spec": r.enriched_spec,
            "stages": [
                {k: v for k, v in s.items() if k != "raw_text"}
                for s in (r.stages or [])
            ],
            "raw_llm_outputs": [
                {"stage": s.get("stage"), "text": s.get("raw_text")}
                for s in (r.stages or [])
                if s.get("raw_text")
            ],
        }
        run_file.write_text(json.dumps(run_data, indent=2))

    summary_file = run_dir / "summary.json"
    summary = {
        "concurrency": concurrency,
        "wall_time_s": metrics.wall_time,
        "total_runs": len(metrics.results),
        "success_rate": metrics.success_rate,
        "total_cost_usd": metrics.total_cost,
        "throughput_rps": metrics.throughput,
        "results": [
            {
                "board_id": r.board_id,
                "model": r.model,
                "ok": r.ok,
                "cost_usd": r.cost_usd,
                "latency_s": r.latency_s,
                "tokens": r.tokens,
                "bom_score": r.bom_score,
                "netlist_score": r.netlist_score,
                "grade": r.grade,
                "missing_parts": r.missing_parts,
                "extra_parts": r.extra_parts,
                "enriched_bom_score": r.enriched_bom_score,
                "enriched_grade": r.enriched_grade,
                "enrichment_actions": r.enrichment_actions,
                "error": r.error,
            }
            for r in metrics.results
        ],
    }
    summary_file.write_text(json.dumps(summary, indent=2))
    print(f"\nResults saved: {run_dir}/ ({len(metrics.results)} run files + summary.json)")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--concurrency", type=int, default=1)
    parser.add_argument("--spend-cap", type=float, default=5.0)
    parser.add_argument("--models", nargs="+", default=None,
                        help="Model IDs to test (default: all 5)")
    parser.add_argument("--board", type=str, default=None,
                        help="Filter boards by substring")
    parser.add_argument("--limit", type=int, default=None,
                        help="Max total runs")
    args = parser.parse_args()

    if not os.environ.get("OPENROUTER_API_KEY"):
        print("ERROR: OPENROUTER_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    models = args.models or MODELS
    metrics = asyncio.run(run_stress(
        concurrency=args.concurrency,
        spend_cap=args.spend_cap,
        models=models,
        board_filter=args.board,
        limit=args.limit,
    ))
    print_report(metrics, args.concurrency)


if __name__ == "__main__":
    main()
